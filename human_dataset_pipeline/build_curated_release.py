#!/usr/bin/env python3
"""Materialize an active-only Human release from an audited training view.

The source release remains immutable. The curated release contains only records
that are safe to feed to training/evaluation; rejected documents are represented
by a text-free audit manifest so exclusions remain explainable.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def rows(path: Path):
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def write_line(handle, row: dict) -> None:
    handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def exclusion_reason(doc: dict, excluded_institutions: set[str], max_year: int) -> str | None:
    if doc.get("institution_id") in excluded_institutions:
        return "partial_preview_source"
    year = doc.get("year")
    if year is None:
        return "unknown_year"
    if year > max_year:
        return "post_cutoff_year"
    if not doc.get("training_eligible"):
        missing = doc.get("metadata_missing_required") or []
        if missing:
            return "missing_required_metadata:" + ",".join(sorted(missing))
        if doc.get("quality_tier") not in {"gold", "silver"}:
            return "quality_gate"
        if doc.get("duplicate_of"):
            return "duplicate_document"
        return "not_training_eligible"
    if not doc.get("core_human_eligible"):
        return "not_core_human"
    return None


def stripped_audit_record(doc: dict, reason: str) -> dict:
    keep = {
        "schema_version", "document_id", "group_id", "source_path", "source_sha256",
        "title", "authors", "year", "repository_year", "cover_year", "year_conflict",
        "institution_id", "institution_name", "document_type_id", "domain_id",
        "language_detected", "quality_tier", "metadata_status", "metadata_missing_required",
        "metadata_missing_optional", "provenance_status", "split", "duplicate_of",
        "clean_character_count", "clean_sentence_count", "clean_paragraph_count",
    }
    record = {key: doc.get(key) for key in keep if key in doc}
    record["release_exclusion_reason"] = reason
    return record


def build(source: Path, output: Path, excluded_institutions: set[str], max_year: int) -> dict:
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"Output must be new or empty: {output}")
    for directory in ("chunks", "passages", "review", "reports", "manifest"):
        (output / directory).mkdir(parents=True, exist_ok=True)

    documents = list(rows(source / "documents.jsonl"))
    active_ids: set[str] = set()
    excluded: list[dict] = []
    active_docs: list[dict] = []
    exclusion_counts: Counter[str] = Counter()
    for doc in documents:
        reason = exclusion_reason(doc, excluded_institutions, max_year)
        if reason is None:
            active_ids.add(doc["document_id"])
            active_docs.append(doc)
        else:
            exclusion_counts[reason] += 1
            excluded.append(stripped_audit_record(doc, reason))

    with (output / "documents.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for doc in active_docs:
            write_line(handle, doc)
    with (output / "manifest" / "excluded_records.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for doc in excluded:
            write_line(handle, doc)

    sentence_count = 0
    with (output / "sentences.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for sentence in rows(source / "sentences.jsonl"):
            if sentence["document_id"] in active_ids:
                write_line(handle, sentence)
                sentence_count += 1

    passage_handles = {}
    chunk_handles = {}
    for name in ("all", "train", "dev", "test", "recent_or_uncertain", "excluded"):
        passage_handles[name] = (output / "passages" / f"{name}.jsonl").open("w", encoding="utf-8", newline="\n")
        chunk_handles[name] = (output / "chunks" / f"{name}.jsonl").open("w", encoding="utf-8", newline="\n")
    passage_counts: Counter[str] = Counter()
    try:
        for passage in rows(source / "passages" / "all.jsonl"):
            if passage["document_id"] not in active_ids or passage.get("subset") != "core_human":
                continue
            split = passage["split"]
            if split not in {"train", "dev", "test"}:
                raise ValueError(f"Unexpected active split: {split}")
            write_line(passage_handles["all"], passage)
            write_line(passage_handles[split], passage)
            write_line(chunk_handles["all"], passage)
            write_line(chunk_handles[split], passage)
            passage_counts[split] += 1
    finally:
        for handle in list(passage_handles.values()) + list(chunk_handles.values()):
            handle.close()

    with (output / "split_manifest.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows(source / "split_manifest.jsonl"):
            if row["document_id"] in active_ids:
                write_line(handle, row)

    review_source = source / "review" / "metadata_review_queue.jsonl"
    with (output / "review" / "metadata_review_queue.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows(review_source):
            if row.get("document_id") in active_ids:
                write_line(handle, row)

    dimensions = {
        "institutions": Counter(doc.get("institution_id") for doc in active_docs),
        "year_buckets": Counter(doc.get("year_bucket") for doc in active_docs),
        "domains": Counter(doc.get("domain_id") for doc in active_docs),
        "document_types": Counter(doc.get("document_type_id") for doc in active_docs),
        "languages": Counter(doc.get("language_detected") for doc in active_docs),
        "quality_tiers": Counter(doc.get("quality_tier") for doc in active_docs),
        "splits": Counter(doc.get("split") for doc in active_docs),
    }
    diversity = {name: dict(values) for name, values in dimensions.items()}
    diversity["passages_by_split"] = dict(passage_counts)
    diversity["exclusion_reasons"] = dict(exclusion_counts)
    (output / "reports" / "diversity.json").write_text(
        json.dumps(diversity, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    info = {
        "dataset_release": "human_written_dataset_v2_8",
        "record_schema_version": "human-training-v2.7",
        "source_release": str(source.resolve()),
        "policy": {
            "active_only": True,
            "max_human_year": max_year,
            "excluded_institutions": sorted(excluded_institutions),
            "excluded_source_reason": "HCMUTE PDFs are 20-page partial previews, not verified full text",
        },
        "counts": {
            "source_documents": len(documents),
            "active_documents": len(active_docs),
            "excluded_documents": len(excluded),
            "active_sentences": sentence_count,
            "active_passages": sum(passage_counts.values()),
            **{f"passages_{key}": passage_counts[key] for key in ("train", "dev", "test")},
        },
        "exclusion_reasons": dict(exclusion_counts),
    }
    (output / "dataset_info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return info


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Build an active-only Human release from an audited view.")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-year", type=int, default=2022)
    parser.add_argument("--exclude-institution", action="append", default=["hcmute"])
    args = parser.parse_args()
    info = build(args.source.resolve(), args.output.resolve(), set(args.exclude_institution), args.max_year)
    print(json.dumps(info, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
