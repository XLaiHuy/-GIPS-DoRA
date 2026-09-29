import unittest
from prepare_gips_training_data import should_merge, merge_sentences_in_passage

class TestPrepareGIPSTrainingData(unittest.TestCase):
    def test_should_merge_lowercase(self):
        prev = "Game là thể loại bắn súng sinh tồn,"
        curr = "người chơi sẽ được trải nghiệm..."
        self.assertTrue(should_merge(prev, curr))

    def test_should_merge_no_terminal_punct(self):
        prev = "Sau hơn nửa thế kỷ phát triển, công nghệ thực tế ảo đã trở thành một ngành"
        curr = "công nghiệp thực sự hái ra tiền tại các nước phát triển"
        self.assertTrue(should_merge(prev, curr))

    def test_should_not_merge_proper_sentences(self):
        prev = "Hệ thống đã hoàn thành việc huấn luyện."
        curr = "Kết quả đạt độ chính xác 95%."
        self.assertFalse(should_merge(prev, curr))

    def test_merge_sentences_in_passage(self):
        input_sents = [
            {"sentence_id": "s1", "text": "Game là thể loại bắn súng sinh tồn,"},
            {"sentence_id": "s2", "text": "người chơi sẽ được trải nghiệm bối cảnh chiến đấu."},
            {"sentence_id": "s3", "text": "Đồ họa game rất chân thực."}
        ]
        merged = merge_sentences_in_passage(input_sents)
        self.assertEqual(len(merged), 2)
        self.assertIn("Game là thể loại bắn súng sinh tồn, người chơi sẽ được trải nghiệm bối cảnh chiến đấu.", merged[0]["text"])
        self.assertEqual(merged[0]["constituent_ids"], ["s1", "s2"])
        self.assertEqual(merged[1]["text"], "Đồ họa game rất chân thực.")

if __name__ == "__main__":
    unittest.main()
