"""Hybrid Sequence Synthesizer for GIPS-DoRA CRF Sequence Labeling.

Constructs realistic mixed-provenance passages (H / P / G) from aligned sentences
originating from the same source passage/document.
Produces:
- Sentence-level label sequences: [0, 0, 1, 1, 0, 2, 2, 0]
- AI-generated ratio (r_G)
- AI-assisted ratio (r_AI_assisted)
- Exact transition boundary metadata
"""

import random
import hashlib
from typing import List, Dict, Any, Optional

LABEL_MAP = {"H": 0, "P": 1, "G": 2}
LABEL_INV = {0: "H", 1: "P", 2: "G"}
TERMINAL_PUNCTUATION = set(".!?;:")


class HybridSequenceSynthesizer:
    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)

    def synthesize_hybrid(
        self,
        h_sentences: List[Dict[str, Any]],
        p_sentences: List[Dict[str, Any]],
        g_sentences: List[Dict[str, Any]],
        source_document_id: str,
        split: str,
        topic_cluster: str,
        mode: str = "realistic_transition"
    ) -> Optional[Dict[str, Any]]:
        """Synthesize a hybrid passage with sentence-level ground truth labels.

        ``realistic_transition`` creates an aligned H-P-H sequence and rejects
        malformed boundaries. ``random_aux`` preserves the old random splice as
        a secondary stress test; it must not be used as the primary train view.
        """
        if not h_sentences or (not p_sentences and not g_sentences):
            return None

        # Build sentence pools
        h_pool = [dict(s, source_type="H", label_id=0) for s in h_sentences]
        p_pool = [dict(s, source_type="P", label_id=1) for s in p_sentences] if p_sentences else []
        g_pool = [dict(s, source_type="G", label_id=2) for s in g_sentences] if g_sentences else []

        spliced_sentences = []
        recipe = mode
        if mode == "realistic_transition":
            if len(h_pool) < 5 or len(p_pool) != len(h_pool):
                return None
            span = min(3, max(1, len(h_pool) // 4))
            start = max(2, (len(h_pool) - span) // 2)
            start = min(start, len(h_pool) - span - 2)
            end = start + span
            if start < 2 or len(h_pool) - end < 2:
                return None
            previous_h = str(h_pool[start - 1].get("text", "")).rstrip()
            previous_p = str(p_pool[end - 1].get("text", "")).rstrip()
            if not previous_h or previous_h[-1] not in TERMINAL_PUNCTUATION:
                return None
            if not previous_p or previous_p[-1] not in TERMINAL_PUNCTUATION:
                return None
            window_start = max(0, start - 4)
            window_end = min(len(h_pool), end + 4)
            spliced_sentences.extend(h_pool[window_start:start])
            spliced_sentences.extend(p_pool[start:end])
            spliced_sentences.extend(h_pool[end:window_end])
            recipe = "h_context_contiguous_p_light_h_context"
        elif mode == "random_aux":
            pools = [pool for pool in (h_pool, p_pool, g_pool) if pool]
            self.rng.shuffle(pools)
            for pool in pools:
                take_n = min(len(pool), self.rng.randint(1, 3))
                spliced_sentences.extend(pool[:take_n])
            recipe = "random_auxiliary_stress_test"
        else:
            raise ValueError(f"Unsupported hybrid synthesis mode: {mode}")

        if not spliced_sentences:
            return None

        # Re-index ordinals
        final_sentences = []
        sentence_labels = []
        total_tokens = 0
        tokens_g = 0
        tokens_ai_assisted = 0

        for idx, s in enumerate(spliced_sentences):
            toks = s.get("approx_tokens", len(s["text"].split()))
            lbl = s["label_id"]
            
            total_tokens += toks
            if lbl == 2:  # G
                tokens_g += toks
                tokens_ai_assisted += toks
            elif lbl == 1:  # P
                tokens_ai_assisted += toks

            final_sentences.append({
                "sentence_id": f"hyb_{split}_{idx:03d}_{s['sentence_id']}",
                "ordinal": idx,
                "text": s["text"],
                "approx_tokens": toks,
                "label_id": lbl,
                "source_type": s["source_type"],
                "parent_record_id": s.get("parent_record_id", s.get("sentence_id"))
            })
            sentence_labels.append(lbl)

        full_text = " ".join(s["text"] for s in final_sentences)
        
        # Readable label string format e.g. "H H | P P | G G"
        label_seq_parts = []
        curr_lbl = None
        for lbl in sentence_labels:
            tag = LABEL_INV[lbl]
            if curr_lbl is not None and lbl != curr_lbl:
                label_seq_parts.append("|")
            label_seq_parts.append(tag)
            curr_lbl = lbl
        label_seq_str = " ".join(label_seq_parts)

        r_G = round(tokens_g / max(1, total_tokens), 4)
        r_ai = round(tokens_ai_assisted / max(1, total_tokens), 4)

        seq_hash = hashlib.sha256(full_text.encode("utf-8")).hexdigest()[:12]
        sequence_id = f"seq_{split}_{source_document_id[:8]}_{seq_hash}"

        return {
            "sequence_id": sequence_id,
            "source_document_id": source_document_id,
            "split": split,
            "topic_cluster": topic_cluster,
            "total_sentences": len(final_sentences),
            "total_tokens": total_tokens,
            "sentence_labels": sentence_labels,
            "label_sequence_str": label_seq_str,
            "r_G": r_G,
            "r_AI_assisted": r_ai,
            "text": full_text,
            "sentences": final_sentences,
            "hybrid_recipe": recipe,
            "hybrid_quality": {
                "primary_training_view": mode == "realistic_transition",
                "terminal_punctuation_at_transitions": mode == "realistic_transition",
                "minimum_h_context_each_side": 2 if mode == "realistic_transition" else None,
                "maximum_h_context_each_side": 4 if mode == "realistic_transition" else None,
            },
        }
