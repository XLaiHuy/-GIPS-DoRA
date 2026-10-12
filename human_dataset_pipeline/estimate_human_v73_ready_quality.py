#!/usr/bin/env python3
"""Reproduce a text-only, source-stratified quality audit of Human ready v73.

The fixed decisions below are assistant triage, not a full PDF attestation.
They must not be interpreted as a certified ready rate.
"""
from __future__ import annotations

import json
import math
import random
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASE = ROOT / "human_v3_2_ready_v73_20261007"
OUT = ROOT / "human_v3_2_quality_estimate_v74_20261007"
SEED = 20261007
SAMPLE_PER_ORIGIN = 60

# Reject only when the observed text or a rendered source page has a concrete
# scope/format/content defect under the project's prose-only CNTT rubric.
REJECT = {
    1: "community-device placement; no computing prose in chunk",
    11: "IT education policy rather than computing analysis",
    14: "imperative sequence of API-attribute configuration items",
    16: "insurance market strategy outside computing scope",
    17: "HR recruitment context without a computing method",
    22: "PDF shows bold caption beneath a report screenshot, not body prose",
    24: "numbered 1)-4) phase list embedded in chunk",
    28: "HR promotion approval workflow outside computing scope",
    31: "generic enterprise collaboration practices",
    37: "two component-list labels joined in one chunk",
    52: "generic business-transformation motivation",
    55: "book-inventory business problem without computing content",
    56: "stock-market background outside computing scope",
    57: "PDF URL wraps across lines; extracted URL contains an inserted space",
    58: "problem statement about medical-device administration without computing analysis",
    64: "personal project conclusion instead of technical body prose",
    69: "NP-complete label/list-like entry and run-on formatting",
    81: "electronic warranty/legal-value description rather than computing prose",
    84: "generic computers-and-Internet introduction without a technical point",
    86: "PDF shows the text is inside a bullet item although marker vanished",
    92: "ATM component labels joined as a list",
    97: "single command-syntax instruction rather than continuous prose",
    103: "PDF shows multiple criterion labels merged into a chunk",
    104: "personal closing remarks instead of technical body prose",
    106: "warehouse clerical workflow without computing content",
    108: "severely malformed prose in source PDF, unsuitable for clean-text target",
    111: "unmarked series of retrieval-mode labels and definitions",
    112: "sequence of imperative SEO checklist items",
    113: "severely broken translated prose",
    117: "event-management workflow outside computing scope",
}
UNCERTAIN = {
    13: "general mobile-map context; computing scope depends on adjacent section",
    33: "AR real-estate application prose; technical-content threshold is policy-sensitive",
    109: "office-workflow motivation with Odoo implementation mentioned only at the end",
}
RENDERED_PAGE_EXAMPLES = {22, 33, 39, 57, 58, 69, 73, 75, 86, 99, 103, 107, 108}
SOURCE_TYPO_CONFIRMED_ON_RENDERED_PAGE = {39, 73, 75, 99, 107}


