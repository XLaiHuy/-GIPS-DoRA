"""Join local PDF triage with catalog metadata and prior review flags."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import fitz

from human_v3.core import JsonlWriter, rows

ROOT = Path(__file__).resolve().parents[1]
NON_TECH_FIELD = re.compile(r"quản\s+trị|kinh\s+tế|luật|tài\s+chính|marketing|ngân\s+hàng", re.I)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path,
                        default=ROOT / "human_v3_2_unused_local_pdf_audit_v87_20261007")
    args = parser.parse_args()
    base = args.audit.resolve()
    catalog = {}
    for row in rows(ROOT / "DATASET_CNTT_SAU_PREPROCESS/json/ou_it_theses.jsonl"):
        match = re.search(r"RecordID=(\d+)", row.get("url") or "")
        if match:
            catalog[match.group(1)] = row
    prior = {Path(row.get("source_path") or "").name.casefold(): row
             for row in rows(ROOT / "human_v3_1_review_app/decisions.pending.jsonl")
             if row.get("kind") == "document"}
    output = base / "priority_review_queue.jsonl"
    if output.exists():
        raise SystemExit(f"Refusing to overwrite: {output}")
    counts = Counter()
    with JsonlWriter(output) as writer:
        for item in rows(base / "records.jsonl"):
            if item["status"] != "priority_document_review":
                continue
            match = re.search(r"RecordID=(\d+)", item.get("catalog_url") or "")
            meta = catalog.get(match.group(1)) if match else None
            review = prior.get(item["filename"].casefold())
            path = ROOT / item["pdf_path"]
            with fitz.open(path) as pdf:
                cover = pdf[0].get_text("text")[:1500]
                middle = pdf[len(pdf) // 2].get_text("text")[:1200]
            field = (meta or {}).get("school_or_faculty") or ""
            reasons = list((review or {}).get("review_reasons") or [])
            if NON_TECH_FIELD.search(field):
                counts["metadata_noncomputing_field"] += 1
            if not meta:
                counts["missing_catalog_metadata"] += 1
            if reasons:
                counts["prior_review_flags"] += 1
            writer.write({"filename": item["filename"], "pdf_path": item["pdf_path"],
                          "pdf_sha256": item["pdf_sha256"], "pages": item["pages"],
                          "catalog_url": item["catalog_url"],
                          "catalog_url_status": item["catalog_url_status"],
                          "catalog_title": (meta or {}).get("title"),
                          "catalog_year": (meta or {}).get("year"),
                          "catalog_degree": (meta or {}).get("degree"),
                          "catalog_faculty": field or None,
                          "metadata_noncomputing_field": bool(NON_TECH_FIELD.search(field)),
                          "prior_review_reasons": reasons,
                          "cover_excerpt": cover, "middle_page_excerpt": middle,
                          "decision": "pending_document_level_review"})
    summary = {"priority_documents": sum(1 for _ in rows(output)), "flags": dict(counts),
               "ready_chunks_added": 0}
    (base / "priority_review_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
