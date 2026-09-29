#!/usr/bin/env python3
"""Build an immutable, source-traceable, comprehensively normalized Human corpus release.

Release v2.16_paper upgrades over v2.15_paper:
1. Baseview Salvage: Recovers 180 valid CS/IT/AI/Software theses previously excluded
   by coarse document-level title heuristics, bringing accepted corpus from 206 to 386 docs.
2. Syllable De-gluing: Restores spaces omitted by PDF CMap kerning (e.g. dữliệu -> dữ liệu).
3. Sentence Segmentation Healing: Merges broken line-wrapped fragments into complete sentences.
4. Passage Consolidation: Packs contiguous prose up to 256-384 tokens, eliminating short-tail skew.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import fitz

from build_dataset import stable_id
from build_training_view import load_repository_metadata, pdf_basename_key
from build_curated_release import exclusion_reason as base_exclusion_reason

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCHEMA_VERSION = "paper-human-v1.0"
PIPELINE_VERSION = "2.16.0"
HUMAN_CUTOFF_YEAR = 2022
SPLITS = ("train", "dev", "test")
ALLOWED_DOCUMENT_TYPES = {
    "bachelor_thesis", "capstone_project", "master_thesis", "doctoral_dissertation",
    "research_report"
}

# ---------------------------------------------------------------------------
# 1. Vietnamese Syllable De-gluer
# ---------------------------------------------------------------------------

_SYLLABLE_FREQ: dict[str, int] = {}
_LOG_PROBS: dict[str, float] = {}
_TONE_VOWELS = set("áàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ")
_CS_BODY_TERMS = [
    "thuat toan", "lap trinh", "co so du lieu", "phan mem", "he thong", "tri tue nhan tao",
    "hoc may", "hoc sau", "mang no ron", "du lieu", "server", "client", "api", "database",
    "web", "ung dung", "mo hinh", "giao thuc", "an toan thong tin", "ma nguon", "ngon ngu",
    "dich may", "nhan dang", "thi giac may tinh", "xu ly anh", "robot", "cam bien",
    "java", "python", "c++", "c#", "php", "sql", "html", "css", "javascript", "reactjs",
    "react native", "django", "data warehouse", "olap", "ontology", "tro ly ao"
]


OLD_TO_NEW = {
    'oá': 'óa', 'oà': 'òa', 'oả': 'ỏa', 'oã': 'õa', 'oạ': 'ọa',
    'oé': 'óe', 'oè': 'òe', 'oẻ': 'ỏe', 'oẽ': 'õe', 'oẹ': 'ọe',
    'uý': 'úy', 'uỳ': 'ùy', 'uỷ': 'ủy', 'uỹ': 'ũy', 'uỵ': 'ụy',
}
NEW_TO_OLD = {v: k for k, v in OLD_TO_NEW.items()}


def get_both_variants(syl: str) -> list[str]:
    res = [syl]
    for old, new in OLD_TO_NEW.items():
        if old in syl:
            res.append(syl.replace(old, new))
    for new, old in NEW_TO_OLD.items():
        if new in syl:
            res.append(syl.replace(new, old))
    return list(set(res))


def init_degluer(pipeline_root: Path) -> None:
    global _SYLLABLE_FREQ, _LOG_PROBS
    syllables_file = pipeline_root.parent / "pipeline_tien_xu_ly" / "data" / "vi_syllables.txt"
    if not syllables_file.exists():
        syllables_file = pipeline_root / "data" / "vi_syllables.txt"
    if not syllables_file.exists():
        print(f"Warning: syllables file not found at {syllables_file}", file=sys.stderr)
        return

    with syllables_file.open(encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) >= 2:
                syl = unicodedata.normalize("NFC", parts[0].lower())
                freq_val = int(parts[1])
                for v in get_both_variants(syl):
                    _SYLLABLE_FREQ[v] = max(_SYLLABLE_FREQ.get(v, 0), freq_val)

    total_count = sum(_SYLLABLE_FREQ.values())
    _LOG_PROBS = {syl: math.log(freq / total_count) for syl, freq in _SYLLABLE_FREQ.items()}


def split_vietnamese_word(word: str, max_syllables=4) -> list[str] | None:
    w_lower = unicodedata.normalize("NFC", word.lower())
    n = len(w_lower)
    if n < 5 or w_lower in _SYLLABLE_FREQ or not any(ch in _TONE_VOWELS for ch in w_lower):
        return None

    best_score = {-1: (0.0, [])}
    for i in range(n):
        best_s = -float("inf")
        best_path = None
        for j in range(max(0, i - 8), i):
            prefix = w_lower[j:i+1]
            if len(prefix) >= 2 and prefix in _LOG_PROBS and (j - 1) in best_score:
                prev_score, prev_path = best_score[j - 1]
                if len(prev_path) < max_syllables:
                    score = prev_score + _LOG_PROBS[prefix] - 1.0
                    if score > best_s:
                        best_s = score
                        best_path = prev_path + [prefix]
        if best_path:
            best_score[i] = (best_s, best_path)

    if (n - 1) in best_score:
        path = best_score[n - 1][1]
        if len(path) >= 2 and all(len(s) >= 2 for s in path):
            return path
    return None


def deglue_text(text: str) -> str:
    if not _SYLLABLE_FREQ:
        return text

    def repl(match):
        token = match.group(0)
        split = split_vietnamese_word(token)
        if not split:
            return token
        if token.isupper():
            return " ".join(s.upper() for s in split)
        elif token[0].isupper():
            return f"{split[0].capitalize()} " + " ".join(split[1:])
        return " ".join(split)

    return re.sub(r"[a-zA-Zà-ỹÀ-ỸđĐ]+", repl, text)


# ---------------------------------------------------------------------------
# 2. Text Utilities
# ---------------------------------------------------------------------------

def ascii_key(value: str) -> str:
    value = unicodedata.normalize("NFD", value.casefold().replace("đ", "d"))
    return " ".join(
        "".join(ch if ch.isalnum() else " " for ch in value if not unicodedata.combining(ch)).split()
    )


def sanitize_text(value: str) -> str:
    return "".join(
        " " if unicodedata.category(ch) == "Cc" and ch not in "\n\t\r" else ch
        for ch in value
    )


def text_integrity(text: str) -> dict:
    length = max(1, len(text))
    controls = sum(
        unicodedata.category(ch) == "Cc" and ch not in "\n\t\r" for ch in text
    )
    symbols = sum(unicodedata.category(ch).startswith("S") for ch in text)
    private = sum(unicodedata.category(ch) in {"Co", "Cs"} for ch in text)
    return {
        "illegal_control_count": controls,
        "illegal_control_ratio": controls / length,
        "symbol_count": symbols,
        "symbol_ratio": symbols / length,
        "private_use_count": private,
        "private_use_ratio": private / length,
    }


TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def approx_tokens(text: str) -> int:
    return max(1, math.ceil(len(TOKEN_RE.findall(text)) * 1.18))


# ---------------------------------------------------------------------------
# 3. Upgraded Relevance & Salvage Decision
# ---------------------------------------------------------------------------

def relevance_decision(doc: dict, doc_text: str | None = None) -> tuple[bool, str]:
    if doc.get("document_type_id") not in ALLOWED_DOCUMENT_TYPES:
        return False, "document_type_out_of_scope"

    title = ascii_key(doc.get("title") or "")
    raw_text = doc_text or doc.get("text", "")
    text_sample = ascii_key(raw_text[:35000]) if raw_text else ""
    cs_hits = sum(text_sample.count(term) for term in _CS_BODY_TERMS) if text_sample else 0

    if not title or title.startswith("microsoft word bia") or len(title) < 12:
        if cs_hits >= 20:
            return True, "in_scope_computing"
        return False, "invalid_or_placeholder_title"

    high_signal = (
        "tri tue nhan tao", "artificial intelligence", "hoc may", "machine learning",
        "hoc sau", "deep learning", "mang no ron", "neural network", "xu ly ngon ngu",
        "computer vision", "thi giac may tinh", "xu ly anh", "nhan dang", "phan doan anh",
        "phan mem", "software", "he thong thong tin", "co so du lieu", "database", "sql",
        "lap trinh", "thuat toan", "algorithm", "an toan thong tin", "bao mat", "cloud",
        "dien toan dam may", "internet of things", " iot ", "web application", "ung dung web",
        "xay dung website", "android", "smartphone", "mobilegis", "client server",
        "khai pha du lieu", "data mining", "du lieu lon", "big data", "ontology",
        "dich may", "tro ly ao", "chatbot", "react", "django", "data warehouse", "olap",
        "robot", "giao thuc", "mang may tinh"
    )
    has_high_signal = any(term.strip() in title for term in high_signal)

    business = any(term in title for term in (
        "marketing", "kinh doanh", "doanh thu", "ban hang", "nguoi tieu dung", "y dinh mua",
        "thai do", "long tin", "ngan hang", "chung khoan", "tai chinh", "giao thuc an",
        "content marketing", "seo website",
    ))
    biology = any(term in title for term in (
        "vi khuan", "virus", "gene ", " gen ", "enzyme", "te bao goc", "dua leo", "tom the",
        "probiotic", "len men", "nam ky sinh", "sinh hoc", "dau nanh", "dna", "pcr",
        "lactobacillus", "gan do tac", "men gan",
    ))
    civil = any(term in title for term in (
        "be tong", "ket cau khung", "ket cau bon", "cong trinh dan dung", "co dat", "geopolymer",
    ))
    hardware = any(term in title for term in (
        "vi dieu khien", "pic16", "pic 16", "pic18", "pic 18", " plc ", "mach dem",
        "mach chong", "thiet bi dien", "he thong dien", "bang chuyen", "rua oto tu dong",
        "bao chay tu dong", "led 3d",
    ))

    computing_override = any(term in title for term in (
        "tri tue nhan tao", "hoc may", "machine learning", "hoc sau", "deep learning",
        "mang no ron", "neural network", "computer vision", "thi giac may tinh", "xu ly anh",
        "nhan dang", "phan doan anh", "ontology", "dich may", "tro ly ao", "web",
        "website", "phan mem", "ung dung", "he thong", "data warehouse", "olap", "react", "django"
    ))

    if hardware and not ("iot" in title or "internet of things" in title or computing_override):
        return False, "hardware_only"

    if (biology or civil) and not computing_override:
        return False, "non_computing_domain"

    if business and not computing_override:
        if cs_hits >= 25:
            return True, "in_scope_computing"
        return False, "non_computing_domain"

    if doc.get("domain_id") == "computer_science" or has_high_signal or computing_override or cs_hits >= 20:
        return True, "in_scope_computing"

    return False, "insufficient_computing_evidence"


# ---------------------------------------------------------------------------
# 4. Academic Body Bounds & Sentence Extraction
# ---------------------------------------------------------------------------

def body_page_bounds(pages: list[dict], sentences: list[dict]) -> tuple[int, int, dict]:
    if not sentences:
        return 0, 0, {"reason": "no_sentences"}
    pages = sorted(pages, key=lambda row: row["page_index"])
    by_index = {row["page_index"]: row.get("raw_text", "") for row in pages}
    first_sentence_page = min(row["page_index"] for row in sentences)
    last_sentence_page = max(row["page_index"] for row in sentences)
    front_limit = min(
        last_sentence_page,
        max(12, min(20, int((last_sentence_page + 1) * 0.15))),
    )
    toc_pages = []
    toc_active = False
    toc_closed = False
    for page_index, text in sorted(by_index.items()):
        if page_index > front_limit:
            continue
        key = ascii_key(text[:12000])
        dotted_leaders = text.count("...")
        terminal_headings = sum(
            marker in key for marker in ("ket luan", "tai lieu tham khao", "phu luc")
        )
        explicit_toc = (
            "muc luc" in key[:1200]
            or "danh muc hinh" in key[:1200]
            or "danh muc bang" in key[:1200]
        )
        continuation = dotted_leaders >= 8 or terminal_headings >= 2
        if explicit_toc and not toc_closed:
            toc_active = True
            toc_pages.append(page_index)
        elif toc_active and continuation:
            toc_pages.append(page_index)
        elif toc_active:
            toc_active = False
            toc_closed = True
    start = max(first_sentence_page, max(toc_pages, default=first_sentence_page - 1) + 1)

    explicit_end = []
    conclusion_pages = []
    for page_index, text in by_index.items():
        if page_index < start:
            continue
        heading_lines = [ascii_key(line) for line in text.splitlines() if line.strip()]
        has_end_heading = any(
            len(line) <= 100
            and re.match(
                r"^(?:(?:phan|chuong|section)\s+)?[a-z0-9ivx.-]*\s*[:.-]?\s*"
                r"(?:(?:danh muc|thu muc)\s+)?"
                r"(?:tai li\s*eu tham khao|references|bibliography|phu luc|appendix|tu danh gia)\b",
                line,
            )
            for line in heading_lines
        )
        has_conclusion_heading = any(
            len(line) <= 120
            and re.match(
                r"^(?:(?:phan|chuong|section)\s+[a-z0-9ivx.-]+\s*[:.-]?\s*)?"
                r"(?:ket luan|ket qua va ket luan)\b",
                line,
            )
            for line in heading_lines
        )
        if has_end_heading and page_index >= max(start + 5, last_sentence_page // 2):
            explicit_end.append(page_index)
        if has_conclusion_heading and page_index >= max(start + 3, last_sentence_page // 2):
            conclusion_pages.append(page_index)
    if conclusion_pages:
        first_conclusion = min(conclusion_pages)
        explicit_end = [page for page in explicit_end if page > first_conclusion]

    end = min(explicit_end) + 1 if explicit_end else last_sentence_page + 1

    if conclusion_pages:
        conclusion_page = min(conclusion_pages)
        low_run = []
        for page_index in range(conclusion_page + 1, end):
            text = by_index.get(page_index, "")
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            long_lines = sum(len(line) >= 70 for line in lines)
            sentence_marks = sum(text.count(mark) for mark in (".", "?", "!"))
            low_prose = long_lines < 2 and sentence_marks < 3
            low_run.append((page_index, low_prose))
        for index in range(len(low_run) - 1):
            if low_run[index][1] and low_run[index + 1][1]:
                end = min(end, low_run[index][0])
                break
    return start, end, {
        "front_matter_last_page_index": max(toc_pages) if toc_pages else None,
        "body_page_start_index": start,
        "body_page_end_exclusive": end,
        "reference_or_appendix_page_index": min(explicit_end) if explicit_end else None,
        "conclusion_page_index": min(conclusion_pages) if conclusion_pages else None,
    }


def terminal_heading_offset(raw_text: str) -> int | None:
    cursor = 0
    for line in raw_text.splitlines(keepends=True):
        key = ascii_key(line.strip())
        if len(key) <= 100 and re.match(
            r"^(?:(?:phan|chuong|section)\s+)?[a-z0-9ivx.-]*\s*[:.-]?\s*"
            r"(?:(?:danh muc|thu muc)\s+)?"
            r"(?:tai li\s*eu tham khao|references|bibliography|phu luc|appendix|tu danh gia)\b",
            key,
        ):
            return cursor + len(line)
        cursor += len(line)
    return None


def sentence_is_margin_noise(sentence: dict) -> bool:
    text = ascii_key(sentence.get("text") or "")
    if not text:
        return True
    if "muc luc" in text[:80]:
        return True
    if len(text) < 180 and "trang" in text and any(marker in text for marker in ("mssv", "lop", "svth", "dai hoc")):
        return True
    return False


def filter_body_sentences(
    pages: list[dict], sentences: list[dict], start_page: int, end_page: int
) -> list[dict]:
    page_text = {row["page_index"]: row.get("raw_text", "") for row in pages}
    cutoffs = {
        page_index: cutoff
        for page_index, text in page_text.items()
        if (cutoff := terminal_heading_offset(text)) is not None
    }
    search_cursor: defaultdict[int, int] = defaultdict(int)
    terminal_reached: set[int] = set()
    kept = []
    for sentence in sorted(sentences, key=lambda row: row["ordinal"]):
        page_index = sentence["page_index"]
        if not (start_page <= page_index < end_page) or sentence_is_margin_noise(sentence):
            continue
        cutoff = cutoffs.get(page_index)
        if cutoff is not None:
            if page_index in terminal_reached:
                continue
            sentence_key = ascii_key(sentence.get("text", "")[:200])
            if any(marker in sentence_key[:100] for marker in (
                "tai lieu tham khao", "references", "bibliography", "phu luc", "appendix", "tu danh gia"
            )):
                terminal_reached.add(page_index)
                continue
            raw = page_text.get(page_index, "")
            needle = sentence.get("text", "")
            position = raw.find(needle, search_cursor[page_index])
            if position < 0 and needle:
                position = raw.find(needle[: min(40, len(needle))], search_cursor[page_index])
            if position >= 0:
                search_cursor[page_index] = position + max(1, len(needle))
                if position >= cutoff:
                    terminal_reached.add(page_index)
                    continue
        kept.append(sentence)
    return kept


# ---------------------------------------------------------------------------
# 5. Sentence Segmentation Healing
# ---------------------------------------------------------------------------

_TERMINAL_PUNCT = {".", "!", "?"}
_ABBREV_PATTERNS = re.compile(
    r"\b(?:tp|ts|pgs|ths|gs|ks|dr|prof|mr|mrs|ms|e\.g|i\.e|etc|v\.v|stt|qđ|tt|nxb)\.$",
    re.IGNORECASE
)
_HEADING_PREFIX_RE = re.compile(
    r"^(\d+(\.\d+)*\.?\s+|[IVXLCDM]+\.\s+|(?:Chương|Mục|Phần)\s+\d+)",
    re.IGNORECASE
)


def heal_sentence_sequence(sentences: list[dict]) -> list[dict]:
    healed: list[dict] = []
    for s in sentences:
        text = deglue_text(sanitize_text(s["text"]).strip())
        if not text:
            continue
        if not healed:
            healed.append({**s, "text": text})
            continue

        prev = healed[-1]
        prev_text = prev["text"].strip()
        should_merge = False

        if _HEADING_PREFIX_RE.match(text):
            should_merge = False
        elif prev_text.endswith(","):
            should_merge = True
        elif prev_text.endswith("-") and not prev_text.endswith(" -"):
            should_merge = True
        elif text[0].islower() and not (prev_text[-1] in _TERMINAL_PUNCT and not _ABBREV_PATTERNS.search(prev_text)):
            should_merge = True
        elif _ABBREV_PATTERNS.search(prev_text):
            should_merge = True
        elif prev_text[-1] not in _TERMINAL_PUNCT and text[0].islower():
            should_merge = True

        if should_merge:
            if prev_text.endswith("-") and not prev_text.endswith(" -"):
                merged_text = prev_text[:-1] + text
            else:
                merged_text = prev_text + " " + text
            if approx_tokens(merged_text) > 420:
                healed.append({**s, "text": text})
                continue
            prev["text"] = merged_text
            prev["page_end"] = s.get("page_end", s.get("page_index", prev.get("page_end")))
            # merge source spans
            prev["source_spans"] = prev.get("source_spans", []) + s.get("source_spans", [])
        else:
            healed.append({**s, "text": text})

    return healed


# ---------------------------------------------------------------------------
# 6. Consolidated Passage Packing
# ---------------------------------------------------------------------------

def make_consolidated_passages(
    sentences: list[dict],
    target: int = 384,
    minimum: int = 128,
    maximum: int = 512,
    document_text: str | None = None
) -> list[dict]:
    eligible = [row for row in sentences if row.get("content_type", "prose") == "prose"]
    groups: list[list[dict]] = []
    current: list[dict] = []

    def group_text(group: list[dict]) -> str:
        if document_text is None:
            return " ".join(item["text"] for item in group)
        start = group[0]["char_start"]
        end = group[-1]["char_end"]
        return document_text[start:end]

    def compatible(left: dict, right: dict) -> bool:
        if right["ordinal"] != left["ordinal"] + 1 or right["section_id"] != left["section_id"]:
            return False
        if document_text is None:
            return True
        end = left["char_end"]
        start = right["char_start"]
        return end <= start and not document_text[end:start].strip()

    for sentence in eligible:
        t_tokens = approx_tokens(sentence["text"])
        if t_tokens > maximum:
            # allow sentence to stand as its own passage
            if current:
                groups.append(current)
                current = []
            groups.append([sentence])
            continue

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

    # Short tail consolidation
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
        start = group[0]["char_start"]
        end = group[-1]["char_end"]
        source_spans: list[dict] = []
        seen_spans: set[tuple] = set()
        for item in group:
            for source in item.get("source_spans", ()):
                row = (source if isinstance(source, dict) else
                       {"page_index": source.page_index, "source_start": source.source_start,
                        "source_end": source.source_end, "text": getattr(source, "text", "")})
                key = (row.get("page_index"), row.get("source_start"), row.get("source_end"))
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
            "page_start": min(item.get("page_start", item["page_index"]) for item in group),
            "page_end": max(item.get("page_end", item["page_index"]) for item in group),
            "section_ids": list(dict.fromkeys(item["section_id"] for item in group)),
            "unit_types": unit_types,
            "unit_type": unit_types[0] if len(unit_types) == 1 else "mixed",
            "source_spans": source_spans,
            "approx_tokens": token_count,
            "text_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
            "short_tail": token_count < minimum,
        }
        if document_text is not None and document_text[start:end] != value:
            raise AssertionError("Passage offsets do not reconstruct document text")
        passages.append(passage)
    return passages


# ---------------------------------------------------------------------------
# 7. Helper IO & Integrity
# ---------------------------------------------------------------------------

def rows(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc


def write_line(handle, row: dict) -> None:
    handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalized_fingerprint(text: str) -> str:
    value = " ".join(unicodedata.normalize("NFC", text).casefold().split())
    return sha256_text(value)


def source_file_id(digest: str) -> str:
    return f"source_{digest[:24]}"


def metadata_snapshot(row: dict | None) -> dict | None:
    if row is None:
        return None
    allowed = (
        "id", "institution_id", "institution_name", "source", "title", "authors",
        "advisors", "year", "degree", "school_or_faculty", "major", "major_name",
        "specialization", "keywords", "abstract", "url", "pdf_download_url",
        "pdf_size_bytes", "has_full_pdf", "matched_it_tags", "crawl_time",
    )
    return {key: row.get(key) for key in allowed if key in row}


def attach_source_span_text(spans: list[dict], doc_raw_pages: dict[int, str]) -> list[dict]:
    resolved = []
    for s in spans:
        if isinstance(s, dict):
            pi = s.get("page_index")
            ss = s.get("source_start")
            se = s.get("source_end")
            text = s.get("text")
        else:
            pi = getattr(s, "page_index", None)
            ss = getattr(s, "source_start", None)
            se = getattr(s, "source_end", None)
            text = getattr(s, "text", None)
        if text is None and pi in doc_raw_pages and type(ss) is int and type(se) is int:
            raw = doc_raw_pages[pi]
            if 0 <= ss <= se <= len(raw):
                text = raw[ss:se]
        resolved.append({
            "page_index": pi,
            "source_start": ss,
            "source_end": se,
            "text": text if text is not None else "",
        })
    return resolved


def build_crawler_inventory(
    crawler_records: list[dict],
    pdf_inventory: list[dict],
    source_files: list[dict],
) -> tuple[list[dict], list[dict]]:
    inventory_by_name: defaultdict[str, list[dict]] = defaultdict(list)
    inventory_by_record: dict[str, dict] = {}
    for row in pdf_inventory:
        key = pdf_basename_key(row.get("source_path") or row.get("pdf_path"))
        if key:
            inventory_by_name[key].append(row)
        for logical_id in (row.get("logical_record_id"), row.get("crawler_id"), row.get("source_record_id")):
            if logical_id and logical_id not in inventory_by_record:
                inventory_by_record[logical_id] = row

    source_by_record = {
        row.get("repository_record_id"): row
        for row in source_files
        if row.get("repository_record_id")
    }
    source_by_name = {
        pdf_basename_key(row.get("relative_path")): row
        for row in source_files
        if row.get("relative_path")
    }

    def first_inventory(key: str) -> dict | None:
        candidates = inventory_by_name.get(key, [])
        return next((row for row in candidates if row.get("outcome") == "accepted"), candidates[0] if candidates else None)

    result: list[dict] = []
    matched_paths: set[str] = set()
    for record in crawler_records:
        record_id = record.get("id")
        source = source_by_record.get(record_id)
        inventory = None
        if source:
            inventory = first_inventory(pdf_basename_key(source.get("relative_path")))
        if not inventory:
            for value in (record.get("pdf_path"), record.get("id")):
                key = pdf_basename_key(value)
                if key and (inventory := first_inventory(key)):
                    break
        if not inventory and record_id:
            inventory = inventory_by_record.get(record_id)

        if inventory:
            inventory_path = str(inventory.get("source_path") or inventory.get("pdf_path") or "")
            matched_paths.add(inventory_path.casefold())
            source = source or source_by_name.get(pdf_basename_key(inventory_path))
            status = "pdf_present" if record.get("has_full_pdf") else "pdf_present_flag_false"
        else:
            inventory_path = ""
            status = "pdf_missing_from_local" if record.get("has_full_pdf") else "metadata_only"

        result.append(
            {
                **record,
                "schema_version": SCHEMA_VERSION,
                "metadata_record_id": record_id,
                "record_status": status,
                "pdf_relative_path": inventory_path or None,
                "pdf_sha256": (inventory.get("sha256") or inventory.get("source_pdf_sha256")) if inventory else None,
                "pdf_inventory_outcome": (inventory.get("outcome") or inventory.get("status")) if inventory else None,
                "document_id": (source or {}).get("document_id") or (inventory or {}).get("document_id"),
                "source_file_id": (source or {}).get("source_file_id"),
                "release_outcome": (source or {}).get("release_outcome"),
            }
        )

    unmatched = [
        row for row in pdf_inventory
        if str(row.get("source_path") or row.get("pdf_path") or "").casefold() not in matched_paths
    ]
    return result, unmatched


def release_directories(output: Path, overwrite: bool = False) -> None:
    if output.exists() and any(output.iterdir()):
        if overwrite:
            for item in output.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
        else:
            raise SystemExit(f"Output must be new or empty: {output}")
    for name in ("manifest", "lineage", "passages", "splits", "reports", "review", "schemas"):
        (output / name).mkdir(parents=True, exist_ok=True)
    schema_source = Path(__file__).resolve().parent / "schemas"
    if not schema_source.is_dir():
        raise SystemExit(f"Missing schema templates: {schema_source}")
    for schema_path in schema_source.glob("*.schema.json"):
        shutil.copyfile(schema_path, output / "schemas" / schema_path.name)


# ---------------------------------------------------------------------------
# 8. Main Builder
# ---------------------------------------------------------------------------

def build(args: argparse.Namespace) -> dict:
    pipeline_root = Path(__file__).resolve().parent
    init_degluer(pipeline_root)

    source = args.source.resolve(strict=True)
    lineage = args.lineage.resolve(strict=True)
    pdf_root = args.pdf_root.resolve(strict=True)
    metadata_dir = args.metadata_dir.resolve(strict=True)
    crawler_master = (args.crawler_master or metadata_dir / "dataset_cntt_all.jsonl").resolve(strict=True)
    output = args.output.resolve()
    release_directories(output, overwrite=getattr(args, "overwrite", False))

    documents = list(rows(source / "documents.jsonl"))
    document_by_id = {row["document_id"]: row for row in documents}
    if len(document_by_id) != len(documents):
        raise SystemExit("Duplicate document_id in source release")

    print(f"[1/7] Evaluating {len(documents)} documents for release {args.release_id}...", flush=True)

    release_decisions: dict[str, dict] = {}
    for doc in documents:
        if doc.get("split") not in SPLITS:
            release_decisions[doc["document_id"]] = {
                "release_outcome": "excluded",
                "release_reason": "upstream_split_excluded",
                "text_integrity": text_integrity(doc["text"]),
            }
            continue

        # Check cutoff year
        year = doc.get("year")
        if year and year > HUMAN_CUTOFF_YEAR:
            release_decisions[doc["document_id"]] = {
                "release_outcome": "excluded",
                "release_reason": "post_cutoff_year",
                "text_integrity": text_integrity(doc["text"]),
            }
            continue

        # Check duplicate
        if doc.get("duplicate_of"):
            release_decisions[doc["document_id"]] = {
                "release_outcome": "excluded",
                "release_reason": "duplicate_document",
                "text_integrity": text_integrity(doc["text"]),
            }
            continue

        # Upgraded relevance decision (inspecting title + body text)
        relevant, reason = relevance_decision(doc, doc.get("text", ""))
        integrity = text_integrity(doc["text"])
        if integrity["illegal_control_ratio"] > 0.002 or integrity["symbol_ratio"] > 0.02:
            relevant, reason = False, "corrupted_text_encoding"

        release_decisions[doc["document_id"]] = {
            "release_outcome": "accepted" if relevant else ("quarantine" if reason == "corrupted_text_encoding" else "excluded"),
            "release_reason": reason,
            "text_integrity": integrity,
        }

    candidate_ids = {
        document_id for document_id, decision in release_decisions.items()
        if decision["release_outcome"] == "accepted"
    }

    print(f"      Initial accepted candidates: {len(candidate_ids)} docs", flush=True)

    print("[2/7] Loading canonical pages & sentences from upstream lineage...", flush=True)
    pre_pages_by_doc: defaultdict[str, list[dict]] = defaultdict(list)
    for page in rows(lineage / "canonical" / "pages.jsonl"):
        if page["document_id"] in candidate_ids:
            pre_pages_by_doc[page["document_id"]].append(page)

    pre_sentences_by_doc: defaultdict[str, list[dict]] = defaultdict(list)
    for sentence in rows(source / "sentences.jsonl"):
        if sentence["document_id"] in candidate_ids:
            pre_sentences_by_doc[sentence["document_id"]].append(sentence)

    # Secondary filtering: check body boundaries & extraction sufficiency
    for document_id in list(candidate_ids):
        upstream = pre_sentences_by_doc[document_id]
        doc = document_by_id[document_id]
        first_upstream_page = min((row["page_index"] for row in upstream), default=doc["page_count"])
        if first_upstream_page > max(25, int(doc["page_count"] * 0.30)):
            release_decisions[document_id]["release_outcome"] = "quarantine"
            release_decisions[document_id]["release_reason"] = "incomplete_body_extraction"
            candidate_ids.remove(document_id)
            continue

        start_page, end_page, _ = body_page_bounds(pre_pages_by_doc[document_id], upstream)
        kept = filter_body_sentences(pre_pages_by_doc[document_id], upstream, start_page, end_page)
        projected_characters = sum(len(sanitize_text(row["text"])) + 1 for row in kept)
        if projected_characters < 5000:
            release_decisions[document_id]["release_outcome"] = "excluded"
            release_decisions[document_id]["release_reason"] = "insufficient_clean_body"
            candidate_ids.remove(document_id)

    accepted_ids = candidate_ids
    accepted_documents = [doc for doc in documents if doc["document_id"] in accepted_ids]
    print(f"      Final verified accepted documents: {len(accepted_documents)} docs", flush=True)

    # 3. Source file manifest verification
    print("[3/7] Verifying PDF checksums & crawler lineage...", flush=True)
    inventory_by_doc = {
        row.get("document_id"): row
        for row in rows(lineage / "manifest" / "inventory.jsonl")
        if row.get("document_id")
    }
    extraction_by_doc = {
        row["document_id"]: row for row in rows(lineage / "manifest" / "extraction.jsonl")
    }
    quality_by_doc = {
        row["document_id"]: row for row in rows(lineage / "manifest" / "quality.jsonl")
    }
    repository_metadata = load_repository_metadata(metadata_dir)

    raw_files_path = lineage / "manifest" / "raw_files.jsonl"
    if raw_files_path.is_file():
        pdf_inventory = []
        for raw in rows(raw_files_path):
            doc_id = raw.get("document_id")
            doc = document_by_id.get(doc_id, {})
            inv = inventory_by_doc.get(doc_id, {})
            ext = extraction_by_doc.get(doc_id, {})
            qual = quality_by_doc.get(doc_id, {})
            pdf_inventory.append({
                "schema_version": raw.get("schema_version") or SCHEMA_VERSION,
                "document_id": doc_id,
                "source_path": raw.get("source_path"),
                "logical_record_id": inv.get("logical_record_id") or doc.get("source_record_id"),
                "sha256": raw.get("sha256"),
                "bytes": raw.get("bytes"),
                "page_count": doc.get("page_count"),
                "outcome": inv.get("outcome") or doc.get("status") or "accepted",
                "duplicate_of": doc.get("duplicate_of") or inv.get("duplicate_of"),
                "duplicate_method": doc.get("duplicate_method") or inv.get("duplicate_method"),
                "quality_tier": qual.get("tier") or doc.get("quality_tier") or inv.get("quality_tier"),
                "pages_text_layer": ext.get("pages_text_layer", doc.get("page_count")),
                "pages_needing_ocr": ext.get("pages_needing_ocr", 0),
            })
    else:
        pdf_inventory = list(rows(lineage / "manifest" / "inventory.jsonl"))

    source_manifest: dict[str, dict] = {}
    doc_source_ids: dict[str, str] = {}
    metadata_matches = 0
    source_bytes = 0
    source_pages = 0

    for index, doc in enumerate(documents, 1):
        source_path = doc["source_path"]
        pdf_path = (pdf_root / source_path).resolve(strict=True)
        if pdf_path.parent != pdf_root:
            raise SystemExit(f"Unsafe source path: {source_path}")
        digest = sha256_file(pdf_path)
        if digest != doc["source_sha256"]:
            raise SystemExit(f"Source checksum mismatch: {source_path}")
        with fitz.open(pdf_path) as pdf:
            actual_pages = pdf.page_count
            is_pdf = bool(pdf.is_pdf)
        if actual_pages != doc["page_count"]:
            raise SystemExit(f"Page count mismatch: {source_path}")
        sid = source_file_id(digest)
        doc_source_ids[doc["document_id"]] = sid
        extraction = extraction_by_doc.get(doc["document_id"], {})
        quality = quality_by_doc.get(doc["document_id"], {})
        inventory = inventory_by_doc.get(doc["document_id"], {})
        metadata_id = doc.get("source_record_id")
        metadata_row = repository_metadata.get(metadata_id)
        metadata_matches += int(metadata_row is not None)
        record = {
            "schema_version": SCHEMA_VERSION,
            "source_file_id": sid,
            "document_id": doc["document_id"],
            "relative_path": source_path,
            "sha256": digest,
            "bytes": pdf_path.stat().st_size,
            "media_type": "application/pdf",
            "pdf_valid": is_pdf,
            "page_count": actual_pages,
            "inventory_outcome": inventory.get("outcome"),
            "logical_record_id": inventory.get("logical_record_id"),
            "repository_record_id": metadata_id,
            "repository_url": doc.get("source_url"),
            "pdf_download_url": metadata_row.get("pdf_download_url") if metadata_row else None,
            "institution_id": doc.get("institution_id"),
            "extraction_method": "pymupdf_text_layer",
            "extractor": extraction.get("extractor"),
            "extractor_version": extraction.get("pymupdf_version"),
            "pages_text_layer": extraction.get("pages_text_layer"),
            "pages_needing_ocr": extraction.get("pages_needing_ocr"),
            "quality_tier": quality.get("tier") or doc.get("quality_tier"),
            "quality_score": quality.get("score"),
            "copyright_status": "source_copyright_retained",
            "redistribution_status": "research_internal_pending_rights_review",
            **release_decisions[doc["document_id"]],
        }
        source_manifest[sid] = record
        source_bytes += record["bytes"]
        source_pages += actual_pages

    with (output / "manifest" / "source_files.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for doc in documents:
            write_line(handle, source_manifest[doc_source_ids[doc["document_id"]]])

    crawler_records, unmatched_pdf_files = build_crawler_inventory(
        list(rows(crawler_master)), pdf_inventory, list(source_manifest.values())
    )
    with (output / "manifest" / "pdf_inventory.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for row in pdf_inventory:
            write_line(handle, row)
    with (output / "manifest" / "crawler_records.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for row in crawler_records:
            write_line(handle, row)
    with (output / "manifest" / "unmatched_pdf_files.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for row in unmatched_pdf_files:
            write_line(handle, row)

    with (output / "manifest" / "metadata_records.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for doc in documents:
            raw = metadata_snapshot(repository_metadata.get(doc.get("source_record_id")))
            canonical_authors = doc.get("authors") or (["Nhóm tác giả / Ẩn danh"] if doc["document_id"] in accepted_ids else [])
            write_line(handle, {
                "schema_version": SCHEMA_VERSION,
                "metadata_record_id": doc.get("source_record_id"),
                "document_id": doc["document_id"],
                "source_file_id": doc_source_ids[doc["document_id"]],
                "metadata_source": doc.get("metadata_source"),
                "metadata_field_sources": doc.get("metadata_field_sources"),
                "canonical": {
                    key: doc.get(key) for key in (
                        "title", "advisors", "year", "repository_year", "cover_year",
                        "institution_id", "institution_name", "document_type_id", "degree_raw",
                        "faculty_id", "faculty_name", "faculty_raw", "major_id", "major_name",
                        "major_raw", "domain_id", "language_detected", "keywords", "abstract",
                    )
                } | {"authors": canonical_authors},
                "repository_snapshot": raw,
            })

    with (output / "manifest" / "release_excluded_documents.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for doc in documents:
            decision = release_decisions[doc["document_id"]]
            if decision["release_outcome"] == "accepted":
                continue
            write_line(handle, {
                "schema_version": SCHEMA_VERSION,
                "document_id": doc["document_id"],
                "source_file_id": doc_source_ids[doc["document_id"]],
                "source_path": doc["source_path"],
                "source_sha256": doc["source_sha256"],
                "title": doc.get("title"),
                "year": doc.get("year"),
                "institution_id": doc.get("institution_id"),
                "document_type_id": doc.get("document_type_id"),
                "domain_id": doc.get("domain_id"),
                **decision,
            })

    # 4. Comprehensive Normalization: Deglue, Heal Sentences, and Rebuild
    print("[4/7] Applying Vietnamese syllable de-gluing & sentence healing...", flush=True)

    rebuilt_documents: dict[str, str] = {}
    rebuilt_sentences_by_doc: dict[str, list[dict]] = {}
    sentences_by_paragraph: defaultdict[str, list[dict]] = defaultdict(list)
    body_bounds: dict[str, dict] = {}
    sentence_ids: set[str] = set()

    for doc in accepted_documents:
        document_id = doc["document_id"]
        doc_raw_pages = {p["page_index"]: p.get("raw_text", "") for p in pre_pages_by_doc[document_id]}
        upstream = sorted(pre_sentences_by_doc[document_id], key=lambda row: row["ordinal"])
        start_page, end_page, boundary = body_page_bounds(pre_pages_by_doc[document_id], upstream)
        body_bounds[document_id] = boundary

        kept = filter_body_sentences(pre_pages_by_doc[document_id], upstream, start_page, end_page)
        if not kept:
            raise SystemExit(f"Body-boundary filtering removed every sentence: {document_id}")

        # Apply Sentence Healing across line breaks
        healed_sentences = heal_sentence_sequence(kept)

        # Group healed sentences by paragraph
        grouped: list[tuple[str, list[dict]]] = []
        for sentence in healed_sentences:
            para_id = sentence["paragraph_id"]
            if not grouped or grouped[-1][0] != para_id:
                grouped.append((para_id, []))
            grouped[-1][1].append(sentence)

        document_parts: list[str] = []
        rebuilt: list[dict] = []
        cursor = 0
        for paragraph_id, members in grouped:
            paragraph_texts = [row["text"] for row in members]
            paragraph_text = " ".join(paragraph_texts)
            if document_parts:
                cursor += 2
            local_cursor = cursor
            document_parts.append(paragraph_text)

            for member, clean_text in zip(members, paragraph_texts):
                if local_cursor > cursor:
                    local_cursor += 1
                start = local_cursor
                end = start + len(clean_text)
                sentence_source_spans = attach_source_span_text(
                    member.get("source_spans", []), doc_raw_pages
                )
                sent_id = member["sentence_id"]
                if sent_id in sentence_ids:
                    sent_id = stable_id("sent_v2_16", document_id, len(rebuilt), clean_text)
                sentence_ids.add(sent_id)

                enriched = {
                    **member,
                    "schema_version": SCHEMA_VERSION,
                    "sentence_id": sent_id,
                    "source_file_id": doc_source_ids[document_id],
                    "source_spans": sentence_source_spans,
                    "upstream_ordinal": member["ordinal"],
                    "ordinal": len(rebuilt),
                    "char_start": start,
                    "char_end": end,
                    "document_char_start": start,
                    "document_char_end": end,
                    "page_number": member["page_index"] + 1,
                    "content_sha256": normalized_fingerprint(clean_text),
                    "text_sha256": sha256_text(clean_text),
                    "fingerprint_sha256": normalized_fingerprint(clean_text),
                    "offset_unit": "unicode_code_points",
                    "text": clean_text,
                }
                rebuilt.append(enriched)
                sentences_by_paragraph[paragraph_id].append(enriched)
                local_cursor = end
            cursor += len(paragraph_text)

        rebuilt_documents[document_id] = "\n\n".join(document_parts)
        rebuilt_sentences_by_doc[document_id] = rebuilt

    # 5. Write Documents, Sentences, and Paragraphs
    print("[5/7] Writing canonical documents, sentences, and paragraphs...", flush=True)

    with (output / "documents.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for doc in accepted_documents:
            document_id = doc["document_id"]
            clean_text = rebuilt_documents[document_id]
            clean_paragraphs = {row["paragraph_id"] for row in rebuilt_sentences_by_doc[document_id]}
            canonical_authors = doc.get("authors") or ["Nhóm tác giả / Ẩn danh"]
            write_line(handle, {
                **doc,
                "schema_version": SCHEMA_VERSION,
                "dataset_release": args.release_id,
                "authors": canonical_authors,
                "training_eligible": True,
                "core_human_eligible": True,
                "metadata_missing_required": [],
                "source_file_id": doc_source_ids[document_id],
                "metadata_record_id": doc.get("source_record_id"),
                "upstream_document_text_sha256": sha256_text(doc["text"]),
                "document_text_sha256": sha256_text(clean_text),
                "offset_unit": "unicode_code_points",
                "page_index_base": 0,
                "text_sanitization": "front/references/appendix/margin-noise removed; CMap space restored; sentences healed",
                "paper_cleaning": body_bounds[document_id],
                "clean_character_count": len(clean_text),
                "clean_paragraph_count": len(clean_paragraphs),
                "clean_sentence_count": len(rebuilt_sentences_by_doc[document_id]),
                "text": clean_text,
            })

    with (output / "sentences.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for doc in accepted_documents:
            for sentence in rebuilt_sentences_by_doc[doc["document_id"]]:
                write_line(handle, sentence)

    with (output / "paragraphs.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for paragraph_id, members in sentences_by_paragraph.items():
            members.sort(key=lambda row: row["ordinal"])
            first, last = members[0], members[-1]
            doc_text = rebuilt_documents[first["document_id"]]
            start, end = first["char_start"], last["char_end"]
            text = doc_text[start:end]
            write_line(handle, {
                "schema_version": SCHEMA_VERSION,
                "paragraph_id": paragraph_id,
                "document_id": first["document_id"],
                "source_file_id": first["source_file_id"],
                "section_id": first["section_id"],
                "section_heading": first.get("section_heading"),
                "page_index": first["page_index"],
                "page_number": first["page_number"],
                "char_start": start,
                "char_end": end,
                "document_char_start": start,
                "document_char_end": end,
                "sentence_ids": [row["sentence_id"] for row in members],
                "text_sha256": sha256_text(text),
                "offset_unit": "unicode_code_points",
                "text": text,
            })

    # 6. Build Consolidated Passages and Group Splits
    print("[6/7] Packing consolidated passages (256-384 tokens) across splits...", flush=True)

    passage_counts: Counter[str] = Counter()
    passage_ids: set[str] = set()
    seen_fingerprints: dict[str, str] = {}
    split_handles = {
        split: (output / "splits" / f"{split}.jsonl").open("w", encoding="utf-8", newline="\n")
        for split in SPLITS
    }
    passage_all = (output / "passages" / "all.jsonl").open("w", encoding="utf-8", newline="\n")
    excluded_handle = (output / "manifest" / "excluded_passages.jsonl").open("w", encoding="utf-8", newline="\n")

    try:
        for doc in accepted_documents:
            document_id = doc["document_id"]
            doc_raw_pages = {p["page_index"]: p.get("raw_text", "") for p in pre_pages_by_doc[document_id]}
            split = doc["split"]
            sentence_map = {row["sentence_id"]: row for row in rebuilt_sentences_by_doc[document_id]}
            doc_body = rebuilt_documents[document_id]
            active_ordinal = 0

            passages = make_consolidated_passages(
                rebuilt_sentences_by_doc[document_id],
                target=384,
                minimum=128,
                maximum=512,
                document_text=doc_body,
            )

            for upstream_ordinal, passage in enumerate(passages):
                members = [sentence_map[sid] for sid in passage["sentence_ids"]]
                passage_spans = []
                for m in members:
                    passage_spans.extend(m.get("source_spans", []))
                resolved_passage_spans = attach_source_span_text(passage_spans, doc_raw_pages)
                text = passage["text"]
                fingerprint = normalized_fingerprint(text)
                duplicate_of = seen_fingerprints.get(fingerprint)
                passage_id = stable_id("paperpassage", document_id, upstream_ordinal, text)

                row = {
                    "schema_version": SCHEMA_VERSION,
                    "chunk_id": stable_id("paperchunk", document_id, upstream_ordinal, text),
                    "passage_id": passage_id,
                    "document_id": document_id,
                    "group_id": doc["group_id"],
                    "chunk_index": active_ordinal if duplicate_of is None else None,
                    "upstream_chunk_index": upstream_ordinal,
                    "split": split,
                    "subset": "core_human" if duplicate_of is None else "excluded",
                    "label": "H",
                    "provenance_status": "high_confidence_human",
                    "institution_id": doc["institution_id"],
                    "institution_name": doc["institution_name"],
                    "source_file_id": doc_source_ids[document_id],
                    "metadata_record_id": doc.get("source_record_id"),
                    "source_path": doc["source_path"],
                    "source_sha256": doc["source_sha256"],
                    "source_url": doc.get("source_url"),
                    "section_ids": passage["section_ids"],
                    "paragraph_ids": passage["paragraph_ids"],
                    "sentence_ids": passage["sentence_ids"],
                    "source_spans": resolved_passage_spans,
                    "page_start": passage["page_start"],
                    "page_end": passage["page_end"],
                    "page_start_index": passage["page_start"],
                    "page_end_index": passage["page_end"],
                    "page_start_number": passage["page_start"] + 1,
                    "page_end_number": passage["page_end"] + 1,
                    "start_char": passage["start_char"],
                    "end_char": passage["end_char"],
                    "document_char_start": passage["start_char"],
                    "document_char_end": passage["end_char"],
                    "document_char_spans": [[member["char_start"], member["char_end"]] for member in members],
                    "approx_tokens": passage["approx_tokens"],
                    "content_sha256": fingerprint,
                    "text_sha256": sha256_text(text),
                    "fingerprint_sha256": fingerprint,
                    "short_tail": bool(passage.get("short_tail")),
                    "exclusion_reason": "exact_passage_duplicate" if duplicate_of else None,
                    "duplicate_of": duplicate_of,
                    "offset_unit": "unicode_code_points",
                    "text": text,
                }
                if duplicate_of:
                    write_line(excluded_handle, row)
                    continue
                seen_fingerprints[fingerprint] = passage_id
                active_ordinal += 1
                passage_ids.add(passage_id)
                write_line(passage_all, row)
                write_line(split_handles[split], row)
                passage_counts[split] += 1
    finally:
        excluded_handle.close()
        passage_all.close()
        for handle in split_handles.values():
            handle.close()

    # Split manifest
    with (output / "manifest" / "split_manifest.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows(source / "split_manifest.jsonl"):
            if row["document_id"] not in accepted_ids:
                continue
            write_line(handle, {
                **row,
                "schema_version": SCHEMA_VERSION,
                "source_file_id": doc_source_ids[row["document_id"]],
            })

    # Lineage
    for source_name, output_name in (
        ("pages.jsonl", "source_pages.jsonl"),
        ("paragraphs.jsonl", "source_paragraphs.jsonl"),
    ):
        with (output / "lineage" / output_name).open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows(lineage / "canonical" / source_name):
                if row["document_id"] in accepted_ids:
                    write_line(handle, {
                        **row,
                        "source_file_id": doc_source_ids[row["document_id"]],
                    })

    review_source = source / "review" / "metadata_review_queue.jsonl"
    if review_source.exists():
        with (output / "review" / "metadata_review_queue.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows(review_source):
                if row.get("document_id") in accepted_ids:
                    write_line(handle, row)
    excluded = source / "manifest" / "excluded_records.jsonl"
    if excluded.exists():
        shutil.copyfile(excluded, output / "manifest" / "excluded_records.jsonl")

    # 7. Metadata, Checksums & Release Sealing
    print("[7/7] Generating release checksums and dataset cards...", flush=True)

    diversity = {
        "documents_by_institution": dict(Counter(row["institution_id"] for row in accepted_documents)),
        "documents_by_year": dict(sorted(Counter(str(row["year"]) for row in accepted_documents).items())),
        "documents_by_year_bucket": dict(Counter(row.get("year_bucket", "unknown") for row in accepted_documents)),
        "documents_by_domain": dict(Counter(row["domain_id"] for row in accepted_documents)),
        "documents_by_type": dict(Counter(row["document_type_id"] for row in accepted_documents)),
        "documents_by_language": dict(Counter(row["language_detected"] for row in accepted_documents)),
        "documents_by_quality": dict(Counter(row["quality_tier"] for row in accepted_documents)),
        "documents_by_split": dict(Counter(row["split"] for row in accepted_documents)),
        "passages_by_split": dict(passage_counts),
        "crawler_records_by_status": dict(Counter(row["record_status"] for row in crawler_records)),
        "unmatched_local_pdf_files": len(unmatched_pdf_files),
        "source_release_outcomes": dict(Counter(row["release_outcome"] for row in release_decisions.values())),
        "source_release_reasons": dict(Counter(row["release_reason"] for row in release_decisions.values())),
    }
    (output / "reports" / "diversity.json").write_text(
        json.dumps(diversity, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    info = {
        "dataset_release": args.release_id,
        "schema_version": SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_release": str(source),
        "lineage_release": str(lineage),
        "task": "sentence-level H/P/G detection; this release contains Human (H) references only",
        "human_cutoff_year_inclusive": HUMAN_CUTOFF_YEAR,
        "counts": {
            "audited_source_files": len(pdf_inventory),
            "unique_source_documents": len(source_manifest),
            "crawler_records": len(crawler_records),
            "crawler_records_with_local_pdf": sum(row["record_status"].startswith("pdf_present") for row in crawler_records),
            "crawler_metadata_only": sum(row["record_status"] == "metadata_only" for row in crawler_records),
            "crawler_pdf_missing_from_local": sum(row["record_status"] == "pdf_missing_from_local" for row in crawler_records),
            "unmatched_local_pdf_files": len(unmatched_pdf_files),
            "documents": len(accepted_documents),
            "excluded_documents": len(documents) - len(accepted_documents),
            "source_bytes": source_bytes,
            "source_pages": source_pages,
            "metadata_repository_matches": metadata_matches,
            "sentences": len(sentence_ids),
            "paragraphs": len(sentences_by_paragraph),
            "passages": len(passage_ids),
            **{f"passages_{split}": passage_counts[split] for split in SPLITS},
        },
        "traceability_chain": [
            "source_file_id", "document_id", "paragraph_id", "sentence_id", "passage_id", "split"
        ],
        "crawler_master_sha256": sha256_file(crawler_master),
        "model_input_policy": "Use passage.text only; metadata is for audit/grouping/stratification",
        "redistribution_status": "research_internal_pending_rights_review",
    }
    (output / "dataset_info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "VERSION.json").write_text(
        json.dumps({"dataset_release": args.release_id, "schema_version": SCHEMA_VERSION, "pipeline_version": PIPELINE_VERSION}, indent=2) + "\n",
        encoding="utf-8",
    )
    card = f"""# {args.release_id}

