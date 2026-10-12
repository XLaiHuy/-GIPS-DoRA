"""Regressions for the conservative PDF review interval and sample strata."""
from __future__ import annotations

import unittest

from evaluate_human_pdf_release_gate import cluster_interval
from prepare_human_pdf_release_sample import allocate, population_digest, source_family, year_band
from screen_new_human_pdf_batch import screen


class PdfReleaseGateTests(unittest.TestCase):
    @staticmethod
    def cards(documents: int, chunks_per_document: int = 1, failures: int = 0):
        return [{"source_family": "ou_hcmc" if did % 2 else "hpu",
                 "document_id": f"doc_{did}", "sample_weight": 1.0,
                 "decision": "reject" if did < failures else "pass"}
                for did in range(documents) for _ in range(chunks_per_document)]

    def test_all_pass_does_not_claim_perfect_lower_bound(self):
        result = cluster_interval(self.cards(400), iterations=200)
        self.assertEqual(result["weighted_pass_rate"], 1.0)
        self.assertEqual(result["cluster_bootstrap_one_sided_95_lower"], 1.0)
        self.assertGreaterEqual(result["conservative_one_sided_95_lower"], .95)
        self.assertLess(result["conservative_one_sided_95_lower"], 1.0)

    def test_four_hundred_chunks_from_few_documents_are_not_enough(self):
        result = cluster_interval(self.cards(40, chunks_per_document=10), iterations=200)
        self.assertEqual(result["effective_document_clusters"], 40)
        self.assertLess(result["conservative_one_sided_95_lower"], .95)

    def test_observed_errors_reduce_lower_bound(self):
        result = cluster_interval(self.cards(400, failures=20), iterations=200)
        self.assertEqual(result["weighted_pass_rate"], .95)
        self.assertLess(result["conservative_one_sided_95_lower"], .95)

    def test_source_and_year_are_real_strata(self):
        self.assertEqual(source_family({"institution_id": "ou_hcmc"}), "ou_hcmc")
        self.assertEqual(year_band(2022), "2020_2022")
        self.assertEqual(year_band(2013), "2010_2014")
        self.assertEqual(sum(allocate({("hpu", "2010_2014"): list(range(80)),
                                       ("ou_hcmc", "2020_2022"): list(range(20))}, 10).values()), 10)

    def test_sample_fingerprint_changes_after_source_pdf_changes(self):
        sample = [{"chunk_id": "a", "document_id": "d", "split": "train", "text": "Một đoạn văn."}]
        first = population_digest(sample, {"d": {"source_pdf_sha256": "old"}})
        second = population_digest(sample, {"d": {"source_pdf_sha256": "new"}})
        self.assertNotEqual(first, second)

    def test_new_pdf_without_document_rights_and_fulltext_is_quarantined(self):
        result = screen({"document_type_id": "bachelor_thesis", "year": 2021,
                         "language": "vi", "pdf_path": "nonexistent.pdf",
                         "rights_status": "pending"}, set())
        self.assertEqual(result["screening_status"], "quarantine")
        self.assertIn("pdf_missing", result["screening_reasons"])
        self.assertIn("document_specific_rights_proof_missing", result["screening_reasons"])
        self.assertIn("fulltext_review_pending", result["screening_reasons"])


if __name__ == "__main__":
    unittest.main()
