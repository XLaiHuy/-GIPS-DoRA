# Kế hoạch xây dựng Human-Written Dataset chuẩn hóa

> Mục tiêu: biến 507 PDF luận văn CNTT tiếng Việt thành một bộ dữ liệu Human-written có provenance rõ ràng, cấu trúc đồng nhất, tái lập được và an toàn cho train/dev/test của GIPS-DoRA.

## 1. Kết quả đầu ra cần đạt

Bộ dữ liệu hoàn chỉnh phải cung cấp đồng thời bốn mức:

1. **Document:** metadata, nguồn, checksum, split và quality report.
2. **Section/paragraph:** cấu trúc học thuật và loại nội dung.
3. **Sentence:** văn bản, character offsets, page mapping và quality flags.
4. **Canonical passage/model window:** đơn vị dùng để tạo H/P/G và đơn vị đưa vào model.

Các điều kiện bắt buộc:

- Raw PDF bất biến, không ghi đè.
- Mọi output truy ngược được về PDF, trang và character offsets.
- Split theo source document trước khi tạo chunk/counterfactual.
- Không dùng LLM để sửa nhánh Human.
- Không để cùng luận văn hoặc near-duplicate lọt qua nhiều split.
- Mọi bước có manifest, version, config hash và reject reason.

## 2. Hiện trạng và khoảng thiếu

### Tài nguyên hiện có

- `Dataset_khoaluan/`: 507 PDF.
- `pipeline_tien_xu_ly/`: đã có PDF filter, extraction/OCR, cleaner, section splitter, quality filter, deduplicator và JSON exporter.
- `DATASET_CNTT_SAU_PREPROCESS/json/`: metadata crawler từ VNU/OU/UTE; chưa phải full-text corpus chuẩn hóa của 507 PDF.

### Các điểm cần nâng cấp trước khi chạy production

1. Schema hiện tại chưa giữ page, paragraph, sentence và character offsets.
2. `min_text_length = 200` chỉ phù hợp smoke test; quá thấp cho một luận văn hoàn chỉnh.
3. `require_title` và `require_authors` đang tắt trong config; nên chuyển thành quality flags/quarantine thay vì bỏ qua im lặng.
4. Language detection chỉ đọc phần đầu; trang bìa/abstract tiếng Anh có thể làm sai nhãn tài liệu tiếng Việt.
5. Dedup hiện chỉ encode 5.000 ký tự đầu; dễ nhầm vì luận văn có trang bìa/mẫu giống nhau và bỏ sót trùng ở thân bài.
6. Section extraction regex có thể nhận nhầm dòng mục lục thành section thật.
7. Sentence output từ VnCoreNLP có thể thay khoảng trắng bằng dấu gạch dưới; không được dùng chuỗi này làm canonical raw sentence.
8. Chưa có content-type filtering cho mục lục, lời cảm ơn, danh mục, references, code, bảng và công thức.
9. Chưa có split manifest bất biến và kiểm tra leakage.
10. Có module `spelling_corrector.py`/`llm_fix_reviewed.py`; không được chạy LLM correction lên bản Human canonical.

## 3. Cấu trúc lưu trữ đề xuất

```text
human_written_dataset_v1/
├── README.md
├── DATA_CARD.md
├── VERSION
├── configs/
│   ├── pipeline.v1.yaml
│   ├── filters.v1.yaml
│   └── chunking.v1.yaml
├── manifests/
│   ├── raw_files.jsonl
│   ├── extraction.jsonl
│   ├── quality.jsonl
│   ├── duplicates.jsonl
│   ├── split_manifest.jsonl
│   └── dataset_stats.json
├── 00_raw/
│   └── pdf/                     # read-only hoặc symlink tới Dataset_khoaluan
├── 01_extracted/
│   ├── pages/                   # một JSONL/document, text theo trang
│   └── assets/                  # optional: OCR/page render diagnostics
├── 02_normalized/
│   └── documents/               # một JSON/document, giữ offsets
├── 03_curated/
│   ├── accepted/
│   ├── quarantine/
│   └── rejected/
├── 04_units/
│   ├── sentences.jsonl
│   ├── paragraphs.jsonl
│   ├── passages.jsonl
│   └── model_windows.jsonl
├── 05_splits/
│   ├── train_documents.jsonl
│   ├── dev_documents.jsonl
│   ├── test_documents.jsonl
│   ├── train_passages.jsonl
│   ├── dev_passages.jsonl
│   └── test_passages.jsonl
└── reports/
    ├── qa_summary.md
    ├── rejected_samples.csv
    ├── duplicate_clusters.csv
    └── manual_review_sample.csv
```