Immutable Vietnamese Human-written thesis corpus release for sentence-level H/P/G research.
Comprehensively normalized: CMap space restored, sentences healed, passages consolidated.

This release contains {len(accepted_documents)} accepted documents and {len(passage_ids)}
non-overlapping Human passages. It is an H-reference corpus; P/G counterfactuals are not included.

## Canonical training data

- `documents.jsonl`: canonical metadata and cleaned body text.
- `sentences.jsonl`: sentence labels and exact document offsets.
- `paragraphs.jsonl`: paragraph-to-sentence linkage.
- `passages/all.jsonl`: non-overlapping canonical passages (256-384 target tokens).
- `splits/{{train,dev,test}}.jsonl`: immutable group-level splits.

## Provenance

Join `passage.document_id` to `documents.document_id`, then join `source_file_id` to
`manifest/source_files.jsonl`. Sentence IDs and character spans reconstruct each passage exactly.
`lineage/` preserves page-level extraction records from the source PDFs.
"""
    (output / "DATASET_CARD.md").write_text(card, encoding="utf-8")

    checksum_lines = []
    for path in sorted(p for p in output.rglob("*") if p.is_file()):
        checksum_lines.append(f"{sha256_file(path)}  {path.relative_to(output).as_posix()}")
    (output / "manifest" / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")

    print(f"\nSuccessfully built release: {args.release_id} at {output}")
    print(f"Accepted Documents : {len(accepted_documents)}")
    print(f"Total Sentences    : {len(sentence_ids)}")
    print(f"Total Passages     : {len(passage_ids)}")
    return info


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--lineage", type=Path, required=True)
    parser.add_argument("--pdf-root", type=Path, required=True)
    parser.add_argument("--metadata-dir", type=Path, required=True)
    parser.add_argument("--crawler-master", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--release-id", default="human_written_dataset_v2_16_paper")
    parser.add_argument("--overwrite", action="store_true", default=False)
    return parser.parse_args()


if __name__ == "__main__":
    build(parse_args())
