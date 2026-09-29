# Hệ Thống Thu Thập Dataset Khóa Luận, Đồ Án & Luận Văn CNTT (Vietnamese IT Theses Crawler)

Hệ thống thu thập dữ liệu học thuật tự động về đề tài **Công nghệ Thông tin (CNTT)** từ các kho thư viện số đại học lớn tại Việt Nam, phục vụ nghiên cứu NLP, huấn luyện Large Language Models (LLMs), tóm tắt văn bản và phân tích xu hướng công nghệ.

---

## 1. Nguồn Dữ Liệu Hỗ Trợ
1. **Đại học Quốc gia Hà Nội (VNU DSpace 7)**:
   - Kho học liệu số lớn nhất Việt Nam.
   - Trực tiếp từ Trường ĐH Công nghệ (UET), Viện CNTT, Khoa Toán - Cơ - Tin học.
   - Dữ liệu chuẩn Dublin Core đầy đủ Tác giả, GVHD, Năm, Từ khóa, và Tóm tắt (Abstract).
2. **Trường Đại học Mở TP.HCM (OU Library)**:
   - Kho lưu trữ số `https://thuvien.ou.edu.vn`.
   - Thu thập Khóa luận, Luận văn, Luận án chuyên ngành CNTT qua chuẩn MARC21.

---

## 2. Kiến Trúc & Cơ Chế Chống Ban IP
- **Kiến trúc Hybrid**: Kết nối trực tiếp vào REST API nội bộ của thư viện và DSpace, không gây tải ảo giao diện, không cần mở trình duyệt nặng nề.
- **Xoay vòng User-Agent**: Giả lập ngẫu nhiên các trình duyệt máy tính hiện đại (Chrome 124, Firefox 125, Edge 123, Safari 17).
- **Jitter Delay ngẫu nhiên**: Giãn cách các lượt gọi từ 1.2s - 2.8s (tùy chỉnh được), mô phỏng hành vi đọc tự nhiên, tránh kích hoạt rate limit của máy chủ thư viện.
- **Exponential Backoff**: Tự động thử lại (retry) với thời gian chờ tăng dần nếu gặp HTTP 429 hoặc 503.
- **Checkpoint & Auto-Resume**: Lưu vết ID các đề tài đã cào vào `json/.checkpoint.json`. Bạn có thể bấm `Ctrl + C` để dừng bất cứ lúc nào và khi chạy lại, hệ thống sẽ tự động bỏ qua các bản ghi cũ, tiếp tục cào các bản ghi mới mà không bị trùng lặp.
- **Lọc chuyên ngành đa tầng (`filters.py`)**: Tự động nhận diện và phân loại đề tài CNTT dựa trên đơn vị đào tạo, từ khóa chuyên môn (AI, NLP, CV, Web, Mobile, Cloud, IoT, Security...) và loại trừ các đề tài thuần kinh tế, luật, y dược.

---

## 3. Cấu Trúc Thư Mục
```
DATASET_CNTT_SAU_PREPROCESS/
├── json/                             # Thư mục lưu dataset đầu ra
│   ├── vnu_it_theses.jsonl          # Dữ liệu từ ĐHQG Hà Nội
│   ├── ou_it_theses.jsonl           # Dữ liệu từ ĐH Mở TP.HCM
│   ├── dataset_cntt_all.jsonl       # File tổng hợp gộp & khử trùng
│   └── .checkpoint.json             # Trạng thái tiến trình đã cào
├── crawlers/
│   ├── __init__.py
│   ├── base_crawler.py              # Base class: Anti-ban, Jitter, Checkpoint, JSONL
│   ├── filters.py                   # Bộ lọc từ khóa và phân loại chuyên ngành CNTT
│   ├── vnu_crawler.py               # Crawler VNU DSpace 7 REST API
│   └── ou_crawler.py                # Crawler OU Library API
├── main.py                          # CLI runner chính
├── merge_datasets.py                # Script gộp, deduplicate và thống kê dataset
├── test_filters.py                  # Unit test cho bộ lọc CNTT
├── requirements.txt                 # Thư viện phụ thuộc (requests, bs4, tqdm)
└── README.md                        # Hướng dẫn sử dụng
```

---

## 4. Hướng Dẫn Sử Dụng

### Cài đặt thư viện
```bash
py -3 -m pip install -r requirements.txt
```

### Chạy cào dữ liệu
#### 1. Cào thử nghiệm quy mô nhỏ (Ví dụ: mỗi nguồn 20 bản ghi):
```bash
py -3 main.py --limit 20
```

#### 2. Chỉ cào nguồn ĐHQG Hà Nội (VNU):
```bash
py -3 main.py --source vnu --limit 100
```

#### 3. Chỉ cào nguồn ĐH Mở TP.HCM (OU):
```bash
py -3 main.py --source ou --limit 50
```

#### 4. Cào toàn diện quy mô lớn (Không giới hạn số lượng):
```bash
py -3 main.py --source all
```

#### 5. Tùy chỉnh độ trễ chống ban (Delay):
```bash
py -3 main.py --delay-min 2.0 --delay-max 4.0
```

---

## 5. Cấu Trúc Bản Ghi Dataset (Schema JSONL)
Mỗi dòng trong file `.jsonl` là một đối tượng JSON hợp lệ:
```json
{
  "id": "vnu_2009355d-1556-4671-b2f0-3e0cab09501e",
  "source": "VNU DSpace (ĐHQG Hà Nội)",
  "title": "Nghiên cứu giải pháp cải tạo, giải pháp phát triển mới hệ thống thanh toán điện tử liên ngân hàng...",
  "authors": ["Nguyễn, Văn Luân"],
  "advisors": ["Trịnh, Nhật Tiến"],
  "year": 2006,
  "degree": "Thạc sĩ",
  "school_or_faculty": "Trường Đại học Công nghệ",
  "keywords": ["Công nghệ thông tin", "Hệ thống IPBS", "Thanh toán điện tử"],
  "abstract": "Tổng quan về chiến lược tập trung hóa tài khoản và hiện đại hóa ngân hàng...",
  "matched_it_tags": ["công nghệ thông tin"],
  "url": "http://repository.vnu.edu.vn/handle/VNU_123/42521",
  "crawl_time": "2026-09-17T20:01:47"
}
```

---

## 6. Gộp và Phân Tích Thống Kê Dataset
Sau khi cào thêm từ bất kỳ nguồn nào, bạn có thể chạy lại script sau để gộp và xem báo cáo phân bố dữ liệu:
```bash
py -3 merge_datasets.py
```
Script sẽ tự động khử trùng lặp theo ID và độ tương đồng tiêu đề, xuất ra `json/dataset_cntt_all.jsonl` và in bảng thống kê theo:
- Tỷ lệ có tóm tắt (Abstract).
- Phân bố theo nguồn thu thập.
- Phân bố theo bậc đào tạo (Khóa luận, Luận văn thạc sĩ, Luận án tiến sĩ).
- Phân bố theo năm hoàn thành đề tài.
- Top các chủ đề / từ khóa CNTT phổ biến nhất.
