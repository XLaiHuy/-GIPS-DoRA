# -*- coding: utf-8 -*-
"""
ou_crawler.py: Crawler thu thập khóa luận, luận văn, luận án CNTT từ
Thư viện Trường Đại học Mở TP.HCM (https://thuvien.ou.edu.vn)
Đảm bảo khử trùng tiêu đề đa tầng (> 85%), Quality Gate >= 15 trang, >= 3.000 ký tự text.
"""
import os
import re
import concurrent.futures
from datetime import datetime
from typing import Optional, Dict, Any, List
from .base_crawler import BaseCrawler
from .filters import is_it_topic

class OUCrawler(BaseCrawler):
    BASE_URL = "https://thuvien.ou.edu.vn"
    SEARCH_LOCAL_API = "https://thuvien.ou.edu.vn/api/ebook/SearchLocal"
    RECORD_CONTENT_API = "https://thuvien.ou.edu.vn/api/Book/GetRecordContent"

    # Các nhóm tài liệu học thuật (RecordGroupId) - Khóa luận, Luận văn, Luận án & NCKH Sinh viên
    RECORD_GROUPS = {
        13: "Khóa luận",               # Khóa luận Tốt nghiệp Đại học (nhiều đề tài CNTT toàn văn nhất)
        3: "Luận văn",                 # Luận văn Thạc sĩ
        14: "Luận án",                 # Luận án Tiến sĩ
        8: "Công trình nghiên cứu"     # Đề tài NCKH Sinh viên / Giảng viên CNTT (toàn văn)
    }

    # Các từ khóa tìm kiếm chuyên ngành CNTT chuẩn xác cao
    IT_SEARCH_TERMS = [
        # 0. Các tiền tố tên đề tài đồ án/khóa luận CNTT năng suất cao
        "xây dựng",
        "thiết kế",
        "phát triển",
        "tìm hiểu",
        "nghiên cứu",
        "triển khai",
        "quản trị",
        "tối ưu",
        "công nghệ",
        "web",
        "gis",
        "windows",
        "java",
        "máy tính",
        "tin học",

        # 1. Hệ điều hành, Mật mã & Bảo mật (Theo yêu cầu người dùng)
        "bảo mật hệ điều hành",
        "hệ điều hành",
        "mật mã học",
        "mật mã",
        "mã hóa",
        "chữ ký số",
        "pki",
        "an ninh mạng",
        "an toàn thông tin",
        "bảo mật mạng",
        "lỗ hổng",
        "mã độc",
        "tường lửa",
        "phát hiện xâm nhập",
        "tấn công mạng",
        "forensics",
        "điều tra số",
        
        # 2. Đồ họa máy tính, Thị giác & Đa phương tiện
        "đồ họa máy tính",
        "đồ họa",
        "computer graphics",
        "thực tế ảo",
        "thực tế tăng cường",
        "dựng hình",
        "xử lý ảnh",
        "xử lý video",
        "xử lý tín hiệu",
        "nhận dạng",
        "nhận diện",
        "thị giác máy tính",
        "ocr",
        
        # 3. Kỹ thuật phần mềm, Kiểm thử & Kiến trúc
        "kiểm thử phần mềm",
        "kiểm thử",
        "software testing",
        "kỹ thuật phần mềm",
        "công nghệ phần mềm",
        "kiến trúc phần mềm",
        "mã nguồn mở",
        "microservices",
        "phát triển phần mềm",
        
        # 4. Trí tuệ nhân tạo, Học máy & Dữ liệu
        "trí tuệ nhân tạo",
        "học máy",
        "machine learning",
        "học sâu",
        "deep learning",
        "mạng nơ ron",
        "xử lý ngôn ngữ tự nhiên",
        "nlp",
        "khoa học dữ liệu",
        "khai phá dữ liệu",
        "dữ liệu lớn",
        "big data",
        "kho dữ liệu",
        "cơ sở dữ liệu",
        "database",
        "hệ khuyến nghị",
        "hệ chuyên gia",
        
        # 5. Hệ phân tán, Mạng & Điện toán đám mây
        "hệ phân tán",
        "hệ thống phân tán",
        "tính toán song song",
        "điện toán phân tán",
        "điện toán đám mây",
        "cloud computing",
        "ảo hóa",
        "mạng máy tính",
        "mạng không dây",
        "giao thức mạng",
        "truyền thông dữ liệu",
        
        # 6. Hệ nhúng, Vi điều khiển, IoT & Tự động hóa
        "hệ thống nhúng",
        "hệ nhúng",
        "vi điều khiển",
        "microcontroller",
        "iot",
        "internet vạn vật",
        "robotics",
        "robot",
        "tự động hóa",
        "blockchain",
        "chuỗi khối",
        "hợp đồng thông minh",
        
        # 7. Khoa học máy tính & Thuật toán nền tảng
        "khoa học máy tính",
        "công nghệ thông tin",
        "hệ thống thông tin",
        "thuật toán",
        "cấu trúc dữ liệu",
        "tối ưu hóa",
        "lập trình",
        "website",
        "phần mềm",
        "hệ thống",
        "ứng dụng",
        "trực tuyến",
        "mạng",
        "dữ liệu",
        "mô hình",
        "tự động",
        "bảo mật",
        "android",
        "ios",
        "ứng dụng di động",
        "hệ thống quản lý",
        "phần mềm quản lý",
        "website quản lý"
    ]

    def __init__(
        self,
        output_file: str = "json/ou_it_theses.jsonl",
        checkpoint_file: str = "json/.checkpoint.json",
        pdf_dir: Optional[str] = r"D:\Documents\NCKH- Đồ án\NCKH về NLP, VLM, Vision\Dataset_khoaluan",
        min_delay: float = 1.5,
        max_delay: float = 3.0,
        download_pdf: bool = True,
        only_pdf: bool = False,
        ou_cookie: Optional[str] = None,
        ou_user: Optional[str] = None,
        ou_pass: Optional[str] = None,
        auth_file: str = "ou_auth.json",
        max_year: Optional[int] = 2022
    ):
        super().__init__(
            name="ou_library",
            output_file=output_file,
            checkpoint_file=checkpoint_file,
            pdf_dir=pdf_dir,
            min_delay=min_delay,
            max_delay=max_delay
        )
        self.download_pdf = download_pdf
        self.only_pdf = only_pdf
        self.ou_cookie = ou_cookie
        self.ou_user = ou_user
        self.ou_pass = ou_pass
        self.auth_file = auth_file
        self.max_year = max_year
        self.is_authenticated = False
        
        # Khởi tạo xác thực
        self._init_auth()

    def _init_auth(self):
        """Khởi tạo phiên đăng nhập từ file ou_auth.json hoặc CLI parameters."""
        import os, json
        
        cookie_str = self.ou_cookie
        username = self.ou_user
        password = self.ou_pass

        # Đọc từ file ou_auth.json nếu chưa truyền từ CLI
        if not (cookie_str or (username and password)) and os.path.exists(self.auth_file):
            try:
                with open(self.auth_file, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    c1 = cfg.get("cach_1_cookie_trinh_duyet", {})
                    c2 = cfg.get("cach_2_tai_khoan_truc_tiep", {})
                    if c1.get("cookie"):
                        cookie_str = c1["cookie"].strip()
                    if c2.get("username") and c2.get("password"):
                        username = c2["username"].strip()
                        password = c2["password"].strip()
            except Exception as e:
                self.logger.warning(f"Lỗi đọc file cấu hình {self.auth_file}: {e}")

        # Cách 1: Thiết lập qua Cookie trình duyệt (Khuyên dùng cho SSO)
        if cookie_str:
            self.logger.info("Đang áp dụng Cookie đăng nhập OU...")
            cookie_str = cookie_str.strip()
            if cookie_str.lower().startswith("cookie:"):
                cookie_str = cookie_str[7:].strip()

            import urllib.parse
            for pair in cookie_str.split(";"):
                if "=" in pair:
                    k, v = pair.strip().split("=", 1)
                    k = k.strip()
                    v = v.strip()
                    if k == "ReaderCookie":
                        try:
                            val_decoded = urllib.parse.unquote(v)
                            reader_info = json.loads(val_decoded)
                            token = reader_info.get("Token")
                            if token:
                                self.session.headers.update({"Token": token})
                                self.logger.info(f"Đã nhận diện Token độc giả: {reader_info.get('ReaderName', 'OU Reader')}")
                                self.is_authenticated = True
                            # Chuẩn hóa JSON ASCII để HTTP header không bị lỗi latin-1
                            clean_reader_data = {}
                            for rk, rv in reader_info.items():
                                if isinstance(rv, str):
                                    clean_reader_data[rk] = rv.encode('ascii', 'ignore').decode('ascii')
                                else:
                                    clean_reader_data[rk] = rv
                            v = json.dumps(clean_reader_data, separators=(',', ':'))
                        except Exception as e:
                            self.logger.warning(f"Lỗi phân tích ReaderCookie: {e}")
                    elif k in ["OUToken", "Token"]:
                        self.session.headers.update({"Token": v})
                        self.is_authenticated = True
                    
                    self.session.cookies.set(k, v, domain="thuvien.ou.edu.vn")

            # Đảm bảo có ASP.NET_SessionId
            if "ASP.NET_SessionId" not in self.session.cookies.get_dict():
                try:
                    self.session.get(self.BASE_URL, headers=self.get_random_headers(), timeout=10)
                except Exception:
                    pass
            self.is_authenticated = True
            return

        # Cách 2: Đăng nhập trực tiếp bằng Username / Password (Dành cho tài khoản Reader)
        if username and password:
            self.logger.info(f"Đang thực hiện đăng nhập tài khoản OU ({username})...")
            login_url = f"{self.BASE_URL}/api/Reader/Login"
            payload = {
                "authenticationtype": "password",
                "device": "online",
                "username": username,
                "password": password
            }
            try:
                resp = self.session.post(login_url, json=payload, headers=self.get_random_headers(), timeout=self.timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("Success"):
                        token = data.get("Token")
                        reader_name = data.get("ReaderName", username)
                        self.session.headers.update({"Token": token})
                        self.session.cookies.set("ReaderCookie", json.dumps(data), domain="thuvien.ou.edu.vn")
                        self.logger.info(f"Đăng nhập OU thành công! Chào mừng {reader_name}")
                        self.is_authenticated = True
                    else:
                        self.logger.error(f"Đăng nhập OU thất bại: {data.get('Message')}")
                else:
                    self.logger.error(f"Lỗi gọi API đăng nhập OU: HTTP {resp.status_code}")
            except Exception as e:
                self.logger.error(f"Lỗi khi gửi yêu cầu đăng nhập OU: {e}")

        if not self.is_authenticated and self.download_pdf:
            self.logger.warning(
                "CHƯA ĐĂNG NHẬP TÀI KHOẢN OU! Thư viện ĐH Mở TP.HCM yêu cầu đăng nhập để tải file PDF. "
                "Vui lòng cấu hình file ou_auth.json hoặc truyền --ou-cookie để tải file hoàn chỉnh."
            )

    def _download_pdf(self, raw_id: int, record_id: str, title: str) -> Optional[Dict[str, Any]]:
        """Tìm file và tải toàn văn PDF từ Thư viện ĐH Mở TP.HCM."""
        url_file = f"{self.RECORD_CONTENT_API}?id={raw_id}&type=file"
        try:
            r = self.session.get(url_file, headers=self.get_random_headers(), timeout=self.timeout)
            if r.status_code != 200:
                return None
            data = r.json()
            if not isinstance(data, list) or not data:
                return None

            list_files = data[0].get("ListFiles", [])
            for file_info in list_files:
                file_id = file_info.get("RecordFileID")
                doc_code = file_info.get("RecordDocumentCode")  # GUID dùng cho RecordFileHandler
                save_name = file_info.get("SaveFileName", f"{raw_id}.pdf")
                if not file_id and not doc_code:
                    continue

                pdf_dir = self.pdf_dir or os.path.join("pdf", "ou")
                os.makedirs(pdf_dir, exist_ok=True)
                safe_title = re.sub(r"[^\w\.-]", "_", title)[:60].strip("_")
                if save_name and save_name.lower().endswith(".pdf"):
                    clean_save = re.sub(r"[^\w\.-]", "_", save_name)
                    pdf_filename = clean_save if clean_save.startswith(str(raw_id)) else f"{raw_id}_{clean_save}"
                else:
                    pdf_filename = f"{raw_id}_{safe_title}.pdf"
                save_path = os.path.join(pdf_dir, pdf_filename)
                alt_path = os.path.join(pdf_dir, save_name) if save_name else None

                # Kiểm tra xem file đã tồn tại trên đĩa chưa (tránh gọi request không cần thiết)
                existing_file = None
                if os.path.exists(save_path) and os.path.getsize(save_path) > 1000:
                    existing_file = save_path
                elif alt_path and os.path.exists(alt_path) and os.path.getsize(alt_path) > 1000:
                    existing_file = alt_path

                target_param = doc_code if doc_code else file_id
                download_url = f"{self.BASE_URL}/Services/RecordFileHandler.ashx?id={target_param}&type=book"

                if existing_file:
                    size = os.path.getsize(existing_file)
                    # Kiểm tra nhị phân siêu tốc text layer (tránh treo khi đọc file scan lớn)
                    if size > 35 * 1024 * 1024:
                        self.logger.info(f"[BỎ QUA FILE SCAN TRÊN ĐĨA] {os.path.basename(existing_file)} (>35MB ảnh scan).")
                        return None
                    try:
                        with open(existing_file, "rb") as f_chk:
                            font_count = f_chk.read(min(size, 500000)).count(b"/Font")
                        if font_count < 2:
                            self.logger.info(f"[BỎ QUA FILE SCAN TRÊN ĐĨA] {os.path.basename(existing_file)} không có font chữ (ảnh scan).")
                            return None
                    except Exception:
                        return None

                    self.logger.info(f"[ĐÃ CÓ TRÊN ĐĨA & TEXT OK] {os.path.basename(existing_file)} ({size:,} bytes, fonts={font_count})")
                    return {
                        "pdf_path": existing_file,
                        "pdf_filename": os.path.basename(existing_file),
                        "pdf_size_bytes": size,
                        "pdf_download_url": download_url
                    }

                # Kích hoạt quyền đọc file qua CheckPermit nếu có Token
                if "Token" in self.session.headers and file_id:
                    try:
                        self.session.post(
                            f"{self.BASE_URL}/api/ebook/CheckPermit",
                            json={"type": "book", "id": file_id},
                            headers=self.get_random_headers({"Content-Type": "application/json"}),
                            timeout=10
                        )
                    except Exception:
                        pass

                # Tải file qua RecordFileHandler (ưu tiên dùng doc_code GUID của OU)
                headers = self.get_random_headers({
                    "Referer": f"{self.BASE_URL}/module/chi-tiet-sach?RecordID={raw_id}"
                })
                r_c = self.session.get(download_url, headers=headers, stream=True, timeout=45)

                if r_c.status_code == 200:
                    content_iter = r_c.iter_content(chunk_size=32768)
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
                                        # Kiểm tra nhanh 100KB đầu xem có font chữ không
                                        if first_100k.count(b"/Font") < 2:
                                            is_early_scan = True
                                            break

                        if is_early_scan or first_100k.count(b"/Font") < 2:
                            self.logger.warning(f"[HỦY TẢI SỚM - BẢN SCAN] {pdf_filename} 100KB đầu không có font text. Hủy tải ngay!")
                            try:
                                os.remove(save_path)
                            except Exception:
                                pass
                            return None
                                
                        size = os.path.getsize(save_path)

                        # KIỂM TRA CHẤT LƯỢNG TEXT: Đảm bảo PDF trích xuất được text, loại bỏ scan ảnh thuần
                        if size > 35 * 1024 * 1024:
                            self.logger.warning(f"[LOẠI BỎ - BẢN SCAN ẢNH THUẦN] {pdf_filename} dung lượng quá lớn ({size:,} bytes). Đang xóa file!")
                            try:
                                os.remove(save_path)
                            except Exception:
                                pass
                            return None

                        if size < 600 * 1024:
                            self.logger.warning(f"[LOẠI BỎ - DUNG LƯỢNG < 600KB] {pdf_filename} ({size//1024} KB). Xóa file!")
                            try:
                                os.remove(save_path)
                            except Exception:
                                pass
                            return None

                        # Quality Gate nghiêm ngặt: Số trang >= 35 và Text Layer >= 20.000 ký tự
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

                        self.logger.info(
                            f"[ĐÃ TẢI DIGITAL TEXT PDF OU] {pdf_filename} ({size:,} bytes, {num_pages} trang, text {len(sample_text):,} chars) -> {save_path}"
                        )
                        return {
                            "pdf_path": save_path,
                            "pdf_filename": pdf_filename,
                            "pdf_size_bytes": size,
                            "pdf_download_url": download_url
                        }
                    else:
                        self.logger.warning(f"File tải về từ RecordID {raw_id} không phải định dạng PDF.")
        except Exception as e:
            self.logger.debug(f"Lỗi tải PDF cho RecordID {raw_id}: {e}")
        return None

    def _clean_title(self, raw_title: str) -> str:
        """Làm sạch tiêu đề, tách bỏ phần tác giả sau dấu gạch chéo '/'."""
        if not raw_title:
            return ""
        # Định dạng chuẩn MARC 245: 'Tên đề tài : Phụ đề / Tác giả'
        title = raw_title.strip()
        if " / " in title:
            title = title.split(" / ")[0].strip()
        return title

    def _clean_advisors(self, raw_700: List[Any]) -> List[str]:
        """Bóc tách người hướng dẫn từ trường MARC 700."""
        advisors = []
        for item in raw_700:
            for line in str(item).replace("<br/>", "\n").replace("<br>", "\n").split("\n"):
                line = re.sub(r"^\+\s*", "", line.strip())
                if "hướng dẫn" in line.lower():
                    name = re.sub(r"[,;]?\s*(?:người\s+)?hướng\s+dẫn.*", "", line, flags=re.IGNORECASE).strip()
                    name = re.sub(r"\[hướng\s+dẫn\].*", "", name, flags=re.IGNORECASE).strip()
                    name = re.sub(r"\(hướng\s+dẫn\).*", "", name, flags=re.IGNORECASE).strip()
                    if name and name not in advisors:
                        advisors.append(name)
        return advisors

    def _clean_keywords(self, raw_650: List[Any]) -> List[str]:
        """Làm sạch và chuẩn hóa danh sách từ khóa từ trường MARC 650."""
        keywords = []
        for item in raw_650:
            for part in str(item).replace("<br/>", "\n").replace("<br>", "\n").split("\n"):
                clean_k = re.sub(r"^\+\s*", "", part.strip()).strip()
                if clean_k and clean_k not in keywords:
                    keywords.append(clean_k)
        return keywords

    def _extract_year(self, raw_260: List[Any], raw_502: Optional[List[Any]] = None, title: str = "") -> Optional[int]:
        """Trích xuất năm phát hành từ trường MARC 260, 502 hoặc tiêu đề."""
        for item in (raw_260 or []):
            m = re.search(r"\b(19\d{2}|20\d{2})\b", str(item))
            if m:
                return int(m.group(1))
        for item in (raw_502 or []):
            m = re.search(r"\b(19\d{2}|20\d{2})\b", str(item))
            if m:
                return int(m.group(1))
        if title:
            m = re.search(r"\b(19\d{2}|20\d{2})\b", title)
            if m:
                return int(m.group(1))
        return None

    def _fetch_record_details(self, record_id: int) -> Optional[Dict[str, Any]]:
        """Lấy chi tiết bản ghi qua API GetRecordContent (MARC21 fields)."""
        url = f"{self.RECORD_CONTENT_API}?id={record_id}&type=field"
        headers = self.get_random_headers()
        try:
            resp = self.session.get(url, headers=headers, timeout=self.timeout)
            if resp.status_code == 200:
                fields_data = resp.json()
                field_map = {}
                for f in fields_data:
                    tag = str(f.get("Tag", ""))
                    val = f.get("Value")
                    field_map[tag] = val
                return field_map
        except Exception as e:
            self.logger.warning(f"Lỗi khi lấy chi tiết RecordID {record_id}: {e}")
        return None

    def _search_ebook_local(self, group_id: int, query: str, index: int, count: int = 20) -> List[Dict[str, Any]]:
        """Gọi API SearchLocal của OU Library."""
        payload = {
            "RecordGroupId": group_id,
            "FileType": "book",
            "GroupType": 0,
            "OlogyId": "",
            "Search": query,
            "Index": index,
            "Count": count,
            "CurriculumId": ""
        }
        headers = self.get_random_headers({"Content-Type": "application/json"})
        try:
            resp = self.session.post(
                self.SEARCH_LOCAL_API,
                json=payload,
                headers=headers,
                timeout=self.timeout
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
        except Exception as e:
            self.logger.error(f"Lỗi gọi SearchLocal (group={group_id}, query='{query}', index={index}): {e}")
        return []

    def _process_item(self, item: Dict[str, Any], group_name: str, group_id: int) -> Optional[Dict[str, Any]]:
        """Xử lý 1 bản ghi đề tài OU với cơ chế khử trùng đa tầng & kiểm định chất lượng."""
        raw_id = item.get("RecordID")
        if not raw_id:
            return None
        record_id = f"ou_{raw_id}"

        # 1. Kiểm tra ID sơ bộ
        with self.lock:
            if record_id in self.seen_ids or str(raw_id) in self.seen_ids:
                return None

        # TỐI ƯU 1: Bỏ qua các bản ghi không có file số đính kèm (0ms)
        num_files = item.get("NumberOfFiles")
        if num_files is not None and num_files == 0:
            return None

        # TỐI ƯU 2: Kiểm tra năm xuất bản từ kết quả tìm kiếm (0ms)
        raw_py = item.get("PublishYear")
        if raw_py and str(raw_py).isdigit():
            py = int(raw_py)
            if self.max_year and py > self.max_year:
                return None

        quick_raw_title = str(item.get("Title1") or item.get("Title") or "")
        quick_title = self._clean_title(quick_raw_title)
        if not quick_title:
            return None

        # TỐI ƯU 3: Bỏ qua ngay các bài báo, tạp chí khoa học, kỷ yếu
        quick_lower = quick_title.lower()
        if any(bad in quick_lower for bad in ["tạp chí", "bài báo", "kỷ yếu", "hội thảo", "journal", "proceedings", "conference"]):
            return None

        # Bắt buộc có dấu tiếng Việt
        vn_char_pattern = re.compile(r'[àáảãạăắằẳẵặâấầẩẫậđèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵ]')
        if not vn_char_pattern.search(quick_lower):
            return None

        # TỐI ƯU 4: Lọc chuyên ngành CNTT nhanh qua tiêu đề, Subject & Dewey (0ms)
        # Loại bỏ ngay 95% tài liệu ngành khác trước khi tốn tài nguyên so khớp mờ!
        quick_subjects = [str(s) for s in (item.get("Subject") or [])]
        dewey = str(item.get("Dewey") or "").strip()
        is_dewey_it = any(dewey.startswith(pfx) for pfx in ["004", "005", "006", "621.39"])
        is_cand, _ = is_it_topic(title=quick_title, keywords=quick_subjects)
        if not (is_cand or is_dewey_it):
            return None

        # 2. ĐỐI VỚI CÁC ĐỀ TÀI CNTT TIỀM NĂNG: Khóa lock nguyên tử và kiểm tra khử trùng tiêu đề
        core_quick = self.clean_core_title(quick_title)
        with self.lock:
            if record_id in self.seen_ids or str(raw_id) in self.seen_ids:
                return None
            if self.is_duplicate_title(quick_title):
                self.seen_ids.add(record_id)
                self.seen_ids.add(str(raw_id))
                return None
            # Đánh dấu giữ chỗ ID và tiêu đề ngay lập tức để tránh thread khác cào trùng
            self.seen_ids.add(record_id)
            self.seen_ids.add(str(raw_id))
            norm_q = self.normalize_title_for_dedup(quick_title)
            if norm_q:
                self.reserved_titles.add(norm_q)
            if core_quick:
                self.reserved_core_titles.add(core_quick)

        # Lấy chi tiết MARC21 (Tag 100, 245, 502, 520, 650, 700...)
        self.jitter_delay()
        field_map = self._fetch_record_details(int(raw_id))
        if not field_map:
            return None

        raw_title = field_map.get("245")
        if isinstance(raw_title, list) and raw_title:
            full_raw_title = str(raw_title[0])
        else:
            full_raw_title = quick_raw_title
        title = self._clean_title(full_raw_title)

        if not vn_char_pattern.search(title.lower()):
            return None

        # Khử trùng tiêu đề lần 2 với tiêu đề đầy đủ từ MARC21
        core_marc = self.clean_core_title(title)
        with self.lock:
            if self.is_duplicate_title(title):
                self.logger.info(f"[TRÙNG TIÊU ĐỀ MARC] Bỏ qua đề tài: {title[:50]}...")
                return None
            norm_m = self.normalize_title_for_dedup(title)
            if norm_m:
                self.reserved_titles.add(norm_m)
            if core_marc:
                self.reserved_core_titles.add(core_marc)

        # Kiểm tra Dewey chi tiết từ MARC 082 / 090 (Loại trừ ngành Kinh tế, Quản trị, Sinh học...)
        raw_082 = field_map.get("082", []) or field_map.get("090", [])
        detail_dewey = str(raw_082[0]).strip() if raw_082 else dewey
        non_it_dewey_prefixes = ["658", "657", "330", "340", "570", "660", "690", "300", "400", "420"]
        if any(detail_dewey.startswith(pfx) for pfx in non_it_dewey_prefixes):
            return None

        # Tác giả & Người hướng dẫn
        raw_authors = field_map.get("100", [])
        authors = [str(a).strip() for a in raw_authors if a]
        if not authors and item.get("Author"):
            authors = [str(a).strip() for a in item.get("Author")]

        advisors = self._clean_advisors(field_map.get("700", []))
        raw_502 = field_map.get("502", [])
        faculty = str(raw_502[0]).strip() if raw_502 else "Trường Đại học Mở TP.HCM"

        # Kiểm tra Khoa/Ngành từ Tag 502 (Loại trừ chắc chắn các khoa không phải CNTT)
        norm_faculty = faculty.lower()
        non_it_faculties = [
            "quản trị kinh doanh", "quản trị nhân lực", "marketing", "kinh tế",
            "tài chính", "ngân hàng", "kế toán", "kiểm toán", "luật", "luật kinh tế",
            "công nghệ sinh học", "sinh học", "xây dựng", "kỹ thuật công trình",
            "xã hội học", "công tác xã hội", "ngôn ngữ anh", "ngôn ngữ trung",
            "du lịch", "đông nam á"
        ]
        if any(bad_f in norm_faculty for bad_f in non_it_faculties):
            if not any(good in norm_faculty for good in ["công nghệ thông tin", "tin học", "khoa học máy tính", "toán - tin"]):
                return None

        raw_520 = field_map.get("520", [])
        abstract = str(raw_520[0]).strip() if raw_520 else ""
        keywords = self._clean_keywords(field_map.get("650", []))

        # Năm xuất bản
        year = self._extract_year(field_map.get("260", []), field_map.get("502", []), title)
        if self.max_year and year and year > self.max_year:
            return None

        # Lọc đề tài CNTT
        is_it, matched_tags = is_it_topic(
            title=title,
            abstract=abstract,
            keywords=keywords,
            faculty=faculty
        )
        if not (is_it or is_dewey_it or any(detail_dewey.startswith(pfx) for pfx in ["004", "005", "006", "621.39"])):
            return None

        # Khử trùng tác giả với kho Dataset_khoaluan
        if hasattr(self, "external_authors") and self.external_authors:
            for author in authors:
                clean_a = self.normalize_title_for_dedup(author)
                if clean_a and clean_a in self.external_authors:
                    self.logger.info(f"[TRÙNG TÁC GIẢ] Bỏ qua đề tài của '{author}'.")
                    return None

        # Tải file PDF nếu được bật
        pdf_info = None
        if getattr(self, "download_pdf", False):
            pdf_info = self._download_pdf(raw_id, record_id, title)
            if getattr(self, "only_pdf", False) and not pdf_info:
                return None

        degree_val = group_name
        if group_id == 0:
            lt = title.lower()
            if "khóa luận" in lt:
                degree_val = "Khóa luận"
            elif "luận văn" in lt:
                degree_val = "Luận văn"
            elif "luận án" in lt:
                degree_val = "Luận án"
            elif "nghiên cứu" in lt:
                degree_val = "Công trình nghiên cứu"
            else:
                degree_val = "Đồ án / Luận văn"

        record = {
            "id": record_id,
            "source": "Thư viện Trường Đại học Mở TP.HCM (OU)",
            "title": title,
            "authors": authors,
            "advisors": advisors,
            "year": year,
            "degree": degree_val,
            "school_or_faculty": faculty,
            "keywords": keywords,
            "abstract": abstract,
            "matched_it_tags": matched_tags,
            "url": f"https://thuvien.ou.edu.vn/module/chi-tiet-sach?RecordID={raw_id}",
            "has_full_pdf": pdf_info is not None,
            "pdf_path": pdf_info["pdf_path"] if pdf_info else None,
            "pdf_size_bytes": pdf_info["pdf_size_bytes"] if pdf_info else None,
            "pdf_download_url": pdf_info["pdf_download_url"] if pdf_info else None,
            "crawl_time": datetime.now().isoformat()
        }
        return record

    def _process_batch_items(self, items: List[Dict[str, Any]], group_name: str, group_id: int, limit: Optional[int], current_crawled: int) -> List[Dict[str, Any]]:
        """Xử lý song song danh sách bản ghi bằng ThreadPoolExecutor."""
        if not items:
            return []
        valid_records = []
        max_workers = min(5, len(items))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_item = {executor.submit(self._process_item, it, group_name, group_id): it for it in items}
            for fut in concurrent.futures.as_completed(future_to_item):
                try:
                    rec = fut.result()
                    if rec:
                        valid_records.append(rec)
                        if limit is not None and (current_crawled + len(valid_records)) >= limit:
                            break
                except Exception as e:
                    self.logger.debug(f"Lỗi worker OU: {e}")
        return valid_records

    def crawl(self, limit: Optional[int] = None) -> int:
        """
        Cào khóa luận & luận văn CNTT từ Thư viện ĐH Mở TP.HCM:
        Giai đoạn 1: Quét vét cạn trực tiếp toàn bộ danh mục Khóa luận (13), Luận văn (3), NCKH (8) <= 2022.
        Giai đoạn 2: Quét theo các bộ từ khóa chuyên ngành CNTT để bổ khuyết các đề tài phân lớp khác.
        """
        self.logger.info(f"Bắt đầu cào dữ liệu Thư viện ĐH Mở (Mục tiêu: {limit or 'Không giới hạn'} bản ghi, Multi-threaded)...")
        crawled_count = 0
        batch_size = 100

        # GIAI ĐOẠN 1: Quét vét cạn trực tiếp toàn bộ danh mục học thuật tại các dải chỉ mục <= 2022
        sweep_groups = [
            (13, "Khóa luận tốt nghiệp", 2400),
            (3, "Luận văn thạc sĩ", 500),
            (8, "Công trình NCKH", 0),
            (14, "Luận án tiến sĩ", 0)
        ]

        for group_id, group_name, start_idx in sweep_groups:
            if limit is not None and crawled_count >= limit:
                break
            self.logger.info(f"[OU Sweep] Bắt đầu quét danh mục: '{group_name}' (GroupId: {group_id}, Bắt đầu từ index {start_idx})...")
            
            index = start_idx
            while True:
                if limit is not None and crawled_count >= limit:
                    break

                results = self._search_ebook_local(group_id, "", index, batch_size)
                if not results or (len(results) == 1 and str(results[0].get("RecordID")) == "-1"):
                    break

                total_records = results[0].get("TotalRecords", 0) if results else 0
                self.logger.info(f"[OU Sweep] {group_name}: đang quét trang index {index}/{total_records} (Đã thu thập: {crawled_count}/{limit or 'Full'})...")
                
                # Kiểm tra nhanh: nếu toàn bộ trang là năm mới > 2022 thì bỏ qua
                has_cand_year = False
                for it in results:
                    py = it.get("PublishYear")
                    if py and str(py).isdigit():
                        if int(py) <= (self.max_year or 2022):
                            has_cand_year = True
                            break
                    else:
                        has_cand_year = True
                        break

                if has_cand_year:
                    batch_records = self._process_batch_items(results, group_name, group_id, limit, crawled_count)
                    for rec in batch_records:
                        self.append_record(rec)
                        crawled_count += 1
                        self.logger.info(f"[OU Sweep - MỚI ĐÃ LƯU] #{crawled_count}: {rec['title'][:55]} (Năm: {rec.get('year')})")
                        if limit is not None and crawled_count >= limit:
                            break

                    if batch_records:
                        self.save_checkpoint()

                index += batch_size
                if index >= total_records:
                    break

        # GIAI ĐOẠN 2: Quét theo các từ khóa CNTT trọng điểm
        if limit is None or crawled_count < limit:
            self.logger.info("[OU Keyword Search] Bắt đầu giai đoạn 2: Quét bổ trợ theo từ khóa CNTT...")
            for group_id, group_name in self.RECORD_GROUPS.items():
                if limit is not None and crawled_count >= limit:
                    break
                    
                for term in self.IT_SEARCH_TERMS:
                    if limit is not None and crawled_count >= limit:
                        break

                    index = 0
                    max_pages_per_term = 30

                    for page_idx in range(max_pages_per_term):
                        if limit is not None and crawled_count >= limit:
                            break

                        results = self._search_ebook_local(group_id, term, index, batch_size)
                        if not results or (len(results) == 1 and str(results[0].get("RecordID")) == "-1"):
                            break

                        total_records = results[0].get("TotalRecords", 0) if results else 0
                        batch_records = self._process_batch_items(results, group_name, group_id, limit, crawled_count)
                        for rec in batch_records:
                            self.append_record(rec)
                            crawled_count += 1
                            self.logger.info(f"[OU Keyword - MỚI ĐÃ LƯU] #{crawled_count}: {rec['title'][:55]} (Năm: {rec.get('year')})")
                            if limit is not None and crawled_count >= limit:
                                break

                        if batch_records:
                            self.save_checkpoint()

                        index += batch_size
                        if index >= total_records:
                            break

        self.save_checkpoint()
        self.logger.info(f"Hoàn thành thu thập OU Library! Đã lưu {crawled_count} bản ghi mới vào {self.output_file}")
        return crawled_count
