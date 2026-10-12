#!/usr/bin/env python3
"""Final fail-closed v67 audit and screened candidate export.

Known rejected sample rows are removed from a new candidate view. No row is
renamed `ready` unless the full review/rights/visual gates are actually closed.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "human_v3_2_merged_candidate_v67_20261006"
REVIEW = ROOT / "human_v3_2_review_579_v69g_20261006"
OUT = ROOT / "human_v3_2_screened_candidate_v71_20261006"
SPLITS = ("train", "dev", "test")


def rows(path: Path):
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


def pdf_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def main() -> None:
    if OUT.exists() and any(OUT.iterdir()):
        raise ValueError(f"Refusing to overwrite existing output: {OUT}")
    documents = {r["document_id"]: r for r in rows(SOURCE / "documents.jsonl")}
    decisions = {r["chunk_id"]: r for r in rows(REVIEW / "decisions.jsonl")}
    active = {r["chunk_id"] for r in rows(SOURCE / "active_review_cards.jsonl")}
    if len(decisions) != 579 or len(active) != 579 or set(decisions) != active:
        raise ValueError("The 579-card decision ledger does not match v67")
    rejected = {cid: r for cid, r in decisions.items() if r["decision"] == "reject"}
    if len(rejected) != 158 or sum(r["decision"] == "provisional_pass" for r in decisions.values()) != 421:
        raise ValueError("Unexpected sample decision totals")

    OUT.mkdir(parents=True)
    (OUT / "candidate/t192").mkdir(parents=True)
    (OUT / "quarantine/t192").mkdir(parents=True)
    stats = Counter()
    after = Counter()
    rights_chunks = Counter()
    source_document_ids = set()
    kept_document_ids = set()
    source_by_split = defaultdict(set)
    group_splits = defaultdict(set)
    fp_split = {}
    sentence_split = {}
    seen_ids = set()
    seen_rejected = set()
    risks = {r["chunk_id"]: r for r in rows(REVIEW / "corpus_risk_screen.jsonl")}
    risks_after = set()
    errors = []
    rejected_rows = []

    for split in SPLITS:
        target = OUT / f"candidate/t192/{split}.jsonl.gz"
        with gzip.open(target, "wt", encoding="utf-8", compresslevel=6) as writer:
            for record in rows(SOURCE / f"candidate/t192/{split}.jsonl.gz"):
                cid, did = record["chunk_id"], record["document_id"]
                stats[split] += 1
                source_document_ids.add(did)
                source_by_split[did].add(split)
                group_splits[record["group_id"]].add(split)
                if cid in seen_ids:
                    errors.append(["duplicate_chunk_id", cid])
                seen_ids.add(cid)
                doc = documents.get(did)
                if not doc or record["source_pdf_sha256"] != doc["source_pdf_sha256"]:
                    errors.append(["document_or_pdf_sha_mismatch", cid])
                if record["split"] != split or (doc and doc["split"] != split):
                    errors.append(["document_split_mismatch", cid])
                if record["text_sha256"] != hashlib.sha256(record["text"].encode("utf-8")).hexdigest():
                    errors.append(["text_sha_mismatch", cid])
                if not 64 <= record["approx_tokens"] <= 256 or record["sentence_count"] < 2:
                    errors.append(["chunk_budget_or_sentences", cid])
                if record.get("overlap_sentence_ids"):
                    errors.append(["overlap_sentence_ids", cid])
                if not record.get("source_spans") or not record.get("section_id"):
                    errors.append(["missing_lineage_or_section", cid])
                fingerprint = record["fingerprint_sha256"]
                previous = fp_split.setdefault(fingerprint, split)
                if previous != split:
                    errors.append(["exact_fingerprint_across_splits", cid])
                for sid in record["sentence_ids"]:
                    previous = sentence_split.setdefault(sid, split)
                    if previous != split:
                        errors.append(["sentence_id_across_splits", cid])
                rights_chunks[str(doc.get("rights_status") if doc else None)] += 1
                if cid in rejected:
                    seen_rejected.add(cid)
                    rejected_rows.append({"chunk_id": cid, "document_id": did, "split": split,
                                          "reason_codes": rejected[cid]["reason_codes"],
                                          "source_pdf_sha256": record["source_pdf_sha256"],
                                          "text": record["text"]})
                    continue
                writer.write(json.dumps(record, ensure_ascii=False) + "\n")
                after[split] += 1
                kept_document_ids.add(did)
                if cid in risks:
                    risks_after.add(cid)

    if seen_rejected != set(rejected):
        errors.append(["rejected_sample_missing_from_v67", sorted(set(rejected) - seen_rejected)[:10]])
    if any(len(x) > 1 for x in source_by_split.values()):
        errors.append(["document_split_leakage", sum(len(x) > 1 for x in source_by_split.values())])
    if any(len(x) > 1 for x in group_splits.values()):
        errors.append(["group_split_leakage", sum(len(x) > 1 for x in group_splits.values())])
    with gzip.open(OUT / "quarantine/t192/known_review_rejects.jsonl.gz", "wt", encoding="utf-8") as writer:
        for row in rejected_rows:
            writer.write(json.dumps(row, ensure_ascii=False) + "\n")
    with (OUT / "remaining_risk_flags.jsonl").open("w", encoding="utf-8") as writer:
        for cid in sorted(risks_after):
            writer.write(json.dumps(risks[cid], ensure_ascii=False) + "\n")
    with (OUT / "documents.jsonl").open("w", encoding="utf-8") as writer:
        for did in sorted(kept_document_ids):
            writer.write(json.dumps(documents[did], ensure_ascii=False) + "\n")

    pdf_errors = []
    for index, did in enumerate(sorted(source_document_ids), 1):
        doc = documents[did]
        path = pdf_path(doc["source_path"])
        if not path.is_file():
            pdf_errors.append([did, "pdf_missing", str(path)])
        elif sha_file(path) != doc["source_pdf_sha256"]:
            pdf_errors.append([did, "pdf_sha_mismatch", str(path)])
        if index % 100 == 0 or index == len(source_document_ids):
            print(f"PDF SHA-256 {index}/{len(source_document_ids)}", flush=True)

    rights_kept = Counter(str(documents[did].get("rights_status")) for did in kept_document_ids)
    summary = {
        "source_candidate_chunks": sum(stats.values()),
        "source_candidate_documents": len(source_document_ids),
        "source_split_chunks": dict(stats),
        "sample_rejected_rows_quarantined": len(rejected_rows),
        "screened_candidate_chunks": sum(after.values()),
        "screened_candidate_documents": len(kept_document_ids),
        "screened_split_chunks": dict(after),
        "remaining_screen_risk_rows": len(risks_after),
        "source_rights_chunk_counts": dict(rights_chunks),
        "screened_document_rights_status": dict(rights_kept),
        "source_pdf_hashes_checked": len(source_document_ids),
        "source_pdf_errors": pdf_errors,
        "structural_errors": errors[:100],
        "structural_error_count": len(errors),
        "formal_human_sample_attestations": 0,
        "ready_chunks": 0,
        "release_gate": "HOLD",
        "blockers": [
            "579 formal visual-review cards remain pending; assistant OCR/triage is not human attestation",
            "sample found 158 content errors; the extraction/scope rules have not yet been rebuilt and resampled",
            "unreviewed candidate rows remain, including automatic risk flags",
            "rights evidence/status is not recorded for every source document in the artifact",
        ],
    }
    if pdf_errors or errors:
        summary["blockers"].append("source PDF integrity or structural validation failed")
    (OUT / "release_gate_status.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "README.md").write_text(
        "# Human v71 screened candidate\n\n"
        f"Source v67: {sum(stats.values()):,} candidate chunks. "
        f"Quarantined {len(rejected_rows):,} confirmed sample rejects. "
        f"This view retains **{sum(after.values()):,} candidate chunks** "
        f"across {len(kept_document_ids)} documents. `ready=0`.\n\n"
        "The sample and full-corpus gate results are in `release_gate_status.json`. "
        "`quarantine/t192/known_review_rejects.jsonl.gz` records excluded IDs and reasons. "
        "`remaining_risk_flags.jsonl` lists rows needing targeted PDF review. "
        "This export is for internal review and is not Human ground truth.\n",
        encoding="utf-8",
    )
    with (OUT / "checksums.sha256").open("w", encoding="utf-8") as writer:
        for path in sorted(p for p in OUT.rglob("*") if p.is_file() and p.name != "checksums.sha256"):
            writer.write(f"{sha_file(path)}  {path.relative_to(OUT).as_posix()}\n")
    print(json.dumps({k: summary[k] for k in ("source_candidate_chunks", "sample_rejected_rows_quarantined",
          "screened_candidate_chunks", "remaining_screen_risk_rows", "source_pdf_errors", "structural_error_count",
          "ready_chunks", "release_gate")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
