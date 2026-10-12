#!/usr/bin/env python3
"""Adjudicate the 119 text-risk flags without claiming visual PDF review.

Only known clean mathematical symbols and two narrative mentions of numbered
steps are retained. Every other flagged row goes to quarantine. This remains a
candidate view; formal release gates are inherited from v71.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "human_v3_2_screened_candidate_v71_20261006"
OUT = ROOT / "human_v3_2_screened_candidate_v72_20261006"
SPLITS = ("train", "dev", "test")

# Inspected text: multiplication sign, Greek mu, and registered trademark are
# legitimate content here; the original screen treated them as encoding risks.
SYMBOL_FALSE_POSITIVES = {
    "chunk32_47c9a88f3bc99031325d60e9",
    "chunk32_25c2d075e61ae38f66c10ea4",
    "chunk32_6aa1e268646315010c8d45fa",
    "chunk32_73fe8b044bab5e5180b74267",
    "chunk32_a2afd2d8b7ca1dd4bf6b0044",
    "chunk32_b196db5404c2248fbcb2c3b2",
    "chunk32_13b370149d30609993500d39",
    "chunk32_8bca43633389151dac991798",
    "chunk32_7511a916ea7a244e03954aca",
    "chunk32_c510fdfe97be69321c07231c",
    "chunk32_1af8791188eb2f1b43afa413",
    "chunk32_3dccfcc94c8a3ee92b7eeb5d",
}
NARRATIVE_FALSE_POSITIVES = {
    "chunk32_3dd18c701e94fd92a37cef18",
    "chunk32_a4179090a95de3253b8748cf",
}


def read_rows(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def write_row(handle, row: dict) -> None:
    handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    if OUT.exists() and any(OUT.iterdir()):
        raise ValueError(f"Refusing to overwrite: {OUT}")
    flags = {r["chunk_id"]: r for r in read_rows(SOURCE / "remaining_risk_flags.jsonl")}
    keep_flagged = SYMBOL_FALSE_POSITIVES | NARRATIVE_FALSE_POSITIVES
    if len(flags) != 119 or not keep_flagged <= flags.keys():
        raise ValueError("Unexpected v71 risk set")
    quarantine_ids = set(flags) - keep_flagged
    OUT.mkdir(parents=True)
    (OUT / "candidate/t192").mkdir(parents=True)
    (OUT / "quarantine/t192").mkdir(parents=True)
    shutil.copyfile(SOURCE / "documents.jsonl", OUT / "documents.jsonl")
    shutil.copyfile(
        SOURCE / "quarantine/t192/known_review_rejects.jsonl.gz",
        OUT / "quarantine/t192/known_review_rejects.jsonl.gz",
    )

    counts = Counter()
    seen_flags = set()
    retained_document_ids = set()
    document_rights = {r["document_id"]: r.get("rights_status") for r in read_rows(SOURCE / "documents.jsonl")}
    retained_rights_chunks = Counter()
    with (OUT / "risk_adjudication.jsonl").open("w", encoding="utf-8") as ledger, gzip.open(
        OUT / "quarantine/t192/automatic_risk_rejects.jsonl.gz", "wt", encoding="utf-8"
    ) as rejected:
        for split in SPLITS:
            with gzip.open(OUT / f"candidate/t192/{split}.jsonl.gz", "wt", encoding="utf-8") as accepted:
                for row in read_rows(SOURCE / f"candidate/t192/{split}.jsonl.gz"):
                    cid = row["chunk_id"]
                    counts[f"source_{split}"] += 1
                    if cid in flags:
                        seen_flags.add(cid)
                        reason = (
                            "legitimate_symbol_in_text"
                            if cid in SYMBOL_FALSE_POSITIVES
                            else "narrative_reference_to_steps"
                            if cid in NARRATIVE_FALSE_POSITIVES
                            else "visible_text_format_or_encoding_defect"
                        )
                        write_row(
                            ledger,
                            {
                                "chunk_id": cid,
                                "document_id": row["document_id"],
                                "split": split,
                                "flags": flags[cid]["flags"],
                                "disposition": "quarantine" if cid in quarantine_ids else "candidate",
                                "reason": reason,
                                "basis": "text_inspection_only_no_pdf_attestation",
                            },
                        )
                    if cid in quarantine_ids:
                        write_row(rejected, row)
                        counts[f"new_quarantine_{split}"] += 1
                    else:
                        write_row(accepted, row)
                        counts[f"candidate_{split}"] += 1
                        retained_document_ids.add(row["document_id"])
                        retained_rights_chunks[str(document_rights[row["document_id"]])] += 1
    if seen_flags != flags.keys():
        raise ValueError("Some flagged rows were not found in candidate streams")
    if sum(counts[f"new_quarantine_{s}"] for s in SPLITS) != len(quarantine_ids):
        raise ValueError("Quarantine count mismatch")

    prior = json.loads((SOURCE / "release_gate_status.json").read_text(encoding="utf-8"))
    status = {
        **prior,
        "source_candidate_chunks": 22558,
        "known_sample_rejects": 158,
        "additional_text_risk_rejects": len(quarantine_ids),
        "risk_false_positives_retained": len(keep_flagged),
        "remaining_screen_risk_rows": 0,
        "screened_candidate_chunks": sum(counts[f"candidate_{s}"] for s in SPLITS),
        "screened_candidate_documents": len(retained_document_ids),
        "screened_split_chunks": {s: counts[f"candidate_{s}"] for s in SPLITS},
        "screened_rights_chunk_counts": dict(retained_rights_chunks),
        "screened_document_rights_status": dict(
            Counter(str(document_rights[did]) for did in retained_document_ids)
        ),
        "ready_chunks": 0,
        "release_gate": "HOLD",
        "blockers": [
            "formal PDF visual-review attestations remain pending",
            "source extraction and scope rules still require rebuild and stratified resampling after found defects",
            "document rights evidence/status remains incomplete",
            "retained candidates still need complete lineage, content/scope and near-duplicate release audit",
        ],
    }
    (OUT / "release_gate_status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUT / "README.md").write_text(
        "# Human v72 screened candidate (2026-10-06)\n\n"
        "## Result\n\n"
        f"- Original v67: 22,558 candidate chunks from 835 documents.\n"
        f"- Known rejected review cards quarantined: 158.\n"
        f"- Additional text-risk rows quarantined: {len(quarantine_ids)}.\n"
        f"- Retained: {status['screened_candidate_chunks']} candidate chunks from "
        f"{status['screened_candidate_documents']} documents; train "
        f"{counts['candidate_train']}, dev {counts['candidate_dev']}, "
        f"test {counts['candidate_test']}.\n"
        "- Ready: **0**; release gate: **HOLD**.\n\n"
        "The 119 v71 text risk flags have an auditable decision in "
        "`risk_adjudication.jsonl`. Fourteen were retained as text-screen "
        "false positives; they are still candidates, not PDF-verified. "
        "Quarantine files retain all rejected rows. The v71 full-source audit "
        "found zero structural or split errors and verified SHA-256 for all "
        "835 source PDFs represented in v67. Filtering does not introduce "
        "new cross-split rows.\n\n"
        "## Release blockers\n\n"
        "The 579 review cards have no formal PDF visual attestation. "
        "The source extraction rules need to be rebuilt and resampled after "
        "the observed defects. Rights evidence/status remains incomplete "
        "for 322 retained documents, covering 11,038 candidate chunks. "
        "Complete lineage, scope, and near-duplicate release checks remain "
        "open. `release_gate_status.json` records the machine-readable status.\n",
        encoding="utf-8",
    )
    with (OUT / "checksums.sha256").open("w", encoding="utf-8") as handle:
        for path in sorted(OUT.rglob("*")):
            if path.is_file() and path.name != "checksums.sha256":
                handle.write(f"{file_sha256(path)}  {path.relative_to(OUT).as_posix()}\n")
    print(json.dumps(status, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