Không sao chép raw PDF nếu không cần; có thể dùng đường dẫn tương đối và SHA-256. Nhưng một release đóng gói phải có quy tắc rõ raw PDF có được phân phối hay không theo giấy phép nguồn.

## 4. Stage 0 — Freeze inventory và provenance

Tạo `raw_files.jsonl`, một dòng/PDF:

```json
{
  "document_id": "thesis_000001",
  "source_file": "Dataset_khoaluan/21436_3485.pdf",
  "source_repository": null,
  "source_record_id": "21436_3485",
  "sha256": "...",
  "byte_size": 1234567,
  "filename_original": "21436_3485.pdf",
  "ingested_at": "2026-09-19T...+07:00",
  "dataset_version": "human-v1"
}
```

Quy tắc:

- `document_id` sinh ổn định từ source ID hoặc SHA-256; không dùng vị trí dòng.
- PDF trùng byte dùng cùng một duplicate cluster.
- Manifest append-only trong một version; thay đổi dữ liệu tạo version mới.
- Lưu git commit/config hash của code đã chạy.

**Quality gate 0**

- Đếm đúng 507 source records.
- 100% có SHA-256 và đường dẫn đọc được.
- Không có hai `document_id` khác nhau cùng SHA-256 mà không được đánh dấu duplicate.

## 5. Stage 1 — PDF validation và routing

Kiểm tra từng PDF:

- mở được, không encrypted hoặc có password;
- số trang hợp lệ;
- page size/orientation distribution;
- tỷ lệ trang có text layer;
- số ký tự trung vị/trang;
- tỷ lệ ký tự replacement `�`;
- tỷ lệ trang ảnh;
- dấu hiệu scan, font mapping lỗi hoặc text extraction rỗng.

Không loại toàn bộ PDF chỉ vì có trang landscape; luận văn CNTT thường có sơ đồ hoặc bảng ngang. Thay `allow_landscape: false` bằng page-level flag. Chỉ quarantine nếu phần lớn tài liệu bất thường.

Routing:

```text
digital_pdf     → direct extraction
hybrid_pdf      → direct extraction + OCR cho trang lỗi
scanned_pdf     → OCR toàn bộ
corrupt/locked  → quarantine
```

**Quality gate 1**

- Mỗi tài liệu có `pdf_type`, `num_pages`, page-level extraction route.
- Mọi tài liệu thất bại có mã lỗi, không bị mất im lặng.

## 6. Stage 2 — Page-aware extraction

Mỗi trang lưu tối thiểu:

```json
{
  "document_id": "thesis_000001",
  "page_index": 0,
  "page_label": "1",
  "extraction_method": "native|ocr|hybrid",
  "raw_text": "...",
  "char_count": 1842,
  "ocr_confidence": null,
  "rotation": 0,
  "warnings": []
}
```

Nguyên tắc:

- Giữ `raw_text` theo trang trước làm sạch.
- OCR chỉ áp dụng cho trang cần OCR; không OCR lại text tốt.
- Với hybrid extraction, chọn native/OCR theo page quality, không nối cả hai gây duplicate.
- Lưu page separator nội bộ để ánh xạ offsets.
- Không sửa nội dung bằng LLM.

**Quality gate 2**

- Tỷ lệ trang có text hữu dụng.
- Median/percentile char count theo trang.
- OCR confidence distribution.
- Random render review tối thiểu 2 trang/document cho tập audit.

## 7. Stage 3 — Normalization bảo toàn nội dung

Tạo hai representation:

### 7.1 `text_faithful`

Bản gần extraction nhất, chỉ:

- Unicode NFC;
- sửa mojibake đã xác minh;
- chuẩn hóa line endings;
- loại ký tự điều khiển/invisible nguy hiểm;
- ghi lại mọi transformation trong audit log.

### 7.2 `text_model`

Bản dùng cho NLP:

