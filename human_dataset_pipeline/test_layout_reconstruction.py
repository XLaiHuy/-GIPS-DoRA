import json
import unittest
from pathlib import Path

import fitz

from layout_reconstruction import (
    LayoutConfig,
    TextLine,
    TextSpan,
    reconstruct_document_units,
    reconstruct_page_units,
)


FIXTURE_PATH = Path(__file__).with_name("testdata") / "layout_fixture_page49.json"


def fixture_lines():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    lines = []
    for line_index, row in enumerate(fixture["lines"]):
        text = row["text"]
        spans = tuple(TextSpan(
            text=span["text"], font_name=span["font_name"], font_size=span["font_size"],
            bbox=tuple(span["bbox"]), source_start=span["source_start"],
            source_end=span["source_end"], page_index=fixture["page_index"],
        ) for span in row["spans"])
        lines.append(TextLine(
            page_index=fixture["page_index"], line_index=line_index, bbox=tuple(row["bbox"]),
            text=text, spans=spans, source_start=row["source_start"], source_end=row["source_end"],
        ))
    return fixture, lines


def line(index, text, x0=72.0, y0=72.0, x1=300.0, y1=84.0, font="Times-Roman", size=12.0):
    return TextLine(
        page_index=0, line_index=index, bbox=(x0, y0, x1, y1), text=text,
        spans=(TextSpan(text, font, size, (x0, y0, x1, y1), index * 100, index * 100 + len(text)),),
        source_start=index * 100, source_end=index * 100 + len(text),
    )


def page_line(page_index, line_index, text, x0, y0):
    box = (x0, y0, 400.0, y0 + 12)
    return TextLine(page_index, line_index, box, text,
                    (TextSpan(text, "Times-Roman", 12, box, 0, len(text)),), 0, len(text))


def table_line(index, text, cell_x, y0):
    cells = text.split("\t") if "\t" in text else text.split("        ")
    if len(cells) == 2:
        cells = [cells[0].strip(), *cells[1].split("        ")]
    cells = [cell.strip() for cell in cells]
    boxes = [(x, y0, x + 20, y0 + 12) for x in cell_x]
    spans = tuple(TextSpan(cell, "Times-Roman", 12, box, index * 100 + i * 20,
                           index * 100 + i * 20 + len(cell))
                  for i, (cell, box) in enumerate(zip(cells, boxes)))
    return TextLine(0, index, (cell_x[0], y0, cell_x[-1] + 20, y0 + 12), text,
                    spans, index * 100, index * 100 + len(text))


