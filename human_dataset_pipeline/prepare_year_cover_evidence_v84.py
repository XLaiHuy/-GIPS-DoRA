"""Build a reviewable PDF-cover evidence pack for year-only quarantines."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import fitz

from human_v3.core import JsonlWriter, rows

ROOT = Path(__file__).resolve().parents[1]
SPLITS = ("train", "dev", "test")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    candidate = args.candidate.resolve()
    output = (args.output or candidate / "audit/year_cover_evidence").resolve()
    if output.exists():
        raise SystemExit(f"Refusing to replace existing evidence: {output}")
    docs = {doc["document_id"]: doc for doc in rows(candidate / "documents.jsonl")}
    counts = Counter(chunk["document_id"] for split in SPLITS for chunk in rows(
        candidate / "chunk_streams/quarantine/t192" / f"{split}.jsonl")
        if chunk.get("strict_screening_reasons") == ["year_conflict"])
    output.mkdir(parents=True)
    with JsonlWriter(output / "cards.jsonl") as writer:
        for did, count in sorted(counts.items()):
            doc = docs[did]
            path = Path(doc["source_path"])
            if not path.is_absolute():
                path = ROOT / path
            page_evidence = []
            with fitz.open(path) as pdf:
                for index in range(min(2, len(pdf))):
                    page = pdf[index]
                    raw = page.get_text("text")
                    years = sorted({int(value) for value in re.findall(r"\b(?:19|20)\d{2}\b", raw)})
                    name = f"{did}_page_{index + 1}.jpg"
                    page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2), alpha=False).save(output / name)
                    page_evidence.append({"page_index": index, "image": name, "years_in_text": years,
                                          "cover_excerpt": raw[:1200]})
            writer.write({"document_id": did, "document_type_id": doc.get("document_type_id"),
                          "catalog_url": doc.get("source_url"), "catalog_year": doc.get("year"),
                          "pdf_path": doc["source_path"], "pdf_sha256": doc["source_pdf_sha256"],
                          "year_only_quarantined_chunks": count, "pages": page_evidence,
                          "decision": "pending_cover_visual_review"})
    print(json.dumps({"documents": len(counts), "quarantined_chunks": sum(counts.values()),
                      "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
