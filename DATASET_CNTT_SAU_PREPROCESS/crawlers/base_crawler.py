# -*- coding: utf-8 -*-
"""
base_crawler.py: Lớp cơ sở trang bị cơ chế chống ban IP, xoay vòng User-Agent,
retry với exponential backoff, quản lý checkpoint và ghi file JSONL.
"""
import os
import re
import json
import time
import random
import logging
import threading
from typing import Dict, Any, List, Set, Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%H:%M:%S"
)

# Danh sách User-Agent phổ biến của Desktop Browsers
DESKTOP_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
]

class BaseCrawler:
    def __init__(
        self,
        name: str,
        output_file: str,
        checkpoint_file: str = "json/.checkpoint.json",
        exclude_dir: Optional[str] = r"D:\Documents\NCKH- Đồ án\NCKH về NLP, VLM, Vision\Dataset_khoaluan",
        pdf_dir: Optional[str] = r"D:\Documents\NCKH- Đồ án\NCKH về NLP, VLM, Vision\Dataset_khoaluan",
        min_delay: float = 2.0,
        max_delay: float = 4.5,
        timeout: int = 25
    ):
        self.name = name
        self.output_file = output_file
        self.checkpoint_file = checkpoint_file
        self.exclude_dir = exclude_dir
        self.pdf_dir = pdf_dir
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.timeout = timeout
        self.logger = logging.getLogger(self.name)
        self.external_titles: Set[str] = set()
        self.lock = threading.RLock()
        
        # Đảm bảo thư mục đích tồn tại
        os.makedirs(os.path.dirname(self.output_file), exist_ok=True)
        os.makedirs(os.path.dirname(self.checkpoint_file), exist_ok=True)
        if self.pdf_dir:
            os.makedirs(self.pdf_dir, exist_ok=True)
        
        # Khởi tạo session kèm retry tự động
        self.session = self._create_resilient_session()
        
        # Tải checkpoint các ID đã cào
        self.seen_ids: Set[str] = self._load_checkpoint()

    def _create_resilient_session(self) -> requests.Session:
        """Tạo HTTP Session với retry policy tự động khi server quá tải hoặc 429."""
        session = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 502, 503, 504],
            raise_on_status=False
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=30, pool_maxsize=30)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def get_random_headers(self, extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Tạo headers giả lập trình duyệt với User-Agent ngẫu nhiên và anti-bot flags."""
        headers = {
            "User-Agent": random.choice(DESKTOP_USER_AGENTS),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
            "Connection": "keep-alive",
            "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Referer": "https://thuvien.ou.edu.vn/"
        }
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def jitter_delay(self):
        """Tạo khoảng nghỉ ngẫu nhiên giữa các request để chống bị ban IP."""
        delay = random.uniform(self.min_delay, self.max_delay)
        time.sleep(delay)

    @staticmethod
    def normalize_title_for_dedup(title: str) -> str:
        """Chuẩn hóa tiêu đề: chữ thường, loại bỏ ký tự đặc biệt, khoảng trắng thừa."""
        if not title:
            return ""
        t = title.lower()
        t = re.sub(r"[^\w\s]", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    @staticmethod
    def clean_core_title(title: str) -> str:
        """Bóc tách tiêu đề cốt lõi: loại bỏ tiền tố/hậu tố học vị (Khóa luận, Luận văn, Báo cáo thực tập, tác giả)."""
        if not title:
            return ""
        s = title.strip()
        # Loại bỏ phần tác giả sau dấu gạch chéo
        s = re.sub(r"\s*/\s*.*$", "", s)
        # Loại bỏ hậu tố học vị/loại hình tài liệu sau dấu hai chấm hoặc gạch ngang
        s = re.sub(
            r"\s*[:\-]\s*(?:khóa luận tốt nghiệp|luận văn thạc sĩ|báo cáo tốt nghiệp|báo cáo thực tập|đồ án tốt nghiệp|công trình nghiên cứu|luận án tiến sĩ|báo cáo chuyên đề).*$",
            "",
            s,
            flags=re.IGNORECASE
        )
        # Loại bỏ tiền tố học vị ở đầu câu
        s = re.sub(
            r"^(?:khóa luận tốt nghiệp|luận văn thạc sĩ|đồ án tốt nghiệp|báo cáo tốt nghiệp|báo cáo thực tập|đề tài nghiên cứu khoa học|đề tài nckh|đề tài|nghiên cứu đề tài|tiểu luận)\s*[:\-\./]?\s*",
            "",
            s,
            flags=re.IGNORECASE
        )
        s = s.lower()
        s = re.sub(r"[^\w\s]", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    GENERIC_TITLES = {
        "khoa luan tot nghiep", "do an tot nghiep", "luan van tot nghiep",
        "khoa luan", "do an", "luan van", "kltn", "datn", "lvtn",
        "bao cao tot nghiep", "bao cao khoa luan", "bao cao do an",
        "bao cao thuc tap", "thuc tap tot nghiep", "tieu luan",
        "thesis", "graduation thesis", "capstone project", "final report",
        "report", "bao cao"
    }

    def is_duplicate_title(self, title: str, threshold: float = 0.85) -> bool:
        """Kiểm tra xem tiêu đề có trùng hoặc gần trùng (> 85%) với bất kỳ đề tài nào đã có."""
        if not title:
            return False
        norm = self.normalize_title_for_dedup(title)
        core = self.clean_core_title(title)
        if not norm and not core:
            return False

        # Không kiểm tra trùng đối với các nhãn phân loại tài liệu chung chung
        if norm in self.GENERIC_TITLES or core in self.GENERIC_TITLES:
            return False

        with self.lock:
            # 1. So khớp chính xác O(1) tiêu đề đầy đủ
            if norm and norm not in self.GENERIC_TITLES and (norm in self.all_known_titles or norm in getattr(self, "reserved_titles", set())):
                return True

            # 2. So khớp chính xác O(1) tiêu đề cốt lõi (sau khi bóc tiền tố/hậu tố)
            if core and core not in self.GENERIC_TITLES and (core in self.all_known_core_titles or core in getattr(self, "reserved_core_titles", set())):
                return True

            # Lấy snapshot các token đã tiền xử lý
            core_tokens_snapshot = list(getattr(self, "all_known_core_tokens", []))

        if not core:
            return False

        core_len = len(core)
        core_words = set(core.split())
        import difflib

        for existing, ex_len, ex_words in core_tokens_snapshot:
            if core == existing:
                return True

            max_len = max(core_len, ex_len)
            min_len = min(core_len, ex_len)

            # Substring containment
            if min_len >= 20 and (core in existing or existing in core):
                if (min_len / max_len) >= 0.82:
                    return True

            # Bỏ qua nếu chênh lệch chiều dài > 30%
            if abs(core_len - ex_len) > max_len * 0.3:
                continue

            # Token overlap Jaccard siêu tốc
            if core_words and ex_words:
                inter_len = len(core_words & ex_words)
                union_len = len(core_words | ex_words)
                jaccard = inter_len / union_len if union_len else 0
                if jaccard >= 0.80:
                    return True
                # Chỉ tính SequenceMatcher đắt đỏ nếu Jaccard >= 0.65
                if jaccard >= 0.65:
                    ratio = difflib.SequenceMatcher(None, core, existing).ratio()
                    if ratio >= threshold:
                        return True

        return False

    def _load_checkpoint(self) -> Set[str]:
        """Tải danh sách ID và toàn bộ tiêu đề đã hoàn thành từ checkpoint, các file JSONL và kho PDF."""
        seen: Set[str] = set()
        self.all_known_titles = set()
        self.all_known_title_list = []
        self.all_known_core_titles = set()
        self.all_known_core_title_list = []
        self.reserved_titles = set()
        self.reserved_core_titles = set()
        self.external_authors: Set[str] = set()
        
        # 1. Quét toàn bộ các file JSONL trong thư mục json/ (dataset_cntt_all, ou, ute, vnu, v.v.)
        json_dir = os.path.dirname(self.output_file) or "json"
        if os.path.exists(json_dir):
            for fname in os.listdir(json_dir):
                if fname.endswith(".jsonl"):
                    fpath = os.path.join(json_dir, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            for line in f:
                                line = line.strip()
                                if line:
                                    try:
                                        data = json.loads(line)
                                        rec_id = str(data.get("id"))
                                        if rec_id:
                                            seen.add(rec_id)
                                        t = data.get("title", "")
                                        norm_t = self.normalize_title_for_dedup(t)
                                        core_t = self.clean_core_title(t)
                                        if norm_t:
                                            self.all_known_titles.add(norm_t)
                                            self.all_known_title_list.append(norm_t)
                                        if core_t:
                                            self.all_known_core_titles.add(core_t)
                                            self.all_known_core_title_list.append(core_t)
                                        for a in data.get("authors", []):
                                            if a:
                                                self.external_authors.add(self.normalize_title_for_dedup(a))
                                    except Exception:
                                        pass
                    except Exception as e:
                        self.logger.warning(f"Lỗi khi đọc file {fname}: {e}")

        # 2. Đọc thêm từ checkpoint file
        if os.path.exists(self.checkpoint_file):
            try:
                with open(self.checkpoint_file, "r", encoding="utf-8") as f:
                    cp_data = json.load(f)
                    source_ids = cp_data.get(self.name, [])
                    seen.update([str(x) for x in source_ids])
            except Exception as e:
                self.logger.warning(f"Lỗi đọc file checkpoint: {e}")

        # 3. Nạp danh sách các file PDF từ thư mục đối chiếu Dataset_khoaluan để loại trùng
        if self.exclude_dir and os.path.exists(self.exclude_dir):
            try:
                count = 0
                for f in os.listdir(self.exclude_dir):
                    if f.lower().endswith(".pdf"):
                        name_no_ext = os.path.splitext(f)[0]
                        # Trích xuất RecordID số hoặc UUID
                        m = re.match(r"^(?:ou_|dut_|ute_|vnu_)?(\d+|[a-f0-9\-]+)", f)
                        if m:
                            seen.add(m.group(1))
                            seen.add(f"{self.name}_{m.group(1)}")
                        
                        # Chuẩn hóa tên file làm tiêu đề dự phòng
                        clean_name = self.normalize_title_for_dedup(name_no_ext)
                        core_name = self.clean_core_title(name_no_ext)
                        if clean_name:
                            self.all_known_titles.add(clean_name)
                            self.all_known_title_list.append(clean_name)
                        if core_name:
                            self.all_known_core_titles.add(core_name)
                            self.all_known_core_title_list.append(core_name)
                            
                        # Trích xuất tên tác giả (phần sau dấu gạch dưới nếu có)
                        parts = name_no_ext.split("_", 1)
                        if len(parts) > 1:
                            clean_author = self.normalize_title_for_dedup(parts[1])
                            if clean_author:
                                self.external_authors.add(clean_author)
                        count += 1
                self.logger.info(f"Đã nạp {count} file PDF từ kho [{self.exclude_dir}] để đối chiếu chống trùng.")
            except Exception as e:
                self.logger.warning(f"Lỗi khi nạp thư mục đối chiếu {self.exclude_dir}: {e}")

        # Tiền xử lý danh sách token để so khớp Jaccard siêu tốc
        self.all_known_core_tokens = [(c, len(c), set(c.split())) for c in self.all_known_core_titles]
                
        self.logger.info(
            f"TỔNG HỢP REGISTRY KHỬ TRÙNG: {len(seen):,} ID đề tài, "
            f"{len(self.all_known_titles):,} tiêu đề đầy đủ, "
            f"{len(self.all_known_core_titles):,} tiêu đề cốt lõi duy nhất, "
            f"{len(self.external_authors):,} tác giả sẵn sàng."
        )
        return seen

    def save_checkpoint(self):
        """Ghi trạng thái checkpoint xuống ổ đĩa bằng cơ chế atomic write an toàn."""
        with self.lock:
            try:
                cp_data = {}
                if os.path.exists(self.checkpoint_file):
                    try:
                        with open(self.checkpoint_file, "r", encoding="utf-8") as f:
                            cp_data = json.load(f)
                    except Exception:
                        cp_data = {}
                cp_data[self.name] = list(self.seen_ids)
                tmp_file = f"{self.checkpoint_file}.tmp"
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(cp_data, f, ensure_ascii=False, indent=2)
                
                replaced = False
                for _ in range(5):
                    try:
                        if os.path.exists(tmp_file):
                            os.replace(tmp_file, self.checkpoint_file)
                        replaced = True
                        break
                    except (PermissionError, OSError):
                        time.sleep(0.1)
                
                if not replaced and os.path.exists(tmp_file):
                    try:
                        with open(self.checkpoint_file, "w", encoding="utf-8") as f:
                            json.dump(cp_data, f, ensure_ascii=False, indent=2)
                        os.remove(tmp_file)
                    except Exception:
                        pass
            except Exception as e:
                self.logger.error(f"Lỗi khi lưu checkpoint: {e}")

    def append_record(self, record: Dict[str, Any]):
        """Ghi một bản ghi vào file JSONL lập tức (stream append) và cập nhật registry chống trùng."""
        with self.lock:
            rec_id = str(record.get("id"))
            with open(self.output_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.seen_ids.add(rec_id)
            raw_t = record.get("title", "")
            norm_t = self.normalize_title_for_dedup(raw_t)
            core_t = self.clean_core_title(raw_t)
            if norm_t:
                self.all_known_titles.add(norm_t)
                self.all_known_title_list.append(norm_t)
            if core_t:
                self.all_known_core_titles.add(core_t)
                self.all_known_core_title_list.append(core_t)
                self.all_known_core_tokens.append((core_t, len(core_t), set(core_t.split())))

    def crawl(self, limit: Optional[int] = None) -> int:
        """Hàm trừu tượng, crawler con cần override."""
        raise NotImplementedError
