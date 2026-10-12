"""Full candidate integrity audit; never turns unreviewed data into ready."""
from __future__ import annotations

import hashlib
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import fitz

from human_v3.core import file_sha, rows

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "human_v3_2_rebuild_v76_20261007"
SPLITS = ("train", "dev", "test")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=Path, nargs="?", default=SOURCE)
    args = parser.parse_args()
    source = args.candidate.resolve()
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    documents = {doc["document_id"]: doc for doc in rows(source / "documents.jsonl")}
    errors = []
    page_counts = {}
    for number, (did, doc) in enumerate(sorted(documents.items()), 1):
        path = Path(doc["source_path"])
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file() or file_sha(path) != doc["source_pdf_sha256"]:
            errors.append({"kind": "pdf_missing_or_hash_mismatch", "document_id": did})
            continue
        try:
            with fitz.open(path) as pdf:
                page_counts[did] = len(pdf)
        except (OSError, RuntimeError, ValueError) as exc:
            errors.append({"kind": "pdf_open_error", "document_id": did,
                           "detail": f"{type(exc).__name__}: {exc}"})
        if number % 100 == 0:
            print(f"verified PDF hashes {number}/{len(documents)}", flush=True)

    ids, fingerprints, sentence_splits = set(), {}, {}
    doc_splits, group_splits = defaultdict(set), defaultdict(set)
    source_counts, institution_counts, review_reasons = Counter(), Counter(), Counter()
    split_counts, year_counts = Counter(), Counter()
    for split in SPLITS:
        for chunk in rows(source / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl"):
            cid, did = chunk["chunk_id"], chunk["document_id"]
            doc = documents.get(did)
            split_counts[split] += 1
            if cid in ids:
                errors.append({"kind": "duplicate_chunk_id", "chunk_id": cid})
            ids.add(cid)
            if not doc or doc["source_pdf_sha256"] != chunk["source_pdf_sha256"]:
                errors.append({"kind": "source_pdf_hash_or_doc_mismatch", "chunk_id": cid})
                continue
            if chunk["split"] != split or doc["split"] != split:
                errors.append({"kind": "split_mismatch", "chunk_id": cid})
            doc_splits[did].add(split)
            group_splits[chunk["group_id"]].add(split)
            if chunk["text_sha256"] != hashlib.sha256(chunk["text"].encode()).hexdigest():
                errors.append({"kind": "chunk_text_hash_mismatch", "chunk_id": cid})
            if not 64 <= chunk["approx_tokens"] <= 256 or chunk["sentence_count"] < 2:
                errors.append({"kind": "invalid_chunk_size", "chunk_id": cid})
            if chunk.get("overlap_sentence_ids"):
                errors.append({"kind": "overlap_in_canonical_chunk", "chunk_id": cid})
            if not chunk.get("source_spans") or not chunk.get("section_id"):
                errors.append({"kind": "missing_source_lineage_or_section", "chunk_id": cid})
            for span in chunk.get("source_spans", []):
                if not 0 <= span["page_index"] < page_counts.get(did, 0):
                    errors.append({"kind": "invalid_source_page", "chunk_id": cid})
                    break
                if span["source_start"] >= span["source_end"]:
                    errors.append({"kind": "invalid_source_offset", "chunk_id": cid})
                    break
            fp = chunk["fingerprint_sha256"]
            if fp in fingerprints:
                errors.append({"kind": "duplicate_text_fingerprint", "chunk_id": cid,
                               "other": fingerprints[fp]})
            fingerprints[fp] = cid
            for sid in chunk["sentence_ids"]:
                earlier = sentence_splits.setdefault(sid, split)
                if earlier != split:
                    errors.append({"kind": "sentence_cross_split", "chunk_id": cid})
            source_counts[chunk.get("candidate_origin", "unknown")] += 1
            institution_counts[doc.get("institution_id") or "unknown"] += 1
            year_counts[str(doc.get("year"))] += 1
            review_reasons.update(chunk.get("review_reasons", []))
    errors.extend({"kind": "document_cross_split", "document_id": did}
                  for did, values in doc_splits.items() if len(values) > 1)
    errors.extend({"kind": "group_cross_split", "group_id": gid}
                  for gid, values in group_splits.items() if len(values) > 1)
    if sum(split_counts.values()) != manifest["pass_candidate_chunk_count"]:
        errors.append({"kind": "manifest_chunk_count_mismatch"})
    if manifest.get("rebuild_errors"):
        errors.append({"kind": "rebuild_error_documents", "count": len(manifest["rebuild_errors"])})
    report = {"candidate": source.name, "status": "HOLD_PENDING_VISUAL_AND_SCOPE_GATES",
        "chunks": sum(split_counts.values()), "documents": len(doc_splits),
        "pdf_hashes_checked": len(page_counts), "split_counts": dict(split_counts),
        "origin_counts": dict(source_counts), "institution_counts": dict(institution_counts),
        "year_counts": dict(year_counts), "review_reason_counts": dict(review_reasons),
        "error_count": len(errors), "errors_first_100": errors[:100],
        "formal_pdf_visual_attestations": 0,
        "ready_for_bulk_ai_generation": False}
    path = source / "audit/validation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: value for k, value in report.items()
                      if k not in {"institution_counts", "year_counts", "review_reason_counts", "errors_first_100"}},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