class LayoutReconstructionTests(unittest.TestCase):
    def test_page49_fixture_emits_introduction_and_five_independent_bullets(self):
        """Removing bullet detection or continuation indentation must fail this test."""
        fixture, lines = fixture_lines()
        config = LayoutConfig(**fixture["config"])

        units = reconstruct_page_units(lines[2:], page_width=fixture["page_size"][0],
                                       page_height=fixture["page_size"][1], config=config)

        self.assertEqual(units[0].unit_type, "paragraph")
        self.assertIn("vực phát triển như:", units[0].text)
        self.assertEqual(sum(unit.unit_type == "bullet_item" for unit in units), 5)
        self.assertIn("hiệu Carillon", units[1].text)
        self.assertIn("khách hàng sẵn có", units[-1].text)
        self.assertTrue(all(
            not (left.unit_type == right.unit_type == "paragraph"
                 and left.page_start == right.page_start
                 and left.text.endswith("khách") and right.text.startswith("hàng"))
            for left, right in zip(units, units[1:])
        ))

    def test_page49_heading_stays_outside_introduction(self):
        """Merging a visual heading into the introduction must fail this test."""
        fixture, lines = fixture_lines()
        units = reconstruct_page_units(lines, 612, 792, LayoutConfig(**fixture["config"]))

        self.assertEqual(units[0].unit_type, "heading")
        self.assertEqual(units[0].text, "3.4 Lĩnh vực Bất động sản")
        self.assertEqual(units[1].unit_type, "paragraph")

    def test_fixture_source_spans_preserve_each_original_line_range(self):
        """Dropping source lineage while joining wrapped lines must fail this test."""
        fixture, lines = fixture_lines()
        units = reconstruct_page_units(lines, 612, 792, LayoutConfig(**fixture["config"]))

        spans = [span for unit in units for span in unit.source_spans]
        self.assertEqual([(span.page_index, span.source_start, span.source_end) for span in spans], [
            (row.page_index, row.source_start, row.source_end) for row in lines
        ])
        source = fixture["source_text"]
        self.assertEqual(fixture["source_pdf"], "Dataset_khoaluan/63617_VO VAN THUAN.pdf")
        self.assertEqual(fixture["source_page_number"], 49)
        source_pdf = FIXTURE_PATH.parent.parent.parent / fixture["source_pdf"]
        with fitz.open(source_pdf) as pdf:
            self.assertEqual(pdf[fixture["page_index"]].get_text("text"), source)
        for row in lines:
            self.assertEqual(source[row.source_start:row.source_end], row.text)
            for span in row.spans:
                self.assertEqual(span.page_index, fixture["page_index"])
                self.assertEqual(source[span.source_start:span.source_end], span.text)
        for unit in units:
            for source_span in unit.source_spans:
                self.assertEqual(source[source_span.source_start:source_span.source_end],
                                 next(row.text for row in lines if row.source_start == source_span.source_start))

    def test_two_columns_never_merge(self):
        """Ignoring x-column clusters must fail this test by joining adjacent columns."""
        lines = [
            line(0, "Trái một", 72, 72, 210, 84), line(1, "Phải một", 342, 72, 500, 84),
            line(2, "Trái hai", 72, 88, 210, 100), line(3, "Phải hai", 342, 88, 500, 100),
        ]
        units = reconstruct_page_units(lines, 612, 792, LayoutConfig())

        self.assertEqual([unit.text for unit in units], ["Trái một Trái hai", "Phải một Phải hai"])
        self.assertTrue(all("Trái" not in unit.text or "Phải" not in unit.text for unit in units))

    def test_gap_indent_heading_caption_and_table_alignment_force_boundaries(self):
        """Treating every nearby line as prose must fail this boundary regression."""
        lines = [
            line(0, "Đoạn đầu", 72, 72, 250, 84),
            line(1, "Đoạn sau", 72, 130, 250, 142),
            line(2, "Lệch lề", 110, 146, 250, 158),
            line(3, "MỤC MỚI", 72, 164, 250, 176, font="Times-Bold", size=14),
            line(4, "Hình 1. Minh họa", 72, 182, 250, 194),
            table_line(5, "A        B        C", (72, 160, 250), 200),
            table_line(6, "1        2        3", (73, 161, 251), 216),
        ]
        units = reconstruct_page_units(lines, 612, 792, LayoutConfig())

        self.assertEqual([unit.unit_type for unit in units], [
            "paragraph", "paragraph", "paragraph", "heading", "caption", "table_like",
        ])

    def test_only_unambiguous_page_continuations_join(self):
        """Joining pages without edge and sentence-continuation checks must fail this test."""
        pages = [
            [line(0, "Nội dung chưa kết thúc", 72, 744, 400, 756)],
            [TextLine(1, 0, (72, 48, 400, 60), "tiếp tục ở trang sau.",
                      (TextSpan("tiếp tục ở trang sau.", "Times-Roman", 12, (72, 48, 400, 60), 0, 20),), 0, 20)],
            [TextLine(2, 0, (72, 260, 400, 272), "Một đoạn ở giữa trang.",
                      (TextSpan("Một đoạn ở giữa trang.", "Times-Roman", 12, (72, 260, 400, 272), 0, 22),), 0, 22)],
        ]
        units = reconstruct_document_units(pages, [(612, 792)] * 3, LayoutConfig())

        self.assertEqual(units[0].page_end, 1)
        self.assertEqual(units[0].review_flags, ())
        self.assertEqual(units[1].page_start, 2)
        self.assertEqual(units[1].review_flags, ())

    def test_punctuationless_end_does_not_join_new_sentence(self):
        pages = [
            [line(0, "A line ending without a period", 72, 744, 400, 756)],
            [page_line(1, 0, "A new sentence begins here.", 72, 48)],
        ]
        units = reconstruct_document_units(pages, [(612, 792)] * 2, LayoutConfig())
        self.assertEqual(len(units), 2)
        self.assertEqual(units[1].review_flags, ())

    def test_uppercase_and_digit_page_starts_receive_ambiguity_flag(self):
        cases = [
            ("The project was led by", "Nguyễn Văn A and colleagues.", 400),
            ("The fiscal year began in", "2024 and ended in 2025.", 400),
            ("The report listed several contributors", "Nguyễn Văn A appears first.", 530),
        ]
        for ending, beginning, right_edge in cases:
            with self.subTest(beginning=beginning):
                pages = [
                    [line(0, ending, 72, 744, right_edge, 756)],
                    [page_line(1, 0, beginning, 72, 48)],
                ]
                units = reconstruct_document_units(pages, [(612, 792)] * 2, LayoutConfig())
                self.assertEqual(len(units), 2)
                self.assertEqual(units[1].text, beginning)
                self.assertIn("ambiguous_page_continuation", units[1].review_flags)

    def test_quoted_lowercase_page_start_continues_or_gets_review_flag(self):
        pages = [
            [line(0, "The quotation says", 72, 744, 400, 756)],
            [page_line(1, 0, '"continued on the next page', 72, 48)],
        ]
        units = reconstruct_document_units(pages, [(612, 792)] * 2, LayoutConfig())
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0].page_end, 1)
        self.assertEqual(units[0].review_flags, ())

        misaligned = [pages[0], [page_line(1, 0, "(continued despite a new indent", 126, 48)]]
        units = reconstruct_document_units(misaligned, [(612, 792)] * 2, LayoutConfig())
        self.assertEqual(len(units), 2)
        self.assertIn("ambiguous_page_continuation", units[1].review_flags)

    def test_blank_intermediate_page_keeps_boundary(self):
        pages = [
            [line(0, "a thought may continue", 72, 744, 400, 756)],
            [],
            [page_line(2, 0, "continued after blank page.", 72, 48)],
        ]
        units = reconstruct_document_units(pages, [(612, 792)] * 3, LayoutConfig())
        self.assertEqual(len(units), 2)
        self.assertEqual(units[1].page_start, 2)
        self.assertEqual(units[1].review_flags, ())

    def test_table_uses_aligned_span_boxes_and_preserves_spacing(self):
        rows = [table_line(0, "A  \tB        C", (72, 160, 250), 72),
                table_line(1, "1  \t2        3", (75, 163, 253), 88)]
        units = reconstruct_page_units(rows, 612, 792, LayoutConfig(table_alignment_tolerance=5))
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0].unit_type, "table_like")
        self.assertEqual(units[0].text, "A  \tB        C\n1  \t2        3")
        prose = [line(0, "Some  spaced prose", 72, 72, 300, 84),
                 line(1, "More  spaced prose", 72, 88, 300, 100)]
        prose_units = reconstruct_page_units(prose, 612, 792, LayoutConfig())
        self.assertEqual(len(prose_units), 1)
        self.assertEqual(prose_units[0].unit_type, "paragraph")
        self.assertEqual(prose_units[0].text, "Some spaced prose More spaced prose")
        misaligned = [table_line(0, "A        B        C", (72, 160, 250), 72),
                      table_line(1, "1        2        3", (72, 178, 268), 88)]
        self.assertTrue(all(unit.unit_type == "paragraph" for unit in
                            reconstruct_page_units(misaligned, 612, 792,
                                                   LayoutConfig(table_alignment_tolerance=5))))

    def test_line_gap_and_left_edge_thresholds_control_grouping(self):
        rows = [line(0, "ordinary start", 72, 72, 300, 84),
                line(1, "ordinary next", 78, 94, 300, 106)]
        self.assertEqual(len(reconstruct_page_units(rows, 612, 792,
                         LayoutConfig(line_gap_multiplier=1.9, left_edge_tolerance=8))), 1)
        self.assertEqual(len(reconstruct_page_units(rows, 612, 792,
                         LayoutConfig(line_gap_multiplier=1.5, left_edge_tolerance=8))), 2)
        self.assertEqual(len(reconstruct_page_units(rows, 612, 792,
                         LayoutConfig(line_gap_multiplier=1.9, left_edge_tolerance=4))), 2)

    def test_ambiguous_cross_page_continuation_is_reviewed_and_excluded(self):
        """Dropping the review flag for a layout-mismatched page break must fail this test."""
        pages = [
            [line(0, "Một ý tưởng dường như còn tiếp", 72, 744, 400, 756)],
            [TextLine(1, 0, (126, 48, 400, 60), "tục nhưng đổi lề ở trang mới.",
                      (TextSpan("tục nhưng đổi lề ở trang mới.", "Times-Roman", 12,
                                (126, 48, 400, 60), 0, 30),), 0, 30)],
        ]

        units = reconstruct_document_units(pages, [(612, 792)] * 2, LayoutConfig())
        active_units = [unit for unit in units if "ambiguous_page_continuation" not in unit.review_flags]

        self.assertEqual(len(units), 2)
        self.assertEqual(units[1].page_start, 1)
        self.assertIn("ambiguous_page_continuation", units[1].review_flags)
        self.assertNotIn(units[1], active_units)


if __name__ == "__main__":
    unittest.main()
