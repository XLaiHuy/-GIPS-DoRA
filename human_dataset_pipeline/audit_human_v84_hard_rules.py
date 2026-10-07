"""Fail-closed document, prose, and cross-split near-duplicate audit."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from human_v3.core import JsonlWriter, rows
from human_v3.dedup import near_pairs

CORE_TYPES = {"bachelor_thesis", "master_thesis", "doctoral_thesis", "capstone_project"}
KNOWN_ORIGINS = {"core_v55", "hpu"}
SPLITS = ("train", "dev", "test")
BAD_START = re.compile(r"^\s*[•●▪◦*+-]\s+")
REPLACEMENT_OR_CONTROL = re.compile(r"\ufffd|[\x00-\x08\x0b\x0c\x0e-\x1f]")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    candidate = args.candidate.resolve()
    docs = {row["document_id"]: row for row in rows(candidate / "documents.jsonl")}
    chunks = [row for split in SPLITS for row in rows(
        candidate / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl")]
    issues = []
    counts = Counter()
    referenced_docs = {row["document_id"] for row in chunks}
    by_sha = defaultdict(list)
    for did in sorted(referenced_docs):
        doc = docs.get(did)
        if doc is None:
            issues.append({"reason": "missing_document", "document_id": did})
            continue
        by_sha[doc["source_pdf_sha256"]].append(did)
        checks = {
            "non_core_document_type": doc.get("document_type_id") not in CORE_TYPES,
            "invalid_or_post_2022_year": not isinstance(doc.get("year"), int)
                or not 1990 <= doc.get("year", 0) <= 2022,
            "missing_source_catalog": not doc.get("source_url"),
            "missing_institution": not doc.get("institution_id"),
            "internal_rights_not_cleared": doc.get("rights_status") not in
                {"internal_research_cleared", "redistribution_cleared"} or not doc.get("rights_evidence"),
            "new_source_lacks_document_specific_rights_evidence":
                doc.get("origin") not in KNOWN_ORIGINS
                and not (doc.get("rights_evidence_url") or doc.get("rights_evidence_path")),
        }
        for reason, bad in checks.items():
            if bad:
                issues.append({"reason": reason, "document_id": did})
        counts[f"document_type:{doc.get('document_type_id')}"] += 1
        counts[f"institution:{doc.get('institution_id')}"] += 1
        counts[f"year:{doc.get('year')}"] += 1
    for sha, ids in by_sha.items():
        if len(ids) > 1 and len({docs[did]["group_id"] for did in ids}) > 1:
            issues.append({"reason": "same_pdf_different_groups", "pdf_sha256": sha,
                           "document_ids": ids})
    for chunk in chunks:
        for reason, bad in {
            "blocked_review_reason_on_pass": bool(set(chunk.get("review_reasons", [])) & {
                "computing_scope_requires_review", "body_boundaries_require_review",
                "language_requires_review", "source_evidence_requires_review",
                "year_conflict", "institution_is_repository_placeholder",
                "legacy_exclusion:incomplete_body_extraction"}),
            "replacement_or_control_character": bool(REPLACEMENT_OR_CONTROL.search(chunk["text"])),
            "leading_bullet": bool(BAD_START.search(chunk["text"])),
            "published_status_without_attestation": chunk.get("release_status") == "ready"
                or chunk.get("generation_ready") is True,
        }.items():
            if bad:
                issues.append({"reason": reason, "chunk_id": chunk["chunk_id"],
                               "document_id": chunk["document_id"]})
    config = {"near_duplicate_shingle_words": 5, "near_duplicate_threshold": .8,
              "near_duplicate_min_words": 40}
    near_edges = []
    for left, right, score in near_pairs(chunks, config, cross_split_only=True):
        near_edges.append({"left_chunk_id": chunks[left]["chunk_id"],
                           "right_chunk_id": chunks[right]["chunk_id"],
                           "left_document_id": chunks[left]["document_id"],
                           "right_document_id": chunks[right]["document_id"],
                           "similarity": round(score, 5)})
    if near_edges:
        issues.append({"reason": "cross_split_near_duplicate", "pair_count": len(near_edges)})
    with JsonlWriter(candidate / "audit/cross_split_near_duplicate_pairs.jsonl") as writer:
        for edge in near_edges:
            writer.write(edge)
    result = {"candidate": candidate.name, "checked_chunks": len(chunks),
              "checked_documents": len(referenced_docs), "status": "PASS" if not issues else "HOLD",
              "error_count": len(issues),
              "error_reason_counts": dict(Counter(item["reason"] for item in issues)),
              "errors_first_100": issues[:100],
              "cross_split_near_duplicate_pair_count": len(near_edges),
              "cross_split_near_duplicate_pairs_first_100": near_edges[:100],
              "document_distributions": dict(counts)}
    path = candidate / "audit/hard_rules.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items()
                      if key not in {"errors_first_100", "cross_split_near_duplicate_pairs_first_100",
                                     "document_distributions"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
