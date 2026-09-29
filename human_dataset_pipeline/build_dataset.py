#!/usr/bin/env python3
"""Build a reproducible, page-aware Human-written thesis corpus.

Canonical records use UTF-8 JSONL.  The script never edits source PDFs and does
not silently OCR pages: pages without a usable text layer are flagged for the
OCR queue so they cannot contaminate the Human reference corpus.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import re
import statistics
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

import fitz

from layout_reconstruction import (
    LayoutConfig, LayoutUnit, SourceSpan, TextLine, TextSpan,
    reconstruct_document_units,
)
from unicode_normalization import GlyphRule, NormalizationResult, normalize_unit

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


SCHEMA_VERSION = "human-written-v1.2"
PIPELINE_VERSION = "1.2.0"
WS_RE = re.compile(r"[ \t\f\v]+")
TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)
WORD_RE = re.compile(r"\w+", re.UNICODE)
SENTENCE_RE = re.compile(
    r".+?(?:[.!?…]+(?=\s+[A-ZÀ-ỴĐ0-9])|(?=\n)|$)", re.DOTALL
)
HEADING_RE = re.compile(
    r"^(?:chương\s+(?:\d+|[ivxlcdm]+)|\d+(?:\.\d+){0,4}[.)]?\s+|"
    r"mở\s+đầu|kết\s+luận|tóm\s+tắt|abstract|tài\s+liệu\s+tham\s+khảo|"
    r"phụ\s+lục|lời\s+cảm\s+ơn|mục\s+lục)\b",
    re.IGNORECASE,
)
REFERENCE_RE = re.compile(r"^(?:tài\s+liệu\s+tham\s+khảo|references|bibliography)\b", re.I)
APPENDIX_RE = re.compile(r"^(?:phụ\s+lục|appendix)\b", re.I)
FRONT_RE = re.compile(
    r"^(?:lời\s+cảm\s+ơn|lời\s+cam\s+đoan|mục\s+lục|danh\s+mục|abstract|tóm\s+tắt)\b",
    re.I,
)
CODE_RE = re.compile(
    r"(?:\b(?:class|def|function|public|private|return|import|SELECT|INSERT|UPDATE)\b|"
    r"[{};]{2,}|</?[a-z][^>]*>)",
    re.I,
)
TABLE_RE = re.compile(r"(?:\|.*\||\t.*\t|(?:\s{3,}\S+){3,})")
VI_CHARS_RE = re.compile(
    "[àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợ"
    "ùúủũụưứừửữựỳýỷỹỵđ]",
    re.I,
)
VI_COMMON_RE = re.compile(
    r"\b(?:và|của|các|được|trong|một|những|cho|với|này|nghiên\s+cứu|"
    r"hệ\s+thống|phương\s+pháp|kết\s+quả|dữ\s+liệu)\b",
    re.I,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dump_line(handle, record: dict) -> None:
    handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(block_size):
            digest.update(chunk)
    return digest.hexdigest()


def stable_id(prefix: str, *parts: object, length: int = 20) -> str:
    raw = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(raw).hexdigest()[:length]}"


def logical_record_id(source_path: str) -> str | None:
    """Infer a stable repository record id from crawler filenames."""
    stem = Path(source_path).stem
    generic = re.match(r"([a-z][a-z0-9-]*)_([^_]+)", stem, re.I)
    if generic:
        return f"{generic.group(1).lower()}_{generic.group(2)}"
    match = re.match(r"(\d+)", stem)
    return "ou_" + match.group(1) if match else None


def normalized_key(text: str) -> str:
    text = unicodedata.normalize("NFC", text).casefold()
    text = "".join(ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def normalize_line(text: str) -> str:
    text = unicodedata.normalize("NFC", text.replace("\u00a0", " "))
    text = text.replace("\u00ad", "")
    return WS_RE.sub(" ", text).strip()


def normalize_paragraph(text: str) -> str:
    lines = [normalize_line(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ""
    output = lines[0]
    for line in lines[1:]:
        if output.endswith("-") and line[:1].islower():
            output = output[:-1] + line
        else:
            output += " " + line
    return WS_RE.sub(" ", output).strip()


def approx_tokens(text: str) -> int:
    """Conservative tokenizer-neutral estimate; final windows use the real tokenizer."""
    return max(1, math.ceil(len(TOKEN_RE.findall(text)) * 1.18))


def is_heading(text: str) -> bool:
    value = normalize_line(text)
    if not value or len(value) > 180 or value.endswith((".", ",", ";")):
        return False
    if HEADING_RE.match(value):
        return True
    letters = [ch for ch in value if ch.isalpha()]
    return len(letters) >= 4 and sum(ch.isupper() for ch in letters) / len(letters) >= 0.82


def content_type(text: str, section_heading: str) -> str:
    heading = normalize_line(section_heading)
    if REFERENCE_RE.match(heading):
        return "references"
    if APPENDIX_RE.match(heading):
        return "appendix"
    if FRONT_RE.match(heading):
        return "front_matter"
    if CODE_RE.search(text) and len(CODE_RE.findall(text)) >= 2:
        return "code"
    if TABLE_RE.search(text):
        return "table"
    alpha = sum(ch.isalpha() for ch in text)
    symbolic = sum(not (ch.isalnum() or ch.isspace()) for ch in text)
    if len(text) > 20 and symbolic / len(text) > 0.22 and alpha / len(text) < 0.45:
        return "formula"
    return "prose"


@dataclass(frozen=True)
class SentenceLikeUnit:
    text: str
    unit_type: str
    source_spans: tuple[SourceSpan, ...]
    page_start: int
    page_end: int
    review_flags: tuple[str, ...]
    start_char: int = 0
    end_char: int | None = None


@dataclass(frozen=True)
class SentenceFragment(SentenceLikeUnit):
    parent_sentence_id: str = ""
    fragment_index: int = 0
    fragment_count: int = 1


_ABBREVIATIONS = {
    "ts", "ths", "th.s", "gs", "pgs", "pgs.ts", "gs.ts", "pgs.th.s",
    "tp", "dr", "mr", "mrs", "ms", "prof", "vs", "v.v",
}


def _sentence_bounds(text: str, unit_type: str) -> list[tuple[int, int]]:
    """Find prose sentence boundaries after physical lines have been joined."""
    if unit_type in {"bullet_item", "heading", "table_like", "caption"}:
        return [(0, len(text))] if text.strip() else []
    bounds: list[tuple[int, int]] = []
    start = 0
    for index, char in enumerate(text):
        if char not in ".!?…":
            continue
        if char == ".":
            if index and index + 1 < len(text) and text[index - 1].isdigit() and text[index + 1].isdigit():
                continue
            prefix = text[start:index + 1]
            token = re.search(r"([^\s]+)\.$", prefix)
            if token and token.group(1).casefold() in _ABBREVIATIONS:
                continue
            if re.fullmatch(r"\s*\d+(?:\.\d+)*\.", prefix):
                continue
        boundary_end = index + 1
        while boundary_end < len(text) and text[boundary_end] in '\"\'”’)]}':
            boundary_end += 1
        next_index = boundary_end
        while next_index < len(text) and text[next_index].isspace():
            next_index += 1
        if next_index == boundary_end or next_index == len(text):
            continue
        next_char_index = next_index
        while next_char_index < len(text) and text[next_char_index] in "\"'“‘([{":
            next_char_index += 1
        next_char = text[next_char_index:next_char_index + 1]
        if not next_char or not (next_char.isupper() or next_char.isdigit()):
            continue
        left = start + len(text[start:index + 1]) - len(text[start:index + 1].lstrip())
        if left < boundary_end:
            bounds.append((left, boundary_end))
        start = next_index
    left = start + len(text[start:]) - len(text[start:].lstrip())
    end = len(text.rstrip())
    if left < end:
        bounds.append((left, end))
    return bounds


def segment_layout_unit(unit: LayoutUnit, normalized: NormalizationResult) -> list[SentenceLikeUnit]:
    """Segment normalized text within one already reconstructed layout unit."""
    if normalized.status == "review_required" or unit.review_flags:
        return []
    flags = tuple(dict.fromkeys(unit.review_flags + normalized.review_flags))
    return [SentenceLikeUnit(normalized.text[start:end], unit.unit_type,
                             unit.source_spans, unit.page_start, unit.page_end, flags,
                             start, end)
            for start, end in _sentence_bounds(normalized.text, unit.unit_type)]


def split_long_unit(unit: SentenceLikeUnit, max_tokens: int = 512) -> list[SentenceFragment]:
    """Recursively split only an overlong unit, retaining its parent lineage."""
    if max_tokens < 2:
        raise ValueError("max_tokens must accommodate one estimated token")
    spans: list[tuple[int, int]] = []

    def recurse(start: int, end: int) -> None:
        while start < end and unit.text[start].isspace():
            start += 1
        while end > start and unit.text[end - 1].isspace():
            end -= 1
        if start >= end:
            return
        if approx_tokens(unit.text[start:end]) <= max_tokens:
            spans.append((start, end))
            return
        portion = unit.text[start:end]
        tokens = list(TOKEN_RE.finditer(portion))
        budget = max(1, math.floor(max_tokens / 1.18) - 1)
        cut = tokens[min(budget - 1, len(tokens) - 1)].end()
        lower = max(1, cut // 2)
        punctuation = [match.end() for match in re.finditer(r"[.!?…;:,](?=\s)", portion[:cut])
                       if match.end() >= lower]
        if punctuation:
            cut = punctuation[-1]
        else:
            whitespace = portion.rfind(" ", lower, cut)
            if whitespace > 0:
                cut = whitespace
        if cut <= 0 or cut >= len(portion):
            cut = max(1, len(portion) // 2)
        recurse(start, start + cut)
        recurse(start + cut, end)

    recurse(0, len(unit.text))
    parent_id = stable_id("parentsent", unit.page_start, unit.page_end,
                          unit.unit_type, unit.start_char, unit.text)
    return [SentenceFragment(unit.text[start:end], unit.unit_type, unit.source_spans,
                             unit.page_start, unit.page_end, unit.review_flags,
                             unit.start_char + start, unit.start_char + end,
                             parent_id, index, len(spans))
            for index, (start, end) in enumerate(spans)]


def split_sentence_units(text: str, max_tokens: int = 512) -> list[dict]:
    """Backward-compatible text-only view of typed sentence segmentation."""
    results: list[dict] = []
    for parent_ordinal, (start, end) in enumerate(_sentence_bounds(text, "paragraph")):
        unit = SentenceLikeUnit(text[start:end], "paragraph", (), -1, -1, (), start, end)
        for fragment in split_long_unit(unit, max_tokens):
            results.append({"start": fragment.start_char, "end": fragment.end_char,
                            "text": fragment.text, "parent_ordinal": parent_ordinal,
                            "fragment_index": fragment.fragment_index,
                            "fragment_count": fragment.fragment_count,
                            "is_fragment": fragment.fragment_count > 1})
    return results


def split_sentences_with_offsets(text: str, max_tokens: int = 512) -> list[tuple[int, int, str]]:
    """Backward-compatible offset view used by downstream cleaning code."""
    return [(row["start"], row["end"], row["text"]) for row in split_sentence_units(text, max_tokens)]


def sentence_units_for_text(text: str, unit_type: str, page_index: int,
                            source_spans: Sequence[SourceSpan | dict] = (),
                            review_flags: Sequence[str] = (),
                            max_tokens: int = 512) -> list[dict]:
    """Bridge an already normalized record to typed layout sentence units."""
    spans = tuple(source if isinstance(source, SourceSpan) else SourceSpan(
        source["page_index"], source["source_start"], source["source_end"])
        for source in source_spans)
    layout = LayoutUnit(text, unit_type, page_index, page_index, spans, tuple(review_flags))
    identity = NormalizationResult(text, "active", (), (),
                                   tuple(((index, index + 1),) for index in range(len(text))))
    rows: list[dict] = []
    for parent_ordinal, unit in enumerate(segment_layout_unit(layout, identity)):
        for fragment in split_long_unit(unit, max_tokens):
            rows.append({
                "start": fragment.start_char, "end": fragment.end_char,
                "text": fragment.text, "unit_type": fragment.unit_type,
                "source_spans": [
                    {"page_index": span.page_index, "source_start": span.source_start,
                     "source_end": span.source_end} for span in fragment.source_spans
                ],
                "page_start": fragment.page_start, "page_end": fragment.page_end,
                "review_flags": list(fragment.review_flags),
                "parent_ordinal": parent_ordinal,
                "fragment_index": fragment.fragment_index,
                "fragment_count": fragment.fragment_count,
                "is_fragment": fragment.fragment_count > 1,
            })
    return rows


def simhash(text: str) -> int:
    tokens = [token for token in normalized_key(text).split() if len(token) > 1]
    if not tokens:
        return 0
    counts = Counter(" ".join(tokens[i : i + 3]) for i in range(max(1, len(tokens) - 2)))
    vector = [0] * 64
    for token, weight in counts.items():
        value = int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "big")
        for bit in range(64):
            vector[bit] += weight if value & (1 << bit) else -weight
    return sum(1 << bit for bit, value in enumerate(vector) if value >= 0)


def hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


@dataclass
class ExtractedPage:
    page_index: int
    width: float
    height: float
    raw_text: str
    blocks: list[dict]
    extraction_method: str
    needs_ocr: bool
    lines: tuple[TextLine, ...] = ()
    raw_sha256: str = ""
    extraction_status: str = "no_text_layer"
    error_type: str | None = None
    error_message: str | None = None


def extract_pages(pdf_path: Path) -> list[ExtractedPage]:
    """Read only ordered PDF text-layer characters and assign page offsets."""
    pages: list[ExtractedPage] = []
    with fitz.open(pdf_path) as document:
        for page_index, page in enumerate(document):
            try:
                raw = page.get_text("rawdict", sort=True)
                page_parts: list[str] = []
                lines: list[TextLine] = []
                blocks: list[dict] = []
                cursor = 0
                for block_index, block in enumerate(raw.get("blocks", [])):
                    if block.get("type") != 0:
                        continue
                    block_lines: list[str] = []
                    for line in block.get("lines", []):
                        span_rows: list[TextSpan] = []
                        line_text = "".join("".join(char.get("c", "") for char in span.get("chars", []))
                                            for span in line.get("spans", []))
                        if not line_text:
                            continue
                        if page_parts:
                            page_parts.append("\n")
                            cursor += 1
                        start = cursor
                        for span in line.get("spans", []):
                            span_text = "".join(char.get("c", "") for char in span.get("chars", []))
                            if not span_text:
                                continue
                            end = cursor + len(span_text)
                            span_rows.append(TextSpan(
                                span_text, str(span.get("font", "")), float(span.get("size", 0)),
                                tuple(float(value) for value in span.get("bbox", line.get("bbox", (0, 0, 0, 0)))),
                                cursor, end, page_index,
                            ))
                            cursor = end
                        page_parts.append(line_text)
                        block_lines.append(line_text)
                        lines.append(TextLine(
                            page_index, len(lines),
                            tuple(float(value) for value in line.get("bbox", (0, 0, 0, 0))),
                            line_text, tuple(span_rows), start, cursor,
                        ))
                    if block_lines:
                        blocks.append({"bbox": list(block.get("bbox", (0, 0, 0, 0))),
                                       "text": "\n".join(block_lines), "block_no": block_index})
                raw_text = "".join(page_parts)
                status = "text_extracted" if raw_text.strip() else "no_text_layer"
                pages.append(ExtractedPage(
                    page_index, float(page.rect.width), float(page.rect.height), raw_text,
                    blocks, "pymupdf_rawdict" if status == "text_extracted" else "none",
                    status != "text_extracted", tuple(lines),
                    hashlib.sha256(raw_text.encode("utf-8")).hexdigest(), status,
                ))
            except Exception as exc:
                pages.append(ExtractedPage(
                    page_index, float(page.rect.width), float(page.rect.height), "", [],
                    "none", True, (), hashlib.sha256(b"").hexdigest(),
                    "extraction_error", type(exc).__name__, str(exc),
                ))
    return pages


@dataclass
class DocumentBuildResult:
    document: dict
    pages: list[dict]
    paragraphs: list[dict]
    sentences: list[dict]
    passages: list[dict]
    inventory: list[dict]
    normalization_manifest: list[dict]


def load_frozen_splits(path: Path | None) -> dict[str, dict[str, str]]:
    """Index frozen rows by every stable source identity and reject conflicts."""
    lookup: dict[str, dict[str, str]] = {
        "group_id": {}, "document_id": {}, "source_file_id": {}, "source_record_id": {},
    }
    if path is None:
        return lookup
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            split = row.get("split")
            if split not in {"train", "dev", "test"}:
                continue
            for field, values in lookup.items():
                value = row.get(field)
                if not value:
                    continue
                prior = values.get(value)
                if prior is not None and prior != split:
                    raise ValueError(f"frozen split conflict for {field}={value}")
                values[value] = split
    return lookup


def resolve_document_split(record: Mapping[str, object], frozen: Mapping[str, Mapping[str, str]],
                           seed: str = "gips-dora-human-v1") -> str:
    matches = {
        frozen.get(field, {}).get(str(record[field]))
        for field in ("group_id", "document_id", "source_file_id", "source_record_id")
        if record.get(field)
    } - {None}
    if len(matches) > 1:
        raise ValueError("frozen split identities disagree for one document")
    return next(iter(matches)) if matches else assign_split(str(record["group_id"]), seed)


def approved_glyph_rules(pdf_path: Path, pdf_sha256: str | None,
                         document_id: str) -> dict[tuple[str, str, int], GlyphRule]:
    """Enable the reviewed page-49 Symbol bullet only for its exact PDF bytes."""
    if (pdf_path.name != "63617_VO VAN THUAN.pdf" or
            pdf_sha256 != "c492a5589d1aa3fa3d814beca489acb36667c705a55087e1f7b553fd3b37e314"):
        return {}
    return {(document_id, "Symbol", 0xF0B7): GlyphRule(
        replacement="•",
        evidence="layout_fixture_page49.json: page 49, five Symbol U+F0B7 bullet starts; verified PDF SHA-256",
        reviewer_status="reviewed",
    )}


def _unit_source_char_map(text: str, spans: Sequence[TextSpan],
                          raw_pages: Mapping[int, str]) -> list[tuple[tuple[int, int, int], ...]] | None:
    """Align layout input characters to exact (page, start, end) PDF ranges."""
    input_nonspace = [(index, char) for index, char in enumerate(text) if not char.isspace()]
    source_nonspace: list[tuple[int, str, int]] = []
    for span in spans:
        if type(span.page_index) is not int:
            return None
        page = raw_pages.get(span.page_index)
        if (page is None or
                type(span.source_start) is not int or
                type(span.source_end) is not int or
                not 0 <= span.source_start <= span.source_end <= len(page) or
                page[span.source_start:span.source_end] != span.text):
            return None
        source_nonspace.extend((span.page_index, char, span.source_start + offset)
                               for offset, char in enumerate(span.text) if not char.isspace())
    if [char for _, char in input_nonspace] != [char for _, char, _ in source_nonspace]:
        return None
    aligned: list[tuple[tuple[int, int, int], ...]] = [() for _ in text]
    for (index, _), (page_index, _, source_index) in zip(input_nonspace, source_nonspace):
        aligned[index] = ((page_index, source_index, source_index + 1),)
    for match in re.finditer(r"\s+", text):
        left, right = match.start() - 1, match.end()
        if left < 0 or right >= len(text) or not aligned[left] or not aligned[right]:
            continue
        before, after = aligned[left][-1], aligned[right][0]
        if before[0] == after[0] and before[2] <= after[1]:
            gap = raw_pages[before[0]][before[2]:after[1]]
            if gap and gap.isspace():
                aligned[match.start()] = ((before[0], before[2], after[1]),)
    return aligned


def _normalization_source_char_map(
        normalized: NormalizationResult,
        input_map: Sequence[tuple[tuple[int, int, int], ...]],
        ) -> list[tuple[tuple[int, int, int], ...]] | None:
    result: list[tuple[tuple[int, int, int], ...]] = []
    for char, ranges in zip(normalized.text, normalized.source_char_ranges):
        sources = tuple(source for start, end in ranges
                        for index in range(start, end) for source in input_map[index])
        if not char.isspace() and not sources:
            return None
        result.append(sources)
    return result if len(result) == len(normalized.text) else None


def _source_rows_for_chars(char_sources: Sequence[tuple[tuple[int, int, int], ...]],
                           raw_pages: Mapping[int, str]) -> list[dict]:
    ranges = sorted({source for char in char_sources for source in char})
    merged: list[list[int]] = []
    for page_index, start, end in ranges:
        if merged and merged[-1][0] == page_index and start <= merged[-1][2]:
            merged[-1][2] = max(merged[-1][2], end)
        else:
            merged.append([page_index, start, end])
    return [{"page_index": page_index, "source_start": start, "source_end": end,
             "text": raw_pages[page_index][start:end]}
            for page_index, start, end in merged]


def build_document(pdf_path: Path, document_record: Mapping[str, object],
                   frozen_split_manifest: Path | None = None) -> DocumentBuildResult:
    """Build one document from the PDF text layer through typed passages."""
    pdf_path = Path(pdf_path)
    document_id = str(document_record.get("document_id") or stable_id("doc", pdf_path.name))
    group_id = str(document_record.get("group_id") or document_id)
    pdf_sha256 = sha256_file(pdf_path) if pdf_path.is_file() else None
    source_file_id = str(document_record.get("source_file_id") or
                         (f"source_{pdf_sha256[:24]}" if pdf_sha256 else ""))
    identities = {"document_id": document_id, "group_id": group_id,
                  "source_file_id": source_file_id,
                  "source_record_id": document_record.get("source_record_id")}
    split = resolve_document_split(identities, load_frozen_splits(frozen_split_manifest),
                                   str(document_record.get("seed") or "gips-dora-human-v1"))
    base = {"document_id": document_id, "group_id": group_id,
            "source_file_id": source_file_id, "source_record_id": identities["source_record_id"],
            "source_path": str(document_record.get("source_path") or pdf_path),
            "source_pdf_sha256": pdf_sha256, "split": split, "label": "H"}
    try:
        pages = extract_pages(pdf_path)
    except Exception as exc:
        status = "missing_pdf" if isinstance(exc, FileNotFoundError) else "extraction_error"
        inventory = [{**base, "page_index": None, "extraction_status": status,
                      "status": "excluded", "reason": status,
                      "error_type": type(exc).__name__, "error_message": str(exc),
                      "review_flags": []}]
        return DocumentBuildResult({**base, "text": "", "status": "excluded",
                                    "exclusion_reason": status, "page_count": 0},
                                   [], [], [], [], inventory, [])

    raw_pages = {page.page_index: page.raw_text for page in pages}
    page_rows = [{**base, "page_index": page.page_index, "width": page.width,
                  "height": page.height, "raw_text": page.raw_text,
                  "raw_sha256": page.raw_sha256, "extraction_status": page.extraction_status,
                  "error_type": page.error_type, "error_message": page.error_message,
                  "line_count": len(page.lines),
                  "lines": [asdict(line) for line in page.lines]} for page in pages]
    config_value = document_record.get("layout_config")
    config = LayoutConfig(**config_value) if isinstance(config_value, dict) else LayoutConfig()
    layout_units = reconstruct_document_units(
        [page.lines for page in pages], [(page.width, page.height) for page in pages], config)
    line_lookup = {(line.page_index, line.source_start, line.source_end): line
                   for page in pages for line in page.lines}
    rules = document_record.get("glyph_rules")
    glyph_rules: dict[tuple[str, str, int], GlyphRule] = approved_glyph_rules(
        pdf_path, pdf_sha256, document_id)
    if isinstance(rules, Mapping):
        glyph_rules.update(rules)
    paragraphs: list[dict] = []
    sentences: list[dict] = []
    manifest: list[dict] = []
    body_parts: list[str] = []
    document_source_chars: list[tuple[tuple[int, int, int], ...]] = []
    cursor = 0
    page_flags: defaultdict[int, set[str]] = defaultdict(set)
    section_id = stable_id("sec", document_id, 0)
    section_heading = ""
    for unit in layout_units:
        spans = tuple(span for source in unit.source_spans
                      for line in [line_lookup.get((source.page_index, source.source_start,
                                                    source.source_end))] if line is not None
                      for span in line.spans)
        normalized = normalize_unit(unit.text, document_id, spans, glyph_rules, raw_pages)
        input_source_chars = _unit_source_char_map(unit.text, spans, raw_pages)
        output_source_chars = (_normalization_source_char_map(normalized, input_source_chars)
                               if input_source_chars is not None else None)
        if output_source_chars is None and normalized.text:
            normalized = NormalizationResult(normalized.text, "review_required",
                                             tuple(dict.fromkeys(normalized.review_flags +
                                                                 ("source_alignment_unresolved",))),
                                             normalized.rules_applied, normalized.source_char_ranges)
            output_source_chars = [() for _ in normalized.text]
        flags = tuple(dict.fromkeys(unit.review_flags + normalized.review_flags))
        source_rows = [{"page_index": source.page_index, "source_start": source.source_start,
                        "source_end": source.source_end,
                        "text": raw_pages[source.page_index][source.source_start:source.source_end]}
                       for source in unit.source_spans]
        for source in unit.source_spans:
            page_flags[source.page_index].update(flags)
        manifest.append({**base, "unit_index": len(manifest), "unit_type": unit.unit_type,
                         "page_start": unit.page_start, "page_end": unit.page_end,
                         "source_spans": source_rows, "status": normalized.status,
                         "review_flags": list(flags), "rules_applied": list(normalized.rules_applied),
                         "glyph_rule_evidence": [
                             {"document_id": key[0], "font_name": key[1],
                              "codepoint": f"U+{key[2]:04X}", **asdict(rule)}
                             for key, rule in glyph_rules.items()],
                         "layout_config": asdict(config), "text": normalized.text})
        if not normalized.text:
            continue
        if body_parts:
            cursor += 2
            document_source_chars.extend(((), ()))
        paragraph_start = cursor
        body_parts.append(normalized.text)
        document_source_chars.extend(output_source_chars)
        cursor += len(normalized.text)
        paragraph_id = stable_id("par", document_id, len(paragraphs), normalized.text)
        content_kind = ("prose" if unit.unit_type in {"paragraph", "bullet_item"}
                        else unit.unit_type)
        if unit.unit_type == "heading":
            section_heading = normalized.text
            section_id = stable_id("sec", document_id, len(paragraphs), section_heading)
        paragraph = {**base, "paragraph_id": paragraph_id, "ordinal": len(paragraphs),
                     "unit_type": unit.unit_type, "content_type": content_kind,
                     "section_id": section_id, "section_heading": section_heading,
                     "page_start": unit.page_start, "page_end": unit.page_end,
                     "page_index": unit.page_start,
                     "doc_char_start": paragraph_start, "doc_char_end": cursor,
                     "source_spans": source_rows, "normalization_status": normalized.status,
                     "review_flags": list(flags), "source_char_ranges": normalized.source_char_ranges,
                     "text": normalized.text}
        paragraphs.append(paragraph)
        for sentence_unit in segment_layout_unit(unit, normalized):
            parent_id = stable_id("parentsent", document_id, paragraph_id,
                                  sentence_unit.start_char)
            for fragment in split_long_unit(sentence_unit, int(document_record.get("passage_max", 512))):
                sentence_text = fragment.text
                sentence_start = paragraph_start + fragment.start_char
                sentence_end = paragraph_start + fragment.end_char
                exact_sources = _source_rows_for_chars(
                    output_source_chars[fragment.start_char:fragment.end_char], raw_pages)
                sentence_page_start = (min(source["page_index"] for source in exact_sources)
                                       if exact_sources else unit.page_start)
                sentence_page_end = (max(source["page_index"] for source in exact_sources)
                                     if exact_sources else unit.page_end)
                sentences.append({**base, "sentence_id": stable_id("sent", document_id,
                                                                       len(sentences), sentence_text),
                                  "paragraph_id": paragraph_id, "parent_sentence_id": parent_id,
                                  "fragment_index": fragment.fragment_index,
                                  "fragment_count": fragment.fragment_count,
                                  "is_fragment": fragment.fragment_count > 1,
                                  "ordinal": len(sentences), "page_index": sentence_page_start,
                                  "page_start": sentence_page_start, "page_end": sentence_page_end,
                                  "section_id": section_id,
                                  "content_type": content_kind, "unit_type": unit.unit_type,
                                  "normalization_status": normalized.status,
                                  "review_flags": list(flags), "source_spans": exact_sources,
                                  "doc_char_start": sentence_start,
                                  "doc_char_end": sentence_end,
                                  "paragraph_char_start": fragment.start_char,
                                  "paragraph_char_end": fragment.end_char,
                                  "approx_tokens": approx_tokens(sentence_text),
                                  "content_sha256": hashlib.sha256(sentence_text.encode()).hexdigest(),
                                  "text": sentence_text})
    body = "\n\n".join(body_parts)
    passages = make_passages(sentences, int(document_record.get("passage_target", 384)),
                             int(document_record.get("passage_min", 128)),
                             int(document_record.get("passage_max", 512)), body)
    for ordinal, passage in enumerate(passages):
        passage["source_spans"] = _source_rows_for_chars(
            document_source_chars[passage["start_char"]:passage["end_char"]], raw_pages)
        if passage["source_spans"]:
            passage["page_start"] = min(row["page_index"] for row in passage["source_spans"])
            passage["page_end"] = max(row["page_index"] for row in passage["source_spans"])
        passage.update({**base, "passage_id": stable_id("pass", document_id, ordinal, passage["text"]),
                        "ordinal": ordinal, "normalization_status": "active",
                        "review_flags": [], "content_sha256": passage["text_sha256"]})
    inventory = []
    for page in pages:
        flags = sorted(page_flags[page.page_index])
        status = ("excluded" if page.extraction_status != "text_extracted" or flags else "active")
        reason = (page.extraction_status if page.extraction_status != "text_extracted"
                  else "review_required" if flags else None)
        inventory.append({**base, "page_index": page.page_index,
                          "raw_sha256": page.raw_sha256,
                          "extraction_status": page.extraction_status,
                          "status": status, "reason": reason,
                          "review_flags": flags,
                          "error_type": page.error_type, "error_message": page.error_message})
    document_status = "active" if any(row["status"] == "active" for row in inventory) else "excluded"
    document = {**base, "text": body, "status": document_status,
                "exclusion_reason": None if document_status == "active" else "no_active_text",
                "page_count": len(pages), "paragraph_count": len(paragraphs),
                "sentence_count": len(sentences), "passage_count": len(passages),
                "raw_pages": [{"page_index": page.page_index, "raw_sha256": page.raw_sha256}
                              for page in pages]}
    return DocumentBuildResult(document, page_rows, paragraphs, sentences,
                               passages, inventory, manifest)


def repeated_margin_keys(pages: Sequence[ExtractedPage]) -> set[str]:
    appearances: defaultdict[str, set[int]] = defaultdict(set)
    for page in pages:
        for block in page.blocks:
            x0, y0, x1, y1 = block["bbox"]
            if y1 <= page.height * 0.12 or y0 >= page.height * 0.88:
                for line in block["text"].splitlines():
                    key = normalized_key(line)
                    if 3 <= len(key) <= 140 and not key.isdigit():
                        appearances[key].add(page.page_index)
    threshold = max(3, math.ceil(len(pages) * 0.30))
    return {key for key, page_ids in appearances.items() if len(page_ids) >= threshold}


def page_paragraphs(page: ExtractedPage, margin_keys: set[str]) -> list[dict]:
    results: list[dict] = []
    for block in page.blocks:
        _, y0, _, y1 = block["bbox"]
        kept_lines = []
        for line in block["text"].splitlines():
            key = normalized_key(line)
            in_margin = y1 <= page.height * 0.12 or y0 >= page.height * 0.88
            if in_margin and (key in margin_keys or key.isdigit()):
                continue
            kept_lines.append(line)
        value = normalize_paragraph("\n".join(kept_lines))
        if value:
            results.append({"text": value, "bbox": block["bbox"]})
    return results


def quality_metrics(pages: Sequence[ExtractedPage], clean_text: str) -> dict:
    total = len(clean_text)
    alpha = sum(ch.isalpha() for ch in clean_text)
    replacement = clean_text.count("�") + clean_text.count("□")
    extracted_pages = sum(not page.needs_ocr for page in pages)
    vi_chars = len(VI_CHARS_RE.findall(clean_text))
    vi_common = len(VI_COMMON_RE.findall(clean_text))
    alpha_ratio = alpha / max(1, total)
    garbage_ratio = replacement / max(1, total)
    coverage = extracted_pages / max(1, len(pages))
    vi_score = min(1.0, vi_chars / max(1, alpha) * 8 + vi_common / max(1, len(clean_text.split())) * 15)
    score = round(
        100
        * (
            0.42 * min(1.0, coverage)
            + 0.24 * min(1.0, total / 20000)
            + 0.16 * min(1.0, alpha_ratio / 0.65)
            + 0.14 * vi_score
            + 0.04 * (1.0 - min(1.0, garbage_ratio * 50))
        ),
        2,
    )
    if coverage >= 0.90 and total >= 10000 and alpha_ratio >= 0.55 and garbage_ratio <= 0.002 and vi_score >= 0.18:
        tier = "gold"
    elif coverage >= 0.65 and total >= 3000 and alpha_ratio >= 0.45 and garbage_ratio <= 0.01:
        tier = "silver"
    else:
        tier = "quarantine"
    return {
        "tier": tier,
        "score": score,
        "characters": total,
        "alpha_ratio": round(alpha_ratio, 6),
        "garbage_ratio": round(garbage_ratio, 6),
        "vietnamese_score": round(vi_score, 6),
        "page_text_coverage": round(coverage, 6),
        "pages_total": len(pages),
        "pages_needing_ocr": len(pages) - extracted_pages,
    }


def assign_split(group_id: str, seed: str) -> str:
    value = int(hashlib.sha256(f"{seed}:{group_id}".encode()).hexdigest()[:16], 16) / 2**64
    if value < 0.70:
        return "train"
    if value < 0.85:
        return "dev"
    return "test"


def make_passages(sentences: list[dict], target: int, minimum: int, maximum: int,
                  document_text: str | None = None) -> list[dict]:
    """Pack ordered units once, retaining exact document slices when available."""
    eligible = [row for row in sentences if row["content_type"] == "prose"]
    groups: list[list[dict]] = []
    current: list[dict] = []

    def group_text(group: list[dict]) -> str:
        if document_text is None:
            return " ".join(item["text"] for item in group)
        start = group[0].get("doc_char_start", group[0].get("char_start"))
        end = group[-1].get("doc_char_end", group[-1].get("char_end"))
        if start is None or end is None or not 0 <= start <= end <= len(document_text):
            raise ValueError("passage lacks valid document offsets")
        for item in group:
            left = item.get("doc_char_start", item.get("char_start"))
            right = item.get("doc_char_end", item.get("char_end"))
            if document_text[left:right] != item["text"]:
                raise ValueError("sentence text does not match document offsets")
        return document_text[start:end]

    def compatible(left: dict, right: dict) -> bool:
        if right["ordinal"] != left["ordinal"] + 1 or right["section_id"] != left["section_id"]:
            return False
        if document_text is None:
            return True
        end = left.get("doc_char_end", left.get("char_end"))
        start = right.get("doc_char_start", right.get("char_start"))
        return end is not None and start is not None and end <= start and not document_text[end:start].strip()

    for sentence in eligible:
        if approx_tokens(sentence["text"]) > maximum:
            raise ValueError("sentence exceeds passage maximum; split it first")
        if current and (not compatible(current[-1], sentence)
                        or approx_tokens(group_text(current + [sentence])) > maximum):
            groups.append(current)
            current = []
        current.append(sentence)
        if approx_tokens(group_text(current)) >= target:
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    index = len(groups) - 1
    while index > 0:
        if (approx_tokens(group_text(groups[index])) < minimum
                and compatible(groups[index - 1][-1], groups[index][0])
                and approx_tokens(group_text(groups[index - 1] + groups[index])) <= maximum):
            groups[index - 1].extend(groups.pop(index))
        index -= 1

    passages: list[dict] = []
    for group in groups:
        value = group_text(group)
        start = group[0].get("doc_char_start", group[0].get("char_start"))
        end = group[-1].get("doc_char_end", group[-1].get("char_end"))
        source_spans: list[dict] = []
        seen_spans: set[tuple] = set()
        for item in group:
            for source in item.get("source_spans", ()):
                row = (source if isinstance(source, dict) else
                       {"page_index": source.page_index, "source_start": source.source_start,
                        "source_end": source.source_end})
                key = (row["page_index"], row["source_start"], row["source_end"])
                if key not in seen_spans:
                    source_spans.append(row)
                    seen_spans.add(key)
        unit_types = list(dict.fromkeys(item.get("unit_type", "paragraph") for item in group))
        token_count = approx_tokens(value)
        passage = {
            "text": value,
            "start_char": start,
            "end_char": end,
            "sentence_ids": [item["sentence_id"] for item in group],
            "paragraph_ids": list(dict.fromkeys(item["paragraph_id"] for item in group)),
            "page_start": min(item["page_start"] if "page_start" in item else item["page_index"]
                              for item in group),
            "page_end": max(item["page_end"] if "page_end" in item else item["page_index"]
                            for item in group),
            "section_ids": list(dict.fromkeys(item["section_id"] for item in group)),
            "unit_types": unit_types,
            "unit_type": unit_types[0] if len(unit_types) == 1 else "mixed",
            "source_spans": source_spans,
            "approx_tokens": token_count,
            "text_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
            "short_tail": token_count < minimum,
        }
        if document_text is not None and document_text[start:end] != value:
            raise AssertionError("passage offsets do not reconstruct text")
        passages.append(passage)
    return passages


def open_outputs(root: Path) -> dict[str, object]:
    paths = {
        "raw": root / "manifest" / "raw_files.jsonl",
        "inventory": root / "manifest" / "inventory.jsonl",
        "page_inventory": root / "manifest" / "page_inventory.jsonl",
        "normalization_manifest": root / "manifest" / "normalization_manifest.jsonl",
        "extraction": root / "manifest" / "extraction.jsonl",
        "quality": root / "manifest" / "quality.jsonl",
        "duplicates": root / "manifest" / "duplicates.jsonl",
        "split_manifest": root / "manifest" / "split_manifest.jsonl",
        "documents": root / "canonical" / "documents.jsonl",
        "pages": root / "canonical" / "pages.jsonl",
        "paragraphs": root / "canonical" / "paragraphs.jsonl",
        "sentences": root / "canonical" / "sentences.jsonl",
        "passages": root / "canonical" / "passages.jsonl",
        "errors": root / "reports" / "errors.jsonl",
    }
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    return {name: path.open("w", encoding="utf-8", newline="\n") for name, path in paths.items()}


def close_outputs(outputs: dict[str, object]) -> None:
    for handle in outputs.values():
        handle.close()


def _legacy_build(args: argparse.Namespace) -> dict:
    input_dir = args.input.resolve()
    output_dir = args.output.resolve()
    pdf_paths = sorted(input_dir.rglob("*.pdf"), key=lambda path: str(path).casefold())
    if args.limit:
        pdf_paths = pdf_paths[: args.limit]
    if not pdf_paths:
        raise SystemExit(f"No PDFs found under {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = open_outputs(output_dir)
    stats = Counter()
    split_counts = Counter()
    passage_split_counts = Counter()
    seen_file_hash: dict[str, str] = {}
    seen_record_id: dict[str, str] = {}
    seen_content_hash: dict[str, str] = {}
    seen_passage_hash: dict[str, str] = {}
    representatives: list[tuple[str, int, str]] = []
    run_started = utc_now()

    try:
        for file_index, pdf_path in enumerate(pdf_paths, 1):
            relative_path = pdf_path.relative_to(input_dir).as_posix()
            print(f"[{file_index}/{len(pdf_paths)}] {relative_path}", flush=True)
            try:
                file_hash = sha256_file(pdf_path)
                document_id = stable_id("doc", file_hash)
                record_id = logical_record_id(relative_path)
                raw_record = {
                    "schema_version": SCHEMA_VERSION,
                    "document_id": document_id,
                    "source_path": relative_path,
                    "logical_record_id": record_id,
                    "sha256": file_hash,
                    "bytes": pdf_path.stat().st_size,
                }
                json_dump_line(outputs["raw"], raw_record)
                if file_hash in seen_file_hash:
                    json_dump_line(outputs["duplicates"], {"document_id": document_id, "duplicate_of": seen_file_hash[file_hash], "method": "file_sha256"})
                    json_dump_line(
                        outputs["inventory"],
                        {
                            **raw_record,
                            "page_count": None,
                            "outcome": "duplicate",
                            "duplicate_of": seen_file_hash[file_hash],
                            "duplicate_method": "file_sha256",
                        },
                    )
                    stats["duplicate_files"] += 1
                    continue
                seen_file_hash[file_hash] = document_id

                pages, metadata = extract_pages(pdf_path, args.min_page_chars)
                margin_keys = repeated_margin_keys(pages)
                paragraph_records: list[dict] = []
                sentence_records: list[dict] = []
                page_records: list[dict] = []
                doc_parts: list[str] = []
                doc_cursor = 0
                section_heading = "Nội dung"
                section_id = stable_id("sec", document_id, 0, section_heading)
                section_ordinal = 0

                for page in pages:
                    items = page_paragraphs(page, margin_keys)
                    page_clean_parts: list[str] = []
                    page_doc_start = doc_cursor
                    for item in items:
                        value = item["text"]
                        if item.get("unit_type") == "heading" or is_heading(value):
                            section_heading = value
                            section_ordinal += 1
                            section_id = stable_id("sec", document_id, section_ordinal, value)
                            kind = "heading"
                        else:
                            kind = content_type(value, section_heading)
                        unit_type = item.get("unit_type") or (
                            "heading" if kind == "heading" else
                            "bullet_item" if re.match(r"^\s*(?:[•\uf0b7▪●◦]|[-*])\s+", value) else "paragraph"
                        )
                        if doc_parts:
                            doc_cursor += 2
                        paragraph_start = doc_cursor
                        doc_parts.append(value)
                        doc_cursor += len(value)
                        paragraph_id = stable_id("par", document_id, len(paragraph_records), value)
                        paragraph = {
                            "schema_version": SCHEMA_VERSION,
                            "paragraph_id": paragraph_id,
                            "document_id": document_id,
                            "ordinal": len(paragraph_records),
                            "page_index": page.page_index,
                            "section_id": section_id,
                            "section_heading": section_heading,
                            "content_type": kind,
                            "unit_type": unit_type,
                            "source_spans": item.get("source_spans", []),
                            "review_flags": item.get("review_flags", []),
                            "doc_char_start": paragraph_start,
                            "doc_char_end": doc_cursor,
                            "bbox": item["bbox"],
                            "text": value,
                        }
                        paragraph_records.append(paragraph)
                        page_clean_parts.append(value)
                        if value:
                            for unit in sentence_units_for_text(
                                    value, unit_type, page.page_index,
                                    item.get("source_spans", ()), item.get("review_flags", ()),
                                    args.passage_max):
                                local_start, local_end, sentence_text = unit["start"], unit["end"], unit["text"]
                                parent_sentence_id = stable_id(
                                    "parentsent", document_id, paragraph_id, unit["parent_ordinal"]
                                )
                                sentence_records.append(
                                    {
                                        "schema_version": SCHEMA_VERSION,
                                        "sentence_id": stable_id("sent", document_id, len(sentence_records), sentence_text),
                                        "document_id": document_id,
                                        "paragraph_id": paragraph_id,
                                        "parent_sentence_id": parent_sentence_id,
                                        "fragment_index": unit["fragment_index"],
                                        "fragment_count": unit["fragment_count"],
                                        "is_fragment": unit["is_fragment"],
                                        "ordinal": len(sentence_records),
                                        "page_index": page.page_index,
                                        "section_id": section_id,
                                        "content_type": kind,
                                        "unit_type": unit["unit_type"],
                                        "source_spans": unit["source_spans"],
                                        "review_flags": unit["review_flags"],
                                        "page_start": unit["page_start"],
                                        "page_end": unit["page_end"],
                                        "paragraph_char_start": local_start,
                                        "paragraph_char_end": local_end,
                                        "doc_char_start": paragraph_start + local_start,
                                        "doc_char_end": paragraph_start + local_end,
                                        "approx_tokens": approx_tokens(sentence_text),
                                        "content_sha256": hashlib.sha256(
                                            normalized_key(sentence_text).encode("utf-8")
                                        ).hexdigest(),
                                        "text": sentence_text,
                                    }
                                )
                    page_clean_text = "\n\n".join(page_clean_parts)
                    page_records.append(
                        {
                            "schema_version": SCHEMA_VERSION,
                            "page_id": stable_id("page", document_id, page.page_index),
                            "document_id": document_id,
                            "page_index": page.page_index,
                            "width": round(page.width, 2),
                            "height": round(page.height, 2),
                            "extraction_method": page.extraction_method,
                            "needs_ocr": page.needs_ocr,
                            "doc_char_start": page_doc_start,
                            "doc_char_end": doc_cursor,
                            "raw_text": page.raw_text,
                            "text": page_clean_text,
                        }
                    )

                clean_text = "\n\n".join(doc_parts)
                metrics = quality_metrics(pages, clean_text)
                content_hash = hashlib.sha256(normalized_key(clean_text).encode()).hexdigest()
                signature = simhash(clean_text[:500000])
                duplicate_of = None
                duplicate_method = None
                if record_id and record_id in seen_record_id:
                    duplicate_of = seen_record_id[record_id]
                    duplicate_method = "logical_record_id"
                elif content_hash in seen_content_hash:
                    duplicate_of = seen_content_hash[content_hash]
                    duplicate_method = "normalized_content_sha256"
                else:
                    title_key = normalized_key(str(metadata.get("title") or pdf_path.stem))
                    for rep_id, rep_signature, rep_title in representatives:
                        if hamming(signature, rep_signature) <= args.simhash_distance and (
                            not title_key or not rep_title or title_key == rep_title
                        ):
                            duplicate_of = rep_id
                            duplicate_method = "simhash_near_duplicate"
                            break
                    if duplicate_of is None:
                        seen_content_hash[content_hash] = document_id
                        representatives.append((document_id, signature, title_key))
                if record_id and duplicate_of is None:
                    seen_record_id[record_id] = document_id

                group_id = duplicate_of or document_id
                split = "excluded" if duplicate_of or metrics["tier"] == "quarantine" else assign_split(group_id, args.seed)
                if duplicate_of:
                    json_dump_line(outputs["duplicates"], {"document_id": document_id, "duplicate_of": duplicate_of, "method": duplicate_method})
                    stats["duplicate_documents"] += 1

                document_record = {
                    "schema_version": SCHEMA_VERSION,
                    "document_id": document_id,
                    "source_path": relative_path,
                    "logical_record_id": record_id,
                    "source_sha256": file_hash,
                    "content_sha256": content_hash,
                    "title": metadata.get("title") or pdf_path.stem,
                    "author": metadata.get("author") or None,
                    "subject": metadata.get("subject") or None,
                    "page_count": len(pages),
                    "paragraph_count": len(paragraph_records),
                    "sentence_count": len(sentence_records),
                    "character_count": len(clean_text),
                    "quality_tier": metrics["tier"],
                    "duplicate_of": duplicate_of,
                    "group_id": group_id,
                    "split": split,
                    "label": "H",
                    "provenance": "human_written_thesis_pdf",
                }
                json_dump_line(outputs["documents"], document_record)
                for record in page_records:
                    json_dump_line(outputs["pages"], record)
                for record in paragraph_records:
                    json_dump_line(outputs["paragraphs"], record)
                for record in sentence_records:
                    json_dump_line(outputs["sentences"], record)

                passages = make_passages(sentence_records, args.passage_target,
                                         args.passage_min, args.passage_max, clean_text)
                sentence_by_id = {row["sentence_id"]: row for row in sentence_records}
                for ordinal, passage in enumerate(passages):
                    passage_id = stable_id("pass", document_id, ordinal, passage["text"])
                    passage_fingerprint = hashlib.sha256(
                        normalized_key(passage["text"]).encode("utf-8")
                    ).hexdigest()
                    passage_duplicate_of = seen_passage_hash.get(passage_fingerprint)
                    exclusion_reason = None
                    if passage_duplicate_of:
                        exclusion_reason = "exact_passage_duplicate"
                    elif passage["approx_tokens"] < args.passage_min and not passage.get("short_tail"):
                        exclusion_reason = "below_min_tokens"
                    elif passage["approx_tokens"] > args.passage_max:
                        exclusion_reason = "above_max_tokens"
                    passage_split = "excluded" if exclusion_reason else split
                    if passage_duplicate_of:
                        stats["duplicate_passages"] += 1
                        json_dump_line(
                            outputs["duplicates"],
                            {
                                "record_type": "passage",
                                "passage_id": passage_id,
                                "document_id": document_id,
                                "duplicate_of": passage_duplicate_of,
                                "method": "normalized_passage_sha256",
                            },
                        )
                    else:
                        seen_passage_hash[passage_fingerprint] = passage_id
                    passage_record = {
                        "schema_version": SCHEMA_VERSION,
                        "passage_id": passage_id,
                        "document_id": document_id,
                        "group_id": group_id,
                        "ordinal": ordinal,
                        "split": passage_split,
                        "duplicate_of": passage_duplicate_of,
                        "exclusion_reason": exclusion_reason,
                        "label": "H",
                        "content_sha256": passage_fingerprint,
                        "document_char_spans": [
                            [sentence_by_id[sentence_id]["doc_char_start"], sentence_by_id[sentence_id]["doc_char_end"]]
                            for sentence_id in passage["sentence_ids"]
                        ],
                        **passage,
                    }
                    json_dump_line(outputs["passages"], passage_record)
                    if passage_split != "excluded":
                        passage_split_counts[passage_split] += 1

                json_dump_line(outputs["extraction"], {
                    "document_id": document_id,
                    "extractor": "PyMuPDF",
                    "pymupdf_version": fitz.VersionBind,
                    "pages_total": len(pages),
                    "pages_text_layer": sum(not page.needs_ocr for page in pages),
                    "pages_needing_ocr": sum(page.needs_ocr for page in pages),
                    "repeated_margin_keys_removed": len(margin_keys),
                })
                json_dump_line(outputs["quality"], {"document_id": document_id, **metrics})
                json_dump_line(outputs["split_manifest"], {"document_id": document_id, "group_id": group_id, "split": split, "seed": args.seed})
                outcome = "duplicate" if duplicate_of else ("quarantine" if metrics["tier"] == "quarantine" else "accepted")
                json_dump_line(
                    outputs["inventory"],
                    {
                        **raw_record,
                        "page_count": len(pages),
                        "outcome": outcome,
                        "duplicate_of": duplicate_of,
                        "duplicate_method": duplicate_method,
                        "quality_tier": metrics["tier"],
                        "pages_text_layer": sum(not page.needs_ocr for page in pages),
                        "pages_needing_ocr": sum(page.needs_ocr for page in pages),
                    },
                )
                stats["processed"] += 1
                stats[f"tier_{metrics['tier']}"] += 1
                stats["pages"] += len(pages)
                stats["pages_needing_ocr"] += metrics["pages_needing_ocr"]
                stats["paragraphs"] += len(paragraph_records)
                stats["sentences"] += len(sentence_records)
                stats["passages"] += len(passages)
                split_counts[split] += 1
            except Exception as exc:
                stats["errors"] += 1
                json_dump_line(outputs["errors"], {"source_path": relative_path, "error_type": type(exc).__name__, "message": str(exc)})
                json_dump_line(
                    outputs["inventory"],
                    {
                        "schema_version": SCHEMA_VERSION,
                        "document_id": locals().get("document_id"),
                        "source_path": relative_path,
                        "logical_record_id": logical_record_id(relative_path),
                        "sha256": locals().get("file_hash"),
                        "bytes": pdf_path.stat().st_size,
                        "page_count": None,
                        "outcome": "failed",
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    },
                )
                print(f"  ERROR: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    finally:
        close_outputs(outputs)

    # Materialize split-specific passage files without duplicating extraction logic.
    passage_file = output_dir / "canonical" / "passages.jsonl"
    split_handles = {}
    try:
        for split in ("train", "dev", "test"):
            target = output_dir / "splits" / f"{split}_passages.jsonl"
            target.parent.mkdir(parents=True, exist_ok=True)
            split_handles[split] = target.open("w", encoding="utf-8", newline="\n")
        with passage_file.open("r", encoding="utf-8") as handle:
            for line in handle:
                record = json.loads(line)
                if record["split"] in split_handles:
                    split_handles[record["split"]].write(line)
    finally:
        for handle in split_handles.values():
            handle.close()

    summary = {
        "schema_version": SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "run_started_utc": run_started,
        "run_finished_utc": utc_now(),
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "pdfs_discovered": len(pdf_paths),
        "counts": dict(stats),
        "document_splits": dict(split_counts),
        "passage_splits": dict(passage_split_counts),
        "parameters": {
            "seed": args.seed,
            "min_page_chars": args.min_page_chars,
            "passage_target": args.passage_target,
            "passage_min": args.passage_min,
            "passage_max": args.passage_max,
            "simhash_distance": args.simhash_distance,
        },
    }
    with (output_dir / "reports" / "summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    with (output_dir / "VERSION.json").open("w", encoding="utf-8") as handle:
        json.dump({"schema_version": SCHEMA_VERSION, "pipeline_version": PIPELINE_VERSION}, handle, indent=2)
    return summary


def _old_paper_record_links(frozen_split_manifest: Path | None) -> dict[str, dict]:
    """Relate crawler IDs to the existing paper document and group IDs."""
    if frozen_split_manifest is None:
        return {}
    path = frozen_split_manifest.parent.parent / "documents.jsonl"
    if not path.is_file():
        return {}
    links = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                if row.get("source_record_id"):
                    links[row["source_record_id"]] = row
    return links




def build(args: argparse.Namespace) -> dict:
    """Collect crawler outcomes, group duplicates, resolve splits, then serialize."""
    input_dir = args.input.resolve()
    output_dir = args.output.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("output directory must be empty")
    pdf_paths = sorted(input_dir.rglob("*.pdf"), key=lambda path: str(path).casefold())
    if getattr(args, "limit", 0):
        pdf_paths = pdf_paths[:args.limit]
    by_name = {path.name.casefold(): path for path in pdf_paths}
    by_id = {logical_record_id(path.name): path for path in pdf_paths if logical_record_id(path.name)}
    crawler_master = getattr(args, "crawler_master", None)
    if crawler_master is None:
        crawler_rows = [{"id": logical_record_id(path.name) or path.stem,
                         "pdf_path": str(path), "has_full_pdf": True} for path in pdf_paths]
    else:
        with Path(crawler_master).open("r", encoding="utf-8") as handle:
            crawler_rows = [json.loads(line) for line in handle if line.strip()]
    frozen_path = getattr(args, "frozen_split_manifest", None)
    frozen_path = Path(frozen_path) if frozen_path is not None else None
    frozen = load_frozen_splits(frozen_path)
    old_links = _old_paper_record_links(frozen_path)
    frozen_by_record: dict[str, dict] = {}
    if frozen_path is not None:
        with frozen_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    if row.get("source_record_id"):
                        frozen_by_record[str(row["source_record_id"])] = row
    entries: list[dict] = []
    by_path: dict[Path, dict] = {}

    with tempfile.TemporaryDirectory(prefix="gips_spool_") as spool_dir_str:
        spool_dir = Path(spool_dir_str)

        def collect(crawler: dict, crawler_id: str | None) -> dict:
            candidate = crawler.get("pdf_relative_path") or crawler.get("pdf_path")
            path = by_name.get(Path(str(candidate)).name.casefold()) if candidate else None
            if path is None and crawler_id:
                path = by_id.get(str(crawler_id))
            old = {**old_links.get(str(crawler_id), {}),
                   **frozen_by_record.get(str(crawler_id), {})}
            if path in by_path and path is not None:
                return {"crawler": crawler, "crawler_id": crawler_id, "owner": by_path[path]}
            pdf_hash = sha256_file(path) if path is not None else None
            source_key = str(crawler_id or candidate or (path.stem if path else "unknown"))
            doc_id = str(old.get("document_id") or stable_id(
                "doc", pdf_hash if pdf_hash else "crawler", source_key))
            source_id = str(old.get("source_file_id") or (
                f"source_{pdf_hash[:24]}" if pdf_hash else stable_id("source", "crawler", source_key)))
            group_id = str(old.get("group_id") or doc_id)
            record = {"document_id": doc_id, "group_id": group_id,
                      "source_file_id": source_id, "source_record_id": crawler_id,
                      "source_path": path.relative_to(input_dir).as_posix() if path else str(candidate or ""),
                      "seed": args.seed, "passage_target": args.passage_target,
                      "passage_min": args.passage_min, "passage_max": args.passage_max}
            entry = {"crawler": crawler, "crawler_id": crawler_id, "path": path,
                     "pdf_hash": pdf_hash, "record": record, "old": old,
                     "spool_path": None, "error": None, "duplicate_of": None,
                     "duplicate_method": None, "has_result": False,
                     "has_text": False, "content_hash": None, "signature": None,
                     "extraction_status": None, "document_status": None,
                     "exclusion_reason": None, "review_flags": []}
            if path is not None:
                try:
                    res = build_document(path, record, frozen_path)
                except ValueError as exc:
                    if "split" in str(exc):
                        raise
                    entry["error"] = exc
                    res = None
                except Exception as exc:
                    entry["error"] = exc
                    res = None

                if res is not None:
                    entry["has_result"] = True
                    doc = res.document
                    text = doc.get("text", "")
                    if text:
                        exact_text = " ".join(unicodedata.normalize("NFC", text).casefold().split())
                        entry["content_hash"] = hashlib.sha256(exact_text.encode("utf-8")).hexdigest()
                        entry["signature"] = simhash(text[:500000])
                        entry["has_text"] = True
                    entry["document_status"] = doc.get("status")
                    entry["exclusion_reason"] = doc.get("exclusion_reason")
                    entry["extraction_status"] = (
                        "text_extracted" if any(
                            page.get("extraction_status") == "text_extracted" for page in res.pages)
                        else res.inventory[0].get("extraction_status")
                        if res.inventory else "no_text_layer"
                    )
                    entry["review_flags"] = sorted({
                        flag for page in res.inventory
                        for flag in page.get("review_flags", [])
                    })
                    spool_file = spool_dir / f"{record['document_id']}.jsonl.gz"
                    entry["spool_path"] = spool_file
                    with gzip.open(spool_file, "wt", encoding="utf-8") as gz:
                        json.dump({
                            "document": res.document,
                            "pages": res.pages,
                            "paragraphs": res.paragraphs,
                            "sentences": res.sentences,
                            "passages": res.passages,
                            "inventory": res.inventory,
                            "normalization_manifest": res.normalization_manifest,
                        }, gz, ensure_ascii=False)
                    del res
                by_path[path] = entry
            entries.append(entry)
            return entry

        inventory_entries = [collect(row, str(row.get("id")) if row.get("id") is not None else None)
                             for row in crawler_rows]
        for path in pdf_paths:
            if path not in by_path:
                pseudo = {"id": None,
                          "pdf_path": str(path), "has_full_pdf": True}
                inventory_entries.append(collect(pseudo, None))
                inventory_entries[-1]["unmatched"] = True

        representatives: list[tuple[dict, str, int, str]] = []
        ordered = sorted((entry for entry in entries if entry["has_result"] and
                          entry["has_text"]),
                         key=lambda entry: (0 if entry["old"].get("group_id") else 1,
                                            entry["record"]["document_id"]))
        for entry in ordered:
            content_hash = entry["content_hash"]
            signature = entry["signature"]
            title = normalized_key(str(entry["crawler"].get("title") or entry["path"].stem))
            for representative, rep_hash, rep_signature, rep_title in representatives:
                method = ("normalized_content_sha256" if content_hash == rep_hash else
                          "simhash_near_duplicate" if hamming(signature, rep_signature) <=
                          getattr(args, "simhash_distance", 3) and
                          (not title or not rep_title or title == rep_title) else None)
                if method is None:
                    continue
                old_group = entry["old"].get("group_id")
                representative_group = representative["record"]["group_id"]
                if old_group and old_group != representative_group:
                    raise ValueError("frozen duplicate groups conflict")
                entry["duplicate_of"] = representative["record"]["document_id"]
                entry["duplicate_method"] = method
                entry["record"]["group_id"] = representative_group
                break
            if entry["duplicate_of"] is None:
                representatives.append((entry, content_hash, signature, title))

        group_splits: dict[str, str] = {}
        for entry in entries:
            record = entry["record"]
            split = resolve_document_split(record, frozen, args.seed)
            group_id = record["group_id"]
            if group_id in group_splits and group_splits[group_id] != split:
                raise ValueError(f"group split conflict for {group_id}")
            group_splits[group_id] = split
            entry["split"] = split

        output_dir.mkdir(parents=True, exist_ok=True)
        outputs = open_outputs(output_dir)
        stats = Counter()
        try:
            written_groups: set[str] = set()
            for entry in entries:
                record = entry["record"]
                group_id = record["group_id"]
                if group_id not in written_groups:
                    json_dump_line(outputs["split_manifest"], {
                        "group_id": group_id, "document_id": record["document_id"],
                        "source_file_id": record["source_file_id"],
                        "source_record_id": record["source_record_id"],
                        "split": entry["split"], "seed": args.seed})
                    written_groups.add(group_id)
                spool_path = entry.get("spool_path")
                path = entry["path"]
                if spool_path is None:
                    if entry["error"] is not None:
                        json_dump_line(outputs["errors"], {
                            "document_id": record["document_id"],
                            "source_record_id": record["source_record_id"],
                            "error_type": type(entry["error"]).__name__,
                            "message": str(entry["error"])})
                    continue
                with gzip.open(spool_path, "rt", encoding="utf-8") as gz:
                    res_data = json.load(gz)
                res_doc = res_data["document"]
                res_pages = res_data["pages"]
                res_paragraphs = res_data["paragraphs"]
                res_sentences = res_data["sentences"]
                res_passages = res_data["passages"]
                res_inventory = res_data["inventory"]
                res_normalization_manifest = res_data["normalization_manifest"]

                for row in ([res_doc] + res_pages + res_paragraphs +
                            res_sentences + res_passages + res_inventory +
                            res_normalization_manifest):
                    row["group_id"] = group_id
                    row["split"] = entry["split"]
                    row["duplicate_of"] = entry["duplicate_of"]
                if entry["duplicate_of"]:
                    res_doc["status"] = "excluded"
                    res_doc["exclusion_reason"] = entry["duplicate_method"]
                    res_doc["passage_count"] = 0
                    res_passages.clear()

                document = {"schema_version": SCHEMA_VERSION, **res_doc,
                            "source_sha256": entry["pdf_hash"],
                            "title": entry["crawler"].get("title") or path.stem,
                            "quality_tier": "gold" if res_doc["status"] == "active"
                            else "quarantine", "duplicate_method": entry["duplicate_method"]}
                json_dump_line(outputs["documents"], document)
                json_dump_line(outputs["raw"], {"schema_version": SCHEMA_VERSION,
                                                "document_id": record["document_id"],
                                                "source_file_id": record["source_file_id"],
                                                "source_path": record["source_path"],
                                                "sha256": entry["pdf_hash"], "bytes": path.stat().st_size})
                for name, rows in (("pages", res_pages), ("paragraphs", res_paragraphs),
                                   ("sentences", res_sentences), ("passages", res_passages),
                                   ("page_inventory", res_inventory),
                                   ("normalization_manifest", res_normalization_manifest)):
                    for row in rows:
                        json_dump_line(outputs[name], {"schema_version": SCHEMA_VERSION, **row})
                if entry["duplicate_of"]:
                    json_dump_line(outputs["duplicates"], {
                        "document_id": record["document_id"], "group_id": group_id,
                        "duplicate_of": entry["duplicate_of"],
                        "duplicate_method": entry["duplicate_method"]})
                stats["documents"] += 1
                stats["pages"] += len(res_pages)
                stats["sentences"] += len(res_sentences)
                stats["passages"] += len(res_passages)

            for inventory_entry in inventory_entries:
                entry = inventory_entry.get("owner", inventory_entry)
                record = entry["record"]
                error = entry["error"]
                crawler = inventory_entry["crawler"]
                if entry["path"] is None:
                    extraction_status = ("missing_pdf" if crawler.get("has_full_pdf")
                                         else "metadata_only")
                elif error is not None:
                    extraction_status = "build_error"
                elif not entry.get("has_result"):
                    extraction_status = "build_error"
                else:
                    extraction_status = entry["extraction_status"]
                reason = (extraction_status if extraction_status != "text_extracted" else
                          entry["duplicate_method"] or entry.get("exclusion_reason"))
                status = ("excluded" if entry["duplicate_of"] or entry.get("document_status") != "active"
                          else "active")
                json_dump_line(outputs["inventory"], {
                    "schema_version": SCHEMA_VERSION,
                    "crawler_id": inventory_entry.get("crawler_id"),
                    "source_record_id": inventory_entry.get("crawler_id"),
                    "document_id": record["document_id"], "group_id": record["group_id"],
                    "source_file_id": record["source_file_id"],
                    "pdf_path": record["source_path"],
                    "source_path": record["source_path"],
                    "source_pdf_sha256": entry["pdf_hash"],
                    "split": entry["split"], "extraction_status": extraction_status,
                    "status": status, "reason": reason,
                    "duplicate_of": entry["duplicate_of"],
                    "duplicate_method": entry["duplicate_method"],
                    "error_type": type(error).__name__ if error else None,
                    "error_message": str(error) if error else None,
                    "review_flags": entry.get("review_flags", []),
                })
                stats["crawler_rows"] += 1
        finally:
            close_outputs(outputs)
        summary = {"schema_version": SCHEMA_VERSION, "pipeline_version": PIPELINE_VERSION,
                   "counts": dict(stats), "groups": len(group_splits),
                   "crawler_rows": len(crawler_rows), "source_pdf_count": len(by_path)}
        with (output_dir / "reports" / "summary.json").open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=2)
        return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Directory containing source PDFs")
    parser.add_argument("--output", type=Path, required=True, help="New/empty output directory")
    parser.add_argument("--seed", default="gips-dora-human-v1")
    parser.add_argument("--min-page-chars", type=int, default=40)
    parser.add_argument("--passage-target", type=int, default=384)
    parser.add_argument("--passage-min", type=int, default=128)
    parser.add_argument("--passage-max", type=int, default=512)
    parser.add_argument("--simhash-distance", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0, help="Process only N PDFs (smoke testing)")
    parser.add_argument("--crawler-master", type=Path,
                        default=Path(__file__).resolve().parent.parent /
                        "DATASET_CNTT_SAU_PREPROCESS" / "json" / "dataset_cntt_all.jsonl")
    parser.add_argument("--frozen-split-manifest", type=Path, default=None)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("--output must be empty or not exist; datasets are immutable builds")
    if not (0 < args.passage_min <= args.passage_target <= args.passage_max):
        parser.error("require 0 < passage-min <= passage-target <= passage-max")
    return args


if __name__ == "__main__":
    summary = build(parse_args())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
