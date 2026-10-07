"""Re-evaluate flagged thesis body boundaries at source-document level."""
from __future__ import annotations

import json
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from human_v3.core import JsonlWriter, rows, sha
from human_v3.extraction import process_source

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "human_v3_2_rebuild_v76_20261007"
CONFIG = ROOT / "human_dataset_pipeline/human_v3/config_v3_5_recovery.json"


def main():
    target = SOURCE / "audit/body_bounds_audit.jsonl"
    if target.exists():
        raise SystemExit(f"Output exists: {target}")
    documents = {doc["document_id"]: doc for doc in rows(SOURCE / "documents.jsonl")}
    flagged = defaultdict(list)
    for split in ("train", "dev", "test"):
        for chunk in rows(SOURCE / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl"):
            if ("body_boundaries_require_review" in chunk.get("review_reasons", [])
                    or chunk.get("rebuild_body_bounds_requires_review")):
                flagged[chunk["document_id"]].append(chunk)
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    jobs = []
    for did in sorted(flagged):
        existing = SOURCE / "shards" / did / "done.json"
        if existing.is_file():
            continue
        doc = documents[did]
        path = Path(doc["source_path"])
        if not path.is_absolute():
            path = ROOT / path
        jobs.append({"path": str(path), "shard": str(SOURCE / "body_boundary_shards" / did),
                     "document_id": did, "source_sha256": doc["source_pdf_sha256"],
                     "job_hash": sha(json.dumps([doc["source_pdf_sha256"], config,
                                                   "body_bounds_review_v76"], sort_keys=True)),
                     "config": config, "repair_visual_spacing": False})
    done = {}
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(process_source, job): job["document_id"] for job in jobs}
        for number, future in enumerate(as_completed(futures), 1):
            did = futures[future]
            try:
                done[did] = future.result()
            except Exception as exc:
                done[did] = {"error": f"{type(exc).__name__}: {exc}"}
            if number % 10 == 0 or number == len(jobs):
                print(f"body-boundary replay {number}/{len(jobs)} PDFs", flush=True)
    counts = defaultdict(int)
    with JsonlWriter(target) as writer:
        for did, chunks in sorted(flagged.items()):
            status_path = SOURCE / "shards" / did / "done.json"
            status = (json.loads(status_path.read_text(encoding="utf-8"))
                      if status_path.is_file() else done.get(did, {"error": "missing_result"}))
            bounds = status.get("bounds") or {}
            start = bounds.get("start", [0, 0])[0]
            end = bounds.get("end_exclusive", [status.get("page_count", 0), 0])[0]
            outside = [chunk["chunk_id"] for chunk in chunks
                       if any(span["page_index"] < start
                              or span["page_index"] > end
                              or (span["page_index"] == end
                                  and bounds.get("end_exclusive", [end, 0])[1] == 0)
                              for span in chunk["source_spans"])]
            toc_inside = any(start <= page < end for page in bounds.get("toc_pages", []))
            resolved = bool(bounds.get("start_detected") and bounds.get("end_detected")
                            and not bounds.get("requires_review") and not outside
                            and not toc_inside
                            and not status.get("error"))
            decision = "source_bounds_detected_candidate" if resolved else "requires_pdf_boundary_review"
            counts[decision] += 1
            writer.write({"document_id": did, "candidate_chunks": len(chunks),
                          "bounds": bounds, "outside_page_chunk_ids": outside,
                          "error": status.get("error"), "toc_inside_detected_body": toc_inside,
                          "decision": decision})
    summary = {"flagged_document_count": len(flagged), "new_replays": len(jobs),
               "decision_counts": dict(counts),
               "note": "Detected headings are machine evidence; source_bounds_detected_candidate still needs visual spot-check before release."}
    (SOURCE / "audit/body_bounds_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