- loại repeated header/footer dựa trên tần suất và vị trí trang;
- dehyphenate khi từ bị ngắt cuối dòng;
- nối dòng trong cùng paragraph;
- giữ paragraph breaks;
- chuẩn hóa whitespace;
- giữ nguyên hoa/thường, dấu câu, dấu tiếng Việt, số và ký hiệu kỹ thuật.

Không thực hiện:

- lowercase toàn bộ;
- stop-word removal;
- stemming;
- bỏ dấu tiếng Việt;
- thay số bằng token chung;
- viết lại câu cho “đẹp”;
- sửa chính tả bằng LLM.

Mọi normalized span phải có mapping về raw page span:

```text
model_char_start/end
faithful_char_start/end
page_start/page_end
```

**Quality gate 3**

- Round-trip kiểm tra offsets trên sample.
- Không còn header/footer lặp ở >80% trang.
- Không làm mất paragraph boundaries.
- Character deletion ratio bất thường phải quarantine.

## 8. Stage 4 — Document structure và content typing

Tách hierarchy:

```text
document
  → front matter
  → chapter
  → section/subsection
  → paragraph
  → sentence
```

Mỗi block có `content_type`:

```text
title_page
declaration
acknowledgement
abstract_vi
abstract_en
table_of_contents
list_of_figures
list_of_tables
main_prose
caption
table
equation
code
reference
appendix
unknown
```

Trainable Human corpus mặc định chỉ lấy:

- `abstract_vi` nếu đủ dài;
- `main_prose`;
- prose hợp lệ trong `appendix` nếu được duyệt.

Loại khỏi passage training nhưng vẫn giữ trong document archive:

- title page;
- declaration/acknowledgement;
- mục lục và danh mục;
- reference list;
- standalone code/equation/table;
- caption đơn lẻ;
- template hành chính lặp lại.

Section detection cần phân biệt heading thật với dòng mục lục bằng page position, dot leaders, page-number suffix và mật độ heading.

## 9. Stage 5 — Sentence và word segmentation

Canonical sentence phải giữ nguyên substring của `text_model`; không lưu chuỗi có dấu gạch dưới của word segmentation làm văn bản chính.

Schema câu:

```json
{
  "sentence_id": "thesis_000001_sec003_sent0012",
  "document_id": "thesis_000001",
  "section_id": "thesis_000001_sec003",
  "paragraph_id": "thesis_000001_sec003_para0004",
  "text": "Mô hình được đánh giá trên tập dữ liệu ...",
  "char_start": 12034,
  "char_end": 12091,
  "page_start": 17,
  "page_end": 17,
  "language": "vi",
  "word_tokens": ["Mô_hình", "được", "đánh_giá", "..."],
  "quality_flags": []
}
```

Dùng sentence splitter tiếng Việt và bổ sung rules cho:

- chữ viết tắt: `TS.`, `ThS.`, `PGS.`, `TP.HCM`;
- số mục: `1.2.3`;
- decimal/version/IP address;
- citations `[12]`, `(Nguyễn, 2020)`;
- tên framework và file code;
- bullet/list.

**Quality gate 5**

- 100% câu có valid ordered offsets.
- Ghép các sentence substring phải phủ paragraph ngoại trừ whitespace.
- Cảnh báo câu <3 token, >200 token, không có chữ cái hoặc có OCR noise cao.

## 10. Stage 6 — Quality scoring và Human eligibility

Không dùng một boolean filter duy nhất. Tính quality profile:

```text
metadata_score
extraction_score
language_score
structure_score
prose_score
ocr_score
contamination_risk
overall_quality_score
```

### Ngưỡng document gợi ý cho production

- Có ít nhất 10 trang có prose hoặc ít nhất 8.000 ký tự main prose.
- `vi_probability ≥ 0.80` trên nhiều sample phân bố khắp tài liệu, không chỉ 2.000 ký tự đầu.
- Replacement/control character rate ≤ 0,2%.
- Ít nhất 30 câu main prose hợp lệ.
- Main-prose ratio đủ lớn so với TOC/reference/template.
- OCR confidence đạt ngưỡng nếu tài liệu scan.

Các ngưỡng phải được hiệu chỉnh sau khi xem phân bố thực tế; không tự động reject hàng loạt chỉ vì threshold ban đầu.

### Human provenance tiers

