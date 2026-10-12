"""Transparent automatic quality gates for Human v3.2 chunks."""
from __future__ import annotations

import re
import unicodedata

from .core import unsafe_reason

BULLET_SYMBOLS = "•▪◦●○◊❖◆◇▸▶►➔➢⁃■□♦‣⁌⁍‧※∙◾◽◈✦✧"
BULLET_PREFIX = rf"(?:[-–—−+]\s+|[-–—−+](?=[A-ZÀ-Ỹ0-9])|[{BULLET_SYMBOLS}]\s*|_\s+|(?:\(?[a-zA-Z][.)]|\(?[0-9]{{1,2}}[.)]|\((?:i|ii|iii|iv|v|vi|vii|viii|ix|x)\))\s+)"
BULLET_START = re.compile(rf"^\s*{BULLET_PREFIX}")
TOC_LEADER = re.compile(r"\.{3,}\s*\d+\s*$")
CAPTION_START = re.compile(r"(?i)^\s*(?:biểu\s*đồ|figure|table)\s+\d")
CAPTION_SENTENCE = re.compile(r"(?i)(?:^|(?<=[.!?])\s+)(?:hình|bảng|biểu\s*đồ|figure|table)\s+[\dIVXLCDM]+\s*[:.)]")
BULLET_SENTENCE = re.compile(rf"(?:^|(?<=[.!?])\s+){BULLET_PREFIX}")


def chunk_quality_reasons(chunk: dict, document: dict, config: dict) -> list[str]:
    """Return deterministic reasons that keep a chunk out of the candidate export."""
    reasons: list[str] = []
    text = chunk["text"]
    minimum = config.get("chunk_candidate_min_tokens", config["passage_min"])
    maximum = config.get("chunk_candidate_max_tokens", config["passage_max"])
    min_sentences = config.get("chunk_candidate_min_sentences", config["min_sentences"])
    if chunk["sentence_count"] < min_sentences:
        reasons.append("fewer_than_two_complete_sentences")
    if chunk["approx_tokens"] < minimum:
        reasons.append("below_candidate_token_minimum")
    if chunk["approx_tokens"] > maximum:
        reasons.append("above_candidate_token_maximum")
    problem = unsafe_reason(text)
    if problem:
        reasons.append(problem)
    if BULLET_START.match(text):
        reasons.append("list_or_outline_start")
    if CAPTION_START.match(text):
        reasons.append("caption_at_chunk_start")
    if CAPTION_SENTENCE.search(text) and not CAPTION_START.match(text):
        reasons.append("caption_inside_chunk")
    if BULLET_SENTENCE.search(text) and not BULLET_START.match(text):
        reasons.append("list_marker_inside_chunk")
    if any(unicodedata.category(c) in {"Cc", "Cf", "Cn", "Co", "Cs"} for c in text):
        reasons.append("unexpected_unicode_category")

    toc_pages = set(document.get("extraction", {}).get("bounds", {}).get("toc_pages", []))
    max_heading_chars = config.get("heading_max_chars", 100)
    for heading in chunk.get("heading_path", []):
        title = heading.get("title", "")
        if heading.get("page_index") in toc_pages or TOC_LEADER.search(title):
            reasons.append("heading_from_table_of_contents")
        if len(title) > max_heading_chars:
            reasons.append("overlong_heading_path")
    return sorted(set(reasons))


def chunk_quality_warnings(chunk: dict, config: dict) -> list[str]:
    """Non-blocking metadata warnings; prose remains usable and traceable."""
    minimum = config.get("heading_min_confidence", 0.85)
    if any(h.get("confidence", 0) < minimum for h in chunk.get("heading_path", [])):
        return ["low_confidence_heading_path"]
    return []
