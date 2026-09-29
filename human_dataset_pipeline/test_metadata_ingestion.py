import json
import tempfile
import unittest
from pathlib import Path

import build_paper_release
import build_training_view


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


class RepositoryMetadataIngestionTests(unittest.TestCase):
    def test_loads_master_and_github_metadata_without_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            github = {
                "id": "github_user_project_thesis.pdf",
                "title": "A thesis",
                "has_full_pdf": True,
                "pdf_path": "D:/pdfs/GITHUB_user_project_thesis.pdf",
            }
            ou = {"id": "ou_123", "title": "Another thesis"}
            write_jsonl(root / "dataset_cntt_all.jsonl", [github, ou])
            write_jsonl(root / "github_theses.jsonl", [{**github, "title": "Stale source copy", "has_full_pdf": False}])

            records = build_training_view.load_repository_metadata(root)

            self.assertEqual(set(records), {github["id"], ou["id"]})
            self.assertEqual(records[github["id"]]["title"], "A thesis")
            self.assertTrue(records[github["id"]]["has_full_pdf"])

    def test_matches_source_pdf_to_metadata_path_after_mojibake_repair(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = {
                "id": "ou_123",
                "title": "Thesis",
                "pdf_path": "D:/pdfs/bao_cao_bÃ¡n_cÃ¡i.pdf",
            }
            write_jsonl(root / "dataset_cntt_all.jsonl", [record])
            records = build_training_view.load_repository_metadata(root)
            finder = getattr(build_training_view, "find_repository_metadata", None)
            self.assertTrue(callable(finder), "PDF path metadata lookup is missing")
            self.assertIs(finder("BAO_CAO_bán_cái.pdf", records), records[record["id"]])


class CrawlerInventoryTests(unittest.TestCase):
    def test_inventory_accounts_for_linked_metadata_only_and_unmatched_pdfs(self):
        builder = getattr(build_paper_release, "build_crawler_inventory", None)
        self.assertTrue(callable(builder), "crawler inventory builder is missing")
        if not callable(builder):
            return

        crawl_records = [
            {
                "id": "ou_123",
                "has_full_pdf": True,
                "pdf_path": "D:/pdfs/thesis.pdf",
                "title": "Thesis",
            },
            {"id": "vnu_456", "has_full_pdf": False, "pdf_path": None},
            {
                "id": "github_missing.pdf",
                "has_full_pdf": True,
                "pdf_path": "D:/pdfs/missing.pdf",
            },
        ]
        pdf_inventory = [
            {
                "source_path": "thesis.pdf",
                "sha256": "a" * 64,
                "document_id": "doc_123",
                "logical_record_id": "ou_123",
                "outcome": "accepted",
            },
            {
                "source_path": "unlinked.pdf",
                "sha256": "b" * 64,
                "document_id": "doc_unlinked",
                "logical_record_id": None,
                "outcome": "quarantine",
            },
        ]
        source_files = [
            {
                "relative_path": "thesis.pdf",
                "source_file_id": "source_123",
                "document_id": "doc_123",
                "repository_record_id": "ou_123",
                "release_outcome": "accepted",
            }
        ]

        rows, unmatched_pdfs = builder(crawl_records, pdf_inventory, source_files)

        by_id = {row["metadata_record_id"]: row for row in rows}
        self.assertEqual(len(rows), 3)
        self.assertEqual(by_id["ou_123"]["record_status"], "pdf_present")
        self.assertEqual(by_id["ou_123"]["source_file_id"], "source_123")
        self.assertEqual(by_id["vnu_456"]["record_status"], "metadata_only")
        self.assertEqual(by_id["github_missing.pdf"]["record_status"], "pdf_missing_from_local")
        self.assertEqual([row["source_path"] for row in unmatched_pdfs], ["unlinked.pdf"])


if __name__ == "__main__":
    unittest.main()
