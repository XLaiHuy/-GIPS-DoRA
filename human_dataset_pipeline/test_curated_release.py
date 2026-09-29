import unittest

from build_curated_release import exclusion_reason


class CuratedReleaseTests(unittest.TestCase):
    def test_hcmute_is_excluded_even_if_pre_2023(self):
        doc = {"institution_id": "hcmute", "year": 2021, "training_eligible": True, "core_human_eligible": True}
        self.assertEqual(exclusion_reason(doc, {"hcmute"}, 2022), "partial_preview_source")

    def test_post_cutoff_and_ineligible_are_excluded(self):
        recent = {"institution_id": "ou_hcmc", "year": 2023, "training_eligible": True, "core_human_eligible": False}
        self.assertEqual(exclusion_reason(recent, {"hcmute"}, 2022), "post_cutoff_year")
        bad = {"institution_id": "ou_hcmc", "year": 2020, "training_eligible": False, "quality_tier": "quarantine"}
        self.assertEqual(exclusion_reason(bad, {"hcmute"}, 2022), "quality_gate")

    def test_valid_core_document_is_kept(self):
        doc = {"institution_id": "ou_hcmc", "year": 2022, "training_eligible": True, "core_human_eligible": True}
        self.assertIsNone(exclusion_reason(doc, {"hcmute"}, 2022))


if __name__ == "__main__":
    unittest.main()
