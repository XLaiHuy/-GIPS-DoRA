"""Render source regions for a 400-card primary and 40-card crosscheck review."""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import fitz

from human_v3.core import rows

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("review_dir", type=Path)
    args = parser.parse_args()
    review = args.review_dir.resolve()
    if (review / "primary_cards.csv").exists():
        raise SystemExit("Review cards already exist; refusing to replace decisions")
    sample = list(rows(review / "sample.jsonl"))
    crosscheck = {row["chunk_id"] for row in rows(review / "crosscheck_40.jsonl")}
    by_doc = defaultdict(list)
    for item in sample:
        by_doc[item["document_id"]].append(item)
    images = review / "images"
    images.mkdir(parents=True, exist_ok=True)
    cards = []
    for did, members in sorted(by_doc.items()):
        source = Path(members[0]["source_path"])
        if not source.is_absolute():
            source = ROOT / source
        with fitz.open(source) as pdf:
            for item in members:
                spans = defaultdict(list)
                for span in item["source_spans"]:
                    spans[span["page_index"]].append(span["bbox"])
                paths = []
                for page_index, boxes in sorted(spans.items()):
                    page = pdf[page_index]
                    clip = fitz.Rect(35, max(0, min(b[1] for b in boxes) - 85),
                                     page.rect.width - 35,
                                     min(page.rect.height, max(b[3] for b in boxes) + 55))
                    name = f"{item['chunk_id']}_p{page_index + 1}.jpg"
                    page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), clip=clip, alpha=False).save(images / name)
                    paths.append("images/" + name)
                cards.append({"chunk_id": item["chunk_id"], "document_id": did,
                              "source_family": item["source_family"], "split": item["split"],
                              "year": item["year"], "page_type": item["page_type"],
                              "risk_flag": item["risk_flag"], "sample_weight": item["sample_weight"],
                              "source_path": item["source_path"],
                              "pages": ",".join(str(p + 1) for p in sorted(spans)),
                              "images": ";".join(paths), "text": item["text"],
                              "decision": "", "error_type": "", "reason": "", "reviewer": ""})
    columns = list(cards[0])
    for filename, subset in (("primary_cards.csv", cards),
                             ("crosscheck_cards.csv", [card for card in cards if card["chunk_id"] in crosscheck])):
        with (review / filename).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(subset)
    (review / "README.md").write_text(
        f"# PDF review pack\n\n{len(cards)} primary cards, {len(crosscheck)} independent crosscheck cards, "
        f"{sum(len(card['images'].split(';')) for card in cards)} PDF crops. "
        "Read the PDF crop and chunk together. Record `pass`, `reject`, or `uncertain`, "
        "a reason, and reviewer in the appropriate CSV. The second reviewer must make "
        "an independent decision before seeing the primary decision. "
        "A prepared card is not a completed visual review.\n", encoding="utf-8")
    print(json.dumps({"primary_cards": len(cards), "crosscheck_cards": len(crosscheck),
                      "images": sum(len(card["images"].split(";")) for card in cards)}))


if __name__ == "__main__":
    main()
