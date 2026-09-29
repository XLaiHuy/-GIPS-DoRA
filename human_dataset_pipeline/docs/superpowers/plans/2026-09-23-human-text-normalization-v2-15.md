# H Text Normalization v2.15 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a new H-only v2.15 paper corpus whose text follows PDF paragraph/list structure, whose Unicode cleanup is source-verified, and whose units retain source, document, group, and split lineage for sentence-level training and testing.

**Architecture:** Add focused layout-reconstruction and Unicode-normalization modules, then connect them to the existing `build_dataset.py` sentence/passage pipeline. Keep raw text-layer extraction immutable, maintain a character-level map from normalized document offsets to PDF page/source spans, and publish only versioned outputs; retain old group splits where available and freeze deterministic assignments for newly included groups.

**Tech Stack:** Python 3, PyMuPDF, existing JSONL release builders and validators, `unittest`, PowerShell, existing local crawler/PDF directories.

**Spec:** [../specs/2026-09-23-human-text-normalization-design.md](../specs/2026-09-23-human-text-normalization-design.md)

## Global Constraints

- Reprocess the 427 local crawler PDFs with the PDF text layer only. Do not use OCR or image content.
- Keep H text verbatim apart from layout/Unicode normalization that is validated against the source. Do not use LLM rewriting, proofreading, or paraphrasing.
- Preserve the existing document/group-level split assignments. Every H/P/G variant later derived from a source document inherits that document's split.
- Create `human_written_dataset_v1_4`, `human_written_dataset_v2_15`, and `human_written_dataset_v2_15_paper`. Do not overwrite v1.3, v2.14 training view, or v2.14 paper release.
- Keep all crawler records, PDF inventory, excluded cases, and review reasons in manifests. This step creates H only; P/G generation remains out of scope.
- Build non-overlapping passages with 128 minimum as a soft reference, 384 target, and 512 maximum approximate tokens; flag short tails.
- Model input is `passage.text` only. Metadata supports lineage, stratification, and audit.
- No commits are planned: this workspace has no Git repository. Keep each task's changes reviewable and leave prior releases untouched.

## Review Focus

- Nearby text in two-column pages must stay in reading order and never merge across columns; pin with `test_two_columns_never_merge` in Task 1.
- A paragraph apparently continuing across a page break must be joined only when layout and text support it; otherwise flag and exclude the ambiguous unit; pin with `test_ambiguous_cross_page_continuation_is_reviewed_and_excluded` in Task 1 and Task 4.
- Soft hyphens may be removed, but ordinary lexical hyphens must remain; pin with `test_remove_soft_hyphen_preserve_ordinary_hyphen` in Task 2.
- The same private-use code point may mean different glyphs in different fonts/documents; pin with `test_glyph_map_requires_document_and_font_key` in Task 2.
- Image-only or textless pages must be inventoried without OCR or generated chunks; pin with `test_textless_page_inventory_without_chunk` in Task 4.

---

### Task 1: Reconstruct visual paragraphs and list items from PDF text layout

**Files:**
- Create: `human_dataset_pipeline/layout_reconstruction.py`
- Create: `human_dataset_pipeline/test_layout_reconstruction.py`
- Create: `human_dataset_pipeline/testdata/layout_fixture_page49.json`
- Modify: `human_dataset_pipeline/build_dataset.py` only if a small adapter is needed to feed the existing extraction records into the new module.

**Interfaces:**
- Consumes PyMuPDF text-line records. A `TextLine` has `page_index: int`, `line_index: int`, `bbox: tuple[float, float, float, float]`, `text: str`, `spans: tuple[TextSpan, ...]`, and `source_start: int`/`source_end: int` offsets in that page's raw extracted text. Each `TextSpan` has `text: str`, `font_name: str`, `font_size: float`, `bbox`, source offsets, and a trailing zero-based `page_index: int = -1` field so existing positional constructors remain valid. Glyph replacement requires a nonnegative span page index and verification against that page's raw text.
- Produces `LayoutUnit(text: str, unit_type: str, page_start: int, page_end: int, source_spans: tuple[SourceSpan, ...], review_flags: tuple[str, ...])`; `unit_type` is one of `paragraph`, `heading`, `bullet_item`, `caption`, or `table_like`.
- Public function: `reconstruct_page_units(lines: Sequence[TextLine], page_width: float, page_height: float, config: LayoutConfig) -> list[LayoutUnit]`. `LayoutConfig` holds the measured gap, indentation, heading, and column thresholds; thresholds are saved in the release normalization manifest.
- Document-level function: `reconstruct_document_units(pages: Sequence[Sequence[TextLine]], page_sizes: Sequence[tuple[float, float]], config: LayoutConfig) -> list[LayoutUnit]`; it may join across a page boundary only when the documented continuation rule succeeds, otherwise it retains the page boundary and review flag.

