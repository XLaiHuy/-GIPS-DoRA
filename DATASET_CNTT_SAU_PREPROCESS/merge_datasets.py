# -*- coding: utf-8 -*-
"""
merge_datasets.py: Gộp các file jsonl từ các nguồn (VNU, OU, v.v.),
khử trùng lặp (deduplicate), xuất file tổng hợp dataset_cntt_all.jsonl
và in báo cáo thống kê chuyên sâu về dataset.
"""
import os
import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
import json
import re
from collections import Counter
from typing import Dict, Any, List

def normalize_title(title: str) -> str:
    """Làm sạch tiêu đề để so sánh khử trùng lặp."""
    if not title:
        return ""
    t = title.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def merge_and_stats(
    input_dir: str = "json",
    output_filename: str = "dataset_cntt_all.jsonl"
):
    output_path = os.path.join(input_dir, output_filename)
    all_files = [
        os.path.join(input_dir, f)
        for f in os.listdir(input_dir)
        if f.endswith(".jsonl") and f != output_filename
    ]

    print(f"=== BẮT ĐẦU GỘP DATASET KHÓA LUẬN & LUẬN VĂN CNTT ===")
    print(f"Tìm thấy các file nguồn: {[os.path.basename(f) for f in all_files]}")

    records_by_id: Dict[str, Dict[str, Any]] = {}
    seen_titles = set()

    for file_path in all_files:
        filename = os.path.basename(file_path)
        count_in_file = 0
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    rec_id = str(data.get("id"))
                    title = data.get("title", "")
                    norm_t = normalize_title(title)

                    # Khử trùng theo ID và tiêu đề
                    if rec_id in records_by_id or (norm_t and norm_t in seen_titles):
                        continue

                    records_by_id[rec_id] = data
                    if norm_t:
                        seen_titles.add(norm_t)
                    count_in_file += 1
                except Exception as e:
                    pass
        print(f"  + {filename}: Đã nạp {count_in_file} bản ghi hợp lệ.")

    # Ghi ra file gộp tổng hợp
    total_records = len(records_by_id)
    with open(output_path, "w", encoding="utf-8") as f:
        for r in records_by_id.values():
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\n=> ĐÃ XUẤT FILE TỔNG HỢP: {output_path}")
    print(f"=> Tổng số bản ghi duy nhất: {total_records}")

    if total_records == 0:
        return

    # Thống kê chi tiết
    sources_counter = Counter()
    degrees_counter = Counter()
    years_counter = Counter()
    has_abstract_count = 0
    top_keywords = Counter()

    for r in records_by_id.values():
        sources_counter[r.get("source", "Không rõ")] += 1
        degrees_counter[r.get("degree", "Khác")] += 1
        y = r.get("year")
        if y:
            years_counter[y] += 1
        abstract = r.get("abstract", "").strip()
        if abstract:
            has_abstract_count += 1
        for kw in r.get("keywords", []):
            top_keywords[kw] += 1
        for tag in r.get("matched_it_tags", []):
            top_keywords[tag] += 1

    print("\n" + "=" * 55)
    print("           BÁO CÁO THỐNG KÊ BỘ DỮ LIỆU (DATASET STATS)")
    print("=" * 55)
    print(f"Tổng số bản ghi: {total_records}")
    print(f"Số bản ghi có tóm tắt (Abstract): {has_abstract_count} ({has_abstract_count / total_records * 100:.1f}%)")

    print("\n[Phân bố theo Nguồn thu thập]")
    for src, cnt in sources_counter.most_common():
        print(f"  - {src}: {cnt} ({cnt / total_records * 100:.1f}%)")

    print("\n[Phân bố theo Bậc đào tạo]")
    for deg, cnt in degrees_counter.most_common():
        print(f"  - {deg}: {cnt} ({cnt / total_records * 100:.1f}%)")

    print("\n[Phân bố theo Năm (5 năm gần nhất có dữ liệu)]")
    sorted_years = sorted(years_counter.items(), key=lambda x: x[0], reverse=True)
    for y, cnt in sorted_years[:5]:
        print(f"  - Năm {y}: {cnt} đề tài")

    print("\n[Top 10 Chủ đề / Từ khóa CNTT phổ biến]")
    for kw, cnt in top_keywords.most_common(10):
        print(f"  - {kw}: {cnt} lần xuất hiện")
    print("=" * 55)

if __name__ == "__main__":
    merge_and_stats()
