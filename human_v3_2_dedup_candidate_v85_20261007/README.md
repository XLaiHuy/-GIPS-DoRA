# Human CNTT v85 — ứng viên, chưa phát hành

**Trạng thái: `HOLD`.** Đây là 15.340 chunk ứng viên thuộc 601 khóa luận hoặc luận văn tiếng Việt. Không có chunk nào của v85 được gắn `ready`, `provisional_internal` hay `ready_attested`. Không dùng v85 để sinh AI hàng loạt.

## Dữ liệu và lineage

- `chunk_streams/pass_candidate/t192/{train,dev,test}.jsonl`: 10.330 / 2.368 / 2.642 chunk; mỗi dòng là một đối tượng JSON, trường `text` là văn xuôi, `document_id`, `section_id`, `source_spans`, `source_pdf_sha256`, `group_id` và `split` lưu nguồn gốc.
- `chunk_streams/quarantine/t192/`: 13.860 chunk bị cách ly. Không cộng vào số ứng viên.
- `supplementary_non_thesis/pass_candidate/t192/`: 694 chunk báo cáo NCKH hoặc bài báo, tách khỏi chỉ tiêu luận văn/đồ án.
- `documents.jsonl`: hồ sơ 798 PDF đã kiểm tra SHA; 601 tài liệu có chunk ở cohort chính. Quyền sử dụng nguồn hiện có là nghiên cứu nội bộ theo xác nhận của chủ dự án; không suy ra quyền phân phối công khai.
- `audit/validation.json`, `audit/hard_rules.json`, `audit/layout_residual_summary.json`: kết quả kiểm tra toàn bộ ứng viên. Cả ba báo **0 lỗi** trong các phép kiểm tự động được triển khai. Điều này chưa đo được tỷ lệ lỗi thực tế so với ảnh PDF.
- `audit/release_gate_provisional_internal.json` và `audit/release_gate_ready_attested.json`: đều `HOLD`.

## Các thay đổi so với v81

Tách 694 chunk không thuộc luận văn/đồ án; đối chiếu ảnh bìa và thu hồi 203 chunk chỉ bị cờ năm từ 5 luận văn/khóa luận. Một tài liệu có năm catalog 2008 nhưng bìa 2009 vẫn bị cách ly. Quét trùng gần phát hiện 41 cặp xuyên split và cách ly 76 chunk liên quan. V73 vẫn là bản lịch sử độc lập; không cộng số liệu hai phiên bản.

## Còn thiếu trước khi dùng làm ground truth

Cần thêm **4.660 chunk đạt chuẩn** để tới 20.000; đích dự phòng 23.000 cần thêm 7.660. PDF mới cần được duyệt bằng chứng toàn văn, CNTT trong thân bài, năm ≤2022 và quyền nghiên cứu nội bộ theo từng tài liệu. Sau khi khóa nguồn và quy tắc, lấy **mẫu cuối mới 400 chunk** đối chiếu ảnh PDF. Cổng `provisional_internal` chỉ mở khi cận dưới 95% một phía, có xét cụm tài liệu, đạt ≥95% cùng mọi kiểm tra khác; nó chỉ cho pilot khoảng 200 chunk train. Cổng `ready_attested` còn đòi 40 quyết định độc lập và phân xử bất đồng.

Xem [báo cáo v85](../docs/HUMAN_V85_CANDIDATE_AUDIT_2026-10-07.md) để biết nguồn, số liệu và giới hạn.