```text
Gold:
- tài liệu hoàn thành trước 2020
- nguồn/tác giả/năm đáng tin cậy
- không có dấu hiệu synthetic contamination

Silver:
- 2020 đến trước 30/11/2022
- provenance hợp lệ nhưng vẫn có khả năng dùng công cụ rewrite cũ

Quarantine:
- thiếu/không chắc năm
- sau mốc cho phép
- text có dấu hiệu bất thường hoặc provenance không đủ
```

`Gold` là tập chính cho geometry discovery. `Silver` dùng mở rộng/ablation. Không gọi bất kỳ tập nào là “100% human”; gọi là human-provenance corpus với tier rõ ràng.

## 11. Stage 7 — Deduplication chống leakage

Dedup theo nhiều tầng:

### 11.1 Exact file/document

- SHA-256 PDF.
- Hash `text_faithful` và `text_model` sau normalization.

### 11.2 Metadata

- Chuẩn hóa title, author, year.
- Fuzzy title + author overlap.

### 11.3 Near-duplicate document

- MinHash/SimHash trên paragraph shingles sau khi bỏ boilerplate.
- Embedding nhiều đoạn lấy từ đầu/giữa/cuối, không chỉ 5.000 ký tự đầu.
- Cluster duplicate bằng graph connected components, không loại từng cặp greedy.

### 11.4 Paragraph/passage contamination

- Hash exact paragraph.
- Character 5-gram MinHash.
- Loại template, quy định, lời cam đoan và mô tả trường/khoa lặp lại.
- Tạo `duplicate_cluster_id` cho document và passage.

Thứ tự bắt buộc:

```text
extract → normalize → remove boilerplate → dedup cluster → split
```

**Quality gate 7**

- Không có exact hash cross-split.
- Không có near-duplicate cluster cross-split.
- Báo cáo số document/passages giữ, gộp, quarantine và reject.

## 12. Stage 8 — Chia train/dev/test

Chia theo `source_document_id` sau dedup nhưng trước chunking và trước tạo P/G.

Điểm khởi đầu:

```text
train: 70%
dev:   15%
test:  15%
```

Với 507 PDF ban đầu, xấp xỉ 355/76/76 trước khi loại chất lượng. Sau filtering phải stratify lại theo:

- provenance tier;
- năm;
- trường/nguồn nếu biết;
- lĩnh vực CNTT;
- độ dài;
- native PDF vs OCR.

Dùng grouped deterministic split:

```text
split_key = stable_hash(dataset_version + duplicate_cluster_id)
```

Không random lại split giữa các lần chạy. Lưu `split_manifest.jsonl` và checksum.

Mọi thành phần sau phải kế thừa split của source:

```text
sentence
paragraph
canonical passage
overlapping model window
P-light/P-medium/P-heavy
G từ mọi generator
mọi augmentation
```

Test set được khóa; không dùng để chọn filter threshold, prompt, rank GIPS, checkpoint hoặc calibration.

## 13. Stage 9 — Canonical passage chunking

Tách biệt hai khái niệm:

### 13.1 Canonical source passages

Đây là đơn vị bất biến dùng để tạo paired H/P/G và khám phá geometry.

Thuật toán:

1. Chỉ lấy block `main_prose`/`abstract_vi` đủ chất lượng.
2. Không trộn hai section khác nhau.
3. Tích lũy nguyên paragraph theo thứ tự.
4. Target 512 backbone tokens.
5. Soft range 384–640 tokens.
6. Hard maximum 768 tokens.
7. Nếu một paragraph >768 token, cắt tại sentence boundary.
8. Tail <192 token được nhập vào passage trước nếu tổng ≤768; nếu không thì giữ và gắn `short_tail=true`.
9. Canonical passages **không overlap**.

Lý do không overlap ở tầng này: cùng một câu xuất hiện ở nhiều source unit sẽ làm tăng trọng số giả, gây leakage trong pairing và làm sai singular spectrum.

`passage_id` sinh quyết định từ:

```text
document_id + section_id + first_sentence_id + last_sentence_id + chunking_version
```

### 13.2 Model windows

Đây là đơn vị input cho Bi-Sentence Transformer/CRF, được tạo sau khi đã có sequence H/P/G:

- target 768 tokens;
- maximum 1.024 tokens;
- tối thiểu 5 câu nếu có thể;
- overlap 25% hoặc 3 câu;
- luôn cắt tại sentence boundary;
- ưu tiên paragraph boundary;
- lưu `window_index`, global sentence indices và overlap mapping.

Model windows được phép overlap vì loss/evaluation có masking và de-overlap aggregation. Canonical passages không được overlap.

## 14. Schema document chuẩn

```json
{
  "schema_version": "human-thesis-1.0",
  "dataset_version": "human-v1",
  "document_id": "thesis_000001",
  "source": {
    "filename": "21436_3485.pdf",
    "relative_path": "Dataset_khoaluan/21436_3485.pdf",
    "record_id": "21436_3485",
    "repository": null,
    "url": null,
    "sha256": "..."
  },
  "metadata": {
    "title": "...",
    "authors": ["..."],
    "advisors": [],
    "year": 2018,
    "institution": null,
    "faculty": null,
    "degree": "khóa luận",
    "keywords": [],
    "topic_tags": ["software_engineering"]
  },
  "provenance": {
    "human_tier": "gold",
    "year_confidence": 0.98,
    "metadata_source": "pdf",
    "llm_modified": false
  },
  "extraction": {
    "pdf_type": "digital",
    "has_ocr": false,
    "num_pages": 65,
    "extractor": "...",
    "extractor_version": "...",
    "warnings": []
  },
  "quality": {
    "language": "vi",
    "language_probability": 0.99,
    "overall_score": 0.93,
    "status": "accepted",
    "flags": []
  },
  "split": "train",
  "sections": []
}
```

## 15. Schema canonical passage

```json
{
  "schema_version": "human-passage-1.0",
  "dataset_version": "human-v1",
  "passage_id": "thesis_000001_sec003_p0007",
  "document_id": "thesis_000001",
  "split": "train",
  "human_tier": "gold",
  "topic_tags": ["computer_vision"],
  "section": {
    "section_id": "thesis_000001_sec003",
    "heading": "3.2 Phương pháp đề xuất",
    "content_type": "main_prose"
  },
  "sentence_ids": ["..."],
  "text": "...",
  "document_char_start": 23110,
  "document_char_end": 25892,
  "page_start": 31,
  "page_end": 33,
  "num_sentences": 9,
  "num_words": 341,
  "num_backbone_tokens": 518,
  "text_sha256": "...",
  "duplicate_cluster_id": null,
  "quality_score": 0.95,
  "quality_flags": [],
  "eligible_for_geometry": true,
  "eligible_for_training": true
}
```

## 16. Storage formats

Nguồn sự thật:

- JSON document riêng cho cấu trúc lồng nhau và audit.
- JSONL cho sentence/paragraph/passage/window streaming.
- UTF-8, `ensure_ascii=false`, newline `LF`.

Khuyến nghị thêm Parquet cho training/statistics khi dataset lớn, nhưng JSONL vẫn là canonical exchange format.

Không lưu token IDs làm dữ liệu gốc duy nhất. Tokenization phụ thuộc model/version; lưu thêm:

```text
tokenizer_name
tokenizer_revision
num_tokens
optional cached token_ids
```

## 17. Quality assurance

### 17.1 Automated checks

- JSON schema validation.
- Unique IDs.
- Monotonic/non-overlapping canonical offsets.
- Sentence coverage trong paragraph.
- Passage coverage trong eligible section.
- Token length constraints.
- Không exact/near duplicate cross-split.
- Không missing split.
- Không passage từ rejected/quarantine document trong training exports.
- Không LLM-corrected record mang nhãn Human.
- Re-running cùng config tạo cùng IDs/hashes/splits.

### 17.2 Manual review

Lấy mẫu phân tầng tối thiểu:

```text
10% documents hoặc ít nhất 50 documents
≥2 pages/document được đối chiếu PDF
≥5 passages/document được kiểm tra cấu trúc/nội dung
oversample OCR, low quality và boundary cases
```

Annotator đánh giá:

- extraction fidelity;
- header/footer removal;
- section correctness;
- sentence boundary correctness;
- content type;
- human eligibility;
- passage coherence.

Lưu kết quả review, không chỉ sửa trực tiếp file output.

### 17.3 Acceptance criteria cho v1

