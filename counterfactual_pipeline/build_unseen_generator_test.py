"""Build a held-out test file whose generator/model identity is absent from train."""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any, Iterable


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}") from exc


def identity(row: dict[str, Any]) -> str:
    return f"{row.get('generator_family')}::{row.get('model_name')}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-records", type=Path, required=True)
    parser.add_argument("--candidate-records", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()

    training = [
        row for row in read_jsonl(args.training_records)
        if row.get("split") == "train" and row.get("label_type") in {"P", "G"}
    ]
    candidates = [
        row for row in read_jsonl(args.candidate_records)
        if row.get("label_type") in {"P", "G"}
    ]
    training_identities = {identity(row) for row in training}
    accepted = [row for row in candidates if identity(row) not in training_identities]
    rejected = [row for row in candidates if identity(row) in training_identities]

    accepted_source_ids = {row.get("source_passage_id") for row in accepted}
    source_splits: dict[str, set[str]] = collections.defaultdict(set)
    for row in accepted:
        source_splits[str(row.get("source_passage_id"))].add(str(row.get("split")))
    cross_split_sources = {key: sorted(value) for key, value in source_splits.items() if len(value) > 1}

    problems = []
    if not accepted:
        problems.append("No candidate uses a generator/model identity absent from train.")
    if cross_split_sources:
        problems.append("Accepted candidate sources occur in multiple splits.")
    if any(row.get("split") != "test" for row in accepted):
        problems.append("Every accepted held-out record must have split='test'.")

    manifest = {
        "schema_version": "1.0",
        "training_generator_models": sorted(training_identities),
        "candidate_generator_models": sorted({identity(row) for row in candidates}),
        "accepted_generator_models": sorted({identity(row) for row in accepted}),
        "candidate_records": len(candidates),
        "accepted_records": len(accepted),
        "rejected_seen_generator_records": len(rejected),
        "accepted_source_passages": len(accepted_source_ids),
        "cross_split_accepted_sources": cross_split_sources,
        "valid": not problems,
        "problems": problems,
        "protocol": {
            "purpose": "Final robustness evaluation only",
            "allowed_uses": ["one-time final reporting", "generator-wise F1 reporting"],
            "prohibited_uses": [
                "threshold selection",
                "hyperparameter selection",
                "prompt revision",
                "training or early stopping",
            ],
        },
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if problems:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for row in accepted:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
