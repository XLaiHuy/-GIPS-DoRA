"""Keep thesis and capstone candidates in the core cohort, without promoting them."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from human_v3.core import JsonlWriter, rows

ROOT = Path(__file__).resolve().parents[1]
SPLITS = ("train", "dev", "test")
CORE_TYPES = {"bachelor_thesis", "master_thesis", "doctoral_thesis", "capstone_project"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "human_v3_2_strict_candidate_v81_20261007")
    parser.add_argument("--output", type=Path, default=ROOT / "human_v3_2_thesis_candidate_v83_20261007")
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if output.exists():
        raise SystemExit(f"Refusing to overwrite: {output}")
    docs = {row["document_id"]: row for row in rows(source / "documents.jsonl")}
    count, types, splits, core_docs, extra_docs = Counter(), Counter(), Counter(), set(), set()
    for split in SPLITS:
        core_path = output / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl"
        extra_path = output / "supplementary_non_thesis/pass_candidate/t192" / f"{split}.jsonl"
        quarantine_path = output / "chunk_streams/quarantine/t192" / f"{split}.jsonl"
        with JsonlWriter(core_path) as core, JsonlWriter(extra_path) as extra, JsonlWriter(quarantine_path) as quarantine:
            for chunk in rows(source / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl"):
                doc = docs.get(chunk["document_id"])
                if doc is None:
                    raise ValueError(f"Missing document: {chunk['document_id']}")
                kind = doc.get("document_type_id", "unknown")
                if kind in CORE_TYPES:
                    core.write(chunk)
                    core_docs.add(chunk["document_id"])
                    count["core"] += 1
                    splits[split] += 1
                else:
                    extra.write({**chunk, "cohort": "supplementary_non_thesis",
                                 "generation_ready": False, "model_input_ready": False})
                    extra_docs.add(chunk["document_id"])
                    count["supplementary"] += 1
                    types[kind] += 1
            for chunk in rows(source / "chunk_streams/quarantine/t192" / f"{split}.jsonl"):
                quarantine.write(chunk)
                count["quarantine"] += 1
    with JsonlWriter(output / "documents.jsonl") as writer:
        for doc in docs.values():
            writer.write(doc)
    previous = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    manifest = {**previous, "source": source.name, "status": "candidate_not_ready",
                "cohort": "thesis_and_capstone_only", "pass_candidate_chunk_count": count["core"],
                "document_count": len(core_docs), "split_counts": dict(splits),
                "supplementary_non_thesis_chunks": count["supplementary"],
                "supplementary_non_thesis_documents": len(extra_docs),
                "supplementary_document_type_counts": dict(types),
                "quarantine_chunk_count": count["quarantine"],
                "formal_pdf_visual_attestations": 0,
                "known_limits": ["No final PDF visual attestations or independent crosschecks.",
                                 "Candidate count is below the 20000-chunk release minimum."]}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")
    print(json.dumps({"candidate": output.name, "core_chunks": count["core"],
                      "core_documents": len(core_docs), "supplementary_chunks": count["supplementary"],
                      "supplementary_documents": len(extra_docs), "supplementary_types": dict(types),
                      "quarantine_chunks": count["quarantine"], "gap_to_20000": 20000 - count["core"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
