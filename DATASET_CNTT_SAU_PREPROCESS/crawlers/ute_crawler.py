# -*- coding: utf-8 -*-
"""
ute_crawler.py: Crawler thu thập đồ án, khóa luận tốt nghiệp và luận văn thạc sĩ CNTT
từ Thư viện số Trường Đại học Sư phạm Kỹ thuật TP.HCM (HCMUTE - https://thuvienso.hcmute.edu.vn).
Đảm bảo 100% tiếng Việt, digital text PDF, chuyên ngành CNTT và xuất bản <= 2022.
"""
import os
import re
import io
import urllib.parse
import concurrent.futures
from datetime import datetime
from typing import Optional, Dict, Any, List
import requests
import bs4
import urllib3
from .base_crawler import BaseCrawler
from .filters import is_it_topic

urllib3.disable_warnings()

class UTECrawler(BaseCrawler):
    BASE_URL = "https://thuvienso.hcmute.edu.vn"
    SEARCH_URL_TEMPLATE = "https://thuvienso.hcmute.edu.vn/tim-kiem/{keyword}.html?p={page}&c=&sc=&t="
    
    # Các danh mục học thuật chuẩn của UTE
    CATEGORIES = [
        ("do-an-khoa-luan-tot-nghiep", 9194, "Đồ án tốt nghiệp"),
        ("luan-van-luan-an", 9193, "Luận văn thạc sĩ")
    ]

    # Bộ từ khóa chuyên sâu CNTT tiếng Việt
    IT_SEARCH_TERMS = [
        # Nhóm từ khóa chuyên ngành
        "công nghệ thông tin",
        "khoa học máy tính",
        "kỹ thuật phần mềm",
        "hệ thống thông tin",
        "trí tuệ nhân tạo",
        "mạng máy tính",
        "an toàn thông tin",
        "an ninh mạng",
        "xử lý ảnh",
        "thị giác máy tính",
        "nhận dạng",
        "nhận diện",
        "học máy",
        "học sâu",
        "máy tính",
        "phần mềm",
        "ứng dụng di động",
        "android",
        "ios",
        "web",
        "website",
        "hệ điều hành",
        "mật mã",
        "cơ sở dữ liệu",
        "khai phá dữ liệu",
        "kiểm thử",
        "xây dựng hệ thống",
        "thiết kế hệ thống",
        "quản trị mạng",
        "bảo mật"
    ]

    def __init__(
        self,
        output_file: str = "json/ute_it_theses.jsonl",
        checkpoint_file: str = "json/.checkpoint.json",
        pdf_dir: Optional[str] = r"D:\Documents\NCKH- Đồ án\NCKH về NLP, VLM, Vision\Dataset_khoaluan",
        min_delay: float = 0.8,
        max_delay: float = 1.8,
        download_pdf: bool = True,
        only_pdf: bool = True,
        max_year: Optional[int] = 2022,
        auth_file: str = "library_auth.json",
        cookie: Optional[str] = None
    ):
        super().__init__(
            name="ute_library",
            output_file=output_file,
            checkpoint_file=checkpoint_file,
            pdf_dir=pdf_dir,
            min_delay=min_delay,
            max_delay=max_delay
        )
        self.download_pdf = download_pdf
        self.only_pdf = only_pdf
        self.max_year = max_year
        self.auth_file = auth_file
        self.auth_cookie = cookie
        self._init_auth()

    def _init_auth(self):
        """Khởi tạo phiên xác thực HCMUTE từ library_auth.json hoặc cookie trực tiếp."""
        import json
        cookie_str = self.auth_cookie
        if not cookie_str and os.path.exists(self.auth_file):
            try:
                with open(self.auth_file, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    ute_cfg = cfg.get("hcmute", {})
                    cookie_str = ute_cfg.get("cookie", "").strip()
            except Exception as e:
                self.logger.warning(f"Lỗi đọc {self.auth_file}: {e}")

        if cookie_str:
            if cookie_str.lower().startswith("cookie:"):
                cookie_str = cookie_str[7:].strip()
            self.session.headers["Cookie"] = cookie_str
            self.logger.info("Đã áp dụng Cookie xác thực Thư viện số HCMUTE.")


    def _clean_title(self, raw_title: str) -> str:
        """Làm sạch tiêu đề tài liệu."""
        if not raw_title:
            return ""
        title = raw_title.strip()
        if " / " in title:
            title = title.split(" / ")[0].strip()
        if " : " in title:
            title = title.split(" : ")[0].strip()
        return title

    def _parse_desc_metadata(self, desc_text: str) -> Dict[str, Any]:
        """
        Bóc tách tác giả, người hướng dẫn, năm xuất bản và chuyên ngành từ đoạn mô tả MARC tóm lược.
        Ví dụ: 'Nâng cao chất lượng dịch vụ: Luận văn thạc sĩ ngành CNTT / Đàm Thị Hướng; Hồ Thị Hồng Xuyên (GVHD). -- TP.HCM, 2021.'
        """
        meta = {
            "year": None,
            "authors": [],
            "advisors": [],
            "degree": "Đồ án / Luận văn",
            "major": ""
        }
        if not desc_text:
            return meta

        # 1. Trích xuất năm xuất bản
        m_year = re.search(r"\b(19\d{2}|20\d{2})\b", desc_text)
        if m_year:
            meta["year"] = int(m_year.group(1))

        # 2. Trích xuất học vị / bậc đào tạo
        lt = desc_text.lower()
        if "luận văn thạc sĩ" in lt or "thạc sĩ" in lt:
            meta["degree"] = "Luận văn thạc sĩ"
        elif "tiến sĩ" in lt or "luận án" in lt:
            meta["degree"] = "Luận án tiến sĩ"
        elif "khóa luận" in lt:
            meta["degree"] = "Khóa luận tốt nghiệp"
        else:
            meta["degree"] = "Đồ án tốt nghiệp"

        # 3. Trích xuất tác giả và người hướng dẫn
        if "/" in desc_text:
            after_slash = desc_text.split("/", 1)[1]
            before_pub = after_slash.split(".--", 1)[0].split("--", 1)[0]
            parts = before_pub.split(";")
            
            # Tác giả trước dấu chấm phẩy
            raw_authors = parts[0].strip()
            for a in raw_authors.split(","):
                clean_a = a.strip()
                if clean_a and not any(k in clean_a.lower() for k in ["hướng dẫn", "supervisor", "tp.hcm"]):
                    meta["authors"].append(clean_a)

            # Người hướng dẫn sau dấu chấm phẩy
            if len(parts) > 1:
                raw_adv = parts[1].strip()
                for adv in raw_adv.split(","):
                    clean_adv = re.sub(r"\(.*?\)", "", adv)
                    clean_adv = re.sub(r"\[.*?\]", "", clean_adv)
                    clean_adv = re.sub(r"(?:giảng viên|người|cán bộ)?\s*hướng dẫn", "", clean_adv, flags=re.I).strip()
                    if clean_adv and len(clean_adv) > 2:
                        meta["advisors"].append(clean_adv)

        return meta

    def _extract_pdf_url(self, doc_url: str) -> Optional[str]:
        """Truy cập trang chi tiết đề tài và trích xuất đường dẫn PDF toàn văn trực tiếp."""
        headers = self.get_random_headers({"Referer": self.BASE_URL})
        try:
            r = self.session.get(doc_url, headers=headers, verify=False, timeout=12)
            if r.status_code == 200:
                # Tìm các link kết thúc bằng .pdf trên máy chủ thuvienso.hcmute.edu.vn
                matches = re.findall(r'https?://[^\s\'"<>]+?\.pdf(?:\?[^\s\'"<>]*)?', r.text, re.I)
                for m in matches:
                    if "thuvienso.hcmute.edu.vn" in m and "tvs" in m:
                        clean_url = m.split("#")[0]
                        return clean_url
        except Exception as e:
            self.logger.debug(f"Lỗi khi trích xuất PDF từ {doc_url}: {e}")
        return None

    def _download_pdf(self, pdf_url: str, record_id: str, title: str) -> Optional[Dict[str, Any]]:
        """Tải file PDF, kiểm tra font nhị phân (chống scan ảnh), kiểm tra text layer và lưu đĩa."""
        safe_title = re.sub(r"[^\w\.-]", "_", title)[:50]
        pdf_filename = f"{record_id}_{safe_title}.pdf"
        save_path = os.path.join(self.pdf_dir, pdf_filename)

        # 1. Nếu file đã tồn tại trên đĩa với text layer chuẩn
        if os.path.exists(save_path) and os.path.getsize(save_path) > 1000:
            size = os.path.getsize(save_path)
            try:
                with open(save_path, "rb") as f_chk:
                    head = f_chk.read(min(size, 100000))
                font_count = head.count(b"/Font")
                if font_count >= 2:
                    self.logger.info(f"[ĐÃ TỒN TẠI & TEXT OK] {pdf_filename} ({size:,} bytes)")
                    return {
                        "pdf_path": save_path,
                        "pdf_filename": pdf_filename,
                        "pdf_size_bytes": size,
                        "pdf_download_url": pdf_url
                    }
            except Exception:
                pass

        # 2. Tải trực tiếp stream từ máy chủ UTE
        headers = self.get_random_headers({"Referer": self.BASE_URL})
        try:
            r = self.session.get(pdf_url, headers=headers, stream=True, verify=False, timeout=20)
            if r.status_code == 200:
                content_iter = r.iter_content(chunk_size=32768)
                try:
                    first_chunk = next(content_iter)
                except StopIteration:
                    first_chunk = b""

                if first_chunk.startswith(b"%PDF"):
                    first_100k = bytearray(first_chunk)
                    is_early_scan = False

                    with open(save_path, "wb") as f:
                        f.write(first_chunk)
                        for chunk in content_iter:
                            if chunk:
                                f.write(chunk)
                                if len(first_100k) < 100000:
                                    first_100k.extend(chunk)
                                elif not is_early_scan:
                                    # Kiểm tra font chữ trong 100KB đầu
                                    if first_100k.count(b"/Font") < 2:
                                        is_early_scan = True
                                        break

                    if is_early_scan or first_100k.count(b"/Font") < 2:
                        self.logger.warning(f"[HỦY SỚM - BẢN SCAN] {pdf_filename} không có font text. Hủy tải ngay!")
                        try:
                            os.remove(save_path)
                        except Exception:
                            pass
                        return None

                    size = os.path.getsize(save_path)
                    if size > 35 * 1024 * 1024:
                        self.logger.warning(f"[LOẠI BỎ - SCAN DUNG LƯỢNG QUÁ LỚN] {pdf_filename} ({size:,} bytes).")
                        try:
                            os.remove(save_path)
                        except Exception:
                            pass
                        return None

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
                                f"[LOẠI BỎ - DƯỚI 35 TRANG] {pdf_filename} chỉ có {num_pages} trang. Xóa file!"
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
                                f"[LOẠI BỎ - TEXT LAYER DƯỚI 20000 KÝ TỰ] {pdf_filename} chỉ có {len(sample_text)} chars. Xóa file!"
                            )
                            try:
                                os.remove(save_path)
                            except Exception:
                                pass
                            return None
                    except Exception as e:
                        self.logger.debug(f"Warning kiểm tra pypdf ({pdf_filename}): {e}")

                    self.logger.info(f"[ĐÃ TẢI DIGITAL TEXT PDF UTE CHUẨN] {pdf_filename} ({size:,} bytes, {num_pages} trang, text {len(sample_text):,} chars) -> {save_path}")
                    return {
                        "pdf_path": save_path,
                        "pdf_filename": pdf_filename,
                        "pdf_size_bytes": size,
                        "pdf_download_url": pdf_url
                    }
        except Exception as e:
            self.logger.debug(f"Lỗi tải PDF từ {pdf_url}: {e}")
            if os.path.exists(save_path):
                try:
                    os.remove(save_path)
                except Exception:
                    pass
        return None

    def _process_item_li(self, li: bs4.element.Tag, default_degree: str = "Đồ án tốt nghiệp") -> Optional[Dict[str, Any]]:
        """Xử lý 1 thẻ <li> tài liệu trên giao diện web UTE."""
        a = li.find("a", class_="colorlink")
        if not a or not a.get("href"):
            return None

        doc_url = a["href"].strip()
        raw_title = a.get("title") or a.get_text(strip=True)
        title = self._clean_title(raw_title)
        if not title:
            return None

        # Trích xuất ID đề tài
        m_id = re.search(r"-(\d+)\.html", doc_url)
        raw_id = m_id.group(1) if m_id else str(hash(doc_url))
        record_id = f"ute_{raw_id}"

        with self.lock:
            if record_id in self.seen_ids or raw_id in self.seen_ids:
                return None

            if self.is_duplicate_title(title):
                self.seen_ids.add(record_id)
                return None
            
            # Đánh dấu giữ chỗ ID ngay lập tức để tránh thread khác xử lý trùng
            self.seen_ids.add(record_id)
            self.seen_ids.add(raw_id)

        # TỐI ƯU 1: Kiểm tra sơ bộ tiêu đề bài báo, kỷ yếu, hội thảo, giáo trình, bài giảng
        quick_lower = title.lower()
        if any(bad in quick_lower for bad in ["tạp chí", "bài báo", "kỷ yếu", "hội thảo", "journal", "proceedings", "giáo trình", "bài giảng", "sách giáo trình", "tài liệu hướng dẫn"]):
            return None

        # TỐI ƯU 2: Bắt buộc tiếng Việt có dấu
        vn_char_pattern = re.compile(r'[àáảãạăắằẳẵặâấầẩẫậđèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵ]')
        if not vn_char_pattern.search(quick_lower):
            return None

        # Bóc tách đoạn mô tả
        desc_p = li.find("div", class_="decs_book")
        desc_text = desc_p.get_text(" ", strip=True) if desc_p else ""
        meta = self._parse_desc_metadata(desc_text)

        # TỐI ƯU 3: Kiểm tra năm xuất bản <= 2022
        year = meta.get("year")
        if self.max_year and year and year > self.max_year:
            return None

        # TỐI ƯU 4: Trích xuất từ khóa
        keywords = [a_kw.get_text(strip=True) for a_kw in li.find_all("a", class_="txt_link")]

        # TỐI ƯU 5: Kiểm tra chuyên ngành CNTT
        is_it, matched_tags = is_it_topic(
            title=title,
            abstract=desc_text,
            keywords=keywords
        )
        if not is_it:
            return None

        # TỐI ƯU 6: Kiểm tra chống trùng lặp với Dataset_khoaluan qua tiêu đề
        clean_t = re.sub(r"[^\w]", "", title.lower())
        if hasattr(self, "external_titles") and clean_t in self.external_titles:
            return None

        # TỐI ƯU 7: Kiểm tra chống trùng lặp qua tác giả
        authors = meta.get("authors", [])
        if hasattr(self, "external_authors") and authors:
            for auth in authors:
                clean_a = re.sub(r"[^\w]", "", auth.lower())
                if clean_a and clean_a in self.external_authors:
                    return None

        # Lấy link PDF từ trang chi tiết
        self.jitter_delay()
        pdf_url = self._extract_pdf_url(doc_url)
        if not pdf_url and self.only_pdf:
            return None

        # Tải file PDF
        pdf_info = None
        if self.download_pdf and pdf_url:
            pdf_info = self._download_pdf(pdf_url, record_id, title)
            if self.only_pdf and not pdf_info:
                return None

        record = {
            "id": record_id,
            "source": "Thư viện số Trường ĐH Sư phạm Kỹ thuật TP.HCM (HCMUTE)",
            "title": title,
            "authors": authors,
            "advisors": meta.get("advisors", []),
            "year": year,
            "degree": meta.get("degree") or default_degree,
            "school_or_faculty": "Trường Đại học Sư phạm Kỹ thuật TP.HCM",
            "keywords": keywords,
            "abstract": desc_text,
            "matched_it_tags": matched_tags,
            "url": doc_url,
            "has_full_pdf": pdf_info is not None,
            "pdf_path": pdf_info["pdf_path"] if pdf_info else None,
            "pdf_size_bytes": pdf_info["pdf_size_bytes"] if pdf_info else None,
            "pdf_download_url": pdf_info["pdf_download_url"] if pdf_info else None,
            "crawl_time": datetime.now().isoformat()
        }
        return record

    def _process_batch_items(self, items: List[Any], cat_degree: str, limit: Optional[int], current_crawled: int) -> List[Dict[str, Any]]:
        """Xử lý song song một mảng các thẻ li trên trang bằng ThreadPoolExecutor."""
        if not items:
            return []
        valid_records = []
        max_workers = min(6, len(items))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_li = {executor.submit(self._process_item_li, li, cat_degree): li for li in items}
            for fut in concurrent.futures.as_completed(future_to_li):
                try:
                    rec = fut.result()
                    if rec:
                        valid_records.append(rec)
                        if limit is not None and (current_crawled + len(valid_records)) >= limit:
                            break
                except Exception as e:
                    self.logger.debug(f"Lỗi worker UTE: {e}")
        return valid_records

    def crawl(self, limit: Optional[int] = None) -> int:
        """
        Cào đề tài đồ án, khóa luận tốt nghiệp & luận văn CNTT từ UTE:
        Giai đoạn 1: Quét theo các bộ từ khóa tìm kiếm CNTT
        Giai đoạn 2: Quét trực tiếp các danh mục Đồ án (9194) và Luận văn (9193)
        """
        self.logger.info(f"Bắt đầu cào dữ liệu Thư viện số UTE (Mục tiêu: {limit or 'Không giới hạn'} bản ghi, Multi-threaded)...")
        crawled_count = 0
        headers = self.get_random_headers({"Referer": self.BASE_URL})

        # GIAI ĐOẠN 1: Quét theo danh sách từ khóa CNTT (giới hạn trong Đồ án 9194 và Luận văn 9193)
        for cat_slug, cat_id, cat_degree in self.CATEGORIES:
            if limit is not None and crawled_count >= limit:
                break
            for term in self.IT_SEARCH_TERMS:
                if limit is not None and crawled_count >= limit:
                    break
                key_encoded = urllib.parse.quote(term.replace(" ", "+"))
                self.logger.info(f"[UTE Search] Tìm kiếm '{term}' trong [{cat_degree}]...")

                for page in range(1, 10):
                    if limit is not None and crawled_count >= limit:
                        break

                    search_url = f"{self.BASE_URL}/tim-kiem/{key_encoded}.html?p={page}&c={cat_id}&sc=&t="
                    try:
                        resp = self.session.get(search_url, headers=headers, verify=False, timeout=12)
                        if resp.status_code != 200:
                            break
                        soup = bs4.BeautifulSoup(resp.text, "html.parser")
                        lis = soup.find_all("li")
                        if not lis:
                            break

                        new_in_page = 0
                        batch_records = self._process_batch_items(lis, cat_degree, limit, crawled_count)
                        for rec in batch_records:
                            self.append_record(rec)
                            crawled_count += 1
                            new_in_page += 1
                            if limit is not None and crawled_count >= limit:
                                break

                        if new_in_page > 0:
                            self.save_checkpoint()
                            self.logger.info(f"[UTE - {term}] Trang {page}: Đã lưu {new_in_page} file PDF chuẩn. Tổng UTE: {crawled_count}")

                        if len(lis) < 5:
                            break
                    except Exception as e:
                        self.logger.warning(f"Lỗi khi tìm kiếm UTE '{term}' trang {page}: {e}")
                        break

        # GIAI ĐOẠN 2: Quét trực tiếp danh mục Đồ án tốt nghiệp (9194) và Luận văn (9193) theo trang chuẩn
        for cat_slug, cat_id, cat_degree in self.CATEGORIES:
            if limit is not None and crawled_count >= limit:
                break
            self.logger.info(f"[UTE Category] Đang duyệt toàn bộ danh mục '{cat_degree}' ({cat_slug})...")

            for page in range(1, 450):
                if limit is not None and crawled_count >= limit:
                    break

                cat_url = (
                    f"{self.BASE_URL}/{cat_slug}/tat-ca-tai-lieu-{cat_slug}-{cat_id}-0.html"
                    f"?vt=moinhat&ft=all&fft=pdf&fl=vietnamese&catetl=0&subcatetl=0&page={page}"
                )
                try:
                    resp = self.session.get(cat_url, headers=headers, verify=False, timeout=12)
                    if resp.status_code != 200:
                        break
                    soup = bs4.BeautifulSoup(resp.text, "html.parser")
                    lis = [li for li in soup.find_all("li") if li.find("a", class_="colorlink")]
                    if not lis:
                        break

                    new_in_page = 0
                    batch_records = self._process_batch_items(lis, cat_degree, limit, crawled_count)
                    for rec in batch_records:
                        self.append_record(rec)
                        crawled_count += 1
                        new_in_page += 1
                        if limit is not None and crawled_count >= limit:
                            break

                    if new_in_page > 0:
                        self.save_checkpoint()
                        self.logger.info(f"[UTE - {cat_degree}] Trang {page}: Đã lưu {new_in_page} file PDF chuẩn. Tổng UTE: {crawled_count}")
                except Exception as e:
                    self.logger.warning(f"Lỗi duyệt danh mục UTE {cat_slug} trang {page}: {e}")
                    break

        self.save_checkpoint()
        self.logger.info(f"Hoàn thành thu thập UTE! Đã lưu {crawled_count} bản ghi mới vào {self.output_file}")
        return crawled_count