- [ ] **Step 1: Write the failing layout regression tests**

Add fixture-backed tests for the page-49 introduction, five bullets, a wrapped bullet continuation, a heading boundary, and two-column text. The core assertions are:

```python
units = reconstruct_page_units(lines, page_width=612, page_height=792, config=fixture_config)
assert units[0].unit_type == "paragraph"
assert "vực phát triển như:" in units[0].text
assert sum(unit.unit_type == "bullet_item" for unit in units) == 5
assert all(not (left.unit_type == right.unit_type == "paragraph"
                and left.page_start == right.page_start
                and left.text.endswith("khách") and right.text.startswith("hàng"))
           for left, right in zip(units, units[1:]))
```

Add `test_two_columns_never_merge` and verify each output unit's `source_spans` preserve the original page and character ranges. Include negative cases where a large vertical gap, changed left edge, heading font, caption prefix, or table-like alignment forces a boundary.

- [ ] **Step 2: Run the new tests and confirm the missing implementation fails**

Run from `human_dataset_pipeline`:

```powershell
& '..\pipeline_tien_xu_ly\venv\Scripts\python.exe' -m unittest test_layout_reconstruction -v
```

Expected: import failure because `layout_reconstruction.py` and `reconstruct_page_units` do not exist yet.

- [ ] **Step 3: Implement the smallest deterministic layout reconstructor**

Use text-line/span boxes, font attributes, x-column clusters, indentation, vertical gap, bullet markers, heading/caption cues, and table-like alignment. Calibrate `LayoutConfig` against the page-49 fixture plus a stratified sample of existing PDFs; record the selected values and sample page IDs in the fixture/manifest. Never merge across detected columns, explicit bullets, heading/caption boundaries, or a large gap. Mark a cross-page continuation `ambiguous_page_continuation` unless both the ending/starting text and page layout meet the documented rule.

The testable unit boundary should remain independent of PyMuPDF:

```python
units = reconstruct_page_units(lines, page_width, page_height, config)
assert all(unit.source_spans for unit in units)
assert all(unit.unit_type in {"paragraph", "heading", "bullet_item", "caption", "table_like"}
           for unit in units)
```

- [ ] **Step 4: Run layout tests and review the visual regression pages**

Run the command from Step 2. Expected: all layout tests pass, the page-49 fixture emits one introduction plus five separate bullet items, and no adjacent-column text is joined. Render the fixture source pages during later release QA; do not use rendered pixels as extraction input.

- [ ] **Step 5: Record the task review checkpoint**

Review `layout_reconstruction.py`, test output, calibrated config, and source span preservation together. No commit command: the workspace is not a Git repository.

### Task 2: Add conservative, source-aware Unicode normalization

**Files:**
- Create: `human_dataset_pipeline/unicode_normalization.py`
- Create: `human_dataset_pipeline/test_unicode_normalization.py`
- Modify: `human_dataset_pipeline/layout_reconstruction.py` to carry zero-based page index on each `TextSpan` while retaining page-relative source offsets.
- Modify: `human_dataset_pipeline/testdata/layout_fixture_page49.json` to retain the original bullet glyph/font evidence used by the regression.

