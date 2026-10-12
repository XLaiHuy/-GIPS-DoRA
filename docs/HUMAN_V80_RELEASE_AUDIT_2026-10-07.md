# Human corpus v80: automated release audit (2026-10-07)

## Decision

**HOLD.** `human_v3_2_provenance_candidate_v80_20261007` is a research candidate, not an attested `ready` release and not approved as bulk AI rewriting ground truth. The owner-approved v73 release remains untouched; its `ready` label reflects the owner's earlier decision and its own manifest records zero formal PDF visual attestations and unresolved rights metadata. Do not silently substitute v80 for v73 or combine their counts.

## Measured state

| Measure | v80 result |
| --- | ---: |
| Pass candidates | 20,020 |
| Source documents contributing chunks | 792 |
| Train / dev / test | 13,727 / 3,041 / 3,252 |
| Source PDF SHA-256 checks | 798 matched |
| Structural, exact duplicate and split audit errors | 0 |
| Residual hits from current image-marker, wrapped-list, attached-caption and image-overlap detectors | 0 |
| Corrected development PDF sample | 400 cards from 265 documents; 442 rendered image crops |
| Recorded independent PDF visual decisions / crosschecks | 0 / 0 |
| Cluster-aware 95% quality lower bound | Not estimable |

The zero layout-hit result covers the explicit detectors above. It does not establish that no other extraction defect exists. PDF hashes verify the local sources, not identity with remote repository PDFs. OU catalog records were linked by item ID and PDF filename for 19 documents; one catalog mismatch remains unresolved.

Source concentration remains high: HPU contributes 10,921 chunks (54.6%) and OU HCMC 6,180 (30.9%); together they account for 85.4% of candidates. Repository-placeholder institutions contribute 2,200 (11.0%) and require source review before release. This distribution must be reported with any future dataset card and considered when interpreting model results.

## Source-level repairs completed

The pipeline rebuilt affected documents from PDFs, rather than editing individual output strings. It removed text from image-backed bullets and wrapped list continuations, repaired two PDFs whose text layer fused spaces using glyph geometry, quarantined a third PDF whose text layer omitted letters pending OCR or replacement, and rejected identified noncomputing paragraphs. A full-source replay after the list fix reduced the candidates to 20,020. The local rights basis is the dataset owner's authorization for internal research; it is not a public redistribution license.

## Open decisions and count gap

The candidate still has 2,012 chunks with computing-scope flags and 931 with body-boundary flags. Other review-reason counts are: repository-placeholder institution 2,200; year conflict 316; language 195; incomplete body extraction 80; unresolved source evidence 16. These categories overlap. Treating all release-blocking flags as unresolved leaves 15,907 chunks without those flags, at least **4,093 below** the 20,000-ready target even before a final visual review. The 3,269 low-confidence heading-path warnings are tracked separately as advisory metadata, pending evaluation of downstream uses.

This conservative subset is materialized as `human_v3_2_strict_candidate_v81_20261007`: 15,907 pass candidates from 619 documents (train 10,777; dev 2,270; test 2,860). All 4,113 flagged v80 pass candidates moved to quarantine, bringing total quarantine to 13,987. The v81 integrity replay matched 798 local PDF hashes and reported zero structural or split errors. The subset remains `candidate_not_ready` and below the target.

The stricter subset is even more concentrated: HPU has 10,449 chunks (65.7%), OU HCMC 5,101 (32.1%), and VNU Hanoi 326 (2.0%). Expansion should prioritize additional universities and evaluate source-balanced performance, not simply add more HPU pages.

The corrected 400-card pack is a **development** sample. It spans source family, split, page type, year and risk. It cannot be used as the post-repair final estimate. Its decisions are blank. A distinct reviewer for the 40-card crosscheck is not available, as confirmed by the dataset owner. No reviewer name, decision, agreement, or confidence interval has been invented.

## Supplemental-source pilot

The official VNU DSpace API was queried for IT thesis metadata, excluding handles already in the corpus. An unrestricted pilot found 100 metadata leads; all 10 downloaded sample PDFs were only 11–17 pages, so none passed the conservative full-text threshold (at least 30 pages and 20,000 extracted characters). A second pilot restricted to 2021–2022 found 90 metadata leads, but zero of them exposed an ORIGINAL PDF bundle to the API. No VNU pilot file was ingested or counted. This result is specific to these search queries and samples; it does not prove that all VNU theses lack full text. Source availability and rights require document-specific evidence.

## Remaining release sequence

1. Resolve source, scope, language, year and body-boundary flags at document/page/paragraph level; rebuild all affected chunks and rerun full-source integrity and layout audits. The cover triage found text evidence for 59 of 91 placeholder-institution documents, but this is not a complete institution verification and does not clear their flags.
2. Recover enough independently valid IT full-text PDFs or verified quarantined material to place 20,000–30,000 **unflagged** chunks into the prospective ready population. Do not inflate the count using overlap, smaller chunks, or duplicate windows.
3. Freeze rules, draw a new *final* 400-card PDF sample from that exact population, inspect all 400 against page images, obtain 40 independent human crosschecks, adjudicate disagreements, and calculate the document-cluster-aware lower confidence bound. Any systematic error requires a rule fix, rebuild, and new final sample.
4. Release only if the gate reports `PASS`. Then pilot AI rewriting on about 200 train chunks, verify meaning and terminology, freeze the prompt, and preserve parent IDs and document splits for all AI outputs.

Machine-readable gate statuses: `human_v3_2_provenance_candidate_v80_20261007/audit/release_gate_status.json` and `human_v3_2_strict_candidate_v81_20261007/audit/release_gate_status.json`. The v81 gate blocks on the quantity threshold and the absent final PDF review/crosscheck; its full layout audit also reports zero defined-pattern hits. Corrected development review pack: `human_v3_2_provenance_candidate_v80_20261007/development_pdf_review_400_corrected/`. The older `development_pdf_review_400/` pack is explicitly marked invalid because its source-family stratification was wrong.
