"""Build a non-destructive, reviewable cleaned dataset from the NCKH repo copy."""
from __future__ import annotations

import argparse
import collections
import difflib
import hashlib
import json
import random
import re
import shutil
from pathlib import Path


WORD_RE = re.compile(r"[\w]+", re.UNICODE)
NUMBER_RE = re.compile(r"(?<!\w)\d+(?:[.,]\d+)*(?!\w)")
ENTITY_RE = re.compile(r"\b(?:[A-Z][A-Za-z0-9.+#/-]{1,}|[A-Z]{2,}[0-9A-Z.+#/-]*)\b")
LABEL_ID = {"H": 0, "P": 1, "G": 2}
TERMINAL_PUNCTUATION = set(".!?;:")


def read_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Malformed JSON at {path}:{line_no}") from exc


def write_jsonl(path: Path, rows):
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def normalize(text: str) -> str:
    return " ".join(WORD_RE.findall(text.lower()))


def sets(text: str):
    return set(WORD_RE.findall(text.lower()))


def counts(rows):
    return {
        "records": len(rows),
        "sentences": sum(int(row.get("sentence_count", 0)) for row in rows),
        "approx_tokens": sum(int(row.get("approx_tokens", 0)) for row in rows),
    }


def build_hybrid(parent, human_sentences, polish, source_id):
    original = [s.get("text", "").strip() for s in human_sentences if s.get("text", "").strip()]
    rewritten = [s.get("text", "").strip() for s in polish.get("sentences", []) if s.get("text", "").strip()]
    if len(original) < 5 or len(original) != len(rewritten):
        return None
    n = len(original)
    span = min(3, max(1, n // 4))
    start = max(2, (n - span) // 2)
    start = min(start, n - span - 2)
    end = start + span
    if start < 2 or n - end < 2:
        return None
    for index in range(start, end):
        left, right = sets(original[index]), sets(rewritten[index])
        similarity = len(left & right) / max(1, len(left | right))
        if similarity < 0.08:
            return None
    if original[start - 1][-1] not in TERMINAL_PUNCTUATION:
        return None
    if rewritten[end - 1][-1] not in TERMINAL_PUNCTUATION:
        return None

    window_start = max(0, start - 4)
    window_end = min(n, end + 4)
    pieces = [(original[i], "H", i) for i in range(window_start, start)]
    pieces.extend((rewritten[i], "P", i) for i in range(start, end))
    pieces.extend((original[i], "H", i) for i in range(end, window_end))
    labels, final_sentences, total_tokens, p_tokens = [], [], 0, 0
    for ordinal, (text, label, source_ordinal) in enumerate(pieces):
        token_count = len(WORD_RE.findall(text))
        label_id = LABEL_ID[label]
        labels.append(label_id)
        total_tokens += token_count
        if label == "P":
            p_tokens += token_count
        final_sentences.append({
            "sentence_id": f"hyb_{source_id}_{ordinal:03d}",
            "ordinal": ordinal,
            "text": text,
            "approx_tokens": token_count,
            "label_id": label_id,
            "source_type": label,
            "parent_record_id": polish["record_id"] if label == "P" else f"h_{source_id}",
            "source_sentence_ordinal": source_ordinal,
        })
    full_text = " ".join(item["text"] for item in final_sentences)
    label_names = ["H" if x == 0 else "P" if x == 1 else "G" for x in labels]
    sequence = []
    for index, label in enumerate(label_names):
        if index and label != label_names[index - 1]:
            sequence.append("|")
        sequence.append(label)
    return {
        "sequence_id": f"seq_{parent.get('split')}_{source_id}_{hashlib.sha256(full_text.encode()).hexdigest()[:12]}",
        "source_passage_id": parent["passage_id"],
        "source_document_id": parent["source_document_id"],
        "split": parent["split"],
        "topic_cluster": parent.get("topic_cluster", "unknown"),
        "total_sentences": len(final_sentences),
        "total_tokens": total_tokens,
        "sentence_labels": labels,
        "label_sequence_str": " ".join(sequence),
        "r_G": 0.0,
        "r_AI_assisted": round(p_tokens / max(1, total_tokens), 4),
        "text": full_text,
        "sentences": final_sentences,
        "hybrid_recipe": "H_context_with_contiguous_aligned_P_light_span",
        "hybrid_quality": {
            "aligned_sentence_ordinals": True,
            "terminal_punctuation_at_transitions": True,
            "minimum_h_context_each_side": 2,
            "maximum_h_context_each_side": 4,
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--audit-report", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    repo, out = args.repo.resolve(), args.output_dir.resolve()
    if out.exists():
        raise SystemExit(f"Refusing to overwrite existing output directory: {out}")

    data = repo / "human_written_dataset_v2_16_paper" / "gips_curated"
    raw_h = list(read_jsonl(data / "passages_gips_ready.jsonl"))
    raw_cf = list(read_jsonl(repo / "ai_counterfactual_dataset_v1" / "counterfactual_records.jsonl"))
    audit = json.loads(args.audit_report.read_text(encoding="utf-8"))
    h_by_id = {row["passage_id"]: row for row in raw_h}

    # Quarantine every H passage participating in an exact/near duplicate across splits.
    leakage_ids = set()
    leakage_pairs = []
    for pair in audit.get("near_duplicates", {}).get("examples", []):
        if pair.get("left_split") == pair.get("right_split"):
            continue
        ids = []
        for side in ("left", "right"):
            identifier = pair.get(side, "")
            if identifier.startswith("H:"):
                passage_id = identifier[2:]
                leakage_ids.add(passage_id)
                ids.append(passage_id)
        if ids:
            leakage_pairs.append({**pair, "human_passage_ids": ids})
    for group in audit.get("exact_duplicates", {}).get("examples", []):
        if len(group.get("splits", [])) > 1:
            ids = [value[2:] for value in group.get("ids", []) if value.startswith("H:")]
            leakage_ids.update(ids)
            if ids:
                leakage_pairs.append({"reason": "exact_duplicate_cross_split", "human_passage_ids": ids, "splits": group.get("splits")})

    # Collapse same-split exact duplicate H passages deterministically.
    seen_h_text, same_split_duplicate_ids = {}, set()
    for row in sorted(raw_h, key=lambda item: item["passage_id"]):
        key = normalize(row.get("text", ""))
        if not key:
            continue
        if key in seen_h_text and seen_h_text[key].get("split") == row.get("split"):
            same_split_duplicate_ids.add(row["passage_id"])
        else:
            seen_h_text[key] = row

    removed_cf, fixed_doc, identical_p = [], [], []
    clean_cf = []
    for row in raw_cf:
        parent = h_by_id.get(row.get("source_passage_id"))
        if parent is None:
            removed_cf.append({"record": row, "reason": "parent_missing_from_h_v2_16"})
            continue
        if row.get("label_type") == "P" and normalize(row.get("text", "")) == normalize(parent.get("text", "")):
            identical_p.append({"record_id": row.get("record_id"), "source_passage_id": row["source_passage_id"], "intervention_type": row.get("intervention_type"), "reason": "P_text_identical_to_H"})
            continue
        if row["source_passage_id"] in leakage_ids:
            removed_cf.append({"record": row, "reason": "parent_quarantined_cross_split_duplicate_candidate"})
            continue
        if row["source_passage_id"] in same_split_duplicate_ids:
            removed_cf.append({"record": row, "reason": "duplicate_H_parent_removed"})
            continue
        if row.get("split") != parent.get("split"):
            removed_cf.append({"record": row, "reason": "cf_split_differs_from_H_parent"})
            continue
        expected_document = parent.get("source_document_id")
        if row.get("source_document_id") != expected_document:
            fixed_doc.append({"record_id": row.get("record_id"), "old": row.get("source_document_id"), "new": expected_document})
            row = dict(row)
            row["source_document_id"] = expected_document
        clean_cf.append(row)

    clean_h = [row for row in raw_h if row["passage_id"] not in leakage_ids and row["passage_id"] not in same_split_duplicate_ids]
    h_sentences = {row["sentence_id"]: row for row in read_jsonl(data / "sentences_gips_ready.jsonl")}
    labeled_h = []
    for passage in clean_h:
        sentence_ids = passage.get("merged_sentence_ids", [])
        sentence_rows = [h_sentences[sid] for sid in sentence_ids if sid in h_sentences]
        sentence_texts = []
        for ordinal, sentence in enumerate(sentence_rows):
            sentence_texts.append({
                "sentence_id": sentence["sentence_id"],
                "ordinal": ordinal,
                "text": sentence.get("text", ""),
                "approx_tokens": int(sentence.get("approx_tokens", len(WORD_RE.findall(sentence.get("text", ""))))),
                "label_id": 0,
            })
        labeled_h.append({
            "record_id": f"h_{passage['passage_id']}",
            "source_passage_id": passage["passage_id"],
            "source_document_id": passage["source_document_id"],
            "split": passage["split"],
            "topic_cluster": passage.get("topic_cluster", "unknown"),
            "label_type": "H",
            "label_id": 0,
            "intervention_type": "original",
            "generator_family": "human_source",
            "model_name": "human_v2_16_paper",
            "approx_tokens": int(passage.get("approx_tokens", 0)),
            "sentence_count": len(sentence_texts),
            "text": passage["text"],
            "sentences": sentence_texts,
            "source_file_id": passage.get("source_file_id"),
            "section_id": passage.get("section_id"),
            "merged_sentence_ids": sentence_ids,
        })

    all_labeled = labeled_h + clean_cf
    by_label = {label: [row for row in all_labeled if row.get("label_type") == label] for label in LABEL_ID}
    by_split_label = {
        split: {label: len([row for row in all_labeled if row.get("split") == split and row.get("label_type") == label]) for label in LABEL_ID}
        for split in ("train", "dev", "test")
    }
    by_sentence = {label: sum(int(row.get("sentence_count", 0)) for row in rows) for label, rows in by_label.items()}
    train_sentence_counts = {
        label: sum(int(row.get("sentence_count", 0)) for row in all_labeled if row.get("split") == "train" and row.get("label_type") == label)
        for label in LABEL_ID
    }
    n_train_sentences = sum(train_sentence_counts.values())
    class_weights = {label: round(n_train_sentences / max(1, len(LABEL_ID) * train_sentence_counts[label]), 6) for label in LABEL_ID}

    # Record-level review queue: factual literals, named-token proxies, and lexical distance.
    h_lookup = {row["passage_id"]: row for row in clean_h}
    quality_queue = []
    for row in clean_cf:
        parent = h_lookup[row["source_passage_id"]]
        src_text, out_text = parent["text"], row["text"]
        source_nums, output_nums = set(NUMBER_RE.findall(src_text)), set(NUMBER_RE.findall(out_text))
        source_entities, output_entities = set(ENTITY_RE.findall(src_text)), set(ENTITY_RE.findall(out_text))
        src_words, out_words = sets(src_text), sets(out_text)
        lexical = len(src_words & out_words) / max(1, len(src_words | out_words))
        flags = []
        if source_nums != output_nums:
            flags.append("numeric_literals_differ_review_format_and_facts")
        if source_entities != output_entities:
            flags.append("capitalized_or_technical_tokens_differ_review_entities")
        if row["intervention_type"] == "p_light" and lexical < 0.5:
            flags.append("low_lexical_similarity_for_light_edit")
        if row["intervention_type"] == "p_heavy" and lexical > 0.75:
            flags.append("high_lexical_similarity_for_heavy_edit")
        quality_queue.append({
            "record_id": row["record_id"],
            "source_passage_id": row["source_passage_id"],
            "split": row["split"],
            "topic_cluster": row.get("topic_cluster"),
            "label_type": row["label_type"],
            "intervention_type": row["intervention_type"],
            "generator_family": row.get("generator_family"),
            "model_name": row.get("model_name"),
            "lexical_jaccard_proxy": round(lexical, 4),
            "source_numbers": sorted(source_nums),
            "output_numbers": sorted(output_nums),
            "missing_number_literals": sorted(source_nums - output_nums),
            "new_number_literals": sorted(output_nums - source_nums),
            "source_entity_candidates": sorted(source_entities),
            "output_entity_candidates": sorted(output_entities),
            "entity_candidates_missing": sorted(source_entities - output_entities),
            "entity_candidates_new": sorted(output_entities - source_entities),
            "review_flags": flags,
            "manual_semantic_fact_review": "pending",
            "source_text": src_text,
            "generated_text": out_text,
        })

    # Select 50 H passages across unique documents, stratified by split for human provenance screening.
    meta_path = repo / "human_written_dataset_v2_16_paper" / "manifest" / "metadata_records.jsonl"
    metadata = {row.get("document_id"): row for row in read_jsonl(meta_path)}
    source_documents = {row.get("source_document_id") for row in raw_h}
    source_years = collections.Counter(
        metadata[doc_id].get("canonical", {}).get("year")
        for doc_id in source_documents
        if doc_id in metadata
    )
    rng = random.Random(20261012)
    candidates = list(clean_h)
    rng.shuffle(candidates)
    chosen, chosen_docs = [], set()
    target_per_split = {"train": 35, "dev": 8, "test": 7}
    split_chosen = collections.Counter()
    for passage in candidates:
        doc_id = passage.get("source_document_id")
        if doc_id in chosen_docs or split_chosen[passage["split"]] >= target_per_split[passage["split"]]:
            continue
        doc_meta = metadata.get(doc_id, {})
        canonical = doc_meta.get("canonical", {})
        chosen.append({
            "passage_id": passage["passage_id"],
            "source_document_id": doc_id,
            "split": passage.get("split"),
            "topic_cluster": passage.get("topic_cluster"),
            "year": canonical.get("year"),
            "metadata_source": doc_meta.get("metadata_source"),
            "source_title": canonical.get("title"),
            "source_url": doc_meta.get("source_url") or (doc_meta.get("repository_snapshot") or {}).get("url"),
            "source_text": passage.get("text"),
            "review_status": "pending_manual_source_and_human_authorship_review",
        })
        chosen_docs.add(doc_id)
        split_chosen[passage["split"]] += 1
        if len(chosen) >= 50:
            break

    # Build local, contiguous H -> P hybrid edits only when source and P-light sentence ordinals align.
    light = {row["source_passage_id"]: row for row in clean_cf if row.get("intervention_type") == "p_light"}
    hybrids = []
    for passage in clean_h:
        polish = light.get(passage["passage_id"])
        if not polish:
            continue
        human_sentence_rows = [h_sentences[sid] for sid in passage.get("merged_sentence_ids", []) if sid in h_sentences]
        hybrid = build_hybrid(passage, human_sentence_rows, polish, passage["passage_id"])
        if hybrid:
            hybrids.append(hybrid)

    out.mkdir(parents=True)
    write_jsonl(out / "counterfactual_records.jsonl", clean_cf)
    write_jsonl(out / "labeled_records.jsonl", all_labeled)
    write_jsonl(out / "hybrid_sequences.jsonl", hybrids)
    write_jsonl(out / "leakage_review_queue.jsonl", [
        {"pair": pair, "left_passage": h_by_id.get(pair.get("human_passage_ids", [None])[0]), "right_passage": h_by_id.get(pair.get("human_passage_ids", [None])[1]) if len(pair.get("human_passage_ids", [])) > 1 else None}
        for pair in leakage_pairs
    ])
    write_jsonl(out / "removed_records.jsonl", removed_cf)
    write_jsonl(out / "label_noise_removed.jsonl", identical_p)
    write_jsonl(out / "quality_review_queue.jsonl", quality_queue)
    write_jsonl(out / "human_provenance_review_sample.jsonl", chosen)
    (out / "fixed_document_ids.json").write_text(json.dumps(fixed_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "audit_report.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copy2(Path(__file__).resolve(), out / "repair_nckh_dataset.py")
    (out / "class_weights_sentence_level.json").write_text(json.dumps({"method": "inverse frequency over training-split sentence counts only", "training_counts_by_label": train_sentence_counts, "weights": class_weights, "warning": "Use as a training loss aid; keep dev/test distributions untouched."}, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "source_version": "human_written_dataset_v2_16_paper + ai_counterfactual_dataset_v1",
        "reason_for_source_choice": "AI source_passage_id values resolve in H v2.16; v2.18 passage IDs do not match, so releases were not mixed.",
        "raw_counts": {"H": counts(raw_h), "P": counts([r for r in raw_cf if r.get("label_type") == "P"]), "G": counts([r for r in raw_cf if r.get("label_type") == "G"])},
        "clean_counts": {label: counts(rows) for label, rows in by_label.items()},
        "clean_split_counts_by_record": by_split_label,
        "clean_split_counts_by_sentence": {split: {label: sum(row.get("sentence_count", 0) for row in all_labeled if row.get("split") == split and row.get("label_type") == label) for label in LABEL_ID} for split in ("train", "dev", "test")},
        "clean_domain_counts_by_record": {label: dict(collections.Counter(row.get("topic_cluster", "unknown") for row in rows)) for label, rows in by_label.items()},
        "human_source_provenance": {
            "documents_with_metadata": len(source_documents & metadata.keys()),
            "unique_source_documents": len(source_documents),
            "year_counts": dict(sorted(source_years.items(), key=lambda item: str(item[0]))),
            "documents_after_2022_cutoff": sum(count for year, count in source_years.items() if isinstance(year, int) and year > 2022),
        },
        "training_sentence_class_weights": {"counts": train_sentence_counts, "weights": class_weights},
        "counterfactual_generator_models": dict(collections.Counter(f"{row.get('generator_family')}:{row.get('model_name')}" for row in clean_cf)),
        "repairs": {
            "p_identical_to_h_removed": len(identical_p),
            "source_document_ids_fixed": len(fixed_doc),
            "cross_split_duplicate_candidate_h_passages_quarantined": len(leakage_ids),
            "same_split_exact_duplicate_h_passages_removed": len(same_split_duplicate_ids),
            "counterfactual_rows_removed_with_quarantined_or_duplicate_h": len(removed_cf),
            "human_provenance_sample_rows": len(chosen),
            "quality_review_queue_rows": len(quality_queue),
            "contiguous_h_p_hybrid_sequences": len(hybrids),
        },
        "unresolved_or_requires_new_data": [
            "Semantic similarity and factual consistency need human/semantic-model adjudication; quality_review_queue is a candidate list, not automatic rejection.",
            "Human authorship, year, and PDF evidence require manual checking of human_provenance_review_sample.",
            "AI variants cover only a tiny dev/test sample and only Gemini family; valid unseen-generator and robust test sets require more generation and review.",
            "Generated labels cover only the topics present in existing P/G; networking and cybersecurity need new P/G generation.",
            "Hybrid output uses aligned contiguous H-to-P-light edits; G remains separate because independent G text cannot be safely spliced sentence-by-sentence without discourse review.",
        ],
    }
    (out / "repair_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    notes = """# Repaired Dataset Notes

This is a derived copy built from Human v2.16 and the existing counterfactual v1 records. The generated passage IDs resolve against v2.16; v2.18 uses different passage IDs, so those releases were not mixed.

## Files

- `labeled_records.jsonl`: clean H/P/G passage records for downstream loading.
- `counterfactual_records.jsonl`: cleaned P/G records accepted by the repository validator.
- `hybrid_sequences.jsonl`: contiguous H-context / P-light-span edits with at least two H sentences on both sides and valid punctuation at each transition.
- `repair_summary.json`: raw and cleaned passage, sentence, token-proxy, split, class, topic, and model counts.
- `class_weights_sentence_level.json`: inverse-frequency weights calculated from train sentences only.
- `leakage_review_queue.jsonl`: H passages quarantined because the duplicate scan found a cross-split exact/near-duplicate candidate.
- `quality_review_queue.jsonl`: P/G paired with H source and flags for human fact/entity/semantic review.
- `human_provenance_review_sample.jsonl`: 50 H samples from distinct documents for source, year, and authorship review.
- `label_noise_removed.jsonl`, `removed_records.jsonl`, and `fixed_document_ids.json`: repair logs.
- `audit_report.json`: complete pre-repair audit report.
- `release_audit.json`: post-repair counts, leakage gates, hybrid gates, and generator-generalization status.
- `EVALUATION_PROTOCOL.md`: rules that keep test data out of threshold, prompt, and hyperparameter decisions.

## Counting Units

- 21,024 is the raw v2.16 Human passage count.
- 22,044 is the repaired combined H/P/G record count: 20,997 H, 783 P, and 264 G.
- Approximate tokens are stored whitespace/token-proxy counts, not counts from a specific model tokenizer.

## Limits

Lexical overlap and literal-number differences are review signals, not semantic verdicts. The hybrid set contains local H-to-P edits only; independent G passages are kept as standalone counterfactuals because their sentence-level discourse alignment is not established. The generated dev/test sample remains too small for reliable performance claims, all generated records come from the Gemini family, and Networking/Cybersecurity lack P/G variants. An unseen-generator test therefore remains not ready and requires newly generated, manually reviewed records.
"""
    (out / "REPAIR_NOTES.md").write_text(notes, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
