#!/usr/bin/env python3
"""Validate schema invariants, offsets, IDs, splits, and passage leakage."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def records(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise AssertionError(f"Invalid JSON: {path}:{line_number}: {exc}") from exc


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    document_id: str | None
    group_id: str | None
    page_index: int | None
    record_id: str | None
    message: str


def validate_release(root: Path, *, pdf_root: Path | None = None,
                     crawler_master: Path | None = None,
                     frozen_split_manifest: Path | None = None,
                     passage_min: int = 128, passage_max: int = 512) -> list[ValidationIssue]:
    """Return deterministic, source-traceable issues without writing to a release."""
    root = Path(root)
    issues: list[ValidationIssue] = []

    def read(relative: str) -> list[dict]:
        path = root / relative
        if not path.is_file():
            return []
        return list(records(path))

    required_paths = (
        "canonical/documents.jsonl", "canonical/pages.jsonl",
        "canonical/paragraphs.jsonl", "canonical/sentences.jsonl",
        "canonical/passages.jsonl", "manifest/inventory.jsonl",
        "manifest/page_inventory.jsonl", "manifest/raw_files.jsonl",
        "manifest/split_manifest.jsonl", "manifest/normalization_manifest.jsonl",
        "reports/summary.json",
    )

    def add(code: str, message: str, row: dict | None = None, *,
            severity: str = "error", record_id: str | None = None,
            page_index: int | None = None) -> None:
        row = row or {}
        group_id = row.get("group_id") or docs_by_id.get(row.get("document_id"), {}).get("group_id")
        issues.append(ValidationIssue(
            severity, code, row.get("document_id"), group_id,
            page_index if page_index is not None else row.get("page_index"),
            record_id or row.get("passage_id") or row.get("sentence_id") or
            row.get("paragraph_id") or
            row.get("crawler_id") or row.get("source_record_id") or
            row.get("document_id"), message))

    documents = read("canonical/documents.jsonl")
    inventory = read("manifest/inventory.jsonl")
    split_rows = read("manifest/split_manifest.jsonl")
    docs_by_id = {row.get("document_id"): row for row in documents}
    page_raw_map: dict[tuple[str | None, int | None], str] = {}
    for relative in required_paths:
        if not (root / relative).is_file():
            add("required_artifact_missing", f"Required release artifact is absent: {relative}.",
                record_id=relative)
    pages_path = root / "canonical/pages.jsonl"
    if pages_path.is_file():
        for page in records(pages_path):
            raw = page.get("raw_text", "")
            page_raw_map[(page.get("document_id"), page.get("page_index"))] = raw
            if not page.get("raw_sha256"):
                add("raw_page_checksum_missing", "Canonical page lacks raw text checksum.", page)
            elif page["raw_sha256"] != hashlib.sha256(raw.encode()).hexdigest():
                add("raw_page_checksum_mismatch", "Raw page checksum differs from serialized text.", page)
            for line_number, line in enumerate(page.get("lines", [])):
                for kind, value, ordinal in [("line", line, line_number)] + [
                        ("span", span, index) for index, span in enumerate(line.get("spans", []))]:
                    start, end = value.get("source_start"), value.get("source_end")
                    if (type(start) is not int or type(end) is not int or
                            not 0 <= start <= end <= len(raw) or raw[start:end] != value.get("text")):
                        add("source_span_invalid", f"Serialized {kind} offsets do not match raw page.",
                            page, record_id=f"page:{page.get('page_index')}:line:{line_number}:{kind}:{ordinal}")
    if len(docs_by_id) != len(documents):
        add("duplicate_document_id", "A document_id appears more than once.",
            next((row for row in documents if row.get("document_id")), {}))

    if crawler_master is not None:
        master = [row.get("id") for row in records(Path(crawler_master))]
        found = [row.get("crawler_id") for row in inventory if row.get("crawler_id") is not None]
        for crawler_id in sorted(set(master) - set(found)):
            add("inventory_incomplete", "Crawler record has no inventory outcome.",
                {"source_record_id": crawler_id}, record_id=str(crawler_id))
        for crawler_id in sorted(set(found) - set(master)):
            add("inventory_unexpected", "Inventory crawler ID is absent from master.",
                {"source_record_id": crawler_id}, record_id=str(crawler_id))
        if len(found) != len(set(found)):
            add("inventory_duplicate", "Crawler ID has multiple inventory outcomes.",
                {"source_record_id": next((value for value in found if found.count(value) > 1), None)})
    for row in inventory:
        if not all(row.get(key) for key in ("document_id", "group_id", "source_file_id", "split")):
            add("inventory_identity_missing", "Inventory outcome lacks a stable identity or split.", row)

    for doc in documents:
        if doc.get("duplicate_of"):
            if doc.get("status") == "active" or doc.get("training_eligible"):
                add("duplicate_not_excluded", "Duplicate document is active.", doc)
            if doc["duplicate_of"] not in docs_by_id:
                add("duplicate_lineage_missing", "Duplicate representative is absent.", doc)
        if not doc.get("source_pdf_sha256"):
            add("source_pdf_checksum_missing", "Canonical document lacks source PDF checksum.", doc)
        elif pdf_root is None:
            add("source_pdf_root_missing", "PDF root is required to verify source checksum.", doc)
        else:
            candidate = (Path(pdf_root) / str(doc.get("source_path") or "")).resolve()
            try:
                candidate.relative_to(Path(pdf_root).resolve())
            except ValueError:
                add("source_pdf_path_unsafe", "Source PDF path escapes the PDF root.", doc)
                continue
            if not candidate.is_file():
                add("source_pdf_missing", "Source PDF is absent.", doc)
            elif hashlib.sha256(candidate.read_bytes()).hexdigest() != doc["source_pdf_sha256"]:
                add("source_pdf_checksum_mismatch", "Source PDF checksum differs.", doc)

    group_splits: defaultdict[str, set[str]] = defaultdict(set)
    for row in documents + inventory + split_rows:
        if row.get("group_id") and row.get("split") in {"train", "dev", "test"}:
            group_splits[row["group_id"]].add(row["split"])
    for group_id, values in sorted(group_splits.items()):
        if len(values) > 1:
            add("group_split_leak", f"Group appears in multiple splits: {sorted(values)}.",
                {"group_id": group_id}, record_id=group_id)
    split_by_group: dict[str, str] = {}
    for row in split_rows:
        group_id = row.get("group_id")
        if group_id and group_id in split_by_group and split_by_group[group_id] != row.get("split"):
            add("group_split_leak", "Split manifest conflicts within a group.", row)
        if group_id:
            split_by_group[group_id] = row.get("split")
    for row in documents + inventory:
        if row.get("group_id") in split_by_group and row.get("split") != split_by_group[row["group_id"]]:
            add("frozen_split_mismatch", "Record split differs from group manifest.", row)
    if frozen_split_manifest is not None:
        frozen_indexes: dict[str, dict[str, str]] = {
            key: {} for key in ("group_id", "document_id", "source_file_id", "source_record_id")}
        for frozen_row in records(Path(frozen_split_manifest)):
            for key, index in frozen_indexes.items():
                if frozen_row.get(key) and frozen_row.get("split"):
                    index[str(frozen_row[key])] = frozen_row["split"]
        for row in documents + inventory + split_rows:
            expected = {index.get(str(row[key])) for key, index in frozen_indexes.items()
                        if row.get(key)} - {None}
            if expected and (len(expected) != 1 or row.get("split") not in expected):
                add("frozen_split_mismatch", "Record changed a frozen source/group split.", row)

    def check_offsets(row: dict, start_key: str, end_key: str) -> None:
        doc = docs_by_id.get(row.get("document_id"))
        if doc is None:
            add("document_reference_missing", "Text record references an absent document.", row)
            return
        start, end = row.get(start_key), row.get(end_key)
        body = doc.get("text", "")
        if (type(start) is not int or type(end) is not int or
                not 0 <= start < end <= len(body) or body[start:end] != row.get("text")):
            add("normalized_offset_mismatch", "Text does not match normalized document offsets.", row)

    def check_spans(row: dict) -> None:
        doc = docs_by_id.get(row.get("document_id"), {})
        active = (row.get("status") != "excluded" and
                  row.get("normalization_status") != "review_required" and
                  not row.get("review_flags") and not row.get("duplicate_of") and
                  doc.get("status") == "active" and not doc.get("duplicate_of"))
        if active and not row.get("source_spans"):
            add("source_lineage_missing", "Active text record lacks source spans.", row)
        for span in row.get("source_spans", []):
            page_index = span.get("page_index")
            doc_id = row.get("document_id")
            key = (doc_id, page_index)
            start, end = span.get("source_start"), span.get("source_end")
            raw = page_raw_map.get(key)
            if (type(page_index) is not int or type(start) is not int or
                    type(end) is not int or key not in page_raw_map or
                    not 0 <= start < end <= len(raw) or
                    "text" not in span or raw[start:end] != span.get("text")):
                add("source_span_invalid", "Source span does not resolve to its raw page slice.",
                    row, page_index=page_index if type(page_index) is int else None)

    paragraphs_path = root / "canonical/paragraphs.jsonl"
    if paragraphs_path.is_file():
        for row in records(paragraphs_path):
            check_offsets(row, "doc_char_start" if "doc_char_start" in row else "char_start",
                          "doc_char_end" if "doc_char_end" in row else "char_end")
            check_spans(row)

    sentence_ids: set[str] = set()
    sentences_path = root / "canonical/sentences.jsonl"
    if sentences_path.is_file():
        for row in records(sentences_path):
            sentence_id = row.get("sentence_id")
            if sentence_id in sentence_ids:
                add("duplicate_sentence_id", "Sentence ID appears more than once.", row)
            sentence_ids.add(sentence_id)
            check_offsets(row, "doc_char_start" if "doc_char_start" in row else "char_start",
                          "doc_char_end" if "doc_char_end" in row else "char_end")
            check_spans(row)

    passages_path = root / "canonical/passages.jsonl"
    if passages_path.is_file():
        for row in records(passages_path):
            check_offsets(row, "start_char", "end_char")
            check_spans(row)
            value = row.get("text", "")
            if not row.get("text_sha256"):
                add("text_checksum_missing", "Passage lacks text checksum.", row)
            elif row["text_sha256"] != hashlib.sha256(value.encode()).hexdigest():
                add("text_checksum_mismatch", "Passage text checksum differs.", row)
            active = (row.get("status") == "active" or
                      (row.get("status") is None and row.get("split") in {"train", "dev", "test"}
                       and not row.get("exclusion_reason")))
            if active and any(char == "\ufffd" or 0xE000 <= ord(char) <= 0xF8FF for char in value):
                add("unresolved_glyph", "Active passage contains unresolved PUA or replacement character.", row)
            tokens = row.get("approx_tokens")
            if type(tokens) is int:
                if tokens > passage_max:
                    add("token_limit_exceeded", "Passage exceeds configured token maximum.", row)
                if active and tokens < passage_min and not row.get("short_tail"):
                    add("short_tail_unflagged", "Short active passage lacks short_tail flag.", row)
            if active and docs_by_id.get(row.get("document_id"), {}).get("duplicate_of"):
                add("duplicate_not_excluded", "Duplicate document has an active passage.", row)
            if active and (row.get("normalization_status") == "review_required" or row.get("review_flags")):
                add("review_unit_active", "Review-required text appears in an active passage.", row)
    return issues


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--pdf-root", type=Path, default=None)
    parser.add_argument("--crawler-master", type=Path, default=None)
    parser.add_argument("--frozen-split-manifest", type=Path, default=None)
    args = parser.parse_args()
    root = args.dataset
    summary_path = root / "reports" / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    if "parameters" not in summary:
        issues = validate_release(
            root, pdf_root=args.pdf_root, crawler_master=args.crawler_master,
            frozen_split_manifest=args.frozen_split_manifest)
        report = {"valid": not any(issue.severity == "error" for issue in issues),
                  "error_count": sum(issue.severity == "error" for issue in issues),
                  "issues": [asdict(issue) for issue in issues]}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report["valid"]:
            raise SystemExit(1)
        return
    parameters = summary["parameters"]
    passage_min = parameters["passage_min"]
    passage_max = parameters["passage_max"]
    errors: list[str] = []
    inventory = list(records(root / "manifest" / "inventory.jsonl"))
    allowed_outcomes = {"accepted", "duplicate", "quarantine", "failed"}
    inventory_paths: set[str] = set()
    for row in inventory:
        source_path = row.get("source_path")
        if source_path in inventory_paths:
            errors.append(f"duplicate inventory source_path: {source_path}")
        inventory_paths.add(source_path)
        if row.get("outcome") not in allowed_outcomes:
            errors.append(f"invalid inventory outcome: {source_path} -> {row.get('outcome')}")
    document_split: dict[str, str] = {}
    group_splits: defaultdict[str, set[str]] = defaultdict(set)
    for doc in records(root / "canonical" / "documents.jsonl"):
        if doc["document_id"] in document_split:
            errors.append(f"duplicate document_id: {doc['document_id']}")
        document_split[doc["document_id"]] = doc["split"]
        if doc["split"] != "excluded":
            group_splits[doc["group_id"]].add(doc["split"])
    for group_id, splits in group_splits.items():
        if len(splits) > 1:
            errors.append(f"group leakage: {group_id} -> {sorted(splits)}")

    seen_passages: dict[str, str] = {}
    passage_counts = Counter()
    previous_ordinal = defaultdict(lambda: -1)
    for passage in records(root / "canonical" / "passages.jsonl"):
        doc_id = passage["document_id"]
        if passage["split"] != "excluded" and document_split.get(doc_id) != passage["split"]:
            errors.append(f"split mismatch: {passage['passage_id']}")
        if passage["ordinal"] != previous_ordinal[doc_id] + 1:
            errors.append(f"non-contiguous passage ordinal: {passage['passage_id']}")
        previous_ordinal[doc_id] = passage["ordinal"]
        fingerprint = hashlib.sha256(" ".join(passage["text"].casefold().split()).encode()).hexdigest()
        if passage["split"] != "excluded":
            valid_length = passage_min <= passage["approx_tokens"] <= passage_max
            valid_short_tail = passage.get("short_tail") and passage["approx_tokens"] < passage_min
            if not (valid_length or valid_short_tail):
                errors.append(
                    f"eligible passage outside token bounds: {passage['passage_id']} "
                    f"({passage['approx_tokens']})"
                )
            if fingerprint in seen_passages:
                errors.append(f"exact passage leakage/duplicate: {passage['passage_id']} and {seen_passages[fingerprint]}")
            else:
                seen_passages[fingerprint] = passage["passage_id"]
        passage_counts[passage["split"]] += 1

    paragraphs = {record["paragraph_id"]: record for record in records(root / "canonical" / "paragraphs.jsonl")}
    sentence_rows = list(records(root / "canonical" / "sentences.jsonl"))
    sentence_ids = {record["sentence_id"] for record in sentence_rows}
    sentences_by_id = {record["sentence_id"]: record for record in sentence_rows}
    for sentence in sentence_rows:
        paragraph = paragraphs.get(sentence["paragraph_id"])
        if not paragraph:
            errors.append(f"missing paragraph for sentence: {sentence['sentence_id']}")
            continue
        reconstructed = paragraph["text"][sentence["paragraph_char_start"]:sentence["paragraph_char_end"]]
        if reconstructed != sentence["text"]:
            errors.append(f"sentence offset mismatch: {sentence['sentence_id']}")
        if sentence.get("is_fragment") and not sentence.get("parent_sentence_id"):
            errors.append(f"fragment without parent: {sentence['sentence_id']}")
    for passage in records(root / "canonical" / "passages.jsonl"):
        missing = set(passage["sentence_ids"]) - sentence_ids
        if missing:
            errors.append(f"missing sentence refs in {passage['passage_id']}: {len(missing)}")
        else:
            reconstructed = " ".join(sentences_by_id[sentence_id]["text"] for sentence_id in passage["sentence_ids"])
            if reconstructed != passage["text"]:
                errors.append(f"passage reconstruction mismatch: {passage['passage_id']}")
        if len(passage.get("section_ids", [])) > 1:
            errors.append(f"passage crosses sections: {passage['passage_id']}")

    report = {"valid": not errors, "errors": errors[:100], "error_count": len(errors), "inventory": len(inventory), "inventory_outcomes": dict(Counter(row.get("outcome") for row in inventory)), "documents": len(document_split), "sentences": len(sentence_ids), "passages": dict(passage_counts)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
