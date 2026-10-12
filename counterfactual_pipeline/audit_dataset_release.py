"""Reproducible audit for a labeled H/P/G dataset release."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable


WORD_RE = re.compile(r"[\w]+", re.UNICODE)
TERMINAL_PUNCTUATION = set(".!?;:")


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}") from exc


def normalize(text: str) -> str:
    return " ".join(WORD_RE.findall(text.casefold()))


def shingles(text: str, size: int = 4) -> set[str]:
    words = WORD_RE.findall(text.casefold())
    if len(words) < size:
        return {" ".join(words)} if words else set()
    return {" ".join(words[index:index + size]) for index in range(len(words) - size + 1)}


def minhash_signature(values: set[str], size: int = 8) -> tuple[int, ...]:
    hashes = sorted(
        int.from_bytes(hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest(), "big")
        for value in values
    )
    return tuple(hashes[:size])


def count_scope(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "records": len(records),
        "sentences": sum(int(row.get("sentence_count", len(row.get("sentences", [])))) for row in records),
        "approx_tokens": sum(int(row.get("approx_tokens", 0)) for row in records),
        "by_label": dict(sorted(collections.Counter(row.get("label_type", "unknown") for row in records).items())),
        "by_split": dict(sorted(collections.Counter(row.get("split", "unknown") for row in records).items())),
    }


def source_integrity(records: list[dict[str, Any]]) -> dict[str, Any]:
    human = [row for row in records if row.get("label_type") == "H"]
    variants = [row for row in records if row.get("label_type") in {"P", "G"}]
    parents = {row.get("source_passage_id"): row for row in human}
    documents: dict[str, set[str]] = collections.defaultdict(set)
    sources: dict[str, set[str]] = collections.defaultdict(set)
    for row in records:
        documents[str(row.get("source_document_id"))].add(str(row.get("split")))
        sources[str(row.get("source_passage_id"))].add(str(row.get("split")))

    missing_parent = []
    split_mismatch = []
    document_mismatch = []
    for row in variants:
        parent = parents.get(row.get("source_passage_id"))
        if parent is None:
            missing_parent.append(row.get("record_id"))
            continue
        if row.get("split") != parent.get("split"):
            split_mismatch.append(row.get("record_id"))
        if row.get("source_document_id") != parent.get("source_document_id"):
            document_mismatch.append(row.get("record_id"))

    cross_documents = {key: sorted(value) for key, value in documents.items() if len(value) > 1}
    cross_sources = {key: sorted(value) for key, value in sources.items() if len(value) > 1}
    return {
        "human_parent_count": len(parents),
        "variant_count": len(variants),
        "missing_h_parent": len(missing_parent),
        "variant_split_mismatch": len(split_mismatch),
        "variant_document_mismatch": len(document_mismatch),
        "document_ids_in_multiple_splits": len(cross_documents),
        "source_passage_ids_in_multiple_splits": len(cross_sources),
        "examples": {
            "missing_h_parent": missing_parent[:10],
            "variant_split_mismatch": split_mismatch[:10],
            "variant_document_mismatch": document_mismatch[:10],
            "cross_split_documents": list(cross_documents.items())[:10],
            "cross_split_sources": list(cross_sources.items())[:10],
        },
    }


def duplicate_audit(records: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    exact_groups: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in records:
        exact_groups[normalize(row.get("text", ""))].append(row)
    exact_cross = []
    for normalized, group in exact_groups.items():
        if normalized and len({row.get("split") for row in group}) > 1:
            exact_cross.append([row.get("record_id") for row in group])

    prepared = []
    buckets: dict[tuple[int, int], list[int]] = collections.defaultdict(list)
    for index, row in enumerate(records):
        row_shingles = shingles(row.get("text", ""))
        signature = minhash_signature(row_shingles)
        prepared.append((row, row_shingles))
        for position, value in enumerate(signature):
            buckets[(position, value)].append(index)

    candidates: set[tuple[int, int]] = set()
    for bucket in buckets.values():
        for left_position, left in enumerate(bucket):
            for right in bucket[left_position + 1:]:
                pair = (left, right) if left < right else (right, left)
                candidates.add(pair)

    near_cross = []
    for left, right in candidates:
        left_row, left_shingles = prepared[left]
        right_row, right_shingles = prepared[right]
        if left_row.get("split") == right_row.get("split"):
            continue
        union = left_shingles | right_shingles
        score = len(left_shingles & right_shingles) / len(union) if union else 0.0
        if score >= threshold:
            near_cross.append({
                "left": left_row.get("record_id"),
                "left_split": left_row.get("split"),
                "right": right_row.get("record_id"),
                "right_split": right_row.get("split"),
                "jaccard_4gram": round(score, 4),
            })
    near_cross.sort(key=lambda item: item["jaccard_4gram"], reverse=True)
    return {
        "normalization": "casefolded word tokens",
        "near_duplicate_method": "4-word shingle Jaccard with MinHash candidate retrieval",
        "near_duplicate_threshold": threshold,
        "candidate_pairs_checked": len(candidates),
        "exact_cross_split_groups": len(exact_cross),
        "near_duplicate_cross_split_pairs": len(near_cross),
        "exact_examples": exact_cross[:10],
        "near_duplicate_examples": near_cross[:20],
    }


def generator_audit(records: list[dict[str, Any]]) -> dict[str, Any]:
    identities: dict[str, set[str]] = collections.defaultdict(set)
    prompt_strategies: dict[str, set[str]] = collections.defaultdict(set)
    for row in records:
        if row.get("label_type") not in {"P", "G"}:
            continue
        split = str(row.get("split"))
        identities[split].add(f"{row.get('generator_family')}::{row.get('model_name')}")
        prompt_strategies[split].add(str(row.get("prompt_strategy", row.get("intervention_type", "unknown"))))
    train = identities.get("train", set())
    test = identities.get("test", set())
    return {
        "generator_model_identities_by_split": {key: sorted(value) for key, value in sorted(identities.items())},
        "prompt_strategies_by_split": {key: sorted(value) for key, value in sorted(prompt_strategies.items())},
        "test_generator_models_seen_in_train": sorted(test & train),
        "test_generator_models_unseen_in_train": sorted(test - train),
        "unseen_generator_test_ready": bool(test - train),
    }


def hybrid_audit(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {"available": False}
    rows = list(read_jsonl(path))
    invalid_boundaries = []
    invalid_recipes = []
    for row in rows:
        labels = row.get("sentence_labels", [])
        sentences = row.get("sentences", [])
        transitions = [index for index in range(1, len(labels)) if labels[index] != labels[index - 1]]
        if labels[:1] != [0] or labels[-1:] != [0] or len(transitions) != 2 or 2 in labels:
            invalid_recipes.append(row.get("sequence_id"))
        for index in transitions:
            previous = str(sentences[index - 1].get("text", "")).rstrip()
            if not previous or previous[-1] not in TERMINAL_PUNCTUATION:
                invalid_boundaries.append({"sequence_id": row.get("sequence_id"), "before_ordinal": index - 1})
    lengths = [len(row.get("sentence_labels", [])) for row in rows]
    return {
        "available": True,
        "records": len(rows),
        "expected_recipe": "H context -> contiguous aligned P-light span -> H context",
        "length_min": min(lengths) if lengths else 0,
        "length_max": max(lengths) if lengths else 0,
        "invalid_recipe_records": len(set(invalid_recipes)),
        "invalid_transition_boundaries": len(invalid_boundaries),
        "invalid_recipe_examples": invalid_recipes[:10],
        "invalid_boundary_examples": invalid_boundaries[:10],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labeled-records", type=Path, required=True)
    parser.add_argument("--hybrids", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--near-threshold", type=float, default=0.80)
    args = parser.parse_args()

    records = list(read_jsonl(args.labeled_records))
    by_label = {
        label: count_scope([row for row in records if row.get("label_type") == label])
        for label in ("H", "P", "G")
    }
    source = source_integrity(records)
    duplicates = duplicate_audit(records, args.near_threshold)
    generators = generator_audit(records)
    hybrids = hybrid_audit(args.hybrids)
    hard_failures = {
        "document_split_leakage": source["document_ids_in_multiple_splits"],
        "source_split_leakage": source["source_passage_ids_in_multiple_splits"],
        "missing_h_parent": source["missing_h_parent"],
        "variant_split_mismatch": source["variant_split_mismatch"],
        "variant_document_mismatch": source["variant_document_mismatch"],
        "exact_cross_split_duplicates": duplicates["exact_cross_split_groups"],
        "near_cross_split_duplicates": duplicates["near_duplicate_cross_split_pairs"],
        "invalid_hybrid_recipes": hybrids.get("invalid_recipe_records", 0),
        "invalid_hybrid_boundaries": hybrids.get("invalid_transition_boundaries", 0),
    }
    report = {
        "schema_version": "1.0",
        "counting_units": {
            "records": "one JSONL row; H is a source passage, P/G is one generated variant",
            "sentences": "sum of sentence_count fields",
            "approx_tokens": "sum of stored approximate token counts; not a model tokenizer count",
        },
        "all_hpg": count_scope(records),
        "by_label": by_label,
        "source_split_integrity": source,
        "duplicates": duplicates,
        "generator_generalization": generators,
        "hybrids": hybrids,
        "hard_failures": hard_failures,
        "valid": not any(hard_failures.values()),
        "limitations": [
            "MinHash retrieves candidates before exact 4-gram Jaccard scoring; it is not a semantic duplicate model.",
            "This audit cannot prove that test data was never consulted during earlier experiments; that requires experiment logs and governance.",
            "An unseen-generator test is only ready when test contains a generator/model identity absent from train.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "valid": report["valid"],
        "all_hpg": report["all_hpg"],
        "hard_failures": hard_failures,
        "unseen_generator_test_ready": generators["unseen_generator_test_ready"],
    }, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