**Interfaces:**
- `GlyphRule` is keyed by `(document_id: str, font_name: str, codepoint: int)` and stores `replacement: str`, `evidence: str`, and `reviewer_status: str`.
- `NormalizationResult` contains `text: str`, `status: str`, `review_flags: tuple[str, ...]`, `rules_applied: tuple[str, ...]`, and `source_char_ranges: tuple[tuple[tuple[int, int], ...], ...]` with one or more half-open source ranges per normalized output character.
- Public function: `normalize_unit(text: str, document_id: str, spans: Sequence[TextSpan], glyph_rules: Mapping[GlyphKey, GlyphRule], source_page_texts: Mapping[int, str] | None = None) -> NormalizationResult`.
- `source_char_ranges` are exact half-open index ranges into the `text` argument; one output character may map to multiple discontiguous input ranges after canonical combining-mark reordering. Source PDF page offsets remain in `TextSpan.source_start/source_end` and layout lineage. Align spans in source order by text and whitespace-normalized form. Apply font-specific rules only when the glyph's span has a valid zero-based `page_index`, its offsets slice the supplied raw source page text back to the full `TextSpan.text`, and its position aligns unambiguously to the corresponding input character; otherwise flag `source_span_unverified` or `span_alignment_unresolved` and do not repair that glyph.

- [ ] **Step 1: Write the failing normalization tests**

Cover NFC, NBSP-to-space, soft-hyphen removal, repeated whitespace collapse, ordinary hyphen preservation, verified Symbol-font bullet mapping, unknown PUA/U+FFFD review flags, and document/font-specific glyph lookup. Include a regression proving `Ƣ` remains unchanged without verified evidence.

```python
result = normalize_unit("A\u00a0B\u00adC lexical-hyphen", "doc-1", spans, {})
assert result.text == "A BC lexical-hyphen"
assert "unresolved_pua" not in result.review_flags
assert normalize_unit("Ƣ", "doc-1", spans, {}).text == "Ƣ"
```

`test_glyph_map_requires_document_and_font_key` must show that a rule for another document or font is not applied. Verify each returned normalized character range points to the raw input character(s) that produced it, including collapsed whitespace.

- [ ] **Step 2: Run the tests and confirm the missing implementation fails**

Run: `& '..\pipeline_tien_xu_ly\venv\Scripts\python.exe' -m unittest test_unicode_normalization -v`

Expected: import failure because `unicode_normalization.py` is not present.

- [ ] **Step 3: Implement only verified normalization rules**

Normalize NFC, map NBSP to a normal space, remove U+00AD, and collapse whitespace while carrying source ranges. Do not use NFKC, global glyph substitutions, or document-wide replacements without an exact `(document_id, font_name, codepoint)` rule with recorded evidence. Convert an approved bullet glyph to `•` and retain `unit_type="bullet_item"`. Set `review_flags` for unmapped private-use characters and U+FFFD; the containing unit receives `status="review_required"` and is not emitted as an active sentence/passage.

```python
result = normalize_unit("A\u00a0B\u00adC", "doc-1", spans, glyph_rules)
assert result.text == "A BC"
assert len(result.text) == len(result.source_char_ranges)
```

- [ ] **Step 4: Run normalization tests**

Run the command from Step 2. Expected: verified rules apply only to their keyed source, unresolved characters are flagged, source ranges cover all normalized characters, `Ƣ` remains unchanged absent evidence, and all tests pass.

- [ ] **Step 5: Record the rule format for the release manifest**

Confirm each `rules_applied` entry can be serialized with document ID, font, code point, replacement, source page/span, evidence, and `automatic` or `reviewed` disposition. No commit command: the workspace is not a Git repository.

### Task 3: Segment reconstructed units and assemble lineage-safe passages

**Files:**
- Modify: `human_dataset_pipeline/build_dataset.py` (`split_sentence_units`, `make_passages`, and record construction)
- Modify: `human_dataset_pipeline/build_training_view.py` (`build_clean_text` and sentence record construction)
- Modify: `human_dataset_pipeline/test_pipeline.py`
- Modify: `human_dataset_pipeline/test_training_view.py`
- Create: `human_dataset_pipeline/test_sentence_units.py`

**Interfaces:**
- Add `SentenceLikeUnit(text: str, unit_type: str, source_spans: tuple[SourceSpan, ...], page_start: int, page_end: int, review_flags: tuple[str, ...])`.
- `segment_layout_unit(unit: LayoutUnit, normalized: NormalizationResult) -> list[SentenceLikeUnit]` segments prose after layout reconstruction; a complete bullet item remains one `bullet_item` unless recursive length splitting is required.
- `split_long_unit(unit: SentenceLikeUnit, max_tokens: int = 512) -> list[SentenceFragment]` uses punctuation, whitespace, then token boundaries and records `parent_sentence_id`, `fragment_index`, and `fragment_count`.
- Passage construction consumes ordered sentence-like units and emits non-overlapping passages with normalized document offsets and source spans.
- Training-view sentence construction consumes each selected canonical paragraph's `unit_type`; it preserves a complete `bullet_item` as one sentence-like training unit unless the long-unit fallback is needed.

