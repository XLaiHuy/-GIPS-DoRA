# Audit Human CNTT v85 — 2026-10-07

## Kết quả thực tế

| Mục | Số lượng / trạng thái |
|---|---:|
| v81 ứng viên chưa có cờ phát hành | 15.907 chunk |
| Tách báo cáo NCKH và bài báo sang cohort phụ | 694 chunk / 23 tài liệu |
| v83 cohort luận văn, khóa luận, đồ án | 15.213 chunk / 596 tài liệu |
| Thu hồi sau kiểm tra ảnh bìa PDF và năm catalog | +203 chunk / 5 tài liệu |
| Cách ly do 41 cặp trùng gần xuyên split | −76 chunk |
| **v85 ứng viên cohort chính** | **15.340 chunk / 601 tài liệu** |
| Cách ly hiện tại | 13.860 chunk |
| Thiếu để đạt 20.000 | **4.660 chunk** |
| Quyết định đối chiếu mẫu PDF cuối | 0 / 400 |
| Duyệt chéo độc lập | 0 / 40 |
| `provisional_internal` / `ready_attested` | `HOLD` / `HOLD` |

Train/dev/test = 10.330 / 2.368 / 2.642 chunk. Theo trường: HPU 10.404 (67,8%), ĐH Mở TP.HCM 4.594 (29,9%), ĐHQG Hà Nội 342 (2,2%). Theo loại: 13.779 chunk khóa luận đại học và 1.561 chunk luận văn thạc sĩ; 573 và 28 tài liệu tương ứng. Năm của tập này trải từ 2007 đến 2022. Độ tập trung lớn ở HPU làm giới hạn khả năng suy rộng; nguồn mới cần ưu tiên trường khác.

## Các quyết định có chứng cứ

Ảnh bìa và năm catalog khớp cho 5 tài liệu được thu hồi. Chứng cứ gồm PDF SHA-256, trang bìa và quyết định riêng trong `human_dataset_pipeline/year_cover_decisions_v84.json`; ảnh ở `human_v3_2_thesis_candidate_v83_20261007/audit/year_cover_evidence/`. Tài liệu catalog 2008 nhưng bìa 2009 không được thu hồi. Việc sửa cờ năm không sửa nội dung chunk; lỗi chữ vốn có của tác giả được giữ nguyên.

Kiểm tra toàn v85: 798 PDF đối chiếu SHA-256, 15.340 chunk kiểm tra ID, nguồn, section, độ dài 64–256 token ước lượng, tối thiểu hai câu, không overlap, hash văn bản, split và trùng chính xác; `error_count=0`. Audit hard rules kiểm tra loại tài liệu, năm, quyền nội bộ, catalog, ký tự lỗi, bullet đầu chunk và trùng gần xuyên split; `error_count=0`. Quét bố cục PDF theo span kiểm tra bullet, caption, ảnh và dòng cuộn; `any_hit_chunks=0`. Không kiểm tra tự động nào thay thế được việc đọc ảnh PDF trên mẫu cuối.

## Nguồn mới đã rà

