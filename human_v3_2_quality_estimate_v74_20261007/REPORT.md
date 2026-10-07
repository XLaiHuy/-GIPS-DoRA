# Ước lượng chất lượng Human ready v73 — 07/10/2026

## Kết luận dùng được ngay

`ready` trong v73 là quyết định phát hành của chủ dataset: **22.295/22.295 dòng có nhãn ready**. Nhãn đó không phải bằng chứng mọi chunk đạt chất lượng.

Mẫu mới lấy ngẫu nhiên từ chính v73 gồm **120 chunk / 102 tài liệu**, 60 từ `core` và 60 từ `hpu` (quần thể tương ứng 11.038 và 11.257 chunk). Trợ lý đọc toàn bộ văn bản mẫu và đối chiếu hình trang PDF ở **13 trường hợp minh họa**:

| Kết luận sơ bộ | Core | HPU | Tổng |
|---|---:|---:|---:|
| Đạt theo màn rà text | 43 | 44 | **87** |
| Chưa chắc | 2 | 1 | **3** |
| Không đạt | 15 | 15 | **30** |

Ước lượng theo hai nguồn, tính cả trường hợp chưa chắc là **72,5–75,0%** có thể dùng theo rubric văn xuôi CNTT. Tương đương khoảng **16,2–16,7 nghìn chunk** trong 22.295, **chỉ là phép chiếu từ mẫu**, không phải danh sách chunk đã được xác minh. Nếu tạm coi từng nhãn text là đúng và các chunk độc lập, khoảng Wilson 95% là **63,9–79,7%** cho mức chắc đạt và **66,6–81,9%** khi tính cả chưa chắc là đạt. Các khoảng này **không** phản ánh lỗi người chấm, tương quan nhiều chunk trong cùng tài liệu hay lỗi PDF chưa được quan sát; không gọi chúng là khoảng tin cậy cho “ground truth hoàn chỉnh”.

Mẫu 579 thẻ cũ có 152/570 thẻ thuộc phần lấy mẫu phân tầng ban đầu bị loại (26,7%); chín thẻ chọn riêng vì cờ rủi ro có 6 thẻ bị loại. Đây là bản trước khi loại 263 ID và không được gộp với mẫu v73 để tính một tỷ lệ chung. Hai mẫu cùng cho thấy nhãn `ready` không đồng nghĩa với tỷ lệ sạch gần 100%.

## Lỗi quan sát được và ý nghĩa

- **Sai phạm vi:** đoạn về chiến lược bảo hiểm, thị trường chứng khoán, nghiệp vụ nhân sự, kho hàng hoặc đặt vấn đề kinh doanh không có nội dung CNTT trực tiếp. Rubric này chấp nhận đoạn nói về thiết kế/vận hành phần mềm hoặc phương pháp CNTT, kể cả luận văn ứng dụng.
- **Sai dạng văn bản:** danh sách bước đánh số, nhãn thành phần, bảng/ảnh kèm chú thích, đoạn kết luận cá nhân. Một marker bullet có thể biến mất trong text layer; PDF của `chunk32_1922c233060ce4c1945050e8` cho thấy nó nằm trong bullet dù chunk không chứa ký hiệu bullet.
- **Lỗi nối/trình bày:** PDF của `chunk32_745372ebbfe1bfae2c98d512` xuống dòng giữa một URL; chunk đưa khoảng trắng vào URL. Cần sửa quy tắc nối URL tại nguồn.
- **Lỗi vốn có của tác giả:** một số chữ dính/sai đã hiện trên trang PDF, ví dụ `chunk32_7816c89ef2b3d63076e61a1c` và `chunk32_88a0fd3e7df416233245360c`. Theo quyết định dự án, giữ nguyên lỗi tác giả nếu đoạn vẫn là văn xuôi dùng được; những đoạn quá méo nghĩa được loại khỏi mục tiêu “text sạch”.

