# H text normalization v2.15 — Design Specification

## Status

Written for review after approval of the conversational design. Implementation has not started. The current v2.14 release remains unchanged.

## Goal

Create a new H-only release whose text units follow the PDF's paragraph and list structure, whose Unicode text is verified against the PDF text layer and page layout, and whose sentence IDs are suitable for sentence-level H/P/G sequence labeling in `overall architect.md`.

## Context and evidence

The v2.14 release has 15,860 passages from 203 documents. Structural checks passed: passage hashes and offsets reconstruct, required document metadata is populated, and no group spans more than one split. The sentence layer is not ready for the architecture: 63.8% of 191,713 sentence records do not end in sentence-terminal punctuation, and 92.8% of passages contain at least one such record. The random PDF-page spot check showed that physical wrapped lines were retained as separate paragraph and sentence records. The corpus also contains 3,705 private-use glyphs across 86 documents and four replacement characters.

Root cause: `build_dataset.py` extracts page text blocks and creates a paragraph from each block. It joins lines only inside a block, so separate PDF blocks that represent one visual paragraph cannot be rejoined before sentence splitting.

## Scope

- Reprocess the 427 local crawler PDFs with the PDF text layer only. Do not use OCR or image content.
- Keep H text verbatim apart from layout/Unicode normalization that is validated against the source. Do not use LLM rewriting, proofreading, or paraphrasing.
- Preserve the existing document/group-level split assignments. Every H/P/G variant later derived from a source document inherits that document's split.
- Create `human_written_dataset_v1_4`, `human_written_dataset_v2_15`, and `human_written_dataset_v2_15_paper`. Do not overwrite v1.3, v2.14 training view, or v2.14 paper release.
- Keep all crawler records, PDF inventory, excluded cases, and review reasons in manifests. This step creates H only; P/G generation remains out of scope.

## Design

### 1. Separate immutable source text from normalized text

Retain PDF checksum, page index, raw extracted page text, extraction method, and source character spans. Build a separate normalized body text with its own document offsets, sentence offsets, and hashes. Each normalized segment must map back to its source page/span so whitespace or glyph normalization never breaks lineage.

### 2. Reconstruct page layout before paragraphs

Use PyMuPDF text spans/lines and their bounding boxes, font names, and ordering. Merge adjacent physical lines when alignment, vertical gap, indentation, and text continuation indicate one paragraph. Keep section headings, captions, table-like material, and each bullet item as separate units. Permit paragraph continuation across a page break only when layout and text support it; put ambiguous cases in a review queue.

The reconstruction must handle the page-49 regression fixture from `63617_VO VAN THUAN.pdf`: join the wrapped introduction into one paragraph, keep its five bullet items separate, and avoid splitting the phrase `khách hàng` across a passage boundary.

### 3. Apply conservative Unicode and whitespace normalization

- Normalize to NFC; convert NBSP to ordinary space; remove soft hyphens; collapse repeated whitespace.
- Resolve private-use symbols through font/document-aware rules. Convert a verified list glyph to a standard bullet representation while retaining the fact that the source was a list item.
- Repair mis-mapped Vietnamese characters only when the PDF text spans/font map or rendered source verifies the intended character. Unknown private-use glyphs and U+FFFD go to review; affected text does not silently enter the active corpus.
- Do not apply blanket NFKC or global substitutions for glyphs such as `Ƣ`.
- Preserve lexical abbreviations, source spelling, and genuine grammatical errors in H.

### 4. Segment sentence-like units after paragraph reconstruction

Run Vietnamese sentence segmentation after layout reconstruction. Handle common abbreviations, decimal values, numbered headings, and punctuation. Preserve one complete bullet item as one typed `bullet_item` unit, even when the source omits a final period. Do not represent each physical PDF line as a sentence. Uncertain units receive an explicit review flag; they are excluded from sentence-level training/test until resolved.

Long individual units may use the existing recursive fallback at punctuation, whitespace, then token boundaries. Record parent sentence ID and fragment index. Ordinary paragraph/list reconstruction must happen before that fallback.

### 5. Rebuild passages and retain split lineage

Build non-overlapping passages from section → paragraph/list item → sentence-like unit, with 128 minimum as a soft reference, 384 target, and 512 maximum approximate tokens. Keep short tails explicit. Store `unit_type`, `normalization_status`, review flags, source document/group/source-file IDs, split, page range, normalized and source offsets, sentence IDs, token count, hashes, and text. Model input remains `passage.text` only.

### 6. Version and audit

Freeze the old release and publish the rebuilt corpus under v2.15 names. Include a normalization manifest that records each glyph/layout rule, source page, affected span, and whether it was automatically resolved or reviewed. Unknown cases remain auditable and do not become active passages.

## Alternatives considered

1. **Layout-aware reconstruction (selected):** use PDF span geometry and source rendering. It preserves document structure and supports offset lineage, with more extraction logic and visual QA.
2. **Regex-only joining on flattened page text:** simpler, but cannot distinguish wrapped prose from bullets/headings and risks changing text meaning.
3. **OCR the page image:** not selected because the corpus is text-layer-only by requirement and OCR would add recognition noise.

## Acceptance criteria

1. The old releases remain byte-for-byte unchanged.
2. Every crawler row and each of the 427 PDFs has an inventory outcome; exclusions have explicit reasons.
3. Every active sentence-like unit and passage reconstructs from normalized document offsets; source page/span lineage, hashes, and PDF checksums validate.
4. Layout regression tests join wrapped lines and preserve heading/bullet boundaries. No known physical-line fragment is emitted as an independent prose sentence.
5. No unresolved U+FFFD or private-use glyph occurs in active passages. Any glyph map is source/font-aware and documented; ambiguous spans are reviewed/excluded.
6. Sentence-ID use is unique across active passages; duplicate passages are represented in the exclusion manifest.
7. No `group_id` spans train/dev/test. Split membership matches the frozen document split manifest.
8. No passage exceeds 512 approximate tokens; short tails are flagged and reported.
9. The final paper release validator passes. A visual QA sample covers each distinct layout/glyph rule and all ambiguous cases; sentence-boundary metrics are reported by document and split.
10. The release card explicitly states that this H-only version is source data for later P/G construction, not a complete H/P/G training/evaluation set.

## Open implementation detail

The exact geometry thresholds for joining blocks must be established from the regression fixture and stratified PDF samples, then recorded in tests. No arbitrary global threshold may silently merge headings or bullet items.
