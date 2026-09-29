"""Deterministic reconstruction of PDF text-layer lines into visual units."""

from __future__ import annotations

import re
import statistics
import unicodedata
from dataclasses import dataclass
from typing import Sequence


UNIT_TYPES = {"paragraph", "heading", "bullet_item", "caption", "table_like"}
BULLET_RE = re.compile(r"^\s*(?:[\u2022\uf0b7\u25aa\u25cf\u25e6]|[-*])\s+")
NUMBERED_HEADING_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)*[.)]?|chương\s+\d+)", re.I)
ENDING_PUNCTUATION = ".!?:;…"


@dataclass(frozen=True)
class TextSpan:
    text: str
    font_name: str
    font_size: float
    bbox: tuple[float, float, float, float]
    source_start: int
    source_end: int
    page_index: int = -1


@dataclass(frozen=True)
class TextLine:
    page_index: int
    line_index: int
    bbox: tuple[float, float, float, float]
    text: str
    spans: tuple[TextSpan, ...]
    source_start: int
    source_end: int


@dataclass(frozen=True)
class SourceSpan:
    page_index: int
    source_start: int
    source_end: int


@dataclass(frozen=True)
class LayoutUnit:
    text: str
    unit_type: str
    page_start: int
    page_end: int
    source_spans: tuple[SourceSpan, ...]
    review_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class LayoutConfig:
    """Thresholds measured from text-layer samples and stored in a release manifest."""

    line_gap_multiplier: float = 1.65
    large_gap_multiplier: float = 1.9
    left_edge_tolerance: float = 8.0
    continuation_indent_tolerance: float = 24.0
    column_gap: float = 48.0
    heading_font_ratio: float = 1.1
    heading_max_chars: int = 180
    caption_prefixes: tuple[str, ...] = ("Hình", "Bảng", "Figure", "Table")
    table_alignment_tolerance: float = 10.0
    page_top_continuation_ratio: float = 0.22
    page_bottom_continuation_ratio: float = 0.84


def _clean(text: str) -> str:
    return " ".join(text.replace("\u00a0", " ").split())


def _is_bullet(line: TextLine) -> bool:
    return bool(BULLET_RE.match(line.text.lstrip()) or (
        line.spans and "symbol" in line.spans[0].font_name.casefold()
    ))


def _is_caption(text: str, config: LayoutConfig) -> bool:
    value = _clean(text).casefold()
    return any(value.startswith(prefix.casefold() + " ") or value.startswith(prefix.casefold() + ".")
               for prefix in config.caption_prefixes)


def _is_heading(line: TextLine, text: str, median_size: float, config: LayoutConfig) -> bool:
    if not text or len(text) > config.heading_max_chars or text.endswith((".", ",", ";", ":")):
        return False
    alpha = [char for char in text if char.isalpha()]
    all_caps = len(alpha) >= 4 and sum(char.isupper() for char in alpha) / len(alpha) >= 0.75
    bold = any("bold" in span.font_name.casefold() for span in line.spans)
    larger = any(span.font_size >= median_size * config.heading_font_ratio for span in line.spans)
    return bool((bold and (NUMBERED_HEADING_RE.match(text) or all_caps or len(text) <= 70)) or
                (larger and NUMBERED_HEADING_RE.match(text)))


def _line_type(line: TextLine, median_size: float, config: LayoutConfig,
               table_lines: set[int]) -> str:
    text = _clean(line.text)
    if _is_bullet(line):
        return "bullet_item"
    if _is_caption(text, config):
        return "caption"
    if id(line) in table_lines:
        return "table_like"
    if _is_heading(line, text, median_size, config):
        return "heading"
    return "paragraph"


