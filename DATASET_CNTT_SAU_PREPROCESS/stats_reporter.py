# -*- coding: utf-8 -*-
"""
stats_reporter.py: Thống kê chi tiết số lượng đồ án theo từng trường đại học
và so sánh số lượng mới cào thêm được so với mốc trước.
"""
import os
import sys
import json
import re
from collections import Counter
from typing import Dict, Tuple

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

def normalize_school_name(school: str) -> str:
    if not school:
        return "Khác / Đang phân loại"
    s = school.lower()
    if "bách khoa hà nội" in s or "hust" in s:
        return "ĐH Bách Khoa Hà Nội (HUST)"
    if "bách khoa" in s or "bku" in s or "hcmut" in s or "b`ch khoa" in s:
        return "ĐH Bách Khoa TP.HCM (HCMUT)"
    if "công nghệ thông tin" in s or "uit" in s:
        return "ĐH Công Nghệ Thông Tin - ĐHQG-HCM (UIT)"
    if "khoa học tự nhiên" in s or "hcmus" in s:
        return "ĐH Khoa Học Tự Nhiên - ĐHQG-HCM (HCMUS)"
    if "đại học công nghệ" in s or "uet" in s or ("công nghệ" in s and ("hà nội" in s or "quốc gia" in s)):
        return "ĐH Công Nghệ - ĐHQG Hà Nội (VNU-UET)"
    if "bưu chính" in s or "ptit" in s:
        return "Học viện CNBCVT (PTIT)"
    if "sư phạm kỹ thuật" in s or "ute" in s:
        return "ĐH Sư phạm Kỹ thuật TP.HCM (HCMUTE)"
    if "cần thơ" in s or "ctu" in s:
        return "ĐH Cần Thơ (CTU)"
    if "sư phạm hà nội" in s or "hnue" in s:
        return "ĐH Sư phạm Hà Nội (HNUE)"
    if "giao thông" in s or "utc" in s:
        return "ĐH Giao thông Vận tải (UTC)"
    if "công nghiệp" in s or "iuh" in s or "haui" in s:
        return "ĐH Công Nghiệp (IUH / HaUI)"
    if "an giang" in s:
        return "ĐH An Giang (AGU)"
    if "sài gòn" in s:
        return "ĐH Sài Gòn (SGU)"
    if "thủy lợi" in s:
        return "ĐH Thủy Lợi (TLU)"
    if "xây dựng" in s:
        return "ĐH Xây Dựng (NUCE)"
    if "quốc gia tp. hồ chí minh" in s:
        return "ĐHQG TP. Hồ Chí Minh"
    if "quốc gia hà nội" in s:
        return "ĐHQG Hà Nội"
    if "mở" in s or "ou" in s:
        return "ĐH Mở TP.HCM (OU)"
    return "ĐH khác tại Việt Nam"

def get_stats() -> Dict:
    pdf_dir = r"D:\Documents\NCKH- Đồ án\NCKH về NLP, VLM, Vision\Dataset_khoaluan"
    total_pdfs = len([f for f in os.listdir(pdf_dir) if f.lower().endswith(".pdf")]) if os.path.exists(pdf_dir) else 0

    # Đọc từ file master nếu có, hoặc tổng hợp từ các file con
    all_theses = []
    seen_ids = set()
    master_path = "json/dataset_cntt_all.jsonl"
    sources_count = {}
    
    source_files = {
        "GitHub": "json/github_theses.jsonl",
        "VNU DSpace (ĐHQGHN)": "json/vnu_it_theses.jsonl",
        "HCMUTE (ĐH SPKT TP.HCM)": "json/ute_it_theses.jsonl",
        "OU (ĐH Mở TP.HCM)": "json/ou_it_theses.jsonl",
        "Edu Open Repositories (.edu.vn)": "json/edu_theses.jsonl"
    }


    for src_name, src_path in source_files.items():
        cnt = 0
        if os.path.exists(src_path):
            with open(src_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            item = json.loads(line)
                            cnt += 1
                            item_id = item.get("id") or item.get("title")
                            if item_id and item_id not in seen_ids:
                                seen_ids.add(item_id)
                                all_theses.append(item)
                        except Exception:
                            pass
        sources_count[src_name] = cnt

    school_counts = Counter()
    for t in all_theses:
        raw_s = t.get("school_or_faculty") or t.get("institution") or ""
        norm_s = normalize_school_name(raw_s)
        school_counts[norm_s] += 1

    state_file = "json/.last_stats_count.json"
    session_start_pdfs = 336
    prev_pdfs = total_pdfs

    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                last_data = json.load(f)
                prev_pdfs = last_data.get("last_10m_pdfs", last_data.get("total_pdfs", total_pdfs))
                session_start_pdfs = last_data.get("session_start_pdfs", 336)
        except Exception:
            pass

    delta_10m = total_pdfs - prev_pdfs
    delta_session = total_pdfs - session_start_pdfs

    try:
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump({
                "last_10m_pdfs": total_pdfs,
                "session_start_pdfs": session_start_pdfs,
                "total_records": len(all_theses)
            }, f, indent=2)
    except Exception:
        pass

    return {
        "total_records": len(all_theses),
        "total_pdfs": total_pdfs,
        "delta_10m": delta_10m,
        "delta_session": delta_session,
        "session_start_pdfs": session_start_pdfs,
        "sources_count": sources_count,
        "school_counts": dict(school_counts.most_common()),
        "recent_theses": [
            {
                "title": t.get("title", ""),
                "school": normalize_school_name(t.get("school_or_faculty", "")),
                "pages": t.get("num_pages") or t.get("pages", 0),
                "year": t.get("year", ""),
                "source": t.get("source", "")[:30]
            }
            for t in all_theses[-5:]
        ]
    }

if __name__ == "__main__":
    stats = get_stats()
    print(json.dumps(stats, ensure_ascii=False, indent=2))

