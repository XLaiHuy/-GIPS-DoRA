"""Find source spans under embedded images that are actually unlabelled captions."""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

import fitz

from human_v3.extraction import image_attached_caption_lines
from screen_human_v73_pdf_layout_v75 import overlap_ratio, page_lines, rows, write_row

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "human_v3_2_screened_candidate_v75b_20261007"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "human_v3_2_rebuild_v76_20261007")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    target = out / "caption_hits.jsonl"
    if target.exists():
        raise SystemExit(f"Output exists: {target}")
    documents = {row["document_id"]: row for row in rows(SOURCE / "documents.jsonl")}
    by_doc = defaultdict(list)
    for split in ("train", "dev", "test"):
        for chunk in rows(SOURCE / "chunk_streams" / "pass_candidate" / "t192" / f"{split}.jsonl"):
            by_doc[chunk["document_id"]].append(chunk)
    hits = []
    with target.open("w", encoding="utf-8") as stream:
        for number, (did, chunks) in enumerate(sorted(by_doc.items()), 1):
            source = Path(documents[did]["source_path"])
            if not source.is_absolute():
                source = ROOT / source
            by_page = defaultdict(lambda: defaultdict(list))
            for chunk in chunks:
                for span in chunk["source_spans"]:
                    by_page[span["page_index"]][chunk["chunk_id"]].append(span["bbox"])
            with fitz.open(source) as pdf:
                for page_index, chunk_boxes in by_page.items():
                    page = pdf[page_index]
                    images = page.get_image_info()
                    if not images:
                        continue
                    lines = page_lines(page)
                    if not lines:
                        continue
                    page_record = {"width": page.rect.width, "height": page.rect.height,
                        "lines": lines, "layout_regions": [
                            {"kind": "figure", "method": "pymupdf_image_bbox", "bbox": list(image["bbox"])}
                            for image in images]}
                    marked = image_attached_caption_lines(
                        page_record, statistics.median(line["font_size"] for line in lines))
                    for line in lines:
                        if line["line_index"] not in marked:
                            continue
                        for cid, boxes in chunk_boxes.items():
                            if any(overlap_ratio(line["bbox"], box) >= .65 for box in boxes):
                                hit = {"chunk_id": cid, "document_id": did, "page_index": page_index,
                                    "bbox": line["bbox"], "pdf_line": line["text"][:200]}
                                write_row(stream, hit)
                                hits.append(hit)
            if number % 100 == 0:
                print(f"caption screen {number}/{len(by_doc)} documents", flush=True)
    summary = {"source": SOURCE.name, "hit_count": len(hits),
               "distinct_chunk_count": len({hit["chunk_id"] for hit in hits}),
               "distinct_document_count": len({hit["document_id"] for hit in hits})}
    (out / "caption_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
