"""Rebuild two visually verified fused-space PDFs from glyph geometry.

Old malformed chunks stay quarantined. This produces candidates only; the
repaired text must still pass an independent PDF sample before release.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from human_v3.chunking import build_chunk_streams
from human_v3.core import JsonlWriter, file_sha, rows, sha
from human_v3.extraction import process_source
from human_v3.quality import chunk_quality_reasons, chunk_quality_warnings
from human_v3.triage import chunk_scope_profile
from audit_human_v76_scope import classify

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "human_v3_2_screened_candidate_v77d_20261007"
ORIGINAL = ROOT / "human_v3_2_rebuild_v76_20261007"
OUT = ROOT / "human_v3_2_repaired_candidate_v78b_20261007"
CONFIG = ROOT / "human_dataset_pipeline/human_v3/config_v3_5_recovery.json"
DOC_IDS = ("doc3_721498c6c155d6cee90d2741", "doc3_46179cb4edf5f29af98fefc1")
SPLITS = ("train", "dev", "test")
BOUNDARY_OVERRIDES = {
    # Rendered PDF pages 20 and 91 were visually checked. The front-matter
    # table of contents begins on page 13, after a misleading abstract heading.
    "doc3_721498c6c155d6cee90d2741": (19, 90),
}


def shingles(text: str) -> set[tuple[str, ...]]:
    words = text.casefold().split()
    return {tuple(words[index:index + 5]) for index in range(max(0, len(words) - 4))}


def known_rejected_text() -> dict[str, list[set[tuple[str, ...]]]]:
    result = {did: [] for did in DOC_IDS}
    v75 = ROOT / "human_v3_2_screened_candidate_v75b_20261007"
    for chunk in rows(v75 / "quarantine/t192/screened_rejects.jsonl"):
        if chunk["document_id"] in result and any(
                reason in chunk["v75_screening_reasons"] for reason in
                ("v74_text_review_reject", "v74_text_review_uncertain")):
            result[chunk["document_id"]].append(shingles(chunk["text"]))
    v73 = ROOT / "human_v3_2_ready_v73_20261007"
    for name in ("known_review_rejects", "automatic_risk_rejects"):
        for chunk in rows(v73 / "quarantine/t192" / f"{name}.jsonl.gz"):
            if chunk["document_id"] in result:
                result[chunk["document_id"]].append(shingles(chunk["text"]))
    return result


def main() -> None:
    if OUT.exists():
        raise SystemExit(f"Output exists: {OUT}")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    documents = {row["document_id"]: row for row in rows(ORIGINAL / "documents.jsonl")}
    known_bad = known_rejected_text()
    repairs = []
    repair_quarantine = []
    status_rows = []
    for did in DOC_IDS:
        doc = documents[did]
        source = ROOT / doc["source_path"]
        if file_sha(source) != doc["source_pdf_sha256"]:
            raise ValueError(f"PDF SHA mismatch: {did}")
        job = {"path": str(source), "shard": str(OUT / "shards" / did),
               "document_id": did, "source_sha256": doc["source_pdf_sha256"],
               "job_hash": sha(json.dumps([did, doc["source_pdf_sha256"], config,
                                            "glyph_gap_recovery_v78"], sort_keys=True)),
               "config": config, "repair_visual_spacing": True}
        status = process_source(job)
        if status.get("error"):
            raise RuntimeError(f"{did}: {status['error']}")
        bounds = status.get("bounds") or {}
        start = bounds.get("start", [0])[0]
        end = bounds.get("end_exclusive", [status.get("page_count", 0)])[0]
        boundary_override = BOUNDARY_OVERRIDES.get(did)
        if boundary_override:
            start, end = boundary_override
        bound_ok = bool(bounds.get("start_detected") and bounds.get("end_detected")
                        and not bounds.get("requires_review")
                        and (boundary_override or not any(
                            start <= page < end for page in bounds.get("toc_pages", []))))
        sentences = list(rows(OUT / "shards" / did / "sentences.jsonl.gz"))
        if boundary_override:
            sentences = [sentence for sentence in sentences
                         if sentence.get("source_spans") and all(
                             start <= span["page_index"] < end
                             for span in sentence["source_spans"])]
        for sentence in sentences:
            sentence.update(group_id=doc["group_id"], split=doc["split"],
                            paper_title=doc.get("title", ""),
                            source_pdf_sha256=doc["source_pdf_sha256"])
        windows = [row for row in build_chunk_streams(sentences, config)
                   if row["stream_id"] == "t192"]
        counts = Counter()
        for chunk in windows:
            chunk["candidate_origin"] = "rebuild_v78_glyph_gap"
            chunk["source_offset_basis"] = "visual_glyph_geometry_recovery"
            chunk["visual_spacing_recovery"] = True
            chunk["pre_review_candidate"] = True
            chunk["release_status"] = "pass_candidate"
            chunk["candidate_review_status"] = "screened_candidate_requires_pdf_review"
            chunk["generation_ready"] = False
            chunk["model_input_ready"] = False
            chunk["formal_pdf_visual_attestation"] = False
            chunk["rights_basis"] = "dataset_owner_prior_conversation_authorized_internal_research_use"
            chunk["quality_warnings"] = chunk_quality_warnings(chunk, config)
            chunk["scope_profile"] = chunk_scope_profile(chunk)
            reasons = chunk_quality_reasons(chunk, {"extraction": status}, config)
            screening = classify(chunk["text"])
            if screening["decision"] == "reject_high_precision":
                reasons.append("screen_v3_noncomputing_high_risk")
            current_shingles = shingles(chunk["text"])
            if any(other and current_shingles and
                   len(current_shingles & other) / min(len(current_shingles), len(other)) >= .5
                   for other in known_bad[did]):
                reasons.append("overlaps_known_rejected_text")
            if not bound_ok:
                reasons.append("pdf_body_boundary_unresolved")
            if any(span["page_index"] < start or span["page_index"] >= end
                   for span in chunk["source_spans"]):
                reasons.append("chunk_outside_detected_body")
            chunk["review_reasons"] = sorted(set(reasons))
            if reasons:
                chunk["release_status"] = "quarantine"
                chunk["v78_quarantine_reasons"] = sorted(set(reasons))
                repair_quarantine.append(chunk)
                counts["quarantine"] += 1
            else:
                repairs.append(chunk)
                counts["candidate"] += 1
        status_rows.append({"document_id": did, "split": doc["split"],
                            "bounds": bounds, "bound_ok": bound_ok,
                            "visual_boundary_override": boundary_override,
                            "sentence_count": len(sentences),
                            "glyph_recovery_insertions": status.get("visual_spacing_insertion_count"),
                            "counts": dict(counts)})
        print(f"{did}: {dict(counts)}, bound_ok={bound_ok}", flush=True)
    existing_fingerprints = set()
    for split in SPLITS:
        for row in rows(SOURCE / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl"):
            existing_fingerprints.add(row["fingerprint_sha256"])
    unique_repairs = []
    for chunk in repairs:
        fp = chunk["fingerprint_sha256"]
        if fp in existing_fingerprints:
            chunk["release_status"] = "quarantine"
            chunk["v78_quarantine_reasons"] = ["duplicate_text_fingerprint"]
            repair_quarantine.append(chunk)
        else:
            existing_fingerprints.add(fp)
            unique_repairs.append(chunk)
    for split in SPLITS:
        with JsonlWriter(OUT / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl") as writer:
            for chunk in rows(SOURCE / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl"):
                writer.write(chunk)
            for chunk in unique_repairs:
                if chunk["split"] == split:
                    writer.write(chunk)
        with JsonlWriter(OUT / "chunk_streams/quarantine/t192" / f"{split}.jsonl") as writer:
            for chunk in rows(SOURCE / "chunk_streams/quarantine/t192" / f"{split}.jsonl"):
                writer.write(chunk)
            for chunk in repair_quarantine:
                if chunk["split"] == split:
                    writer.write(chunk)
    with JsonlWriter(OUT / "documents.jsonl") as writer:
        for doc in rows(SOURCE / "documents.jsonl"):
            writer.write(doc)
        for did in DOC_IDS:
            if not any(chunk["document_id"] == did for chunk in unique_repairs):
                continue
            doc = dict(documents[did])
            doc["rights_status"] = "internal_research_cleared"
            doc["rights_evidence"] = "dataset_owner_prior_conversation_authorized_internal_research_use"
            doc["rights_reviewed_by"] = "dataset_owner"
            doc["rights_reviewed_at"] = None
            writer.write(doc)
    old_manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    split_counts = dict(old_manifest["split_counts"])
    for chunk in unique_repairs:
        split_counts[chunk["split"]] += 1
    manifest = {**old_manifest, "source": SOURCE.name,
                "status": "candidate_not_ready", "split_counts": split_counts,
                "pass_candidate_chunk_count": sum(split_counts.values()),
                "document_count": old_manifest["document_count"] +
                                  len({chunk["document_id"] for chunk in unique_repairs}),
                "quarantine_chunk_count": old_manifest["quarantine_chunk_count"] +
                                          len(repair_quarantine),
                "v78_repaired_candidate_chunks": len(unique_repairs),
                "v78_repair_quarantine_chunks": len(repair_quarantine),
                "v78_visual_spacing_document_count": len(DOC_IDS),
                "formal_pdf_visual_attestations": 0}
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)
                                        + "\n", encoding="utf-8")
    (OUT / "audit").mkdir(parents=True, exist_ok=True)
    (OUT / "audit/visual_spacing_rebuild.json").write_text(
        json.dumps(status_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"candidate_chunks": manifest["pass_candidate_chunk_count"],
                      "recovered_chunks": len(unique_repairs),
                      "quarantined_new_windows": len(repair_quarantine)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
