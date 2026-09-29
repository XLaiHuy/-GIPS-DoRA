"""Small deliberately damaged releases for structured validation regressions."""

import hashlib
import json
import os
import tempfile
import unittest
import subprocess
import sys
from pathlib import Path

from validate_dataset import validate_release
from validate_paper_release import validate_release as validate_paper_release
from build_review_reports import build_quality_report


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                    encoding="utf-8")


class ReleaseValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "release"
        self.pdf_root = Path(self.temporary.name) / "pdfs"
        self.pdf_root.mkdir()
        (self.pdf_root / "sample.pdf").write_bytes(b"PDF fixture bytes")
        self.crawler = Path(self.temporary.name) / "crawler.jsonl"
        self.crawler.write_text(json.dumps({"id": "crawler_a"}) + "\n", encoding="utf-8")
        self.text = "First complete sentence."
        self.document = {"document_id": "doc_a", "group_id": "group_a", "split": "train",
                         "source_file_id": "source_a", "source_record_id": "crawler_a",
                         "source_path": "sample.pdf",
                         "source_pdf_sha256": hashlib.sha256(b"PDF fixture bytes").hexdigest(),
                         "text": self.text, "status": "active", "duplicate_of": None}
        self.source = {"page_index": 0, "source_start": 0,
                       "source_end": len(self.text), "text": self.text}
        self.page = {"document_id": "doc_a", "page_index": 0,
                     "raw_text": self.text, "raw_sha256": digest(self.text)}
        self.sentence = {"sentence_id": "sent_a", "document_id": "doc_a", "group_id": "group_a",
                         "split": "train", "page_start": 0, "page_end": 0,
                         "doc_char_start": 0, "doc_char_end": len(self.text),
                         "source_spans": [self.source.copy()], "text": self.text,
                         "normalization_status": "active", "review_flags": []}
        self.passage = {"passage_id": "pass_a", "document_id": "doc_a", "group_id": "group_a",
                        "split": "train", "status": "active", "start_char": 0,
                        "end_char": len(self.text), "text": self.text,
                        "text_sha256": digest(self.text), "sentence_ids": ["sent_a"],
                        "source_spans": [self.source.copy()], "page_start": 0, "page_end": 0,
                        "approx_tokens": 6, "short_tail": True, "review_flags": []}
        self.inventory = {"crawler_id": "crawler_a", "source_record_id": "crawler_a",
                          "document_id": "doc_a", "group_id": "group_a", "source_file_id": "source_a",
                          "pdf_path": "sample.pdf", "source_pdf_sha256": self.document["source_pdf_sha256"],
                          "split": "train", "extraction_status": "text_extracted", "status": "active"}
        self.split = {"document_id": "doc_a", "group_id": "group_a",
                      "source_record_id": "crawler_a", "split": "train"}
        self.flush()

    def flush(self):
        write_rows(self.root / "canonical" / "documents.jsonl", [self.document])
        write_rows(self.root / "canonical" / "pages.jsonl", [self.page])
        write_rows(self.root / "canonical" / "paragraphs.jsonl", [])
        write_rows(self.root / "canonical" / "sentences.jsonl", [self.sentence])
        write_rows(self.root / "canonical" / "passages.jsonl", [self.passage])
        write_rows(self.root / "manifest" / "inventory.jsonl", [self.inventory])
        write_rows(self.root / "manifest" / "page_inventory.jsonl", [])
        write_rows(self.root / "manifest" / "raw_files.jsonl", [])
        write_rows(self.root / "manifest" / "split_manifest.jsonl", [self.split])
        write_rows(self.root / "manifest" / "normalization_manifest.jsonl", [])
        (self.root / "reports").mkdir(exist_ok=True)
        (self.root / "reports" / "summary.json").write_text(
            json.dumps({"crawler_rows": 1}), encoding="utf-8")

    def issues(self):
        return validate_release(self.root, pdf_root=self.pdf_root,
                                crawler_master=self.crawler, passage_min=10)

    def assert_code(self, code):
        matches = [issue for issue in self.issues() if issue.code == code]
        self.assertTrue(matches, code)
        for issue in matches:
            self.assertEqual(issue.severity, "error")
            self.assertTrue(issue.message)
            self.assertTrue(issue.document_id or issue.group_id or issue.record_id)

    def test_clean_fixture_has_no_errors(self):
        self.assertEqual(self.issues(), [])

    def test_required_artifacts_fail_closed_without_crawler_master(self):
        empty = Path(self.temporary.name) / "empty"
        empty.mkdir()
        issues = validate_release(empty)
        missing = {issue.record_id for issue in issues if issue.code == "required_artifact_missing"}
        self.assertIn("canonical/documents.jsonl", missing)
        self.assertIn("manifest/inventory.jsonl", missing)
        (self.root / "canonical" / "pages.jsonl").unlink()
        issues = validate_release(self.root, pdf_root=self.pdf_root, crawler_master=self.crawler,
                                  passage_min=10)
        self.assertIn("canonical/pages.jsonl",
                      {issue.record_id for issue in issues if issue.code == "required_artifact_missing"})

    def test_required_checksums_and_active_lineage(self):
        self.document.pop("source_pdf_sha256")
        self.page.pop("raw_sha256")
        self.passage.pop("text_sha256")
        self.sentence.pop("source_spans")
        self.passage.pop("source_spans")
        self.flush()
        write_rows(self.root / "canonical" / "paragraphs.jsonl",
                   [{"paragraph_id": "par_a", "document_id": "doc_a", "group_id": "group_a",
                     "doc_char_start": 0, "doc_char_end": len(self.text), "text": self.text}])
        codes = {issue.code for issue in self.issues()}
        self.assertIn("source_pdf_checksum_missing", codes)
        self.assertIn("raw_page_checksum_missing", codes)
        self.assertIn("text_checksum_missing", codes)
        self.assertIn("source_lineage_missing", codes)

    def test_metadata_only_inventory_allows_null_pdf_checksum(self):
        metadata_only = {"crawler_id": "metadata_only", "source_record_id": "metadata_only",
                         "document_id": "doc_meta", "group_id": "group_meta",
                         "source_file_id": "source_meta", "split": "dev",
                         "extraction_status": "metadata_only", "status": "excluded",
                         "source_pdf_sha256": None}
        write_rows(self.root / "manifest" / "inventory.jsonl", [self.inventory, metadata_only])
        write_rows(self.root / "manifest" / "split_manifest.jsonl",
                   [self.split, {"group_id": "group_meta", "split": "dev"}])
        self.crawler.write_text("".join(json.dumps({"id": value}) + "\n"
                                       for value in ("crawler_a", "metadata_only")), encoding="utf-8")
        self.assertNotIn("source_pdf_checksum_missing", {issue.code for issue in self.issues()})

    def test_missing_crawler_inventory_row(self):
        write_rows(self.root / "manifest" / "inventory.jsonl", [])
        self.assert_code("inventory_incomplete")

    def test_source_pdf_checksum(self):
        self.document["source_pdf_sha256"] = "0" * 64
        self.flush()
        self.assert_code("source_pdf_checksum_mismatch")

    def test_normalized_offset_and_hash(self):
        self.passage["start_char"] = 1
        self.passage["text_sha256"] = "0" * 64
        self.flush()
        self.assert_code("normalized_offset_mismatch")
        self.assert_code("text_checksum_mismatch")

    def test_source_span_must_match_exact_page_slice(self):
        self.sentence["source_spans"][0]["source_end"] -= 1
        self.flush()
        self.assert_code("source_span_invalid")
        issue = next(issue for issue in self.issues() if issue.code == "source_span_invalid")
        self.assertEqual((issue.document_id, issue.group_id, issue.page_index, issue.record_id),
                         ("doc_a", "group_a", 0, "sent_a"))

    def test_paragraph_source_span_is_validated(self):
        paragraph = {"paragraph_id": "par_a", "document_id": "doc_a",
                     "group_id": "group_a", "doc_char_start": 0,
                     "doc_char_end": len(self.text), "text": self.text,
                     "source_spans": [{**self.source, "source_start": 1}]}
        write_rows(self.root / "canonical" / "paragraphs.jsonl", [paragraph])
        match = next(issue for issue in self.issues() if issue.code == "source_span_invalid")
        self.assertEqual((match.document_id, match.page_index, match.record_id),
                         ("doc_a", 0, "par_a"))

    def test_nested_page_span_and_page_hash_are_validated(self):
        self.page["lines"] = [{"line_index": 0, "source_start": 0,
                               "source_end": len(self.text), "text": self.text,
                               "spans": [{"source_start": 1, "source_end": len(self.text),
                                          "text": self.text}]}]
        self.page["raw_sha256"] = "0" * 64
        self.flush()
        spans = [issue for issue in self.issues() if issue.code == "source_span_invalid"]
        self.assertTrue(spans)
        self.assertEqual(spans[0].page_index, 0)
        self.assertIn("page:0:line:0:span:0", spans[0].record_id)
        self.assert_code("raw_page_checksum_mismatch")

    def test_unresolved_glyph_in_active_passage(self):
        self.passage["text"] = "\ue001" + self.text[1:]
        self.flush()
        self.assert_code("unresolved_glyph")

    def test_duplicate_sentence_id(self):
        write_rows(self.root / "canonical" / "sentences.jsonl", [self.sentence, self.sentence])
        self.assert_code("duplicate_sentence_id")

    def test_duplicate_document_cannot_be_active(self):
        self.document["duplicate_of"] = "doc_original"
        self.flush()
        self.assert_code("duplicate_not_excluded")

    def test_token_max_and_unflagged_short_tail(self):
        self.passage["approx_tokens"] = 513
        self.flush()
        self.assert_code("token_limit_exceeded")
        self.passage["approx_tokens"] = 6
        self.passage["short_tail"] = False
        self.flush()
        self.assert_code("short_tail_unflagged")

    def test_frozen_split_and_group_isolation(self):
        self.split["split"] = "dev"
        self.flush()
        self.assert_code("frozen_split_mismatch")
        second = {**self.document, "document_id": "doc_b", "split": "dev",
                  "source_record_id": "crawler_b", "source_file_id": "source_b"}
        write_rows(self.root / "canonical" / "documents.jsonl", [self.document, second])
        self.assert_code("group_split_leak")

    def test_external_frozen_source_identity_is_authoritative(self):
        frozen = Path(self.temporary.name) / "old_split.jsonl"
        write_rows(frozen, [{"source_record_id": "crawler_a", "group_id": "old_group",
                             "split": "dev"}])
        issues = validate_release(self.root, pdf_root=self.pdf_root,
                                  crawler_master=self.crawler,
                                  frozen_split_manifest=frozen, passage_min=10)
        match = next(issue for issue in issues if issue.code == "frozen_split_mismatch")
        self.assertEqual((match.document_id, match.group_id), ("doc_a", "group_a"))

    def test_quality_report_uses_structural_heuristics(self):
        write_rows(self.root / "manifest" / "normalization_manifest.jsonl",
                   [{"document_id": "doc_a", "rules_applied": ["reviewed_symbol_bullet"]}])
        write_rows(self.root / "manifest" / "page_inventory.jsonl",
                   [{"document_id": "doc_a", "page_index": 1,
                     "extraction_status": "extraction_error", "status": "excluded"}])
        report = build_quality_report(self.root)
        self.assertEqual(report["heuristic_label"], "structural_nonterminal_sentence_like_units")
        self.assertEqual(report["by_document"]["doc_a"]["short_tails"], 1)
        self.assertEqual(report["by_split"]["train"]["passage_tokens"]["max"], 6)
        self.assertEqual(report["by_document"]["doc_a"]["glyph_rule_count"], 1)
        self.assertEqual(report["by_split"]["train"]["extraction_failures"], 1)
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent / "build_review_reports.py"),
             str(self.root)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        written = json.loads((self.root / "reports" / "quality_report.json").read_text(encoding="utf-8"))
        self.assertEqual(written["by_document"]["doc_a"]["glyph_rule_count"], 1)

    def test_paper_release_validator_returns_structured_source_issue(self):
        paper = Path(self.temporary.name) / "paper"
        write_rows(paper / "documents.jsonl", [self.document])
        write_rows(paper / "sentences.jsonl", [self.sentence])
        write_rows(paper / "passages" / "all.jsonl", [self.passage])
        write_rows(paper / "manifest" / "source_files.jsonl",
                   [{"source_file_id": "source_a", "document_id": "doc_a",
                     "relative_path": "sample.pdf", "sha256": "0" * 64,
                     "release_outcome": "accepted"}])
        write_rows(paper / "manifest" / "split_manifest.jsonl", [self.split])
        (paper / "dataset_info.json").write_text(
            json.dumps({"dataset_release": "human_written_dataset_v2_15_paper"}),
            encoding="utf-8")
        before = {path.relative_to(paper).as_posix(): path.read_bytes()
                  for path in paper.rglob("*") if path.is_file()}
        issues = validate_paper_release(paper, pdf_root=self.pdf_root)
        after = {path.relative_to(paper).as_posix(): path.read_bytes()
                 for path in paper.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        match = next(issue for issue in issues if issue.code == "source_pdf_checksum_mismatch")
        self.assertEqual(match.document_id, "doc_a")
        self.assertEqual(match.record_id, "source_a")
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent / "validate_paper_release.py"),
             str(paper), "--pdf-root", str(self.pdf_root)],
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(completed.returncode, 1)
        report = json.loads(completed.stdout)
        checksum_issue = next(issue for issue in report["issues"]
                              if issue["code"] == "source_pdf_checksum_mismatch")
        self.assertEqual(set(checksum_issue), {
            "severity", "code", "document_id", "group_id", "page_index", "record_id", "message",
        })

    def test_paper_lineage_pages_are_authoritative_and_missing_reference_fails(self):
        paper = Path(self.temporary.name) / "paper_lineage"
        write_rows(paper / "documents.jsonl", [self.document])
        sentence = {**self.sentence, "source_spans": [{**self.source, "page_index": 99}]}
        write_rows(paper / "sentences.jsonl", [sentence])
        write_rows(paper / "paragraphs.jsonl", [{"paragraph_id": "par_a",
                   "document_id": "doc_a", "source_spans": [{**self.source, "page_index": 99}]}])
        write_rows(paper / "passages" / "all.jsonl", [{**self.passage,
                   "source_spans": [{**self.source, "page_index": 99}]}])
        write_rows(paper / "lineage" / "source_pages.jsonl", [self.page])
        issues = validate_paper_release(paper, pdf_root=self.pdf_root)
        missing = [issue for issue in issues if issue.code == "source_page_missing"]
        self.assertEqual(len(missing), 3)
        self.assertEqual({issue.page_index for issue in missing}, {99})

    def test_paper_offsets_reject_negative_and_out_of_range_values(self):
        paper = Path(self.temporary.name) / "paper_offsets"
        write_rows(paper / "documents.jsonl", [self.document])
        write_rows(paper / "sentences.jsonl", [{**self.sentence,
                   "document_char_start": -1, "document_char_end": len(self.text)}])
        write_rows(paper / "passages" / "all.jsonl", [{**self.passage,
                   "start_char": 0, "end_char": len(self.text) + 1}])
        issues = validate_paper_release(paper, pdf_root=self.pdf_root)
        self.assertEqual(sum(issue.code == "normalized_offset_mismatch" for issue in issues), 2)

    def test_paper_paragraph_offsets_reject_negative_overrun_and_text_mismatch(self):
        paper = Path(self.temporary.name) / "paper_paragraph_offsets"
        write_rows(paper / "documents.jsonl", [self.document])
        write_rows(paper / "lineage" / "source_pages.jsonl", [self.page])
        write_rows(paper / "paragraphs.jsonl", [
            {"paragraph_id": "par_negative", "document_id": "doc_a",
             "document_char_start": -1, "document_char_end": len(self.text), "text": self.text},
            {"paragraph_id": "par_overrun", "document_id": "doc_a",
             "document_char_start": 0, "document_char_end": len(self.text) + 1, "text": self.text},
            {"paragraph_id": "par_mismatch", "document_id": "doc_a",
             "document_char_start": 0, "document_char_end": len(self.text), "text": "Wrong text"},
        ])
        issues = validate_paper_release(paper, pdf_root=self.pdf_root)
        self.assertEqual({issue.record_id for issue in issues
                          if issue.code == "normalized_offset_mismatch"},
                         {"par_negative", "par_overrun", "par_mismatch"})

    def test_paper_source_spans_require_text_and_page_index(self):
        paper = Path(self.temporary.name) / "paper_span_text"
        write_rows(paper / "documents.jsonl", [self.document])
        write_rows(paper / "lineage" / "source_pages.jsonl", [self.page])
        without_text = {key: value for key, value in self.source.items() if key != "text"}
        write_rows(paper / "paragraphs.jsonl", [{"paragraph_id": "par_missing_text",
                   "document_id": "doc_a", "source_spans": [without_text]}])
        write_rows(paper / "sentences.jsonl", [{**self.sentence,
                   "source_spans": [without_text]}])
        write_rows(paper / "passages" / "all.jsonl", [{**self.passage,
                   "source_spans": [{key: value for key, value in self.source.items()
                                     if key != "page_index"}]}])
        issues = validate_paper_release(paper, pdf_root=self.pdf_root)
        self.assertEqual({issue.record_id for issue in issues if issue.code == "source_span_invalid"},
                         {"par_missing_text", "sent_a", "pass_a"})

    def test_quality_states_do_not_count_excluded_as_reviewed(self):
        write_rows(self.root / "canonical" / "paragraphs.jsonl", [
            {"document_id": "doc_a", "status": "excluded", "review_flags": ["source_unverified"]},
            {"document_id": "doc_a", "normalization_status": "review_required"},
            {"document_id": "doc_a", "duplicate_of": "doc_original", "status": "excluded"},
        ])
        write_rows(self.root / "canonical" / "sentences.jsonl", [
            self.sentence, {"document_id": "doc_a", "text": "A bullet without terminal punctuation",
                            "normalization_status": "review_required"},
        ])
        row = build_quality_report(self.root)["by_document"]["doc_a"]
        self.assertEqual(row["excluded_records"], 1)
        self.assertEqual(row["reviewed_records"], 2)
        self.assertEqual(row["duplicate_records"], 1)
        self.assertEqual(row["structural_nonterminal_sentence_like_units"], 1)

    def test_complete_paper_cli_reports_structured_issues_without_writing(self):
        paper = Path(self.temporary.name) / "paper_complete"
        (paper / "manifest").mkdir(parents=True)
        info = {"dataset_release": "human_written_dataset_v2_15_paper",
                "schema_version": "paper-human-v1.0", "pipeline_version": "2.15.0",
                "counts": {"audited_source_files": 1, "crawler_records": 0,
                           "crawler_records_with_local_pdf": 0, "crawler_metadata_only": 0,
                           "crawler_pdf_missing_from_local": 0, "unmatched_local_pdf_files": 1}}
        (paper / "dataset_info.json").write_text(json.dumps(info), encoding="utf-8")
        (paper / "VERSION.json").write_text(json.dumps({key: info[key] for key in
            ("dataset_release", "schema_version", "pipeline_version")}), encoding="utf-8")
        write_rows(paper / "manifest" / "source_files.jsonl", [{
            "document_id": "doc_a", "source_file_id": "source_a", "relative_path": "sample.pdf",
            "release_outcome": "failed",
            "extraction_method": "pymupdf_text_layer"}])
        write_rows(paper / "manifest" / "pdf_inventory.jsonl", [{
            "source_path": "sample.pdf", "sha256": self.document["source_pdf_sha256"]}])
        write_rows(paper / "manifest" / "unmatched_pdf_files.jsonl", [{"source_path": "sample.pdf"}])
        for relative in ("manifest/crawler_records.jsonl", "manifest/metadata_records.jsonl",
                         "documents.jsonl", "sentences.jsonl", "paragraphs.jsonl",
                         "passages/all.jsonl", "manifest/excluded_passages.jsonl",
                         "manifest/split_manifest.jsonl", "splits/train.jsonl",
                         "splits/dev.jsonl", "splits/test.jsonl"):
            write_rows(paper / relative, [])
        for name in ("document", "sentence", "paragraph", "passage", "source_file"):
            path = paper / "schemas" / f"{name}.schema.json"
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema",
                                        "type": "object", "required": ["id"]}), encoding="utf-8")
        files = sorted(path for path in paper.rglob("*") if path.is_file())
        (paper / "manifest" / "checksums.sha256").write_text("".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(paper).as_posix()}\n"
            for path in files), encoding="utf-8")
        before = {path.relative_to(paper).as_posix(): path.read_bytes()
                  for path in paper.rglob("*") if path.is_file()}
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent / "validate_paper_release.py"),
             str(paper), "--pdf-root", str(self.pdf_root)],
            capture_output=True, text=True, encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        self.assertEqual(completed.returncode, 1, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertIn("errors", report)
        self.assertIn("issues", report)
        missing = next(issue for issue in report["issues"]
                       if issue["code"] == "source_pdf_checksum_missing")
        self.assertEqual(set(missing), {"severity", "code", "document_id", "group_id",
                                         "page_index", "record_id", "message"})
        self.assertEqual((missing["document_id"], missing["record_id"]),
                         ("doc_a", "source_a"))
        self.assertEqual(before, {path.relative_to(paper).as_posix(): path.read_bytes()
                                  for path in paper.rglob("*") if path.is_file()})


if __name__ == "__main__":
    unittest.main()
