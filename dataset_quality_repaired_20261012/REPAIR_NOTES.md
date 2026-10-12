# Repaired Dataset Notes

This is a derived copy built from Human v2.16 and the existing counterfactual v1 records. The generated passage IDs resolve against v2.16; v2.18 uses different passage IDs, so those releases were not mixed.

## Files

- `labeled_records.jsonl`: clean H/P/G passage records for downstream loading.
- `counterfactual_records.jsonl`: cleaned P/G records accepted by the repository validator.
- `hybrid_sequences.jsonl`: contiguous H-context / P-light-span edits where source and rewrite sentence ordinals align.
- `repair_summary.json`: raw and cleaned passage, sentence, token-proxy, split, class, topic, and model counts.
- `class_weights_sentence_level.json`: inverse-frequency weights calculated from train sentences only.
- `leakage_review_queue.jsonl`: H passages quarantined because the duplicate scan found a cross-split exact/near-duplicate candidate.
- `quality_review_queue.jsonl`: P/G paired with H source and flags for human fact/entity/semantic review.
- `human_provenance_review_sample.jsonl`: 50 H samples from distinct documents for source, year, and authorship review.
- `label_noise_removed.jsonl`, `removed_records.jsonl`, and `fixed_document_ids.json`: repair logs.
- `audit_report.json`: complete pre-repair audit report.

## Limits

Lexical overlap and literal-number differences are review signals, not semantic verdicts. The hybrid set contains local H-to-P edits only; independent G passages are kept as standalone counterfactuals because their sentence-level discourse alignment is not established. The generated dev/test sample remains too small for reliable performance claims, all generated records come from the Gemini family, and Networking/Cybersecurity lack P/G variants. Those gaps require additional generation and manual review.