def _table_lines(lines: Sequence[TextLine], median_height: float,
                 config: LayoutConfig) -> set[int]:
    """Recognize repeated cell starts from separate text span boxes."""
    def cells(line: TextLine) -> list[TextSpan]:
        return sorted((span for span in line.spans if span.text.strip()),
                      key=lambda span: span.bbox[0])

    def aligned(left: TextLine, right: TextLine) -> bool:
        left_cells, right_cells = cells(left), cells(right)
        if min(len(left_cells), len(right_cells)) < 2:
            return False
        if right.bbox[1] - left.bbox[1] > median_height * config.large_gap_multiplier:
            return False
        for row in (left_cells, right_cells):
            if not any(row[i + 1].bbox[0] - row[i].bbox[0] >= 2 * median_height
                       for i in range(len(row) - 1)):
                return False
        return len(left_cells) == len(right_cells) and all(
            abs(a.bbox[0] - b.bbox[0]) <= config.table_alignment_tolerance
            for a, b in zip(left_cells, right_cells)
        )

    result: set[int] = set()
    ordered = sorted(lines, key=lambda item: (item.bbox[1], item.bbox[0]))
    for left, right in zip(ordered, ordered[1:]):
        if aligned(left, right):
            result.update((id(left), id(right)))
    return result


def _column_groups(lines: Sequence[TextLine], config: LayoutConfig) -> list[list[TextLine]]:
    groups: list[list[TextLine]] = []
    for line in sorted(lines, key=lambda item: (item.bbox[0], item.bbox[1], item.line_index)):
        if not groups or line.bbox[0] - min(item.bbox[0] for item in groups[-1]) > config.column_gap:
            groups.append([line])
        else:
            groups[-1].append(line)
    return [sorted(group, key=lambda item: (item.bbox[1], item.bbox[0], item.line_index)) for group in groups]


def _source_span(line: TextLine) -> SourceSpan:
    return SourceSpan(line.page_index, line.source_start, line.source_end)


def _can_continue(current: list[TextLine], next_line: TextLine, unit_type: str,
                  median_height: float, config: LayoutConfig) -> bool:
    previous = current[-1]
    if unit_type == "heading":
        return abs(next_line.bbox[1] - previous.bbox[1]) <= median_height * 0.25
    if unit_type not in {"paragraph", "bullet_item", "table_like"}:
        return False
    if unit_type == "bullet_item" and _is_bullet(next_line):
        return False
    if next_line.bbox[1] - previous.bbox[3] > median_height * config.large_gap_multiplier:
        return False
    if next_line.bbox[1] - previous.bbox[1] > median_height * config.line_gap_multiplier:
        return False
    edge_difference = next_line.bbox[0] - previous.bbox[0]
    if abs(edge_difference) <= config.left_edge_tolerance:
        return True
    if unit_type == "bullet_item":
        return abs(next_line.bbox[0] - current[0].bbox[0]) <= config.continuation_indent_tolerance
    return (unit_type == "paragraph" and len(current) == 1 and edge_difference < 0
            and abs(edge_difference) <= config.continuation_indent_tolerance)


def _make_unit(lines: Sequence[TextLine], unit_type: str, review_flags: tuple[str, ...] = ()) -> LayoutUnit:
    return LayoutUnit(
        text=("\n".join(line.text.rstrip("\r\n") for line in lines) if unit_type == "table_like"
              else " ".join(_clean(line.text) for line in lines if _clean(line.text))),
        unit_type=unit_type, page_start=lines[0].page_index, page_end=lines[-1].page_index,
        source_spans=tuple(_source_span(line) for line in lines), review_flags=review_flags,
    )


def reconstruct_page_units(lines: Sequence[TextLine], page_width: float, page_height: float,
                           config: LayoutConfig) -> list[LayoutUnit]:
    """Join visual continuations without crossing a column, cue, or large gap."""
    del page_width, page_height
    nonempty = [line for line in lines if _clean(line.text)]
    if not nonempty:
        return []
    sizes = [span.font_size for line in nonempty for span in line.spans if span.font_size > 0]
    median_size = statistics.median(sizes) if sizes else 12.0
    median_height = statistics.median(max(1.0, line.bbox[3] - line.bbox[1]) for line in nonempty)
    table_lines = _table_lines(nonempty, median_height, config)
    units: list[LayoutUnit] = []
    for column in _column_groups(nonempty, config):
        group: list[TextLine] = []
        group_type = "paragraph"
        for item in column:
            item_type = _line_type(item, median_size, config, table_lines)
            if not group:
                group, group_type = [item], item_type
            elif ((item_type == group_type or (group_type == "bullet_item" and item_type == "paragraph"))
                  and _can_continue(group, item, group_type, median_height, config)):
                group.append(item)
            else:
                units.append(_make_unit(group, group_type))
                group, group_type = [item], item_type
        if group:
            units.append(_make_unit(group, group_type))
    return units


