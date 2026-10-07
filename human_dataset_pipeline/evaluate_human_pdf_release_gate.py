"""Fail-closed statistical and provenance gate for bulk H-to-AI generation."""
from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from human_v3.core import rows

ROOT = Path(__file__).resolve().parents[1]
VALID = {"pass", "reject", "uncertain"}
ERROR_TYPES = {"scope", "layout", "ocr", "other"}
UNRESOLVED_RELEASE_REASONS = {
    "language_requires_review", "source_evidence_requires_review", "year_conflict",
    "institution_is_repository_placeholder", "legacy_exclusion:incomplete_body_extraction",
}


def csv_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def cluster_interval(cards: list[dict], iterations: int = 10000) -> tuple[float, float, float]:
    """Weighted rate and percentile CI with whole documents as bootstrap units."""
    by_family = defaultdict(lambda: defaultdict(list))
    for card in cards:
        by_family[card["source_family"]][card["document_id"]].append(card)
    def rate(chosen):
        numerator = sum(float(row["sample_weight"]) * (row["decision"] == "pass") for row in chosen)
        denominator = sum(float(row["sample_weight"]) for row in chosen)
        return numerator / denominator
    estimate = rate(cards)
    rng = random.Random(20261008)
    values = []
    for _ in range(iterations):
        chosen = []
        for documents in by_family.values():
            document_ids = list(documents)
            for did in rng.choices(document_ids, k=len(document_ids)):
                chosen.extend(documents[did])
        values.append(rate(chosen))
    values.sort()
    return estimate, values[int(iterations * .025)], values[int(iterations * .975)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--review-kind", choices=("development", "final"), default="final")
    parser.add_argument("--review-dir", type=Path,
                        help="Explicit sample directory; defaults to <kind>_pdf_review_400 in candidate")
    args = parser.parse_args()
    candidate = args.candidate.resolve()
    manifest = json.loads((candidate / "manifest.json").read_text(encoding="utf-8"))
    validation_path = candidate / "audit/validation.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8")) if validation_path.is_file() else None
    review = args.review_dir.resolve() if args.review_dir else candidate / f"{args.review_kind}_pdf_review_400"
    primary = csv_rows(review / "primary_cards.csv")
    crosscheck = csv_rows(review / "crosscheck_cards.csv")
    sample = list(rows(review / "sample.jsonl")) if (review / "sample.jsonl").is_file() else []
    design = (json.loads((review / "sample_design.json").read_text(encoding="utf-8"))
              if (review / "sample_design.json").is_file() else {})
    adjudications = {row["chunk_id"]: row for row in csv_rows(review / "adjudications.csv")}
    blockers = []
    if not validation or validation["error_count"]:
        blockers.append("full_corpus_integrity_audit_missing_or_failed")
    layout_path = candidate / "audit/layout_residual_summary.json"
    layout = json.loads(layout_path.read_text(encoding="utf-8")) if layout_path.is_file() else None
    if not layout or layout.get("candidate") != candidate.name or layout.get("any_hit_chunks"):
        blockers.append("full_corpus_layout_audit_missing_or_failed")
    if manifest["pass_candidate_chunk_count"] < 20000 or manifest["pass_candidate_chunk_count"] > 30000:
        blockers.append("ready_count_outside_20000_30000")
    if manifest.get("scope_review_flag_chunks"):
        blockers.append("computing_scope_flags_unresolved")
    if manifest.get("body_boundary_review_flag_chunks"):
        blockers.append("body_boundary_flags_unresolved")
    if validation:
        reason_counts = validation.get("review_reason_counts", {})
        for reason in sorted(UNRESOLVED_RELEASE_REASONS):
            if reason_counts.get(reason):
                blockers.append("review_reason_unresolved:" + reason)
    documents = list(rows(candidate / "documents.jsonl"))
    document_index = {doc["document_id"]: doc for doc in documents}
    if any(row["document_id"] not in document_index or
           row.get("source_family") != (
               "hpu" if document_index[row["document_id"]].get("institution_id") == "hpu"
               or "hpu_source_pilot" in document_index[row["document_id"]].get("source_path", "")
               else "core") for row in sample):
        blockers.append("sample_source_family_stratification_mismatch")
    if any(doc.get("rights_status") not in {"internal_research_cleared", "redistribution_cleared"}
           or not doc.get("rights_evidence") for doc in documents):
        blockers.append("internal_research_rights_evidence_incomplete")
    if args.review_kind != "final":
        blockers.append("development_sample_cannot_open_release_gate")
    if len(primary) != 400 or len({row["chunk_id"] for row in primary}) != 400:
        blockers.append("primary_pdf_sample_missing_or_not_400_unique")
    if (len(sample) != 400 or len({row["chunk_id"] for row in sample}) != 400
            or design.get("population_chunks") != manifest["pass_candidate_chunk_count"]
            or design.get("kind") != args.review_kind):
        blockers.append("sample_design_missing_or_not_from_current_population")
    sample_index = {row["chunk_id"]: row for row in sample}
    if len(primary) == 400 and any(
            row["chunk_id"] not in sample_index
            or row.get("text") != sample_index[row["chunk_id"]]["text"]
            or row.get("source_path") != sample_index[row["chunk_id"]]["source_path"]
            or any(not (review / name).is_file() for name in row.get("images", "").split(";")
                   if name)
            for row in primary):
        blockers.append("review_cards_or_pdf_crops_do_not_match_sample")
    if sample:
        candidate_index = {}
        for split in ("train", "dev", "test"):
            for chunk in rows(candidate / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl"):
                if chunk["chunk_id"] in sample_index:
                    candidate_index[chunk["chunk_id"]] = chunk
        if any(row["chunk_id"] not in candidate_index
               or row["text"] != candidate_index[row["chunk_id"]]["text"]
               or row["document_id"] != candidate_index[row["chunk_id"]]["document_id"]
               for row in sample):
            blockers.append("sample_rows_do_not_match_current_candidate")
    if len(crosscheck) != 40 or len({row["chunk_id"] for row in crosscheck}) != 40:
        blockers.append("independent_crosscheck_missing_or_not_40_unique")
    primary_index = {row["chunk_id"]: row for row in primary}
    if any(row["decision"] not in VALID or not row["reviewer"].strip()
           or (row["decision"] != "pass" and
               (not row["reason"].strip() or row.get("error_type") not in ERROR_TYPES))
           for row in primary):
        blockers.append("primary_pdf_decisions_incomplete")
    disagreements = []
    for row in crosscheck:
        first = primary_index.get(row["chunk_id"])
        if (not first or row["decision"] not in VALID or not row["reviewer"].strip()
                or row["reviewer"] == first["reviewer"]
                or (row["decision"] != "pass" and not row["reason"].strip())):
            blockers.append("independent_crosscheck_decisions_incomplete")
            break
        if row["decision"] != first["decision"]:
            disagreements.append(row["chunk_id"])
    for cid in disagreements:
        adjudication = adjudications.get(cid)
        if (not adjudication or adjudication.get("final_decision") not in VALID
                or not adjudication.get("reason", "").strip()
                or not adjudication.get("adjudicator", "").strip()):
            blockers.append("crosscheck_disagreement_not_adjudicated")
            break
    rate = lower = upper = None
    reason_counts = Counter()
    if len(primary) == 400 and all(row["decision"] in VALID for row in primary):
        effective = [dict(row) for row in primary]
        for row in effective:
            if row["chunk_id"] in disagreements and row["chunk_id"] in adjudications:
                row["decision"] = adjudications[row["chunk_id"]]["final_decision"]
            if row["decision"] != "pass":
                reason_counts[row.get("error_type") or "untyped"] += 1
        try:
            rate, lower, upper = cluster_interval(effective)
        except (ZeroDivisionError, ValueError):
            blockers.append("cluster_interval_could_not_be_computed")
        if lower is not None and lower < .95:
            blockers.append("cluster_robust_lower_bound_below_95_percent")
        if any(row["decision"] == "uncertain" for row in effective):
            blockers.append("uncertain_pdf_decisions_unresolved")
        if reason_counts.get("layout") or reason_counts.get("ocr"):
            blockers.append("known_extraction_error_detected_in_pdf_sample")
    status = "PASS" if not blockers else "HOLD"
    result = {"candidate": candidate.name, "status": status,
        "ready_for_bulk_ai_generation": status == "PASS",
        "candidate_chunks": manifest["pass_candidate_chunk_count"],
        "primary_visual_decisions": sum(row.get("decision") in VALID for row in primary),
        "crosscheck_decisions": sum(row.get("decision") in VALID for row in crosscheck),
        "crosscheck_disagreements": disagreements,
        "weighted_pass_rate": rate, "cluster_bootstrap_95_lower": lower,
        "cluster_bootstrap_95_upper": upper,
        "nonpass_error_types": dict(reason_counts), "blockers": sorted(set(blockers))}
    path = candidate / "audit/release_gate_status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