Pilot VNU cũ có 190 lead metadata qua hai đợt, 10 PDF tải được. Tái sàng lọc 10 PDF bằng `screen_new_human_pdf_batch.py`: 0 hồ sơ đủ điều kiện nhập vì thiếu bằng chứng quyền theo tài liệu, chưa xác minh toàn văn và CNTT trong thân bài; 4 PDF còn mơ hồ về toàn văn hoặc lớp chữ. Không có PDF nào được cộng vào v85. Kho HUST hiển thị luận văn mẫu ở mức [Restricted Access](https://dlib.hust.edu.vn/handle/HUST/352); [CTU](https://dspace.ctu.edu.vn/jspui/handle/123456789/73959?locale=en) cũng ghi Restricted Access cho hồ sơ CNTT đã xem. [VGU](https://epub.vgu.edu.vn/handle/dlibvgu/1733) có giấy phép CC BY-NC cho một luận văn CNTT nhưng nội dung là tiếng Anh, ngoài phạm vi. Trang [VKU](https://elib.vku.udn.vn/handle/123456789/1380) nêu đăng nhập để xem toàn văn ở hồ sơ đã kiểm tra. Quy định của [UIT](https://ir.vnulib.edu.vn/communities/c6181ae0-f204-42e9-9de5-ad3b855acfa5) được giữ ngoài luồng thu thập tự động. Khả năng xem metadata hoặc tải PDF không tự chứng minh quyền tạo corpus.

Kho PDF địa phương hiện có 160 tệp chưa nằm trong hồ sơ v85. `audit_unused_local_theses_v87.py` chọn 57 hồ sơ để rà nguồn trước, không tự nhập. Sau khi ghép catalog và cờ duyệt cũ, 17/57 có metadata ngành ngoài CNTT, 45/57 đã mang cờ cần xem lại, 29/57 chưa ghép được catalog. Hàng đợi có trích đoạn bìa/trang giữa và lý do tại `human_v3_2_unused_local_pdf_audit_v87_20261007/priority_review_queue.jsonl`. Vì từ khóa “phần mềm”, “website” có thể xuất hiện trong luận văn quản trị hoặc luật, 57 chỉ là **lead**, chưa phải 57 tài liệu hợp lệ hay một dự báo chunk.

Đầu vào của lô mới tối đa 100 PDF phải có catalog URL, PDF URL/path, SHA-256, trường, năm, loại luận văn/đồ án, ngôn ngữ, chứng cứ CNTT ở thân bài, bằng chứng quyền theo tài liệu và người xác minh. `screen_new_human_pdf_batch.py` giữ `quarantine` cho trường hợp thiếu; chỉ `eligible_for_extraction_review` mới đi vào trích xuất. Độ dài trang chỉ là tín hiệu rà soát, không phải điều kiện loại cứng cho một luận văn ngắn. Với hiệu suất v85 khoảng 25,5 chunk/tài liệu, khoảng thiếu tương đương sơ bộ 183 tài liệu cùng mức trước hao hụt; phải đo lại sau từng lô 100.

## Quy tắc và cổng phát hành

Trích xuất theo PDF → trang → khối/đoạn → câu → chunk, giữ một tài liệu và một section cho mỗi chunk; loại bìa, mục lục, cảm ơn, tài liệu tham khảo, hình, bảng, code, caption, bullet và viền trang tại tầng nguồn. Tọa độ chữ cần được kiểm tra vì thứ tự đọc PDF có thể khác thứ tự hiển thị ([PyMuPDF](https://pymupdf.readthedocs.io/en/latest/recipes-text.html)). OCR là nhánh riêng và cần đối chiếu ảnh ([OCRmyPDF](https://ocrmypdf.readthedocs.io/en/stable/introduction.html)). Cấu trúc body text theo section và tách bảng/hình tham khảo cách biểu diễn [S2ORC](https://aclanthology.org/2020.acl-main.447/), nhưng quy tắc đã được thiết kế lại cho luận văn tiếng Việt.

Trùng gần dùng shingles 5 từ, Jaccard ≥0,8; 76 chunk trong 41 cặp xuyên split được cách ly ở v85. Việc kiểm tra trùng và rò rỉ trước đánh giá theo động cơ thực nghiệm của [Lee et al.](https://aclanthology.org/2022.acl-long.577/). Mẫu cuối mới sẽ phân tầng theo trường, dải năm, split, loại trang và rủi ro; trọng số chọn mẫu được lưu theo thẻ. Tỷ lệ đạt được tính theo trọng số; cận dưới một phía 95% dùng bootstrap cụm tài liệu và cận Wilson theo số cụm hiệu dụng, lấy cận bảo thủ hơn, nên 400/400 đạt không cho cận 100%.

`provisional_internal` cần 20.000–30.000 chunk, audit tự động sạch, 400 thẻ PDF cuối có quyết định và cận dưới ≥95%. Chỉ được pilot viết lại khoảng 200 chunk train. `ready_attested` đòi thêm 40 quyết định độc lập cùng phân xử bất đồng mới cho sinh AI hàng loạt. Cả hai đang `HOLD`, không có ước lượng tỷ lệ lỗi quan sát được từ mẫu cuối. Nhãn Human thể hiện provenance tài liệu có trước 2023; không chứng minh tuyệt đối rằng tác giả không dùng công cụ AI. Mô tả nguồn, thời gian và giới hạn suy rộng theo tinh thần [Data Statements for NLP](https://aclanthology.org/Q18-1041/).

## Lệnh tái lập phần QA

```powershell
./.venv-human-v3/Scripts/python.exe human_dataset_pipeline/audit_human_v76_candidate.py human_v3_2_dedup_candidate_v85_20261007
./.venv-human-v3/Scripts/python.exe human_dataset_pipeline/audit_human_v84_hard_rules.py human_v3_2_dedup_candidate_v85_20261007
./.venv-human-v3/Scripts/python.exe human_dataset_pipeline/audit_candidate_pdf_layout.py human_v3_2_dedup_candidate_v85_20261007 --tag layout_residual_recheck
./.venv-human-v3/Scripts/python.exe human_dataset_pipeline/evaluate_human_pdf_release_gate.py human_v3_2_dedup_candidate_v85_20261007 --tier provisional_internal
./.venv-human-v3/Scripts/python.exe human_dataset_pipeline/evaluate_human_pdf_release_gate.py human_v3_2_dedup_candidate_v85_20261007 --tier ready_attested
./.venv-human-v3/Scripts/python.exe human_dataset_pipeline/test_human_pdf_release_gate.py
```

Không lấy mẫu PDF cuối cho đến khi nguồn và quy tắc được khóa; các thẻ phát triển v80 không dùng làm phép đo cuối. V73 giữ nguyên như bản lịch sử, không cộng số liệu vào v85.
