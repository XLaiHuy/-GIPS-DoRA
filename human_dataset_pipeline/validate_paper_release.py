#!/usr/bin/env python3
"""Validate a paper Human release, including source and record traceability."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from validate_dataset import ValidationIssue, validate_release as validate_base_release
from dataclasses import asdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SPLITS = ("train", "dev", "test")


def rows(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise AssertionError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_release(root: Path, *, pdf_root: Path,
                     crawler_master: Path | None = None,
                     frozen_split_manifest: Path | None = None,
                     passage_min: int = 128, passage_max: int = 512) -> list[ValidationIssue]:
    """Read-only structured validation of the paper release layout."""
    root, pdf_root = Path(root), Path(pdf_root)
    if (root / "canonical" / "documents.jsonl").is_file():
        return validate_base_release(root, pdf_root=pdf_root,
                                     crawler_master=crawler_master,
                                     frozen_split_manifest=frozen_split_manifest,
                                     passage_min=passage_min, passage_max=passage_max)
    issues: list[ValidationIssue] = []

    def read(relative: str) -> list[dict]:
        path = root / relative
        return list(rows(path)) if path.is_file() else []

    def add(code: str, message: str, row: dict | None = None,
            record_id: str | None = None, page_index: int | None = None) -> None:
        row = row or {}
        group_id = row.get("group_id") or docs_by_id.get(row.get("document_id"), {}).get("group_id")
        issues.append(ValidationIssue("error", code, row.get("document_id"),
                                      group_id, page_index,
                                      record_id or row.get("passage_id") or row.get("sentence_id") or
                                      row.get("paragraph_id") or
                                      row.get("metadata_record_id") or row.get("source_file_id") or
                                      row.get("document_id"), message))

    documents = read("documents.jsonl")
    paragraphs = read("paragraphs.jsonl")
    sentences = read("sentences.jsonl")
    passages = read("passages/all.jsonl")
    source_files = read("manifest/source_files.jsonl")
    crawler_records = read("manifest/crawler_records.jsonl")
    split_manifest = read("manifest/split_manifest.jsonl")
    lineage_path = root / "lineage" / "source_pages.jsonl"
    info_path = root / "dataset_info.json"
    release_name = (json.loads(info_path.read_text(encoding="utf-8")).get("dataset_release", "")
                    if info_path.is_file() else "")
    older_release = "v2_14" in release_name or "v2_13" in release_name
    page_raw_map: dict[tuple[str | None, int | None], str] = {}
    if lineage_path.is_file():
        page_iter = rows(lineage_path)
    elif older_release and (root / "pages.jsonl").is_file():
        page_iter = rows(root / "pages.jsonl")
    else:
        page_iter = iter(())
    for row in page_iter:
        page_raw_map[(row.get("document_id"), row.get("page_index"))] = row.get("raw_text", "")
    docs_by_id = {row.get("document_id"): row for row in documents}
    if not lineage_path.is_file() and not (older_release and (root / "pages.jsonl").is_file()):
        add("required_artifact_missing", "Paper source-page lineage is absent.",
            record_id="lineage/source_pages.jsonl")

    def valid_offsets(row: dict, start: object, end: object, doc: dict) -> bool:
        text = doc.get("text", "")
        return (type(start) is int and type(end) is int and
                0 <= start < end <= len(text) and text[start:end] == row.get("text"))

    for source in source_files:
        if not source.get("sha256"):
            add("source_pdf_checksum_missing", "Source PDF checksum is absent.", source,
                record_id=source.get("source_file_id"))
        relative = source.get("relative_path")
        if not relative:
            continue
        path = (pdf_root / relative).resolve()
        try:
            path.relative_to(pdf_root.resolve())
        except ValueError:
            add("source_pdf_path_unsafe", "Source PDF path escapes the PDF root.", source)
            continue
        if not path.is_file():
            add("source_pdf_missing", "Source PDF is absent.", source)
        elif source.get("sha256") and sha256_file(path) != source["sha256"]:
            add("source_pdf_checksum_mismatch", "Source PDF checksum differs.", source,
                record_id=source.get("source_file_id"))
    if crawler_master is not None:
        master_ids = {row.get("id") for row in rows(Path(crawler_master))}
        inventory_ids = {row.get("metadata_record_id") for row in crawler_records}
        for crawler_id in sorted(master_ids - inventory_ids):
            add("inventory_incomplete", "Crawler record lacks a paper inventory outcome.",
                record_id=str(crawler_id))

    group_splits: defaultdict[str, set[str]] = defaultdict(set)
    for row in documents + split_manifest:
        if row.get("group_id") and row.get("split"):
            group_splits[row["group_id"]].add(row["split"])
    for group_id, values in group_splits.items():
        if len(values) > 1:
            add("group_split_leak", "Group appears in multiple splits.",
                {"group_id": group_id}, record_id=group_id)
    frozen = {row.get("group_id"): row.get("split") for row in split_manifest
              if row.get("group_id")}
    if frozen_split_manifest is not None:
        frozen.update({row.get("group_id"): row.get("split")
                       for row in rows(Path(frozen_split_manifest)) if row.get("group_id")})
    for doc in documents:
        if doc.get("group_id") in frozen and doc.get("split") != frozen[doc["group_id"]]:
            add("frozen_split_mismatch", "Document split differs from frozen group.", doc)
        if doc.get("duplicate_of") and (doc.get("status") == "active" or doc.get("training_eligible")):
            add("duplicate_not_excluded", "Duplicate document remains active.", doc)

    for paragraph in paragraphs:
        doc = docs_by_id.get(paragraph.get("document_id"))
        if doc is not None:
            start = paragraph.get("document_char_start", paragraph.get("char_start"))
            end = paragraph.get("document_char_end", paragraph.get("char_end"))
            if not valid_offsets(paragraph, start, end, doc):
                add("normalized_offset_mismatch", "Paragraph offset does not reconstruct text.",
                    paragraph)

    seen_sentences: set[str] = set()
    for sentence in sentences:
        sentence_id = sentence.get("sentence_id")
        if sentence_id in seen_sentences:
            add("duplicate_sentence_id", "Sentence ID appears more than once.", sentence)
        seen_sentences.add(sentence_id)
        doc = docs_by_id.get(sentence.get("document_id"))
        if doc is not None:
            start = sentence.get("document_char_start", sentence.get("char_start"))
            end = sentence.get("document_char_end", sentence.get("char_end"))
            if not valid_offsets(sentence, start, end, doc):
                add("normalized_offset_mismatch", "Sentence offset does not reconstruct text.", sentence)
    for passage in passages:
        doc = docs_by_id.get(passage.get("document_id"))
        if doc is not None:
            start = passage.get("start_char", passage.get("document_char_start"))
            end = passage.get("end_char", passage.get("document_char_end"))
            if not valid_offsets(passage, start, end, doc):
                add("normalized_offset_mismatch", "Passage offset does not reconstruct text.", passage)
        text = passage.get("text", "")
        if passage.get("text_sha256") and passage["text_sha256"] != sha256_text(text):
            add("text_checksum_mismatch", "Passage checksum differs.", passage)
        if any(ch == "\ufffd" or 0xE000 <= ord(ch) <= 0xF8FF for ch in text):
            add("unresolved_glyph", "Paper passage has an unresolved glyph.", passage)
        tokens = passage.get("approx_tokens")
        if type(tokens) is int:
            if tokens > passage_max:
                add("token_limit_exceeded", "Passage exceeds token maximum.", passage)
            elif tokens < passage_min and not passage.get("short_tail"):
                add("short_tail_unflagged", "Short passage lacks short_tail flag.", passage)
    for row in paragraphs + sentences + passages:
        for source in row.get("source_spans", []):
            page_index = source.get("page_index")
            start, end = source.get("source_start"), source.get("source_end")
            source_text = source.get("text")
            if (type(page_index) is not int or type(start) is not int or
                    type(end) is not int or type(source_text) is not str):
                add("source_span_invalid", "Source span lacks required typed coordinates or text.",
                    row, page_index=page_index if type(page_index) is int else None)
                continue
            doc_id = row.get("document_id")
            key = (doc_id, page_index)
            if key not in page_raw_map:
                add("source_page_missing", "Referenced source page is absent.",
                    row, page_index=page_index if type(page_index) is int else None)
                continue
            raw = page_raw_map[key]
            if not 0 <= start <= end <= len(raw) or raw[start:end] != source_text:
                add("source_span_invalid", "Source span does not match raw page.",
                    row, page_index=page_index)
    return issues


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--pdf-root", type=Path, required=True)
    parser.add_argument("--crawler-master", type=Path, default=None)
    parser.add_argument("--frozen-split-manifest", type=Path, default=None)
    args = parser.parse_args()
    root = args.dataset.resolve(strict=True)
    pdf_root = args.pdf_root.resolve(strict=True)
    info_path = root / "dataset_info.json"
    if (not info_path.is_file() or not (root / "VERSION.json").is_file() or
            not (root / "manifest" / "checksums.sha256").is_file()):
        issues = validate_release(
            root, pdf_root=pdf_root, crawler_master=args.crawler_master,
            frozen_split_manifest=args.frozen_split_manifest)
        report = {"valid": not any(issue.severity == "error" for issue in issues),
                  "error_count": sum(issue.severity == "error" for issue in issues),
                  "errors": [], "warnings": [],
                  "issues": [asdict(issue) for issue in issues]}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report["valid"]:
            raise SystemExit(1)
        return
    errors: list[str] = []
    warnings: list[str] = []
    info = json.loads((root / "dataset_info.json").read_text(encoding="utf-8"))
    counts = info.get("counts", {})

    source_files = list(rows(root / "manifest" / "source_files.jsonl"))
    sources = {row["source_file_id"]: row for row in source_files}
    if len(sources) != len(source_files):
        errors.append("duplicate source_file_id")
    source_docs = {
        row["document_id"] for row in source_files if row.get("release_outcome") == "accepted"
    }
    review_page_sources = 0
    for source in source_files:
        path = (pdf_root / source["relative_path"]).resolve()
        if path.parent != pdf_root or not path.is_file():
            errors.append(f"missing/unsafe source PDF: {source['relative_path']}")
            continue
        if not source.get("sha256"):
            errors.append(f"source PDF checksum missing: {source['relative_path']}")
        elif sha256_file(path) != source["sha256"]:
            errors.append(f"source PDF checksum mismatch: {source['relative_path']}")
        if source.get("extraction_method") != "pymupdf_text_layer":
            errors.append(f"non-text extraction method in source manifest: {source['source_file_id']}")
        if source.get("release_outcome") == "accepted" and source.get("pages_needing_ocr") not in (0, None):
            review_page_sources += 1
        integrity = source.get("text_integrity") or {}
        if source.get("release_outcome") == "accepted" and (
            integrity.get("illegal_control_ratio", 0) > 0.002
            or integrity.get("symbol_ratio", 0) > 0.02
        ):
            errors.append(f"accepted source fails text-integrity gate: {source['source_file_id']}")
    if review_page_sources:
        warnings.append(
            f"{review_page_sources} accepted PDFs include pages with insufficient text-layer content; no OCR was run."
        )

    pdf_inventory_path = root / "manifest" / "pdf_inventory.jsonl"
    pdf_inventory = list(rows(pdf_inventory_path)) if pdf_inventory_path.is_file() else []
    if not pdf_inventory_path.is_file():
        errors.append("missing full PDF inventory")
    inventory_paths: dict[str, dict] = {}
    for entry in pdf_inventory:
        relative = entry.get("source_path")
        if not relative:
            errors.append("PDF inventory row missing source_path")
            continue
        path = (pdf_root / relative).resolve()
        try:
            path.relative_to(pdf_root)
        except ValueError:
            errors.append(f"unsafe PDF inventory path: {relative}")
            continue
        key = str(relative).replace("\\", "/").casefold()
        if key in inventory_paths:
            errors.append(f"duplicate PDF inventory path: {relative}")
        inventory_paths[key] = entry
        if not path.is_file():
            errors.append(f"missing inventoried PDF: {relative}")
        elif sha256_file(path) != entry.get("sha256"):
            errors.append(f"PDF inventory checksum mismatch: {relative}")
    actual_pdf_paths = {
        path.relative_to(pdf_root).as_posix().casefold()
        for path in pdf_root.rglob("*")
        if path.is_file() and path.suffix.casefold() == ".pdf"
    }
    if actual_pdf_paths != set(inventory_paths):
        errors.append(
            "PDF inventory/disk file sets differ: "
            f"missing={len(actual_pdf_paths - set(inventory_paths))}, "
            f"extra={len(set(inventory_paths) - actual_pdf_paths)}"
        )
    if counts.get("audited_source_files") != len(pdf_inventory):
        errors.append("dataset_info PDF inventory count mismatch")

    crawler_path = root / "manifest" / "crawler_records.jsonl"
    crawler_records = list(rows(crawler_path)) if crawler_path.is_file() else []
    if not crawler_path.is_file():
        errors.append("missing crawler record inventory")
    crawler_ids = [row.get("metadata_record_id") for row in crawler_records]
    if any(not record_id for record_id in crawler_ids) or len(set(crawler_ids)) != len(crawler_ids):
        errors.append("crawler inventory has missing or duplicate metadata_record_id")
    if counts.get("crawler_records") != len(crawler_records):
        errors.append("dataset_info crawler record count mismatch")
    allowed_record_statuses = {
        "pdf_present", "pdf_present_flag_false", "metadata_only", "pdf_missing_from_local"
    }
    linked_inventory_paths: set[str] = set()
    for row in crawler_records:
        status = row.get("record_status")
        if status not in allowed_record_statuses:
            errors.append(f"invalid crawler record status: {row.get('metadata_record_id')}: {status}")
        if status in {"pdf_present", "pdf_present_flag_false"}:
            key = str(row.get("pdf_relative_path") or "").replace("\\", "/").casefold()
            if key not in inventory_paths:
                errors.append(f"crawler record has no PDF inventory link: {row.get('metadata_record_id')}")
            else:
                linked_inventory_paths.add(key)
        if row.get("source_file_id") and row["source_file_id"] not in sources:
            errors.append(f"crawler record has unknown source_file_id: {row.get('metadata_record_id')}")
    if counts.get("crawler_records_with_local_pdf") != sum(
        row.get("record_status") in {"pdf_present", "pdf_present_flag_false"}
        for row in crawler_records
    ):
        errors.append("dataset_info linked crawler record count mismatch")
    if counts.get("crawler_metadata_only") != sum(
        row.get("record_status") == "metadata_only" for row in crawler_records
    ):
        errors.append("dataset_info metadata-only count mismatch")
    if counts.get("crawler_pdf_missing_from_local") != sum(
        row.get("record_status") == "pdf_missing_from_local" for row in crawler_records
    ):
        errors.append("dataset_info missing-PDF record count mismatch")

    unmatched_path = root / "manifest" / "unmatched_pdf_files.jsonl"
    unmatched_pdf_files = list(rows(unmatched_path)) if unmatched_path.is_file() else []
    unmatched_paths = {
        str(row.get("source_path") or "").replace("\\", "/").casefold()
        for row in unmatched_pdf_files
    }
    if unmatched_paths != set(inventory_paths) - linked_inventory_paths:
        errors.append("unmatched PDF inventory does not match crawler links")
    if counts.get("unmatched_local_pdf_files") != len(unmatched_pdf_files):
        errors.append("dataset_info unmatched local PDF count mismatch")

    if args.crawler_master:
        master_path = args.crawler_master.resolve(strict=True)
        master_ids = [row.get("id") for row in rows(master_path)]
        if sha256_file(master_path) != info.get("crawler_master_sha256"):
            errors.append("crawler master checksum mismatch")
        if set(master_ids) != set(crawler_ids) or len(master_ids) != len(crawler_ids):
            errors.append("crawler inventory/master record sets differ")

    documents_list = list(rows(root / "documents.jsonl"))
    documents = {row["document_id"]: row for row in documents_list}
    if len(documents) != len(documents_list):
        errors.append("duplicate document_id")
    if set(documents) != source_docs:
        errors.append("source/document ID sets differ")
    group_splits: defaultdict[str, set[str]] = defaultdict(set)
    for document_id, doc in documents.items():
        sid = doc.get("source_file_id")
        source = sources.get(sid)
        if not source or source.get("document_id") != document_id:
            errors.append(f"broken document -> source link: {document_id}")
        required = ("title", "authors", "year", "institution_id", "institution_name")
        missing = [key for key in required if not doc.get(key)]
        if missing:
            errors.append(f"missing required metadata: {document_id}: {missing}")
        if not doc.get("training_eligible") or not doc.get("core_human_eligible"):
            errors.append(f"ineligible document in active release: {document_id}")
        if not isinstance(doc.get("year"), int) or doc["year"] > 2022:
            errors.append(f"invalid Human provenance year: {document_id}: {doc.get('year')}")
        if doc.get("quality_tier") not in {"gold", "silver"}:
            errors.append(f"invalid quality tier: {document_id}")
        if len(doc.get("text", "")) < 5000:
            errors.append(f"document text too short: {document_id}")
        if doc.get("document_text_sha256") != sha256_text(doc.get("text", "")):
            errors.append(f"document text checksum mismatch: {document_id}")
        if unicodedata.normalize("NFC", doc.get("text", "")) != doc.get("text", ""):
            errors.append(f"document text is not NFC: {document_id}")
        illegal_controls = sum(
            unicodedata.category(ch) == "Cc" and ch not in "\n\t\r" for ch in doc.get("text", "")
        )
        if illegal_controls:
            errors.append(f"illegal control characters remain: {document_id}: {illegal_controls}")
        if doc.get("split") not in SPLITS:
            errors.append(f"invalid split: {document_id}")
        group_splits[doc["group_id"]].add(doc["split"])
    for group_id, splits in group_splits.items():
        if len(splits) > 1:
            errors.append(f"group leakage: {group_id}: {sorted(splits)}")

    metadata_rows = list(rows(root / "manifest" / "metadata_records.jsonl"))
    metadata_docs = {row["document_id"] for row in metadata_rows}
    if not set(documents).issubset(metadata_docs):
        errors.append("active document missing metadata record")
    for row in metadata_rows:
        doc = documents.get(row["document_id"])
        if not doc:
            continue
        canonical = row.get("canonical") or {}
        for key in ("title", "authors", "year", "institution_id"):
            if canonical.get(key) != doc.get(key):
                errors.append(f"canonical metadata mismatch: {row['document_id']}:{key}")

    sentence_list = list(rows(root / "sentences.jsonl"))
    sentences = {row["sentence_id"]: row for row in sentence_list}
    if len(sentences) != len(sentence_list):
        errors.append("duplicate sentence_id")
    sentence_usage: Counter[str] = Counter()
    prior_ordinal: defaultdict[str, int] = defaultdict(lambda: -1)
    for sentence in sentence_list:
        doc = documents.get(sentence["document_id"])
        if not doc:
            errors.append(f"sentence missing document: {sentence['sentence_id']}")
            continue
        start, end = sentence["document_char_start"], sentence["document_char_end"]
        if doc["text"][start:end] != sentence["text"]:
            errors.append(f"sentence offset mismatch: {sentence['sentence_id']}")
        if sentence.get("text_sha256") != sha256_text(sentence["text"]):
            errors.append(f"sentence checksum mismatch: {sentence['sentence_id']}")
        if sentence.get("source_file_id") != doc.get("source_file_id"):
            errors.append(f"sentence source mismatch: {sentence['sentence_id']}")
        if sentence["ordinal"] != prior_ordinal[sentence["document_id"]] + 1:
            errors.append(f"non-contiguous sentence ordinal: {sentence['sentence_id']}")
        prior_ordinal[sentence["document_id"]] = sentence["ordinal"]

    paragraphs = list(rows(root / "paragraphs.jsonl"))
    paragraph_ids = {row["paragraph_id"] for row in paragraphs}
    if len(paragraph_ids) != len(paragraphs):
        errors.append("duplicate paragraph_id")
    if paragraph_ids != {row["paragraph_id"] for row in sentence_list}:
        errors.append("paragraph/sentence paragraph ID sets differ")
    for paragraph in paragraphs:
        doc = documents[paragraph["document_id"]]
        start, end = paragraph["document_char_start"], paragraph["document_char_end"]
        if doc["text"][start:end] != paragraph["text"]:
            errors.append(f"paragraph offset mismatch: {paragraph['paragraph_id']}")
        if paragraph["text_sha256"] != sha256_text(paragraph["text"]):
            errors.append(f"paragraph checksum mismatch: {paragraph['paragraph_id']}")
        if any(sentence_id not in sentences for sentence_id in paragraph["sentence_ids"]):
            errors.append(f"paragraph has missing sentence: {paragraph['paragraph_id']}")

    passages = list(rows(root / "passages" / "all.jsonl"))
    passage_ids: set[str] = set()
    fingerprints: dict[str, str] = {}
    passage_split_counts: Counter[str] = Counter()
    prior_chunk: defaultdict[str, int] = defaultdict(lambda: -1)
    for passage in passages:
        passage_id = passage["passage_id"]
        if passage_id in passage_ids:
            errors.append(f"duplicate passage_id: {passage_id}")
        passage_ids.add(passage_id)
        doc = documents.get(passage["document_id"])
        if not doc:
            errors.append(f"passage missing document: {passage_id}")
            continue
        if passage.get("source_file_id") != doc.get("source_file_id"):
            errors.append(f"passage source mismatch: {passage_id}")
        if passage.get("source_sha256") != doc.get("source_sha256"):
            errors.append(f"passage source checksum link mismatch: {passage_id}")
        if passage["split"] != doc["split"]:
            errors.append(f"passage split mismatch: {passage_id}")
        if passage["chunk_index"] != prior_chunk[passage["document_id"]] + 1:
            errors.append(f"non-contiguous passage ordinal: {passage_id}")
        prior_chunk[passage["document_id"]] = passage["chunk_index"]
        missing = [sid for sid in passage["sentence_ids"] if sid not in sentences]
        if missing:
            errors.append(f"passage has missing sentences: {passage_id}")
        else:
            reconstructed = " ".join(sentences[sid]["text"] for sid in passage["sentence_ids"])
            sc = passage.get("document_char_start", passage.get("start_char"))
            ec = passage.get("document_char_end", passage.get("end_char"))
            doc_slice = (
                doc["text"][sc:ec]
                if (doc and type(sc) is int and type(ec) is int and 0 <= sc <= ec <= len(doc["text"]))
                else None
            )
            if passage["text"] != reconstructed and passage["text"] != doc_slice:
                errors.append(f"passage reconstruction mismatch: {passage_id}")
            for sid in passage["sentence_ids"]:
                sentence_usage[sid] += 1
        if passage["text_sha256"] != sha256_text(passage["text"]):
            errors.append(f"passage checksum mismatch: {passage_id}")
        if len(passage.get("section_ids", [])) != 1:
            errors.append(f"passage crosses/omits section: {passage_id}")
        tokens = passage["approx_tokens"]
        if not (128 <= tokens <= 512 or passage.get("short_tail") and tokens < 128):
            errors.append(f"passage token bound: {passage_id}: {tokens}")
        fp = passage["fingerprint_sha256"]
        if fp in fingerprints:
            errors.append(f"duplicate passage text: {passage_id}, {fingerprints[fp]}")
        fingerprints[fp] = passage_id
        passage_split_counts[passage["split"]] += 1

    excluded_passage_count = 0
    excluded_path = root / "manifest" / "excluded_passages.jsonl"
    if excluded_path.exists():
        for passage in rows(excluded_path):
            excluded_passage_count += 1
            if passage["document_id"] not in documents:
                errors.append(f"excluded passage references inactive document: {passage['passage_id']}")
                continue
            missing = [sid for sid in passage["sentence_ids"] if sid not in sentences]
            if missing:
                errors.append(f"excluded passage has missing sentences: {passage['passage_id']}")
            else:
                reconstructed = " ".join(sentences[sid]["text"] for sid in passage["sentence_ids"])
                ex_doc = documents.get(passage["document_id"])
                sc = passage.get("document_char_start", passage.get("start_char"))
                ec = passage.get("document_char_end", passage.get("end_char"))
                doc_slice = (
                    ex_doc["text"][sc:ec]
                    if (ex_doc and type(sc) is int and type(ec) is int and 0 <= sc <= ec <= len(ex_doc["text"]))
                    else None
                )
                if passage["text"] != reconstructed and passage["text"] != doc_slice:
                    errors.append(f"excluded passage reconstruction mismatch: {passage['passage_id']}")
                for sid in passage["sentence_ids"]:
                    sentence_usage[sid] += 1

    unused_sentences = sorted(set(sentences) - set(sentence_usage))
    multiply_used = sorted(sid for sid, count in sentence_usage.items() if count != 1)
    if unused_sentences:
        errors.append(f"sentences absent from passages: {len(unused_sentences)}")
    if multiply_used:
        errors.append(f"sentences used by multiple passages: {len(multiply_used)}")

    for split in SPLITS:
        split_rows = list(rows(root / "splits" / f"{split}.jsonl"))
        expected = {row["passage_id"] for row in passages if row["split"] == split}
        actual = {row["passage_id"] for row in split_rows}
        if actual != expected or len(actual) != len(split_rows):
            errors.append(f"split passage view mismatch: {split}")

    split_manifest = list(rows(root / "manifest" / "split_manifest.jsonl"))
    manifest_docs = {row["document_id"] for row in split_manifest}
    if manifest_docs != set(documents) or len(manifest_docs) != len(split_manifest):
        errors.append("split manifest/document sets differ")
    for row in split_manifest:
        doc = documents[row["document_id"]]
        if row["split"] != doc["split"] or row["group_id"] != doc["group_id"]:
            errors.append(f"split manifest mismatch: {row['document_id']}")

    required_schemas = {
        "document.schema.json", "sentence.schema.json", "paragraph.schema.json",
        "passage.schema.json", "source_file.schema.json",
    }
    schema_paths = {path.name: path for path in (root / "schemas").glob("*.schema.json")}
    if set(schema_paths) != required_schemas:
        errors.append("schema file set mismatch")
    for name, path in schema_paths.items():
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid schema JSON: {name}: {exc}")
            continue
        if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            errors.append(f"unexpected schema dialect: {name}")
        if schema.get("type") != "object" or not schema.get("required"):
            errors.append(f"incomplete schema contract: {name}")

    version = json.loads((root / "VERSION.json").read_text(encoding="utf-8"))
    for key in ("dataset_release", "schema_version", "pipeline_version"):
        if info.get(key) != version.get(key):
            errors.append(f"dataset/version mismatch: {key}")

    checksum_path = root / "manifest" / "checksums.sha256"
    expected_checksums = {}
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        expected_checksums[relative] = digest
    actual_files = {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if path.is_file() and path != checksum_path
    }
    if set(expected_checksums) != set(actual_files):
        errors.append("release checksum manifest file set mismatch")
    for relative, path in actual_files.items():
        if expected_checksums.get(relative) != sha256_file(path):
            errors.append(f"release file checksum mismatch: {relative}")

    structured_issues = validate_release(
        root, pdf_root=pdf_root, crawler_master=args.crawler_master,
        frozen_split_manifest=args.frozen_split_manifest)
    errors.extend(f"{issue.code}: {issue.message}" for issue in structured_issues
                  if issue.severity == "error")
    report = {
        "valid": not errors,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors[:200],
        "warnings": warnings[:200],
        "issues": [asdict(issue) for issue in structured_issues],
        "counts": {
            "source_files": len(source_files),
            "pdf_inventory": len(pdf_inventory),
            "crawler_records": len(crawler_records),
            "crawler_records_by_status": dict(Counter(row.get("record_status") for row in crawler_records)),
            "unmatched_local_pdf_files": len(unmatched_pdf_files),
            "documents": len(documents),
            "paragraphs": len(paragraphs),
            "sentences": len(sentences),
            "passages": len(passages),
            "excluded_passages": excluded_passage_count,
            "passages_by_split": dict(passage_split_counts),
            "accepted_sources_with_unusable_text_pages": review_page_sources,
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
