"""Unit tests for the GIPS-DoRA Counterfactual Generation Pipeline."""

import unittest
from pathlib import Path
from counterfactual_pipeline.skeleton_extractor import SemanticSkeletonExtractor
from counterfactual_pipeline.prompt_templates import (
    build_polish_prompt,
    build_generation_prompt,
    compute_prompt_hash
)
from counterfactual_pipeline.sentence_aligner import (
    clean_llm_markdown,
    segment_sentences,
    align_and_package_sentences
)
from counterfactual_pipeline.providers.mock_provider import MockLLMProvider
from counterfactual_pipeline.hybrid_synthesizer import HybridSequenceSynthesizer


class TestCounterfactualPipeline(unittest.TestCase):
    def setUp(self):
        self.sample_passage = {
            "passage_id": "paperpassage_test_001",
            "source_document_id": "doc_test_123",
            "split": "train",
            "topic_cluster": "artificial_intelligence",
            "text": "Mô hình ResNet-50 được huấn luyện với batch size 32 trên tập dữ liệu ImageNet đạt độ chính xác 92.5%. Chúng tôi áp dụng hàm mất mát Cross-Entropy và thuật toán Adam. Kết quả cho thấy mô hình hội tụ sau 50 epochs."
        }

    def test_skeleton_extraction(self):
        extractor = SemanticSkeletonExtractor()
        skeleton = extractor.extract_skeleton(self.sample_passage)
        
        self.assertEqual(skeleton["source_passage_id"], "paperpassage_test_001")
        self.assertEqual(skeleton["topic_cluster"], "artificial_intelligence")
        self.assertIn("ResNet-50", skeleton["key_entities"])
        self.assertIn("ImageNet", skeleton["key_entities"])
        self.assertTrue(any("92.5%" in c or "32" in c or "50" in c for c in skeleton["numeric_constraints"]))

    def test_polish_prompt_construction(self):
        sys_p, user_p, p_hash = build_polish_prompt(
            text=self.sample_passage["text"],
            level="light",
            topic_cluster="artificial_intelligence"
        )
        self.assertIn("P-LIGHT", user_p)
        self.assertIn("BẢO TOÀN NGUYÊN VẸN cấu trúc câu", user_p)
        self.assertIn("ResNet-50", user_p)
        self.assertEqual(len(p_hash), 16)

    def test_generation_prompt_zero_leakage(self):
        extractor = SemanticSkeletonExtractor()
        skeleton = extractor.extract_skeleton(self.sample_passage)
        sys_p, user_p, p_hash = build_generation_prompt(skeleton)
        
        # Verify NO raw passage text exists in the prompt
        self.assertNotIn(self.sample_passage["text"], user_p)
        self.assertIn("SEMANTIC SKELETON", user_p)
        self.assertIn("ResNet-50", user_p)

    def test_sentence_alignment_and_cleaning(self):
        raw_llm_output = "```markdown\n\"Đây là câu thứ nhất tại TP.HCM. Còn đây là câu thứ hai với độ chính xác 95.5%.\"\n```"
        cleaned = clean_llm_markdown(raw_llm_output)
        self.assertNotIn("```", cleaned)
        self.assertFalse(cleaned.startswith('"'))
        self.assertFalse(cleaned.endswith('"'))

        res = align_and_package_sentences(raw_llm_output, "rec_test", 1)
        self.assertEqual(res["sentence_count"], 2)
        self.assertEqual(res["sentences"][0]["ordinal"], 0)
        self.assertEqual(res["sentences"][1]["ordinal"], 1)
        self.assertEqual(res["sentences"][0]["label_id"], 1)

    def test_mock_provider(self):
        provider = MockLLMProvider()
        res_p = provider.generate("sys", "--- ĐOẠN VĂN GỐC CẦN HIỆU ĐÍNH ---\nP-LIGHT: Thực nghiệm web ui.\n----------------------------------")
        self.assertIn("UI", res_p)
        
        res_g = provider.generate("sys", "KHUNG NGỮ NGHĨA (SEMANTIC SKELETON)\nChuyên ngành: AI\nbắt buộc đề cập: Transformer")
        self.assertIn("Transformer", res_g)

    def test_hybrid_synthesizer(self):
        synthesizer = HybridSequenceSynthesizer(seed=123)
        h_sents = [
            {"sentence_id": "h_1", "text": "Câu mở đầu của con người.", "approx_tokens": 7},
            {"sentence_id": "h_2", "text": "Câu phương pháp do người viết.", "approx_tokens": 7}
        ]
        p_sents = [
            {"sentence_id": "p_1", "text": "Câu hiệu đính bởi AI.", "approx_tokens": 6}
        ]
        g_sents = [
            {"sentence_id": "g_1", "text": "Câu do AI tự sinh hoàn toàn.", "approx_tokens": 8}
        ]

        hybrid = synthesizer.synthesize_hybrid(
            h_sentences=h_sents,
            p_sentences=p_sents,
            g_sentences=g_sents,
            source_document_id="doc_test",
            split="train",
            topic_cluster="artificial_intelligence"
        )

        self.assertIsNotNone(hybrid)
        self.assertIn(hybrid["split"], ["train"])
        self.assertTrue(len(hybrid["sentences"]) >= 2)
        self.assertIn(1, hybrid["sentence_labels"])
        self.assertTrue(0.0 <= hybrid["r_G"] <= 1.0)
        self.assertTrue(0.0 <= hybrid["r_AI_assisted"] <= 1.0)


if __name__ == "__main__":
    unittest.main()