- [ ] **Step 1: Write failing sentence and passage tests**

Add tests for Vietnamese terminal punctuation, common abbreviations, decimal values, numbered headings, a bullet with no final period, a wrapped paragraph that becomes a complete sentence after reconstruction, and an overlong unit that recursively splits with parent lineage. Add a training-view regression proving `unit_type="bullet_item"` survives into the final sentence and passage metadata without being split at its physical source lines.

```python
bullet = LayoutUnit("• Mô tả một mục hoàn chỉnh", "bullet_item", 1, 1, spans, ())
units = segment_layout_unit(bullet, normalized_result)
assert len(units) == 1
assert units[0].unit_type == "bullet_item"
```

Also assert no fragment exceeds 512 approximate tokens, ordinary adjacent passages have no overlap, passage text reconstructs from normalized document offsets, and every active sentence ID is unique.

- [ ] **Step 2: Run the focused tests and confirm the behavior is missing**

Run: `& '..\pipeline_tien_xu_ly\venv\Scripts\python.exe' -m unittest test_pipeline test_training_view test_sentence_units -v`

Expected: new imports or assertions fail because segmentation still receives block paragraphs and has no typed bullet behavior.

- [ ] **Step 3: Segment only after layout and normalization**

Update the existing sentence path to process one `LayoutUnit` at a time. Keep heading/bullet unit types on sentence records; apply abbreviation/decimal/numbered-heading protections before Vietnamese sentence boundaries. Run recursive splitting only when one sentence-like unit exceeds the configured maximum. Preserve the parent sentence lineage on every fragment and never split a normal paragraph at physical line boundaries.

- [ ] **Step 4: Assemble and verify canonical passages**

Pass units in document order into existing 128/384/512 non-overlapping passage packing. Store short-tail status explicitly. For each passage, compute normalized `start_char`/`end_char`, source page/span lineage, ordered sentence IDs, token count, content hash, and `text`; assert that slicing normalized body by its offsets exactly equals `text`.

- [ ] **Step 5: Run sentence and passage tests**

Run the command from Step 2. Expected: wrapped lines no longer create fragment sentence records, bullets remain typed and complete, long-unit fragments retain parent lineage, and all prior `test_pipeline` tests still pass.

- [ ] **Step 6: Record the task review checkpoint**

Review sentence IDs, unit types, fragment metadata, passage reconstruction, and max-token assertions together. No commit command: the workspace is not a Git repository.

### Task 4: Integrate text-layer extraction, offsets, inventory, and frozen splits

**Files:**
- Modify: `human_dataset_pipeline/build_dataset.py` (`ExtractedPage`, `extract_pages`, `page_paragraphs`, `build_dataset`, CLI)
- Modify: `human_dataset_pipeline/build_training_view.py` (load and preserve the base corpus split manifest)
- Modify: `human_dataset_pipeline/dataset_document.schema.json`
- Modify: `human_dataset_pipeline/dataset_sentence.schema.json`
- Modify: `human_dataset_pipeline/dataset_chunk.schema.json`
- Modify: `human_dataset_pipeline/test_pipeline.py`
- Modify: `human_dataset_pipeline/test_training_view.py`
- Create: `human_dataset_pipeline/test_text_layer_integration.py`

