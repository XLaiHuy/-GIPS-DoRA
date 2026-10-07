"""Conservative PDF text-layer space recovery from visible glyph geometry.

Only inserts a space where two adjacent letter glyphs have a gap comparable
to an explicit space in the same page. No language-model or dictionary edits.
"""
from __future__ import annotations

import statistics


def page_space_width(rawdict: dict) -> float | None:
    widths = [
        char["bbox"][2] - char["bbox"][0]
        for block in rawdict.get("blocks", []) if block.get("type") == 0
        for line in block.get("lines", [])
        for span in line.get("spans", [])
        for char in span.get("chars", [])
        if char.get("c") == " " and 1.0 <= char["bbox"][2] - char["bbox"][0] <= 20.0
    ]
    return statistics.median(widths) if len(widths) >= 8 else None


def recover_line(line: dict, fallback_space_width: float | None) -> tuple[str, str, list[dict]]:
    """Return visual text, text-layer text, and auditable inserted-space gaps."""
    chars = [char for span in line.get("spans", []) for char in span.get("chars", [])]
    source = "".join(char["c"] for char in chars)
    if not chars or fallback_space_width is None:
        return source, source, []
    explicit_widths = [
        char["bbox"][2] - char["bbox"][0]
        for char in chars if char["c"] == " "
        and 1.0 <= char["bbox"][2] - char["bbox"][0] <= 20.0
    ]
    font_sizes = [span.get("size", 0) for span in line.get("spans", []) if span.get("size", 0) > 0]
    font_reference = 0.32 * statistics.median(font_sizes) if font_sizes else fallback_space_width
    space_width = (statistics.median(explicit_widths) if len(explicit_widths) >= 2
                   else max(fallback_space_width, font_reference))
    output = []
    insertions = []
    for index, char in enumerate(chars):
        if index:
            left = chars[index - 1]
            gap = char["bbox"][0] - left["bbox"][2]
            same_baseline = abs(char["bbox"][1] - left["bbox"][1]) <= 2.0
            if (left["c"].isalpha() and char["c"].isalpha() and same_baseline
                    and 0.72 * space_width <= gap <= 1.65 * space_width
                    and gap >= 1.4):
                output.append(" ")
                insertions.append({
                    "source_char_offset": index,
                    "left_char": left["c"], "right_char": char["c"],
                    "gap_pt": round(gap, 3), "reference_space_width_pt": round(space_width, 3),
                })
        output.append(char["c"])
    return "".join(output), source, insertions
