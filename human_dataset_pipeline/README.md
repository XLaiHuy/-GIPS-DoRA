# Human-written dataset pipeline v2.7

Pipeline rebuild toàn corpus thành dataset Human (`H`) bất biến, có provenance, dedup và split
ở cấp tài liệu. PDF nguồn không bị sửa. Trang thiếu text layer được đánh dấu `needs_ocr`; OCR
chưa QA không được trộn vào Human core.

## 1. Build canonical corpus

```powershell
& '..\pipeline_tien_xu_ly\venv\Scripts\python.exe' .\build_dataset.py `
  --input '..\Dataset_khoaluan' `
  --output '..\human_written_dataset_v1_2' `
  --passage-min 128 --passage-target 384 --passage-max 512
```

Output phải mới hoặc rỗng để tránh ghép nhầm nhiều release. Kiểm định:

```powershell
& '..\pipeline_tien_xu_ly\venv\Scripts\python.exe' .\validate_dataset.py `
  '..\human_written_dataset_v1_2'
```

## 2. Build clean training view

Extraction theo từng PDF có thể chạy song song; dedup và split là bước reduce toàn corpus;
chunking chỉ chạy sau khi group/split đã khóa.

```powershell
& '..\pipeline_tien_xu_ly\venv\Scripts\python.exe' .\build_training_view.py `
  --base '..\human_written_dataset_v1_2' `
  --metadata-dir '..\DATASET_CNTT_SAU_PREPROCESS\json' `
  --output '..\human_written_dataset_v2_7' `
  --min-tokens 128 --target-tokens 384 --max-tokens 512
```

Kiểm định:

```powershell
& '..\pipeline_tien_xu_ly\venv\Scripts\python.exe' .\validate_training_view.py `
  '..\human_written_dataset_v2_7'
```

Các artifact chính:

- `documents.jsonl`: metadata canonical, clean full text, eligibility và provenance.
- `sentences.jsonl`: đơn vị H/P/G, offsets và fragment lineage.
- `passages/all.jsonl`: passage canonical không overlap để sinh counterfactual.
- `passages/{train,dev,test}.jsonl`: Human core có year ≤ 2022.
- `passages/recent_or_uncertain.jsonl`: từ 2023 trở đi hoặc provenance chưa chắc chắn.
- `split_manifest.jsonl`: split lock theo group.
- `review/`: metadata/OCR review queues.
- `reports/diversity.json`: phân phối và lý do loại.

`chunks/` được mirror từ `passages/` để tương thích code v2.6; code mới nên dùng
`passages/`.

## 3. Sinh model windows sau khi chọn backbone

Canonical data độc lập tokenizer. Khi đã chọn backbone, tạo derived windows bằng tokenizer
thật:

```powershell
& '..\pipeline_tien_xu_ly\venv\Scripts\python.exe' .\make_model_windows.py `
  --dataset '..\human_written_dataset_v2_7' `
  --tokenizer 'path\to\local-tokenizer' `
  --max-tokens 512 --central-ratio 0.75
```

Window được packing theo nguyên câu: khoảng 75% câu trung tâm chịu loss, 12,5% context mỗi
bên. Không random lại split khi đổi tokenizer hoặc sinh nhánh P/G. Chỉ `model_views/` cần tái
sinh; `document_id`, `group_id`, sentence/passages và split được giữ nguyên.

## 4. Curated active-only release

Khi một nguồn được xác định chỉ cung cấp preview hoặc cần loại toàn bộ record không đạt gate,
tạo release dùng cho train/test mà không phá hủy bản audit:

```powershell
& '..\pipeline_tien_xu_ly\venv\Scripts\python.exe' .\build_curated_release.py `
  --source '..\human_written_dataset_v2_7' `
  --output '..\human_written_dataset_v2_8' `
  --max-year 2022 --exclude-institution hcmute
```

V2.8 chỉ chứa document/chunk Human hợp lệ trong `documents.jsonl`, `sentences.jsonl` và
`passages/`. Record bị loại được giữ dưới dạng metadata không có text tại
`manifest/excluded_records.jsonl` để audit; PDF nguồn không bị xóa.

## Định dạng lưu trữ

JSONL là nguồn canonical vì stream được, dễ diff và audit. Có thể xuất Parquet/WebDataset làm
view tối ưu I/O sau khi schema ổn định, nhưng không thay thế manifest/checksum và JSONL
canonical.

## Rebuild the current crawler corpus as v1.4 / v2.15 / v2.15_paper

Run from `human_dataset_pipeline/`. Outputs are immutable and must not already exist.
The extraction stage reads the PDF text layer only; it does not run OCR or ingest image pixels.

```powershell
# 1. Base Canonical Corpus v1.4
..\pipeline_tien_xu_ly\venv\Scripts\python.exe .\build_dataset.py `
  --input '..\Dataset_khoaluan' `
  --output '..\human_written_dataset_v1_4' `
  --passage-min 128 --passage-target 384 --passage-max 512

..\pipeline_tien_xu_ly\venv\Scripts\python.exe .\validate_dataset.py `
  '..\human_written_dataset_v1_4' `
  --pdf-root '..\Dataset_khoaluan'

# 2. Training View v2.15
..\pipeline_tien_xu_ly\venv\Scripts\python.exe .\build_training_view.py `
  --base '..\human_written_dataset_v1_4' `
  --metadata-dir '..\DATASET_CNTT_SAU_PREPROCESS\json' `
  --output '..\human_written_dataset_v2_15' `
  --min-tokens 128 --target-tokens 384 --max-tokens 512

..\pipeline_tien_xu_ly\venv\Scripts\python.exe .\validate_training_view.py `
  '..\human_written_dataset_v2_15'

# 3. Paper Release v2.15_paper
..\pipeline_tien_xu_ly\venv\Scripts\python.exe .\build_paper_release.py `
  --source '..\human_written_dataset_v2_15' `
  --lineage '..\human_written_dataset_v1_4' `
  --pdf-root '..\Dataset_khoaluan' `
  --metadata-dir '..\DATASET_CNTT_SAU_PREPROCESS\json' `
  --crawler-master '..\DATASET_CNTT_SAU_PREPROCESS\json\dataset_cntt_all.jsonl' `
  --output '..\human_written_dataset_v2_15_paper' `
  --release-id human_written_dataset_v2_15_paper

..\pipeline_tien_xu_ly\venv\Scripts\python.exe .\validate_paper_release.py `
  '..\human_written_dataset_v2_15_paper' `
  --pdf-root '..\Dataset_khoaluan' `
  --crawler-master '..\DATASET_CNTT_SAU_PREPROCESS\json\dataset_cntt_all.jsonl'
```

The final release records the full PDF inventory, all crawler master rows, unmatched local PDFs,
and the document-level train/dev/test assignment. `manifest/crawler_records.jsonl` distinguishes
linked PDFs, metadata-only records, and crawler claims whose PDF is absent locally.
