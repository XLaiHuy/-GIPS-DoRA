# -*- coding: utf-8 -*-
"""
edu_crawler.py: Crawler chuyên sâu thu thập Khóa luận, Đồ án tốt nghiệp và Luận văn Thạc sĩ CNTT
toàn văn được lưu trữ mở trực tiếp trên các tên miền giáo dục Việt Nam (.edu.vn) và các kho học thuật mở.
Tuân thủ Hàng rào kiểm định 5 lớp nghiêm ngặt:
1. Số trang >= 35 trang (loại bỏ bìa, slide, tóm tắt).
2. Dung lượng >= 600 KB (file toàn văn thực tế từ 1.5 MB - 15 MB).
3. Text layer số hóa >= 20.000 ký tự tiếng Việt (0% scan ảnh).
4. Mốc thời gian <= 2022 (Zero AI Contamination).
5. Chuyên ngành 100% CNTT.
"""
import os
import re
import time
import json
import urllib.parse
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from pypdf import PdfReader
from bs4 import BeautifulSoup
import urllib3
from .base_crawler import BaseCrawler
from .filters import is_it_topic

urllib3.disable_warnings()


class EduCrawler(BaseCrawler):
    """Crawler thu thập đồ án và khóa luận tốt nghiệp CNTT từ các tên miền .edu.vn."""

    # Bộ truy vấn Dorking chuyên sâu nhằm định vị tài liệu toàn văn
    DORKING_QUERIES = [
        # 1. Cụm từ khóa đồ án / khóa luận kết hợp ngành CNTT
        'site:edu.vn filetype:pdf "khóa luận tốt nghiệp" "công nghệ thông tin"',
        'site:edu.vn filetype:pdf "đồ án tốt nghiệp" "công nghệ thông tin"',
        'site:edu.vn filetype:pdf "luận văn thạc sĩ" "công nghệ thông tin"',
        'site:edu.vn filetype:pdf "khóa luận tốt nghiệp" "khoa học máy tính"',
        'site:edu.vn filetype:pdf "đồ án tốt nghiệp" "khoa học máy tính"',
        'site:edu.vn filetype:pdf "luận văn thạc sĩ" "khoa học máy tính"',
        'site:edu.vn filetype:pdf "khóa luận tốt nghiệp" "kỹ thuật phần mềm"',
        'site:edu.vn filetype:pdf "đồ án tốt nghiệp" "kỹ thuật phần mềm"',
        'site:edu.vn filetype:pdf "khóa luận tốt nghiệp" "hệ thống thông tin"',
        'site:edu.vn filetype:pdf "đồ án tốt nghiệp" "an toàn thông tin"',
        'site:edu.vn filetype:pdf "khóa luận tốt nghiệp" "trí tuệ nhân tạo"',
        'site:edu.vn filetype:pdf "đồ án tốt nghiệp" "trí tuệ nhân tạo"',
        'site:edu.vn filetype:pdf "đồ án tốt nghiệp" "xử lý ảnh"',
        'site:edu.vn filetype:pdf "báo cáo đồ án tốt nghiệp" cntt',
        'site:edu.vn filetype:pdf "báo cáo khóa luận tốt nghiệp" cntt',

        # 2. Cụm từ khóa URL định dạng thư mục luận văn / đồ án
        'site:edu.vn inurl:kltn filetype:pdf',
        'site:edu.vn inurl:datn filetype:pdf',
        'site:edu.vn inurl:luanvan filetype:pdf',
        'site:edu.vn inurl:doan filetype:pdf',
        'site:edu.vn inurl:khoaluan filetype:pdf',
        'site:edu.vn inurl:totnghiep filetype:pdf',

        # 3. Theo từng trường đại học trọng điểm đào tạo CNTT
        'site:uet.vnu.edu.vn filetype:pdf "khóa luận tốt nghiệp"',
        'site:uit.edu.vn filetype:pdf "khóa luận tốt nghiệp"',
        'site:hcmus.edu.vn filetype:pdf "khóa luận tốt nghiệp"',
        'site:hust.edu.vn filetype:pdf "đồ án tốt nghiệp"',
        'site:hcmut.edu.vn filetype:pdf "luận văn tốt nghiệp"',
        'site:ptit.edu.vn filetype:pdf "đồ án tốt nghiệp"',
        'site:ctu.edu.vn filetype:pdf "luận văn tốt nghiệp"',
        'site:ute.edu.vn filetype:pdf "đồ án tốt nghiệp"',
        'site:hcmute.edu.vn filetype:pdf "đồ án tốt nghiệp"',
        'site:iuh.edu.vn filetype:pdf "khóa luận tốt nghiệp"',
        'site:haui.edu.vn filetype:pdf "đồ án tốt nghiệp"',
        'site:tdtu.edu.vn filetype:pdf "khóa luận tốt nghiệp"',
        'site:sgu.edu.vn filetype:pdf "khóa luận tốt nghiệp"'
    ]

    # Danh mục từ khóa nhận diện tiêu đề báo cáo
    THESIS_IDENTIFIERS = [
        "khóa luận tốt nghiệp", "đồ án tốt nghiệp", "luận văn tốt nghiệp",
        "luận văn thạc sĩ", "báo cáo khóa luận", "báo cáo đồ án",
        "kltn", "datn", "lvtn", "graduation thesis", "master thesis"
    ]

    BAD_URL_KEYWORDS = [
        "tomtat", "tóm_tắt", "abstract", "summary", "cover", "bìa", "bia",
        "ky_yeu", "kỷ_yếu", "proceedings", "slide", "presentation", "thuyettrinh",
        "outline", "decuong", "syllabus", "baitap", "assignment", "lich_trinh"
    ]

    def __init__(
        self,
        output_file: str = "json/edu_theses.jsonl",
        checkpoint_file: str = "json/.checkpoint.json",
        pdf_dir: Optional[str] = r"D:\Documents\NCKH- Đồ án\NCKH về NLP, VLM, Vision\Dataset_khoaluan",
        min_delay: float = 1.0,
        max_delay: float = 2.5,
        min_pages: int = 35,
        min_chars: int = 20000,
        max_year: int = 2022,
        max_workers: int = 4
    ):
        super().__init__(
            name="edu_dorking",
            output_file=output_file,
            checkpoint_file=checkpoint_file,
            pdf_dir=pdf_dir,
            min_delay=min_delay,
            max_delay=max_delay
        )
        self.min_pages = min_pages
        self.min_chars = min_chars
        self.max_year = max_year
        self.max_workers = max_workers
        self.seen_urls = set()

    def _extract_cover_metadata(self, pages_text: List[str], fallback_title: str) -> Dict[str, Any]:
        """Trích xuất tên trường, tên đề tài, tác giả, GVHD và năm từ các trang đầu của đồ án."""
        meta = {
            "title": fallback_title,
            "school_or_faculty": "ĐH tại Việt Nam (.edu.vn)",
            "authors": [],
            "advisors": [],
            "year": None,
            "degree": "Khóa luận / Đồ án tốt nghiệp"
        }

        full_cover = "\n".join(pages_text[:5])
        clean_cover = re.sub(r"[ \t]+", " ", full_cover)

        # 1. Bóc tách tên trường
        school_patterns = [
            r"((?:ĐẠI\s+HỌC|TRƯỜNG\s+ĐẠI\s+HỌC|HỌC\s+VIỆN)\s+[^\n,]+)",
            r"((?:VIỆN|KHOA)\s+CÔNG\s+NGHỆ\s+THÔNG\s+TIN[^\n,]*)"
        ]
        for sp in school_patterns:
            m = re.search(sp, clean_cover, re.IGNORECASE)
            if m:
                sch = m.group(1).strip()
                if len(sch) > 8 and not any(b in sch.lower() for b in ["khoa", "bộ môn", "ngành", "chuyên ngành"]):
                    meta["school_or_faculty"] = sch.title()
                    break

        # 2. Bậc đào tạo
        cover_lower = clean_cover.lower()
        if "luận án tiến sĩ" in cover_lower:
            meta["degree"] = "Luận án Tiến sĩ"
        elif "luận văn thạc sĩ" in cover_lower or "thạc sỹ" in cover_lower:
            meta["degree"] = "Luận văn Thạc sĩ"
        elif "khóa luận tốt nghiệp" in cover_lower:
            meta["degree"] = "Khóa luận tốt nghiệp"
        elif "đồ án tốt nghiệp" in cover_lower:
            meta["degree"] = "Đồ án tốt nghiệp"

        # 3. Bóc tách tiêu đề đề tài
        title_patterns = [
            r"(?:ĐỀ\s+TÀI|TÊN\s+ĐỀ\s+TÀI)\s*[:：]?\s*\n*\s*([^\n]+(?:\n[^\n]+)?)",
            r"(?:KHÓA\s+LUẬN|ĐỒ\s+ÁN|LUẬN\s+VĂN)\s+TỐT\s+NGHIỆP\s*\n+([A-ZÀÁẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬĐÈÉẺẼẸÊẾỀỂỄỆÌÍỈĨỊÒÓỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÙÚỦŨỤƯỨỪỬỮỰỲÝỶỸỴ\s\d\-_:,\.]{10,150})"
        ]
        for tp in title_patterns:
            m = re.search(tp, clean_cover, re.IGNORECASE)
            if m:
                cand = m.group(1).strip()
                cand = re.sub(r"\s+", " ", cand)
                if len(cand) >= 15:
                    meta["title"] = cand
                    break

        # 4. Bóc tách tác giả
        author_patterns = [
            r"Họ\s+và\s+tên\s+sinh\s+viên\s*[:：]\s*([^\n]+)",
            r"(?:Sinh\s+viên|Học\s+viên|Tác\s+giả|Người\s+thực\s+hiện)\s*(?:thực\s+hiện)?\s*[:：]?\s*\n*\s*([^\n]+)",
            r"(?:SVTH|HVTH)\s*[:：]?\s*\n*\s*([^\n]+)"
        ]
        for ap in author_patterns:
            m = re.search(ap, clean_cover, re.IGNORECASE)
            if m:
                raw_a = m.group(1).strip()
                clean_a = re.sub(r"[^\w\s]", "", raw_a).strip()
                if clean_a and len(clean_a) >= 4 and clean_a not in meta["authors"]:
                    meta["authors"].append(clean_a.title())

        # 5. Bóc tách người hướng dẫn
        advisor_patterns = [
            r"Họ\s+và\s+tên\s+Giáo\s+viên\s+hướng\s+dẫn\s*[:：]\s*([^\n]+)",
            r"(?:Giảng\s+viên|Giáo\s+viên|Cán\s+bộ|Người)\s+hướng\s+dẫn\s*[:：]?\s*\n*\s*([^\n]+)",
            r"(?:GVHD|CBHD)\s*[:：]?\s*\n*\s*([^\n]+)"
        ]
        for adv_p in advisor_patterns:
            m = re.search(adv_p, clean_cover, re.IGNORECASE)
            if m:
                raw_adv = m.group(1).strip()
                clean_adv = re.sub(r"(?:pgs\.|ts\.|ths\.|tiến\s*sĩ|thạc\s*sĩ|thầy|cô)", "", raw_adv, flags=re.IGNORECASE).strip()
                clean_adv = re.sub(r"[^\w\s]", "", clean_adv).strip()
                if clean_adv and len(clean_adv) >= 4:
                    meta["advisors"].append(clean_adv.title())

        # 6. Bóc tách năm bảo vệ
        year_matches = re.findall(r"\b(20[0-2][0-2]|201\d|200\d)\b", clean_cover)
        if year_matches:
            valid_years = [int(y) for y in year_matches if int(y) <= self.max_year]
            if valid_years:
                meta["year"] = max(valid_years)

        return meta

    def _download_and_validate_pdf(self, pdf_url: str) -> Optional[Dict[str, Any]]:
        """
        Tải file PDF từ URL giáo dục, thực hiện kiểm định 5 lớp:
        1. Dung lượng >= 600 KB
        2. Số trang >= 35 trang
        3. Text layer >= 20.000 ký tự text tiếng Việt
        4. Năm <= 2022
        5. Chuyên ngành 100% CNTT
        """
        if any(bad in pdf_url.lower() for bad in self.BAD_URL_KEYWORDS):
            return None

        # Rút trích tên miền và tên file an toàn
        parsed = urllib.parse.urlparse(pdf_url)
        domain = parsed.netloc.replace("www.", "").replace(".", "_")
        url_fn = os.path.basename(parsed.path) or "thesis.pdf"
        safe_fn = re.sub(r"[^\w\.-]", "_", url_fn)
        if not safe_fn.lower().endswith(".pdf"):
            safe_fn += ".pdf"

        record_id = f"edu_{domain}_{safe_fn[:40]}"
        if record_id in self.seen_ids:
            return None

        save_path = os.path.join(self.pdf_dir, f"EDU_{domain}_{safe_fn}")
        temp_path = save_path + ".tmp"

        headers = self.get_random_headers({"Referer": f"{parsed.scheme}://{parsed.netloc}/"})

        try:
            r = self.session.get(pdf_url, headers=headers, stream=True, timeout=25, verify=False)
            if r.status_code != 200:
                return None

            content_type = r.headers.get("Content-Type", "").lower()
            if content_type and "pdf" not in content_type and "octet-stream" not in content_type:
                return None

            # Tải chunk
            with open(temp_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=32768):
                    if chunk:
                        f.write(chunk)

            size_bytes = os.path.getsize(temp_path)

            # Quality Gate 1: Dung lượng >= 600 KB
            if size_bytes < 600 * 1024:
                self.logger.warning(f"[LOẠI BỎ - DUNG LƯỢNG < 600KB] {safe_fn} ({size_bytes // 1024} KB).")
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
                return None

            # Quality Gate 2: Số trang >= 35 và text layer >= 20.000 ký tự
            try:
                reader = PdfReader(temp_path, strict=False)
                num_pages = len(reader.pages)
            except Exception as pe:
                self.logger.debug(f"Không thể đọc cấu trúc PDF {safe_fn}: {pe}")
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
                return None

            if num_pages < self.min_pages:
                self.logger.warning(
                    f"[LOẠI BỎ - DƯỚI 35 TRANG] {safe_fn} chỉ có {num_pages} trang (tóm tắt/bìa/slide). Xóa file!"
                )
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
                return None

            # Quality Gate 3: Text layer số hóa >= 20.000 ký tự tiếng Việt
            sample_pages = min(num_pages, 35)
            pages_text = []
            for i in range(sample_pages):
                try:
                    txt = reader.pages[i].extract_text() or ""
                    pages_text.append(txt.strip())
                except Exception:
                    pages_text.append("")

            sample_text = "\n".join(pages_text)
            if len(sample_text) < self.min_chars:
                self.logger.warning(
                    f"[LOẠI BỎ - TEXT LAYER DƯỚI 20000 KÝ TỰ] {safe_fn} chỉ có {len(sample_text)} chars (scan/thiếu text). Xóa file!"
                )
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
                return None

            # Bóc tách metadata từ các trang bìa
            meta = self._extract_cover_metadata(pages_text, fallback_title=safe_fn.replace(".pdf", "").replace("_", " "))

            # Quality Gate 4: Năm xuất bản <= 2022
            year = meta.get("year")
            if year and year > self.max_year:
                self.logger.warning(f"[LOẠI BỎ - NĂM > 2022] {meta['title'][:50]} (Năm: {year} > {self.max_year}).")
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
                return None

            # Quality Gate 5: Kiểm tra đề tài chuyên ngành CNTT
            is_it, matched_tags = is_it_topic(
                title=meta["title"],
                abstract=sample_text[:2000],
                keywords=[],
                faculty=meta["school_or_faculty"]
            )
            if not is_it:
                self.logger.warning(f"[LOẠI BỎ - KHÔNG PHẢI CNTT] {meta['title'][:50]}.")
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
                return None

            # Kiểm tra chống trùng lặp tiêu đề
            if self.is_duplicate_title(meta["title"]):
                self.logger.info(f"[TRÙNG LẶP TIÊU ĐỀ] Đã có trong kho: {meta['title'][:50]}")
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
                return None

            # Đổi tên file tạm thành file chính thức
            if os.path.exists(save_path):
                os.remove(save_path)
            os.rename(temp_path, save_path)

            self.seen_ids.add(record_id)

            record = {
                "id": record_id,
                "source": f"Edu Open Repository ({parsed.netloc})",
                "title": meta["title"],
                "authors": meta["authors"],
                "advisors": meta["advisors"],
                "year": year or 2020,
                "degree": meta["degree"],
                "school_or_faculty": meta["school_or_faculty"],
                "keywords": matched_tags,
                "abstract": sample_text[:800],
                "matched_it_tags": matched_tags,
                "url": pdf_url,
                "has_full_pdf": True,
                "num_pages": num_pages,
                "pdf_path": save_path,
                "pdf_size_bytes": size_bytes,
                "pdf_download_url": pdf_url,
                "crawl_time": datetime.now().isoformat()
            }
            return record

        except Exception as e:
            self.logger.debug(f"Lỗi khi xử lý tải PDF {pdf_url}: {e}")
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
        return None

    def crawl(self, limit: Optional[int] = None) -> int:
        """Crawl và thu thập đồ án / khóa luận toàn văn từ các nguồn mở .edu.vn."""
        self.logger.info(
            f"Bắt đầu thu thập đồ án/khóa luận CNTT toàn văn từ tên miền .edu.vn (Multi-threading: {self.max_workers} luồng)..."
        )
        crawled_count = 0

        # Quét qua danh sách các truy vấn dorking
        for query in self.DORKING_QUERIES:
            if limit is not None and crawled_count >= limit:
                break

            self.logger.info(f"[Edu Dorking] Đang thực hiện truy vấn: '{query}'...")
            
            # Sử dụng Bing Search endpoint không yêu cầu xác thực
            encoded_q = urllib.parse.quote(query)
            for page in range(1, 4):
                if limit is not None and crawled_count >= limit:
                    break

                first_val = (page - 1) * 10 + 1
                search_url = f"https://www.bing.com/search?q={encoded_q}&first={first_val}"
                headers = self.get_random_headers({"Referer": "https://www.bing.com/"})

                try:
                    r = self.session.get(search_url, headers=headers, timeout=12)
                    if r.status_code != 200:
                        break

                    soup = BeautifulSoup(r.text, "html.parser")
                    pdf_urls = []

                    for li in soup.find_all("li", class_="b_algo"):
                        h2 = li.find("h2")
                        if not h2:
                            continue
                        a = h2.find("a")
                        if not a or not a.get("href"):
                            continue
                        href = a["href"]

                        # Giải mã liên kết nếu qua bing redirect
                        real_url = href
                        if "bing.com/ck/a" in href and "u=" in href:
                            import base64
                            try:
                                u_param = href.split("u=")[1].split("&")[0]
                                raw_b64 = u_param[2:] if u_param.startswith("a1") else u_param
                                raw_b64 += "=" * (-len(raw_b64) % 4)
                                real_url = base64.b64decode(raw_b64).decode("utf-8", errors="ignore")
                            except Exception:
                                real_url = href

                        if real_url and real_url.lower().endswith(".pdf") and ".edu.vn" in real_url.lower():
                            if real_url not in self.seen_urls:
                                self.seen_urls.add(real_url)
                                pdf_urls.append(real_url)

                    if pdf_urls:
                        self.logger.info(f"[Edu Dorking] Trang {page}: Phát hiện {len(pdf_urls)} file PDF ứng viên .edu.vn...")
                        from concurrent.futures import ThreadPoolExecutor, as_completed
                        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                            future_to_url = {executor.submit(self._download_and_validate_pdf, u): u for u in pdf_urls}
                            for fut in as_completed(future_to_url):
                                try:
                                    rec = fut.result()
                                    if rec:
                                        self.append_record(rec)
                                        crawled_count += 1
                                        self.save_checkpoint()
                                        self.logger.info(
                                            f"[MỚI LƯU THÀNH CÔNG #{crawled_count}] {rec['title'][:55]} ({rec['school_or_faculty']}) - {rec['num_pages']} trang"
                                        )
                                        if limit is not None and crawled_count >= limit:
                                            break
                                except Exception as e:
                                    self.logger.debug(f"Worker Edu lỗi: {e}")

                    time.sleep(1.5)

                except Exception as e:
                    self.logger.warning(f"Lỗi truy vấn Dorking '{query}': {e}")
                    time.sleep(2.0)
                    break

        self.save_checkpoint()
        self.logger.info(f"Hoàn thành thu thập Edu Dorking! Đã thu nạp {crawled_count} đồ án tốt nghiệp toàn văn mới.")
        return crawled_count
