# Dataset frame v2.7

## Mục tiêu và các tầng dữ liệu

Dataset phục vụ sequence labeling `H/P/G` theo câu. Metadata chỉ dùng cho provenance,
dedup, split, stratification và audit; không nối title, tác giả, GVHD, trường hay năm vào
`text` đưa cho mô hình.

Ba tầng được tách biệt:

1. **Canonical documents/sentences**: văn bản sạch, cấu trúc section → paragraph → sentence,
   có exact document offsets và content hash.
2. **Canonical passages**: các câu liên tiếp trong cùng section, không overlap; đây là đơn vị
   tạo counterfactual `P/G` và pairing `H↔P/G`.
3. **Model windows**: view dẫn xuất theo tokenizer/backbone, có context overlap theo nguyên câu
   và loss chỉ trên các câu trung tâm. Đổi backbone không làm thay đổi hai tầng canonical.

## Inventory và trạng thái xử lý

Mỗi PDF nguồn phải xuất hiện đúng một lần trong `manifest/inventory.jsonl` với SHA-256,
logical repository ID, kích thước, số trang và một outcome:
`accepted`, `duplicate`, `quarantine` hoặc `failed`.

Dedup chạy trước split theo thứ tự SHA-256 → logical repository ID → fingerprint
title/author/year → SimHash text. Các bản tương đương dùng chung `group_id`; một group không
được xuất hiện trong nhiều split hoạt động.

Trang chỉ có hình được bỏ qua. OCR chỉ dành cho tài liệu không đủ text layer, luôn mang
`extraction_method=ocr` và phải qua QA trước khi có thể vào Human core.

## Metadata schema

Các giá trị gốc và canonical được giữ song song:

- `degree_raw` → `document_type_id`
- `faculty_raw` → `faculty_id`, `faculty_name`
- `major_raw` → `major_id`, `major_name`
- `domain_id`
- `language_detected ∈ {vi,en,mixed}`

Field bắt buộc để `training_eligible=true`: `document_id`, `group_id`, provenance nguồn,
title, ít nhất một author, year, institution, clean text ≥ 5.000 ký tự, quality `gold` hoặc
`silver`, và split không phải `excluded`. GVHD, abstract, keywords, degree/faculty/major và
source URL là optional; thiếu optional field chỉ tạo review flag.

Năm trên hai trang bìa đầu được ưu tiên hơn repository year. Cả `cover_year` và
`repository_year` đều được giữ; bất đồng tạo `year_conflict` và đưa vào review, không âm thầm
ghi đè provenance.

## Lọc nội dung

Loại bìa, mục lục, lời cảm ơn, nhận xét, danh mục bảng/hình, references và phụ lục. Giữ prose
học thuật trong thân bài. Clean text phải round-trip được qua các exact character offsets.

## Canonical chunking

- Tách `document → section → paragraph → sentence`.
- Passage ghép nguyên câu liên tiếp trong cùng section; không trộn document và không overlap.
- Budget approximate token: minimum 128, target 384, soft maximum 512.
- Ưu tiên ngắt ở section, sau đó paragraph.
- Tail dưới 128 được nhập vào passage trước nếu tổng không quá 512; nếu không, giữ lại với
  `short_tail=true`.
- Chỉ câu riêng lẻ quá dài mới recursive split theo dấu câu/mệnh đề → whitespace → token
  boundary. Fragment giữ chung `parent_sentence_id`, `fragment_index` và `fragment_count`.

Passage có thể xóa và dựng lại từ sentence records, nhưng là đơn vị canonical dùng sinh dữ
liệu H/P/G. Không sinh counterfactual từ model windows vì overlap sẽ tạo duplicate examples.

## Model windows

Sau khi khóa backbone, dùng tokenizer thật và budget
`model_max_length - special_tokens`. Với context 512 mặc định:

- khoảng 75% budget là câu trung tâm chịu loss;
- khoảng 12,5% là context trái và 12,5% là context phải;
- context được ghép theo nguyên câu và không vượt document hoặc major section;
- `loss_mask=true` chỉ cho câu trung tâm; mỗi câu trung tâm chịu loss đúng một lần;
- giữ mapping token → sentence → exact document offsets.

Các view 256/512/1024 là ablation dẫn xuất. Chọn bằng sentence macro-F1,
boundary-F1/span-IoU, unseen-source F1, VRAM và throughput.

## Split và provenance

Split deterministic 70/15/15 ở cấp `group_id`, stratify theo institution, year bucket,
domain và document type khi kích thước stratum cho phép. Dedup/group luôn chạy trước split.

- `year <= 2022`: `high_confidence_human`, được phép vào Human core.
- `year >= 2023`: `recent_provenance_uncertain`, giữ riêng.
- thiếu năm: `unknown_year`, không vào Human core.

`recent_or_uncertain` chứa tài liệu từ 2023 trở đi hoặc provenance không xác định; đây là
holdout nghiên cứu/OOD, không phải ground-truth Human mặc định.

## Release gates

- Mọi PDF có đúng một inventory outcome; không bỏ file âm thầm.
- 100% sentence/passage offsets round-trip về clean document text.
- Không có active `group_id` ở nhiều split và không exact duplicate trong Human core.
- Không passage vượt 512 approximate tokens hoặc cắt câu nếu không có fragment lineage.
- Mỗi câu trung tâm trong model view chịu loss đúng một lần; không window vượt tokenizer limit.
- Báo cáo phân phối theo institution, year, domain, document type, language, split,
  provenance, quality và lý do loại.
- Review thủ công phân tầng; toàn bộ OCR/quarantine phải được kiểm riêng.

Mục tiêu diversity thực dụng là không trường nào chiếm quá 60% Human core và mỗi trường dùng
để đánh giá độc lập có ít nhất 100 document hợp lệ. Đây là release gate, không phải lý do xóa
dữ liệu nguồn.
