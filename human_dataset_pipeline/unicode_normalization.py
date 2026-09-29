"""Conservative Unicode cleanup with character lineage and keyed glyph evidence."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from typing import Mapping, Sequence

from layout_reconstruction import TextSpan


GlyphKey = tuple[str, str, int]


@dataclass(frozen=True)
class GlyphRule:
    replacement: str
    evidence: str
    reviewer_status: str


@dataclass(frozen=True)
class NormalizationResult:
    text: str
    status: str
    review_flags: tuple[str, ...]
    rules_applied: tuple[str, ...]
    source_char_ranges: tuple[tuple[tuple[int, int], ...], ...]


SourceRange = tuple[int, int]
CharacterSources = tuple[SourceRange, ...]


def _merge_ranges(ranges: Sequence[SourceRange]) -> CharacterSources:
    """Coalesce only overlapping or adjacent contributors, never gaps."""
    merged: list[SourceRange] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def _span_alignment(text: str, spans: Sequence[TextSpan]) -> dict[int, tuple[TextSpan, int]] | None:
    """Align ordered nonwhitespace characters, allowing layout whitespace changes."""
    if not spans:
        return None
    input_chars = [(index, char) for index, char in enumerate(text) if not char.isspace()]
    span_chars: list[tuple[TextSpan, int, str]] = []
    previous_page: int | None = None
    previous_end: int | None = None
    for span in spans:
        if (type(span.page_index) is not int
                or type(span.source_start) is not int or type(span.source_end) is not int
                or span.source_end - span.source_start != len(span.text)
                or (previous_page is not None and span.page_index < previous_page)
                or (previous_page == span.page_index and previous_end is not None
                    and span.source_start < previous_end)):
            return None
        previous_page = span.page_index
        previous_end = span.source_end
        span_chars.extend((span, index, char) for index, char in enumerate(span.text)
                          if not char.isspace())
    if [char for _, char in input_chars] != [char for _, _, char in span_chars]:
        return None
    return {input_index: (span, span_index)
            for (input_index, _), (span, span_index, _) in zip(input_chars, span_chars)}


def _canonical_ranges(chars: list[tuple[str, CharacterSources]]
                      ) -> tuple[str, list[CharacterSources]]:
    """NFC-compose while assigning each output character its NFD contributors."""
    source_text = "".join(char for char, _ in chars)
    normalized = unicodedata.normalize("NFC", source_text)
    decomposed: list[tuple[str, CharacterSources]] = []
    for char, source_ranges in chars:
        for part in unicodedata.normalize("NFD", char):
            position = len(decomposed)
            combining_class = unicodedata.combining(part)
            if combining_class:
                while (position and unicodedata.combining(decomposed[position - 1][0])
                       > combining_class):
                    position -= 1
            decomposed.insert(position, (part, source_ranges))

    ranges: list[CharacterSources] = []
    cursor = 0
    for char in normalized:
        parts = unicodedata.normalize("NFD", char)
        contributors = decomposed[cursor:cursor + len(parts)]
        if "".join(part for part, _ in contributors) != parts:
            raise ValueError("NFC source correspondence could not be established")
        ranges.append(_merge_ranges([source_range for _, sources in contributors
                                     for source_range in sources]))
        cursor += len(parts)
    return normalized, ranges


def _collapse_whitespace(text: str, ranges: list[CharacterSources]
                         ) -> tuple[str, tuple[CharacterSources, ...]]:
    result: list[str] = []
    result_ranges: list[CharacterSources] = []
    index = 0
    while index < len(text):
        if not text[index].isspace():
            result.append(text[index])
            result_ranges.append(ranges[index])
            index += 1
            continue
        end = index + 1
        while end < len(text) and text[end].isspace():
            end += 1
        if result and end < len(text):
            result.append(" ")
            result_ranges.append(_merge_ranges([source_range
                                                 for sources in ranges[index:end]
                                                 for source_range in sources]))
        index = end
    return "".join(result), tuple(result_ranges)


def _source_span_verified(span: TextSpan,
                          source_page_texts: Mapping[int, str] | None) -> bool:
    if (type(span.source_start) is not int or type(span.source_end) is not int
            or type(span.page_index) is not int or span.page_index < 0
            or source_page_texts is None or span.page_index not in source_page_texts):
        return False
    raw_page = source_page_texts[span.page_index]
    return (isinstance(raw_page, str) and 0 <= span.source_start <= span.source_end <= len(raw_page)
            and raw_page[span.source_start:span.source_end] == span.text)


def normalize_unit(text: str, document_id: str, spans: Sequence[TextSpan],
                   glyph_rules: Mapping[GlyphKey, GlyphRule],
                   source_page_texts: Mapping[int, str] | None = None) -> NormalizationResult:
    """Normalize a layout unit without inferring unsupported source glyphs."""
    alignment = _span_alignment(text, spans)
    flags: list[str] = []
    applied: list[str] = []
    chars: list[tuple[str, CharacterSources]] = []

    def flag(value: str) -> None:
        if value not in flags:
            flags.append(value)

    for index, char in enumerate(text):
        if char == "\u00ad":
            continue
        replacement = char
        candidate = any(key[0] == document_id and key[2] == ord(char) for key in glyph_rules)
        if candidate and alignment is None:
            flag("span_alignment_unresolved")
        if alignment is not None and index in alignment:
            span, span_index = alignment[index]
            rule = glyph_rules.get((document_id, span.font_name, ord(char)))
            if rule is not None:
                if not _source_span_verified(span, source_page_texts):
                    flag("source_span_unverified")
                elif rule.evidence and rule.reviewer_status in {"automatic", "reviewed"}:
                    replacement = rule.replacement
                    applied.append(json.dumps({
                        "document_id": document_id,
                        "font_name": span.font_name,
                        "codepoint": ord(char),
                        "replacement": replacement,
                        "source_page": span.page_index + 1,
                        "source_span": [span.source_start + span_index,
                                        span.source_start + span_index + 1],
                        "evidence": rule.evidence,
                        "disposition": rule.reviewer_status,
                    }, ensure_ascii=False, sort_keys=True))
                else:
                    flag("unverified_glyph_rule")
        elif candidate:
            flag("span_alignment_unresolved")
        if char == "\u00a0" and replacement == char:
            replacement = " "
        if any(unicodedata.category(value) == "Co" for value in replacement):
            flag("unresolved_pua")
        if "\ufffd" in replacement:
            flag("replacement_character")
        chars.extend((value, ((index, index + 1),)) for value in replacement)

    normalized, ranges = _canonical_ranges(chars)
    normalized, source_ranges = _collapse_whitespace(normalized, ranges)
    return NormalizationResult(normalized, "review_required" if flags else "active",
                               tuple(flags), tuple(applied), source_ranges)
