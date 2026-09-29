# Chunk quality audit — `human_written_dataset_v2_14_paper`

Audit date: 2026-09-23  
Architecture checked: `overall architect.md` (sentence-level H/P/G labeling; document split before counterfactual generation).

## Verdict

The release passes ID, checksum, offset reconstruction, metadata completeness, token ceiling, and group-split checks. It is **not ready as-is for sentence-level model training or testing**: line wrapping in PDF text extraction has been treated as paragraph/sentence boundaries, and font-specific symbols remain in normalized text. The earlier release validator checks structural lineage; it does not establish sentence coherence.

## Full-release checks

| Check | Result |
|---|---:|
| Active documents / sentences / passages | 203 / 191,713 / 15,860 |
| Document split | train 141, dev 34, test 28 |
| Groups spanning more than one split | 0 |
| Duplicate document, sentence, or passage IDs | 0 |
| Missing required document metadata | 0 |
| Passage text/hash/token recomputation mismatches | 0 |
| Passage reconstruction or sentence-offset failures | 0 |
| Sentence IDs referenced by active passages | 191,291 / 191,713 |
| Unreferenced sentence IDs explained by excluded duplicate passages | 422 / 422 |
| Active exact-text duplicate hashes | 0 |
| Approximate passage tokens | min 3; median 209; p90 402; max 511 |
| Passages below 128 tokens | 5,613 / 15,860 (35.4%); all marked `short_tail` |
| Passages above 512 tokens | 0 |

The minimum size is a reference threshold in this release; the maximum is met. The high share of short tails should still be reviewed after sentence reconstruction.

## Sentence and text-quality findings

- 122,305 of 191,713 sentence records (63.8%) do not end in sentence-terminal punctuation. This is a signal, not a direct error count: list items can legitimately lack periods. However, 14,717 of 15,860 passages (92.8%) contain at least one such record, 9,176 passages end with one, and 2,330 adjacent chunk boundaries have a non-terminal record followed by lowercase text.
- 188 of 203 documents have over 25% non-terminal sentence records; 171 have over 50%. Per-document median is 66.1%.
- 3,705 private-use Unicode characters occur across 86 documents; four U+FFFD replacement characters occur. Sentence text is NFC and has no illegal control characters. Private-use glyphs include bullets and other font-mapped symbols, so map them only after verifying the source PDF; do not blindly replace all private-use characters.
- The release is H-only. It can provide H source passages, but it cannot train or evaluate the complete H/P/G detector until paired P/G data are generated under the same document-level split.

## Root cause found in the pipeline

`build_dataset.py` extracts `page.get_text("blocks", sort=True)`, then `page_paragraphs()` normalizes lines only inside each returned block. A PDF whose visual paragraph is returned as several text blocks therefore becomes several paragraph records. `split_sentence_units()` cannot reconnect those separate blocks. Its `is_fragment` marker covers only the 28 recursive long-sentence splits; it does not mark physical-line fragments.

The random spot-check confirms this mechanism. Passage `paperpassage_1bd31cb2552c27c87ef9` has 19 sentence IDs and 19 paragraph IDs, but the rendered source page has one introductory paragraph and five multi-line bullets. Only two of the 19 records end in punctuation. The passage boundary falls between “khách” and “hàng”. The page also renders “THƯƠNG” correctly while the text layer contains `THƢƠNG`; the bullet is U+F0B7. Its PDF SHA-256, document offsets, and passage reconstruction are correct, so this is a segmentation/character-mapping problem rather than broken lineage.

## Recommended normalization policy

1. Keep extracted source text immutable. Build a separate normalized text layer and retain both raw and normalized offsets/hashes.
2. Apply NFC, convert NBSP to regular space, remove soft hyphens, and collapse repeated whitespace. These steps are already present in the pipeline.
3. Reconstruct lines with PDF layout information (block/span coordinates, baseline, indentation, and vertical gaps). Join wrapped lines within a paragraph; preserve headings and bullet-item boundaries. Dehyphenate only verified line-wrap hyphens.
4. Normalize font-specific characters with document/font-aware mappings verified against the rendered PDF. Convert verified list glyphs to a standard bullet marker; repair mappings such as `Ƣ` only when the source image confirms the intended character. Avoid blanket NFKC, global glyph replacement, and LLM rewriting of H text.
5. Segment sentences after paragraph reconstruction. Treat each complete bullet item as its own sentence-like unit with an explicit unit type; do not turn each physical line into a sentence. Keep uncertain cases in a review queue.
6. Rebuild recursive chunks from repaired sentence units, then regenerate IDs, offsets, hashes, token counts, and manifests. Keep the existing document/group split assignments. Publish the repaired corpus as a new version; preserve v2.14 for audit.

## Go/no-go for this release

- **Go:** provenance, document grouping, stratification, and split assignment as lineage for H sources.
- **No-go:** using current sentence records as sentence-level H/P/G train/test examples without paragraph reconstruction and sentence re-segmentation.
- **Test split:** do not move any train source into test. After normalization, all derived P/G variants must inherit their source document's existing split.
