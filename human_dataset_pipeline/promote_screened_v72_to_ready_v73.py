#!/usr/bin/env python3
"""Publish the owner-approved v72 screened Human corpus as a ready view.

This records an explicit owner release decision; it does not fabricate PDF
visual attestations or change source text. Known rejects stay in quarantine.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "human_v3_2_screened_candidate_v72_20261006"
OUT = ROOT / "human_v3_2_ready_v73_20261007"
REVIEW = ROOT / "human_v3_2_review_579_v69g_20261006"
SPLITS = ("train", "dev", "test")


def read_jsonl(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    if OUT.exists() and any(OUT.iterdir()):
        raise ValueError(f"Refusing to overwrite release: {OUT}")
    source_status = json.loads((SOURCE / "release_gate_status.json").read_text(encoding="utf-8"))
    if (
        source_status["screened_candidate_chunks"] != 22295
        or source_status["known_sample_rejects"] != 158
        or source_status["additional_text_risk_rejects"] != 105
    ):
        raise ValueError("Unexpected input version or review totals")
    documents = {r["document_id"]: r for r in read_jsonl(SOURCE / "documents.jsonl")}
    OUT.mkdir(parents=True)
    (OUT / "chunk_streams/ready/t192").mkdir(parents=True)
    (OUT / "quarantine/t192").mkdir(parents=True)
    (OUT / "audit").mkdir(parents=True)

    counts = Counter()
    rights_counts = Counter()
    used_docs = set()
    seen_chunks = set()
    doc_splits = defaultdict(set)
    group_splits = defaultdict(set)
    fingerprint_splits = defaultdict(set)
    sentence_splits = defaultdict(set)

    for split in SPLITS:
        target = OUT / f"chunk_streams/ready/t192/{split}.jsonl"
        with target.open("w", encoding="utf-8") as writer:
            for row in read_jsonl(SOURCE / f"candidate/t192/{split}.jsonl.gz"):
                cid, did = row["chunk_id"], row["document_id"]
                if cid in seen_chunks or did not in documents or row["split"] != split:
                    raise ValueError(f"ID, document, or split failure: {cid}")
                if hashlib.sha256(row["text"].encode("utf-8")).hexdigest() != row["text_sha256"]:
                    raise ValueError(f"Text SHA failure: {cid}")
                if row["source_pdf_sha256"] != documents[did]["source_pdf_sha256"]:
                    raise ValueError(f"PDF SHA metadata failure: {cid}")
                seen_chunks.add(cid)
                used_docs.add(did)
                doc_splits[did].add(split)
                group_splits[row["group_id"]].add(split)
                fingerprint_splits[row["fingerprint_sha256"]].add(split)
                for sid in row["sentence_ids"]:
                    sentence_splits[(did, sid)].add(split)
                rights_counts[str(documents[did].get("rights_status"))] += 1
                counts[split] += 1
                row["release_status"] = "ready"
                row["release_basis"] = "dataset_owner_approval_2026-10-07_after_v72_screening"
                row["candidate_review_status"] = "owner_approved_ready"
                row["human_core_ready"] = True
                row["generation_ready"] = True
                row["formal_pdf_visual_attestation"] = False
                writer.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    if sum(counts.values()) != 22295 or len(used_docs) != 830:
        raise ValueError("Release count differs from screened candidate")
    for name, lookup in (
        ("document", doc_splits),
        ("group", group_splits),
        ("fingerprint", fingerprint_splits),
        ("sentence", sentence_splits),
    ):
        if any(len(splits) > 1 for splits in lookup.values()):
            raise ValueError(f"Cross-split {name} leakage")

    with (OUT / "documents.jsonl").open("w", encoding="utf-8") as writer:
        for did in sorted(used_docs):
            writer.write(json.dumps(documents[did], ensure_ascii=False, separators=(",", ":")) + "\n")
    shutil.copyfile(
        SOURCE / "quarantine/t192/known_review_rejects.jsonl.gz",
        OUT / "quarantine/t192/known_review_rejects.jsonl.gz",
    )
    shutil.copyfile(
        SOURCE / "quarantine/t192/automatic_risk_rejects.jsonl.gz",
        OUT / "quarantine/t192/automatic_risk_rejects.jsonl.gz",
    )
    shutil.copyfile(SOURCE / "risk_adjudication.jsonl", OUT / "audit/risk_adjudication.jsonl")
    shutil.copyfile(SOURCE / "release_gate_status.json", OUT / "audit/v72_screening_status.json")
    shutil.copyfile(REVIEW / "decisions.jsonl", OUT / "audit/review_579_decisions.jsonl")

    manifest = {
        "release_id": "human_v3_2_ready_v73_20261007",
        "release_date": "2026-10-07",
        "status": "ready",
        "readiness_basis": "explicit_dataset_owner_decision_after_v72_screening",
        "claim_scope": "owner_approved_training_testing_dataset_not_formally_visual_attested",
        "label": "H",
        "chunk_count": sum(counts.values()),
        "document_count": len(used_docs),
        "split_chunk_counts": dict(counts),
        "rights_status_chunk_counts": dict(rights_counts),
        "quarantine_chunk_count": 263,
        "quarantine_reasons": {"review_rejects": 158, "additional_text_risk_rejects": 105},
        "formal_pdf_visual_attestations": 0,
        "source_pdf_hashes_previously_checked": 835,
        "cross_split_document_group_fingerprint_sentence_leakage": 0,
        "known_limitations": [
            "579 review cards were text-screened, with no formal PDF visual attestation",
            "rights status/evidence remains missing or pending for 322 included documents and 11038 chunks",
            "the extraction rules were not rebuilt after detected sample defects",
            "complete lineage, scope, and near-duplicate release review remains open",
        ],
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUT / "DATASET_CARD.md").write_text(
        "# Human v3.2 ready v73\n\n"
        "**Status:** ready by explicit dataset-owner decision on 2026-10-07. "
        "This is an owner-approved release for the intended training/testing workflow; "
        "it is not a claim that every PDF page was visually verified.\n\n"
        "## Content and format\n\n"
        "22,295 Vietnamese computing-academic prose chunks from 830 source documents. "
        "Each UTF-8 JSONL row in `chunk_streams/ready/t192/` has `label=H`, "
        "document and section lineage, source PDF SHA-256, split, text, and release fields. "
        "Splits: train 15,601; dev 3,292; test 3,402. "
        "The chunk text and IDs are unchanged from screened candidate v72. "
        "The release sets `human_core_ready` and `generation_ready` by owner decision; "
        "`model_input_ready` remains a separate tokenizer/window property.\n\n"
        "## Screening and limits\n\n"
        "The v67 input had 22,558 chunks. Text/sample review quarantined 158 known "
        "rejects and 105 additional format/encoding risks. Fourteen false-positive "
        "symbol/step flags were retained. The 579 review cards had 158 rejects and "
        "421 provisional passes; no formal PDF visual attestation is recorded. "
        "The source-PDF hash audit matched 835 PDFs, and this release has zero "
        "cross-split document, group, fingerprint, or sentence leakage. "
        "Rights status/evidence is missing or pending for 322 included documents "
        "(11,038 chunks), despite the owner's earlier rights assertion. Extraction "
        "rules were not rebuilt after sample defects; full lineage, scope and "
        "near-duplicate release review remain open. These limits should accompany "
        "any reported training or evaluation result.\n\n"
        "Quarantined rows and the review decisions are retained in `quarantine/` "
        "and `audit/`. The manifest gives machine-readable counts and limits.\n",
        encoding="utf-8",
    )
    (OUT / "README.md").write_text(
        "# Human ready v73 (2026-10-07)\n\n"
        "Owner-approved `ready` dataset: **22,295 chunks, 830 documents**. "
        "Use `chunk_streams/ready/t192/{train,dev,test}.jsonl` (UTF-8 JSONL). "
        "Read `DATASET_CARD.md` and `manifest.json` for provenance and limits. "
        "The 263 known rejected rows remain in `quarantine/`.\n",
        encoding="utf-8",
    )
    with (OUT / "checksums.sha256").open("w", encoding="utf-8") as writer:
        for path in sorted(OUT.rglob("*")):
            if path.is_file() and path.name != "checksums.sha256":
                writer.write(f"{sha_file(path)}  {path.relative_to(OUT).as_posix()}\n")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
