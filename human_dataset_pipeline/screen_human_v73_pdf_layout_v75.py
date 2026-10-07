"""Screen v73 chunks against PDF list geometry and prior review decisions.

This produces a new *candidate*, never changes the owner-approved v73 release.
Only exact page/line geometry hits are automatically quarantined. Everything
else remains a candidate until the independent PDF visual audit is complete.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import fitz

from human_v3.extraction import wrapped_list_lines

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "human_v3_2_ready_v73_20261007"
TRIAGE = ROOT / "human_v3_2_quality_estimate_v74_20261007" / "text_triage_decisions.jsonl"
DEFAULT_OUT = ROOT / "human_v3_2_screened_candidate_v75_20261007"


def rows(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def write_row(stream, row: dict):
    stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def page_lines(page) -> list[dict]:
    flags = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES
    data = page.get_text("dict", flags=flags, sort=True)
    result = []
    for block in data["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            value = "".join(span["text"] for span in spans)
            if not value.strip():
                continue
            n = len(result)
            result.append({"line_index": n, "text": value, "bbox": list(line["bbox"]),
                           "font_size": statistics.median(span["size"] for span in spans),
                           "bold": any(span.get("flags", 0) & 16 for span in spans),
                           "source_start": n, "source_end": n + 1})
    return result


def overlap_ratio(a, b) -> float:
    width = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = max(1.0, (b[2] - b[0]) * (b[3] - b[1]))
    return width * height / area


def list_hits(page, chunk_spans: list[dict]) -> list[dict]:
    lines = page_lines(page)
    if not lines:
        return []
    page_record = {"lines": lines}
    continuation = wrapped_list_lines(lines, page_record,
                                      statistics.median(line["font_size"] for line in lines))
    hits = []
    for line in lines:
        if line["line_index"] not in continuation:
            continue
        for span in chunk_spans:
            if overlap_ratio(line["bbox"], span["bbox"]) >= .65:
                hits.append({"page_index": span["page_index"],
                             "source_start": span["source_start"],
                             "bbox": line["bbox"], "pdf_line": line["text"][:160]})
                break
    return hits


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--sample-size", type=int, default=400)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():
        raise SystemExit(f"Output already exists: {out}")
    out.mkdir(parents=True)
    (out / "chunk_streams" / "pass_candidate" / "t192").mkdir(parents=True)
    (out / "quarantine" / "t192").mkdir(parents=True)

    documents = {row["document_id"]: row for row in rows(SOURCE / "documents.jsonl")}
    decisions = {row["chunk_id"]: row for row in rows(TRIAGE)}
    by_doc = defaultdict(list)
    for split in ("train", "dev", "test"):
        for row in rows(SOURCE / "chunk_streams" / "ready" / "t192" / f"{split}.jsonl"):
            by_doc[row["document_id"]].append(row)

    quarantined, passed = [], []
    errors = []
    for number, (doc_id, chunks) in enumerate(sorted(by_doc.items()), 1):
        doc = documents[doc_id]
        source = Path(doc["source_path"])
        if not source.is_absolute():
            source = ROOT / source
        needed = defaultdict(list)
        for chunk in chunks:
            for span in chunk["source_spans"]:
                needed[span["page_index"]].append((chunk["chunk_id"], span))
        affected = defaultdict(list)
        try:
            with fitz.open(source) as pdf:
                for page_index, refs in needed.items():
                    if not 0 <= page_index < len(pdf):
                        raise ValueError(f"page {page_index} outside PDF")
                    spans_by_chunk = defaultdict(list)
                    for chunk_id, span in refs:
                        spans_by_chunk[chunk_id].append(span)
                    page = pdf[page_index]
                    # Extract once per PDF page; compare only the chunks on it.
                    lines = page_lines(page)
                    if not lines:
                        continue
                    continued = wrapped_list_lines(lines, {"lines": lines},
                                                   statistics.median(line["font_size"] for line in lines))
                    for line in lines:
                        if line["line_index"] not in continued:
                            continue
                        for chunk_id, spans in spans_by_chunk.items():
                            if any(overlap_ratio(line["bbox"], span["bbox"]) >= .65 for span in spans):
                                affected[chunk_id].append({"page_index": page_index,
                                    "bbox": line["bbox"], "pdf_line": line["text"][:160]})
        except (OSError, ValueError, RuntimeError) as exc:
            errors.append({"document_id": doc_id, "source_path": str(source),
                           "reason": f"{type(exc).__name__}: {exc}"})
            for chunk in chunks:
                affected[chunk["chunk_id"]].append({"reason": "pdf_unavailable_for_layout_screen"})
        for chunk in chunks:
            cid = chunk["chunk_id"]
            reasons = []
            if affected[cid]:
                reasons.append("pdf_wrapped_list_continuation" if "pdf_line" in affected[cid][0]
                               else "pdf_unavailable_for_layout_screen")
            decision = decisions.get(cid)
            if decision and decision["decision"] == "reject":
                reasons.append("v74_text_review_reject")
            elif decision and decision["decision"] == "uncertain":
                reasons.append("v74_text_review_uncertain")
            item = dict(chunk)
            item["release_status"] = "quarantine" if reasons else "pass_candidate"
            item["v75_screening_reasons"] = reasons
            if reasons:
                item["v75_pdf_layout_evidence"] = affected[cid]
                quarantined.append(item)
            else:
                passed.append(item)
        if number % 100 == 0:
            print(f"screened {number}/{len(by_doc)} documents", flush=True)

    for split in ("train", "dev", "test"):
        target = out / "chunk_streams" / "pass_candidate" / "t192" / f"{split}.jsonl"
        with target.open("w", encoding="utf-8") as stream:
            for item in passed:
                if item["split"] == split:
                    write_row(stream, item)
    with (out / "quarantine" / "t192" / "screened_rejects.jsonl").open("w", encoding="utf-8") as stream:
        for item in quarantined:
            write_row(stream, item)

    rng = random.Random(20261007)
    strata = defaultdict(list)
    for item in passed:
        strata[(item["candidate_origin"].split("_")[0], item["split"])].append(item)
    samples = []
    for key, members in sorted(strata.items()):
        amount = max(1, round(args.sample_size * len(members) / len(passed)))
        samples.extend(rng.sample(members, min(amount, len(members))))
    if len(samples) > args.sample_size:
        samples = rng.sample(samples, args.sample_size)
    elif len(samples) < args.sample_size:
        selected = {item["chunk_id"] for item in samples}
        samples.extend(rng.sample([item for item in passed if item["chunk_id"] not in selected],
                                  args.sample_size - len(samples)))
    with (out / "independent_pdf_review_400.jsonl").open("w", encoding="utf-8") as stream:
        for item in samples:
            doc = documents[item["document_id"]]
            write_row(stream, {"chunk_id": item["chunk_id"], "document_id": item["document_id"],
                "split": item["split"], "origin": item["candidate_origin"],
                "institution_id": doc.get("institution_id"), "year": doc.get("year"),
                "source_path": doc["source_path"],
                "pages": sorted({span["page_index"] for span in item["source_spans"]}),
                "text": item["text"], "decision": None, "review_basis": "pending_pdf_visual_review"})

    split_counts = Counter(item["split"] for item in passed)
    reason_counts = Counter(reason for item in quarantined for reason in item["v75_screening_reasons"])
    summary = {"source_release": SOURCE.name, "status": "screened_candidate_not_ready",
        "source_chunk_count": len(passed) + len(quarantined),
        "pass_candidate_chunk_count": len(passed), "quarantine_chunk_count": len(quarantined),
        "pass_candidate_document_count": len({item["document_id"] for item in passed}),
        "split_pass_candidate_counts": dict(split_counts), "quarantine_reason_counts": dict(reason_counts),
        "pdf_open_errors": errors, "independent_pdf_visual_review_sample_count": len(samples),
        "completed_visual_reviews_in_this_build": 0,
        "known_limitations": ["List geometry screen is high precision, not a complete prose/scope audit.",
            "Text-review rejects and uncertain cases remain quarantined until PDF adjudication.",
            "No 400-page visual attestation is implied by generating the review sample."]}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "documents.jsonl").write_bytes((SOURCE / "documents.jsonl").read_bytes())
    for path in sorted(out.rglob("*.jsonl")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        print(digest, path.relative_to(out).as_posix())
    print(json.dumps({k: v for k, v in summary.items() if k != "pdf_open_errors"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