**Interfaces:**
- `extract_pages(pdf_path) -> list[ExtractedPage]` reads PyMuPDF text `rawdict` text blocks only; each page records `page_index`, raw page text, checksum, `extraction_status`, and line/span character geometry. Image blocks are ignored and OCR is never called.
- `build_document(pdf_path: Path, document_record: Mapping[str, object], frozen_split_manifest: Path | None = None) -> DocumentBuildResult` runs extraction, layout reconstruction, normalization, sentence segmentation, and passage assembly for one PDF; `build_dataset(...)` orchestrates the 427 PDFs and crawler inventory.
- `build_dataset(..., frozen_split_manifest: Path | None = None)` preserves the prior split for known document/group IDs; previously unseen groups receive one deterministic assignment under the existing 70/15/15 configuration, which is saved in the new `split_manifest` and reused downstream.
- `build_training_view` consumes `base/manifest/split_manifest.jsonl` as authoritative, verifies one split per group, and never calls its stratified splitter for groups already present there. Only a missing group may receive the same deterministic group-hash assignment; that assignment is written to the training-view split manifest.
- Every document inventory row records crawler ID, document/group/source-file IDs, PDF path/hash when present, extraction status, active/excluded status, and an explicit exclusion/review reason.
- Sentence and passage rows record `unit_type`, `normalization_status`, `review_flags`, `source_spans`, page range, normalized offsets, source offsets, split, hashes, token count, IDs, and text. Raw PDF text and normalized body text remain separately addressable.

- [ ] **Step 1: Write failing integration tests using synthetic text-line inputs**

Test the whole transformation without OCR: raw text-layer input → layout units → normalized text → sentences/passages. Assert every normalized offset reconstructs the normalized body; each source span resolves to its source page and raw range; source PDF checksum is stored; textless pages get inventory outcomes and no chunks; unknown glyph spans are excluded with reasons; and a supplied frozen group split is unchanged.

```python
result = build_document(sample_pdf, document_record=sample_record,
                        frozen_split_manifest=old_split_manifest)
assert result.document["source_pdf_sha256"] == sha256(sample_pdf)
assert result.inventory[0]["extraction_status"] == "text_extracted"
assert all(p["split"] == old_split_by_group[p["group_id"]] for p in result.passages)
```

Include `test_textless_page_inventory_without_chunk` and a page whose PyMuPDF text block list contains only images. Expected behavior is `extraction_status="no_text_layer"`, a retained page inventory row, and zero passage rows for that page.

- [ ] **Step 2: Run the integration tests to demonstrate the current extraction gap**

Run: `& '..\pipeline_tien_xu_ly\venv\Scripts\python.exe' -m unittest test_text_layer_integration -v`

Expected: failure because current extraction does not expose span-level geometry, a character map, per-page empty-text status, or split-manifest override.

- [ ] **Step 3: Switch extraction to ordered text spans and preserve raw text**

Build page text deterministically from PyMuPDF `rawdict` text blocks/lines/spans/chars, ignoring image blocks. Keep raw extracted text immutable. Assign page-relative source character offsets before layout reconstruction; compose normalized document text separately and maintain output-character-to-source-range mappings through normalization and paragraph joining. Record extraction errors and zero-text pages without OCR.

- [ ] **Step 4: Add the split resolver and complete inventory records**

Load the v2.14 paper release's frozen group/document split assignments. Preserve every known assignment exactly; apply the existing deterministic 70/15/15 assignment method once to new groups, then persist the combined manifest. Update `build_training_view.py` to consume that base manifest as authoritative instead of re-running its stratified splitter for known groups. Fail either build if one group maps to multiple splits or if an old assignment changes; paper release consumes the same mapping.

- [ ] **Step 5: Extend schemas and write lineage metadata**

Update schemas and serializers with the fields in the Interfaces section. Ensure JSONL `text` is the only model-input field; metadata remain outside the text string. Write normalization rules, layout thresholds, source pages/spans, status, and evidence to `normalization_manifest.jsonl`. Preserve every crawler row in the manifest even if the PDF is absent or a text unit is excluded.

- [ ] **Step 6: Run integration and regression suites**

Run: `& '..\pipeline_tien_xu_ly\venv\Scripts\python.exe' -m unittest test_layout_reconstruction test_unicode_normalization test_sentence_units test_text_layer_integration test_training_view test_pipeline -v`

Expected: all new and existing pipeline tests pass; page/source offsets round-trip; textless pages remain in inventory; frozen splits do not change.

- [ ] **Step 7: Record the task review checkpoint**

Inspect schemas, an example active passage, an excluded glyph case, a textless page inventory row, and the frozen split manifest. No commit command: the workspace is not a Git repository.

### Task 5: Strengthen release validation and produce the v2.15 quality report

