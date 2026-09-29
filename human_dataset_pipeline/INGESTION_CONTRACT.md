# Ingestion contract cho nguồn crawl mới

Mỗi trường dùng một `institution_id` ASCII ổn định, ví dụ `hust`, `hcmus`,
`uit_vnu_hcm`. Không đổi ID sau khi đã phát hành dataset.

## Tên file

- Metadata: `<institution_id>_it_theses.jsonl`
- PDF: `<institution_id>_<record_id>_<safe-title>.pdf`
- Trường `id` trong JSONL: `<institution_id>_<record_id>`

`record_id` không được chứa dấu gạch dưới. UUID và dấu gạch ngang được phép.

## Record tối thiểu

```json
{
  "id": "hust_12345",
  "institution_id": "hust",
  "institution_name": "Đại học Bách khoa Hà Nội",
  "source": "Thư viện số Đại học Bách khoa Hà Nội",
  "title": "Tên đề tài",
  "authors": ["Nguyễn Văn A"],
  "advisors": ["Trần Văn B"],
  "year": 2020,
  "degree": "Đồ án tốt nghiệp",
  "school_or_faculty": "Trường Công nghệ Thông tin và Truyền thông",
  "keywords": ["học máy"],
  "abstract": "",
  "url": "https://...",
  "has_full_pdf": true,
  "pdf_path": "...",
  "pdf_download_url": "https://...",
  "pdf_size_bytes": 1234567,
  "crawl_time": "2026-09-19T00:00:00+07:00"
}
```

Schema máy đọc được nằm tại `crawler_record.schema.json`. Các field mở rộng được
phép, nhưng không đổi nghĩa các field chuẩn.

## Quy tắc dữ liệu

1. Chỉ ghi metadata quan sát được từ repository; thiếu thì dùng `null` hoặc `[]`.
2. Không đoán tác giả, GVHD hoặc năm từ URL.
3. Giữ URL landing page và URL PDF riêng.
4. Checksum PDF được pipeline tính sau download, không tin checksum remote.
5. Crawl state không quyết định train/dev/test. Split chỉ được gán sau global dedup.
6. Ưu tiên tài liệu trước năm 2022 cho Human core.
7. Không tải bản chỉ có preview, thiếu trang hoặc vi phạm quyền truy cập.