def rows(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def sample() -> tuple[list[dict], dict[str, int]]:
    docs = {r["document_id"]: r for r in rows(RELEASE / "documents.jsonl")}
    populations = {"core": [], "hpu": []}
    for split in ("train", "dev", "test"):
        for row in rows(RELEASE / f"chunk_streams/ready/t192/{split}.jsonl"):
            origin = "core" if row["candidate_origin"] == "core_v55" else "hpu"
            source_path = Path(docs[row["document_id"]]["source_path"])
            if source_path.is_absolute():
                try:
                    source_path = source_path.relative_to(ROOT)
                except ValueError:
                    pass
            populations[origin].append({
                "chunk_id": row["chunk_id"],
                "document_id": row["document_id"],
                "origin": origin,
                "split": split,
                "text": row["text"],
                "approx_tokens": row["approx_tokens"],
                "source_path": source_path.as_posix(),
                "required_pages": sorted({span["page_index"] for span in row["source_spans"]}),
            })
    rng = random.Random(SEED)
    selected = []
    for origin in ("core", "hpu"):
        selected.extend(rng.sample(populations[origin], SAMPLE_PER_ORIGIN))
    return selected, {origin: len(pool) for origin, pool in populations.items()}


def wilson(successes: int, total: int, z: float = 1.95996398454) -> list[float]:
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return [centre - half, centre + half]


def main() -> None:
    selected, population = sample()
    OUT.mkdir(exist_ok=True)
    existing = OUT / "sample_120.jsonl"
    if existing.exists():
        before = list(rows(existing))
        if [r["chunk_id"] for r in before] != [r["chunk_id"] for r in selected]:
            raise ValueError("Existing sample differs from deterministic v73 sample")
    with existing.open("w", encoding="utf-8") as handle:
        for row in selected:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    if set(REJECT) & set(UNCERTAIN) or max(REJECT.keys() | UNCERTAIN.keys()) >= len(selected):
        raise ValueError("Invalid adjudication index")

    ledger = []
    counts = Counter()
    for index, row in enumerate(selected):
        decision = "reject" if index in REJECT else "uncertain" if index in UNCERTAIN else "provisional_pass"
        reason = REJECT.get(index) or UNCERTAIN.get(index) or "continuous computing prose in text screen"
        if index in SOURCE_TYPO_CONFIRMED_ON_RENDERED_PAGE:
            reason += "; source PDF itself contains the unusual spelling/notation"
        ledger.append({
            "sample_index": index,
            "chunk_id": row["chunk_id"],
            "document_id": row["document_id"],
            "origin": row["origin"],
            "split": row["split"],
            "decision": decision,
            "reason": reason,
            "review_basis": "text_plus_one_rendered_pdf_page" if index in RENDERED_PAGE_EXAMPLES else "text_only",
            "full_pdf_attestation": False,
        })
        counts[(row["origin"], decision)] += 1
    with (OUT / "text_triage_decisions.jsonl").open("w", encoding="utf-8") as handle:
        for row in ledger:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    n = len(selected)
    definite = sum(r["decision"] == "provisional_pass" for r in ledger)
    possible = definite + sum(r["decision"] == "uncertain" for r in ledger)
    population_total = sum(population.values())
    weighted_floor = sum(
        population[origin] / population_total * counts[(origin, "provisional_pass")] / SAMPLE_PER_ORIGIN
        for origin in ("core", "hpu")
    )
    weighted_ceiling = sum(
        population[origin] / population_total
        * (counts[(origin, "provisional_pass")] + counts[(origin, "uncertain")]) / SAMPLE_PER_ORIGIN
        for origin in ("core", "hpu")
    )
    summary = {
        "release_id": "human_v3_2_ready_v73_20261007",
        "sample_seed": SEED,
        "design": "independent uniform-without-replacement sample of 60 chunks from each source group",
        "population_by_origin": population,
        "sample_size": n,
        "unique_sample_documents": len({r["document_id"] for r in selected}),
        "pdf_page_examples_rendered": len(RENDERED_PAGE_EXAMPLES),
        "full_pdf_attestations": 0,
        "decisions": {"provisional_pass": definite, "uncertain": possible - definite, "reject": n - possible},
        "decisions_by_origin": {origin: {decision: counts[(origin, decision)] for decision in
                                  ("provisional_pass", "uncertain", "reject")}
                                for origin in ("core", "hpu")},
        "text_screen_pass_rate_floor": definite / n,
        "text_screen_pass_rate_if_uncertain_pass": possible / n,
        "population_weighted_text_screen_pass_rate_floor": weighted_floor,
        "population_weighted_text_screen_pass_rate_if_uncertain_pass": weighted_ceiling,
        "wilson_95_assuming_independent_text_labels_floor": wilson(definite, n),
        "wilson_95_assuming_independent_text_labels_ceiling": wilson(possible, n),
        "interpretation": "Exploratory text-screen estimate only. PDF/source fidelity and clustered/document errors are not fully measured; Wilson bounds exclude reviewer and source-cluster uncertainty.",
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
