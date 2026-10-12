"""Screen at most 100 newly authorized thesis PDFs before extraction.

Input is one JSON object per lead. This step records evidence and rejects
missing rights/fulltext/scope reviews; it never labels chunks as ready.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import fitz

from human_v3.core import JsonlWriter, file_sha, rows

ROOT = Path(__file__).resolve().parents[1]
CORE_TYPES = {"bachelor_thesis", "master_thesis", "doctoral_thesis", "capstone_project"}
CHAPTER = re.compile(r"(?im)^\s*(?:chương|chapter)\s+(?:[1-9]|[ivx]+)\b")


def screen(lead: dict, existing_sha: set[str]) -> dict:
    reasons = []
    required = ("catalog_url", "pdf_url", "pdf_path", "institution_id", "document_type_id",
                "year", "language", "computing_scope_evidence", "rights_evidence",
                "rights_reviewed_by", "rights_reviewed_at", "source_pdf_sha256")
    for key in required:
        if lead.get(key) in (None, "", []):
            reasons.append("missing_" + key)
    if lead.get("document_type_id") not in CORE_TYPES:
        reasons.append("not_thesis_or_capstone")
    if not isinstance(lead.get("year"), int) or not 1990 <= lead.get("year", 0) <= 2022:
        reasons.append("year_not_verified_pre_2023")
    if lead.get("language") not in {"vi", "vie"}:
        reasons.append("not_verified_vietnamese")
    if lead.get("rights_status") != "internal_research_cleared":
        reasons.append("internal_rights_not_approved")
    if not (lead.get("rights_evidence_url") or lead.get("rights_evidence_path")):
        reasons.append("document_specific_rights_proof_missing")
    elif lead.get("rights_evidence_path"):
        proof = Path(lead["rights_evidence_path"])
        if not proof.is_absolute():
            proof = ROOT / proof
        if not proof.is_file():
            reasons.append("document_specific_rights_proof_file_missing")
    elif urlparse(lead["rights_evidence_url"]).scheme not in {"http", "https"}:
        reasons.append("document_specific_rights_proof_url_invalid")
    if lead.get("fulltext_review_status") != "verified_fulltext":
        reasons.append("fulltext_review_pending")
    if lead.get("computing_scope_review_status") != "verified_technical_computing":
        reasons.append("body_computing_scope_review_pending")
    if not lead.get("fulltext_reviewed_by") or not lead.get("scope_reviewed_by"):
        reasons.append("document_level_reviewers_missing")
    path = Path(lead.get("pdf_path") or "")
    if not path.is_absolute():
        path = ROOT / path
    observed_sha, page_count, text_chars, chapter_pages = None, None, None, None
    if not path.is_file():
        reasons.append("pdf_missing")
    else:
        try:
            observed_sha = file_sha(path)
            if observed_sha != lead.get("source_pdf_sha256"):
                reasons.append("pdf_sha256_mismatch")
            if observed_sha in existing_sha:
                reasons.append("pdf_already_in_corpus")
            with fitz.open(path) as pdf:
                page_count = len(pdf)
                pages = [page.get_text("text") for page in pdf]
            text_chars = sum(len(value) for value in pages)
            chapter_pages = [index for index, value in enumerate(pages) if CHAPTER.search(value)]
            if not chapter_pages or text_chars < 10_000:
                reasons.append("fulltext_or_text_layer_ambiguous_requires_page_review")
        except (fitz.FileDataError, OSError, RuntimeError, ValueError):
            reasons.append("pdf_unreadable")
    result = {**lead, "observed_pdf_sha256": observed_sha, "pdf_pages": page_count,
              "pdf_text_chars": text_chars, "chapter_marker_pages": chapter_pages,
              "screening_reasons": sorted(set(reasons)),
              "screening_status": "eligible_for_extraction_review" if not reasons else "quarantine"}
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("leads", type=Path, help="JSONL with local PDF and document-specific evidence")
    parser.add_argument("--existing", type=Path,
                        default=ROOT / "human_v3_2_dedup_candidate_v85_20261007/documents.jsonl")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"Refusing to overwrite: {output}")
    leads = list(rows(args.leads.resolve()))
    if not 1 <= len(leads) <= 100:
        raise SystemExit("Each pilot batch must contain 1 to 100 leads")
    existing_sha = {row["source_pdf_sha256"] for row in rows(args.existing.resolve())}
    output.mkdir(parents=True)
    results = [screen(lead, existing_sha) for lead in leads]
    with JsonlWriter(output / "screened_leads.jsonl") as writer:
        for result in results:
            writer.write(result)
    summary = {"leads": len(leads),
               "eligible_for_extraction_review": sum(row["screening_status"] ==
                                                     "eligible_for_extraction_review" for row in results),
               "reason_counts": dict(Counter(reason for row in results
                                             for reason in row["screening_reasons"])),
               "ready_chunks_added": 0}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
