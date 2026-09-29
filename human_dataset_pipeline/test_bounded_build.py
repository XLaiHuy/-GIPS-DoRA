"""Bounded staging regressions for the current base release builder."""

import argparse
import gc
import hashlib
import json
import tempfile
import unittest
import weakref
from pathlib import Path
from unittest.mock import patch

from build_dataset import DocumentBuildResult, build


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class BoundedBuildTests(unittest.TestCase):
    def test_completed_results_are_reclaimed_and_outputs_preserve_grouping(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            input_dir, output_dir = root / "pdfs", root / "release"
            input_dir.mkdir()
            paths = [input_dir / "first.pdf", input_dir / "second.pdf"]
            for index, path in enumerate(paths):
                path.write_bytes(f"PDF fixture {index}".encode())
            crawler = root / "crawler.jsonl"
            crawler.write_text("".join(json.dumps(row) + "\n" for row in [
                {"id": "first", "title": "Shared", "pdf_path": str(paths[0]),
                 "has_full_pdf": True},
                {"id": "second", "title": "Shared", "pdf_path": str(paths[1]),
                 "has_full_pdf": True},
                {"id": "missing", "pdf_path": str(input_dir / "missing.pdf"),
                 "has_full_pdf": True},
            ]), encoding="utf-8")
            frozen = root / "frozen.jsonl"
            frozen.write_text(json.dumps({"source_record_id": "first", "group_id": "frozen_group",
                                          "split": "dev"}) + "\n", encoding="utf-8")
            first_ref = None
            built = []

            def fake_build_document(path, record, frozen_path):
                nonlocal first_ref
                if built:
                    gc.collect()
                    self.assertIsNone(first_ref(), "prior DocumentBuildResult stayed reachable")
                    self.assertFalse(output_dir.exists(), "release output appeared during collection")
                built.append(path.name)
                text = "The same complete source sentence."
                result = DocumentBuildResult(
                    {"document_id": record["document_id"], "text": text, "status": "active",
                     "passage_count": 1, "source_record_id": record["source_record_id"]},
                    [{"page_index": 0, "raw_text": "x" * 100000,
                      "extraction_status": "text_extracted"}],
                    [{"paragraph_id": path.stem + "_paragraph", "text": text}],
                    [{"sentence_id": path.stem + "_sentence", "text": text}],
                    [{"passage_id": path.stem + "_passage", "text": text}],
                    [{"page_index": 0, "extraction_status": "text_extracted",
                      "review_flags": ["fixture_flag"]}],
                    [{"page_index": 0, "rules_applied": []}],
                )
                if first_ref is None:
                    first_ref = weakref.ref(result)
                return result

            args = argparse.Namespace(input=input_dir, output=output_dir, limit=0,
                                      simhash_distance=0, seed="bounded-test",
                                      passage_target=100, passage_min=1, passage_max=512,
                                      crawler_master=crawler, frozen_split_manifest=frozen)
            with patch("build_dataset.build_document", side_effect=fake_build_document):
                summary = build(args)

            documents = read_rows(output_dir / "canonical" / "documents.jsonl")
            passages = read_rows(output_dir / "canonical" / "passages.jsonl")
            inventory = read_rows(output_dir / "manifest" / "inventory.jsonl")
            splits = read_rows(output_dir / "manifest" / "split_manifest.jsonl")
            duplicates = read_rows(output_dir / "manifest" / "duplicates.jsonl")
            pages = read_rows(output_dir / "canonical" / "pages.jsonl")
            self.assertEqual(built, ["first.pdf", "second.pdf"])
            self.assertEqual(len(documents), 2)
            self.assertEqual([row["source_record_id"] for row in documents], ["first", "second"])
            self.assertEqual({row["group_id"] for row in documents}, {"frozen_group"})
            self.assertEqual({row["split"] for row in documents}, {"dev"})
            self.assertEqual([row["status"] for row in documents], ["active", "excluded"])
            self.assertEqual(documents[1]["passage_count"], 0)
            self.assertEqual([row["passage_id"] for row in passages], ["first_passage"])
            self.assertEqual([row["page_index"] for row in pages], [0, 0])
            self.assertEqual(pages[0]["raw_text"], "x" * 100000)
            self.assertEqual(len(duplicates), 1)
            self.assertEqual(duplicates[0]["duplicate_method"], "normalized_content_sha256")
            self.assertEqual(len(splits), 2)  # duplicate group plus metadata-only group
            self.assertEqual([row["crawler_id"] for row in inventory],
                             ["first", "second", "missing"])
            self.assertEqual([row["extraction_status"] for row in inventory],
                             ["text_extracted", "text_extracted", "missing_pdf"])
            self.assertEqual(inventory[0]["review_flags"], ["fixture_flag"])
            self.assertIsNone(inventory[2]["source_pdf_sha256"])
            self.assertEqual(summary["counts"]["passages"], 1)
            self.assertEqual(documents[0]["source_sha256"],
                             hashlib.sha256(paths[0].read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
