"""Rebuild documents affected by PDF layout errors into an immutable candidate.

Re-extraction happens at PDF/page level. Existing chunk text is never patched.
The output is intentionally not a ready release: scope, boundary and visual
review gates still apply after this repair pass.
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

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "human_v3_2_screened_candidate_v75b_20261007"
V73 = ROOT / "human_v3_2_ready_v73_20261007"
OUT = ROOT / "human_v3_2_rebuild_v76_20261007"
CONFIG = ROOT / "human_dataset_pipeline/human_v3/config_v3_5_recovery.json"
SPLITS = ("train", "dev", "test")


def source_path(document: dict) -> Path:
    path = Path(document["source_path"])
    return path if path.is_absolute() else ROOT / path


def shingles(text: str) -> set[tuple[str, ...]]:
    words = text.casefold().split()
    return {tuple(words[i:i + 5]) for i in range(max(0, len(words) - 4))}


def main():
    if (OUT / "manifest.json").exists():
        raise SystemExit("v76 manifest already exists; immutable build will not be overwritten")
    if not (OUT / "caption_summary.json").is_file():
        raise SystemExit("Run audit_human_v75_pdf_captions.py first")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    documents = {doc["document_id"]: doc for doc in rows(SOURCE / "documents.jsonl")}
    by_doc = defaultdict(list)
    for split in SPLITS:
        for chunk in rows(SOURCE / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl"):
            by_doc[chunk["document_id"]].append(chunk)
    old_quarantine = list(rows(SOURCE / "quarantine/t192/screened_rejects.jsonl"))
    list_docs = {c["document_id"] for c in old_quarantine
                 if "pdf_wrapped_list_continuation" in c["v75_screening_reasons"]}
    caption_hits = list(rows(OUT / "caption_hits.jsonl"))
    caption_docs = {hit["document_id"] for hit in caption_hits}
    affected = list_docs | caption_docs
    previous_origin = {did: members[0]["candidate_origin"] for did, members in by_doc.items()}
    for chunk in old_quarantine:
        previous_origin.setdefault(chunk["document_id"], chunk["candidate_origin"])

    jobs, preflight_errors = [], []
    for did in sorted(affected):
        doc = documents[did]
        path = source_path(doc)
        if not path.is_file() or file_sha(path) != doc["source_pdf_sha256"]:
            preflight_errors.append({"document_id": did, "reason": "missing_or_hash_mismatch_pdf"})
            continue
        jobs.append({"path": str(path), "shard": str(OUT / "shards" / did),
                     "document_id": did, "source_sha256": doc["source_pdf_sha256"],
                     "job_hash": sha(json.dumps([doc["source_pdf_sha256"], config,
                                                   "wrapped_list_and_image_caption_v76"], sort_keys=True)),
                     "config": config, "repair_visual_spacing": False})
    done = {}
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(process_source, job): job["document_id"] for job in jobs}
        for number, future in enumerate(as_completed(futures), 1):
            did = futures[future]
            try:
                done[did] = future.result()
            except Exception as exc:
                done[did] = {"document_id": did, "error": f"{type(exc).__name__}: {exc}"}
            if number % 20 == 0 or number == len(jobs):
                print(f"rebuilt {number}/{len(jobs)} affected PDFs", flush=True)

    # Known human/text rejects remain blocked even if sentence packing changes.
    known_bad = defaultdict(list)
    for chunk in old_quarantine:
        if any(reason in chunk["v75_screening_reasons"]
               for reason in ("v74_text_review_reject", "v74_text_review_uncertain")):
            known_bad[chunk["document_id"]].append((chunk["chunk_id"], shingles(chunk["text"])))
    for name in ("known_review_rejects", "automatic_risk_rejects"):
        for chunk in rows(V73 / "quarantine/t192" / f"{name}.jsonl.gz"):
            known_bad[chunk["document_id"]].append((chunk["chunk_id"], shingles(chunk["text"])))

    pass_rows, rejected, superseded = [], [], []
    for did, members in by_doc.items():
        if did in affected:
            superseded.extend(members)
        else:
            pass_rows.extend(members)
    for chunk in old_quarantine:
        if chunk["document_id"] in affected:
            superseded.append(chunk)
        else:
            rejected.append(chunk)

    rebuilt_counts = Counter()
    for did in sorted(affected):
        status = done.get(did)
        if not status or status.get("error"):
            preflight_errors.append({"document_id": did,
                                     "reason": str(status.get("error") if status else "not_processed")})
            continue
        doc = documents[did]
        sentences = list(rows(OUT / "shards" / did / "sentences.jsonl.gz"))
        for sentence in sentences:
            sentence.update(group_id=doc["group_id"], split=doc["split"],
                            paper_title=doc.get("title", ""),
                            source_pdf_sha256=doc["source_pdf_sha256"])
        windows = [c for c in build_chunk_streams(sentences, config) if c["stream_id"] == "t192"]
        old_review = sorted({reason for c in by_doc.get(did, [])
                             for reason in c.get("review_reasons", [])})
        for chunk in windows:
            chunk["candidate_origin"] = "rebuild_v76_" + previous_origin.get(did, "unknown")
            chunk["source_offset_basis"] = "raw_source_extraction"
            chunk["pre_review_candidate"] = True
            chunk["review_reasons"] = old_review
            chunk["quality_warnings"] = chunk_quality_warnings(chunk, config)
            chunk["scope_profile"] = chunk_scope_profile(chunk)
            chunk["rebuild_body_bounds_requires_review"] = status["bounds"]["requires_review"]
            chunk["rebuild_source_document_id"] = did
            reasons = chunk_quality_reasons(chunk, {"extraction": status}, config)
            current_shingles = shingles(chunk["text"])
            for rejected_id, rejected_shingles in known_bad.get(did, []):
                if rejected_shingles and current_shingles:
                    overlap = len(current_shingles & rejected_shingles) / min(
                        len(current_shingles), len(rejected_shingles))
                    if overlap >= .50:
                        reasons.append("overlaps_known_rejected_text")
                        chunk["prior_reject_id"] = rejected_id
                        break
            chunk["v76_screening_reasons"] = sorted(set(reasons))
            chunk["release_status"] = "quarantine" if reasons else "pass_candidate"
            if reasons:
                rejected.append(chunk)
            else:
                pass_rows.append(chunk)
                rebuilt_counts[did] += 1

    # Deduplicate before constructing any new split statistics. Keep unchanged
    # v75b records first; a rebuilt duplicate cannot silently replace one.
    pass_rows.sort(key=lambda c: (c["candidate_origin"].startswith("rebuild_v76_"),
                                  c["split"], c["document_id"], c["chunk_id"]))
    unique, fingerprints, ids = [], {}, set()
    for chunk in pass_rows:
        cid, fp = chunk["chunk_id"], chunk["fingerprint_sha256"]
        reason = ("duplicate_chunk_id" if cid in ids else
                  "duplicate_text_fingerprint" if fp in fingerprints else None)
        if reason:
            chunk["release_status"] = "quarantine"
            chunk["v76_screening_reasons"] = [reason]
            chunk["duplicate_of"] = fingerprints.get(fp)
            rejected.append(chunk)
        else:
            unique.append(chunk)
            ids.add(cid)
            fingerprints[fp] = cid

    for split in SPLITS:
        with JsonlWriter(OUT / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl") as writer:
            for chunk in unique:
                if chunk["split"] == split:
                    writer.write(chunk)
    with JsonlWriter(OUT / "quarantine/t192/rejected.jsonl") as writer:
        for chunk in rejected:
            writer.write(chunk)
    with JsonlWriter(OUT / "audit/superseded_v75_chunks.jsonl.gz") as writer:
        for chunk in superseded:
            writer.write({"chunk_id": chunk["chunk_id"], "document_id": chunk["document_id"],
                          "split": chunk["split"], "replaced_by_source_rebuild": True})
    # Record the dataset owner's prior authorization as an internal research
    # attestation. This does not assert a public redistribution license.
    with JsonlWriter(OUT / "documents.jsonl") as writer:
        for doc in documents.values():
            record = dict(doc)
            if record.get("rights_status") in (None, "pending_review"):
                record["rights_status"] = "internal_research_cleared"
                record["rights_evidence"] = "dataset_owner_prior_conversation_authorized_internal_research_use"
                record["rights_reviewed_by"] = "dataset_owner"
                record["rights_reviewed_at"] = None
            writer.write(record)
    split_docs = defaultdict(set)
    group_splits = defaultdict(set)
    for chunk in unique:
        split_docs[chunk["document_id"]].add(chunk["split"])
        group_splits[chunk["group_id"]].add(chunk["split"])
    manifest = {"source": SOURCE.name, "status": "candidate_not_ready",
        "affected_document_count": len(affected), "list_document_count": len(list_docs),
        "caption_document_count": len(caption_docs),
        "rebuild_success_count": sum(not value.get("error") for value in done.values()),
        "rebuild_errors": preflight_errors,
        "superseded_old_chunk_count": len(superseded),
        "rebuilt_pass_chunk_count": sum(rebuilt_counts.values()),
        "pass_candidate_chunk_count": len(unique), "quarantine_chunk_count": len(rejected),
        "split_counts": dict(Counter(c["split"] for c in unique)),
        "document_count": len({c["document_id"] for c in unique}),
        "scope_review_flag_chunks": sum("computing_scope_requires_review" in c.get("review_reasons", []) for c in unique),
        "body_boundary_review_flag_chunks": sum(bool(
            "body_boundaries_require_review" in c.get("review_reasons", [])
            or c.get("rebuild_body_bounds_requires_review")) for c in unique),
        "document_split_leakage": sum(len(x) > 1 for x in split_docs.values()),
        "group_split_leakage": sum(len(x) > 1 for x in group_splits.values()),
        "formal_pdf_visual_attestations": 0,
        "known_limits": ["No final 400-card PDF visual review has been completed.",
                         "Semantic computing scope and body-boundary flags remain open.",
                         "Owner rights statement covers internal research, not redistribution."]}
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in manifest.items() if k != "rebuild_errors"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
