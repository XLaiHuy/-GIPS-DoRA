# -*- coding: utf-8 -*-
"""
ojs_crawler.py: Crawler thu thập bài báo nghiên cứu khoa học, đồ án và chuyên khảo CNTT
từ các tạp chí khoa học hàng đầu Việt Nam sử dụng nền tảng OJS (Open Journal Systems):
1. VAST JCC (Tạp chí Tin học và Điều khiển học - Viện Hàn lâm KH&CN Việt Nam)
2. VNU JCSCE (Tạp chí Khoa học ĐHQGHN: Khoa học Máy tính & Kỹ thuật Truyền thông)
Tất cả đều là PDF số hóa chất lượng cao (100% digital native text), chuẩn NCKH quốc gia.
"""
import os
import re
import urllib3
from datetime import datetime
from typing import Optional, Dict, Any, List
from bs4 import BeautifulSoup

from .base_crawler import BaseCrawler

urllib3.disable_warnings()


class OJSCrawler(BaseCrawler):
    """Crawler chuyên thu thập bài báo CNTT từ các hệ thống Open Journal Systems."""

    JOURNAL_CONFIGS = [
        {
            "key": "vast_jcc",
            "name": "VAST - Tạp chí Tin học và Điều khiển học",
            "base_url": "https://vjs.ac.vn",
            "archive_url": "https://vjs.ac.vn/jcc/issue/archive",
            "article_prefix": "/jcc/article/view/",
            "issue_prefix": "/jcc/issue/view/",
        },
        {
            "key": "vnu_jcsce",
            "name": "VNU - Chuyên san Khoa học Máy tính & Truyền thông",
            "base_url": "https://jcsce.vnu.edu.vn",
            "archive_url": "https://jcsce.vnu.edu.vn/index.php/jcsce/issue/archive",
            "article_prefix": "/article/view/",
            "issue_prefix": "/issue/view/",
        }
    ]

    def __init__(
        self,
        output_file: str = "json/academic_papers_it.jsonl",
        checkpoint_file: str = "json/.checkpoint.json",
        pdf_dir: Optional[str] = r"D:\Documents\NCKH- Đồ án\NCKH về NLP, VLM, Vision\Dataset_khoaluan",
        min_delay: float = 1.0,
        max_delay: float = 2.0,
        download_pdf: bool = True,
        only_pdf: bool = True,
        max_year: Optional[int] = 2022
    ):
        super().__init__(
            name="ojs_journals",
            output_file=output_file,
            checkpoint_file=checkpoint_file,
            pdf_dir=pdf_dir,
            min_delay=min_delay,
            max_delay=max_delay
        )
        self.download_pdf = download_pdf
        self.only_pdf = only_pdf
        self.max_year = max_year

    def _extract_year_from_text(self, text: str) -> Optional[int]:
        """Trích xuất năm xuất bản từ tiêu đề số báo (ví dụ: 'Vol. 38 No. 4 (2022)')."""
        m = re.search(r"\b(19\d{2}|20\d{2})\b", text)
        if m:
            return int(m.group(1))
        return None

    def _verify_pdf_text(self, filepath: str) -> bool:
        """Kiểm tra xem PDF có chứa text layer thực thụ không (tránh scan ảnh)."""
        try:
            size = os.path.getsize(filepath)
            if size > 35 * 1024 * 1024:  # File > 35MB thường là ảnh scan
                return False
            with open(filepath, "rb") as f:
                head = f.read(min(size, 2000000))
            if head.count(b"/Font") >= 1:
                return True
            try:
                from pypdf import PdfReader
                reader = PdfReader(filepath)
                if len(reader.pages) > 0:
                    t = (reader.pages[0].extract_text() or "").strip()
                    return len(t) > 50
            except Exception:
                pass
            return False
        except Exception:
            return False

    def _fetch_soup(self, url: str) -> Optional[BeautifulSoup]:
        """Gửi request lấy BeautifulSoup từ một trang OJS."""
        headers = self.get_random_headers()
        try:
            r = self.session.get(url, headers=headers, verify=False, timeout=self.timeout)
            if r.status_code == 200:
                return BeautifulSoup(r.text, "html.parser")
            else:
                self.logger.warning(f"OJS GET {url} status {r.status_code}")
                return None
        except Exception as e:
            self.logger.error(f"Lỗi tải trang OJS {url}: {e}")
            return None

    def _download_article_pdf(self, journal_key: str, article_id: str, article_soup: BeautifulSoup, title: str) -> Optional[Dict[str, Any]]:
        """Tìm link Galley PDF và tải file PDF chính bản."""
        # 1. Tìm thẻ download hoặc galley link
        galley_link = None
        for a in article_soup.find_all("a"):
            href = a.get("href", "")
            if "/article/view/" in href and (
                "pdf" in href.lower() or 
                "pdf" in a.get_text().lower() or 
                "download" in href.lower() or
                "obj_galley_link" in a.get("class", [])
            ):
                galley_link = href
                break

        if not galley_link:
            return None

        # Chuẩn hóa URL
        if galley_link.startswith("//"):
            galley_link = "https:" + galley_link

        # 2. Lấy trang download view để trích xuất direct download URL
        headers = self.get_random_headers()
        try:
            rg = self.session.get(galley_link, headers=headers, verify=False, timeout=self.timeout)
            if rg.status_code != 200:
                return None
            soup_g = BeautifulSoup(rg.text, "html.parser")
            dl_btn = soup_g.find("a", class_="download") or soup_g.find("a", href=re.compile(r"/article/download/"))
            direct_url = dl_btn.get("href") if dl_btn else galley_link
            if direct_url.startswith("//"):
                direct_url = "https:" + direct_url
        except Exception as e:
            self.logger.debug(f"Lỗi giải mã download link: {e}")
            direct_url = galley_link

        # 3. Tiến hành tải file PDF
        try:
            pdf_dir = self.pdf_dir or os.path.join("pdf", "ojs")
            os.makedirs(pdf_dir, exist_ok=True)
            safe_title = re.sub(r"[^\w\.-]", "_", title)[:60].strip("_")
            pdf_filename = f"{journal_key}_{article_id}_{safe_title}.pdf"
            save_path = os.path.join(pdf_dir, pdf_filename)

            # Nếu file đã tồn tại trên đĩa và hợp lệ
            if os.path.exists(save_path) and os.path.getsize(save_path) > 5000:
                if self._verify_pdf_text(save_path):
                    size = os.path.getsize(save_path)
                    self.logger.info(f"[ĐÃ TỒN TẠI TRÊN ĐĨA] {pdf_filename} ({size:,} bytes)")
                    return {
                        "pdf_path": save_path,
                        "pdf_filename": pdf_filename,
                        "pdf_size_bytes": size,
                        "pdf_download_url": direct_url
                    }

            r_pdf = self.session.get(direct_url, headers=headers, verify=False, stream=True, timeout=45)
            if r_pdf.status_code != 200:
                return None

            content_iter = r_pdf.iter_content(chunk_size=16384)
            try:
                first_chunk = next(content_iter)
            except StopIteration:
                first_chunk = b""

            if not first_chunk.startswith(b"%PDF"):
                return None

            with open(save_path, "wb") as f:
                f.write(first_chunk)
                for chunk in content_iter:
                    if chunk:
                        f.write(chunk)

            # Kiểm tra chất lượng text
            if not self._verify_pdf_text(save_path):
                self.logger.warning(f"[LOẠI BỎ FILE SCAN] {pdf_filename} không có text font. Xóa file!")
                try:
                    os.remove(save_path)
                except Exception:
                    pass
                return None

            size = os.path.getsize(save_path)
            self.logger.info(f"[ĐÃ TẢI DIGITAL TEXT PDF] {pdf_filename} ({size:,} bytes) -> {save_path}")
            return {
                "pdf_path": save_path,
                "pdf_filename": pdf_filename,
                "pdf_size_bytes": size,
                "pdf_download_url": direct_url
            }
        except Exception as e:
            self.logger.error(f"Lỗi khi tải PDF cho {journal_key} article {article_id}: {e}")
            return None

    def _parse_article(self, journal: Dict[str, Any], article_url: str) -> Optional[Dict[str, Any]]:
        """Bóc tách metadata Dublin Core và tải PDF của một bài báo."""
        soup = self._fetch_soup(article_url)
        if not soup:
            return None

        # Trích xuất Article ID từ URL
        m = re.search(r"/article/view/(\d+)", article_url)
        art_id = m.group(1) if m else str(hash(article_url))
        record_id = f"{journal['key']}_{art_id}"

        if record_id in self.seen_ids:
            return None

        # Trích xuất metadata từ Dublin Core meta tags
        title = ""
        authors = []
        abstract = ""
        keywords = []
        year = None
        date_published = ""
        doi = ""

        for meta in soup.find_all("meta"):
            name = (meta.get("name") or "").lower()
            content = (meta.get("content") or "").strip()
            if not content:
                continue
            if name in ["dc.title", "citation_title"]:
                title = content
            elif name in ["dc.creator", "citation_author"]:
                authors.append(content)
            elif name in ["dc.description", "citation_abstract"]:
                abstract = content
            elif name in ["dc.subject", "citation_keywords"]:
                keywords.extend([k.strip() for k in content.split(",") if k.strip()])
            elif name in ["dc.date.created", "citation_date", "citation_publication_date"]:
                date_published = content
                yr_m = re.search(r"\b(19\d{2}|20\d{2})\b", content)
                if yr_m:
                    year = int(yr_m.group(1))
            elif name in ["dc.identifier.doi", "citation_doi"]:
                doi = content

        # Nếu không có meta tag, lấy từ giao diện HTML
        if not title:
            h1 = soup.find("h1", class_="page_title") or soup.find("h1")
            title = h1.get_text(strip=True) if h1 else ""

        if not title:
            return None

        # Kiểm tra điều kiện năm xuất bản (<= max_year để chống nhiễm AI)
        if self.max_year and year and year > self.max_year:
            self.logger.info(f"[BỎ QUA NĂM {year} > {self.max_year}] '{title[:45]}...' sau năm {self.max_year}.")
            return None

        # Tải PDF
        pdf_info = None
        if self.download_pdf:
            pdf_info = self._download_article_pdf(journal["key"], art_id, soup, title)
            if self.only_pdf and not pdf_info:
                return None

        record = {
            "record_id": record_id,
            "source": journal["key"],
            "journal_name": journal["name"],
            "title": title,
            "authors": authors,
            "abstract": abstract,
            "keywords": list(set(keywords)),
            "year": year,
            "date_published": date_published,
            "doi": doi,
            "url": article_url,
            "degree": "Bài báo Khoa học / Tạp chí Chuyên ngành",
            "crawled_at": datetime.now().isoformat()
        }
        if pdf_info:
            record.update(pdf_info)

        return record

    def crawl(self, limit: Optional[int] = None) -> int:
        """
        Duyệt qua các tạp chí OJS hàng đầu (VAST JCC, VNU JCSCE),
        quét các số lưu trữ từ 2015 đến max_year (2022) và tải PDF bản chính.
        """
        self.logger.info(f"Bắt đầu cào các Tạp chí Khoa học OJS (Mục tiêu: {limit or 'Không giới hạn'} bài báo)...")
        crawled_count = 0

        for journal in self.JOURNAL_CONFIGS:
            if limit is not None and crawled_count >= limit:
                break

            self.logger.info(f"--- Đang duyệt tạp chí: {journal['name']} ({journal['key']}) ---")
            soup_archive = self._fetch_soup(journal["archive_url"])
            if not soup_archive:
                continue

            # Tìm tất cả các số báo (issues)
            issues = []
            for a in soup_archive.find_all("a"):
                href = a.get("href", "")
                if journal["issue_prefix"] in href:
                    issue_title = a.get_text(strip=True)
                    yr = self._extract_year_from_text(issue_title)
                    # Lọc năm <= max_year (mặc định 2022, bỏ qua số báo Online First hoặc năm mới)
                    if yr is not None and yr <= (self.max_year or 2022):
                        if href not in [iss[1] for iss in issues]:
                            issues.append((issue_title, href, yr))

            self.logger.info(f"[{journal['name']}] Tìm thấy {len(issues)} số báo phù hợp (năm <= {self.max_year or 2022}).")

            for issue_title, issue_url, issue_year in issues:
                if limit is not None and crawled_count >= limit:
                    break

                self.logger.info(f"[{journal['name']}] Đang duyệt số báo: '{issue_title}' ({issue_url})...")
                soup_issue = self._fetch_soup(issue_url)
                if not soup_issue:
                    continue

                # Tìm tất cả bài báo trong số báo
                article_urls = []
                for a in soup_issue.find_all("a"):
                    h = a.get("href", "")
                    if journal["article_prefix"] in h:
                        # Bỏ qua link PDF trực tiếp ở ngoài danh mục nếu trùng
                        clean_h = re.sub(r"/\d+$", "", h) if h.endswith("/pdf") else h
                        if clean_h not in article_urls:
                            article_urls.append(h)

                self.logger.info(f"[{journal['name']}] Số báo '{issue_title}' có {len(article_urls)} bài báo.")

                for art_url in article_urls:
                    if limit is not None and crawled_count >= limit:
                        break

                    # Chuẩn hóa URL
                    if art_url.startswith("//"):
                        art_url = "https:" + art_url
                    elif art_url.startswith("/"):
                        art_url = journal["base_url"] + art_url

                    record = self._parse_article(journal, art_url)
                    if record:
                        self.append_record(record)
                        crawled_count += 1
                        self.save_checkpoint()

                    self.jitter_delay()

        self.logger.info(f"Hoàn thành thu thập Tạp chí OJS: Đã tải thêm {crawled_count} bài báo PDF chất lượng cao.")
        return crawled_count
