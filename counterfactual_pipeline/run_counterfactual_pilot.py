"""CLI Orchestrator for GIPS-DoRA Counterfactual Pilot & Production Runs."""

import sys
import io
import os
import json
import logging
import argparse
from pathlib import Path
from typing import List, Dict, Any

# Ensure UTF-8 output on Windows terminal
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Add workspace root to sys.path
workspace_root = Path(__file__).resolve().parent.parent
if str(workspace_root) not in sys.path:
    sys.path.insert(0, str(workspace_root))

from counterfactual_pipeline.skeleton_extractor import SemanticSkeletonExtractor
from counterfactual_pipeline.generator import CounterfactualGenerator
from counterfactual_pipeline.hybrid_synthesizer import HybridSequenceSynthesizer
from counterfactual_pipeline.validate_counterfactuals import validate_counterfactual_dataset
from counterfactual_pipeline.providers import (
    MockLLMProvider,
    GeminiProvider,
    OpenAIProvider,
    OpenRouterProvider
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("counterfactual_pipeline")


def load_balanced_samples(
    human_splits_dir: Path,
    n_train: int = 10,
    n_dev: int = 5,
    n_test: int = 5
) -> List[Dict[str, Any]]:
    """Sample balanced passages across splits and topic clusters."""
    samples = []
    split_targets = {"train": n_train, "dev": n_dev, "test": n_test}

    for split_name, target_count in split_targets.items():
        if target_count <= 0:
            continue
        split_file = human_splits_dir / f"{split_name}.jsonl"
        if not split_file.exists():
            continue
        
        count = 0
        with open(split_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                row = json.loads(line)
                samples.append(row)
                count += 1
                if count >= target_count:
                    break

    return samples


def main():
    parser = argparse.ArgumentParser(description="Run GIPS-DoRA Counterfactual Generation Pilot")
    parser.add_argument("--human_dataset", type=Path, default=Path("human_written_dataset_v2_16_paper/gips_curated"))
    parser.add_argument("--doc_metadata", type=Path, default=Path("human_written_dataset_v2_16_paper/documents.jsonl"))
    parser.add_argument("--output_dir", type=Path, default=Path("ai_counterfactual_dataset_v1"))
    parser.add_argument("--provider", type=str, default="mock", choices=["mock", "gemini", "openai", "openrouter"])
    parser.add_argument("--model_name", type=str, default=None)
    parser.add_argument("--api_key", type=str, default=None)
    parser.add_argument("--n_train", type=int, default=10)
    parser.add_argument("--n_dev", type=int, default=5)
    parser.add_argument("--n_test", type=int, default=5)
    parser.add_argument("--full_data", action="store_true", help="Process all passages in dataset splits")
    parser.add_argument("--delay_seconds", type=float, default=5.0, help="Delay between API calls in seconds (default 5.0s)")
    parser.add_argument("--interventions", nargs="+", default=["p_light", "p_medium", "p_heavy", "g_independent"])
    parser.add_argument("--synthesize_hybrids", action="store_true", default=True)
    parser.add_argument("--temperature", type=float, default=0.7)

    args = parser.parse_args()

    rpm_limit = max(1, int(60.0 / max(0.1, args.delay_seconds)))

    print("=================================================================")
    print("  GIPS-DoRA COUNTERFACTUAL GENERATION (P & G) PIPELINE")
    print("=================================================================")
    print(f"Provider        : {args.provider}")
    print(f"Model Name      : {args.model_name or ('gemini-3.8-flash' if args.provider == 'gemini' else 'default')}")
    print(f"Output Directory: {args.output_dir}")
    print(f"Delay / RPM     : {args.delay_seconds:.1f}s delay (~{rpm_limit} RPM limit)")
    print(f"Interventions   : {args.interventions}")

    # 1. Initialize Provider
    if args.provider == "gemini":
        model = args.model_name or "gemini-3.8-flash"
        provider = GeminiProvider(api_key=args.api_key, model_name=model, rpm_limit=rpm_limit)
    elif args.provider == "openai":
        model = args.model_name or "gpt-4o-mini"
        provider = OpenAIProvider(api_key=args.api_key, model_name=model, rpm_limit=rpm_limit)
    elif args.provider == "openrouter":
        model = args.model_name or "qwen/qwen-2.5-72b-instruct"
        provider = OpenRouterProvider(api_key=args.api_key, model_name=model, rpm_limit=rpm_limit)
    else:
        provider = MockLLMProvider()

    # 2. Initialize Extractor & Generator
    extractor = SemanticSkeletonExtractor(doc_metadata_path=args.doc_metadata)
    generator = CounterfactualGenerator(
        provider=provider,
        skeleton_extractor=extractor,
        output_dir=args.output_dir
    )

    # 3. Load Human Passages
    splits_dir = args.human_dataset / "splits"
    if args.full_data:
        print("Loading FULL dataset across all splits (train, dev, test)...")
        samples = []
        for sp in ("train", "dev", "test"):
            sp_file = splits_dir / f"{sp}.jsonl"
            if sp_file.exists():
                with open(sp_file, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            samples.append(json.loads(line))
        print(f"Loaded ALL {len(samples):,} human passages from {splits_dir}.")
    else:
        samples = load_balanced_samples(
            splits_dir,
            n_train=args.n_train,
            n_dev=args.n_dev,
            n_test=args.n_test
        )
        print(f"Loaded sample of {len(samples)} human passages across splits.")

    # 4. Run Generation
    print("\n--- Generating Counterfactual Records ---")
    stats = generator.process_passages_stream(
        passages=samples,
        interventions=args.interventions
    )
    print(f"Generated/Processed {stats['generated_records']} counterfactual records.")

    # 5. Synthesize Hybrid Sequences if requested
    if args.synthesize_hybrids:
        print("\n--- Synthesizing Hybrid (H / P / G) Sequences for CRF ---")
        synthesizer = HybridSequenceSynthesizer(seed=42)
        hybrids_file = args.output_dir / "hybrid_sequences.jsonl"
        
        # Group generated records by source_passage_id
        records_by_pid = {}
        if generator.pairs_file.exists():
            with open(generator.pairs_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        r = json.loads(line)
                        records_by_pid.setdefault(r["source_passage_id"], []).append(r)

        hybrid_count = 0
        with open(hybrids_file, "w", encoding="utf-8") as h_out:
            for p in samples:
                pid = p["passage_id"]
                cf_list = records_by_pid.get(pid, [])
                
                # Extract P and G sentences
                p_sents = []
                g_sents = []
                for rec in cf_list:
                    if rec["label_type"] == "P":
                        p_sents.extend(rec["sentences"])
                    elif rec["label_type"] == "G":
                        g_sents.extend(rec["sentences"])

                # Get H sentences from passage text
                h_sents = [
                    {"sentence_id": f"h_{pid}_{i}", "text": s, "approx_tokens": len(s.split())}
                    for i, s in enumerate(p["text"].split(". ")) if s.strip()
                ]

                hybrid = synthesizer.synthesize_hybrid(
                    h_sentences=h_sents,
                    p_sentences=p_sents,
                    g_sentences=g_sents,
                    source_document_id=p.get("source_document_id", "doc_unknown"),
                    split=p.get("split", "train"),
                    topic_cluster=p.get("topic_cluster", "software_engineering")
                )
                if hybrid:
                    h_out.write(json.dumps(hybrid, ensure_ascii=False) + "\n")
                    hybrid_count += 1

        print(f"Synthesized {hybrid_count} hybrid sequences saved to {hybrids_file}.")

    # 6. Run Dataset Validation
    print("\n--- Running Validation Suite ---")
    val_results = validate_counterfactual_dataset(generator.pairs_file, splits_dir)
    print(f"Validation Valid       : {val_results['valid']}")
    print(f"Total Records Verified : {val_results['total_records']:,}")
    print(f"Distribution by Split  : {val_results['records_by_split']}")
    print(f"Distribution by Label  : {val_results['records_by_label']}")
    print(f"Distribution by Interv : {val_results['records_by_intervention']}")
    print(f"Errors                 : {val_results['error_count']}")
    print(f"Warnings               : {val_results['warning_count']}")

    print("\n=================================================================")
    print("  COUNTERFACTUAL PIPELINE COMPLETED SUCCESSFULLY")
    print("=================================================================")


if __name__ == "__main__":
    main()
