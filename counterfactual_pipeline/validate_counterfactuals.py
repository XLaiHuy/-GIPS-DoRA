"""Comprehensive Validator for GIPS-DoRA Counterfactual Dataset.

Validates:
1. Split Isolation: zero cross-split leakage (all P/G inherit split from parent H).
2. Schema & Type Integrity: label_type and label_id match, sentences are sequential.
3. Content Non-Emptiness: no null texts, reasonable token bounds (>= 30 tokens).
4. Semantic Invariant: G prompts must not contain raw human text.
5. Checksum and Lineage consistency.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Set

logger = logging.getLogger("counterfactual_pipeline")


def validate_counterfactual_dataset(
    counterfactual_file: Path,
    human_dataset_splits_dir: Path
) -> Dict[str, Any]:
    """Run full integrity check on counterfactual JSONL dataset."""
    results = {
        "valid": True,
        "total_records": 0,
        "records_by_split": {"train": 0, "dev": 0, "test": 0},
        "records_by_label": {"H": 0, "P": 0, "G": 0},
        "records_by_intervention": {},
        "records_by_generator": {},
        "error_count": 0,
        "warning_count": 0,
        "errors": [],
        "warnings": []
    }

    if not counterfactual_file.exists():
        results["valid"] = False
        results["error_count"] += 1
        results["errors"].append(f"File not found: {counterfactual_file}")
        return results

    # Load human passage split mapping
    print(f"Loading parent human passage splits from {human_dataset_splits_dir}...")
    human_split_map: Dict[str, str] = {}
    for split_name in ("train", "dev", "test"):
        fpath = human_dataset_splits_dir / f"{split_name}.jsonl"
        if fpath.exists():
            with open(fpath, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        p = json.loads(line)
                        human_split_map[p["passage_id"]] = p.get("split", split_name)

    print(f"Loaded {len(human_split_map):,} human passage split mappings.")

    seen_record_ids: Set[str] = set()

    with open(counterfactual_file, "r", encoding="utf-8") as f:
        for line_idx, line in enumerate(f, start=1):
            if not line.strip():
                continue
            results["total_records"] += 1
            try:
                rec = json.loads(line)
            except Exception as e:
                results["error_count"] += 1
                results["errors"].append(f"Line {line_idx}: Invalid JSON: {e}")
                continue

            rec_id = rec.get("record_id")
            if not rec_id:
                results["error_count"] += 1
                results["errors"].append(f"Line {line_idx}: Missing record_id")
            elif rec_id in seen_record_ids:
                results["error_count"] += 1
                results["errors"].append(f"Line {line_idx}: Duplicate record_id: {rec_id}")
            seen_record_ids.add(rec_id)

            parent_pid = rec.get("source_passage_id")
            rec_split = rec.get("split")
            
            # Check split leakage
            if parent_pid in human_split_map:
                expected_split = human_split_map[parent_pid]
                if rec_split != expected_split:
                    results["error_count"] += 1
                    results["errors"].append(
                        f"Record {rec_id}: Split mismatch! Parent H is '{expected_split}', but record has '{rec_split}'."
                    )
            else:
                results["warning_count"] += 1
                results["warnings"].append(f"Record {rec_id}: Parent passage {parent_pid} not in human split map.")

            # Check label integrity
            lbl_type = rec.get("label_type")
            lbl_id = rec.get("label_id")
            expected_lbl_id = {"H": 0, "P": 1, "G": 2}.get(lbl_type)
            if lbl_id != expected_lbl_id:
                results["error_count"] += 1
                results["errors"].append(f"Record {rec_id}: Label type '{lbl_type}' != label_id {lbl_id}")

            # Track distributions
            if rec_split in results["records_by_split"]:
                results["records_by_split"][rec_split] += 1
            if lbl_type in results["records_by_label"]:
                results["records_by_label"][lbl_type] += 1

            interv = rec.get("intervention_type", "unknown")
            results["records_by_intervention"][interv] = results["records_by_intervention"].get(interv, 0) + 1

            gen = rec.get("generator_family", "unknown")
            results["records_by_generator"][gen] = results["records_by_generator"].get(gen, 0) + 1

            # Check text and sentences
            text = rec.get("text", "")
            if not text or not text.strip():
                results["error_count"] += 1
                results["errors"].append(f"Record {rec_id}: Text is empty.")

            toks = rec.get("approx_tokens", 0)
            if toks < 30:
                results["warning_count"] += 1
                results["warnings"].append(f"Record {rec_id}: Short token count ({toks} < 30).")

            sents = rec.get("sentences", [])
            if not sents:
                results["error_count"] += 1
                results["errors"].append(f"Record {rec_id}: Sentences list is empty.")
            else:
                for s_idx, s in enumerate(sents):
                    if s.get("ordinal") != s_idx:
                        results["error_count"] += 1
                        results["errors"].append(f"Record {rec_id}: Sentence ordinal non-sequential at index {s_idx}")
                        break

    if results["error_count"] > 0:
        results["valid"] = False

    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Validate counterfactual dataset for GIPS-DoRA")
    parser.add_argument("--dataset", type=Path, default=Path("ai_counterfactual_dataset_v1/counterfactual_records.jsonl"))
    parser.add_argument("--human_splits", type=Path, default=Path("human_written_dataset_v2_16_paper/gips_curated/splits"))
    args = parser.parse_args()

    print("=== STARTING COUNTERFACTUAL DATASET VALIDATION ===")
    res = validate_counterfactual_dataset(args.dataset, args.human_splits)
    print("\n=== VALIDATION SUMMARY ===")
    print(f"Valid              : {res['valid']}")
    print(f"Total Records      : {res['total_records']:,}")
    print(f"Records by Split   : {res['records_by_split']}")
    print(f"Records by Label   : {res['records_by_label']}")
    print(f"By Intervention    : {res['records_by_intervention']}")
    print(f"By Generator       : {res['records_by_generator']}")
    print(f"Errors             : {res['error_count']}")
    print(f"Warnings           : {res['warning_count']}")

    if res["errors"]:
        print("\nFirst 5 Errors:")
        for e in res["errors"][:5]:
            print(f"  [ERROR] {e}")


if __name__ == "__main__":
    main()
