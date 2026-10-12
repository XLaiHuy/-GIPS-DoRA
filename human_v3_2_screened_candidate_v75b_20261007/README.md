# Sàng lọc PDF v75b — 07/10/2026

Bản này lấy từ `human_v3_2_ready_v73_20261007` và **không sửa bản v73**. `ready` ở v73 là quyết định phát hành của chủ dataset. Trong bản v75b, `pass_candidate` nghĩa là đã qua các cổng tự động bổ sung; nó chưa phải chứng nhận chất lượng qua ảnh PDF.

## Kết quả

| Hạng mục | Số lượng |
|---|---:|
| Chunk nguồn v73 | 22.295 |
| Qua sàng lọc tự động v75b | 21.773 |
| Cách ly | 522 |
| Tài liệu còn chunk ứng viên | 828 |
| Train / dev / test ứng viên | 15.189 / 3.245 / 3.339 |
| Chunk dính dòng tiếp của bullet trên PDF | 492 |
| Bị bác bỏ từ mẫu đọc text/PDF v74 | 30 |
| Chưa chắc từ mẫu v74 | 3 |

Ba lý do cách ly có thể chồng nhau, nên tổng lý do là 525 trong khi số chunk duy nhất là 522. Có 400 thẻ được lấy mẫu độc lập từ phần còn lại và 444 ảnh PDF cắt quanh dòng nguồn tại `pdf_review_400/`. **Chưa có thẻ nào trong 400 thẻ được xác nhận hoàn tất bằng mắt ở lần dựng này.**

Sàng lọc đã mở thành công PDF của toàn bộ 830 tài liệu. Kiểm tra tự động trên 21.773 chunk còn lại không thấy ID trùng, tài liệu hoặc nhóm chạy qua nhiều split, sai SHA-256 của text, chunk dưới hai câu, ngoài khoảng 64–256 token ước lượng, hay ký tự xuống dòng/tab trong text. Các kiểm tra này không chứng minh từng chunk là văn xuôi CNTT sạch.

## Đã sửa ở mã nguồn

- Nhận diện marker PDF Private Use `U+F02D` và các dòng tiếp theo cùng bullet bằng thụt đầu dòng, cỡ chữ và khoảng cách dòng; dừng ở khoảng cách đoạn mới. Một heading dạng số mục đứng riêng cạnh tiêu đề in đậm/in hoa được loại khỏi quy tắc bullet.
- Nối URL bị xuống dòng ngay sau dấu `/` khi phần tiếp theo là một thành phần đường dẫn, đồng thời ghi transformation để truy nguyên. Ví dụ đã đối chiếu: `https://github.com/huggingface/transformers.`
- Đối chiếu tọa độ từng `source_span` của 22.295 chunk với text layer PDF, cách ly toàn bộ chunk trúng vùng dòng tiếp của bullet. Các ID bị cách ly nằm trong `quarantine/t192/screened_rejects.jsonl` cùng lý do và dòng PDF làm bằng chứng.

## Cổng chất lượng còn mở

Metadata của phần ứng viên vẫn có 3.566 chunk với `computing_scope_requires_review`, 1.034 chunk với `body_boundaries_require_review`, và 10.767 chunk với `rights_review_pending`; các nhóm chồng nhau. Quyền sử dụng được chủ dataset xác nhận trong trao đổi trước, nhưng trường metadata/evidence chưa cập nhật đồng bộ. Quy tắc mới **chưa dựng lại toàn bộ 168 tài liệu có chunk dính bullet** để thu hồi văn xuôi sạch. Một số lỗi khác như caption ảnh và đoạn ngoài phạm vi mới được loại khi xuất hiện trong mẫu đã duyệt, chưa có cổng phát hiện đầy đủ trên toàn bộ corpus.

Để mở rộng sinh văn bản AI, cần đọc đối chiếu 400 thẻ trên ảnh PDF, sửa quy tắc với mọi lỗi hệ thống phát hiện, dựng lại nhóm bị ảnh hưởng và lấy mẫu độc lập mới. Chỉ khi tỷ lệ đạt theo PDF đủ ngưỡng đã chốt (đề nghị ≥95%) mới coi bản cập nhật là ground truth đủ sạch cho sinh AI hàng loạt. Trước đó có thể thử pilot nhỏ trên chunk train đã được xác nhận riêng.

## Tệp chính

- `chunk_streams/pass_candidate/t192/{train,dev,test}.jsonl`: ứng viên sau sàng lọc.
- `quarantine/t192/screened_rejects.jsonl`: chunk cách ly và bằng chứng.
- `independent_pdf_review_400.jsonl`: mẫu ngẫu nhiên cố định theo nguồn và split.
- `pdf_review_400/cards.csv` và `pdf_review_400/images/`: phiếu đọc PDF có vùng gần text nguồn.
- `summary.json`: số liệu có thể đọc bằng chương trình.

Tái lập bằng `human_dataset_pipeline/screen_human_v73_pdf_layout_v75.py` và `human_dataset_pipeline/prepare_human_v75_pdf_review.py` từ gốc repo. Các tệp v75b là ứng viên; không đổi tên thư mục `pass_candidate` thành `ready` nếu chưa đóng các cổng trên.
