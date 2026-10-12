"""Recover only year-only thesis chunks backed by reviewed PDF cover evidence."""
from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path

import fitz

from human_v3.core import JsonlWriter, file_sha, rows

ROOT = Path(__file__).resolve().parents[1]
SPLITS = ("train", "dev", "test")
CORE_TYPES = {"bachelor_thesis", "master_thesis", "doctoral_thesis", "capstone_project"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "human_v3_2_thesis_candidate_v83_20261007")
    parser.add_argument("--output", type=Path, default=ROOT / "human_v3_2_year_recovered_candidate_v84_20261007")
    parser.add_argument("--decisions", type=Path, default=Path(__file__).with_name("year_cover_decisions_v84.json"))
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if output.exists():
        raise SystemExit(f"Refusing to overwrite: {output}")
    decisions_path = args.decisions.resolve()
    decision_file = json.loads(decisions_path.read_text(encoding="utf-8"))
    approvals = {row["document_id"]: row for row in decision_file["decisions"]
                 if row["decision"] == "approve_year"}
    docs = {doc["document_id"]: doc for doc in rows(source / "documents.jsonl")}
    for did, evidence in approvals.items():
        doc = docs[did]
        path = Path(doc["source_path"])
        if not path.is_absolute():
            path = ROOT / path
        if (doc.get("document_type_id") not in CORE_TYPES
                or doc.get("year") != evidence["cover_year"]
                or not 1990 <= evidence["cover_year"] <= 2022
                or doc["source_pdf_sha256"] != evidence["pdf_sha256"]
                or file_sha(path) != evidence["pdf_sha256"]):
            raise ValueError(f"Evidence does not match document: {did}")
        with fitz.open(path) as pdf:
            if str(evidence["cover_year"]) not in pdf[0].get_text("text"):
                raise ValueError(f"Cover year not found on page 1: {did}")
    recovered = Counter()
    core_docs = set()
    for split in SPLITS:
        pass_path = output / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl"
        quarantine_path = output / "chunk_streams/quarantine/t192" / f"{split}.jsonl"
        with JsonlWriter(pass_path) as passed, JsonlWriter(quarantine_path) as quarantine:
            for chunk in rows(source / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl"):
                passed.write(chunk)
                core_docs.add(chunk["document_id"])
                recovered[f"pass_{split}"] += 1
            for chunk in rows(source / "chunk_streams/quarantine/t192" / f"{split}.jsonl"):
                if (chunk["document_id"] in approvals
                        and chunk.get("strict_screening_reasons") == ["year_conflict"]):
                    restored = dict(chunk)
                    restored["review_reasons"] = [reason for reason in chunk.get("review_reasons", [])
                                                  if reason != "year_conflict"]
                    restored["strict_screening_reasons"] = []
                    restored["resolved_review_reasons"] = ["year_conflict:pdf_cover_year_verified"]
                    restored["year_evidence"] = {
                        "pdf_sha256": approvals[chunk["document_id"]]["pdf_sha256"],
                        "cover_page_index": 0,
                        "cover_year": approvals[chunk["document_id"]]["cover_year"],
                        "decision_file": decisions_path.name}
                    restored["release_status"] = "pass_candidate"
                    restored["candidate_review_status"] = "screened_candidate_requires_pdf_review"
                    restored["generation_ready"] = False
                    restored["model_input_ready"] = False
                    passed.write(restored)
                    core_docs.add(chunk["document_id"])
                    recovered[f"pass_{split}"] += 1
                    recovered["year_recovered"] += 1
                else:
                    quarantine.write(chunk)
                    recovered["quarantine"] += 1
    shutil.copyfile(source / "documents.jsonl", output / "documents.jsonl")
    for split in SPLITS:
        path = source / "supplementary_non_thesis/pass_candidate/t192" / f"{split}.jsonl"
        destination = output / "supplementary_non_thesis/pass_candidate/t192" / f"{split}.jsonl"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
    previous = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    reason_counts = dict(previous.get("strict_screening_reasons", {}))
    reason_counts["year_conflict"] = max(0, reason_counts.get("year_conflict", 0) - recovered["year_recovered"])
    total = sum(recovered[f"pass_{split}"] for split in SPLITS)
    manifest = {**previous, "source": source.name, "status": "candidate_not_ready",
                "pass_candidate_chunk_count": total, "document_count": len(core_docs),
                "quarantine_chunk_count": recovered["quarantine"],
                "split_counts": {split: recovered[f"pass_{split}"] for split in SPLITS},
                "strict_screening_reasons": reason_counts,
                "strict_screening_newly_quarantined":
                    previous["strict_screening_newly_quarantined"] - recovered["year_recovered"],
                "year_only_chunks_recovered_by_cover_review": recovered["year_recovered"],
                "year_cover_decisions_file": str(decisions_path.relative_to(ROOT)),
                "known_limits": ["Still below the 20000-chunk release minimum.",
                                 "No final 400-card PDF visual sample or independent 40-card crosscheck."]}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")
    print(json.dumps({"candidate": output.name, "pass_chunks": total,
                      "documents": len(core_docs), "recovered_year_only_chunks": recovered["year_recovered"],
                      "quarantine_chunks": recovered["quarantine"],
                      "gap_to_20000": max(0, 20000 - total)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