- 100% accepted documents có provenance, hash, split và quality profile.
- ≥98% sentence offsets hợp lệ trên automated check.
- ≥95% sampled passages đạt extraction fidelity theo manual audit.
- 0 exact duplicate và 0 duplicate cluster cross-split.
- 0 LLM-corrected Human passages.
- 100% P/G derivatives về sau truy ngược được tới một `passage_id` H.

## 18. Dataset statistics phải công bố

Theo document và passage:

- số lượng accepted/quarantine/rejected;
- số trang, ký tự, từ, câu, paragraph, passage;
- phân bố năm, nguồn, trường, degree, topic;
- native/OCR/hybrid;
- Gold/Silver/Quarantine;
- content-type proportions;
- passage token-length distribution;
- duplicate clusters;
- split distribution;
- reject reasons;
- missing metadata rates.

## 19. Thứ tự triển khai

### Phase A — Audit và schema

1. Freeze 507-file manifest.
2. Chốt schemas document/page/sentence/passage/window.
3. Viết config versioned và JSON Schema validators.
4. Chạy smoke test 10 PDF đại diện.

**Deliverable:** manifest + schema + audit report 10 PDF.

### Phase B — Extraction và normalization

1. Page-aware native/OCR routing.
2. Tạo `text_faithful` và `text_model`.
3. Header/footer và paragraph reconstruction.
4. Offset mapping tests.

**Deliverable:** 507 extracted records hoặc explicit quarantine/reject records.

### Phase C — Structure và quality

1. Content typing.
2. Section/paragraph/sentence segmentation.
3. Language/quality scoring.
4. Human provenance tiers.
5. Manual audit vòng 1 và chỉnh rule.

**Deliverable:** curated document corpus.

### Phase D — Dedup và split

1. Exact/file/text hashing.
2. Metadata + MinHash + multi-region embedding clusters.
3. Boilerplate removal.
4. Deterministic grouped stratified split.
5. Cross-split leakage tests.

**Deliverable:** locked split manifest.

### Phase E — Passage và model windows

1. Canonical non-overlapping passage generation.
2. Backbone token statistics.
3. Overlapping model-window generation.
4. JSONL/Parquet exports.
5. Manual audit vòng 2.

**Deliverable:** train/dev/test Human-written dataset v1.

### Phase F — Dataset freeze

1. Chạy toàn bộ validators.
2. Sinh stats/report/data card.
3. Ghi checksum cho mọi artifact.
4. Đóng băng `human-v1`.

Chỉ sau Phase F mới bắt đầu tạo P/G counterfactuals.

## 20. Thay đổi ưu tiên trong pipeline hiện có

### P0 — bắt buộc

- Bổ sung raw/page/paragraph/sentence offsets vào schema.
- Tách `text_faithful` và `text_model`.
- Bỏ LLM/spelling rewrite khỏi Human branch.
- Thay document-wide boolean filtering bằng quality profile + quarantine.
- Viết split manifest trước chunking.
- Nâng dedup thành exact + cluster-based near dedup + paragraph dedup.
- Tạo canonical passage IDs ổn định.

### P1 — chất lượng cao

- Page-level native/OCR hybrid routing.
- Content-type classifier/rules.
- Multi-sample language detection.
- Manual review UI/CSV workflow.
- JSON Schema validation và deterministic regression tests.

### P2 — tối ưu

- Parquet exports.
- Cached tokenizer outputs.
- Dashboard thống kê.
- Data lineage graph H → P/G.

## 21. Definition of done

Bộ Human-written dataset được coi là hoàn chỉnh khi:

```text
[ ] 507 raw records đã được inventory bằng SHA-256
[ ] Mọi PDF có accepted/quarantine/rejected outcome
[ ] Accepted text có page-aware provenance và offsets
[ ] Main prose được tách khỏi front/back matter và non-prose
[ ] Document/paragraph/sentence/passages có stable IDs
[ ] Exact và near-duplicate clusters đã khóa
[ ] Train/dev/test split theo source document đã khóa
[ ] Canonical passages không overlap và đúng token constraints
[ ] Model windows có overlap mapping, không gây leakage
[ ] Automated QA đạt acceptance criteria
[ ] Manual audit hoàn tất và được ghi log
[ ] Stats, data card, schema và config được version hóa
[ ] Không có LLM-corrected text trong Human canonical branch
[ ] Mọi future P/G sample có thể truy ngược về source passage H
```