def _edge_metrics(unit: LayoutUnit, lines: Sequence[TextLine]) -> tuple[float, float, float, float]:
    lookup = {(line.page_index, line.source_start, line.source_end): line for line in lines}
    unit_lines = [
        lookup[(span.page_index, span.source_start, span.source_end)]
        for span in unit.source_spans
        if (span.page_index, span.source_start, span.source_end) in lookup
    ]
    return (unit_lines[-1].bbox[0], unit_lines[0].bbox[1],
            unit_lines[-1].bbox[3], unit_lines[-1].bbox[2])


def _joined(left: LayoutUnit, right: LayoutUnit) -> LayoutUnit:
    return LayoutUnit(
        text=f"{left.text} {right.text}".strip(), unit_type=left.unit_type,
        page_start=left.page_start, page_end=right.page_end,
        source_spans=left.source_spans + right.source_spans,
        review_flags=tuple(dict.fromkeys(left.review_flags + right.review_flags)),
    )


def _starts_as_continuation(text: str) -> bool:
    for char in text.lstrip():
        if char.isdigit():
            return False
        if char.isalpha():
            return char.islower()
        if not unicodedata.category(char).startswith("P"):
            return False
    return False


def _uncertain_capital_or_number(left_text: str, right_text: str,
                                 last_right: float, page_width: float) -> bool:
    """A capital or numeral can complete a phrase at a visual line wrap."""
    first = right_text.lstrip()[:1]
    if not first or not (first.isupper() or first.isdigit()):
        return False
    last_word = re.search(r"\w+$", left_text.rstrip())
    bridge_words = {
        "a", "an", "the", "by", "in", "at", "of", "to", "from", "for", "with",
        "và", "của", "tại", "ở", "vào", "từ", "đến", "năm", "ngày", "số",
    }
    return (bool(last_word and last_word.group().casefold() in bridge_words)
            or last_right >= page_width * 0.8)


def reconstruct_document_units(pages: Sequence[Sequence[TextLine]], page_sizes: Sequence[tuple[float, float]],
                               config: LayoutConfig) -> list[LayoutUnit]:
    """Join pages only when the prose and both page edges prove continuation."""
    if len(pages) != len(page_sizes):
        raise ValueError("pages and page_sizes must have the same length")
    result: list[LayoutUnit] = []
    previous_lines: Sequence[TextLine] | None = None
    previous_size: tuple[float, float] | None = None
    for page_lines, (width, height) in zip(pages, page_sizes):
        units = reconstruct_page_units(page_lines, width, height, config)
        if not units:
            previous_lines, previous_size = None, None
            continue
        if result and units and previous_lines is not None and previous_size is not None:
            last, first = result[-1], units[0]
            last_left, _, last_bottom, last_right = _edge_metrics(last, previous_lines)
            first_left, first_top, _, _ = _edge_metrics(first, page_lines)
            prose_pair = (
                last.unit_type == first.unit_type == "paragraph"
                and not last.text.rstrip().endswith(tuple(ENDING_PUNCTUATION))
            )
            geometry_supports = (
                last_bottom >= previous_size[1] * config.page_bottom_continuation_ratio
                and first_top <= height * config.page_top_continuation_ratio
                and abs(last_left - first_left) <= config.continuation_indent_tolerance
            )
            lowercase_start = _starts_as_continuation(first.text)
            uncertain_start = (geometry_supports and _uncertain_capital_or_number(
                last.text, first.text, last_right, previous_size[0]))
            plausible = prose_pair and (lowercase_start or uncertain_start)
            continues = prose_pair and lowercase_start and geometry_supports
            if continues:
                result[-1] = _joined(last, first)
                units = units[1:]
            elif plausible:
                first = units[0]
                units[0] = LayoutUnit(first.text, first.unit_type, first.page_start, first.page_end,
                                      first.source_spans,
                                      tuple(dict.fromkeys(first.review_flags + ("ambiguous_page_continuation",))))
        result.extend(units)
        previous_lines, previous_size = page_lines, (width, height)
    return result
