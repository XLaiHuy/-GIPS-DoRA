#!/usr/bin/env python3
"""Materialize OCR/quarantine review queues and a compact dataset card."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path


def load_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            yield json.loads(line)


def build_quality_report(root: Path) -> dict:
    """Summarize structural and release-state metrics without mutating the corpus."""
    root = Path(root)

    def read(relative: str) -> list[dict]:
        path = root / relative
        return list(load_jsonl(path)) if path.is_file() else []

    documents = read("canonical/documents.jsonl")
    sentences = read("canonical/sentences.jsonl")
    passages = read("canonical/passages.jsonl")
    paragraphs = read("canonical/paragraphs.jsonl")
    inventory = read("manifest/inventory.jsonl")
    page_inventory = read("manifest/page_inventory.jsonl")
    normalization = read("manifest/normalization_manifest.jsonl")
    document_ids = {row.get("document_id") for row in documents}
    document_ids.update(row.get("document_id") for row in inventory if row.get("document_id"))
    document_ids.discard(None)
    docs_by_id = {row.get("document_id"): row for row in documents}

    def state(row: dict) -> str:
        if row.get("duplicate_of"):
            return "duplicate"
        if row.get("status") == "excluded" or row.get("split") == "excluded":
            return "excluded"
        if (row.get("status") in {"review_required", "reviewed"} or
                row.get("normalization_status") in {"review_required", "reviewed"} or
                row.get("review_flags")):
            return "reviewed"
        return "active"

    by_document: dict[str, dict] = {}
    for doc_id in sorted(document_ids):
        doc = docs_by_id.get(doc_id, {})
        owned = lambda rows: [row for row in rows if row.get("document_id") == doc_id]
        doc_sentences = owned(sentences)
        doc_passages = owned(passages)
        doc_paragraphs = owned(paragraphs)
        doc_inventory = owned(inventory) + owned(page_inventory)
        doc_normalization = owned(normalization)
        token_values = [row["approx_tokens"] for row in doc_passages
                        if type(row.get("approx_tokens")) is int]
        nonterminal = sum(bool(row.get("text", "").strip()) and
                          row["text"].rstrip()[-1] not in ".!?…" for row in doc_sentences)
        rows = [doc] if doc else []
        rows += doc_paragraphs + doc_sentences + doc_passages + doc_inventory
        statuses = Counter(state(row) for row in rows)
        by_document[doc_id] = {
            "split": doc.get("split") or next((row.get("split") for row in doc_inventory
                                                 if row.get("split")), "unknown"),
            "status": state(doc) if doc else "excluded",
            "structural_nonterminal_sentence_like_units": nonterminal,
            "short_tails": sum(bool(row.get("short_tail")) for row in doc_passages),
            "active_records": statuses["active"],
            "reviewed_records": statuses["reviewed"],
            "excluded_records": statuses["excluded"],
            "duplicate_records": statuses["duplicate"],
            "glyph_rule_count": sum(len(row.get("rules_applied", [])) for row in doc_normalization),
            "extraction_failures": sum(row.get("extraction_status") in
                                       {"extraction_error", "build_error"}
                                       for row in (owned(page_inventory) or owned(inventory))),
            "passage_tokens": {"min": min(token_values) if token_values else None,
                               "max": max(token_values) if token_values else None,
                               "count": len(token_values)},
        }
    by_split: dict[str, dict] = {}
    for split in sorted({row["split"] for row in by_document.values()}):
        members = [row for row in by_document.values() if row["split"] == split]
        tokens = [passage["approx_tokens"] for passage in passages
                  if passage.get("split") == split and type(passage.get("approx_tokens")) is int]
        by_split[split] = {
            "documents": len(members),
            **{key: sum(row[key] for row in members) for key in (
                "structural_nonterminal_sentence_like_units", "short_tails",
                "active_records", "reviewed_records", "excluded_records",
                "duplicate_records", "glyph_rule_count", "extraction_failures")},
            "passage_tokens": {"min": min(tokens) if tokens else None,
                               "max": max(tokens) if tokens else None,
                               "count": len(tokens)},
        }
    return {"heuristic_label": "structural_nonterminal_sentence_like_units",
            "heuristic_note": "Missing terminal punctuation is a structural cue, not a validation error.",
            "by_document": by_document, "by_split": by_split}


def write_jsonl(path: Path, rows) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()
    root = args.dataset.resolve()
    reports = root / "reports"
    reports.mkdir(exist_ok=True)
    quality_report = build_quality_report(root)
    (reports / "quality_report.json").write_text(
        json.dumps(quality_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary_path = reports / "summary.json"
    if not summary_path.is_file() or "parameters" not in json.loads(
            summary_path.read_text(encoding="utf-8")):
        print(json.dumps({"quality_report": str(reports / "quality_report.json"),
                          "documents": len(quality_report["by_document"])},
                         ensure_ascii=False, indent=2))
        return

    docs = {row["document_id"]: row for row in load_jsonl(root / "canonical" / "documents.jsonl")}
    qualities = {row["document_id"]: row for row in load_jsonl(root / "manifest" / "quality.jsonl")}
    quarantine = []
    for doc_id, quality in qualities.items():
        if quality["tier"] == "quarantine":
            quarantine.append({**docs[doc_id], "quality": quality})
    quarantine.sort(key=lambda row: (row["quality"]["page_text_coverage"], row["source_path"]))
    quarantine_count = write_jsonl(reports / "quarantine_documents.jsonl", quarantine)

    ocr_rows = []
    for page in load_jsonl(root / "canonical" / "pages.jsonl"):
        if page["needs_ocr"]:
            doc = docs[page["document_id"]]
            ocr_rows.append(
                {
                    "document_id": page["document_id"],
                    "source_path": doc["source_path"],
                    "quality_tier": doc["quality_tier"],
                    "page_index": page["page_index"],
                    "current_alpha_chars": sum(ch.isalpha() for ch in page["raw_text"]),
                    "reason": "insufficient_text_layer; visually verify blank/figure page before OCR",
                }
            )
    ocr_count = write_jsonl(reports / "ocr_page_review_queue.jsonl", ocr_rows)

    passages = list(load_jsonl(root / "canonical" / "passages.jsonl"))
    eligible = [row for row in passages if row["split"] != "excluded"]
    split_counts = Counter(row["split"] for row in eligible)
    exclusion_counts = Counter(
        row.get("exclusion_reason") or "document_excluded"
        for row in passages
        if row["split"] == "excluded"
    )
    doc_tiers = Counter(doc["quality_tier"] for doc in docs.values())
    doc_splits = Counter(doc["split"] for doc in docs.values())
    with (root / "reports" / "summary.json").open("r", encoding="utf-8") as handle:
        summary = json.load(handle)
    size_bytes = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
    token_values = [row["approx_tokens"] for row in eligible]
    card = f"""# Human-written Dataset v1.1 — Data Card

