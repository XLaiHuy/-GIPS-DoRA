import hashlib
import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from build_dataset import build, build_document, extract_pages
from layout_reconstruction import TextLine, TextSpan


class TextLayerIntegrationTests(unittest.TestCase):
    def _pdf(self, root, text=True, image=False):
        path = Path(root) / "sample.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page(width=612, height=792)
            if text:
                page.insert_text((72, 100), "The first complete sentence.")
                page.insert_text((72, 116), "The second complete sentence.")
            if image:
                pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 4, 4), 0)
                pixmap.clear_with(255)
                page.insert_image(fitz.Rect(100, 100, 150, 150), stream=pixmap.tobytes("png"))
            pdf.save(path)
        return path

    def test_rawdict_offsets_and_image_only_page(self):
        with tempfile.TemporaryDirectory() as root:
            path = self._pdf(root, text=True)
            with fitz.open(path) as pdf:
                extra = fitz.open()
                page = extra.new_page(width=612, height=792)
                pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 4, 4), 0)
                pixmap.clear_with(255)
                page.insert_image(fitz.Rect(100, 100, 150, 150), stream=pixmap.tobytes("png"))
                pdf.insert_pdf(extra)
                pdf.saveIncr()
            pages = extract_pages(path)
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0].extraction_status, "text_extracted")
        self.assertEqual(pages[1].extraction_status, "no_text_layer")
        self.assertEqual(pages[1].raw_text, "")
        self.assertEqual(pages[1].lines, ())
        self.assertEqual(pages[0].raw_sha256,
                         hashlib.sha256(pages[0].raw_text.encode("utf-8")).hexdigest())
        for line in pages[0].lines:
            self.assertEqual(pages[0].raw_text[line.source_start:line.source_end], line.text)
            for span in line.spans:
                self.assertEqual(span.page_index, line.page_index)
                self.assertEqual(pages[0].raw_text[span.source_start:span.source_end], span.text)

    def test_build_document_preserves_split_and_exact_offsets(self):
        with tempfile.TemporaryDirectory() as root:
            pdf_path = self._pdf(root)
            manifest = Path(root) / "split.jsonl"
            manifest.write_text(json.dumps({"document_id": "doc_known", "group_id": "group_known",
                                            "source_file_id": "source_known", "split": "test"}) + "\n",
                                encoding="utf-8")
            result = build_document(pdf_path, {"document_id": "doc_known",
                                                "group_id": "group_known",
                                                "source_file_id": "source_known"}, manifest)
            pdf_sha256 = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        self.assertEqual(result.document["source_pdf_sha256"], pdf_sha256)
        self.assertEqual(result.document["split"], "test")
        self.assertEqual(result.inventory[0]["extraction_status"], "text_extracted")
        body = result.document["text"]
        self.assertTrue(result.sentences)
        self.assertTrue(result.passages)
        self.assertEqual(len({row["sentence_id"] for row in result.sentences}), len(result.sentences))
        for sentence in result.sentences:
            self.assertEqual(body[sentence["doc_char_start"]:sentence["doc_char_end"]], sentence["text"])
            self.assertEqual(sentence["split"], "test")
            for source in sentence["source_spans"]:
                raw = result.pages[source["page_index"]]["raw_text"]
                self.assertEqual(raw[source["source_start"]:source["source_end"]], source["text"])
        for passage in result.passages:
            self.assertEqual(body[passage["start_char"]:passage["end_char"]], passage["text"])
            self.assertEqual(passage["split"], "test")
        self.assertTrue(result.normalization_manifest)

    def test_serialized_page_geometry_and_exact_span_offsets(self):
        with tempfile.TemporaryDirectory() as root:
            path = self._pdf(root)
            result = build_document(path, {"document_id": "doc_geometry", "group_id": "group_geometry"})
        page = result.pages[0]
        self.assertTrue(page["lines"])
        self.assertEqual(page["line_count"], len(page["lines"]))
        for line in page["lines"]:
            self.assertEqual(page["raw_text"][line["source_start"]:line["source_end"]], line["text"])
            self.assertEqual(len(line["bbox"]), 4)
            for span in line["spans"]:
                self.assertEqual(page["raw_text"][span["source_start"]:span["source_end"]], span["text"])
                self.assertTrue(span["font_name"])
                self.assertGreater(span["font_size"], 0)
                self.assertEqual(len(span["bbox"]), 4)

    def test_two_sentences_on_one_line_have_disjoint_source_ranges(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "two-sentences.pdf"
            with fitz.open() as pdf:
                page = pdf.new_page()
                page.insert_text((72, 100), "First complete sentence. Second complete sentence.")
                pdf.save(path)
            result = build_document(path, {"document_id": "doc_two", "group_id": "group_two",
                                           "passage_target": 1, "passage_min": 1})
        self.assertEqual(len(result.sentences), 2)
        first, second = result.sentences
        self.assertLessEqual(first["source_spans"][-1]["source_end"],
                             second["source_spans"][0]["source_start"])
        for row in result.sentences + result.passages:
            for source in row["source_spans"]:
                raw = result.pages[source["page_index"]]["raw_text"]
                self.assertEqual(raw[source["source_start"]:source["source_end"]], source["text"])
        self.assertEqual(result.pages[0]["raw_text"][first["source_spans"][0]["source_start"]:
                                                      first["source_spans"][-1]["source_end"]], first["text"])
        self.assertEqual(result.pages[0]["raw_text"][second["source_spans"][0]["source_start"]:
                                                      second["source_spans"][-1]["source_end"]], second["text"])

    def test_cross_page_joined_unit_uses_sentence_source_page_bounds(self):
        from build_dataset import ExtractedPage
        values = ["The work continues with", "more evidence. A second sentence follows."]
        pages = []
        for index, value in enumerate(values):
            box = (72.0, 744.0, 400.0, 756.0) if index == 0 else (72.0, 48.0, 400.0, 60.0)
            span = TextSpan(value, "Times-Roman", 12.0, box, 0, len(value), index)
            line = TextLine(index, 0, box, value, (span,), 0, len(value))
            pages.append(ExtractedPage(index, 612.0, 792.0, value, [], "pymupdf_rawdict",
                                       False, (line,), hashlib.sha256(value.encode()).hexdigest(),
                                       "text_extracted"))
        with tempfile.TemporaryDirectory() as root:
            path = self._pdf(root, text=False)
            with patch("build_dataset.extract_pages", return_value=pages):
                result = build_document(path, {"document_id": "doc_cross", "group_id": "group_cross",
                                               "passage_min": 1})
        self.assertEqual(len(result.paragraphs), 1)
        self.assertEqual((result.paragraphs[0]["page_start"], result.paragraphs[0]["page_end"]), (0, 1))
        self.assertEqual(len(result.sentences), 2)
        self.assertEqual((result.sentences[0]["page_start"], result.sentences[0]["page_end"]), (0, 1))
        self.assertEqual((result.sentences[1]["page_start"], result.sentences[1]["page_end"]), (1, 1))

    def test_source_alignment_review_unit_is_audited_but_not_packed(self):
        from build_dataset import ExtractedPage
        raw = "First complete sentence. Second complete sentence."
        box = (72.0, 100.0, 400.0, 112.0)
        bad_span = TextSpan("Wrong", "Times-Roman", 12.0, box, 0, 5, 0)
        line = TextLine(0, 0, box, raw, (bad_span,), 0, len(raw))
        page = ExtractedPage(0, 612.0, 792.0, raw, [], "pymupdf_rawdict", False,
                             (line,), hashlib.sha256(raw.encode()).hexdigest(), "text_extracted")
        with tempfile.TemporaryDirectory() as root:
            path = self._pdf(root, text=False)
            with patch("build_dataset.extract_pages", return_value=[page]):
                result = build_document(path, {"document_id": "doc_bad_span",
                                               "group_id": "group_bad_span", "passage_min": 1})
        self.assertEqual(result.paragraphs[0]["normalization_status"], "review_required")
        self.assertIn("source_alignment_unresolved", result.paragraphs[0]["review_flags"])
        self.assertEqual(result.sentences, [])
        self.assertEqual(result.passages, [])

    def test_textless_page_inventory_without_chunk(self):
        with tempfile.TemporaryDirectory() as root:
            path = self._pdf(root, text=False, image=True)
            result = build_document(path, {"document_id": "doc_blank", "group_id": "group_blank"})
        self.assertEqual(result.inventory[0]["extraction_status"], "no_text_layer")
        self.assertEqual(result.inventory[0]["status"], "excluded")
        self.assertEqual(result.inventory[0]["reason"], "no_text_layer")
        self.assertEqual(result.sentences, [])
        self.assertEqual(result.passages, [])

    def test_unknown_glyph_is_quarantined_and_raw_text_is_retained(self):
        raw = "\ue001 Review this source."
        box = (72.0, 100.0, 300.0, 112.0)
        source_span = TextSpan(raw, "UnknownFont", 12.0, box, 0, len(raw), 0)
        line = TextLine(0, 0, box, raw, (source_span,), 0, len(raw))
        from build_dataset import ExtractedPage
        page = ExtractedPage(0, 612.0, 792.0, raw, [], "pymupdf_text", False,
                             (line,), hashlib.sha256(raw.encode()).hexdigest(), "text_extracted")
        with tempfile.TemporaryDirectory() as root:
            pdf_path = self._pdf(root, text=False)
            with patch("build_dataset.extract_pages", return_value=[page]):
                result = build_document(pdf_path, {"document_id": "doc_pua", "group_id": "group_pua"})
        self.assertEqual(result.pages[0]["raw_text"], raw)
        self.assertEqual(result.sentences, [])
        self.assertEqual(result.passages, [])
        self.assertIn("unresolved_pua", result.inventory[0]["review_flags"])

    def test_pdf_extraction_error_keeps_inventory(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "bad.pdf"
            path.write_bytes(b"not a pdf")
            result = build_document(path, {"document_id": "doc_bad", "group_id": "group_bad"})
        self.assertEqual(result.inventory[0]["extraction_status"], "extraction_error")
        self.assertEqual(result.inventory[0]["status"], "excluded")
        self.assertEqual(result.passages, [])

    def test_approved_symbol_rule_is_bound_to_exact_pdf(self):
        from build_dataset import approved_glyph_rules
        source = Path(__file__).resolve().parent.parent / "Dataset_khoaluan" / "63617_VO VAN THUAN.pdf"
        if not source.is_file():
            self.skipTest("reviewed page-49 source PDF unavailable")
        checksum = hashlib.sha256(source.read_bytes()).hexdigest()
        rules = approved_glyph_rules(source, checksum, "doc_source")
        self.assertEqual(rules[("doc_source", "Symbol", 0xF0B7)].replacement, "•")
        self.assertEqual(approved_glyph_rules(source, "0" * 64, "doc_source"), {})

    def test_crawler_inventory_keeps_missing_pdf_and_frozen_group(self):
        with tempfile.TemporaryDirectory() as root:
            input_dir, output_dir = Path(root) / "pdfs", Path(root) / "base"
            input_dir.mkdir()
            sample = self._pdf(input_dir)
            crawler = Path(root) / "crawler.jsonl"
            rows = [{"id": "record_present", "pdf_path": str(sample), "has_full_pdf": True},
                    {"id": "record_missing", "pdf_path": str(input_dir / "missing.pdf"),
                     "has_full_pdf": True}]
            crawler.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            frozen = Path(root) / "frozen.jsonl"
            frozen.write_text(json.dumps({"source_record_id": "record_present",
                                          "group_id": "old_group", "split": "dev"}) + "\n",
                              encoding="utf-8")
            args = argparse.Namespace(input=input_dir, output=output_dir, limit=0,
                                      min_page_chars=1, simhash_distance=0, seed="test",
                                      passage_target=100, passage_min=1, passage_max=512,
                                      crawler_master=crawler, frozen_split_manifest=frozen)
            build(args)
            inventory = [json.loads(line) for line in
                         (output_dir / "manifest" / "inventory.jsonl").read_text(encoding="utf-8").splitlines()]
            splits = [json.loads(line) for line in
                      (output_dir / "manifest" / "split_manifest.jsonl").read_text(encoding="utf-8").splitlines()]
            normalization_exists = (output_dir / "manifest" / "normalization_manifest.jsonl").exists()
        self.assertEqual({row["crawler_id"] for row in inventory},
                         {"record_present", "record_missing"})
        self.assertEqual(next(row for row in inventory if row["crawler_id"] == "record_missing")["reason"],
                         "missing_pdf")
        missing = next(row for row in inventory if row["crawler_id"] == "record_missing")
        self.assertTrue(all(missing[field] for field in ("document_id", "group_id", "source_file_id", "split")))
        self.assertIsNone(missing["source_pdf_sha256"])
        self.assertIn(missing["group_id"], {row["group_id"] for row in splits})
        self.assertEqual(next(row for row in inventory if row["crawler_id"] == "record_present")["split"],
                         "dev")
        self.assertEqual(len(splits), len({row["group_id"] for row in splits}))
        self.assertTrue(normalization_exists)

    def test_near_duplicate_pdf_bytes_share_group_and_training_source(self):
        with tempfile.TemporaryDirectory() as root:
            input_dir, output_dir = Path(root) / "pdfs", Path(root) / "base"
            input_dir.mkdir()
            paths = []
            for index in range(2):
                path = input_dir / f"copy-{index}.pdf"
                with fitz.open() as pdf:
                    page = pdf.new_page()
                    page.insert_text((72, 100),
                                     "A complete shared source sentence for testing " +
                                     ("duplicates." if index == 0 else "variations."))
                    pdf.set_metadata({"title": f"variant-{index}"})
                    pdf.save(path)
                paths.append(path)
            self.assertNotEqual(hashlib.sha256(paths[0].read_bytes()).digest(),
                                hashlib.sha256(paths[1].read_bytes()).digest())
            crawler = Path(root) / "crawler.jsonl"
            crawler.write_text("".join(json.dumps({"id": f"record_{index}", "title": "Shared Title",
                                                   "pdf_path": str(path), "has_full_pdf": True}) + "\n"
                                       for index, path in enumerate(paths)), encoding="utf-8")
            frozen = Path(root) / "frozen.jsonl"
            frozen.write_text(json.dumps({"source_record_id": "record_0",
                                          "group_id": "frozen_shared", "split": "test"}) + "\n",
                              encoding="utf-8")
            args = argparse.Namespace(input=input_dir, output=output_dir, limit=0,
                                      min_page_chars=1, simhash_distance=8, seed="test",
                                      passage_target=100, passage_min=1, passage_max=512,
                                      crawler_master=crawler, frozen_split_manifest=frozen)
            build(args)
            def records(path):
                return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            documents = records(output_dir / "canonical" / "documents.jsonl")
            splits = records(output_dir / "manifest" / "split_manifest.jsonl")
            inventory = records(output_dir / "manifest" / "inventory.jsonl")
        self.assertEqual(len(documents), 2)
        self.assertEqual(len({row["group_id"] for row in documents}), 1)
        self.assertEqual({row["group_id"] for row in documents}, {"frozen_shared"})
        self.assertEqual(len(splits), 1)
        self.assertEqual({row["split"] for row in inventory}, {"test"})
        self.assertEqual(sum(row.get("duplicate_of") is not None for row in documents), 1)
        self.assertIn("simhash_near_duplicate", {row.get("duplicate_method") for row in documents})
        self.assertEqual(sum(row["status"] == "active" for row in documents), 1)

    def test_build_error_keeps_stable_identity_and_split(self):
        with tempfile.TemporaryDirectory() as root:
            input_dir, output_dir = Path(root) / "pdfs", Path(root) / "base"
            input_dir.mkdir()
            sample = self._pdf(input_dir)
            crawler = Path(root) / "crawler.jsonl"
            crawler.write_text(json.dumps({"id": "record_error", "pdf_path": str(sample),
                                          "has_full_pdf": True}) + "\n", encoding="utf-8")
            args = argparse.Namespace(input=input_dir, output=output_dir, limit=0,
                                      simhash_distance=3, seed="test", passage_target=100,
                                      passage_min=1, passage_max=512, crawler_master=crawler,
                                      frozen_split_manifest=None)
            with patch("build_dataset.build_document", side_effect=RuntimeError("injected")):
                build(args)
            inventory = [json.loads(line) for line in
                         (output_dir / "manifest" / "inventory.jsonl").read_text(encoding="utf-8").splitlines()]
            splits = [json.loads(line) for line in
                      (output_dir / "manifest" / "split_manifest.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(inventory), 1)
        self.assertEqual(inventory[0]["reason"], "build_error")
        self.assertTrue(all(inventory[0][field] for field in
                            ("document_id", "group_id", "source_file_id", "split")))
        self.assertEqual(inventory[0]["split"], splits[0]["split"])

    def test_conflicting_frozen_duplicate_groups_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            input_dir, output_dir = Path(root) / "pdfs", Path(root) / "base"
            input_dir.mkdir()
            paths = []
            for index in range(2):
                path = input_dir / f"source-{index}.pdf"
                with fitz.open() as pdf:
                    page = pdf.new_page()
                    page.insert_text((72, 100), "The same source sentence for both frozen documents.")
                    pdf.set_metadata({"title": str(index)})
                    pdf.save(path)
                paths.append(path)
            crawler = Path(root) / "crawler.jsonl"
            crawler.write_text("".join(json.dumps({"id": f"record_{index}",
                                                   "pdf_path": str(path), "has_full_pdf": True}) + "\n"
                                       for index, path in enumerate(paths)), encoding="utf-8")
            frozen = Path(root) / "frozen.jsonl"
            frozen.write_text("".join(json.dumps({"source_record_id": f"record_{index}",
                                                  "group_id": f"frozen_{index}",
                                                  "split": "train" if index == 0 else "dev"}) + "\n"
                                      for index in range(2)), encoding="utf-8")
            args = argparse.Namespace(input=input_dir, output=output_dir, limit=0,
                                      simhash_distance=3, seed="test", passage_target=100,
                                      passage_min=1, passage_max=512, crawler_master=crawler,
                                      frozen_split_manifest=frozen)
            with self.assertRaisesRegex(ValueError, "frozen duplicate groups conflict"):
                build(args)
            self.assertFalse(output_dir.exists())

    def test_empty_normalized_title_allows_simhash_duplicate_group(self):
        with tempfile.TemporaryDirectory() as root:
            input_dir, output_dir = Path(root) / "pdfs", Path(root) / "base"
            input_dir.mkdir()
            paths = []
            for index, noun in enumerate(("duplicates", "variations")):
                path = input_dir / f"different-name-{index}.pdf"
                with fitz.open() as pdf:
                    page = pdf.new_page()
                    page.insert_text((72, 100),
                                     f"A complete shared source sentence for testing {noun}.")
                    pdf.save(path)
                paths.append(path)
            crawler = Path(root) / "crawler.jsonl"
            crawler.write_text("".join(json.dumps({"id": f"record_{index}",
                                                   "title": "!!!" if index == 0 else "Other title",
                                                   "pdf_path": str(path), "has_full_pdf": True}) + "\n"
                                       for index, path in enumerate(paths)), encoding="utf-8")
            args = argparse.Namespace(input=input_dir, output=output_dir, limit=0,
                                      simhash_distance=8, seed="test", passage_target=100,
                                      passage_min=1, passage_max=512, crawler_master=crawler,
                                      frozen_split_manifest=None)
            build(args)
            docs = [json.loads(line) for line in
                    (output_dir / "canonical" / "documents.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len({row["group_id"] for row in docs}), 1)
        self.assertIn("simhash_near_duplicate", {row["duplicate_method"] for row in docs})


if __name__ == "__main__":
    unittest.main()
