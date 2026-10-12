# Human v3.2 ready v73

**Status:** ready by explicit dataset-owner decision on 2026-10-07. This is an owner-approved release for the intended training/testing workflow; it is not a claim that every PDF page was visually verified.

## Content and format

22,295 Vietnamese computing-academic prose chunks from 830 source documents. Each UTF-8 JSONL row in `chunk_streams/ready/t192/` has `label=H`, document and section lineage, source PDF SHA-256, split, text, and release fields. Splits: train 15,601; dev 3,292; test 3,402. The chunk text and IDs are unchanged from screened candidate v72. The release sets `human_core_ready` and `generation_ready` by owner decision; `model_input_ready` remains a separate tokenizer/window property.

## Screening and limits

The v67 input had 22,558 chunks. Text/sample review quarantined 158 known rejects and 105 additional format/encoding risks. Fourteen false-positive symbol/step flags were retained. The 579 review cards had 158 rejects and 421 provisional passes; no formal PDF visual attestation is recorded. The source-PDF hash audit matched 835 PDFs, and this release has zero cross-split document, group, fingerprint, or sentence leakage. Rights status/evidence is missing or pending for 322 included documents (11,038 chunks), despite the owner's earlier rights assertion. Extraction rules were not rebuilt after sample defects; full lineage, scope and near-duplicate release review remain open. These limits should accompany any reported training or evaluation result.

Quarantined rows and the review decisions are retained in `quarantine/` and `audit/`. The manifest gives machine-readable counts and limits.
