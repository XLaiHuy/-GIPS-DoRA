"""Recover official OU catalog links for local PDFs with missing source URL.

Only an exact catalog PDF filename plus matching record ID clears provenance.
This verifies catalog identity, not an online bitstream hash or public license.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

from human_v3.core import JsonlWriter, rows

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "human_v3_2_rebuilt_candidate_v79_20261007"
BASE = "https://thuvien.ou.edu.vn"


def fetch(doc: dict) -> dict:
    did = doc["document_id"]
    filename = Path(doc["source_path"]).name
    match = re.match(r"^(\d{3,7})[_-]", filename)
    if not match:
        return {"document_id": did, "decision": "unresolved_no_record_id_in_filename"}
    record_id = match.group(1)
    result = {"document_id": did, "record_id": record_id,
              "local_pdf_filename": filename,
              "local_pdf_sha256": doc["source_pdf_sha256"],
              "official_item_url": f"{BASE}/module/chi-tiet-sach?RecordID={record_id}",
              "api_file_url": f"{BASE}/api/Book/GetRecordContent?id={record_id}&type=file",
              "api_field_url": f"{BASE}/api/Book/GetRecordContent?id={record_id}&type=field"}
    try:
        response = httpx.get(result["api_file_url"], timeout=20)
        response.raise_for_status()
        records = response.json()
        files = [item for record in records for item in record.get("ListFiles", [])]
        names = [item.get("SaveFileName") for item in files]
        matching = [item for item in files
                    if (item.get("SaveFileName") or "").casefold() == filename.casefold()]
        prefixed = [item for item in files if (
            f"{record_id}_{item.get('SaveFileName') or ''}").casefold() == filename.casefold()]
        result["catalog_pdf_filenames"] = names
        result["filename_exact_match"] = bool(matching)
        result["filename_recordid_prefix_match"] = bool(prefixed)
        if matching or prefixed:
            result["catalog_record_document_code"] = (matching or prefixed)[0].get("RecordDocumentCode")
        field_response = httpx.get(result["api_field_url"], timeout=20)
        field_response.raise_for_status()
        fields = field_response.json()
        tags = {field.get("Tag"): field.get("Value") for field in fields}
        result["catalog_record_id"] = (tags.get("001") or [None])[0]
        result["catalog_title"] = (tags.get("245") or [None])[0]
        result["catalog_publication"] = (tags.get("260") or [None])[0]
        result["catalog_author"] = (tags.get("100") or [None])[0]
        result["decision"] = (
            "official_record_filename_confirmed" if matching and
            str(result["catalog_record_id"]) == record_id else
            "official_record_prefixed_filename_confirmed" if prefixed and
            str(result["catalog_record_id"]) == record_id else
            "unresolved_catalog_mismatch")
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        result["decision"] = "unresolved_api_error"
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def main() -> None:
    target = SOURCE / "audit/ou_source_provenance_v80b.jsonl"
    if target.exists():
        raise SystemExit(f"Output exists: {target}")
    docs = {doc["document_id"]: doc for doc in rows(SOURCE / "documents.jsonl")}
    flagged = set()
    for split in ("train", "dev", "test"):
        for chunk in rows(SOURCE / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl"):
            if "source_evidence_requires_review" in chunk.get("review_reasons", []):
                flagged.add(chunk["document_id"])
    results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch, docs[did]): did for did in sorted(flagged)}
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda row: row["document_id"])
    with JsonlWriter(target) as writer:
        for row in results:
            writer.write(row)
    summary = {"source": SOURCE.name, "flagged_documents": len(flagged),
               "decisions": dict(Counter(row["decision"] for row in results)),
               "note": "Filename match corroborates official catalog identity; it does not verify a public reuse license or remote PDF hash."}
    (SOURCE / "audit/ou_source_provenance_v80b_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
