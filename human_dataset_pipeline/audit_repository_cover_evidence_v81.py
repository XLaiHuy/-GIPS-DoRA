"""Check GitHub-hosted thesis PDFs for cover evidence before source approval.

This is a document triage aid. A textual cover match does not certify a PDF's
authorship or institution without visual inspection of a stratified sample.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import fitz

from human_v3.core import JsonlWriter, fold, rows

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "human_v3_2_provenance_candidate_v80_20261007"
THESIS = ("do an tot nghiep", "khoa luan tot nghiep", "luan van tot nghiep",
          "luan van thac si", "bao cao khoa luan", "bao cao do an", "do an cntt")
COMPUTING = ("cong nghe thong tin", "khoa hoc may tinh", "ky thuat phan mem",
             "he thong thong tin", "mang may tinh")


def main() -> None:
    target = SOURCE / "audit/repository_cover_evidence_v81.jsonl"
    if target.exists():
        raise SystemExit(f"Output exists: {target}")
    documents = {doc["document_id"]: doc for doc in rows(SOURCE / "documents.jsonl")}
    flagged = set()
    for split in ("train", "dev", "test"):
        for chunk in rows(SOURCE / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl"):
            if "institution_is_repository_placeholder" in chunk.get("review_reasons", []):
                flagged.add(chunk["document_id"])
    decisions = Counter()
    with JsonlWriter(target) as writer:
        for did in sorted(flagged):
            doc = documents[did]
            path = Path(doc["source_path"])
            if not path.is_absolute():
                path = ROOT / path
            with fitz.open(path) as pdf:
                page_count = len(pdf)
                first_pages = [fold(pdf[index].get_text())
                               for index in range(min(8, page_count))]
            thesis_pages = [index for index, text in enumerate(first_pages)
                            if any(term in text for term in THESIS)]
            computing_pages = [index for index, text in enumerate(first_pages)
                               if any(term in text for term in COMPUTING)]
            year_pages = [index for index, text in enumerate(first_pages)
                          if str(doc["year"]) in text]
            github = (doc.get("source_url") or "").startswith("https://github.com/")
            decision = ("cover_text_supports_thesis_and_computing" if
                        github and thesis_pages and computing_pages and year_pages else
                        "needs_visual_source_review")
            decisions[decision] += 1
            writer.write({"document_id": did, "source_url": doc.get("source_url"),
                          "source_path": doc["source_path"],
                          "source_pdf_sha256": doc["source_pdf_sha256"],
                          "catalog_year": doc.get("year"), "page_count": page_count,
                          "thesis_marker_pages": thesis_pages,
                          "computing_marker_pages": computing_pages,
                          "year_marker_pages": year_pages,
                          "decision": decision})
    summary = {"candidate": SOURCE.name, "flagged_documents": len(flagged),
               "decision_counts": dict(decisions),
               "note": "Cover text markers only support triage; no placeholder flag is cleared automatically."}
    (SOURCE / "audit/repository_cover_evidence_v81_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
