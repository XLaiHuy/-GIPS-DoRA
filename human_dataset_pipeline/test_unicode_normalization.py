import json
import unittest
from dataclasses import replace
from pathlib import Path

from layout_reconstruction import LayoutConfig, TextLine, TextSpan, reconstruct_page_units
from unicode_normalization import GlyphRule, _source_span_verified, normalize_unit


FIXTURE = Path(__file__).with_name("testdata") / "layout_fixture_page49.json"
BOX = (0.0, 0.0, 1.0, 1.0)


def span(text, font="Times New Roman", start=0, page_index=-1):
    return TextSpan(text, font, 12.0, BOX, start, start + len(text), page_index)


class UnicodeNormalizationTests(unittest.TestCase):
    def test_legacy_textspan_constructor_uses_unknown_page(self):
        self.assertEqual(TextSpan("A", "Font", 12.0, BOX, 0, 1).page_index, -1)

    def test_nfc_tracks_composed_source_characters(self):
        result = normalize_unit("Cafe\u0301", "doc-1", [span("Cafe\u0301")], {})
        self.assertEqual(result.text, "Café")
        self.assertEqual(result.source_char_ranges,
                         (((0, 1),), ((1, 2),), ((2, 3),), ((3, 5),)))
        self.assertEqual(result.status, "active")

    def test_reordered_combining_marks_keep_exact_ranges(self):
        result = normalize_unit("e\u0301\u0323", "doc-1", [span("e\u0301\u0323")], {})
        self.assertEqual(result.text, "ẹ\u0301")
        self.assertEqual(result.source_char_ranges,
                         (((0, 1), (2, 3)), ((1, 2),)))

    def test_remove_soft_hyphen_preserve_ordinary_hyphen(self):
        raw = "A\u00a0B\u00adC lexical-hyphen"
        result = normalize_unit(raw, "doc-1", [span(raw)], {})
        self.assertEqual(result.text, "A BC lexical-hyphen")
        self.assertEqual(result.source_char_ranges[:4],
                         (((0, 1),), ((1, 2),), ((2, 3),), ((4, 5),)))
        self.assertEqual(result.source_char_ranges[-7], ((len(raw) - 7, len(raw) - 6),))
        self.assertNotIn("unresolved_pua", result.review_flags)

    def test_collapse_whitespace_tracks_entire_raw_run(self):
        raw = "  A\t \nB  "
        result = normalize_unit(raw, "doc-1", [span(raw)], {})
        self.assertEqual(result.text, "A B")
        self.assertEqual(result.source_char_ranges,
                         (((2, 3),), ((3, 6),), ((6, 7),)))

    def test_collapsed_whitespace_excludes_removed_soft_hyphen(self):
        result = normalize_unit("A \u00ad B", "doc-1", [], {})
        self.assertEqual(result.text, "A B")
        self.assertEqual(result.source_char_ranges,
                         (((0, 1),), ((1, 2), (3, 4)), ((4, 5),)))

    def test_verified_symbol_bullet_uses_original_fixture_evidence(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        row = fixture["lines"][5]
        raw = row["text"]
        original = row["spans"][0]
        evidence = fixture["normalization_glyph_evidence"]
        self.assertEqual(original["text"], evidence["glyph"])
        self.assertEqual(original["font_name"], evidence["font_name"])
        self.assertEqual(evidence["page_index"], fixture["page_index"])
        self.assertEqual(evidence["source_span_ranges"][0],
                         [original["source_start"], original["source_end"]])
        self.assertEqual(fixture["source_text"][original["source_start"]:original["source_end"]], "\uf0b7")
        spans = [TextSpan(s["text"], s["font_name"], s["font_size"], tuple(s["bbox"]),
                          s["source_start"], s["source_end"], evidence["page_index"])
                 for s in row["spans"]]
        layout_line = TextLine(fixture["page_index"], 5, tuple(row["bbox"]), raw,
                               tuple(spans), row["source_start"], row["source_end"])
        self.assertEqual(reconstruct_page_units([layout_line], *fixture["page_size"],
                                                LayoutConfig(**fixture["config"]))[0].unit_type,
                         "bullet_item")
        rule = GlyphRule(evidence["replacement"], evidence["evidence"],
                         evidence["reviewer_status"])
        result = normalize_unit(raw, "63617_VO VAN THUAN.pdf", spans,
                                {("63617_VO VAN THUAN.pdf", "Symbol", 0xF0B7): rule},
                                {fixture["page_index"]: fixture["source_text"]})
        self.assertTrue(result.text.startswith("• "))
        self.assertEqual(result.source_char_ranges[0], ((0, 1),))
        self.assertEqual(result.status, "active")
        applied = json.loads(result.rules_applied[0])
        self.assertEqual(applied["source_page"], 49)
        self.assertEqual(applied["source_span"], [350, 351])
        self.assertEqual(applied["disposition"], "reviewed")
        self.assertEqual(applied["codepoint"], 0xF0B7)

    def test_glyph_map_requires_document_and_font_key(self):
        raw = "\uf0b7"
        for key in (("other-doc", "Symbol", 0xF0B7),
                    ("doc-1", "OtherFont", 0xF0B7)):
            with self.subTest(key=key):
                result = normalize_unit(raw, "doc-1", [span(raw, "Symbol", 0, 0)],
                                        {key: GlyphRule("•", "verified elsewhere", "reviewed")},
                                        {0: raw})
                self.assertEqual(result.text, raw)
                self.assertEqual(result.status, "review_required")
                self.assertIn("unresolved_pua", result.review_flags)

    def test_unaligned_span_does_not_authorize_glyph_replacement(self):
        raw = "\uf0b7 X"
        rules = {("doc-1", "Symbol", 0xF0B7): GlyphRule("•", "verified", "reviewed")}
        for spans in ([span("\uf0b7 Y", "Symbol", page_index=0)],
                      [span("\uf0b7", "Symbol", page_index=0),
                       span("\uf0b7", "OtherFont", 1, page_index=0)]):
            with self.subTest(spans=spans):
                result = normalize_unit(raw, "doc-1", spans, rules, {0: raw})
                self.assertEqual(result.text, raw)
                self.assertIn("span_alignment_unresolved", result.review_flags)
                self.assertEqual(result.status, "review_required")

    def test_whitespace_normalized_span_alignment_can_authorize_rule(self):
        raw = "\uf0b7  A"
        spans = [span("\uf0b7", "Symbol", 10, 0), span(" A", "Times New Roman", 11, 0)]
        rules = {("doc-1", "Symbol", 0xF0B7): GlyphRule("•", "verified", "reviewed")}
        result = normalize_unit(raw, "doc-1", spans, rules, {0: "x" * 10 + "\uf0b7 A"})
        self.assertEqual(result.text, "• A")
        self.assertEqual(result.source_char_ranges, (((0, 1),), ((1, 3),), ((3, 4),)))
        self.assertNotIn("span_alignment_unresolved", result.review_flags)

    def test_source_page_must_verify_entire_span(self):
        raw = "\uf0b7 A"
        rule = {("doc-1", "Symbol", 0xF0B7): GlyphRule("•", "evidence", "reviewed")}
        cases = (
            (span(raw, "Symbol", 0, -1), {0: raw}),
            (span(raw, "Symbol", 0, 0), None),
            (span(raw, "Symbol", 0, 0), {0: "\uf0b7 X"}),
            (span(raw, "Symbol", 1, 0), {0: raw}),
        )
        for source_span, pages in cases:
            with self.subTest(source_span=source_span, pages=pages):
                result = normalize_unit(raw, "doc-1", [source_span], rule, pages)
                self.assertEqual(result.text, raw)
                self.assertIn("source_span_unverified", result.review_flags)
                self.assertEqual(result.rules_applied, ())
                self.assertEqual(result.status, "review_required")

    def test_malformed_source_offsets_fail_closed_during_alignment(self):
        raw = "\uf0b7"
        rule = {("doc-1", "Symbol", 0xF0B7): GlyphRule("•", "evidence", "reviewed")}

        class IntSubclass(int):
            pass

        for field, value in (("source_start", "0"), ("source_end", 1.0),
                             ("source_start", False), ("source_end", IntSubclass(1))):
            with self.subTest(field=field, value=value):
                malformed = replace(span(raw, "Symbol", 0, 0), **{field: value})
                result = normalize_unit(raw, "doc-1", [malformed], rule, {0: raw})
                self.assertEqual(result.text, raw)
                self.assertEqual(result.rules_applied, ())
                self.assertIn("span_alignment_unresolved", result.review_flags)
                self.assertEqual(result.status, "review_required")

    def test_malformed_page_indexes_fail_closed_during_alignment(self):
        raw = "\uf0b7"
        rule = {("doc-1", "Symbol", 0xF0B7): GlyphRule("•", "evidence", "reviewed")}

        class IntSubclass(int):
            pass

        for page_index in ("0", 0.0, False, IntSubclass(0)):
            with self.subTest(page_index=page_index):
                result = normalize_unit(raw, "doc-1", [span(raw, "Symbol", 0, page_index)],
                                        rule, {0: raw})
                self.assertEqual(result.text, raw)
                self.assertEqual(result.rules_applied, ())
                self.assertIn("span_alignment_unresolved", result.review_flags)
                self.assertEqual(result.status, "review_required")

        mixed = [span("\uf0b7", "Symbol", 0, "0"),
                 span(" A", "Times New Roman", 1, 0)]
        result = normalize_unit("\uf0b7 A", "doc-1", mixed, rule, {0: "\uf0b7 A"})
        self.assertEqual(result.text, "\uf0b7 A")
        self.assertEqual(result.rules_applied, ())
        self.assertIn("span_alignment_unresolved", result.review_flags)

    def test_source_verifier_rejects_malformed_offsets_directly(self):
        raw = "\uf0b7"
        for field, value in (("source_start", "0"), ("source_end", 1.0),
                             ("source_start", False), ("source_end", True)):
            with self.subTest(field=field, value=value):
                malformed = replace(span(raw, "Symbol", 0, 0), **{field: value})
                self.assertFalse(_source_span_verified(malformed, {0: raw}))

    def test_only_exact_reviewer_statuses_authorize_rule(self):
        raw = "\uf0b7"
        source_span = span(raw, "Symbol", 0, 0)
        for status in ("approved", "pending", "Reviewed"):
            with self.subTest(status=status):
                rule = {("doc-1", "Symbol", 0xF0B7): GlyphRule("•", "evidence", status)}
                result = normalize_unit(raw, "doc-1", [source_span], rule, {0: raw})
                self.assertEqual(result.text, raw)
                self.assertIn("unverified_glyph_rule", result.review_flags)
        for status in ("automatic", "reviewed"):
            with self.subTest(status=status):
                rule = {("doc-1", "Symbol", 0xF0B7): GlyphRule("•", "evidence", status)}
                result = normalize_unit(raw, "doc-1", [source_span], rule, {0: raw})
                self.assertEqual(result.text, "•")
                self.assertEqual(json.loads(result.rules_applied[0])["disposition"], status)

    def test_unknown_pua_replacement_character_and_unverified_letter(self):
        result = normalize_unit("\ue001 \ufffd", "doc-1", [span("\ue001 \ufffd")], {})
        self.assertEqual(result.text, "\ue001 \ufffd")
        self.assertEqual(result.status, "review_required")
        self.assertIn("unresolved_pua", result.review_flags)
        self.assertIn("replacement_character", result.review_flags)
        self.assertEqual(normalize_unit("Ƣ", "doc-1", [span("Ƣ")], {}).text, "Ƣ")


if __name__ == "__main__":
    unittest.main()
