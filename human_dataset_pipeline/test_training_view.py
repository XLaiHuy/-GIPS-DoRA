import unittest
import json
import tempfile
from pathlib import Path

from build_training_view import (
    build_clean_text,
    make_passages,
    canonical_metadata_id,
    clean_people,
    cover_metadata,
    detect_language,
    normalize_academic_unit,
    normalize_document_type,
    normalize_institution,
    provenance_status,
    merge_metadata,
    resolve_training_splits,
)


class TrainingViewTests(unittest.TestCase):
    def test_base_manifest_is_authoritative_and_new_groups_are_hashed(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "split_manifest.jsonl"
            manifest.write_text(json.dumps({"document_id": "doc_old", "group_id": "group_old", "split": "test"}) + "\n", encoding="utf-8")
            rows = [
                {"document_id": "doc_old", "group_id": "group_old", "doc": {"split": "test"}},
                {"document_id": "doc_new", "group_id": "group_new", "doc": {}},
            ]
            assignments = resolve_training_splits(rows, manifest, "fixed-seed")
            self.assertEqual(assignments["group_old"], "test")
            self.assertIn(assignments["group_new"], {"train", "dev", "test"})

    def test_base_manifest_conflicts_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "split_manifest.jsonl"
            rows = [{"document_id": "doc_a", "group_id": "group_a", "doc": {"split": "train"}}]
            manifest.write_text(json.dumps({"group_id": "group_a", "split": "dev"}) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                resolve_training_splits(rows, manifest, "fixed-seed")
            manifest.write_text("\n".join([
                json.dumps({"group_id": "group_a", "split": "train"}),
                json.dumps({"group_id": "group_a", "split": "dev"}),
            ]) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                resolve_training_splits(rows, manifest, "fixed-seed")

    def test_complete_bullet_type_reaches_sentences_and_passages(self):
        text = "• Một mục đầy đủ. Nội dung tiếp tục trên dòng kế"
        paragraph = {"paragraph_id": "p1", "section_id": "sec", "section_heading": "Mục",
                     "page_index": 0, "content_type": "prose", "unit_type": "bullet_item",
                     "source_spans": [{"page_index": 0, "source_start": 10, "source_end": 60}],
                     "text": text}
        body, clean_paragraphs, sentences = build_clean_text("doc-1", [paragraph])
        self.assertEqual(body, text)
        self.assertEqual(clean_paragraphs[0]["unit_type"], "bullet_item")
        self.assertEqual(len(sentences), 1)
        self.assertEqual(sentences[0]["text"], text)
        self.assertEqual(sentences[0]["unit_type"], "bullet_item")
        passages = make_passages(sentences, target=100, minimum=1, maximum=512,
                                 document_text=body)
        self.assertEqual(passages[0]["unit_type"], "bullet_item")
        self.assertEqual(passages[0]["source_spans"], paragraph["source_spans"])

    def test_training_sentence_ids_are_unique_and_respect_configured_maximum(self):
        text = " ".join(["nội dung nghiên cứu"] * 100)
        paragraphs = [
            {"paragraph_id": f"p{index}", "section_id": "sec", "section_heading": "Mục",
             "page_index": 0, "content_type": "prose", "unit_type": "paragraph", "text": text}
            for index in range(2)
        ]
        body, _, sentences = build_clean_text("doc-1", paragraphs, max_tokens=60)
        self.assertEqual(len({row["sentence_id"] for row in sentences}), len(sentences))
        self.assertTrue(all(row["approx_tokens"] <= 60 for row in sentences))
        passages = make_passages(sentences, target=50, minimum=1, maximum=60,
                                 document_text=body)
        self.assertTrue(all(row["approx_tokens"] <= 60 for row in passages))
    def test_metadata_ids(self):
        self.assertEqual(canonical_metadata_id("ute_970426_title.pdf"), "ute_970426")
        self.assertEqual(canonical_metadata_id("ou_63611_title.pdf"), "ou_63611")
        self.assertEqual(canonical_metadata_id("20474_2354.pdf"), "ou_20474")
        self.assertEqual(canonical_metadata_id("vnu_abc-123_2020_title.pdf"), "vnu_abc-123")

    def test_cover_fields(self):
        text = """TRƯỜNG ĐẠI HỌC MẪU
KHOA CÔNG NGHỆ THÔNG TIN
ĐỀ TÀI
PHÁT HIỆN VĂN BẢN SINH BỞI AI
Sinh viên thực hiện: Nguyễn Văn A
Giảng viên hướng dẫn: TS. Trần Văn B
TP. Hồ Chí Minh, năm 2021"""
        row = cover_metadata(text, "fallback")
        self.assertEqual(row["year"], 2021)
        self.assertTrue(row["authors"])
        self.assertTrue(row["advisors"])
        self.assertIn("PHÁT HIỆN", row["title"])
        self.assertIn("ĐẠI HỌC", row["institution"])

    def test_cover_field_variants(self):
        text = """HO CHI MINH CITY OPEN UNIVERSITY
PHAM HO TOAN
Student Identity: 1751010162
Advisor: Dr. TRUONG HOANG VINH
HO CHI MINH CITY, 2021"""
        row = cover_metadata(text, "Brain tumor classification")
        self.assertEqual(row["year"], 2021)
        self.assertIn("Dr. TRUONG HOANG VINH", row["advisors"])

        vietnamese = """SV Thực hiện:
Nguyễn Thị Hà Vân
MSSV: 10762147
GV Hướng Dẫn:
Th.S Nguyễn Cao Tùng
Thành phố Hồ Chí Minh - Năm 2011"""
        parsed = cover_metadata(vietnamese, "Đề tài")
        self.assertIn("Nguyễn Thị Hà Vân", parsed["authors"])
        self.assertIn("Th.S Nguyễn Cao Tùng", parsed["advisors"])

    def test_provenance(self):
        self.assertEqual(provenance_status(2021), "high_confidence_human")
        self.assertEqual(provenance_status(2022), "high_confidence_human")
        self.assertEqual(provenance_status(2024), "recent_provenance_uncertain")

    def test_person_values_reject_repository_ui_noise(self):
        self.assertEqual(clean_people(["1 : TS. LÃŠ XUÃ‚N TRÆ¯á»œNG"]), ["TS. LÃŠ XUÃ‚N TRÆ¯á»œNG"])
        self.assertEqual(clean_people(["Nguyá»…n VÄƒn A", "NgÃ y sinh: 30/08/1999 NÆ¡i sinh: HÃ  Ná»™i"]), ["Nguyá»…n VÄƒn A"])
        self.assertEqual(clean_people(["12/2025 333 0 Download Tá»« khÃ³a: ISO 27001"]), [])

    def test_institution_aliases(self):
        aliases = [
            "Trường Đại học Mở TP.HCM",
            "TRƯỜNG ĐẠI HỌC MỞ THÀNH PHỐ HỒ CHÍ MINH",
            "TRƯỜNG ĐẠI HỌC MỞ TP.HỒ CHÍ MINH",
            "TRƯỜNG ĐẠI HỌC MỞ",
        ]
        self.assertEqual({normalize_institution(value)["institution_id"] for value in aliases}, {"ou_hcmc"})

    def test_metadata_ontology(self):
        self.assertEqual(normalize_document_type("Luáº­n vÄƒn tháº¡c sÄ©"), "master_thesis")
        self.assertEqual(normalize_document_type("Äá»“ Ã¡n tá»‘t nghiá»‡p"), "capstone_project")
        unit = normalize_academic_unit("Khoa CÃ´ng nghá»‡ thÃ´ng tin")
        self.assertEqual(unit["faculty_id"], "information_technology")
        self.assertEqual(unit["domain_id"], "computer_science")
        self.assertEqual(unit["faculty_name"], "C\u00f4ng ngh\u1ec7 th\u00f4ng tin")
        self.assertEqual(unit["major_name"], "C\u00f4ng ngh\u1ec7 th\u00f4ng tin")

    def test_language_detection(self):
        self.assertEqual(detect_language("ÄÃ¢y lÃ  ná»™i dung nghiÃªn cá»©u vÃ  káº¿t quáº£ cá»§a há»‡ thá»‘ng. " * 20), "vi")
        self.assertEqual(detect_language("This is the result of the research and this is for the system. " * 20), "en")

    def test_cover_year_precedes_repository_year(self):
        doc = {"title": "Fallback", "source_path": "ute_1_title.pdf"}
        official = {
            "title": "Title", "authors": ["Nguyen Van A"], "advisors": [],
            "year": 2022, "degree": "Master thesis", "source": "HCMUTE",
            "school_or_faculty": "Information Technology", "keywords": [],
            "abstract": "", "url": "https://example.test", "id": "ute_1",
        }
        row = merge_metadata(doc, official, "HO CHI MINH CITY, 2019")
        self.assertEqual(row["year"], 2019)
        self.assertEqual(row["repository_year"], 2022)
        self.assertTrue(row["year_conflict"])
        noisy = merge_metadata(doc, official, "HO CHI MINH CITY, 2019\nNgÃ y sinh: 1991", "HO CHI MINH CITY, 2019")
        self.assertEqual(noisy["year"], 2019)


if __name__ == "__main__":
    unittest.main()
