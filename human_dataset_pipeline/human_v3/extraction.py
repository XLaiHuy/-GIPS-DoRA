"""PDF-only extraction and conservative layout reconstruction.

Uncertain regions are recorded, never filled by a language model. Paragraph text
is a replayable normalization of exact line slices in raw page text.
"""
from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from pathlib import Path

import fitz

from .core import (JsonlWriter, approx_tokens, dump, fingerprint, fold, ident,
                   normalized, pack_sentences, rows, sentence_bounds, sentence_reason,
                   sha, unsafe_reason)
from .pdf_spacing import page_space_width, recover_line

START = re.compile(
    r"^(?:(?:chuong\s*(?:1|i)|phan\s*(?:1|i))\b.{0,150}|"
    r"(?:phan\s+mo\s+dau|loi\s+mo\s+dau|mo\s+dau|loi\s+noi\s+dau|"
    r"gioi\s+thieu)[\s:.\-\u2013\u2014]*|"
    r"(?:i|1)[.)]?\s+(?:gioi thieu|mo dau|tong quan|dat van de|muc tieu|"
    r"co so ly thuyet|khao sat hien trang)\b.{0,120})$"
)
BOUNDARY_PREFIX = (
    r"(?:(?:\d+(?:\.\d+)*|[ivxlcdm]+(?:\.\d+)*|chuong\s+[\wivxlcdm]+|phan\s+[\wivxlcdm]+)"
    r"(?:\s*[.):\-\u2013\u2014]\s*|\s+))?"
)
REFERENCE_TITLES = (
    r"(?:tai lieu tham khao|tai lie tham khao|cac tai lieu tham khao|"
    r"nguon tai lieu tham khao|nguon tham khao|website tham khao|"
    r"danh muc (?:cac )?tai lieu tham khao|references|bibliography)"
)
REFERENCE_HEADING = re.compile(
    r"^" + BOUNDARY_PREFIX + REFERENCE_TITLES +
    r"\s*[:.]?\s*(?:\d+)?$"
)
BARE_REFERENCE_HEADING = re.compile(
    r"^" + BOUNDARY_PREFIX + r"tham\s+khao\s*[:.]\s*(?:\d+)?$"
)
APPENDIX_HEADING = re.compile(
    r"^" + BOUNDARY_PREFIX + r"(?:phu luc|appendix)\b"
)
# Broad line classifier used to keep back-matter headings out of prose. The
# body-end detector below is stricter because it must not mistake SQL text for
# an actual reference heading.
END = re.compile(
    r"^" + BOUNDARY_PREFIX +
    r"(?:" + REFERENCE_TITLES + r"|phu luc|appendix)\b"
)
FRONT = re.compile(r"^(?:muc luc|danh muc (?:hinh|bang|(?:tu )?viet tat)|loi cam on|loi cam doan|nhan xet|tom tat|abstract)\b")
PROSE_CHAPTER = re.compile(r"^(?:chuong|phan)\s+(?:\d+|[ivxlcdm]+)\s+(?:tap trung|trinh bay|se|duoc|noi ve|de cap|gioi thieu ve)\b", re.I)
NUMBER_HEADING = re.compile(r"^\s*\d+(?:\.\d+)*[.)]?\s+[^\W\d_]")
OUTLINE_HEADING = re.compile(r"^\s*(?:([IVXLCDM]{1,6})[.)]|(\d{1,2})[.)]|([A-Za-z])[)])\s+[^\W\d_]", re.I)


def boundary_fold(text: str) -> str:
    """Fold boundary headings and repair a known Ƣ/ƣ glyph substitution only."""
    return fold(normalized(text)).replace("ƣ", "u")


def _heading_has_prose_after(pages: list[dict], position: tuple[int, int]) -> bool:
    """Distinguish a real pre-contents introduction from a cover/contents label."""
    page_index, line_index = position
    if not 0 <= page_index < len(pages):
        return False
    lines = pages[page_index]["lines"][line_index + 1:]
    tail = normalized(" ".join(line["text"] for line in lines))
    heading = boundary_fold(pages[page_index]["lines"][line_index]["text"])
    # Some PDFs put acknowledgments under "Loi noi dau". Keep that front
    # matter out when the opening paragraph clearly thanks contributors.
    if heading.rstrip(" .:") == "loi noi dau" and re.search(
            r"\b(?:cam on|chan thanh cam on|biet on|tri an|xin gui loi)\b", boundary_fold(tail[:500])):
        return False
    letters = sum(char.isalpha() for char in tail)
    if len(tail) < 80 or letters / max(1, len(tail)) < 0.55:
        return False
    if re.search(r"\.{3,}", tail) or len(tail.split()) < 10:
        return False
    return bool(re.search(r"[.!?](?:[\"'\u201d\u2019\)\]]*)?(?:\s|$)", tail))


def heading_descriptor(line: dict, median_font: float, stack: list[dict]) -> dict:
    """Return a conservative title/level. Explicit numbering outranks style guesses."""
    title = normalized(line["text"])
    folded = fold(title)
    numbered = re.match(r"^\s*(\d+(?:\.\d+)*)(?:[.)])?(?:\s+|$)", title)
    chapter = re.match(r"^(?:chuong|phan)\s+([\dIVX]+)\b", folded, re.I)
    if chapter:
        return {"title": title, "level": 1, "confidence": 0.98, "method": "chapter_or_part_keyword"}
    outline = OUTLINE_HEADING.match(title)
    if outline and outline.group(1):
        return {"title": title, "level": 1, "confidence": 0.96, "method": "roman_outline_heading"}
    if numbered:
        return {"title": title, "level": min(6, numbered.group(1).count(".") + 1),
                "confidence": 0.98, "method": "numbered_heading"}
    if outline and outline.group(2):
        return {"title": title, "level": min(6, max(2, stack[-1]["level"] + 1 if stack else 2)),
                "confidence": 0.94, "method": "arabic_outline_heading"}
    if outline and outline.group(3):
        return {"title": title, "level": min(6, max(3, stack[-1]["level"] + 1 if stack else 3)),
                "confidence": 0.92, "method": "letter_outline_heading"}
    if stack and line["font_size"] < stack[-1]["font_size"] * 0.93:
        level = min(6, stack[-1]["level"] + 1)
        confidence = 0.72
    else:
        level = 1
        confidence = 0.62
    return {"title": title, "level": level, "confidence": confidence, "method": "visual_style_inference"}


