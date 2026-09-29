# human_written_dataset_v2_16_paper

Immutable Vietnamese Human-written thesis corpus release for sentence-level H/P/G research.
Comprehensively normalized: CMap space restored, sentences healed, passages consolidated.

This release contains 387 accepted documents and 34158
non-overlapping Human passages. It is an H-reference corpus; P/G counterfactuals are not included.

## Canonical training data

- `documents.jsonl`: canonical metadata and cleaned body text.
- `sentences.jsonl`: sentence labels and exact document offsets.
- `paragraphs.jsonl`: paragraph-to-sentence linkage.
- `passages/all.jsonl`: non-overlapping canonical passages (256-384 target tokens).
- `splits/{train,dev,test}.jsonl`: immutable group-level splits.

## Provenance

Join `passage.document_id` to `documents.document_id`, then join `source_file_id` to
`manifest/source_files.jsonl`. Sentence IDs and character spans reconstruct each passage exactly.
`lineage/` preserves page-level extraction records from the source PDFs.
