# -*- coding: utf-8 -*-
"""
vnu_crawler.py: Crawler thu thập đồ án, khóa luận, luận văn CNTT từ kho lưu trữ số
Đại học Quốc gia Hà Nội (VNU DSpace 7 REST API) - đặc biệt từ Trường ĐH Công nghệ (UET)
và các khoa chuyên ngành CNTT.
"""
import os
import re
from datetime import datetime
from typing import Optional, Dict, Any, List
from .base_crawler import BaseCrawler
from .filters import is_it_topic

class VNUCrawler(BaseCrawler):
    BASE_API_URL = "https://repository.vnu.edu.vn/server/api/discover/search/objects"
    UET_SCOPE = "8cfcf102-73be-4e14-9b66-df5226723bae"  # Trường Đại học Công nghệ (UET)

    # Danh mục các truy vấn tìm kiếm mở rộng trên toàn kho VNU
    SEARCH_QUERIES = [
        "công nghệ thông tin",
        "khoa học máy tính",
        "kỹ thuật phần mềm",
        "hệ thống thông tin",
        "trí tuệ nhân tạo",
        "khoa học dữ liệu",
        "học máy",
        "học sâu",
        "xử lý ảnh",
        "thị giác máy tính",
        "nhận dạng",
        "an toàn thông tin",
        "an ninh mạng",
        "mạng máy tính",
        "xử lý ngôn ngữ tự nhiên",
        "xử lý tiếng nói",
        "khai phá dữ liệu",
        "cơ sở dữ liệu",
        "deep learning",
        "machine learning",
        "blockchain",
        "khóa luận tốt nghiệp",
        "đồ án tốt nghiệp",
        "luận văn thạc sĩ",
        "luận án tiến sĩ",
        "tin sinh học",
        "robotics",
        "iot"
    ]

    def __init__(
        self,
        output_file: str = "json/vnu_it_theses.jsonl",
        checkpoint_file: str = "json/.checkpoint.json",
        pdf_dir: Optional[str] = r"D:\Documents\NCKH- Đồ án\NCKH về NLP, VLM, Vision\Dataset_khoaluan",
        min_delay: float = 1.2,
        max_delay: float = 2.5,
        download_pdf: bool = True,
        only_pdf: bool = False,
        max_year: Optional[int] = 2022,
        auth_file: str = "library_auth.json",
        cookie: Optional[str] = None
    ):
        super().__init__(
            name="vnu_dspace",
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
        """Khởi tạo phiên xác thực VNU từ library_auth.json hoặc cookie trực tiếp."""
        import json
        cookie_str = self.auth_cookie
        if not cookie_str and os.path.exists(self.auth_file):
            try:
                with open(self.auth_file, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    vnu_cfg = cfg.get("vnu_lic", {})
                    cookie_str = vnu_cfg.get("cookie", "").strip()
            except Exception as e:
                self.logger.warning(f"Lỗi đọc {self.auth_file}: {e}")

        if cookie_str:
            if cookie_str.lower().startswith("cookie:"):
                cookie_str = cookie_str[7:].strip()
            self.session.headers["Cookie"] = cookie_str
            self.logger.info("Đã áp dụng Cookie xác thực Thư viện VNU LIC / DSpace.")


    def _extract_degree(self, title: str, citation: str, degreecode: str, types: List[str]) -> str:
        """Nhận diện bậc đào tạo: Khóa luận / Đồ án (Cử nhân, Kỹ sư), Luận văn Thạc sĩ, Luận án Tiến sĩ."""
        full_text = f"{title} {citation} {degreecode} {' '.join(types)}".lower()
        if "tiến sĩ" in full_text or "tiến sỹ" in full_text or "luận án" in full_text or "phd" in full_text:
            return "Tiến sĩ"
        if "thạc sĩ" in full_text or "thạc sỹ" in full_text or "luận văn" in full_text or "master" in full_text:
            return "Thạc sĩ"
        if "kỹ sư" in full_text or "đồ án" in full_text:
            return "Kỹ sư"
        if "khóa luận" in full_text or "cử nhân" in full_text or "bachelor" in full_text:
            return "Cử nhân / Khóa luận tốt nghiệp"
        return "Luận văn / Đồ án"

    def _parse_item(self, dso: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Bóc tách các trường Dublin Core từ DSpace 7 IndexableObject."""
        item_id = dso.get("id")
        if not item_id:
            return None
            
        record_id = f"vnu_{item_id}"
        if record_id in self.seen_ids:
            return None

        meta = dso.get("metadata", {})
        
        # Tiêu đề đề tài
        title_list = meta.get("dc.title", [{}])
        title = title_list[0].get("value", "").strip() if title_list else ""
        if not title:
            return None

        # Tác giả
        authors = [a.get("value", "").strip() for a in meta.get("dc.contributor.author", []) if a.get("value")]
        
        # Người hướng dẫn
        advisors = [adv.get("value", "").strip() for adv in meta.get("dc.contributor.advisor", []) if adv.get("value")]
        
        # Tóm tắt (Abstract)
        abstract_list = meta.get("dc.description.abstract", [{}])
        abstract = abstract_list[0].get("value", "").strip() if abstract_list else ""
        
        # Từ khóa / Chủ đề
        subjects = [s.get("value", "").strip() for s in meta.get("dc.subject", []) if s.get("value")]
        
        # Năm phát hành
        date_issued_list = meta.get("dc.date.issued", [{}])
        year_str = date_issued_list[0].get("value", "").strip() if date_issued_list else ""
        year = None
        match_year = re.search(r"\b(19\d{2}|20\d{2})\b", year_str)
        if match_year:
            year = int(match_year.group(1))

        if self.max_year and year and year > self.max_year:
            return None

        # Đơn vị đào tạo (Khoa / Trường)
        publishers = [p.get("value", "").strip() for p in meta.get("dc.publisher", []) if p.get("value")]
        faculty = ", ".join(publishers) if publishers else "Trường Đại học Công nghệ - ĐHQG Hà Nội"

        # Loại hình & Bậc học
        citation_list = meta.get("dc.identifier.citation", [{}])
        citation = citation_list[0].get("value", "").strip() if citation_list else ""
        degreecode = meta.get("dc.identifier.degreecode", [{}])[0].get("value", "") if meta.get("dc.identifier.degreecode") else ""
        types = [t.get("value", "").strip() for t in meta.get("dc.type", []) if t.get("value")]
        degree = self._extract_degree(title, citation, degreecode, types)

        # URL gốc
        uri_list = meta.get("dc.identifier.uri", [{}])
        url = uri_list[0].get("value", "").strip() if uri_list else ""
        if not url:
            handle = dso.get("handle")
            if handle:
                url = f"https://repository.vnu.edu.vn/handle/{handle}"
            else:
                url = f"https://repository.vnu.edu.vn/items/{item_id}"

        # Kiểm tra lọc chuyên ngành CNTT
        is_it, matched_tags = is_it_topic(
            title=title,
            abstract=abstract,
            keywords=subjects,
            faculty=faculty
        )
        if not is_it:
            return None

        # Tải file PDF toàn văn nếu được bật
        pdf_info = None
        if getattr(self, "download_pdf", False):
            pdf_info = self._download_pdf(item_id, record_id)
            # Nếu người dùng yêu cầu CHỈ LẤY BẢN CÓ FULL PDF mà không tải được -> Bỏ qua
            if getattr(self, "only_pdf", False) and not pdf_info:
                return None

        record = {
            "id": record_id,
            "source": "VNU DSpace (ĐHQG Hà Nội)",
            "title": title,
            "authors": authors,
            "advisors": advisors,
            "year": year,
            "degree": degree,
            "school_or_faculty": faculty,
            "keywords": subjects,
            "abstract": abstract,
            "matched_it_tags": matched_tags,
            "url": url,
            "has_full_pdf": pdf_info is not None,
            "pdf_path": pdf_info["pdf_path"] if pdf_info else None,
            "pdf_size_bytes": pdf_info["pdf_size_bytes"] if pdf_info else None,
            "pdf_download_url": pdf_info["pdf_download_url"] if pdf_info else None,
            "crawl_time": datetime.now().isoformat()
        }
        return record

    def _download_pdf(self, item_id: str, record_id: str) -> Optional[Dict[str, Any]]:
        """Tìm và tải file PDF toàn văn công khai nếu có."""
        bundles_url = f"https://repository.vnu.edu.vn/server/api/core/items/{item_id}/bundles"
        try:
            r_b = self.session.get(bundles_url, headers=self.get_random_headers(), timeout=self.timeout)
            if r_b.status_code != 200:
                return None
            for b in r_b.json().get("_embedded", {}).get("bundles", []):
                if b.get("name") == "ORIGINAL":
                    bs_url = b.get("_links", {}).get("bitstreams", {}).get("href")
                    if not bs_url:
                        continue
                    r_bs = self.session.get(bs_url, headers=self.get_random_headers(), timeout=self.timeout)
                    if r_bs.status_code != 200:
                        continue
                    for bs in r_bs.json().get("_embedded", {}).get("bitstreams", []):
                        name = bs.get("name", "")
                        name_lower = name.lower()
                        if not name_lower.endswith(".pdf"):
                            continue

                        # Danh sách từ khóa loại bỏ tài liệu tóm tắt, kỷ yếu, bìa, bản trích rút
                        BAD_PDF_NAMES = [
                            "tomtat", "tóm_tắt", "tóm tắt", "tom_tat", "abstract",
                            "cover", "bìa", "bia", "trang_bia", "trang bìa",
                            "ky_", "ky-", "kyht", "kỷ yếu", "ky_yeu", "hoi_thao", "hoithao",
                            "ban_bong", "bản bông", "trich_luc", "trích lục", "article_text",
                            "p742-", "p01-", "p02-", "slide", "outline", "phuluc", "phụ lục",
                            "summary", "brief", "preview", "mucluc", "mục lục", "toc"
                        ]
                        if any(k in name_lower for k in BAD_PDF_NAMES):
                            continue

                        content_url = bs.get("_links", {}).get("content", {}).get("href")
                        if not content_url:
                            continue
                        
                        pdf_dir = self.pdf_dir or os.path.join("pdf", "vnu")
                        os.makedirs(pdf_dir, exist_ok=True)
                        safe_name = re.sub(r"[^\w\.-]", "_", name)
                        save_path = os.path.join(pdf_dir, f"{record_id}_{safe_name}")

                        # Nếu file đã tồn tại trên đĩa với dung lượng >= 600 KB
                        if os.path.exists(save_path) and os.path.getsize(save_path) >= 600 * 1024:
                            try:
                                from pypdf import PdfReader
                                reader = PdfReader(save_path, strict=False)
                                if len(reader.pages) >= 35:
                                    size = os.path.getsize(save_path)
                                    self.logger.info(f"[ĐÃ TỒN TẠI FULL PDF VNU] {safe_name} ({size:,} bytes, {len(reader.pages)} trang)")
                                    return {
                                        "pdf_path": save_path,
                                        "pdf_filename": safe_name,
                                        "pdf_size_bytes": size,
                                        "pdf_download_url": content_url,
                                        "num_pages": len(reader.pages)
                                    }
                            except Exception:
                                pass

                        # Tải trực tiếp stream từ DSpace
                        try:
                            r_c = self.session.get(content_url, headers=self.get_random_headers(), stream=True, timeout=35)
                        except Exception:
                            continue
                        if r_c.status_code == 200:
                            with open(save_path, "wb") as f:
                                for chunk in r_c.iter_content(chunk_size=16384):
                                    f.write(chunk)
                            size = os.path.getsize(save_path)

                            # 1. Quality Gate 1: Dung lượng tối thiểu >= 600 KB (chống tóm tắt, bìa, slide)
                            if size < 600 * 1024:
                                self.logger.warning(f"[LOẠI BỎ - DUNG LƯỢNG < 600KB] {safe_name} ({size//1024} KB). Xóa file!")
                                try:
                                    os.remove(save_path)
                                except Exception:
                                    pass
                                continue

                            # 2. Quality Gate 2: Số trang >= 35 và text layer >= 20.000 ký tự
                            try:
                                from pypdf import PdfReader
                                reader = PdfReader(save_path, strict=False)
                                num_pages = len(reader.pages)
                                if num_pages < 35:
                                    self.logger.warning(f"[LOẠI BỎ - DƯỚI 35 TRANG] {safe_name} chỉ có {num_pages} trang (tóm tắt/bìa). Xóa file!")
                                    try:
                                        os.remove(save_path)
                                    except Exception:
                                        pass
                                    continue

                                sample_pages = min(num_pages, 35)
                                sample_text = "".join((reader.pages[i].extract_text() or "").strip() for i in range(sample_pages))
                                if len(sample_text) < 20000:
                                    self.logger.warning(f"[LOẠI BỎ - THIẾU TEXT/SCAN] {safe_name} chỉ có {len(sample_text)} ký tự text. Xóa file!")
                                    try:
                                        os.remove(save_path)
                                    except Exception:
                                        pass
                                    continue
                            except Exception as e:
                                self.logger.warning(f"Lỗi kiểm tra text VNU PDF: {e}")
                                try:
                                    os.remove(save_path)
                                except Exception:
                                    pass
                                continue

                            self.logger.info(f"[ĐÃ TẢI FULL PDF VNU ĐẠT CHUẨN] {safe_name} ({size:,} bytes, {num_pages} trang, {len(sample_text):,} chars) -> {save_path}")
                            return {
                                "pdf_path": save_path,
                                "pdf_filename": safe_name,
                                "pdf_size_bytes": size,
                                "pdf_download_url": content_url,
                                "num_pages": num_pages
                            }
        except Exception as e:
            self.logger.debug(f"Không thể tải PDF cho item {item_id}: {e}")
        return None

    def _fetch_page(self, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Gửi request lấy một trang kết quả từ DSpace REST API."""
        headers = self.get_random_headers()
        try:
            resp = self.session.get(
                self.BASE_API_URL,
                params=params,
                headers=headers,
                timeout=self.timeout
            )
            if resp.status_code == 200:
                return resp.json()
            else:
                self.logger.warning(f"DSpace API status {resp.status_code} với params {params}")
                return None
        except Exception as e:
            self.logger.error(f"Lỗi kết nối DSpace API: {e}")
            return None

    def crawl(self, limit: Optional[int] = None) -> int:
        """
        Thực hiện cào tài liệu CNTT từ VNU DSpace:
        1. Quét toàn bộ kho Trường ĐH Công nghệ (UET)
        2. Quét mở rộng theo các bộ từ khóa tìm kiếm
        """
        self.logger.info(f"Bắt đầu cào dữ liệu VNU DSpace (Mục tiêu: {limit or 'Không giới hạn'} bản ghi)...")
        crawled_count = 0
        page_size = 20

        # Nếu only_pdf được bật, ưu tiên quét theo SEARCH_QUERIES trước (nơi có nhiều PDF công khai)
        queries_to_run = self.SEARCH_QUERIES if self.only_pdf else []
        for query in queries_to_run:
            if limit is not None and crawled_count >= limit:
                break
            self.logger.info(f"[VNU - Search Queries] Tìm kiếm đề tài CNTT có full PDF: '{query}'...")
            page = 0
            while page < 15:
                if limit is not None and crawled_count >= limit:
                    break
                params = {"query": query, "size": page_size, "page": page}
                data = self._fetch_page(params)
                if not data:
                    break
                search_res = data.get("_embedded", {}).get("searchResult", {})
                objects = search_res.get("_embedded", {}).get("objects", [])
                if not objects:
                    break

                new_in_page = 0
                for obj in objects:
                    if limit is not None and crawled_count >= limit:
                        break
                    dso = obj.get("_embedded", {}).get("indexableObject", {})
                    record = self._parse_item(dso)
                    if record:
                        self.append_record(record)
                        crawled_count += 1
                        new_in_page += 1

                self.save_checkpoint()
                self.jitter_delay()
                page += 1

        # Giai đoạn tiếp theo: Quét theo scope Trường ĐH Công nghệ (UET)
        page = 0
        while limit is None or crawled_count < limit:
                
            params = {
                "scope": self.UET_SCOPE,
                "size": page_size,
                "page": page
            }
            data = self._fetch_page(params)
            if not data:
                break

            search_res = data.get("_embedded", {}).get("searchResult", {})
            objects = search_res.get("_embedded", {}).get("objects", [])
            if not objects:
                break

            new_in_page = 0
            for obj in objects:
                if limit is not None and crawled_count >= limit:
                    break
                dso = obj.get("_embedded", {}).get("indexableObject", {})
                record = self._parse_item(dso)
                if record:
                    self.append_record(record)
                    crawled_count += 1
                    new_in_page += 1

            self.logger.info(
                f"[VNU - UET Scope] Trang {page+1}: Lưu thêm {new_in_page} bản ghi CNTT. "
                f"Tổng hiện tại: {crawled_count}"
            )
            self.save_checkpoint()
            self.jitter_delay()

            # Kiểm tra trang tiếp theo
            pagination = search_res.get("page", {})
            total_pages = pagination.get("totalPages", 1)
            page += 1
            if page >= total_pages:
                break

        # Giai đoạn 2: Quét mở rộng theo các từ khóa chuyên ngành trên toàn VNU (nếu chưa đủ limit)
        if limit is None or crawled_count < limit:
            for query in self.SEARCH_QUERIES:
                if limit is not None and crawled_count >= limit:
                    break
                self.logger.info(f"[VNU - Query Search] Bắt đầu tìm kiếm với từ khóa: '{query}'...")
                page = 0
                max_search_pages = 10  # Giới hạn số trang mỗi từ khóa để đảm bảo đa dạng
                while page < max_search_pages:
                    if limit is not None and crawled_count >= limit:
                        break
                    params = {
                        "query": query,
                        "size": page_size,
                        "page": page
                    }
                    data = self._fetch_page(params)
                    if not data:
                        break

                    search_res = data.get("_embedded", {}).get("searchResult", {})
                    objects = search_res.get("_embedded", {}).get("objects", [])
                    if not objects:
                        break

                    new_in_page = 0
                    for obj in objects:
                        if limit is not None and crawled_count >= limit:
                            break
                        dso = obj.get("_embedded", {}).get("indexableObject", {})
                        record = self._parse_item(dso)
                        if record:
                            self.append_record(record)
                            crawled_count += 1
                            new_in_page += 1

                    self.save_checkpoint()
                    self.jitter_delay()
                    
                    pagination = search_res.get("page", {})
                    total_pages = pagination.get("totalPages", 1)
                    page += 1
                    if page >= total_pages:
                        break

        self.save_checkpoint()
        self.logger.info(f"Hoàn thành thu thập VNU DSpace! Đã lưu {crawled_count} bản ghi mới vào {self.output_file}")
        return crawled_count
