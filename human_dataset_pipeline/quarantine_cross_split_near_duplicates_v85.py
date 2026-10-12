"""Remove only cross-split near-duplicate chunks, preserving source documents."""
from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path

from human_v3.core import JsonlWriter, rows

ROOT = Path(__file__).resolve().parents[1]
SPLITS = ("train", "dev", "test")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path,
                        default=ROOT / "human_v3_2_year_recovered_candidate_v84_20261007")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "human_v3_2_dedup_candidate_v85_20261007")
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if output.exists():
        raise SystemExit(f"Refusing to overwrite: {output}")
    audit = json.loads((source / "audit/hard_rules.json").read_text(encoding="utf-8"))
    if audit.get("candidate") != source.name or audit.get("error_reason_counts") != {
            "cross_split_near_duplicate": 1}:
        raise ValueError("Expected only cross-split near-duplicate audit findings")
    pairs = list(rows(source / "audit/cross_split_near_duplicate_pairs.jsonl"))
    blocked = {edge[key] for edge in pairs for key in ("left_chunk_id", "right_chunk_id")}
    counts, docs = Counter(), set()
    for split in SPLITS:
        pass_path = output / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl"
        quarantine_path = output / "chunk_streams/quarantine/t192" / f"{split}.jsonl"
        with JsonlWriter(pass_path) as passed, JsonlWriter(quarantine_path) as quarantine:
            for chunk in rows(source / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl"):
                if chunk["chunk_id"] in blocked:
                    rejected = dict(chunk)
                    rejected["release_status"] = "quarantine"
                    rejected["candidate_review_status"] = "cross_split_near_duplicate"
                    rejected["generation_ready"] = False
                    rejected["model_input_ready"] = False
                    rejected["strict_screening_reasons"] = ["cross_split_near_duplicate"]
                    rejected["review_reasons"] = sorted(set(chunk.get("review_reasons", [])) |
                                                          {"cross_split_near_duplicate"})
                    quarantine.write(rejected)
                    counts["newly_quarantined"] += 1
                else:
                    passed.write(chunk)
                    docs.add(chunk["document_id"])
                    counts[f"pass_{split}"] += 1
            for chunk in rows(source / "chunk_streams/quarantine/t192" / f"{split}.jsonl"):
                quarantine.write(chunk)
                counts["quarantine"] += 1
    shutil.copyfile(source / "documents.jsonl", output / "documents.jsonl")
    for split in SPLITS:
        src = source / "supplementary_non_thesis/pass_candidate/t192" / f"{split}.jsonl"
        dst = output / "supplementary_non_thesis/pass_candidate/t192" / f"{split}.jsonl"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    total = sum(counts[f"pass_{split}"] for split in SPLITS)
    manifest.update({"source": source.name, "status": "candidate_not_ready",
                     "pass_candidate_chunk_count": total, "document_count": len(docs),
                     "quarantine_chunk_count": counts["quarantine"] + counts["newly_quarantined"],
                     "split_counts": {split: counts[f"pass_{split}"] for split in SPLITS},
                     "newly_quarantined_cross_split_near_duplicate_chunks": counts["newly_quarantined"],
                     "cross_split_near_duplicate_pairs_screened": len(pairs),
                     "known_limits": ["Below 20000 eligible chunks.",
                                      "No final 400-card PDF visual review or 40 independent crosschecks."]})
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")
    print(json.dumps({"candidate": output.name, "pass_chunks": total,
                      "documents": len(docs), "near_duplicate_chunks_quarantined":
                      counts["newly_quarantined"], "quarantine_chunks": manifest["quarantine_chunk_count"],
                      "gap_to_20000": 20000 - total}, ensure_ascii=False))


if __name__ == "__main__":
    main()
