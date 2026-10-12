# Repaired Dataset Notes

This is a derived copy built from Human v2.16 and the existing counterfactual v1 records. The generated passage IDs resolve against v2.16; v2.18 uses different passage IDs, so those releases were not mixed.

## Files

- `labeled_records.jsonl`: clean H/P/G passage records for downstream loading.
- `counterfactual_records.jsonl`: cleaned P/G records accepted by the repository validator.
- `hybrid_sequences.jsonl`: 58 contiguous H-context / P-light-span edits with at least two H sentences on both sides and valid punctuation at each transition.
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
