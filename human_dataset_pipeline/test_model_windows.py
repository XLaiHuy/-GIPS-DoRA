import re
import unittest
from collections import Counter

from make_model_windows import pack_sentence_windows


class FakeTokenizer:
    def build_inputs_with_special_tokens(self, ids):
        return [101, *ids, 102]

    def __call__(self, text, add_special_tokens=False, return_offsets_mapping=False, truncation=False):
        matches = list(re.finditer(r"\S+", text))
        row = {"input_ids": list(range(len(matches)))}
        if return_offsets_mapping:
            row["offset_mapping"] = [(match.start(), match.end()) for match in matches]
        return row


class ModelWindowTests(unittest.TestCase):
    def sentences(self):
        rows = []
        cursor = 0
        for index in range(12):
            text = "sentence %d has several useful tokens for context" % index
            rows.append({
                "sentence_id": f"s{index}", "document_id": "d", "group_id": "g",
                "section_id": "sec", "ordinal": index, "split": "train",
                "provenance_status": "high_confidence_human", "label": "H",
                "char_start": cursor, "char_end": cursor + len(text), "text": text,
            })
            cursor += len(text) + 1
        return rows

    def test_sentence_windows_have_single_loss_coverage(self):
        windows = pack_sentence_windows(self.sentences(), FakeTokenizer(), max_tokens=42, central_ratio=0.75)
        coverage = Counter()
        for window in windows:
            self.assertLessEqual(window["token_count"], 42)
            self.assertEqual(len(window["sentence_ids"]), len(window["loss_mask"]))
            for unit_id, enabled in zip(window["unit_ids"], window["loss_mask"]):
                if enabled:
                    coverage[unit_id] += 1
        self.assertEqual(coverage, Counter({f"s{i}": 1 for i in range(12)}))

    def test_windows_do_not_cross_sections(self):
        rows = self.sentences()
        for row in rows[6:]:
            row["section_id"] = "sec2"
        windows = pack_sentence_windows(rows, FakeTokenizer(), max_tokens=42)
        self.assertEqual({window["section_id"] for window in windows}, {"sec", "sec2"})


if __name__ == "__main__":
    unittest.main()