**Files:**
- Modify: `human_dataset_pipeline/validate_dataset.py`
- Modify: `human_dataset_pipeline/validate_paper_release.py`
- Modify: `human_dataset_pipeline/build_review_reports.py`
- Modify: `human_dataset_pipeline/test_pipeline.py`
- Create: `human_dataset_pipeline/test_release_validation.py`

**Interfaces:**
- Validators report structured issue records with `severity`, `code`, `document_id`, `group_id`, `page_index`, `record_id`, and a concise `message`.
- New checks cover inventory completeness, source PDF checksum, normalized offset reconstruction, source span validity, unresolved PUA/U+FFFD in active passages, unique sentence IDs, duplicate exclusion lineage, token maximum, short-tail flagging, frozen split equality, and group-level split isolation.
- Quality report includes totals by document/split for nonterminal sentence-like units, short tails, review/excluded units, glyph-rule counts, extraction failures, and passage token ranges.

- [ ] **Step 1: Write failing validator tests for each acceptance invariant**

Create small JSONL fixtures that deliberately violate one invariant at a time: a passage with bad hash/offset, a group on two splits, an unresolved PUA in active text, a duplicate sentence ID, a 513-token passage, a missing crawler inventory row, an unflagged short tail, and an invalid source span. Assert stable issue codes and paths.

```python
issues = validate_release(fixture_release)
assert {issue.code for issue in issues} >= {"group_split_leak", "unresolved_glyph", "token_limit_exceeded"}
```

- [ ] **Step 2: Run validator tests and verify the new codes fail**

Run: `& '..\pipeline_tien_xu_ly\venv\Scripts\python.exe' -m unittest test_release_validation -v`

Expected: failure because the additional invariants and structured issue codes are not implemented.

- [ ] **Step 3: Implement validators and report metrics**

Reuse existing paper-release checks where possible. Add deterministic checks for each listed invariant and keep validation read-only. Compute sentence-boundary metrics by document and split, but label them as structural heuristics; do not classify every nonterminal unit as an error because bullet items may omit terminal punctuation. Generate a report that distinguishes active, reviewed, excluded, and duplicate records.

- [ ] **Step 4: Run release-level tests**

Run: `& '..\pipeline_tien_xu_ly\venv\Scripts\python.exe' -m unittest test_release_validation test_curated_release test_metadata_ingestion test_training_view test_model_windows test_layout_reconstruction test_unicode_normalization test_sentence_units test_text_layer_integration test_pipeline -v`

Expected: all tests pass, with each intentionally malformed fixture receiving its expected validator issue.

- [ ] **Step 5: Record the task review checkpoint**

Review validator coverage against all ten spec acceptance criteria and confirm every issue is traceable to a source/document/page/record. No commit command: the workspace is not a Git repository.

### Task 6: Build and audit the new H-only release without changing old outputs

**Files:**
- Modify: `human_dataset_pipeline/README.md`
- Create through existing release builders: `human_written_dataset_v1_4/`, `human_written_dataset_v2_15/`, and `human_written_dataset_v2_15_paper/` with canonical documents, sentences, passages, manifests, split manifest, normalization manifest, and quality report.
- Keep untouched: `human_written_dataset_v1_3/`, `human_written_dataset_v2_14/`, and `human_written_dataset_v2_14_paper/`.

**Interfaces:**
- Build v1.4 from the local crawler PDF directory and the frozen split manifest; derive v2.15 training view and v2.15 paper release from that same v1.4 source and split mapping.
- The final paper release remains H-only and its release card states it is source data for later P/G construction, not a complete H/P/G training/evaluation set.

- [ ] **Step 1: Record checksums of existing releases and verify output paths are unused**

Before any build, hash all files under the three old releases and confirm the three v2.15 output paths do not exist. Abort the build if any destination already exists; do not pass overwrite/clean flags.

- [ ] **Step 2: Build the new base corpus**

From `human_dataset_pipeline`, run:

```powershell
& '..\pipeline_tien_xu_ly\venv\Scripts\python.exe' build_dataset.py --input '..\Dataset_khoaluan' --output '..\human_written_dataset_v1_4' --passage-target 384 --passage-min 128 --passage-max 512 --frozen-split-manifest '..\human_written_dataset_v2_14_paper\manifest\split_manifest.jsonl'
```

