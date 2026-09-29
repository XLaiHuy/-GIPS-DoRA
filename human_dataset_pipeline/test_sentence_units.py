import hashlib
import unittest

from build_dataset import (
    SentenceFragment,
    approx_tokens,
    make_passages,
    segment_layout_unit,
    sentence_units_for_text,
    split_long_unit,
)
from layout_reconstruction import (
    LayoutConfig, LayoutUnit, SourceSpan, TextLine, TextSpan, reconstruct_page_units,
)
from unicode_normalization import NormalizationResult


def normalized(text):
    return NormalizationResult(text, "active", (), (),
                               tuple(((index, index + 1),) for index in range(len(text))))


def layout(text, unit_type="paragraph"):
    return LayoutUnit(text, unit_type, 0, 0, (SourceSpan(0, 10, 10 + len(text)),), ())


class SentenceUnitTests(unittest.TestCase):
    def test_vietnamese_boundaries_keep_abbreviations_decimals_and_numbered_heading(self):
        text = "TS. Nguyễn ghi nhận giá trị 3.14 tại TP. Hồ Chí Minh. Kết quả ổn định!"
        units = segment_layout_unit(layout(text), normalized(text))
        self.assertEqual([unit.text for unit in units],
                         ["TS. Nguyễn ghi nhận giá trị 3.14 tại TP. Hồ Chí Minh.",
                          "Kết quả ổn định!"])
        heading = "1.2.3 Phương pháp nghiên cứu"
        self.assertEqual([unit.text for unit in segment_layout_unit(layout(heading, "heading"),
                                                                     normalized(heading))], [heading])

    def test_compound_academic_abbreviations_stay_in_sentence(self):
        text = "PGS.TS. Nguyễn và Th.S. Trần nghiên cứu dữ liệu. Kết quả đã công bố."
        self.assertEqual([unit.text for unit in segment_layout_unit(layout(text), normalized(text))],
                         ["PGS.TS. Nguyễn và Th.S. Trần nghiên cứu dữ liệu.",
                          "Kết quả đã công bố."])

    def test_terminal_punctuation_carries_closing_quotes_and_brackets(self):
        text = '"Câu đầu." Câu tiếp theo! (Câu cuối.) Câu sau nữa.'
        self.assertEqual([unit.text for unit in segment_layout_unit(layout(text), normalized(text))],
                         ['"Câu đầu."', 'Câu tiếp theo!', '(Câu cuối.)', 'Câu sau nữa.'])

    def test_complete_bullet_and_reconstructed_wrapped_sentence_stay_whole(self):
        bullet = "• Mô tả một mục. Nội dung tiếp tục nhưng không có dấu kết"
        units = segment_layout_unit(layout(bullet, "bullet_item"), normalized(bullet))
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0].unit_type, "bullet_item")
        self.assertEqual(units[0].text, bullet)
        base_rows = sentence_units_for_text(bullet, "bullet_item", 0,
                                            [SourceSpan(0, 10, 10 + len(bullet))])
        self.assertEqual(len(base_rows), 1)
        self.assertEqual(base_rows[0]["unit_type"], "bullet_item")
        self.assertEqual(base_rows[0]["text"], bullet)
        first, second = "Đây là câu được nối từ", "hai dòng và kết thúc ở đây."
        first_box, second_box = (72.0, 100.0, 330.0, 112.0), (72.0, 114.0, 330.0, 126.0)
        first_span = TextSpan(first, "Times New Roman", 12.0, first_box, 0, len(first), 0)
        second_start = len(first) + 1
        second_span = TextSpan(second, "Times New Roman", 12.0, second_box,
                               second_start, second_start + len(second), 0)
        lines = [TextLine(0, 0, first_box, first, (first_span,), 0, len(first)),
                 TextLine(0, 1, second_box, second, (second_span,),
                          second_start, second_start + len(second))]
        reconstructed = reconstruct_page_units(lines, 612.0, 792.0, LayoutConfig())
        self.assertEqual(len(reconstructed), 1)
        self.assertEqual(reconstructed[0].text, first + " " + second)
        self.assertEqual(len(segment_layout_unit(reconstructed[0],
                                                 normalized(reconstructed[0].text))), 1)

    def test_long_bullet_splits_recursively_with_parent_lineage(self):
        text = "• " + " ".join(["nội dung nghiên cứu"] * 250)
        unit = segment_layout_unit(layout(text, "bullet_item"), normalized(text))[0]
        fragments = split_long_unit(unit, max_tokens=100)
        self.assertGreater(len(fragments), 1)
        self.assertTrue(all(isinstance(fragment, SentenceFragment) for fragment in fragments))
        self.assertEqual(len({fragment.parent_sentence_id for fragment in fragments}), 1)
        self.assertEqual([fragment.fragment_index for fragment in fragments], list(range(len(fragments))))
        self.assertTrue(all(fragment.fragment_count == len(fragments) for fragment in fragments))
        self.assertTrue(all(fragment.unit_type == "bullet_item" for fragment in fragments))
        self.assertTrue(all(approx_tokens(fragment.text) <= 100 for fragment in fragments))

    def test_passage_uses_exact_document_slice_and_source_lineage(self):
        first, second = "Câu thứ nhất hoàn chỉnh.", "Câu thứ hai hoàn chỉnh."
        body = first + "\n\n" + second
        sentences = []
        for index, (text, start) in enumerate(((first, 0), (second, len(first) + 2))):
            sentences.append({
                "sentence_id": f"s{index}", "text": text, "ordinal": index,
                "section_id": "sec", "paragraph_id": f"p{index}", "page_index": index,
                "content_type": "prose", "unit_type": "bullet_item" if index else "paragraph",
                "doc_char_start": start, "doc_char_end": start + len(text),
                "source_spans": [{"page_index": index, "source_start": 10, "source_end": 30}],
                "approx_tokens": approx_tokens(text),
            })
        passages = make_passages(sentences, target=100, minimum=1, maximum=100,
                                 document_text=body)
        self.assertEqual(len(passages), 1)
        passage = passages[0]
        self.assertEqual(body[passage["start_char"]:passage["end_char"]], passage["text"])
        self.assertEqual(passage["sentence_ids"], ["s0", "s1"])
        self.assertEqual(passage["unit_types"], ["paragraph", "bullet_item"])
        self.assertEqual(len(passage["source_spans"]), 2)
        self.assertEqual(passage["text_sha256"], hashlib.sha256(passage["text"].encode()).hexdigest())


if __name__ == "__main__":
    unittest.main()