def read_pdf(path: Path, document_id: str, repair_visual_spacing: bool = False) -> list[dict]:
    pages = []
    with fitz.open(path) as pdf:
        if pdf.needs_pass and not pdf.authenticate(""):
            raise ValueError("password_required")
        for i, page in enumerate(pdf):
            out = {"document_id": document_id, "page_index": i, "width": page.rect.width,
                   "height": page.rect.height, "lines": [], "raw_text": "", "extraction_error": None}
            try:
                out["layout_regions"], drawings = detect_visual_regions(page)
                mode = "rawdict" if repair_visual_spacing else "dict"
                flags = (fitz.TEXTFLAGS_RAWDICT if repair_visual_spacing else fitz.TEXTFLAGS_DICT) & ~fitz.TEXT_PRESERVE_IMAGES
                data = page.get_text(mode, flags=flags, sort=True)
                reference_space = page_space_width(data) if repair_visual_spacing else None
                parts, source_parts, cursor = [], [], 0
                for block_i, block in enumerate(data["blocks"]):
                    if block.get("type") != 0:
                        continue
                    for line in block.get("lines", []):
                        spans = line.get("spans", [])
                        if repair_visual_spacing:
                            text, source_text, spacing_edits = recover_line(line, reference_space)
                        else:
                            text = "".join(s["text"] for s in spans)
                            source_text, spacing_edits = text, []
                        if not text.strip():
                            continue
                        if parts:
                            parts.append("\n"); source_parts.append("\n"); cursor += 1
                        start = cursor
                        parts.append(text); source_parts.append(source_text); cursor += len(text)
                        record = {"line_index": len(out["lines"]), "block_index": block_i,
                            "text": text, "source_start": start, "source_end": cursor,
                            "bbox": list(line["bbox"]), "font_size": statistics.median(s["size"] for s in spans),
                            "bold": any(s.get("flags", 0) & 16 for s in spans),
                            "fonts": sorted({s["font"] for s in spans}), "page_index": i}
                        if spacing_edits:
                            record["text_layer_text"] = source_text
                            record["visual_spacing_insertions"] = spacing_edits
                        out["lines"].append(record)
                # Do not hard-mask prose using text alignment alone. PyMuPDF
                # can expose a normal text line as many overlapping glyph
                # fragments; that heuristic falsely labelled whole paragraphs
                # as borderless tables in the source corpus. Table exclusion
                # therefore uses actual drawn-grid geometry; suspected cases
                # stay visible to page/chunk review and can be added later with
                # a word-level, visually validated detector.
                out["layout_regions"].extend(vector_figure_regions(drawings, page.rect, out["layout_regions"]))
                for text_line in out["lines"]:
                    text_line["layout_flags"] = visual_region_flags(text_line["bbox"], out["layout_regions"])
                out["raw_text"] = "".join(parts)
                if repair_visual_spacing:
                    out["text_layer_raw_sha256"] = sha("".join(source_parts))
                    out["visual_spacing_insertion_count"] = sum(
                        len(line.get("visual_spacing_insertions", [])) for line in out["lines"])
            except Exception as exc:
                out["extraction_error"] = type(exc).__name__
                out["lines"] = []
                out["layout_regions"] = []
            out["raw_sha256"] = sha(out["raw_text"])
            pages.append(out)
    return pages


def _rect_area(rect: list[float]) -> float:
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])


def _intersection_area(left: list[float], right: list[float]) -> float:
    width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    return width * height


def detect_visual_regions(page) -> tuple[list[dict], list[dict]]:
    """Find table boxes and substantial embedded figures without reading pixels."""
    regions = []
    page_area = max(1.0, float(page.rect.width * page.rect.height))
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    if _has_drawn_table_grid(drawings, page.rect):
        finder = getattr(page, "find_tables", None)
        if finder:
            try:
                tables = finder(strategy="lines_strict").tables
            except Exception:
                tables = []
            for table in tables:
                try:
                    bbox = [float(value) for value in table.bbox]
                except (TypeError, ValueError):
                    continue
                if len(bbox) == 4 and _rect_area(bbox) >= page_area * 0.003:
                    regions.append({"kind": "table", "method": "pymupdf_find_tables_lines_strict", "bbox": bbox})

    try:
        image_infos = page.get_image_info()
    except Exception:
        image_infos = []
    for image in image_infos:
        try:
            bbox = [float(value) for value in image["bbox"]]
        except (KeyError, TypeError, ValueError):
            continue
        # Ignore logos and small decorative icons. OCR-only pages have no
        # extracted prose lines and remain covered by the existing empty-page gate.
        if len(bbox) == 4 and _rect_area(bbox) >= page_area * 0.015:
            regions.append({"kind": "figure", "method": "pymupdf_image_bbox", "bbox": bbox})
    return regions, drawings


def _drawing_segments(drawings: list[dict]) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    horizontal, vertical = [], []
    for drawing in drawings:
        for item in drawing.get("items", []):
            op = item[0]
            if op == "l":
                a, b = item[1], item[2]
                x0, x1 = sorted((float(a.x), float(b.x)))
                y0, y1 = sorted((float(a.y), float(b.y)))
                if abs(y1 - y0) <= 2:
                    horizontal.append(((y0 + y1) / 2, x0, x1))
                elif abs(x1 - x0) <= 2:
                    vertical.append(((x0 + x1) / 2, y0, y1))
            elif op == "re":
                rect = item[1]
                x0, y0, x1, y1 = map(float, rect)
                if y1 - y0 <= 3:
                    horizontal.append(((y0 + y1) / 2, x0, x1))
                elif x1 - x0 <= 3:
                    vertical.append(((x0 + x1) / 2, y0, y1))
    return horizontal, vertical