Mẫu cũ tìm thấy lỗi tại 121 tài liệu. Sau khi loại các ID đã biết, v73 vẫn chứa **5.210 chunk trong các tài liệu đó** (23,4% tập). Đây là vùng cần rà ưu tiên, không phải 5.210 chunk đều lỗi. Metadata hiện còn `computing_scope_requires_review` ở 3.640 chunk và `body_boundaries_require_review` ở 1.067 chunk; các cờ có thể chồng nhau và không tự động kết luận sai. Metadata quyền sử dụng còn trống/chờ ở 11.038 chunk, là vấn đề hồ sơ nguồn riêng với ước lượng chất lượng text.

## Chiến lược nâng chất lượng mà không duyệt tay 22 nghìn chunk

1. **Đóng rubric trước khi sửa dữ liệu.** Chốt tiêu chí CNTT ở cấp chunk: văn xuôi liên tiếp về phương pháp, thiết kế, triển khai, vận hành hoặc đánh giá hệ thống/phần mềm; loại nghiệp vụ thuần túy, caption, bảng, bullet/list, code, lời cảm ơn và kết luận cá nhân. Giữ lỗi chính tả có in trong PDF nhưng ghi cờ; loại lỗi do trích xuất và đoạn nguồn méo nghĩa nghiêm trọng.
2. **Sửa tại PDF/page rồi dựng lại.** Ưu tiên 121 tài liệu từng có mẫu loại và nhóm cờ scope/body. Dùng tọa độ dòng/khối để nhận ra item bullet mất marker, caption ngay dưới hình, vùng bảng, đường viền và heading. Nối URL qua dòng theo quy tắc riêng. Khi một trang sai, rà mọi chunk cùng trang và dựng lại cả run/section liên quan; không chỉ xóa ID đã phát hiện.
3. **Quét toàn tập, con người xem phần khó.** Áp dụng rule và mô hình triage cho mọi chunk, giữ ba hàng `pass_candidate`, `reject`, `uncertain` cùng lý do và vị trí PDF. Xem tất cả `uncertain`/cờ nghiêm trọng bằng trang render; rà theo trang hoặc tài liệu để tiết kiệm thao tác. Không dùng OCR thay cho trang gốc.
4. **Lấy mẫu độc lập sau mỗi lần dựng lại.** Ít nhất khoảng **400 chunk** ngẫu nhiên phân tầng theo nguồn, split, năm, kiểu trang và mức rủi ro; đối chiếu đầy đủ hình PDF và text. Cỡ này cho sai số lấy mẫu xấp xỉ ±5 điểm phần trăm ở mức 95% trong trường hợp bất lợi nhất nếu coi chunk độc lập. Nếu cần ±5 điểm phần trăm **riêng cho từng nguồn**, lấy khoảng 370 chunk mỗi nguồn. Báo cáo lỗi quan sát, khoảng tin cậy và tỷ lệ từng nhóm; rà riêng mọi cờ rủi ro không đưa vào ước lượng ngẫu nhiên. Xem [NIST về cỡ mẫu](https://www.itl.nist.gov/div898/handbook/ppc/section3/ppc333.htm) và [khoảng Wilson](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm).
5. **Chỉ mở rộng sinh AI khi mẫu mới đạt ngưỡng.** Đề nghị tỷ lệ đạt theo PDF ≥95% trên mẫu độc lập, không có lỗi hệ thống chưa sửa; báo cáo cận dưới và từng nguồn. Có thể chạy pilot nhỏ từ `train` hiện tại với kiểm tra riêng từng H gốc. Giữ mọi bản AI cùng document/split của H, không dùng test để chọn prompt hay chỉnh bộ lọc. Với báo cáo đánh giá, ghi rõ số H thực sự đã đối chiếu PDF.

Tài liệu mô tả nên nêu nguồn, quy trình lấy mẫu, lỗi quan sát và mục đích dùng theo khuyến nghị của [Datasheets for Datasets](https://doi.org/10.48550/ARXIV.1803.09010). Mẫu và từng quyết định nằm trong `sample_120.jsonl`, `text_triage_decisions.jsonl`, `summary.json`; script tái lập nằm ở `../human_dataset_pipeline/estimate_human_v73_ready_quality.py`.
