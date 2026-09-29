# -*- coding: utf-8 -*-
"""
dut_crawler.py: Crawler thu thập đồ án, khóa luận, luận văn CNTT từ kho học liệu số
Đại học Bách Khoa - Đại học Đà Nẵng (DUT DSpace-CRIS - http://thuvienso.dut.udn.vn).
Đảm bảo 100% digital text PDF, chuyên ngành CNTT, xuất bản <= 2022, >= 15 trang,
khử trùng đa tầng sớm trước khi tải.
"""
import os
import re
import io
import urllib.parse
from datetime import datetime
from typing import Optional, Dict, Any, List
import requests
import bs4
import urllib3
from .base_crawler import BaseCrawler
from .filters import is_it_topic

urllib3.disable_warnings()

class DUTCrawler(BaseCrawler):
    BASE_URL = "http://thuvienso.dut.udn.vn"

    # Danh sách các bộ sưu tập chuyên ngành CNTT tại DUT
    IT_COLLECTIONS = [
        ("Khoa CNTT - An toàn thông tin", 9784),
        ("Khoa CNTT - Công nghệ phần mềm", 9785),
        ("Khoa CNTT - CNTT Việt - Nhật", 9786),
        ("Khoa CNTT - Hệ thống thông tin", 9787),
        ("Khoa CNTT - Khoa học dữ liệu & AI", 9788),
        ("Khoa CNTT - Kỹ thuật máy tính", 24652),
        ("Khoa CNTT - Mạng và Truyền thông", 9789),
        ("Khoa KHCNTT - Công nghệ phần mềm", 9808),
        ("Khoa KHCNTT - Hệ thống nhúng", 9810),
        ("Khoa KHCNTT - Tin học Công nghiệp", 9812),
        ("DUT - Luận văn thạc sĩ", 9741),
        ("DUT - Luận án tiến sĩ", 9742)
    ]

    SEARCH_QUERIES = [
        "công nghệ thông tin",
        "khoa học máy tính",
        "kỹ thuật phần mềm",
        "hệ thống thông tin",
        "trí tuệ nhân tạo",
        "khoa học dữ liệu",
        "học máy",
        "thị giác máy tính",
        "xử lý ảnh",
        "an toàn thông tin",
        "mạng máy tính",
        "cơ sở dữ liệu",
        "phần mềm",
        "ứng dụng di động"
    ]

    def __init__(
        self,
        output_file: str = "json/dut_it_theses.jsonl",
        checkpoint_file: str = "json/.checkpoint.json",
        pdf_dir: Optional[str] = r"D:\Documents\NCKH- Đồ án\NCKH về NLP, VLM, Vision\Dataset_khoaluan",
        min_delay: float = 1.0,
        max_delay: float = 2.2,
        download_pdf: bool = True,
        only_pdf: bool = True,
        max_year: Optional[int] = 2022
    ):
        super().__init__(
            name="dut_dspace",
            output_file=output_file,
            checkpoint_file=checkpoint_file,
            pdf_dir=pdf_dir,
            min_delay=min_delay,
            max_delay=max_delay
        )
        self.download_pdf = download_pdf
        self.only_pdf = only_pdf
        self.max_year = max_year

    def _extract_year(self, date_str: str) -> Optional[int]:
        """Trích xuất năm từ chuỗi ngày tháng."""
        if not date_str:
            return None
        m = re.search(r"\b(19\d{2}|20\d{2})\b", date_str)
        return int(m.group(1)) if m else None

    def _clean_title(self, raw_title: str) -> str:
        if not raw_title:
            return ""
        t = raw_title.strip()
        t = re.sub(r"\s+", " ", t)
        return t

    def _download_pdf(self, bs_href: str, record_id: str, title: str) -> Optional[Dict[str, Any]]:
        """Tải file PDF từ bitstream của DUT và kiểm tra Quality Gate."""
        full_bs_url = urllib.parse.urljoin(self.BASE_URL, bs_href)
        safe_title = re.sub(r"[^\w\.-]", "_", title)[:60]
        pdf_filename = f"{record_id}_{safe_title}.pdf"
        save_path = os.path.join(self.pdf_dir, pdf_filename)

        # 1. Nếu đã tồn tại file hợp lệ trên đĩa
        if os.path.exists(save_path) and os.path.getsize(save_path) > 5000:
            size = os.path.getsize(save_path)
            try:
                from pypdf import PdfReader
                reader = PdfReader(save_path, strict=False)
                num_pages = len(reader.pages)
                if num_pages >= 15:
                    sample_pages = min(num_pages, 10)
                    sample_text = "".join((reader.pages[i].extract_text() or "").strip() for i in range(sample_pages))
                    if len(sample_text) >= 3000:
                        self.logger.info(f"[ĐÃ TỒN TẠI & QUALITY OK] {pdf_filename} ({size:,} bytes, {num_pages} trang)")
                        return {
                            "pdf_path": save_path,
                            "pdf_filename": pdf_filename,
                            "pdf_size_bytes": size,
                            "pdf_download_url": full_bs_url,
                            "pages": num_pages
                        }
            except Exception:
                pass

        # 2. Tải trực tiếp stream
        headers = self.get_random_headers({"Referer": self.BASE_URL})
        try:
            r = self.session.get(full_bs_url, headers=headers, stream=True, allow_redirects=True, timeout=40)
            if r.status_code == 200:
                ct = r.headers.get("Content-Type", "").lower()
                if "text/html" in ct:
                    self.logger.warning(f"[BỊ KHÓA / CHUYỂN HƯỚNG ĐĂNG NHẬP] {pdf_filename} bị khóa sau trang đăng nhập trường. Bỏ qua!")
                    return None

                content_iter = r.iter_content(chunk_size=32768)
                try:
                    first_chunk = next(content_iter)
                except StopIteration:
                    first_chunk = b""

                if not first_chunk.startswith(b"%PDF"):
                    self.logger.warning(f"[BỊ KHÓA / KHÔNG PHẢI PDF] {pdf_filename} không có định dạng PDF chuẩn. Bỏ qua!")
                    return None

                with open(save_path, "wb") as f:
                    f.write(first_chunk)
                    for chunk in content_iter:
                        if chunk:
                            f.write(chunk)

                size = os.path.getsize(save_path)
                if size < 600 * 1024:
                    self.logger.warning(f"[LOẠI BỎ - FILE NHỎ < 600KB] {pdf_filename} ({size//1024} KB). Xóa file!")
                    try:
                        os.remove(save_path)
                    except Exception:
                        pass
                    return None

                # Kiểm tra Quality Gate: Số trang >= 35 và Text Layer >= 20.000 ký tự
                try:
                    from pypdf import PdfReader
                    reader = PdfReader(save_path, strict=False)
                    num_pages = len(reader.pages)
                    if num_pages < 35:
                        self.logger.warning(
                            f"[LOẠI BỎ - DƯỚI 35 TRANG] {pdf_filename} chỉ có {num_pages} trang (tóm tắt/flyer/slide). Xóa file!"
                        )
                        try:
                            os.remove(save_path)
                        except Exception:
                            pass
                        return None

                    sample_pages = min(num_pages, 35)
                    sample_text = "".join((reader.pages[i].extract_text() or "").strip() for i in range(sample_pages))
                    if len(sample_text) < 20000:
                        self.logger.warning(
                            f"[LOẠI BỎ - TEXT LAYER QUÁ ÍT / SCAN] {pdf_filename} chỉ trích xuất được {len(sample_text)} ký tự. Xóa file!"
                        )
                        try:
                            os.remove(save_path)
                        except Exception:
                            pass
                        return None

                    self.logger.info(
                        f"[ĐÃ TẢI FULL PDF DUT CHUẨN] {pdf_filename} ({size:,} bytes, {num_pages} trang, text {len(sample_text):,} chars) -> {save_path}"
                    )
                    return {
                        "pdf_path": save_path,
                        "pdf_filename": pdf_filename,
                        "pdf_size_bytes": size,
                        "pdf_download_url": full_bs_url,
                        "pages": num_pages
                    }
                except Exception as e:
                    self.logger.warning(f"Lỗi kiểm tra Quality Gate PDF DUT: {e}")
                    if os.path.exists(save_path):
                        try:
                            os.remove(save_path)
                        except Exception:
                            pass
                    return None
        except Exception as e:
            self.logger.debug(f"Lỗi tải PDF từ {full_bs_url}: {e}")
            if os.path.exists(save_path):
                try:
                    os.remove(save_path)
                except Exception:
                    pass
        return None

    def _parse_item_page(self, item_url: str, initial_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Truy cập trang chi tiết để bóc tách tóm tắt, người hướng dẫn và PDF bitstream."""
        headers = self.get_random_headers({"Referer": self.BASE_URL})
        try:
            r = self.session.get(item_url, headers=headers, timeout=12)
            if r.status_code != 200:
                return None
            soup = bs4.BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            self.logger.debug(f"Lỗi truy cập trang chi tiết {item_url}: {e}")
            return None

        # Trích xuất metadata từ bảng hoặc item-summary-view-metadata
        abstract = ""
        advisors = []
        subjects = []
        faculty = "Trường Đại học Bách Khoa - Đại học Đà Nẵng"

        # Tìm các khối metadata
        for div in soup.find_all("div", class_="item-summary-view-metadata"):
            text = div.get_text(separator="\n", strip=True)
            lines = text.split("\n")
            for i, line in enumerate(lines):
                line_low = line.lower()
                if "tóm tắt" in line_low or "abstract" in line_low:
                    if i + 1 < len(lines):
                        abstract = lines[i + 1].strip()
                elif "hướng dẫn" in line_low or "advisor" in line_low:
                    if i + 1 < len(lines):
                        adv = lines[i + 1].strip()
                        if adv and len(adv) > 2:
                            advisors.append(adv)
                elif "từ khóa" in line_low or "subject" in line_low:
                    if i + 1 < len(lines):
                        subjects.extend([s.strip() for s in lines[i + 1].split(";") if s.strip()])

        # Kiểm tra bảng chi tiết (nếu có)
        for tr in soup.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) >= 2:
                lbl = tds[0].text.strip().lower()
                val = tds[1].text.strip()
                if "advisor" in lbl or "hướng dẫn" in lbl:
                    for adv in val.split(";"):
                        adv_c = adv.strip()
                        if adv_c and adv_c not in advisors:
                            advisors.append(adv_c)
                elif "abstract" in lbl or "tóm tắt" in lbl:
                    if not abstract:
                        abstract = val
                elif "subject" in lbl or "từ khóa" in lbl:
                    for s in val.split(";"):
                        s_c = s.strip()
                        if s_c and s_c not in subjects:
                            subjects.append(s_c)

        # Lọc đề tài CNTT
        title = initial_data.get("title", "")
        is_it, matched_tags = is_it_topic(
            title=title,
            abstract=abstract,
            keywords=subjects,
            faculty=faculty
        )
        if not is_it:
            return None

        # Tìm bitstream PDF toàn văn
        bitstream_href = None
        for a in soup.find_all("a"):
            href = a.get("href", "")
            if "/bitstream/" in href and href.lower().endswith(".pdf"):
                # Ưu tiên các file không phải bìa, tóm tắt
                name_low = href.lower()
                if not any(k in name_low for k in ["cover", "bia", "tomtat", "abstract"]):
                    bitstream_href = href
                    break
                elif not bitstream_href:
                    bitstream_href = href

        pdf_info = None
        if bitstream_href and self.download_pdf:
            pdf_info = self._download_pdf(bitstream_href, initial_data["id"], title)

        if self.only_pdf and not pdf_info:
            return None

        record = {
            "id": initial_data["id"],
            "source": "Thư viện số ĐH Bách Khoa - ĐH Đà Nẵng (DUT)",
            "title": title,
            "authors": initial_data.get("authors", []),
            "advisors": advisors,
            "year": initial_data.get("year"),
            "degree": initial_data.get("degree", "Đồ án tốt nghiệp"),
            "school_or_faculty": faculty,
            "keywords": subjects,
            "abstract": abstract,
            "matched_it_tags": matched_tags,
            "url": item_url,
            "has_full_pdf": pdf_info is not None,
            "pdf_path": pdf_info["pdf_path"] if pdf_info else None,
            "pdf_size_bytes": pdf_info["pdf_size_bytes"] if pdf_info else None,
            "pdf_download_url": pdf_info["pdf_download_url"] if pdf_info else None,
            "pdf_pages": pdf_info.get("pages") if pdf_info else None,
            "crawl_time": datetime.now().isoformat()
        }
        return record

    def crawl(self, limit: Optional[int] = None) -> int:
        """Thu thập đồ án, khóa luận, luận văn CNTT từ các bộ sưu tập và tìm kiếm trên DUT."""
        self.logger.info("=== BẮT ĐẦU CÀO DATASET TỪ ĐẠI HỌC BÁCH KHOA ĐÀ NẴNG (DUT) ===")
        self.logger.info(f"[*] Thư mục đích: {self.pdf_dir}")
        self.logger.info(f"[*] Năm tối đa: <= {self.max_year}")
        self.logger.info(f"[*] Tiêu chuẩn: >= 15 trang, >= 3.000 ký tự text layer, 0% scan")

        total_crawled = 0

        # 1. Quét theo các Collection chuyên ngành CNTT
        for col_name, col_id in self.IT_COLLECTIONS:
            if limit and total_crawled >= limit:
                break

            self.logger.info(f"\n--- Đang quét Collection: [{col_name}] (ID: {col_id}) ---")
            offset = 0
            empty_pages_in_a_row = 0

            while True:
                if limit and total_crawled >= limit:
                    break

                col_url = f"{self.BASE_URL}/handle/DUT/{col_id}?offset={offset}"
                headers = self.get_random_headers({"Referer": self.BASE_URL})
                try:
                    r = self.session.get(col_url, headers=headers, timeout=12)
                    if r.status_code != 200:
                        break
                    soup = bs4.BeautifulSoup(r.text, "html.parser")
                except Exception as e:
                    self.logger.warning(f"Lỗi tải collection {col_url}: {e}")
                    break

                table = soup.find("table", class_="table")
                if not table:
                    break

                rows = table.find_all("tr")[1:]
                if not rows:
                    empty_pages_in_a_row += 1
                    if empty_pages_in_a_row >= 2:
                        break
                    offset += 20
                    continue

                empty_pages_in_a_row = 0
                self.logger.info(f"Offset {offset}: Tìm thấy {len(rows)} tài liệu")

                for row in rows:
                    if limit and total_crawled >= limit:
                        break

                    tds = row.find_all("td")
                    if len(tds) < 2:
                        continue

                    date_str = tds[0].text.strip()
                    year = self._extract_year(date_str)
                    if self.max_year and year and year > self.max_year:
                        continue

                    a_title = tds[1].find("a")
                    if not a_title or not a_title.get("href"):
                        continue

                    raw_title = a_title.text.strip()
                    title = self._clean_title(raw_title)
                    href = a_title.get("href")

                    # Trích xuất item id
                    m_id = re.search(r"/handle/DUT/(\d+)", href)
                    if not m_id:
                        continue
                    item_num = m_id.group(1)
                    record_id = f"dut_{item_num}"

                    # 1. Kiểm tra ID đã cào
                    if record_id in self.seen_ids or item_num in self.seen_ids:
                        continue

                    # 2. Khử trùng đa tầng bằng Fuzzy Title Match (> 85%)
                    if self.is_duplicate_title(title):
                        self.logger.debug(f"[TRÙNG TIÊU ĐỀ] Bỏ qua: {title[:50]}")
                        self.seen_ids.add(record_id)
                        continue

                    authors = []
                    if len(tds) > 2:
                        raw_auth = tds[2].text.strip()
                        if raw_auth:
                            authors = [raw_auth]

                    degree = "Đồ án tốt nghiệp"
                    if "thạc sĩ" in col_name.lower():
                        degree = "Luận văn thạc sĩ"
                    elif "tiến sĩ" in col_name.lower():
                        degree = "Luận án tiến sĩ"

                    initial_data = {
                        "id": record_id,
                        "title": title,
                        "authors": authors,
                        "year": year,
                        "degree": degree
                    }

                    item_full_url = urllib.parse.urljoin(self.BASE_URL, href)
                    record = self._parse_item_page(item_full_url, initial_data)

                    if record:
                        self.append_record(record)
                        total_crawled += 1
                        self.logger.info(f"[{total_crawled}] Đã lưu: {record['title'][:55]} (Năm: {record.get('year')})")
                    else:
                        self.seen_ids.add(record_id)

                    self.jitter_delay()

                offset += 20
                self.save_checkpoint()

        # 2. Quét qua tìm kiếm Simple-Search để vét cạn theo từ khóa
        for q in self.SEARCH_QUERIES:
            if limit and total_crawled >= limit:
                break

            self.logger.info(f"\n--- Tìm kiếm từ khóa DUT: [{q}] ---")
            start = 0
            while True:
                if limit and total_crawled >= limit:
                    break

                search_url = f"{self.BASE_URL}/simple-search?query={urllib.parse.quote(q)}&start={start}"
                headers = self.get_random_headers({"Referer": self.BASE_URL})
                try:
                    r = self.session.get(search_url, headers=headers, timeout=12)
                    if r.status_code != 200:
                        break
                    soup = bs4.BeautifulSoup(r.text, "html.parser")
                except Exception as e:
                    break

                table = soup.find("table", class_="table")
                if not table:
                    break

                rows = table.find_all("tr")[1:]
                if not rows:
                    break

                for row in rows:
                    if limit and total_crawled >= limit:
                        break

                    tds = row.find_all("td")
                    if len(tds) < 2:
                        continue

                    date_str = tds[0].text.strip()
                    year = self._extract_year(date_str)
                    if self.max_year and year and year > self.max_year:
                        continue

                    a_title = tds[1].find("a")
                    if not a_title or not a_title.get("href"):
                        continue

                    title = self._clean_title(a_title.text.strip())
                    href = a_title.get("href")
                    m_id = re.search(r"/handle/DUT/(\d+)", href)
                    if not m_id:
                        continue
                    item_num = m_id.group(1)
                    record_id = f"dut_{item_num}"

                    if record_id in self.seen_ids or item_num in self.seen_ids:
                        continue

                    if self.is_duplicate_title(title):
                        self.seen_ids.add(record_id)
                        continue

                    authors = [tds[2].text.strip()] if len(tds) > 2 and tds[2].text.strip() else []

                    initial_data = {
                        "id": record_id,
                        "title": title,
                        "authors": authors,
                        "year": year,
                        "degree": "Đồ án / Luận văn tốt nghiệp"
                    }

                    item_full_url = urllib.parse.urljoin(self.BASE_URL, href)
                    record = self._parse_item_page(item_full_url, initial_data)

                    if record:
                        self.append_record(record)
                        total_crawled += 1
                        self.logger.info(f"[{total_crawled}] Đã lưu: {record['title'][:55]} (Năm: {record.get('year')})")
                    else:
                        self.seen_ids.add(record_id)

                    self.jitter_delay()

                start += 20
                self.save_checkpoint()

        self.save_checkpoint()
        self.logger.info(f"=== KẾT THÚC CÀO DUT: Thu thập thành công {total_crawled} tài liệu mới ===")
        return total_crawled
