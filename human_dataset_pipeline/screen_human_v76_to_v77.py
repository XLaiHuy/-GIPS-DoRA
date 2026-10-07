"""Conservatively quarantine unresolved v76 scope and body-boundary cases.

This is a candidate selection step, not a visual review or a release approval.
It preserves source text and sentence lineage without splitting or overlap.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from human_v3.core import JsonlWriter, dump, rows

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "human_v3_2_rebuild_v76_20261007"
TARGET = ROOT / "human_v3_2_screened_candidate_v77d_20261007"
SPLITS = ("train", "dev", "test")


def main() -> None:
    if TARGET.exists():
        raise SystemExit(f"Output exists: {TARGET}")
    scope = {row["chunk_id"]: row for row in rows(SOURCE / "audit/scope_screen_v3.jsonl")}
    corruption = json.loads((ROOT / "human_dataset_pipeline/known_source_text_layer_corruption_v77.json")
                            .read_text(encoding="utf-8"))
    corrupted_docs = {row["document_id"] for row in corruption["documents"]}
    bounds = {row["document_id"]: row for row in rows(SOURCE / "audit/body_bounds_audit.jsonl")}
    source_manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    documents = list(rows(SOURCE / "documents.jsonl"))
    counts = Counter()
    kept_doc_ids = set()
    kept_groups = {}
    for split in SPLITS:
        source_path = SOURCE / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl"
        pass_path = TARGET / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl"
        quarantine_path = TARGET / "chunk_streams/quarantine/t192" / f"{split}.jsonl"
        with JsonlWriter(pass_path) as passed, JsonlWriter(quarantine_path) as quarantined:
            for chunk in rows(source_path):
                cid, did = chunk["chunk_id"], chunk["document_id"]
                result = scope.get(cid)
                body = bounds.get(did)
                reasons = []
                if result and result["decision"] == "reject_high_precision":
                    reasons.append("screen_v3_noncomputing_high_risk")
                elif (result and result["prior_scope_flag"]
                      and result["decision"] == "unresolved"):
                    reasons.append("computing_scope_unresolved")
                bounds_data = body["bounds"] if body else {}
                start_page = bounds_data.get("start", [0])[0]
                end_page = bounds_data.get("end_exclusive", [0])[0]
                toc_inside = any(start_page <= page < end_page
                                 for page in bounds_data.get("toc_pages", []))
                if body and (body["decision"] == "requires_pdf_boundary_review" or toc_inside):
                    reasons.append("pdf_body_boundary_unresolved")
                if did in corrupted_docs:
                    reasons.append("source_text_layer_corruption_visual_confirmed")
                if reasons:
                    quarantined.write({"chunk_id": cid, "document_id": did,
                                       "split": split, "text": chunk["text"],
                                       "source_pdf_sha256": chunk["source_pdf_sha256"],
                                       "source_spans": chunk["source_spans"],
                                       "quarantine_reasons": reasons,
                                       "candidate_origin": chunk.get("candidate_origin"),
                                       "source_chunk": "v76"})
                    counts["quarantine"] += 1
                    for reason in reasons:
                        counts[reason] += 1
                    continue
                chunk = dict(chunk)
                chunk["candidate_review_status"] = "screened_candidate_requires_pdf_review"
                chunk["generation_ready"] = False
                chunk["model_input_ready"] = False
                chunk["formal_pdf_visual_attestation"] = False
                # The owner has asserted internal research rights. The old
                # rights_review_pending marker predates this evidence update.
                chunk["review_reasons"] = [reason for reason in chunk.get("review_reasons", [])
                                           if reason != "rights_review_pending"]
                chunk["rights_basis"] = "dataset_owner_prior_conversation_authorized_internal_research_use"
                if body and body["decision"] == "source_bounds_detected_candidate" and not toc_inside:
                    chunk["body_bound_evidence"] = "both_source_headings_detected_pending_visual_spotcheck"
                if result and result["decision"] == "technical_evidence":
                    chunk["scope_screen_evidence"] = "technical_phrase_match_provisional"
                passed.write(chunk)
                counts[split] += 1
                kept_doc_ids.add(did)
                group = chunk["group_id"]
                if group in kept_groups and kept_groups[group] != split:
                    raise ValueError(f"group split leakage: {group}")
                kept_groups[group] = split
    with JsonlWriter(TARGET / "documents.jsonl") as writer:
        for document in documents:
            if document["document_id"] in kept_doc_ids:
                document = dict(document)
                document["rights_status"] = "internal_research_cleared"
                document["rights_evidence"] = (
                    "dataset_owner_prior_conversation_authorized_internal_research_use")
                document["rights_reviewed_by"] = "dataset_owner"
                document["rights_reviewed_at"] = None
                writer.write(document)
    # Inherited flags remain open. Automated evidence is not a PDF attestation.
    open_scope = open_body = 0
    for split in SPLITS:
        for chunk in rows(TARGET / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl"):
            if "computing_scope_requires_review" in chunk.get("review_reasons", []):
                open_scope += 1
            if ("body_boundaries_require_review" in chunk.get("review_reasons", [])
                    or chunk.get("rebuild_body_bounds_requires_review")):
                open_body += 1
    manifest = {"source": SOURCE.name, "status": "candidate_not_ready",
                "pass_candidate_chunk_count": sum(counts[split] for split in SPLITS),
                "quarantine_chunk_count": counts["quarantine"],
                "split_counts": {split: counts[split] for split in SPLITS},
                "document_count": len(kept_doc_ids),
                "scope_review_flag_chunks": open_scope,
                "body_boundary_review_flag_chunks": open_body,
                "quarantine_reason_counts": {key: value for key, value in counts.items()
                                             if key not in SPLITS and key != "quarantine"},
                "source_candidate_chunks": source_manifest["pass_candidate_chunk_count"],
                "formal_pdf_visual_attestations": 0,
                "rights_scope": "internal_research_only_owner_asserted",
                "known_limits": ["Source heading detection still needs visual review.",
                                 "Technical phrase matches alone do not prove computing scope.",
                                 "No final independent 400 plus 40 PDF review is complete."]}
    dump(TARGET / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
