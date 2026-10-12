"""Draw a reproducible, stratified PDF review sample from a frozen candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

import fitz

from human_v3.core import JsonlWriter, rows

ROOT = Path(__file__).resolve().parents[1]
SPLITS = ("train", "dev", "test")


def year_band(value) -> str:
    year = int(value or 0)
    if year <= 0:
        return "unknown"
    if year <= 2009:
        return "through_2009"
    if year <= 2014:
        return "2010_2014"
    if year <= 2019:
        return "2015_2019"
    return "2020_2022"


def source_family(document: dict) -> str:
    return document.get("institution_id") or "unknown_institution"


def population_digest(chunks: list[dict], documents: dict[str, dict]) -> str:
    """Bind a review draw to the exact eligible rows, splits, text and PDFs."""
    digest = hashlib.sha256()
    for row in sorted(chunks, key=lambda item: item["chunk_id"]):
        record = [row["chunk_id"], row["document_id"], row["split"],
                  hashlib.sha256(row["text"].encode("utf-8")).hexdigest(),
                  documents[row["document_id"]]["source_pdf_sha256"]]
        digest.update(json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def page_type(page) -> str:
    area = max(1, page.rect.width * page.rect.height)
    if any((image["bbox"][2] - image["bbox"][0]) *
           (image["bbox"][3] - image["bbox"][1]) >= area * .015
           for image in page.get_image_info()):
        return "image_mixed"
    substantial = 0
    for drawing in page.get_drawings():
        box = drawing.get("rect")
        if box and box.width * box.height >= area * .003:
            substantial += 1
        if substantial >= 4:
            return "vector_mixed"
    return "text_only"


def allocate(strata: dict, total: int) -> dict:
    population = sum(len(members) for members in strata.values())
    quotas = {key: total * len(members) / population for key, members in strata.items()}
    if len(strata) > total:
        raise ValueError("Sample is too small to cover every nonempty stratum")
    counts = {key: max(1, int(value)) for key, value in quotas.items()}
    while sum(counts.values()) < total:
        key = max(strata, key=lambda k: (quotas[k] - counts[k], len(strata[k])))
        counts[key] += 1
    while sum(counts.values()) > total:
        key = max((k for k in strata if counts[k] > 1),
                  key=lambda k: (counts[k] - quotas[k], counts[k]))
        counts[key] -= 1
    return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--kind", choices=("development", "final"), default="development")
    parser.add_argument("--seed", type=int, default=20261008)
    parser.add_argument("--size", type=int, default=400)
    parser.add_argument("--output-name", type=str,
                        help="Separate review directory name for a corrected or pilot draw")
    args = parser.parse_args()
    candidate = args.candidate.resolve()
    out = candidate / (args.output_name or f"{args.kind}_pdf_review_{args.size}")
    if out.exists():
        raise SystemExit(f"Output already exists: {out}")
    documents = {doc["document_id"]: doc for doc in rows(candidate / "documents.jsonl")}
    by_doc = defaultdict(list)
    for split in SPLITS:
        for chunk in rows(candidate / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl"):
            by_doc[chunk["document_id"]].append(chunk)
    annotated = []
    for number, (did, chunks) in enumerate(sorted(by_doc.items()), 1):
        path = Path(documents[did]["source_path"])
        if not path.is_absolute():
            path = ROOT / path
        needed = {span["page_index"] for chunk in chunks for span in chunk["source_spans"]}
        with fitz.open(path) as pdf:
            page_types = {index: page_type(pdf[index]) for index in needed}
        for chunk in chunks:
            types = {page_types[span["page_index"]] for span in chunk["source_spans"]}
            layout = ("image_mixed" if "image_mixed" in types else
                      "vector_mixed" if "vector_mixed" in types else "text_only")
            risk = bool(set(chunk.get("review_reasons", [])) & {
                "computing_scope_requires_review", "body_boundaries_require_review",
                "language_requires_review", "source_evidence_requires_review"})
            origin = source_family(documents[did])
            row = {"chunk_id": chunk["chunk_id"], "document_id": did,
                   "split": chunk["split"], "source_family": origin,
                   "institution_id": documents[did].get("institution_id"),
                   "year": documents[did].get("year"),
                   "year_band": year_band(documents[did].get("year")),
                   "document_type_id": documents[did].get("document_type_id"),
                   "page_type": layout,
                   "risk_flag": risk, "source_path": documents[did]["source_path"],
                   "source_spans": chunk["source_spans"], "text": chunk["text"]}
            annotated.append(row)
        if number % 100 == 0:
            print(f"classified page types {number}/{len(by_doc)} documents", flush=True)
    strata = defaultdict(list)
    for row in annotated:
        key = (row["source_family"], row["year_band"], row["split"],
               row["page_type"], row["risk_flag"])
        strata[key].append(row)
    counts = allocate(strata, args.size)
    rng = random.Random(args.seed)
    selected = []
    for key, members in sorted(strata.items()):
        amount = counts[key]
        if not amount:
            continue
        for row in rng.sample(members, amount):
            row["sampling_stratum"] = list(key)
            row["sample_weight"] = len(members) / amount
            selected.append(row)
    rng.shuffle(selected)
    out.mkdir(parents=True)
    with JsonlWriter(out / "sample.jsonl") as writer:
        for row in selected:
            writer.write(row)
    by_source = defaultdict(list)
    for row in selected:
        by_source[row["source_family"]].append(row)
    crosscheck = []
    for family, members in sorted(by_source.items()):
        quota = max(1, round(40 * len(members) / len(selected)))
        crosscheck.extend(rng.sample(members, min(quota, len(members))))
    if len(crosscheck) > 40:
        crosscheck = rng.sample(crosscheck, 40)
    elif len(crosscheck) < 40:
        used = {row["chunk_id"] for row in crosscheck}
        crosscheck.extend(rng.sample([row for row in selected if row["chunk_id"] not in used],
                                     40 - len(crosscheck)))
    with JsonlWriter(out / "crosscheck_40.jsonl") as writer:
        for row in crosscheck:
            writer.write({"chunk_id": row["chunk_id"], "document_id": row["document_id"],
                          "source_path": row["source_path"], "source_spans": row["source_spans"],
                          "decision": None, "reason": None, "reviewer": None})
    report = {"kind": args.kind, "sample_size": len(selected), "seed": args.seed,
              "population_chunks": len(annotated),
              "population_sha256": population_digest(annotated, documents),
              "unique_documents": len({row["document_id"] for row in selected}),
              "crosscheck_size": len(crosscheck),
              "source_family": dict(Counter(row["source_family"] for row in selected)),
              "page_type": dict(Counter(row["page_type"] for row in selected)),
              "risk_flag": dict(Counter(str(row["risk_flag"]) for row in selected)),
              "split": dict(Counter(row["split"] for row in selected)),
              "year": dict(Counter(str(row["year"]) for row in selected)),
              "year_band": dict(Counter(row["year_band"] for row in selected)),
              "document_type_id": dict(Counter(row["document_type_id"] for row in selected)),
              "stratum_population": {str(key): len(members) for key, members in strata.items()},
              "stratum_sample": {str(key): counts[key] for key in strata}}
    (out / "sample_design.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if not k.startswith("stratum")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
