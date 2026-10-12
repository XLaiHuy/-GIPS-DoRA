"""Re-extract PDFs affected by image-backed bullets and repack t192 prose.

The source is v78b. All affected documents are rebuilt from PDF pages; no
individual candidate text is hand-edited. The result remains unreviewed.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from human_v3.chunking import build_chunk_streams
from human_v3.core import JsonlWriter, file_sha, rows, sha
from human_v3.extraction import process_source
from human_v3.quality import chunk_quality_reasons, chunk_quality_warnings
from human_v3.triage import chunk_scope_profile
from audit_human_v76_scope import classify

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "human_v3_2_repaired_candidate_v78b_20261007"
OUT = ROOT / "human_v3_2_rebuilt_candidate_v79_20261007"
CONFIG = ROOT / "human_dataset_pipeline/human_v3/config_v3_5_recovery.json"
HITS = SOURCE / "audit/layout_residual_image_markers_v2_hits.jsonl"
SPLITS = ("train", "dev", "test")


def shingles(text: str) -> set[tuple[str, ...]]:
    words = text.casefold().split()
    return {tuple(words[index:index + 5]) for index in range(max(0, len(words) - 4))}


def known_bad_text(doc_ids: set[str]) -> dict[str, list[set[tuple[str, ...]]]]:
    bad = defaultdict(list)
    v75 = ROOT / "human_v3_2_screened_candidate_v75b_20261007"
    for chunk in rows(v75 / "quarantine/t192/screened_rejects.jsonl"):
        if chunk["document_id"] in doc_ids and any(
                reason in chunk["v75_screening_reasons"] for reason in
                ("v74_text_review_reject", "v74_text_review_uncertain")):
            bad[chunk["document_id"]].append(shingles(chunk["text"]))
    v73 = ROOT / "human_v3_2_ready_v73_20261007"
    for name in ("known_review_rejects", "automatic_risk_rejects"):
        for chunk in rows(v73 / "quarantine/t192" / f"{name}.jsonl.gz"):
            if chunk["document_id"] in doc_ids:
                bad[chunk["document_id"]].append(shingles(chunk["text"]))
    return bad


def main() -> None:
    if (OUT / "manifest.json").exists():
        raise SystemExit(f"Output exists: {OUT}")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    affected = {hit["document_id"] for hit in rows(HITS)}
    if not affected:
        raise ValueError("No affected documents to rebuild")
    documents = {doc["document_id"]: doc for doc in rows(SOURCE / "documents.jsonl")}
    old_by_doc = defaultdict(list)
    for split in SPLITS:
        for chunk in rows(SOURCE / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl"):
            if chunk["document_id"] in affected:
                old_by_doc[chunk["document_id"]].append(chunk)
    if affected != set(old_by_doc):
        raise ValueError("Hit document missing from candidate")
    known_bad = known_bad_text(affected)
    jobs = []
    for did in sorted(affected):
        doc = documents[did]
        path = Path(doc["source_path"])
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file() or file_sha(path) != doc["source_pdf_sha256"]:
            raise ValueError(f"Source PDF missing/hash mismatch: {did}")
        jobs.append({"path": str(path), "shard": str(OUT / "shards" / did),
                     "document_id": did, "source_sha256": doc["source_pdf_sha256"],
                     "job_hash": sha(json.dumps([doc["source_pdf_sha256"], config,
                                                   "image_marker_lists_v79"], sort_keys=True)),
                     "config": config, "repair_visual_spacing": False})
    outcomes = {}
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(process_source, job): job["document_id"] for job in jobs}
        for count, future in enumerate(as_completed(futures), 1):
            did = futures[future]
            try:
                outcomes[did] = future.result()
            except Exception as exc:
                outcomes[did] = {"error": f"{type(exc).__name__}: {exc}"}
            if count % 20 == 0 or count == len(jobs):
                print(f"rebuilt {count}/{len(jobs)} PDFs", flush=True)
    failures = {did: status.get("error") for did, status in outcomes.items()
                if status.get("error")}
    if failures:
        raise RuntimeError(f"Rebuild failed: {failures}")

    rebuilt = []
    rejected = []
    counts = Counter()
    for did in sorted(affected):
        doc, status = documents[did], outcomes[did]
        sentences = list(rows(OUT / "shards" / did / "sentences.jsonl.gz"))
        for sentence in sentences:
            sentence.update(group_id=doc["group_id"], split=doc["split"],
                            paper_title=doc.get("title", ""),
                            source_pdf_sha256=doc["source_pdf_sha256"])
        prior_reasons = {reason for chunk in old_by_doc[did]
                         for reason in chunk.get("review_reasons", [])
                         if reason != "rights_review_pending"}
        windows = [chunk for chunk in build_chunk_streams(sentences, config)
                   if chunk["stream_id"] == "t192"]
        bounds = status.get("bounds") or {}
        start = bounds.get("start", [0])[0]
        end = bounds.get("end_exclusive", [status.get("page_count", 0)])[0]
        body_ok = bool(bounds.get("start_detected") and bounds.get("end_detected")
                       and not bounds.get("requires_review") and
                       not any(start <= page < end for page in bounds.get("toc_pages", [])))
        for chunk in windows:
            chunk["candidate_origin"] = "rebuild_v79_image_marker_lists"
            chunk["source_offset_basis"] = "raw_source_extraction"
            chunk["pre_review_candidate"] = True
            chunk["candidate_review_status"] = "screened_candidate_requires_pdf_review"
            chunk["generation_ready"] = False
            chunk["model_input_ready"] = False
            chunk["formal_pdf_visual_attestation"] = False
            chunk["rights_basis"] = "dataset_owner_prior_conversation_authorized_internal_research_use"
            chunk["quality_warnings"] = chunk_quality_warnings(chunk, config)
            chunk["scope_profile"] = chunk_scope_profile(chunk)
            reasons = chunk_quality_reasons(chunk, {"extraction": status}, config)
            if classify(chunk["text"])["decision"] == "reject_high_precision":
                reasons.append("screen_v3_noncomputing_high_risk")
            if not body_ok or any(span["page_index"] < start or span["page_index"] >= end
                                  for span in chunk["source_spans"]):
                prior_reasons.add("body_boundaries_require_review")
            current = shingles(chunk["text"])
            if any(other and current and len(current & other) /
                   min(len(current), len(other)) >= .5 for other in known_bad[did]):
                reasons.append("overlaps_known_rejected_text")
            chunk["review_reasons"] = sorted(prior_reasons)
            chunk["v79_screening_reasons"] = sorted(set(reasons))
            chunk["release_status"] = "quarantine" if reasons else "pass_candidate"
            if reasons:
                rejected.append(chunk)
                counts["new_quarantine"] += 1
            else:
                rebuilt.append(chunk)
                counts["new_candidate"] += 1
        counts["windows"] += len(windows)

    # Keep all unaffected content; rebuilt documents supersede their old chunks.
    unique = []
    seen_ids, seen_fingerprints = set(), set()
    for split in SPLITS:
        for chunk in rows(SOURCE / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl"):
            if chunk["document_id"] not in affected:
                unique.append(chunk)
                seen_ids.add(chunk["chunk_id"])
                seen_fingerprints.add(chunk["fingerprint_sha256"])
    for chunk in rebuilt:
        if chunk["chunk_id"] in seen_ids or chunk["fingerprint_sha256"] in seen_fingerprints:
            chunk["release_status"] = "quarantine"
            chunk["v79_screening_reasons"] = ["duplicate_chunk_id_or_text_fingerprint"]
            rejected.append(chunk)
            counts["new_duplicate_quarantine"] += 1
        else:
            unique.append(chunk)
            seen_ids.add(chunk["chunk_id"])
            seen_fingerprints.add(chunk["fingerprint_sha256"])
    for split in SPLITS:
        with JsonlWriter(OUT / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl") as writer:
            for chunk in unique:
                if chunk["split"] == split:
                    writer.write(chunk)
        with JsonlWriter(OUT / "chunk_streams/quarantine/t192" / f"{split}.jsonl") as writer:
            for chunk in rows(SOURCE / "chunk_streams/quarantine/t192" / f"{split}.jsonl"):
                writer.write(chunk)
            for chunk in rejected:
                if chunk["split"] == split:
                    writer.write(chunk)
    with JsonlWriter(OUT / "documents.jsonl") as writer:
        for doc in rows(SOURCE / "documents.jsonl"):
            writer.write(doc)
    with JsonlWriter(OUT / "audit/superseded_v78b_chunks.jsonl") as writer:
        for did in sorted(affected):
            for chunk in old_by_doc[did]:
                writer.write({"chunk_id": chunk["chunk_id"], "document_id": did,
                              "reason": "superseded_by_source_pdf_rebuild"})
    split_counts = dict(Counter(chunk["split"] for chunk in unique))
    report = {"source": SOURCE.name, "status": "candidate_not_ready",
              "affected_document_count": len(affected),
              "superseded_old_chunk_count": sum(len(x) for x in old_by_doc.values()),
              "rebuilt_window_count": counts["windows"],
              "rebuilt_pass_chunk_count": counts["new_candidate"] -
                                         counts["new_duplicate_quarantine"],
              "pass_candidate_chunk_count": len(unique),
              "quarantine_chunk_count": sum(1 for split in SPLITS for _ in rows(
                  OUT / "chunk_streams/quarantine/t192" / f"{split}.jsonl")),
              "split_counts": split_counts,
              "document_count": len({chunk["document_id"] for chunk in unique}),
              "scope_review_flag_chunks": sum("computing_scope_requires_review" in
                                               chunk.get("review_reasons", []) for chunk in unique),
              "body_boundary_review_flag_chunks": sum(bool("body_boundaries_require_review" in
                  chunk.get("review_reasons", []) or chunk.get("rebuild_body_bounds_requires_review"))
                  for chunk in unique),
              "formal_pdf_visual_attestations": 0,
              "rights_scope": "internal_research_only_owner_asserted",
              "known_limits": ["No final 400-card PDF visual review has been completed.",
                               "Semantic scope, provenance, and boundary flags remain open."]}
    (OUT / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)
                                       + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
