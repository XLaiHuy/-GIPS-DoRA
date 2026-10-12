"""Render the independent v75 PDF review sample with its source-line boxes."""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "human_v3_2_ready_v73_20261007"


def records(path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    candidate = args.candidate.resolve()
    sample = list(records(candidate / "independent_pdf_review_400.jsonl"))
    wanted = {item["chunk_id"] for item in sample}
    original = {}
    for split in ("train", "dev", "test"):
        for item in records(SOURCE / "chunk_streams" / "ready" / "t192" / f"{split}.jsonl"):
            if item["chunk_id"] in wanted:
                original[item["chunk_id"]] = item
    if len(original) != len(wanted):
        raise ValueError("Sample has chunk IDs outside the immutable source release")

    out = candidate / "pdf_review_400"
    if out.exists():
        raise SystemExit(f"Output already exists: {out}")
    images = out / "images"
    images.mkdir(parents=True)
    by_doc = defaultdict(list)
    for item in sample:
        by_doc[item["document_id"]].append(item)
    cards = []
    for doc_id, items in sorted(by_doc.items()):
        source = Path(items[0]["source_path"])
        if not source.is_absolute():
            source = ROOT / source
        with fitz.open(source) as pdf:
            for item in items:
                spans = defaultdict(list)
                for span in original[item["chunk_id"]]["source_spans"]:
                    spans[span["page_index"]].append(span["bbox"])
                image_paths = []
                for page_index, boxes in sorted(spans.items()):
                    page = pdf[page_index]
                    area = fitz.Rect(35, max(0, min(box[1] for box in boxes) - 85),
                                     page.rect.width - 35,
                                     min(page.rect.height, max(box[3] for box in boxes) + 55))
                    image_name = f"{item['chunk_id']}_p{page_index + 1}.jpg"
                    pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), clip=area, alpha=False)
                    pix.save(images / image_name)
                    image_paths.append("images/" + image_name)
                cards.append({"chunk_id": item["chunk_id"], "document_id": doc_id,
                              "origin": item["origin"], "split": item["split"],
                              "source_path": item["source_path"],
                              "pages": ",".join(str(page + 1) for page in sorted(spans)),
                              "images": ";".join(image_paths), "text": item["text"],
                              "decision": "", "reason": "", "reviewer": ""})
    with (out / "cards.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(cards[0]))
        writer.writeheader()
        writer.writerows(cards)
    (out / "README.md").write_text(
        "# Independent PDF review sample\n\n"
        f"{len(cards)} randomly sampled chunks; {sum(len(c['images'].split(';')) for c in cards)} rendered PDF crops. "
        "Every crop contains the chunk source lines plus nearby page context. "
        "Fill `decision` with `pass`, `reject`, or `uncertain`, and record a reason and reviewer. "
        "The cards are prepared for review; generating images is not a visual attestation.\n",
        encoding="utf-8")
    print(json.dumps({"cards": len(cards), "images": sum(len(c["images"].split(";")) for c in cards),
                      "output": str(out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
