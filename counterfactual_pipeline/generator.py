"""Counterfactual Generator Engine for GIPS-DoRA.

Orchestrates the generation of:
1. P (AI Polish) across 3 intervention intensities:
   - P-light (Orthographic & phrasing polish, strict sentence preservation)
   - P-medium (Sentence-level academic rewrite)
   - P-heavy (Structural paragraph overhaul)
2. G (Independent AI Generation from Semantic Skeleton)

Guarantees:
- Strict split isolation: child P and G inherit the exact split (train/dev/test) of parent H.
- Checkpointing: resumes automatically from existing records without duplicate generation.
- Full provenance metadata: records source_passage_id, generator_family, model_name, prompt_hash.
"""

import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Any, Optional, Set, Tuple

from counterfactual_pipeline.skeleton_extractor import SemanticSkeletonExtractor
from counterfactual_pipeline.prompt_templates import (
    build_polish_prompt,
    build_generation_prompt
)
from counterfactual_pipeline.providers.base import BaseLLMProvider
from counterfactual_pipeline.sentence_aligner import align_and_package_sentences

logger = logging.getLogger("counterfactual_pipeline")


class CounterfactualGenerator:
    def __init__(
        self,
        provider: BaseLLMProvider,
        skeleton_extractor: SemanticSkeletonExtractor,
        output_dir: Path
    ):
        self.provider = provider
        self.extractor = skeleton_extractor
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.pairs_file = self.output_dir / "counterfactual_records.jsonl"
        self.completed_keys: Set[str] = set()
        self._load_completed_keys()

    def _make_key(self, source_passage_id: str, intervention: str, generator_family: str) -> str:
        return f"{source_passage_id}::{intervention}::{generator_family}"

    def _load_completed_keys(self):
        if not self.pairs_file.exists():
            return
        with open(self.pairs_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    pid = rec.get("source_passage_id")
                    interv = rec.get("intervention_type")
                    fam = rec.get("generator_family")
                    if pid and interv and fam:
                        self.completed_keys.add(self._make_key(pid, interv, fam))
                except Exception:
                    continue
        logger.info(f"Loaded {len(self.completed_keys)} previously completed counterfactual records.")

    def generate_for_passage(
        self,
        passage: Dict[str, Any],
        interventions: Optional[List[str]] = None,
        temperature: float = 0.7
    ) -> List[Dict[str, Any]]:
        """Generate specified counterfactuals for a single human passage.

        interventions can contain: 'p_light', 'p_medium', 'p_heavy', 'g_independent'
        """
        if interventions is None:
            interventions = ["p_light", "p_medium", "p_heavy", "g_independent"]

        pid = passage["passage_id"]
        doc_id = passage.get("source_document_id", passage.get("document_id"))
        split = passage.get("split", "train")
        topic = passage.get("topic_cluster", "software_engineering")
        raw_text = passage["text"]

        thesis_title = passage.get("thesis_title") or self.extractor.doc_titles.get(doc_id, "")
        results = []

        for interv in interventions:
            key = self._make_key(pid, interv, self.provider.family)
            if key in self.completed_keys:
                continue

            record_id = f"cf_{pid}_{self.provider.family}_{interv}"

            if interv in ("p_light", "p_medium", "p_heavy"):
                level = interv.replace("p_", "")
                sys_prompt, user_prompt, p_hash = build_polish_prompt(
                    text=raw_text,
                    level=level,
                    topic_cluster=topic,
                    thesis_title=thesis_title
                )
                label_type = "P"
                label_id = 1
            elif interv == "g_independent":
                skeleton = self.extractor.extract_skeleton(passage)
                sys_prompt, user_prompt, p_hash = build_generation_prompt(skeleton)
                label_type = "G"
                label_id = 2
            else:
                continue

            try:
                gen_text = self.provider.generate(
                    system_prompt=sys_prompt,
                    user_prompt=user_prompt,
                    temperature=temperature,
                    max_tokens=1024
                )
            except Exception as e:
                logger.error(f"Generation failed for {key}: {e}")
                continue

            # Align sentences and package
            pkg = align_and_package_sentences(
                raw_text=gen_text,
                record_id=record_id,
                label_id=label_id
            )

            record = {
                "record_id": record_id,
                "source_passage_id": pid,
                "source_document_id": doc_id,
                "split": split,
                "topic_cluster": topic,
                "label_type": label_type,
                "label_id": label_id,
                "intervention_type": interv,
                "generator_family": self.provider.family,
                "model_name": self.provider.model_name,
                "generation_parameters": {
                    "temperature": temperature,
                    "max_tokens": 1024
                },
                "prompt_hash": p_hash,
                "approx_tokens": pkg["approx_tokens"],
                "sentence_count": pkg["sentence_count"],
                "text": pkg["text"],
                "sentences": pkg["sentences"],
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            }

            # Append to file immediately for checkpoint safety
            with open(self.pairs_file, "a", encoding="utf-8") as out_f:
                out_f.write(json.dumps(record, ensure_ascii=False) + "\n")

            self.completed_keys.add(key)
            results.append(record)

        return results

    def process_passages_stream(
        self,
        passages: List[Dict[str, Any]],
        interventions: Optional[List[str]] = None,
        max_samples: Optional[int] = None
    ) -> Dict[str, int]:
        stats = {
            "total_processed": 0,
            "generated_records": 0,
            "skipped_existing": 0
        }

        for idx, passage in enumerate(passages):
            if max_samples and stats["total_processed"] >= max_samples:
                break

            stats["total_processed"] += 1
            new_records = self.generate_for_passage(passage, interventions=interventions)
            stats["generated_records"] += len(new_records)
            if idx % 10 == 0:
                logger.info(f"Progress: {idx+1}/{len(passages)} passages processed. Total records: {stats['generated_records']}")

        return stats