## Scope

- Source: {summary['pdfs_discovered']} Vietnamese thesis/research PDF files.
- Unique documents processed: {len(docs)}; exact duplicate files: {summary['counts'].get('duplicate_files', 0)}.
- Label/provenance: `H` / `human_written_thesis_pdf`.
- Canonical format: UTF-8 JSONL; model views are derived with the backbone tokenizer.
- Build is immutable and reproducible with seed `{summary['parameters']['seed']}`.

## Quality and review

- Gold: {doc_tiers['gold']} documents.
- Silver: {doc_tiers['silver']} documents.
- Quarantine: {doc_tiers['quarantine']} documents; excluded from model splits.
- Pages requiring visual OCR review: {ocr_count}.
- Pipeline errors: {summary['counts'].get('errors', 0)}.

The OCR queue is conservative: pages with little text may be blank, covers,
figures, or genuine scans. They must be visually classified before OCR. OCR text
must be stored as a separate extraction method and must never overwrite raw text.

## Leakage-safe splits

- Documents: train={doc_splits['train']}, dev={doc_splits['dev']}, test={doc_splits['test']}, excluded={doc_splits['excluded']}.
- Eligible passages: train={split_counts['train']}, dev={split_counts['dev']}, test={split_counts['test']}.
- Split unit: document/duplicate group, fixed before passage creation.
- Exact duplicate passages are excluded across all splits.

## Passage profile

- Eligible passages: {len(eligible)}.
- Approximate token range: {min(token_values)}–{max(token_values)}.
- Median/mean: {statistics.median(token_values):.0f}/{statistics.mean(token_values):.1f} approximate tokens.
- Passage constraints: min={summary['parameters']['passage_min']}, target={summary['parameters']['passage_target']}, max={summary['parameters']['passage_max']}.
- Canonical passages do not overlap; tokenizer-specific windows may overlap.
- Exclusions: {dict(exclusion_counts)}.

Approximate tokens are tokenizer-neutral and used only for canonical chunking.
Before training, run `make_model_windows.py` with the exact local tokenizer and
use its exact token counts.

## Storage

- Total current size: {size_bytes / (1024**2):.1f} MiB.
- `manifest/`: checksums, extraction status, quality, duplicate and split locks.
- `canonical/`: page, paragraph, sentence, and passage records with lineage.
- `splits/`: directly consumable eligible passage records.
- `reports/ocr_page_review_queue.jsonl`: page-level visual/OCR work queue.
- `reports/quarantine_documents.jsonl`: document-level remediation queue.

## Intended use and limitations

The dataset is suitable as the Human branch for H/P/G counterfactual generation,
detector training, validation, and held-out testing. It is not evidence that every
source was written without any AI assistance; provenance is inherited from the
thesis collection. Rights, consent, personally identifiable information, and
institutional data policy must be reviewed before redistribution or publication.
"""
    (reports / "DATA_CARD.md").write_text(card, encoding="utf-8")
    print(json.dumps({"quarantine_documents": quarantine_count, "ocr_page_queue": ocr_count, "eligible_passages": len(eligible)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