Expected: all 427 local PDFs receive inventory outcomes; crawler-only rows remain represented in the paper inventory; unresolved text and extraction failures are excluded with reasons; active records have source and normalized lineage.

- [ ] **Step 3: Build the training view and H-only paper release**

Run:

```powershell
& '..\pipeline_tien_xu_ly\venv\Scripts\python.exe' build_training_view.py --base '..\human_written_dataset_v1_4' --metadata-dir '..\DATASET_CNTT_SAU_PREPROCESS\json' --output '..\human_written_dataset_v2_15' --min-tokens 128 --target-tokens 384 --max-tokens 512
& '..\pipeline_tien_xu_ly\venv\Scripts\python.exe' build_paper_release.py --source '..\human_written_dataset_v2_15' --lineage '..\human_written_dataset_v1_4' --pdf-root '..\Dataset_khoaluan' --metadata-dir '..\DATASET_CNTT_SAU_PREPROCESS\json' --crawler-master '..\DATASET_CNTT_SAU_PREPROCESS\json\dataset_cntt_all.jsonl' --output '..\human_written_dataset_v2_15_paper' --release-id human_written_dataset_v2_15_paper
```

Expected: training and paper outputs use the same group split assignments, and the paper release retains crawler outcomes for all 441 records.

- [ ] **Step 4: Run final validators and inspect visual/layout samples**

Run:

```powershell
& '..\pipeline_tien_xu_ly\venv\Scripts\python.exe' validate_dataset.py '..\human_written_dataset_v1_4'
& '..\pipeline_tien_xu_ly\venv\Scripts\python.exe' validate_training_view.py '..\human_written_dataset_v2_15'
& '..\pipeline_tien_xu_ly\venv\Scripts\python.exe' validate_paper_release.py '..\human_written_dataset_v2_15_paper' --pdf-root '..\Dataset_khoaluan' --crawler-master '..\DATASET_CNTT_SAU_PREPROCESS\json\dataset_cntt_all.jsonl'
```

Expected: zero blocking validator errors; every group appears in one split; no active passage contains unresolved PUA/U+FFFD or exceeds 512 approximate tokens; offsets and hashes reconstruct. Render and inspect page 49 of `63617_VO VAN THUAN.pdf` plus one source page for every applied glyph/layout rule and every ambiguous case. Record reviewed page IDs and outcomes in the quality report.

- [ ] **Step 5: Compare old release hashes and publish the release card/report**

Re-hash the v1.3/v2.14 releases and compare with Step 1; expected: byte-for-byte identical. Confirm the v2.15 release card states H-only scope, source lineage, frozen split method, text-only/no-OCR policy, review exclusions, and future P/G step. Update `README.md` with build/validation commands and the new release paths.

- [ ] **Step 6: Final acceptance review**

Check each of the ten spec acceptance criteria against validator output, quality report, split manifest, inventory, and visual QA notes. Report active/excluded counts and sentence-boundary metrics by document and split. Do not describe the corpus as ready for H/P/G evaluation; it is the normalized H source for later P/G construction.

## Plan Self-Review

- **Spec coverage:** Tasks 1–2 cover layout and source-verified Unicode normalization; Task 3 covers post-layout sentence segmentation, recursive fallback, passage packing, and typed-unit propagation into training view; Task 4 covers source text separation, page/span lineage, crawler/PDF inventory, and frozen splits shared by base and training view; Task 5 covers acceptance validators and sentence-boundary reports; Task 6 builds the three versioned outputs, verifies old-release hashes, and completes visual QA/release documentation.
- **No placeholders:** The plan defines concrete modules, interfaces, fixtures, commands, expected outcomes, and output paths. No TODO/TBD implementation tasks remain.
- **Type consistency:** `TextLine`/`TextSpan` → `LayoutUnit`/`SourceSpan` → `NormalizationResult` → `SentenceLikeUnit`/`SentenceFragment` → document/passages is the shared data flow; all carry source ranges and page lineage.
- **Review focus coverage:** Two-column order, ambiguous page breaks, soft vs ordinary hyphen, document/font-aware glyph maps, and no-text pages each have named regression tests assigned to the code that owns the behavior.
- **Execution recommendation:** Native execution is recommended because the six tasks share evolving dataclasses, offsets, and release schemas; one implementation session can keep those interfaces consistent, followed by a separate whole-plan review.
