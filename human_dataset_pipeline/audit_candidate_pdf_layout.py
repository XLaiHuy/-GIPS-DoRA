"""Screen every candidate source span for residual PDF list/caption/image/table overlap."""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import fitz

from human_v3.core import JsonlWriter, rows
from human_v3.extraction import image_attached_caption_lines, list_item_line_reason, wrapped_list_lines
from screen_human_v73_pdf_layout_v75 import overlap_ratio, page_lines

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--tag", default="layout_residual")
    args = parser.parse_args()
    candidate = args.candidate.resolve()
    target = candidate / "audit" / f"{args.tag}_hits.jsonl"
    if target.exists():
        raise SystemExit(f"Output exists: {target}")
    documents = {doc["document_id"]: doc for doc in rows(candidate / "documents.jsonl")}
    by_doc = defaultdict(list)
    for split in ("train", "dev", "test"):
        for chunk in rows(candidate / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl"):
            by_doc[chunk["document_id"]].append(chunk)
    counters = Counter()
    hit_chunks = defaultdict(set)
    with JsonlWriter(target) as writer:
        for number, (did, chunks) in enumerate(sorted(by_doc.items()), 1):
            path = Path(documents[did]["source_path"])
            if not path.is_absolute():
                path = ROOT / path
            by_page = defaultdict(lambda: defaultdict(list))
            for chunk in chunks:
                for span in chunk["source_spans"]:
                    by_page[span["page_index"]][chunk["chunk_id"]].append(span["bbox"])
            with fitz.open(path) as pdf:
                for index, chunks_on_page in by_page.items():
                    page = pdf[index]
                    lines = page_lines(page)
                    if not lines:
                        continue
                    images = page.get_image_info()
                    regions = [{"kind": "figure", "method": "pymupdf_image_bbox",
                                "bbox": list(image["bbox"])} for image in images]
                    record = {"width": page.rect.width, "height": page.rect.height,
                              "lines": lines, "layout_regions": regions,
                              "small_image_markers": [list(image["bbox"]) for image in images
                                  if 3 <= image["bbox"][2] - image["bbox"][0] <= 26
                                  and 3 <= image["bbox"][3] - image["bbox"][1] <= 26
                                  and image["bbox"][0] < page.rect.width * .42]}
                    median = statistics.median(line["font_size"] for line in lines)
                    list_lines = wrapped_list_lines(lines, record, median)
                    captions = image_attached_caption_lines(record, median)
                    for line in lines:
                        reason = ("image_marker_list_item" if
                                  list_item_line_reason(line, record) == "image_marker_list_item" else
                                  "wrapped_list" if line["line_index"] in list_lines else
                                  "image_attached_caption" if line["line_index"] in captions else None)
                        if not reason:
                            continue
                        for cid, boxes in chunks_on_page.items():
                            if any(overlap_ratio(line["bbox"], box) >= .65 for box in boxes):
                                writer.write({"chunk_id": cid, "document_id": did, "page_index": index,
                                              "reason": reason, "bbox": line["bbox"],
                                              "pdf_line": line["text"][:160]})
                                counters[reason] += 1
                                hit_chunks[reason].add(cid)
                    for image in images:
                        box = image["bbox"]
                        area = (box[2] - box[0]) * (box[3] - box[1])
                        if area < page.rect.width * page.rect.height * .015:
                            continue
                        for cid, boxes in chunks_on_page.items():
                            if any(overlap_ratio(box, chunk_box) >= .65 for chunk_box in boxes):
                                writer.write({"chunk_id": cid, "document_id": did,
                                              "page_index": index, "reason": "source_span_inside_image",
                                              "bbox": list(box)})
                                counters["source_span_inside_image"] += 1
                                hit_chunks["source_span_inside_image"].add(cid)
            if number % 100 == 0:
                print(f"layout replay {number}/{len(by_doc)} documents", flush=True)
    summary = {"candidate": candidate.name, "hit_rows": dict(counters),
               "distinct_hit_chunks": {key: len(value) for key, value in hit_chunks.items()},
               "any_hit_chunks": len(set().union(*hit_chunks.values())) if hit_chunks else 0}
    (candidate / "audit" / f"{args.tag}_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
