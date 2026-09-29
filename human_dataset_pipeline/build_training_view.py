#!/usr/bin/env python3
"""Build metadata-rich clean documents and leakage-safe fine-tuning chunks.

This is the corpus-level Reduce -> Map stage. It consumes the immutable v1.2
page/paragraph extraction, joins authoritative repository metadata, cleans the
document body, preserves lineage, and materializes train/dev/test chunks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Iterator

from build_dataset import (approx_tokens, assign_split, load_frozen_splits, make_passages,
                           sentence_units_for_text, stable_id)
from ftfy import fix_text

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCHEMA_VERSION = "human-training-v2.7"
YEAR_RE = re.compile(r"\b(?:19[89]\d|20(?:0\d|1\d|2[0-6]))\b")
MAIN_HEADING_RE = re.compile(
    r"^(?:mở\s+đầu|lời\s+mở\s+đầu|chương\s+(?:1|i)\b|"
    r"1(?:\.0+)?[.)]?\s+(?:giới\s+thiệu|tổng\s+quan|mở\s+đầu))",
    re.I,
)
END_HEADING_RE = re.compile(
    r"^(?:tài\s+liệu\s+tham\s+khảo|references|bibliography|phụ\s+lục|appendix)\b",
    re.I,
)
CAPTION_RE = re.compile(r"^(?:hình|figure|bảng|table|biểu\s+đồ|sơ\s+đồ)\s+\d", re.I)
LABELS = re.compile(
    r"^(?:sinh\s+viên|học\s+viên|tác\s+giả|người\s+thực\s+hiện|"
    r"giảng\s+viên|giáo\s+viên|cán\s+bộ|gvhd|svth|mssv|lớp|khóa)\b",
    re.I,
)
FIELD_STOP_RE = re.compile(
    r"^(?:tên\s+(?:đồ\s+án|đề\s+tài)|đề\s+tài|mssv|student\s+id|lớp|ngành|"
    r"chuyên\s+ngành|khóa|khoa|bộ\s+môn|trường|thành\s+phố|tp\.?\s*|"
    r"ho\s+chi\s+minh\s+city|giảng\s+viên|giáo\s+viên|"
    r"gv(?:hd)?|cán\s+bộ|cbhd|người\s+hướng\s+dẫn|advisor|supervisor)\b",
    re.I,
)


def jsonl(path: Path) -> Iterator[dict]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def dump_line(handle, row: dict) -> None:
    handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def clean_line(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip(" :-–—\t")


def ascii_key(value: str) -> str:
    value = value.casefold().replace("đ", "d")
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def normalize_document_type(value: str | None) -> str:
    key = ascii_key(fix_text(value or "").replace("\u0111", "d"))
    if "luan an" in key or "doctoral" in key or "phd" in key:
        return "doctoral_dissertation"
    if "thac si" in key or "master" in key:
        return "master_thesis"
    if "luan van" in key:
        return "master_thesis"
    if "do an" in key or "capstone" in key or "graduation project" in key:
        return "capstone_project"
    if "khoa luan" in key or "bachelor" in key or "undergraduate thesis" in key:
        return "bachelor_thesis"
    if "thuc tap" in key or "internship" in key:
        return "internship_report"
    if any(token in key for token in ("nghien cuu", "cong trinh", "de tai", "research")):
        return "research_report"
    return "other"


def normalize_academic_unit(value: str | None, title: str | None = None) -> dict:
    """Map noisy academic-unit text to stable IDs and valid Unicode labels."""
    raw = clean_line(value or "") or None
    joined = " ".join(part for part in (value or "", title or "") if part)
    key = ascii_key(fix_text(joined).replace("\u0111", "d"))
    mappings = [
        (("cong nghe thong tin", "khoa hoc may tinh", "phan mem", "computer", "information technology"), "information_technology", "C\u00f4ng ngh\u1ec7 th\u00f4ng tin"),
        (("dien tu", "dien dien", "tu dong hoa", "dieu khien", "embedded", "iot"), "electrical_engineering", "\u0110i\u1ec7n - \u0110i\u1ec7n t\u1eed"),
        (("quan tri kinh doanh", "marketing", "tai chinh", "ke toan", "business"), "business", "Kinh doanh"),
        (("kinh te", "economics"), "economics", "Kinh t\u1ebf"),
        (("cong nghe sinh hoc", "biotechnology"), "biotechnology", "C\u00f4ng ngh\u1ec7 sinh h\u1ecdc"),
        (("co khi", "o to", "dong luc", "mechanical"), "mechanical_engineering", "C\u01a1 kh\u00ed - \u0110\u1ed9ng l\u1ef1c"),
        (("giao duc", "day hoc", "education"), "education", "Gi\u00e1o d\u1ee5c"),
    ]
    for aliases, identifier, name in mappings:
        if any(alias in key for alias in aliases):
            domain = "computer_science" if identifier == "information_technology" else identifier
            return {
                "faculty_raw": raw,
                "faculty_id": identifier,
                "faculty_name": name,
                "major_raw": raw,
                "major_id": identifier,
                "major_name": name,
                "domain_id": domain,
            }
    return {
        "faculty_raw": raw,
        "faculty_id": "other",
        "faculty_name": None,
        "major_raw": raw,
        "major_id": "other",
        "major_name": None,
        "domain_id": "other",
    }


def detect_language(text: str) -> str:
    """Lightweight deterministic vi/en/mixed detection after mojibake repair."""
    sample = fix_text(text[:100000]).casefold()
    words = re.findall(r"[^\W\d_]+", sample, re.UNICODE)
    if not words:
        return "mixed"
    vi_mark_chars = set("\u0103\u00e2\u0111\u00ea\u00f4\u01a1\u01b0")
    vi_common_words = {
        "v\u00e0", "c\u1ee7a", "trong", "\u0111\u01b0\u1ee3c", "c\u00e1c",
        "nghi\u00ean", "k\u1ebft", "h\u1ec7", "m\u1ed9t", "nh\u1eefng",
    }
    vi_marks = sum(any(ch in vi_mark_chars or unicodedata.combining(ch) for ch in unicodedata.normalize("NFD", word)) for word in words)
    vi_common = sum(word in vi_common_words for word in words)
    en_common = sum(word in {"the", "of", "and", "in", "to", "is", "for", "this"} for word in words)
    vi_score = (vi_marks + vi_common * 2) / len(words)
    en_score = en_common / len(words)
    if vi_score >= 0.025 and en_score >= 0.025:
        return "mixed"
    if vi_score >= 0.025:
        return "vi"
    if en_score >= 0.025:
        return "en"
    return "mixed"


def year_bucket(year: int | None) -> str:
    if year is None:
        return "unknown"
    if year <= 2010:
        return "pre_2011"
    if year <= 2015:
        return "2011_2015"
    if year <= 2019:
        return "2016_2019"
    if year <= 2021:
        return "2020_2021"
    if year == 2022:
        return "2022"
    return "2023_plus"


def normalize_institution(value: str | None) -> dict:
    raw = clean_line(value or "")
    key = ascii_key(raw)
    if "dai hoc mo" in key:
        return {
            "institution_id": "ou_hcmc",
            "institution_name": "Trường Đại học Mở Thành phố Hồ Chí Minh",
            "institution_raw": raw or None,
        }
    if "su pham ky thuat" in key and ("ho chi minh" in key or "tp hcm" in key):
        return {
            "institution_id": "hcmute",
            "institution_name": "Trường Đại học Sư phạm Kỹ thuật Thành phố Hồ Chí Minh",
            "institution_raw": raw or None,
        }
    if "dai hoc quoc gia ha noi" in key or key == "vnu hanoi":
        return {
            "institution_id": "vnu_hanoi",
            "institution_name": "Đại học Quốc gia Hà Nội",
            "institution_raw": raw or None,
        }
    fallback_id = "institution_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12] if key else "institution_unknown"
    return {
        "institution_id": fallback_id,
        "institution_name": raw or "Không xác định",
        "institution_raw": raw or None,
    }


def canonical_metadata_id(source_path: str) -> str | None:
    stem = Path(source_path).stem
    generic = re.match(r"([a-z][a-z0-9-]*)_([^_]+)", stem, re.I)
    if generic:
        return f"{generic.group(1).lower()}_{generic.group(2)}"
    match = re.match(r"(\d+)", stem)
    return "ou_" + match.group(1) if match else None


def pdf_basename_key(value: str | None) -> str:
    """Normalize a PDF basename across Windows paths and repaired mojibake."""
    basename = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    return unicodedata.normalize("NFC", fix_text(basename)).casefold()


def load_repository_metadata(metadata_dir: Path) -> dict[str, dict]:
    records: dict[str, dict] = {}
    master_path = metadata_dir / "dataset_cntt_all.jsonl"
    paths = sorted(metadata_dir.glob("*_theses.jsonl"))
    if master_path.is_file():
        paths.append(master_path)
    for path in paths:
        for row in jsonl(path):
            record_id = row.get("id")
            if not record_id:
                continue
            merged = dict(records.get(record_id, {}))
            for key, value in row.items():
                if value not in (None, "", [], {}):
                    merged[key] = value
            records[record_id] = merged
    return records


def find_repository_metadata(source_path: str, records: dict[str, dict]) -> dict | None:
    """Match a PDF to crawler metadata by canonical record ID or PDF basename."""
    record_id = canonical_metadata_id(source_path)
    if record_id and record_id in records:
        return records[record_id]
    source_key = pdf_basename_key(source_path)
    if not source_key:
        return None
    for row in records.values():
        if any(
            pdf_basename_key(row.get(field)) == source_key
            for field in ("pdf_path", "id")
            if row.get(field)
        ):
            return row
    return None


def institution_from_source(source: str) -> str | None:
    lower = source.casefold()
    if "hcmute" in lower or "sư phạm kỹ thuật" in lower:
        return "Trường Đại học Sư phạm Kỹ thuật TP.HCM"
    if "đại học mở" in lower or "(ou)" in lower:
        return "Trường Đại học Mở TP.HCM"
    if "vnu" in lower or "đhqg hà nội" in lower:
        return "Đại học Quốc gia Hà Nội"
    return source or None


def clean_person_value(value: str) -> str:
    value = clean_line(value)
    value = re.sub(r"^\d+\s*[:.)-]\s*", "", value)
    value = re.sub(r"\.{3,}.*$", "", value).strip()
    value = re.sub(r"(?i)\b(?:MSSV|MSHV|student\s*(?:id|identity))\s*[:.]?\s*\w+.*$", "", value)
    value = re.sub(r"\s*[:.-]\s*\d{7,12}\s*$", "", value)
    return clean_line(value)


def looks_like_person(value: str) -> bool:
    if not value or len(value) > 120 or FIELD_STOP_RE.match(value):
        return False
    if any(ch.isdigit() for ch in value):
        return False
    if re.search(r"\b(?:download|tá»«\s+khÃ³a|ngÃ y\s+sinh|nÆ¡i\s+sinh|call\s*no|pages?)\b", value, re.I):
        return False
    words = re.findall(r"[A-Za-zÀ-ỹĐđ]+", value)
    if not 2 <= len(words) <= 12:
        return False
    return sum(ch.isalpha() for ch in value) / max(1, len(value)) >= 0.55


def clean_people(values) -> list[str]:
    if isinstance(values, str):
        values = [values]
    cleaned = [clean_person_value(str(value)) for value in (values or [])]
    return list(dict.fromkeys(value for value in cleaned if looks_like_person(value)))


def values_after_label(lines: list[str], pattern: str, max_people: int = 3) -> list[str]:
    regex = re.compile(pattern, re.I)
    values: list[str] = []
    for index, line in enumerate(lines):
        match = regex.search(line)
        if not match:
            continue
        inline = clean_line(line[match.end() :])
        candidates = ([inline] if inline else []) + lines[index + 1 : index + 5]
        local: list[str] = []
        for value in candidates:
            value = clean_person_value(value)
            if not value:
                continue
            if FIELD_STOP_RE.match(value) or LABELS.match(value) or YEAR_RE.fullmatch(value):
                break
            if looks_like_person(value):
                local.append(value)
            if len(local) >= max_people:
                break
        if local:
            values.extend(local)
            break
    return list(dict.fromkeys(values))


def cover_metadata(text: str, fallback_title: str) -> dict:
    lines = [clean_line(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    upper_lines = [line for line in lines if len(line) <= 160]
    institution = next(
        (
            line
            for line in upper_lines
            if re.search(r"\b(?:TRƯỜNG\s+ĐẠI\s+HỌC|ĐẠI\s+HỌC\s+QUỐC\s+GIA|HỌC\s+VIỆN)\b", line, re.I)
        ),
        None,
    )
    faculty = next((line for line in upper_lines if re.match(r"^(?:KHOA|VIỆN|BỘ\s+MÔN)\b", line, re.I)), None)
    authors = values_after_label(
        lines,
        r"^(?:(?:họ\s+và\s+tên\s+)?sinh\s+viên(?:\s+thực\s+hiện)?|"
        r"học\s+viên|tác\s+giả|người\s+thực\s+hiện|tôi\s+tên\s+là|"
        r"sv\s*thực\s*hiện|svth|student)\b\s*[:.-]?",
        max_people=5,
    )
    if not authors:
        # Covers sometimes list names as "NAME: student-id" without a label.
        authors = [
            clean_person_value(match.group(1))
            for line in lines[:120]
            if (match := re.match(r"^([A-ZÀ-ỴĐ][A-ZÀ-ỴĐ\s.]{5,80})\s*:\s*\d{7,12}\s*$", line))
        ][:5]
    advisors = values_after_label(
        lines,
        r"^(?:(?:giảng|giáo)\s+viên\s+hướng\s+dẫn|gv\s+hướng\s+dẫn|gvhd|"
        r"cán\s+bộ\s+hướng\s+dẫn|cbhd|người\s+hướng\s+dẫn|hướng\s+dẫn\s+khoa\s+học|"
        r"advisor|supervisor)\b\s*[:.-]?",
        max_people=3,
    )
    years = []
    for line in lines:
        if re.search(r"(?:năm|tháng|tp\.?\s*hồ\s+chí\s+minh|ho\s+chi\s+minh\s+city|hà\s+nội|đà\s+nẵng)", line, re.I) or YEAR_RE.fullmatch(line):
            years.extend(int(value) for value in YEAR_RE.findall(line))
    year = years[-1] if years else None
    document_type = next(
        (
            label
            for label in ("Luận án", "Luận văn thạc sĩ", "Luận văn", "Khóa luận tốt nghiệp", "Đồ án tốt nghiệp", "Báo cáo nghiên cứu")
            if any(label.casefold() in line.casefold() for line in lines[:100])
        ),
        None,
    )
    # A conservative title heuristic. Uncertain titles stay in the review queue.
    title = fallback_title
    title_from_label = False
    for index, line in enumerate(lines[:100]):
        if re.search(r"(?:tên\s+đề\s+tài|đề\s+tài)\s*[:.-]?\s*$", line, re.I):
            candidate = " ".join(lines[index + 1 : index + 4])
            candidate = clean_line(candidate)
            if 10 <= len(candidate) <= 300:
                title = candidate
                title_from_label = True
                break
    if not title_from_label:
        type_re = re.compile(
            r"^(?:khóa\s+luận|khoá\s+luận|đồ\s+án|luận\s+văn|luận\s+án|"
            r"graduate\s+thesis|master(?:'s)?\s+thesis)\b",
            re.I,
        )
        author_keys = {ascii_key(author) for author in authors}
        for index, line in enumerate(lines[:120]):
            if not type_re.match(line):
                continue
            collected: list[str] = []
            for previous in reversed(lines[max(0, index - 7) : index]):
                key = ascii_key(previous)
                if key in author_keys:
                    break
                if re.search(r"(?:bộ\s+giáo\s+dục|ministry|đại\s+học|university|khoa\b|faculty)", previous, re.I):
                    break
                if len(previous) >= 5 and any(ch.isalpha() for ch in previous):
                    collected.append(previous)
            candidate = clean_line(" ".join(reversed(collected)))
            if 10 <= len(candidate) <= 300:
                title = candidate
                break
    found = sum(bool(value) for value in (title, authors, year, institution))
    return {
        "title": title,
        "authors": authors,
        "advisors": advisors,
        "year": year,
        "degree": document_type,
        "institution": institution,
        "school_or_faculty": faculty,
        "keywords": [],
        "abstract": "",
        "source": None,
        "url": None,
        "source_record_id": None,
        "metadata_source": "cover_heuristic",
        "metadata_confidence": round(found / 4, 3),
    }


def merge_metadata(doc: dict, official: dict | None, cover_text: str, year_text: str | None = None) -> dict:
    cover = cover_metadata(cover_text, doc.get("title") or Path(doc["source_path"]).stem)
    if year_text is not None:
        cover["year"] = cover_metadata(
            year_text, doc.get("title") or Path(doc["source_path"]).stem
        ).get("year")
    if official:
        source = official.get("source") or ""
        field_sources = {}

        def choose(field: str, official_value, cover_value):
            if official_value:
                field_sources[field] = "official_repository"
                return official_value
            if cover_value:
                field_sources[field] = "cover_heuristic"
                return cover_value
            field_sources[field] = "missing"
            return official_value or cover_value

        title = choose("title", official.get("title"), cover.get("title") or doc.get("title"))
        official_authors = clean_people(official.get("authors"))
        official_advisors = clean_people(official.get("advisors"))
        authors = choose("authors", official_authors, cover.get("authors")) or []
        advisors = choose("advisors", official_advisors, cover.get("advisors")) or []
        repository_year = official.get("year")
        cover_year = cover.get("year")
        if cover_year:
            year = cover_year
            field_sources["year"] = "cover_heuristic"
        else:
            year = repository_year
            field_sources["year"] = "official_repository" if repository_year else "missing"
        year_conflict = bool(repository_year and cover_year and repository_year != cover_year)
        degree = choose("degree", official.get("degree"), cover.get("degree"))
        institution = choose("institution", institution_from_source(source), cover.get("institution"))
        school = choose("school_or_faculty", official.get("school_or_faculty"), cover.get("school_or_faculty"))
        major = official.get("major") or official.get("major_name") or official.get("specialization")
        required = (title, authors, year, institution)
        return {
            "title": title,
            "authors": authors,
            "advisors": advisors,
            "year": year,
            "repository_year": repository_year,
            "cover_year": cover_year,
            "year_conflict": year_conflict,
            "degree": degree,
            "institution": institution,
            "provided_institution_id": official.get("institution_id"),
            "provided_institution_name": official.get("institution_name"),
            "school_or_faculty": school,
            "major_source": major,
            "keywords": official.get("keywords") or [],
            "abstract": official.get("abstract") or "",
            "repository": source or None,
            "source_url": official.get("url"),
            "source_record_id": official.get("id"),
            "matched_it_tags": official.get("matched_it_tags") or [],
            "crawl_time": official.get("crawl_time"),
            "metadata_source": "official_repository_with_cover_fallback",
            "metadata_field_sources": field_sources,
            "metadata_confidence": round(sum(bool(value) for value in required) / len(required), 3),
        }
    return {
        **cover,
        "repository_year": None,
        "cover_year": cover.get("year"),
        "year_conflict": False,
        "repository": None,
        "source_url": None,
        "matched_it_tags": [],
        "crawl_time": None,
        "provided_institution_id": None,
        "provided_institution_name": None,
        "major_source": None,
        "metadata_field_sources": {
            field: ("cover_heuristic" if cover.get(field) else "missing")
            for field in ("title", "authors", "advisors", "year", "institution", "school_or_faculty", "degree")
        },
    }


def provenance_status(year: int | None) -> str:
    if year is None:
        return "unknown_year"
    if year <= 2022:
        return "high_confidence_human"
    return "recent_provenance_uncertain"


def paragraph_groups(path: Path) -> Iterator[tuple[str, list[dict]]]:
    current_id = None
    group: list[dict] = []
    for row in jsonl(path):
        if current_id is not None and row["document_id"] != current_id:
            yield current_id, group
            group = []
        current_id = row["document_id"]
        group.append(row)
    if current_id is not None:
        yield current_id, group


def select_clean_paragraphs(paragraphs: list[dict]) -> tuple[list[dict], dict]:
    max_page = max((row["page_index"] for row in paragraphs), default=0)
    main_rows = [
        row
        for row in paragraphs
        if row["content_type"] == "heading"
        and row["page_index"] >= 3
        and MAIN_HEADING_RE.match(clean_line(row["text"]))
    ]
    # TOC entries look like headings too. A real chapter start is followed by
    # substantial prose, whereas a TOC occurrence is followed by more headings.
    main_candidates = []
    for candidate in main_rows:
        following = paragraphs[candidate["ordinal"] + 1 : candidate["ordinal"] + 31]
        prose_chars = sum(len(row["text"]) for row in following if row["content_type"] == "prose")
        if prose_chars >= 800:
            main_candidates.append(candidate["ordinal"])
    if main_candidates:
        start = min(main_candidates)
        start_method = "main_heading_with_prose_context"
    else:
        page_candidates = [row["ordinal"] for row in paragraphs if row["content_type"] == "prose" and row["page_index"] >= 4]
        start = min(page_candidates) if page_candidates else 0
        start_method = "page_fallback"
    late_ordinal = max(start + 1, int(len(paragraphs) * 0.50))
    late_page = max(5, int(max_page * 0.50))
    end_candidates = [
        row["ordinal"]
        for row in paragraphs
        if row["ordinal"] >= late_ordinal
        and row["page_index"] >= late_page
        and row["content_type"] == "heading"
        and END_HEADING_RE.match(clean_line(row["text"]))
    ]
    end = min(end_candidates) if end_candidates else 10**12
    selected: list[dict] = []
    rejection = Counter()
    for row in paragraphs:
        text = clean_line(row["text"])
        reason = None
        if row["ordinal"] <= start:
            reason = "before_main_content"
        elif row["ordinal"] >= end:
            reason = "after_references_or_appendix"
        elif row["content_type"] != "prose":
            reason = f"content_type_{row['content_type']}"
        elif row.get("normalization_status") == "review_required" or row.get("review_flags"):
            reason = "normalization_review_required"
        elif CAPTION_RE.match(text):
            reason = "standalone_caption"
        elif len(text) < 30 or sum(ch.isalpha() for ch in text) < 15:
            reason = "too_short_or_nonlinguistic"
        if reason:
            rejection[reason] += 1
            continue
        selected.append({**row, "text": text})
    return selected, {"start_ordinal": start, "end_ordinal": None if end == 10**12 else end, "start_method": start_method, "rejected_paragraphs": dict(rejection)}


def build_clean_text(document_id: str, paragraphs: list[dict],
                     max_tokens: int = 512) -> tuple[str, list[dict], list[dict]]:
    parts: list[str] = []
    clean_paragraphs: list[dict] = []
    sentences: list[dict] = []
    cursor = 0
    for paragraph in paragraphs:
        if parts:
            cursor += 2
        start = cursor
        text = paragraph["text"]
        parts.append(text)
        cursor += len(text)
        clean_paragraphs.append(
            {
                "paragraph_id": paragraph["paragraph_id"],
                "section_id": paragraph["section_id"],
                "section_heading": paragraph["section_heading"],
                "page_index": paragraph["page_index"],
                "unit_type": paragraph.get("unit_type", "paragraph"),
                "normalization_status": paragraph.get("normalization_status", "active"),
                "review_flags": paragraph.get("review_flags", []),
                "source_spans": paragraph.get("source_spans", []),
                "char_start": start,
                "char_end": cursor,
            }
        )
        for unit in sentence_units_for_text(
                text, paragraph.get("unit_type", "paragraph"), paragraph["page_index"],
                paragraph.get("source_spans", ()), paragraph.get("review_flags", ()),
                max_tokens=max_tokens):
            local_start, local_end, sentence_text = unit["start"], unit["end"], unit["text"]
            parent_sentence_id = stable_id(
                "parentsent2", document_id, paragraph["paragraph_id"], unit["parent_ordinal"]
            )
            sentences.append(
                {
                    "sentence_id": stable_id("sent2", document_id, len(sentences), sentence_text),
                    "document_id": document_id,
                    "group_id": None,
                    "paragraph_id": paragraph["paragraph_id"],
                    "parent_sentence_id": parent_sentence_id,
                    "fragment_index": unit["fragment_index"],
                    "fragment_count": unit["fragment_count"],
                    "is_fragment": unit["is_fragment"],
                    "ordinal": len(sentences),
                    "page_index": paragraph["page_index"],
                    "section_id": paragraph["section_id"],
                    "section_heading": paragraph["section_heading"],
                    "content_type": "prose",
                    "unit_type": unit["unit_type"],
                    "normalization_status": paragraph.get("normalization_status", "active"),
                    "source_spans": unit["source_spans"],
                    "review_flags": unit["review_flags"],
                    "page_start": unit["page_start"],
                    "page_end": unit["page_end"],
                    "char_start": start + local_start,
                    "char_end": start + local_end,
                    "approx_tokens": approx_tokens(sentence_text),
                    "content_sha256": hashlib.sha256(
                        " ".join(sentence_text.casefold().split()).encode("utf-8")
                    ).hexdigest(),
                    "label": "H",
                    "text": sentence_text,
                }
            )
    return "\n\n".join(parts), clean_paragraphs, sentences


def assign_stratified_splits(rows: list[dict], seed: str) -> dict[str, str]:
    """Deterministic 70/15/15 assignment within sufficiently large strata."""
    strata: defaultdict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        key = (
            row["metadata"]["institution_id"],
            row["metadata"]["year_bucket"],
            row["metadata"]["domain_id"],
            row["metadata"]["document_type_id"],
        )
        strata[key].append(row)
    assignments: dict[str, str] = {}
    for key, members in strata.items():
        ordered = sorted(
            members,
            key=lambda row: hashlib.sha256(
                f"{seed}:{key}:{row['group_id']}".encode("utf-8")
            ).hexdigest(),
        )
        count = len(ordered)
        if count < 7:
            for row in ordered:
                value = int(hashlib.sha256(f"{seed}:{row['group_id']}".encode()).hexdigest()[:16], 16) / 2**64
                assignments[row["group_id"]] = "train" if value < 0.70 else ("dev" if value < 0.85 else "test")
            continue
        train_count = max(1, round(count * 0.70))
        dev_count = max(1, round(count * 0.15))
        if train_count + dev_count >= count:
            train_count = count - 2
            dev_count = 1
        for index, row in enumerate(ordered):
            assignments[row["group_id"]] = "train" if index < train_count else ("dev" if index < train_count + dev_count else "test")
    return assignments


def resolve_training_splits(rows: list[dict], manifest_path: Path, seed: str) -> dict[str, str]:
    """Carry base group splits into the training view and hash only new groups."""
    frozen = load_frozen_splits(manifest_path)
    assignments: dict[str, str] = dict(frozen["group_id"])
    for row in rows:
        group_id = row["group_id"]
        doc = row["doc"]
        known = {value for value in (
            assignments.get(group_id), frozen["document_id"].get(row["document_id"]),
            frozen["source_file_id"].get(doc.get("source_file_id")),
            frozen["source_record_id"].get(doc.get("source_record_id")),
            doc.get("split") if doc.get("split") in {"train", "dev", "test"} else None,
        ) if value is not None}
        if len(known) > 1:
            raise ValueError(f"changed frozen split for group {group_id}")
        split = next(iter(known)) if known else assign_split(group_id, seed)
        if group_id in assignments and assignments[group_id] != split:
            raise ValueError(f"group split conflict for {group_id}")
        assignments[group_id] = split
    return assignments


def build(args: argparse.Namespace) -> dict:
    base = args.base.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "chunks").mkdir()
    (output / "review").mkdir()
    (output / "reports").mkdir()
    metadata_records = load_repository_metadata(args.metadata_dir.resolve())
    documents = {row["document_id"]: row for row in jsonl(base / "canonical" / "documents.jsonl")}
    cover_pages: defaultdict[str, list[tuple[int, str]]] = defaultdict(list)
    for page in jsonl(base / "canonical" / "pages.jsonl"):
        if page["page_index"] < args.cover_pages:
            cover_pages[page["document_id"]].append((page["page_index"], page["raw_text"]))

    enriched: dict[str, dict] = {}
    review_rows: list[dict] = []
    for doc_id, doc in documents.items():
        official = find_repository_metadata(doc["source_path"], metadata_records)
        ordered_cover_pages = sorted(cover_pages.get(doc_id, []))
        cover_text = "\n\n".join(text for _, text in ordered_cover_pages)
        year_text = "\n\n".join(text for page_index, text in ordered_cover_pages if page_index < 2)
        metadata = merge_metadata(doc, official, cover_text, year_text)
        if metadata.get("provided_institution_id"):
            explicit_id = re.sub(r"[^a-z0-9_]+", "_", str(metadata["provided_institution_id"]).casefold()).strip("_")
            explicit_name = metadata.get("provided_institution_name") or metadata.get("institution") or "Không xác định"
            institution = {
                "institution_id": explicit_id,
                "institution_name": clean_line(explicit_name),
                "institution_raw": clean_line(metadata.get("institution") or explicit_name),
            }
        else:
            institution = normalize_institution(metadata.get("institution"))
        metadata.pop("provided_institution_id", None)
        metadata.pop("provided_institution_name", None)
        metadata.update(institution)
        # Backward-compatible canonical value; raw spelling remains separately auditable.
        metadata["institution"] = institution["institution_name"]
        metadata["degree_raw"] = metadata.get("degree")
        metadata["document_type_id"] = normalize_document_type(metadata.get("degree"))
        academic_unit = normalize_academic_unit(
            metadata.get("major_source") or metadata.get("school_or_faculty"),
            metadata.get("title"),
        )
        metadata.update(academic_unit)
        metadata["year_bucket"] = year_bucket(metadata.get("year"))
        metadata["metadata_review_flags"] = [
            flag
            for flag, active in (
                ("unmapped_document_type", metadata["document_type_id"] == "other"),
                ("unmapped_domain", metadata["domain_id"] == "other"),
                ("year_conflict", bool(metadata.get("year_conflict"))),
            )
            if active
        ]
        required_missing = [field for field in ("title", "authors", "year", "institution_id", "institution_name") if not metadata.get(field)]
        optional_missing = [field for field in ("advisors", "degree", "school_or_faculty", "keywords", "abstract", "source_url") if not metadata.get(field)]
        metadata["metadata_missing_fields"] = required_missing
        metadata["metadata_missing_required"] = required_missing
        metadata["metadata_missing_optional"] = optional_missing
        metadata["metadata_status"] = "blocking_missing" if required_missing else ("optional_missing" if optional_missing else "complete")
        enriched[doc_id] = metadata
        if required_missing or optional_missing or metadata["metadata_confidence"] < 0.8 or metadata["metadata_review_flags"]:
            review_rows.append({
                "document_id": doc_id,
                "source_path": doc["source_path"],
                "review_priority": "blocking" if required_missing else "optional",
                "missing_required": required_missing,
                "missing_optional": optional_missing,
                "review_flags": metadata["metadata_review_flags"],
                "metadata": metadata,
            })

    prepared: list[dict] = []
    seen_metadata_fingerprints: dict[str, tuple[str, str]] = {}
    paragraphs_by_document = dict(paragraph_groups(base / "canonical" / "paragraphs.jsonl"))
    for doc_id, doc in documents.items():
        paragraphs = paragraphs_by_document.get(doc_id, [])
        metadata = enriched[doc_id]
        selected, cleaning = select_clean_paragraphs(paragraphs)
        clean_text, clean_paragraphs, sentences = build_clean_text(
            doc_id, selected, args.max_tokens)
        group_id = doc["group_id"]
        duplicate_of = doc.get("duplicate_of")
        fingerprint_source = "|".join(
            [
                ascii_key(metadata.get("title") or ""),
                ";".join(sorted(ascii_key(value) for value in metadata.get("authors") or [])),
                str(metadata.get("year") or ""),
            ]
        )
        if all((metadata.get("title"), metadata.get("authors"), metadata.get("year"))):
            metadata_fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
            prior = seen_metadata_fingerprints.get(metadata_fingerprint)
            if prior and not duplicate_of:
                duplicate_of, _ = prior
            elif not prior and not duplicate_of:
                seen_metadata_fingerprints[metadata_fingerprint] = (doc_id, group_id)
        base_eligible = (
            not duplicate_of
            and doc["quality_tier"] in {"gold", "silver"}
            and len(clean_text) >= args.min_clean_chars
            and not metadata["metadata_missing_required"]
        )
        prepared.append(
            {
                "document_id": doc_id,
                "doc": doc,
                "metadata": metadata,
                "group_id": group_id,
                "duplicate_of": duplicate_of,
                "clean_text": clean_text,
                "clean_paragraphs": clean_paragraphs,
                "sentences": sentences,
                "cleaning": cleaning,
                "base_eligible": base_eligible,
            }
        )

    split_assignments = resolve_training_splits(
        prepared, base / "manifest" / "split_manifest.jsonl", args.seed
    )

    (output / "passages").mkdir()
    files = {
        "documents": (output / "documents.jsonl").open("w", encoding="utf-8", newline="\n"),
        "sentences": (output / "sentences.jsonl").open("w", encoding="utf-8", newline="\n"),
        "all": (output / "chunks" / "all.jsonl").open("w", encoding="utf-8", newline="\n"),
        "train": (output / "chunks" / "train.jsonl").open("w", encoding="utf-8", newline="\n"),
        "dev": (output / "chunks" / "dev.jsonl").open("w", encoding="utf-8", newline="\n"),
        "test": (output / "chunks" / "test.jsonl").open("w", encoding="utf-8", newline="\n"),
        "recent": (output / "chunks" / "recent_or_uncertain.jsonl").open("w", encoding="utf-8", newline="\n"),
        "excluded": (output / "chunks" / "excluded.jsonl").open("w", encoding="utf-8", newline="\n"),
        "passages_all": (output / "passages" / "all.jsonl").open("w", encoding="utf-8", newline="\n"),
        "passages_train": (output / "passages" / "train.jsonl").open("w", encoding="utf-8", newline="\n"),
        "passages_dev": (output / "passages" / "dev.jsonl").open("w", encoding="utf-8", newline="\n"),
        "passages_test": (output / "passages" / "test.jsonl").open("w", encoding="utf-8", newline="\n"),
        "passages_recent": (output / "passages" / "recent_or_uncertain.jsonl").open("w", encoding="utf-8", newline="\n"),
        "passages_excluded": (output / "passages" / "excluded.jsonl").open("w", encoding="utf-8", newline="\n"),
        "split": (output / "split_manifest.jsonl").open("w", encoding="utf-8", newline="\n"),
    }
    stats = Counter()
    provenance_counts = Counter()
    split_counts = Counter()
    institution_counts = Counter()
    domain_counts = Counter()
    document_type_counts = Counter()
    language_counts = Counter()
    year_bucket_counts = Counter()
    seen_eligible_chunk_fingerprints: dict[str, str] = {}
    written_groups: set[str] = set()
    try:
        for index, prepared_row in enumerate(prepared, 1):
            doc_id = prepared_row["document_id"]
            doc = prepared_row["doc"]
            metadata = prepared_row["metadata"]
            clean_text = prepared_row["clean_text"]
            clean_paragraphs = prepared_row["clean_paragraphs"]
            sentences = prepared_row["sentences"]
            cleaning = prepared_row["cleaning"]
            group_id = prepared_row["group_id"]
            split = split_assignments.get(group_id, "excluded")
            provenance = provenance_status(metadata.get("year"))
            provenance_counts[provenance] += 1
            institution_counts[metadata["institution_id"]] += 1
            domain_counts[metadata["domain_id"]] += 1
            document_type_counts[metadata["document_type_id"]] += 1
            year_bucket_counts[metadata["year_bucket"]] += 1
            document_eligible = prepared_row["base_eligible"] and split in {"train", "dev", "test"}
            core_human = document_eligible and provenance == "high_confidence_human"
            document_row = {
                "schema_version": SCHEMA_VERSION,
                "document_id": doc_id,
                "group_id": group_id,
                "source_path": doc["source_path"],
                "source_sha256": doc["source_sha256"],
                **metadata,
                "language": detect_language(clean_text),
                "language_detected": detect_language(clean_text),
                "label": "H",
                "provenance_status": provenance,
                "quality_tier": doc["quality_tier"],
                "split": split,
                "duplicate_of": prepared_row["duplicate_of"],
                "training_eligible": document_eligible,
                "core_human_eligible": core_human,
                "page_count": doc["page_count"],
                "clean_paragraph_count": len(clean_paragraphs),
                "clean_sentence_count": len(sentences),
                "clean_character_count": len(clean_text),
                "cleaning": cleaning,
                "text": clean_text,
            }
            language_counts[document_row["language_detected"]] += 1
            dump_line(files["documents"], document_row)
            if group_id not in written_groups:
                dump_line(files["split"], {"document_id": doc_id, "group_id": group_id,
                                           "split": split, "provenance_status": provenance})
                written_groups.add(group_id)
            for sentence in sentences:
                sentence["schema_version"] = SCHEMA_VERSION
                sentence["group_id"] = group_id
                sentence["split"] = split
                sentence["provenance_status"] = provenance
                dump_line(files["sentences"], sentence)
            passages = make_passages(sentences, args.target_tokens, args.min_tokens,
                                     args.max_tokens, clean_text)
            sentence_by_id = {row["sentence_id"]: row for row in sentences}
            for ordinal, passage in enumerate(passages):
                token_count = passage["approx_tokens"]
                valid_length = args.min_tokens <= token_count <= args.max_tokens
                valid_short_tail = passage.get("short_tail") and token_count < args.min_tokens
                if not document_eligible or not (valid_length or valid_short_tail):
                    subset = "excluded"
                    exclusion_reason = "document_or_token_quality"
                elif provenance != "high_confidence_human":
                    subset = "recent"
                    exclusion_reason = None
                else:
                    subset = split
                    exclusion_reason = None
                passage_sentences = [sentence_by_id[sentence_id] for sentence_id in passage["sentence_ids"]]
                first_sentence = passage_sentences[0] if passage_sentences else None
                last_sentence = passage_sentences[-1] if passage_sentences else None
                chunk_id = stable_id("chunk", doc_id, ordinal, passage["text"])
                passage_id = stable_id("passage", doc_id, ordinal, passage["text"])
                duplicate_of = None
                if subset in {"train", "dev", "test", "recent"}:
                    fingerprint = hashlib.sha256(" ".join(passage["text"].casefold().split()).encode("utf-8")).hexdigest()
                    duplicate_of = seen_eligible_chunk_fingerprints.get(fingerprint)
                    if duplicate_of:
                        subset = "excluded"
                        exclusion_reason = "exact_chunk_duplicate"
                        stats["duplicate_chunks_excluded"] += 1
                    else:
                        seen_eligible_chunk_fingerprints[fingerprint] = chunk_id
                row = {
                    "schema_version": SCHEMA_VERSION,
                    "chunk_id": chunk_id,
                    "passage_id": passage_id,
                    "document_id": doc_id,
                    "group_id": group_id,
                    "chunk_index": ordinal,
                    "split": split,
                    "subset": "core_human" if subset in {"train", "dev", "test"} else ("recent_or_uncertain" if subset == "recent" else "excluded"),
                    "label": "H",
                    "provenance_status": provenance,
                    "institution_id": metadata["institution_id"],
                    "institution_name": metadata["institution_name"],
                    "section_ids": passage["section_ids"],
                    "paragraph_ids": passage["paragraph_ids"],
                    "sentence_ids": passage["sentence_ids"],
                    "unit_type": passage["unit_type"],
                    "unit_types": passage["unit_types"],
                    "normalization_status": "active",
                    "review_flags": list(dict.fromkeys(flag for sentence in passage_sentences
                                                        for flag in sentence.get("review_flags", []))),
                    "source_spans": passage["source_spans"],
                    "page_start": passage["page_start"],
                    "page_end": passage["page_end"],
                    "document_char_start": first_sentence["char_start"] if first_sentence else None,
                    "document_char_end": last_sentence["char_end"] if last_sentence else None,
                    "start_char": passage["start_char"],
                    "end_char": passage["end_char"],
                    "text_sha256": passage["text_sha256"],
                    "document_char_spans": [
                        [sentence["char_start"], sentence["char_end"]]
                        for sentence in passage_sentences
                    ],
                    "approx_tokens": token_count,
                    "content_sha256": hashlib.sha256(
                        " ".join(passage["text"].casefold().split()).encode("utf-8")
                    ).hexdigest(),
                    "short_tail": bool(passage.get("short_tail")),
                    "exclusion_reason": exclusion_reason,
                    "duplicate_of": duplicate_of,
                    "text": passage["text"],
                }
                dump_line(files["all"], row)
                dump_line(files[subset], row)
                dump_line(files["passages_all"], row)
                dump_line(files[f"passages_{subset}"], row)
                stats["chunks"] += 1
                stats[f"chunks_{subset}"] += 1
            stats["documents"] += 1
            stats["clean_characters"] += len(clean_text)
            stats["clean_sentences"] += len(sentences)
            stats["clean_paragraphs"] += len(clean_paragraphs)
            if document_eligible:
                stats["eligible_documents"] += 1
            if core_human:
                stats["core_human_documents"] += 1
            split_counts[split] += 1
            if index % 50 == 0:
                print(f"[{index}/{len(documents)}] documents materialized", flush=True)
    finally:
        for handle in files.values():
            handle.close()

    with (output / "review" / "metadata_review_queue.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for row in review_rows:
            dump_line(handle, row)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "base_dataset": str(base),
        "repository_metadata_records": len(metadata_records),
        "official_metadata_matches": sum(row["metadata_source"].startswith("official_repository") for row in enriched.values()),
        "metadata_review_documents": len(review_rows),
        "counts": dict(stats),
        "document_splits": dict(split_counts),
        "provenance": dict(provenance_counts),
        "institutions": dict(institution_counts),
        "domains": dict(domain_counts),
        "document_types": dict(document_type_counts),
        "languages": dict(language_counts),
        "year_buckets": dict(year_bucket_counts),
        "split_seed": args.seed,
        "chunk_parameters": {"min": args.min_tokens, "target": args.target_tokens, "max": args.max_tokens},
    }
    with (output / "dataset_info.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    with (output / "reports" / "diversity.json").open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "institutions": dict(institution_counts),
                "year_buckets": dict(year_bucket_counts),
                "domains": dict(domain_counts),
                "document_types": dict(document_type_counts),
                "languages": dict(language_counts),
                "quality_tiers": dict(Counter(row["doc"]["quality_tier"] for row in prepared)),
                "provenance": dict(provenance_counts),
                "splits": dict(split_counts),
            },
            handle,
            ensure_ascii=False,
            indent=2,
        )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--metadata-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cover-pages", type=int, default=8)
    parser.add_argument("--seed", default="gips-dora-human-v27")
    parser.add_argument("--min-clean-chars", type=int, default=5000)
    parser.add_argument("--min-tokens", type=int, default=128)
    parser.add_argument("--target-tokens", type=int, default=384)
    parser.add_argument("--max-tokens", type=int, default=512)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("--output must not exist; training views are immutable builds")
    return args


if __name__ == "__main__":
    print(json.dumps(build(parse_args()), ensure_ascii=False, indent=2))