def _has_drawn_table_grid(drawings: list[dict], page_rect) -> bool:
    horizontal, vertical = _drawing_segments(drawings)
    min_h = max(24.0, float(page_rect.width) * 0.12)
    min_v = max(12.0, float(page_rect.height) * 0.018)
    horizontal = [line for line in horizontal if line[2] - line[1] >= min_h]
    vertical = [line for line in vertical if line[2] - line[1] >= min_v]
    intersections = set()
    for y, x0, x1 in horizontal:
        for x, y0, y1 in vertical:
            if x0 - 2 <= x <= x1 + 2 and y0 - 2 <= y <= y1 + 2:
                intersections.add((round(x / 3), round(y / 3)))
    return len(intersections) >= 4


def text_aligned_table_regions(lines: list[dict], page_rect) -> list[dict]:
    """Catch borderless tables with repeated text columns across several rows."""
    if len(lines) < 6:
        return []
    heights = [max(1.0, line["bbox"][3] - line["bbox"][1]) for line in lines]
    row_height = max(3.0, statistics.median(heights))
    rows: list[list[dict]] = []
    tolerance = max(2.0, row_height * 0.28)
    for line in sorted(lines, key=lambda item: (item["bbox"][1] + item["bbox"][3]) / 2):
        center_y = (line["bbox"][1] + line["bbox"][3]) / 2
        if not rows:
            rows.append([line])
            continue
        previous_y = sum((item["bbox"][1] + item["bbox"][3]) / 2 for item in rows[-1]) / len(rows[-1])
        if abs(center_y - previous_y) <= tolerance:
            rows[-1].append(line)
        else:
            rows.append([line])
    aligned_rows = []
    for row in rows:
        if len(row) < 2:
            continue
        starts = []
        for line in sorted(row, key=lambda item: item["bbox"][0]):
            x = line["bbox"][0]
            if not starts or x - starts[-1][0] > 14:
                starts.append([x, [line]])
            else:
                starts[-1][1].append(line)
        if len(starts) >= 2:
            aligned_rows.append(starts)
    if len(aligned_rows) < 3:
        return []
    common_columns = []
    for row in aligned_rows:
        for x, members in row:
            match = next((col for col in common_columns if abs(col[0] - x) <= 16), None)
            if match is None:
                common_columns.append([x, 1, list(members)])
            else:
                match[1] += 1
                match[2].extend(members)
    columns = [column for column in common_columns if column[1] >= 3]
    if len(columns) < 2:
        return []
    members = list({line["line_index"]: line for col in columns for line in col[2]}.values())
    bbox = [min(line["bbox"][0] for line in members), min(line["bbox"][1] for line in members),
            max(line["bbox"][2] for line in members), max(line["bbox"][3] for line in members)]
    if _rect_area(bbox) < float(page_rect.width * page_rect.height) * 0.003:
        return []
    return [{"kind": "table", "method": "repeated_text_column_alignment", "bbox": bbox}]


def _is_page_header_footer_rule_path(drawing: dict, page_rect) -> bool:
    """Ignore a path made only of long rules above and below page text.

    Some thesis PDFs store both rules in one PDF drawing path. PyMuPDF reports
    that path's rectangle as the union from the top rule to the bottom rule;
    treating the union as a vector figure then masks every prose line between
    them. These disconnected horizontal rules carry no figure content.
    """
    items = drawing.get("items", [])
    if not 2 <= len(items) <= 4 or not page_rect.width or not page_rect.height:
        return False
    segments = []
    for item in items:
        if item[0] != "l":
            return False
        a, b = item[1], item[2]
        if abs(float(a.y) - float(b.y)) > 2.0:
            return False
        x0, x1 = sorted((float(a.x), float(b.x)))
        if x1 - x0 < float(page_rect.width) * 0.60:
            return False
        segments.append((x0, x1, (float(a.y) + float(b.y)) / 2))
    if len(segments) < 2:
        return False
    top = min(segment[2] for segment in segments)
    bottom = max(segment[2] for segment in segments)
    return (
        bottom - top >= float(page_rect.height) * 0.50
        and top <= float(page_rect.height) * 0.12
        and bottom >= float(page_rect.height) * 0.85
    )


def vector_figure_regions(drawings: list[dict], page_rect, existing: list[dict]) -> list[dict]:
    """Group nearby vector marks into figure areas, excluding page frames/tables."""
    candidates = []
    page_area = max(1.0, float(page_rect.width * page_rect.height))
    for drawing in drawings:
        if _is_page_header_footer_rule_path(drawing, page_rect):
            continue
        rect = drawing.get("rect")
        if rect is None:
            continue
        bbox = [float(value) for value in rect]
        width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
        near_page_frame = (bbox[0] <= page_rect.width * 0.04 and bbox[1] <= page_rect.height * 0.04
                           and bbox[2] >= page_rect.width * 0.96 and bbox[3] >= page_rect.height * 0.96)
        if (width >= page_rect.width * 0.08 and height >= page_rect.height * 0.06
                and _rect_area(bbox) < page_area * 0.85 and not near_page_frame):
            candidates.append(bbox)
    groups = []
    for bbox in candidates:
        touching = [g for g in groups if bbox[0] <= g[2] + 8 and bbox[2] >= g[0] - 8
                    and bbox[1] <= g[3] + 8 and bbox[3] >= g[1] - 8]
        if not touching:
            groups.append(bbox[:])
            continue
        merged = [min([bbox[0], *(g[0] for g in touching)]), min([bbox[1], *(g[1] for g in touching)]),
                  max([bbox[2], *(g[2] for g in touching)]), max([bbox[3], *(g[3] for g in touching)])]
        groups = [g for g in groups if g not in touching]
        groups.append(merged)
    result = []
    for bbox in groups:
        if _rect_area(bbox) < page_area * 0.02 or any(_intersection_area(bbox, region["bbox"]) / max(1.0, _rect_area(bbox)) >= 0.8
                                                       for region in existing if region["kind"] == "table"):
            continue
        result.append({"kind": "figure", "method": "grouped_vector_graphics", "bbox": bbox})
    return result


