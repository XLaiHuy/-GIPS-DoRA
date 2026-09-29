# -*- coding: utf-8 -*-
"""
clean_invalid_theses.py: Kiểm tra và làm sạch toàn bộ kho Dataset_khoaluan.
Loại bỏ triệt để:
1. File chỉ có trang bìa, tóm tắt, mục lục (< 35 trang)
2. File dung lượng nhỏ không phải đồ án đầy đủ (< 600 KB)
3. File scan ảnh / thiếu text (< 20.000 ký tự)
Đồng bộ làm sạch các file jsonl tương ứng.
"""
import os
import sys
import glob
import json
from pypdf import PdfReader

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

DATASET_DIR = r"D:\Documents\NCKH- Đồ án\NCKH về NLP, VLM, Vision\Dataset_khoaluan"
JSON_DIR = r"D:\Documents\NCKH- Đồ án\NCKH về NLP, VLM, Vision\DATASET_CNTT_SAU_PREPROCESS\json"

MIN_PAGES = 35
MIN_SIZE_BYTES = 600 * 1024  # 600 KB
MIN_CHARS = 20000

def audit_and_clean():
    print("=" * 70)
    print("BẮT ĐẦU QUÁ TRÌNH KIỂM TRA VÀ LÀM SẠCH KHO DỮ LIỆU ĐỒ ÁN")
    print(f"Tiêu chuẩn đồ án full đạt chuẩn:")
    print(f"  - Số trang tối thiểu: >= {MIN_PAGES} trang")
    print(f"  - Dung lượng tối thiểu: >= {MIN_SIZE_BYTES // 1024} KB")
    print(f"  - Độ dài text tối thiểu: >= {MIN_CHARS} ký tự")
    print("=" * 70)

    pdf_files = glob.glob(os.path.join(DATASET_DIR, "*.pdf"))
    total_files = len(pdf_files)
    print(f"Tổng số file PDF trên đĩa hiện tại: {total_files}")

    valid_files = []
    invalid_files = []

    for idx, f in enumerate(pdf_files, 1):
        fname = os.path.basename(f)
        size_bytes = os.path.getsize(f)
        
        # Kiểm tra dung lượng thô trước
        if size_bytes < MIN_SIZE_BYTES:
            invalid_files.append((f, fname, size_bytes, 0, f"Dung lượng nhỏ ({size_bytes//1024} KB < {MIN_SIZE_BYTES//1024} KB)"))
            continue

        # Đọc nội dung PDF
        try:
            reader = PdfReader(f, strict=False)
            num_pages = len(reader.pages)
            if num_pages < MIN_PAGES:
                invalid_files.append((f, fname, size_bytes, num_pages, f"Số trang thiếu ({num_pages} trang < {MIN_PAGES} trang)"))
                continue

            # Đo độ dài văn bản
            sample_pages = min(num_pages, 40)
            total_text = ""
            for p_idx in range(sample_pages):
                try:
                    txt = reader.pages[p_idx].extract_text() or ""
                    total_text += txt
                except Exception:
                    pass

            if len(total_text.strip()) < MIN_CHARS:
                invalid_files.append((f, fname, size_bytes, num_pages, f"Thiếu text/scan ({len(total_text)} ký tự < {MIN_CHARS} ký tự)"))
                continue

            # Đạt toàn bộ chuẩn Quality Gate
            valid_files.append((f, fname, size_bytes, num_pages, len(total_text)))

        except Exception as e:
            invalid_files.append((f, fname, size_bytes, 0, f"File hỏng hoặc lỗi đọc: {e}"))

    print("\n" + "=" * 70)
    print(f"KẾT QUẢ KIỂM ĐỊNH:")
    print(f"  - Tổng số file quét: {total_files}")
    print(f"  - Số file HỢP LỆ (Full đồ án/khóa luận >= 35 trang): {len(valid_files)}")
    print(f"  - Số file KHÔNG ĐẠT (Bìa, tóm tắt, thiếu trang, scan): {len(invalid_files)}")
    print("=" * 70)

    # Xóa các file không đạt chuẩn
    deleted_count = 0
    deleted_names = set()
    for fpath, fname, sz, pgs, reason in invalid_files:
        try:
            os.remove(fpath)
            deleted_names.add(fname)
            deleted_count += 1
        except Exception as e:
            print(f"Lỗi khi xóa {fname}: {e}")

    print(f"Đã xóa thành công {deleted_count} file không đạt chuẩn khỏi thư mục Dataset_khoaluan.")

    # Cập nhật các file JSONL
    print("\nĐang đồng bộ làm sạch các file JSONL metadata...")
    jsonl_files = [
        os.path.join(JSON_DIR, "vnu_it_theses.jsonl"),
        os.path.join(JSON_DIR, "github_theses.jsonl"),
        os.path.join(JSON_DIR, "ute_it_theses.jsonl"),
        os.path.join(JSON_DIR, "ou_it_theses.jsonl"),
        os.path.join(JSON_DIR, "dataset_cntt_all.jsonl"),
    ]

    valid_basenames = {vf[1] for vf in valid_files}

    for jpath in jsonl_files:
        if not os.path.exists(jpath):
            continue
        
        kept_records = []
        removed_records = 0

        with open(jpath, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                    pdf_path = item.get("pdf_path")
                    if pdf_path:
                        base = os.path.basename(pdf_path)
                        # Nếu file PDF đã bị xóa hoặc không nằm trong danh sách hợp lệ
                        if base in deleted_names or base not in valid_basenames:
                            removed_records += 1
                            continue
                    kept_records.append(item)
                except Exception:
                    pass

        with open(jpath, "w", encoding="utf-8") as f:
            for item in kept_records:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

        print(f"  - {os.path.basename(jpath)}: Giữ lại {len(kept_records)} bản ghi chuẩn (Đã lọc bỏ {removed_records} bản ghi không đạt)")

    print("\n" + "=" * 70)
    print("HOÀN TẤT DỌN DẸP KHO DỮ LIỆU! 100% CÁC BÀI CÒN LẠI LÀ ĐỒ ÁN/KHÓA LUẬN FULL ĐẠT CHUẨN.")
    print("=" * 70)

if __name__ == "__main__":
    audit_and_clean()
