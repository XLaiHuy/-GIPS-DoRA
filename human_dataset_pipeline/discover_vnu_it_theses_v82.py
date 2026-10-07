"""Find and pilot official VNU IT thesis PDFs; discoveries are not corpus-ready.

Uses the repository's public DSpace API. PDF availability is evidence of access,
not a redistribution licence. Downloaded files stay in a local pilot directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import fitz
import httpx

from human_v3.core import JsonlWriter, rows

ROOT = Path(__file__).resolve().parents[1]
API = "https://repository.vnu.edu.vn/server/api"
SEARCH_TERMS = (
    "công nghệ thông tin", "khoa học máy tính", "phần mềm", "học máy",
    "xử lý ảnh", "an toàn thông tin", "mạng máy tính",
)
IT = re.compile(
    r"công nghệ thông tin|khoa học máy tính|phần mềm|học máy|trí tuệ nhân tạo|"
    r"xử lý ảnh|thị giác máy tính|an toàn thông tin|mạng máy tính|thuật toán|"
    r"cơ sở dữ liệu|khai phá dữ liệu|hệ thống thông tin|computer science|"
    r"information technology|software engineering|machine learning|"
    r"computer vision|cybersecurity",
    re.I,
)
EXCLUDE = re.compile(r"thư viện|giáo dục|sư phạm|kinh doanh|kế toán|tài chính|ngôn ngữ", re.I)


def values(item: dict, key: str) -> list[str]:
    return [str(row.get("value", "")) for row in item.get("metadata", {}).get(key, [])]


def get(client: httpx.Client, url: str, **params) -> dict:
    response = client.get(url, params=params or None)
    response.raise_for_status()
    return response.json()


def existing_handles() -> set[str]:
    path = ROOT / "human_v3_2_provenance_candidate_v80_20261007/documents.jsonl"
    result = set()
    for doc in rows(path):
        match = re.search(r"VNU_123/\d+", doc.get("source_url") or "")
        if match:
            result.add(match.group())
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages-per-term", type=int, default=2)
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--download-limit", type=int, default=20)
    parser.add_argument("--year", type=int, action="append",
                        help="Repeat to restrict discovery to one or more issue years")
    parser.add_argument("--out", type=Path,
                        default=ROOT / "human_v3_2_vnu_source_pilot_v82_20261007")
    args = parser.parse_args()
    if not 0 <= args.download_limit <= 100 or not 1 <= args.pages_per_term <= 20:
        raise SystemExit("Pilot limits are intentionally bounded")
    out = args.out.resolve()
    if (out / "manifest.json").exists():
        raise SystemExit(f"Output already exists: {out}")
    seen = existing_handles()
    leads = {}
    with httpx.Client(timeout=30, follow_redirects=True,
                      headers={"User-Agent": "HumanCorpusResearchPilot/1.0"}) as client:
        for term in SEARCH_TERMS:
            for year_filter, page in ((year, page) for year in (args.year or [None])
                                      for page in range(args.pages_per_term)):
                filters = {"f.itemtype": "Thesis,equals"}
                if year_filter is not None:
                    filters["f.dateIssued"] = f"{year_filter},equals"
                result = get(client, f"{API}/discover/search/objects", query=term,
                             size=args.page_size, page=page, **filters)
                objects = result.get("_embedded", {}).get("searchResult", {}).get("_embedded", {}).get("objects", [])
                for hit in objects:
                    item = hit.get("_embedded", {}).get("indexableObject", {})
                    handle = item.get("handle", "")
                    if not handle or handle in seen or handle in leads:
                        continue
                    years = values(item, "dc.date.issued")
                    year_match = re.search(r"\b(?:19|20)\d{2}\b", years[0]) if years else None
                    if not year_match or not 1990 <= int(year_match.group()) <= 2022:
                        continue
                    languages = values(item, "dc.language.iso")
                    if languages and not any(value.lower() in {"vi", "vie", "vietnamese"} for value in languages):
                        continue
                    title = item.get("name", "")
                    subjects = values(item, "dc.subject")
                    evidence = " ".join([title, *subjects])
                    if not IT.search(evidence) or EXCLUDE.search(title):
                        continue
                    leads[handle] = {
                        "handle": handle, "item_uuid": item["uuid"], "title": title,
                        "year": int(year_match.group()), "subjects": subjects,
                        "languages": languages, "item_url": f"https://repository.vnu.edu.vn/handle/{handle}",
                        "rights_status": "pending_document_specific_review",
                        "source_status": "metadata_lead",
                    }
                print(f"searched {ascii(term)} year {year_filter} page {page + 1}; "
                      f"unique leads {len(leads)}", flush=True)
        downloaded = 0
        for lead in leads.values():
            if downloaded >= args.download_limit:
                break
            try:
                bundles = get(client, f"{API}/core/items/{lead['item_uuid']}/bundles")
                originals = [b for b in bundles.get("_embedded", {}).get("bundles", [])
                             if b.get("name") == "ORIGINAL"]
                if not originals:
                    continue
                bitstreams = get(client, originals[0]["_links"]["bitstreams"]["href"])
                pdfs = [b for b in bitstreams.get("_embedded", {}).get("bitstreams", [])
                        if b.get("name", "").lower().endswith(".pdf")
                        and 100_000 <= b.get("sizeBytes", 0) <= 50_000_000]
                if not pdfs:
                    continue
                bitstream = max(pdfs, key=lambda row: row["sizeBytes"])
                url = f"https://repository.vnu.edu.vn/bitstreams/{bitstream['uuid']}/download"
                response = client.get(url, timeout=90)
                response.raise_for_status()
                if not response.content.startswith(b"%PDF-"):
                    continue
                sha = hashlib.sha256(response.content).hexdigest()
                path = out / "pdf" / f"vnu_{lead['item_uuid']}.pdf"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(response.content)
                with fitz.open(path) as pdf:
                    pages = len(pdf)
                    text_chars = sum(len(page.get_text("text")) for page in pdf)
                lead.update({"source_status": "pilot_pdf_downloaded", "pdf_path": str(path.relative_to(ROOT)),
                             "pdf_url": url, "pdf_filename": bitstream["name"], "pdf_sha256": sha,
                             "pdf_bytes": len(response.content), "pdf_pages": pages,
                             "text_chars": text_chars,
                             "fulltext_pilot_eligible": pages >= 30 and text_chars >= 20_000})
                downloaded += 1
                print(f"downloaded {downloaded}/{args.download_limit}: {lead['handle']} "
                      f"{pages} pages, {text_chars} chars", flush=True)
            except (httpx.HTTPError, fitz.FileDataError, KeyError) as exc:
                lead["download_error"] = type(exc).__name__ + ": " + str(exc)[:150]
    with JsonlWriter(out / "leads.jsonl") as writer:
        for lead in leads.values():
            writer.write(lead)
    result = {"source": "VNU official DSpace API", "status": "pilot_not_ingested",
              "lead_count": len(leads), "downloaded_pdf_count": downloaded,
              "fulltext_pilot_eligible_count": sum(bool(x.get("fulltext_pilot_eligible")) for x in leads.values()),
              "rights_scope": "pending_document_specific_review",
              "notes": ["No lead is automatically admitted to the Human corpus.",
                        "PDF access alone does not prove redistribution rights or IT scope."]}
    (out / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
