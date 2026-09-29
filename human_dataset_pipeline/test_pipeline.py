import unittest
import argparse
import json
import tempfile
import fitz
from pathlib import Path
from unittest.mock import patch
from dataclasses import asdict

from build_dataset import (
    ExtractedPage,
    approx_tokens,
    assign_split,
    build,
    content_type,
    make_passages,
    normalize_paragraph,
    split_sentence_units,
    split_sentences_with_offsets,
)
from validate_dataset import ValidationIssue


class PipelineTests(unittest.TestCase):
    def test_release_issue_has_stable_trace_fields(self):
        issue = ValidationIssue("error", "source_span_invalid", "doc_a", "group_a",
                                2, "sent_a", "Source slice differs.")
        self.assertEqual(set(asdict(issue)), {
            "severity", "code", "document_id", "group_id", "page_index",
            "record_id", "message",
        })
        self.assertEqual((issue.page_index, issue.record_id), (2, "sent_a"))

    def test_normalize_paragraph(self):
        self.assertEqual(normalize_paragraph("Mô hì-\nnh học máy"), "Mô hình học máy")

    def test_sentence_offsets_roundtrip(self):
        text = "Đây là câu một. Đây là câu hai!"
        for start, end, value in split_sentences_with_offsets(text):
            self.assertEqual(text[start:end], value)

    def test_pathological_sentence_is_hard_split(self):
        text = " ".join(["nghiên cứu dữ liệu"] * 500)
        spans = split_sentences_with_offsets(text, max_tokens=100)
        self.assertGreater(len(spans), 1)
        for start, end, value in spans:
            self.assertEqual(text[start:end], value)
            self.assertLessEqual(approx_tokens(value), 100)

    def test_split_is_deterministic(self):
        self.assertEqual(assign_split("doc-a", "seed"), assign_split("doc-a", "seed"))

    def test_content_type(self):
        self.assertEqual(content_type("Nguyễn Văn A, 2020.", "Tài liệu tham khảo"), "references")

    def test_passages_do_not_duplicate_sentences(self):
        sentences = []
        for i in range(20):
            text = f"Đây là câu nghiên cứu số {i} với đủ nội dung để kiểm tra."
            sentences.append({
                "sentence_id": f"s{i}", "text": text, "page_index": 0,
                "section_id": "sec", "paragraph_id": f"p{i // 2}", "content_type": "prose", "ordinal": i,
                "approx_tokens": approx_tokens(text),
            })
        passages = make_passages(sentences, target=50, minimum=20, maximum=70)
        ids = [sid for passage in passages for sid in passage["sentence_ids"]]
        self.assertEqual(ids, [f"s{i}" for i in range(20)])
        self.assertEqual(len(ids), len(set(ids)))

    def test_passages_never_cross_sections(self):
        sentences = []
        for i in range(4):
            text = "Ná»™i dung nghiÃªn cá»©u ngáº¯n nhÆ°ng há»£p lá»‡."
            sentences.append({
                "sentence_id": f"s{i}", "text": text, "page_index": 0,
                "section_id": "a" if i < 2 else "b", "paragraph_id": f"p{i}",
                "content_type": "prose", "ordinal": i, "approx_tokens": approx_tokens(text),
            })
        passages = make_passages(sentences, target=100, minimum=100, maximum=120)
        self.assertTrue(all(len(row["section_ids"]) == 1 for row in passages))
        self.assertTrue(any(row["short_tail"] for row in passages))

    def test_recursive_fallback_has_parent_lineage(self):
        text = " ".join(["nghiÃªn cá»©u dá»¯ liá»‡u"] * 500)
        units = split_sentence_units(text, max_tokens=100)
        self.assertGreater(len(units), 1)
        self.assertTrue(all(row["is_fragment"] for row in units))
        self.assertEqual({row["parent_ordinal"] for row in units}, {0})

    def test_abbreviations_and_decimals_do_not_create_sentence_boundaries(self):
        text = "TS. Nguyễn đo được 3.14 tại TP. Hồ Chí Minh. Kết quả được ghi nhận."
        self.assertEqual([row["text"] for row in split_sentence_units(text)],
                         ["TS. Nguyễn đo được 3.14 tại TP. Hồ Chí Minh.",
                          "Kết quả được ghi nhận."])

    def test_passage_offsets_do_not_overlap(self):
        parts = [f"Câu thứ {index} có nội dung đầy đủ." for index in range(8)]
        body = " ".join(parts)
        rows = []
        cursor = 0
        for index, text in enumerate(parts):
            rows.append({"sentence_id": f"s{index}", "text": text, "ordinal": index,
                         "section_id": "sec", "paragraph_id": "p", "page_index": 0,
                         "content_type": "prose", "unit_type": "paragraph",
                         "doc_char_start": cursor, "doc_char_end": cursor + len(text),
                         "approx_tokens": approx_tokens(text)})
            cursor += len(text) + 1
        passages = make_passages(rows, target=30, minimum=1, maximum=45,
                                 document_text=body)
        self.assertGreater(len(passages), 1)
        self.assertTrue(all(body[row["start_char"]:row["end_char"]] == row["text"]
                            and row["approx_tokens"] <= 45 for row in passages))
        self.assertTrue(all(left["end_char"] < right["start_char"]
                            for left, right in zip(passages, passages[1:])))

    def test_base_records_keep_heading_and_dash_star_bullet_types(self):
        texts = ["1. Introduction", "- First complete item with enough text to inspect.",
                 "* Second complete item with enough text to inspect."]
        with tempfile.TemporaryDirectory() as root:
            input_dir, output_dir = Path(root) / "input", Path(root) / "output"
            input_dir.mkdir()
            pdf = fitz.open()
            page = pdf.new_page()
            for index, value in enumerate(texts):
                page.insert_text((72, 140 + index * 35), value,
                                 fontsize=16 if index == 0 else 11,
                                 fontname="hebo" if index == 0 else "helv")
            pdf.save(input_dir / "sample.pdf")
            pdf.close()
            args = argparse.Namespace(input=input_dir, output=output_dir, limit=None,
                                      min_page_chars=1, simhash_distance=0, seed="test",
                                      passage_target=100, passage_min=1, passage_max=512)
            build(args)
            def rows(name):
                return [json.loads(line) for line in
                        (output_dir / "canonical" / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()]
            paragraphs, sentences, passages = rows("paragraphs"), rows("sentences"), rows("passages")
        self.assertEqual([row["unit_type"] for row in paragraphs],
                         ["heading", "bullet_item", "bullet_item"])
        self.assertEqual([row["unit_type"] for row in sentences],
                         ["heading", "bullet_item", "bullet_item"])
        self.assertEqual(sentences[0]["content_type"], "heading")
        self.assertNotIn(sentences[0]["sentence_id"],
                         [sid for passage in passages for sid in passage["sentence_ids"]])


if __name__ == "__main__":
    unittest.main()