def visual_region_flags(line_bbox: list[float], regions: list[dict]) -> list[str]:
    line_area = max(1.0, _rect_area(line_bbox))
    flags = set()
    for region in regions:
        overlap = _intersection_area(line_bbox, region["bbox"])
        center_x = (line_bbox[0] + line_bbox[2]) / 2
        center_y = (line_bbox[1] + line_bbox[3]) / 2
        box = region["bbox"]
        center_inside = box[0] <= center_x <= box[2] and box[1] <= center_y <= box[3]
        if region["kind"] == "table" and (center_inside or overlap / line_area >= 0.2):
            flags.add("table_region")
        elif region["kind"] == "figure" and center_inside and overlap / line_area >= 0.35:
            flags.add("figure_region")
    return sorted(flags)


def body_bounds(pages: list[dict]) -> dict:
    n = len(pages)
    toc = {p["page_index"] for p in pages[:max(12, min(25, n // 3))]
           if sum(bool(re.search(r"\.{3,}\s*\d+", l["text"])) for l in p["lines"]) >= 3
           or any(boundary_fold(l["text"]) == "muc luc" for l in p["lines"])}
    candidates = [(p["page_index"], l["line_index"]) for p in pages[:max(15, n // 3)]
                  for l in p["lines"] if p["page_index"] not in toc
                  and len(l["text"]) < 180 and START.fullmatch(boundary_fold(l["text"]))
                  and not re.search(r"\.{3,}|\s\d+\s*$", l["text"])]
    after_toc = [x for x in candidates if x[0] > max(toc, default=-1)]
    before_toc_body = [x for x in candidates
                       if x[0] < min(toc, default=n)
                       and _heading_has_prose_after(pages, x)]
    start = min(before_toc_body or after_toc or candidates, default=(0, 0))
    ends = []
    for page in pages:
        if page["page_index"] < max(start[0] + 2, int(n * .5)):
            continue
        for line in page["lines"]:
            text = boundary_fold(line["text"])
            # Some repository PDFs decorate the otherwise exact back-matter
            # title with a leading bullet or surrounding asterisks.
            heading_text = re.sub(r"^[\s\uf0b7\u2022\u25cf\u25cb\u25aa\u25e6*]+", "", text)
            heading_text = re.sub(r"[\s*]+$", "", heading_text)
            if len(line["text"]) >= 130 or re.search(r"\.{3,}", line["text"]):
                continue
            # Reference headings must occupy the line; a prefix-only match can
            # mistake SQL such as "REFERENCES TABLE_NAME" for back matter.
            if (REFERENCE_HEADING.fullmatch(heading_text)
                    or BARE_REFERENCE_HEADING.fullmatch(heading_text)
                    or APPENDIX_HEADING.match(heading_text)):
                ends.append((page["page_index"], line["line_index"]))
    end = min(ends, default=(n, 0))
    return {"start": list(start), "end_exclusive": list(end),
            "start_detected": bool(candidates), "end_detected": bool(ends),
            "toc_pages": sorted(toc), "requires_review": not candidates or not ends}


LIST_SYMBOLS = ("¾", "➢", "➤", "►", "•", "●", "▪", "▫", "◦", "○", "◆", "◇", "■", "□", "☐", "‣", "⁃", "\uf0b7", "\uf02d")
STANDALONE_LIST_MARKERS = set(LIST_SYMBOLS) | {"-", "–", "—", "−", "+", "*", "o", "O"}


def list_item_line_reason(line: dict, page: dict) -> str | None:
    """Identify list text even when its marker is a separate PDF text span.

    Some PDFs extract a dash or plus as its own line at the left margin while
    the item text begins a few points to its right. Pairing same-baseline spans
    catches those items without treating every dash inside prose as a bullet.
    """
    text = normalized(line.get("text", "")).strip()
    if not text:
        return None
    left = text.lstrip()
    if any(left.startswith(marker) and len(left) > len(marker)
           and left[len(marker)].isspace() for marker in LIST_SYMBOLS):
        return "list_item"
    if left.startswith(("o ", "O ")) and left[2:3].isupper():
        return "list_item"

    # An isolated marker belongs to a visible item only when another text
    # span starts on the same baseline immediately to its right.
    marker = left.rstrip()
    if marker not in STANDALONE_LIST_MARKERS and not re.fullmatch(r"(?:\d{1,3}[.)]|[A-Za-z][.)]|[ivxlcdm]{1,6}[.)])", marker, re.I):
        for prefix in ("- ", "– ", "— ", "− ", "+ ", "* "):
            if left.startswith(prefix):
                return "list_item"
        return None

    box = line.get("bbox") or []
    if len(box) != 4:
        return None
    for other in page.get("lines", []):
        if other.get("source_start") == line.get("source_start") and other.get("source_end") == line.get("source_end"):
            continue
        other_text = normalized(other.get("text", "")).strip()
        other_box = other.get("bbox") or []
        if (other_text and len(other_box) == 4
                and abs(float(other_box[1]) - float(box[1])) <= 2.2
                and -2.0 <= float(other_box[0]) - float(box[2]) <= 36.0):
            return "list_item"
    return None


def wrapped_list_lines(lines: list[dict], page: dict, median_font: float) -> set[int]:
    """Exclude text wrapped under a visible list marker on the PDF page.

    PDF text layers often put the marker and every following visual line in
    separate blocks. A line is a continuation only while it keeps the item's
    hanging indent and ordinary line spacing. A larger paragraph gap ends it.
    """
    ordered = sorted(lines, key=lambda item: (round(item["bbox"][1], 1), item["bbox"][0]))
    gaps = []
    for left, right in zip(ordered, ordered[1:]):
        gap = right["bbox"][1] - left["bbox"][3]
        if (0 <= gap <= median_font * 1.3
                and abs(right["bbox"][0] - left["bbox"][0]) <= 35
                and abs(right["font_size"] - left["font_size"]) <= 1.5):
            gaps.append(gap)
    normal_gap = statistics.median(gaps) if gaps else median_font * .45
    max_gap = min(median_font * 1.15, max(3.0, normal_gap * 1.35))
    result: set[int] = set()
    anchor = previous = None
    for index, line in enumerate(ordered):
        if list_item_line_reason(line, page):
            # A split section number such as "1." next to a bold or all-cap
            # title is a heading, not the start of a numbered list item.
            if re.fullmatch(r"\d{1,3}[.)]", normalized(line["text"]).strip()) and index + 1 < len(ordered):
                following = ordered[index + 1]
                title = normalized(following["text"])
                letters = [char for char in title if char.isalpha()]
                same_heading_line = (abs(following["bbox"][1] - line["bbox"][1]) <= 2.2
                                     and 0 <= following["bbox"][0] - line["bbox"][2] <= 36)
                title_style = (following.get("bold", False)
                               or (letters and sum(char.isupper() for char in letters) / len(letters) >= .8))
                if same_heading_line and len(title) < 180 and title_style:
                    anchor = previous = None
                    continue
            anchor = previous = line
            continue
        if previous is None or anchor is None:
            continue
        box, previous_box, anchor_box = line["bbox"], previous["bbox"], anchor["bbox"]
        gap = box[1] - previous_box[3]
        same_baseline = abs(box[1] - previous_box[1]) <= 2.2 and box[0] >= previous_box[2] - 2
        hanging_indent = box[0] >= anchor_box[0] + 5 and box[0] <= anchor_box[0] + 55
        # Wingdings/private-use markers can make the first visual line's
        # median font much smaller than its body text.
        font_compatible = (previous is anchor
                           or abs(line["font_size"] - previous["font_size"]) <= 1.5)
        ordinary_wrap = 0 <= gap <= max_gap and hanging_indent and font_compatible
        if same_baseline or ordinary_wrap:
            result.add(line["line_index"])
            previous = line
        else:
            anchor = previous = None
    return result


def line_reason(line: dict, page: dict, median_font: float, repeated: set[str]) -> str | None:
    text = normalized(line["text"])
    key = fold(text)
    box = line["bbox"]
    layout_flags = set(line.get("layout_flags", []))
    if "table_region" in layout_flags:
        return "table_region"
    if "figure_region" in layout_flags:
        return "figure_region"
    if list_item_line_reason(line, page):
        return "list_item"
    if unsafe_reason(text):
        return unsafe_reason(text)
    if fingerprint(text) in repeated and (box[1] < page["height"] * .15 or box[3] > page["height"] * .86):
        return "running_margin"
    if re.fullmatch(r"[\W_]*\d+(?:\.\d+)*[\W_]*", text):
        return "standalone_number"
    if re.fullmatch(r"[xX×✓✔\s|+\-]+", text):
        return "table_cell"
    if box[1] > page["height"] * .95 or box[3] < page["height"] * .045:
        return "page_margin"
    if line["font_size"] < median_font * .84 and (box[1] > page["height"] * .68 or re.match(r"^\d+\s*[:.)]", text)):
        return "footnote_or_small_annotation"
    if re.match(r"^(?:hinh|bang|figure|table)\s+[\dIVXLCDM]+(?:[-–—.]\d+)*(?:[.:)\s]|$)", key, re.I):
        return "caption"
    if FRONT.match(key) or END.match(key):
        return "non_body_heading"
    # Dot leaders with a terminal page number are TOC rows even when their
    # prefix resembles a numbered heading. Classify them before heading rules.
    if re.search(r"\.{4,}\s*\d+\s*$", text):
        return "table_of_contents"
    letters = [c for c in text if c.isalpha()]
    words = len(text.split())
    upper_ratio = sum(c.isupper() for c in letters) / max(1, len(letters))
    chapter = re.match(r"^(?:chuong|phan)\s+[\dIVX]+", key, re.I)
    numbered = NUMBER_HEADING.match(text)
    outline = OUTLINE_HEADING.match(text)
    if chapter and (PROSE_CHAPTER.match(key) or text.rstrip().endswith((".", "!", "?", ";"))):
        return None
    if len(text) < 180 and chapter:
        return "heading"
    if len(text) < 180 and (numbered or outline) and words < 18:
        if outline and not line["bold"] and line["font_size"] < median_font * 1.08 and upper_ratio < .85:
            return "heading_review"
        return "heading" if len(letters) >= 5 else "heading_review"
    short_candidate = words <= 14 and len(letters) >= 5 and not text.rstrip().endswith((".", "!", "?", ";"))
    style_signal = line["bold"] or upper_ratio > .85 or line["font_size"] >= median_font * 1.18
    if len(text) < 180 and short_candidate and style_signal:
        first_letter = next((c for c in text if c.isalpha()), "")
        centered_top_title = (upper_ratio > .85 and box[1] < page["height"] * .22
                              and abs((box[0] + box[2]) / 2 - page["width"] / 2) < page["width"] * .12)
        strong_style = (len(letters) >= 8 and first_letter.isupper()
                        and ((line["font_size"] >= median_font * 1.08 and (line["bold"] or upper_ratio > .85))
                             or centered_top_title))
        return "heading" if strong_style else "heading_review"
    # Numbered/lettered list entries can look like headings. Preserve strong
    # headings above, then keep remaining list entries out of prose.
    if re.match(r"^\s*(?:\d{1,3}[.)]|[A-Za-z][.)]|[ivxlcdm]{1,6}[.)])\s+", text, re.I):
        return "list_item"
    if re.search(r"(?:\b(?:public|private|void|return)\b.*[;{}]|</?[a-z]+>|^\s*(?:SELECT|INSERT|CREATE TABLE)\b)", text):
        return "code"
    if not letters:
        return "nonlinguistic"
    return None


def merge_overlapped_fragments(fragments: list[dict]) -> str:
    """Reconstruct a visual line from PDF text fragments with overlapping glyphs.

    Only repeated suffix/prefix characters are collapsed when the glyph boxes
    overlap. Every source fragment remains separately addressable in lineage.
    """
    if not fragments:
        return ""
    result = normalized(fragments[0]["text"])
    previous = fragments[0]
    for current in fragments[1:]:
        text = normalized(current["text"])
        x_overlap = previous["bbox"][2] - current["bbox"][0]
        overlap = 0
        if x_overlap > 0:
            for size in range(min(3, len(result), len(text)), 0, -1):
                if result[-size:].casefold() == text[:size].casefold():
                    overlap = size
                    break
        if overlap:
            result += text[overlap:]
        else:
            result = normalized(result + " " + text)
        previous = current
    return normalized(result)


def visual_heading_groups(lines: list[dict], page: dict, median_font: float, repeated: set[str]) -> dict[int, dict]:
    """Find split, numbered headings where PDF text boxes overlap on one baseline."""
    by_source = sorted(lines, key=lambda item: item["source_start"])
    groups: dict[int, dict] = {}
    i = 0
    while i < len(by_source):
        members = [by_source[i]]
        j = i + 1
        while j < len(by_source) and len(members) < 10:
            left, right = members[-1], by_source[j]
            gap = right["source_start"] - left["source_end"]
            same_baseline = abs((right["bbox"][1] + right["bbox"][3] - left["bbox"][1] - left["bbox"][3]) / 2) <= max(1.5, median_font * .4)
            overlaps_x = right["bbox"][0] < left["bbox"][2] - 0.5
            if gap not in (0, 1) or not same_baseline or not overlaps_x:
                break
            members.append(right)
            j += 1
        if len(members) > 1:
            title = merge_overlapped_fragments(members)
            combined = {**members[0], "text": title,
                        "source_start": members[0]["source_start"], "source_end": members[-1]["source_end"],
                        "bbox": [min(x["bbox"][0] for x in members), min(x["bbox"][1] for x in members),
                                 max(x["bbox"][2] for x in members), max(x["bbox"][3] for x in members)],
                        "font_size": statistics.median(x["font_size"] for x in members),
                        "bold": any(x["bold"] for x in members), "visual_fragments": members}
            if line_reason(combined, page, median_font, repeated) == "heading":
                groups[members[0]["line_index"]] = combined
                i += len(members)
                continue
        i += 1
    return groups


def reconstruct(pages: list[dict], document_id: str, bounds: dict):
    margin_keys = Counter()
    for page in pages:
        keys = {fingerprint(l["text"]) for l in page["lines"]
                if l["bbox"][1] < page["height"] * .15 or l["bbox"][3] > page["height"] * .86}
        margin_keys.update(keys)
    repeated = {k for k, v in margin_keys.items() if v >= max(3, len(pages) * .12)}
    section = ident("sec3", document_id, "body")
    heading_stack: list[dict] = []
    heading_path: list[dict] = []
    run = 0
    paragraphs, excluded, transforms = [], [], []
    pending = []

    def flush():
        nonlocal pending
        if not pending:
            return
        pid = ident("par3", document_id, [(x["page_index"], x["source_start"], x["source_end"]) for x in pending])
        parts = [pending[0]["text"]]
        url_joins = []
        for previous, current in zip(pending, pending[1:]):
            # The PDF can wrap inside a URL; ordinary whitespace normalization
            # otherwise corrupts the link (for example, .../huggingface/\ntransformers).
            join_url = (previous["page_index"] == current["page_index"]
                        and re.search(r"https?://[^\s]+/$", previous["text"].rstrip(), re.I)
                        and re.match(r"^[a-z0-9][a-zA-Z0-9_.~%/-]*(?:\s|$)", current["text"].lstrip()))
            parts.append("" if join_url else "\n")
            parts.append(current["text"])
            if join_url:
                url_joins.append((previous, current))
        text = normalized("".join(parts))
        spans, tids = [], []
        for previous, current in url_joins:
            tid = ident("norm3", document_id, previous["page_index"],
                        previous["source_start"], current["source_end"], "join_wrapped_url")
            transforms.append({"transformation_id": tid, "document_id": document_id,
                               "paragraph_id": pid, "operation": "join_wrapped_url",
                               "source_spans": [{k: line[k] for k in ("page_index", "source_start", "source_end", "bbox")}
                                                for line in (previous, current)],
                               "before_sha256": sha(previous["text"] + "\n" + current["text"]),
                               "after_sha256": sha(previous["text"] + current["text"])})
            tids.append(tid)
        for line in pending:
            span = {k: line[k] for k in ("page_index", "source_start", "source_end", "bbox")}
            spans.append(span)
            if line.get("visual_spacing_insertions"):
                tid = ident("norm3", document_id, line["page_index"], line["source_start"],
                            "visual_spacing", sha(line["text"]))
                transforms.append({"transformation_id": tid, "document_id": document_id,
                                   "paragraph_id": pid, "operation": "insert_visual_space_from_glyph_gap",
                                   "source_span": span,
                                   "before_sha256": sha(line["text_layer_text"]),
                                   "after_sha256": sha(line["text"]),
                                   "insertions": line["visual_spacing_insertions"]})
                tids.append(tid)
            if line["text"] != normalized(line["text"]):
                tid = ident("norm3", document_id, line["page_index"], line["source_start"], sha(line["text"]))
                transforms.append({"transformation_id": tid, "document_id": document_id, "paragraph_id": pid,
                                   "operation": "nfc_whitespace", "source_span": span,
                                   "before_sha256": sha(line["text"]), "after_sha256": sha(normalized(line["text"]))})
                tids.append(tid)
        paragraphs.append({"paragraph_id": pid, "document_id": document_id, "section_id": section,
            "heading_path": heading_path, "section_title": heading_path[-1]["title"] if heading_path else None,
            "run_id": ident("run3", document_id, run), "ordinal": len(paragraphs), "text": text,
            "text_sha256": sha(text), "source_spans": spans, "transformation_ids": tids,
            "join_operation": "wrapped_url_and_line_order_whitespace" if url_joins else "line_order_whitespace",
            "content_type": "prose_candidate"})
        pending = []

    for page in pages:
        source_lines = page["lines"]
        lines = sorted(source_lines, key=lambda x: (round(x["bbox"][1], 1), x["bbox"][0], x["line_index"]))
        median = statistics.median(l["font_size"] for l in lines) if lines else 12
        visual_groups = visual_heading_groups(source_lines, page, median, repeated)
        consumed = {fragment["line_index"] for group in visual_groups.values()
                    for fragment in group["visual_fragments"]}
        process_lines = [group for group in visual_groups.values()]
        process_lines.extend(line for line in source_lines if line["line_index"] not in consumed)
        process_lines.sort(key=lambda x: (round(x["bbox"][1], 1), x["bbox"][0], x["line_index"]))
        list_continuations = wrapped_list_lines(source_lines, page, median)
        for line in process_lines:
            pos = (line["page_index"], line["line_index"])
            reason = ("outside_body" if not tuple(bounds["start"]) <= pos < tuple(bounds["end_exclusive"])
                      else "list_item_continuation" if line["line_index"] in list_continuations
                      else line_reason(line, page, median, repeated))
            if reason:
                # Headers/footers are outside the logical prose flow; paragraph
                # continuity is still separately checked by geometry below.
                margin_number = reason == "standalone_number" and (line["bbox"][1] < page["height"] * .12 or line["bbox"][3] > page["height"] * .88)
                if reason not in {"running_margin", "page_margin", "footnote_or_small_annotation"} and not margin_number:
                    flush(); run += 1
                heading_record = None
                if reason == "heading":
                    descriptor = heading_descriptor(line, median, heading_stack)
                    level = descriptor["level"]
                    heading_stack = [h for h in heading_stack if h["level"] < level]
                    heading_id = ident("head31", document_id, line["page_index"], line["source_start"], sha(normalized(line["text"])))
                    heading_record = {"heading_id": heading_id, "title": descriptor["title"], "level": level,
                        "confidence": descriptor["confidence"], "method": descriptor["method"],
                        "page_index": line["page_index"], "source_start": line["source_start"],
                        "source_end": (line["visual_fragments"][0]["source_end"]
                                       if line.get("visual_fragments") else line["source_end"]),
                        "bbox": line["bbox"], "font_size": line["font_size"]}
                    if line.get("visual_fragments"):
                        fragments = line["visual_fragments"]
                        heading_record["source_fragments"] = [{
                            "page_index": x["page_index"], "source_start": x["source_start"],
                            "source_end": x["source_end"], "bbox": x["bbox"],
                            "raw_sha256": sha(x["text"])} for x in fragments]
                        heading_record["method"] = "numbered_heading_visual_fragments"
                        heading_record["confidence"] = min(descriptor["confidence"], 0.92)
                    heading_stack.append(heading_record)
                    heading_path = [{k: h[k] for k in ("heading_id", "title", "level", "confidence", "method",
                                                         "page_index", "source_start", "source_end", "bbox")}
                                    for h in heading_stack]
                    section = ident("sec3", document_id, [h["heading_id"] for h in heading_stack])
                elif reason == "non_body_heading" and FRONT.match(fold(normalized(line["text"]))):
                    heading_stack = []
                    heading_path = []
                    section = ident("sec3", document_id, "body")
                if line.get("visual_fragments") and reason == "heading":
                    for i, fragment in enumerate(line["visual_fragments"]):
                        excluded.append({"document_id": document_id, "page_index": fragment["page_index"],
                            "source_start": fragment["source_start"], "source_end": fragment["source_end"],
                            "bbox": fragment["bbox"], "reason": "heading", "raw_sha256": sha(fragment["text"]),
                            "content_type": "heading" if i == 0 else "heading_fragment",
                            **(heading_record if i == 0 else {})})
                else:
                    excluded.append({"document_id": document_id, "page_index": line["page_index"],
                        "source_start": line["source_start"], "source_end": line["source_end"],
                        "bbox": line["bbox"], "reason": reason, "raw_sha256": sha(line["text"]),
                        "content_type": "heading" if reason == "heading" else "ambiguous_heading" if reason == "heading_review" else "excluded_region",
                        **(heading_record or {})})
                continue
            if pending:
                last = pending[-1]
                same_page = line["page_index"] == last["page_index"]
                vertical = line["bbox"][1] - last["bbox"][3]
                same_block = same_page and line["block_index"] == last["block_index"]
                same_column = abs(line["bbox"][0] - last["bbox"][0]) <= 32
                horizontal_line = same_page and abs(line["bbox"][1] - last["bbox"][1]) < median * .4
                continuation = (not normalized(last["text"]).endswith((".", "!", "?", "…", ":", ";"))
                                and line["text"].lstrip()[:1].islower())
                visual_wrap = (not normalized(last["text"]).endswith((".", "!", "?", "…", ":", ";"))
                               and last["bbox"][2] > page["width"] * .78
                               and abs(line["font_size"] - last["font_size"]) < 1)
                previous_page = pages[last["page_index"]]
                across = (line["page_index"] == last["page_index"] + 1 and continuation and same_column
                          and last["bbox"][3] > previous_page["height"] * .75
                          and line["bbox"][1] < page["height"] * .23)
                within = (same_page and same_column and not horizontal_line and vertical < median * 1.5
                          and (same_block or continuation or visual_wrap))
                if not (within or across):
                    flush()
                    # A region discontinuity prevents later passage packing
                    # from joining across the missing/ambiguous context.
                    if not same_page or not same_column or horizontal_line or vertical > median * 2.5:
                        run += 1
            pending.append(line)
    flush()
    return paragraphs, excluded, transforms


def process_source(job: dict) -> dict:
    """Worker writes one checkpoint shard, never a final release or old corpus."""
    path, root = Path(job["path"]), Path(job["shard"])
    root.mkdir(parents=True, exist_ok=True)
    did = job["document_id"]
    if (root / "done.json").exists():
        done = json.loads((root / "done.json").read_text(encoding="utf-8"))
        if done["job_hash"] == job["job_hash"]:
            return done
        raise ValueError(f"stale checkpoint: {root}")
    try:
        pages = read_pdf(path, did, repair_visual_spacing=job.get("repair_visual_spacing", False))
    except Exception as exc:
        done = {"document_id": did, "job_hash": job["job_hash"], "error": type(exc).__name__, "page_count": 0}
        dump(root / "done.json", done)
        return done
    with JsonlWriter(root / "pages.jsonl.gz") as w:
        for p in pages:
            w.write(p)
    bounds = body_bounds(pages)
    paragraphs, excluded, transforms = reconstruct(pages, did, bounds)
    sentences, cursor = [], 0
    for p in paragraphs:
        p["document_char_start"] = cursor
        p["document_char_end"] = cursor + len(p["text"])
        cursor = p["document_char_end"] + 2
        for a, b in sentence_bounds(p["text"]):
            text = p["text"][a:b]
            reason = sentence_reason(text)
            sentences.append({"sentence_id": ident("sent3", p["paragraph_id"], a, b, sha(text)),
                "document_id": did, "paragraph_id": p["paragraph_id"], "section_id": p["section_id"],
                "heading_path": p.get("heading_path", []), "section_title": p.get("section_title"),
                "run_id": p["run_id"], "ordinal": len(sentences), "text": text, "text_sha256": sha(text),
                "paragraph_char_start": a, "paragraph_char_end": b,
                "document_char_start": p["document_char_start"] + a, "document_char_end": p["document_char_start"] + b,
                "approx_tokens": approx_tokens(text), "sentence_complete": reason is None,
                "extraction_truncated": reason in {"extraction_truncated", "orphan_continuation"},
                "model_fragment": False, "review_reason": reason, "source_spans": p["source_spans"],
                "transformation_ids": p["transformation_ids"], "label": "H", "content_type": "prose" if reason is None else "review"})
    groups, tails = pack_sentences(sentences, job["config"])
    passages = []
    for group in groups:
        text = " ".join(s["text"] for s in group)
        spans = list({(x["page_index"], x["source_start"], x["source_end"]): x for s in group for x in s["source_spans"]}.values())
        passages.append({"schema_version": "human-v3.0", "passage_id": ident("pass3", did, [s["sentence_id"] for s in group]),
            "document_id": did, "section_id": group[0]["section_id"], "run_id": group[0]["run_id"],
            "heading_path": group[0].get("heading_path", []),
            "heading_path_text": [h["title"] for h in group[0].get("heading_path", [])],
            "sentence_ids": [s["sentence_id"] for s in group], "sentence_count": len(group),
            "text": text, "text_sha256": sha(text), "fingerprint_sha256": fingerprint(text),
            "approx_tokens": approx_tokens(text), "source_spans": spans,
            "source_pdf_sha256": job["source_sha256"], "label": "H",
            "document_char_spans": [[s["document_char_start"], s["document_char_end"]] for s in group],
            "transformation_ids": list(dict.fromkeys(t for s in group for t in s["transformation_ids"])),
            "offset_unit": "unicode_code_points", "model_input_ready": False})
    headings = [r for r in excluded if r.get("reason") == "heading" and r.get("heading_id")]
    for name, data in [("paragraphs", paragraphs), ("sentences", sentences), ("passages", passages), ("headings", headings),
                       ("excluded_regions", excluded), ("transformations", transforms),
                       ("leftovers", [{"sentence_id": s["sentence_id"], "document_id": did, "reason": why} for s, why in tails])]:
        with JsonlWriter(root / (name + ".jsonl.gz")) as w:
            for r in data:
                w.write(r)
    done = {"document_id": did, "job_hash": job["job_hash"], "error": None, "page_count": len(pages),
            "extraction_method": "text_layer_glyph_gap_recovery" if job.get("repair_visual_spacing") else "text_layer",
            "visual_spacing_recovery": bool(job.get("repair_visual_spacing")),
            "visual_spacing_insertions": sum(p.get("visual_spacing_insertion_count", 0) for p in pages),
            "visual_spacing_pages": [p["page_index"] for p in pages if p.get("visual_spacing_insertion_count", 0)],
            "bounds": bounds, "paragraphs": len(paragraphs), "sentences": len(sentences), "headings": len(headings),
            "complete_sentences": sum(s["sentence_complete"] for s in sentences), "passages": len(passages),
            "low_confidence_headings": sum(h["confidence"] < 0.75 for h in headings),
            "ambiguous_heading_regions": sum(r["reason"] == "heading_review" for r in excluded),
            "extraction_error_pages": [p["page_index"] for p in pages if p["extraction_error"]],
            "empty_text_pages": [p["page_index"] for p in pages if not p["raw_text"].strip()],
            # Review every page where geometry found a visual region, even if
            # no extracted text box happened to overlap the detected region.
            "layout_review_pages": sorted({p["page_index"] for p in pages if p.get("layout_regions")}),
            "layout_page_types": {str(p["page_index"]): sorted({region["kind"] for region in p.get("layout_regions", [])})
                                  for p in pages if p.get("layout_regions")},
            "layout_region_counts": dict(Counter(region["kind"] for p in pages
                                                  for region in p.get("layout_regions", []))),
            "excluded_region_reasons": dict(Counter(r["reason"] for r in excluded)),
            "sentence_review_reasons": dict(Counter(s["review_reason"] for s in sentences if s["review_reason"]))}
    dump(root / "done.json", done)
    return done
