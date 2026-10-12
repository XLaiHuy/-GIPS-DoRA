"""Triage local PDFs absent from the current corpus; never auto-admit them."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import fitz

from human_v3.core import JsonlWriter, file_sha, rows

ROOT = Path(__file__).resolve().parents[1]
ACADEMIC = re.compile(r"kh[oó]a\s+lu[aậ]n|lu[aậ]n\s+v[aă]n|lu[aậ]n\s+[aá]n|[đd]ồ\s+[aá]n", re.I)
COMPUTING = re.compile(r"công\s+nghệ\s+thông\s+tin|khoa\s+học\s+máy\s+tính|phần\s+mềm|"
                       r"hệ\s+thống\s+thông\s+tin|mạng\s+máy\s+tính|lập\s+trình|thuật\s+toán|"
                       r"xử\s+lý\s+ảnh|trí\s+tuệ\s+nhân\s+tạo|cơ\s+sở\s+dữ\s+liệu|"
                       r"bảo\s+mật|an\s+toàn\s+thông\s+tin|website|web\s+site|"
                       r"computer\s+science|information\s+technology", re.I)
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
CHAPTER = re.compile(r"(?im)^\s*(?:chương|chapter)\s+(?:[1-9]|[ivx]+)\b")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, default=ROOT / "Dataset_khoaluan")
    parser.add_argument("--candidate", type=Path,
                        default=ROOT / "human_v3_2_dedup_candidate_v85_20261007")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "human_v3_2_unused_local_pdf_audit_v87_20261007")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"Refusing to overwrite: {output}")
    docs = list(rows(args.candidate.resolve() / "documents.jsonl"))
    known_sha = {doc["source_pdf_sha256"] for doc in docs}
    known_paths = {Path(doc["source_path"]).name.casefold() for doc in docs}
    paths = sorted(path for path in args.source_dir.resolve().rglob("*.pdf")
                   if path.name.casefold() not in known_paths)
    output.mkdir(parents=True)
    counts = Counter()
    with JsonlWriter(output / "records.jsonl") as writer:
        for path in paths:
            record_id = re.match(r"^(\d+)", path.name)
            item = {"pdf_path": str(path.relative_to(ROOT)), "filename": path.name,
                    "catalog_url": (f"https://thuvien.ou.edu.vn/module/chi-tiet-sach?RecordID={record_id.group(1)}"
                                    if record_id else None),
                    "catalog_url_status": "derived_from_filename_not_verified",
                    "rights_basis": "existing_local_pdf_project_owner_internal_use_attestation",
                    "status": "source_review_required"}
            try:
                sha = file_sha(path)
                item["pdf_sha256"] = sha
                if sha in known_sha:
                    item["status"] = "duplicate_pdf_already_in_corpus"
                    counts["duplicate_pdf"] += 1
                with fitz.open(path) as pdf:
                    item["pages"] = len(pdf)
                    indices = sorted({0, 1, 2, len(pdf) // 3, len(pdf) // 2,
                                      2 * len(pdf) // 3, len(pdf) - 2, len(pdf) - 1}
                                     & set(range(len(pdf))))
                    sampled = {index: pdf[index].get_text("text") for index in indices}
                front = "\n".join(sampled[index] for index in indices if index <= 2)
                body = "\n".join(sampled[index] for index in indices if 2 < index < item["pages"] - 2)
                item["cover_years"] = sorted({int(y) for y in YEAR.findall(front)})
                item["academic_title_signal"] = bool(ACADEMIC.search(front))
                item["computing_title_signal"] = bool(COMPUTING.search(front))
                item["computing_body_sample_signal"] = bool(COMPUTING.search(body))
                item["chapter_marker_sample_pages"] = [index for index, value in sampled.items()
                                                       if CHAPTER.search(value)]
                item["sampled_text_chars"] = sum(len(value) for value in sampled.values())
                if item["status"] != "duplicate_pdf_already_in_corpus":
                    if (item["academic_title_signal"] and item["computing_title_signal"]
                            and item["computing_body_sample_signal"]
                            and any(year <= 2022 for year in item["cover_years"])):
                        item["status"] = "priority_document_review"
                        counts["priority_document_review"] += 1
                    else:
                        counts["lower_priority_or_exclude"] += 1
            except (OSError, ValueError, RuntimeError, fitz.FileDataError) as exc:
                item["status"] = "pdf_unreadable"
                item["error"] = f"{type(exc).__name__}: {exc}"[:200]
                counts["pdf_unreadable"] += 1
            writer.write(item)
    summary = {"candidate": args.candidate.resolve().name,
               "local_pdf_files_absent_by_filename": len(paths), "counts": dict(counts),
               "ready_chunks_added": 0,
               "note": "Cover/body sample signals are triage only; catalog, rights, year, fulltext and IT scope still require review."}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
