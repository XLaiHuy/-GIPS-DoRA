# -*- coding: utf-8 -*-
"""
github_crawler.py: Crawler thu thập Khóa luận, Đồ án tốt nghiệp và Luận văn Thạc sĩ CNTT
toàn văn từ các GitHub repositories của sinh viên các trường đại học tại Việt Nam (<= 2022).
Đảm bảo chuẩn bố cục đồ án (>= 25 trang, >= 10.000 ký tự text layer số hóa, 0% scan, 100% tiếng Việt).
"""
import os
import re
import time
import json
import requests
import urllib.parse
import threading
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple, Set
from pypdf import PdfReader
from .base_crawler import BaseCrawler
from .filters import is_it_topic


class GitHubCrawler(BaseCrawler):
    """Crawler thu thập đồ án và khóa luận tốt nghiệp CNTT từ GitHub."""

    SEARCH_API = "https://api.github.com/search/repositories"
    
    # Danh mục truy vấn tìm kiếm đồ án/khóa luận có mốc thời gian <= 2022-12-31
    SEARCH_QUERIES = [
        # 1. Từ khóa cốt lõi tiếng Việt có dấu
        '"khóa luận tốt nghiệp" created:<=2022-12-31',
        '"đồ án tốt nghiệp" created:<=2022-12-31',
        '"luận văn tốt nghiệp" created:<=2022-12-31',
        '"luận văn thạc sĩ" "công nghệ thông tin" created:<=2022-12-31',
        '"luận văn thạc sĩ" "khoa học máy tính" created:<=2022-12-31',
        '"báo cáo khóa luận" created:<=2022-12-31',
        '"báo cáo đồ án tốt nghiệp" created:<=2022-12-31',

        # 2. Từ khóa không dấu
        '"khoa luan tot nghiep" created:<=2022-12-31',
        '"do an tot nghiep" created:<=2022-12-31',
        '"luan van tot nghiep" created:<=2022-12-31',
        '"luan van thac si" cntt created:<=2022-12-31',
        '"bao cao khoa luan" created:<=2022-12-31',
        '"bao cao do an tot nghiep" created:<=2022-12-31',

        # 3. Kết hợp chuyên ngành CNTT
        '"khóa luận tốt nghiệp" "công nghệ thông tin" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "công nghệ thông tin" created:<=2022-12-31',
        '"khóa luận tốt nghiệp" "kỹ thuật phần mềm" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "kỹ thuật phần mềm" created:<=2022-12-31',
        '"khóa luận tốt nghiệp" "khoa học máy tính" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "khoa học máy tính" created:<=2022-12-31',
        '"khóa luận tốt nghiệp" "hệ thống thông tin" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "an toàn thông tin" created:<=2022-12-31',
        '"khóa luận tốt nghiệp" "trí tuệ nhân tạo" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "xử lý ảnh" created:<=2022-12-31',

        # 4. Truy vấn theo các trường đại học hàng đầu
        '"khóa luận tốt nghiệp" "bách khoa" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "bách khoa" created:<=2022-12-31',
        '"khóa luận tốt nghiệp" "hcmus" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "uit" created:<=2022-12-31',
        '"khóa luận tốt nghiệp" "uit" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "hust" created:<=2022-12-31',
        '"khóa luận tốt nghiệp" "hust" created:<=2022-12-31',
        '"khóa luận tốt nghiệp" "đại học công nghệ" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "sư phạm kỹ thuật" created:<=2022-12-31',
        '"khóa luận tốt nghiệp" "bưu chính" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "bưu chính" created:<=2022-12-31',
        '"khóa luận tốt nghiệp" "ptit" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "fpt" created:<=2022-12-31',
        '"khóa luận tốt nghiệp" "cần thơ" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "cần thơ" created:<=2022-12-31',
        '"khóa luận tốt nghiệp" "công nghiệp" created:<=2022-12-31',

        # 5. Topic và viết tắt
        'kltn cntt created:<=2022-12-31',
        'datn cntt created:<=2022-12-31',
        'topic:kltn created:<=2022-12-31',
        'topic:datn created:<=2022-12-31',
        'topic:khoa-luan-tot-nghiep created:<=2022-12-31',
        'topic:do-an-tot-nghiep created:<=2022-12-31',
        'topic:graduation-thesis created:<=2022-12-31',

        # 6. Các từ khóa viết tắt quy mô lớn (> 4.000 repos)
        'datn created:<=2022-12-31',
        'do-an-tot-nghiep created:<=2022-12-31',
        'kltn created:<=2022-12-31',
        'khoa-luan-tot-nghiep created:<=2022-12-31',
        'luan-van-tot-nghiep created:<=2022-12-31',
        'kltn-cntt created:<=2022-12-31',
        'datn-cntt created:<=2022-12-31',
        'do-an-cntt created:<=2022-12-31',
        'khoa-luan-cntt created:<=2022-12-31',

        # 7. Truy vấn theo công nghệ & chuyên ngành sâu (AI, ML, CV, NLP, Web/App)
        '"đồ án tốt nghiệp" "machine learning" created:<=2022-12-31',
        '"khóa luận tốt nghiệp" "machine learning" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "deep learning" created:<=2022-12-31',
        '"khóa luận tốt nghiệp" "deep learning" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "blockchain" created:<=2022-12-31',
        '"khóa luận tốt nghiệp" "blockchain" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "xử lý ngôn ngữ tự nhiên" created:<=2022-12-31',
        '"khóa luận tốt nghiệp" "xử lý ngôn ngữ tự nhiên" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "nlp" created:<=2022-12-31',
        '"khóa luận tốt nghiệp" "nlp" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "computer vision" created:<=2022-12-31',
        '"khóa luận tốt nghiệp" "thị giác máy tính" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "iot" created:<=2022-12-31',
        '"khóa luận tốt nghiệp" "iot" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "microservices" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "spring boot" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "reactjs" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "flutter" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "android" created:<=2022-12-31',

        # 8. Mở rộng thêm các trường đại học tại Việt Nam
        '"khóa luận tốt nghiệp" "hutech" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "hutech" created:<=2022-12-31',
        '"khóa luận tốt nghiệp" "tdtu" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "tôn đức thắng" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "đại học đà nẵng" created:<=2022-12-31',
        '"khóa luận tốt nghiệp" "đại học đà nẵng" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "đại học huế" created:<=2022-12-31',
        '"khóa luận tốt nghiệp" "đại học huế" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "học viện kỹ thuật quân sự" created:<=2022-12-31',
        '"luận văn tốt nghiệp" "mta" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "học viện công nghệ bưu chính viễn thông" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "đại học sài gòn" created:<=2022-12-31',
        '"khóa luận tốt nghiệp" "đại học sài gòn" created:<=2022-12-31',
        '"đồ án tốt nghiệp" "thăng long" created:<=2022-12-31',
        '"khóa luận tốt nghiệp" "thăng long" created:<=2022-12-31',

        # 9. Báo cáo tốt nghiệp & Capstone chuẩn kỹ sư
        '"báo cáo tốt nghiệp" "công nghệ thông tin" created:<=2022-12-31',
        '"báo cáo tốt nghiệp" "kỹ thuật phần mềm" created:<=2022-12-31',
        '"báo cáo tốt nghiệp" "khoa học máy tính" created:<=2022-12-31',
        '"capstone project" "fpt" created:<=2022-12-31',
        '"capstone project" "cntt" created:<=2022-12-31',
        '"graduation thesis" "vietnam" created:<=2022-12-31',
        '"graduation thesis" "hust" created:<=2022-12-31',
        '"graduation thesis" "uit" created:<=2022-12-31',
        '"graduation thesis" "hcmus" created:<=2022-12-31',
        '"graduation thesis" "hcmut" created:<=2022-12-31',

        # 10. Tên repo viết tắt chuẩn sinh viên các trường kỹ thuật
        'datn-hust created:<=2022-12-31',
        'kltn-hust created:<=2022-12-31',
        'datn-uet created:<=2022-12-31',
        'kltn-uet created:<=2022-12-31',
        'datn-uit created:<=2022-12-31',
        'kltn-uit created:<=2022-12-31',
        'datn-hcmut created:<=2022-12-31',
        'datn-bku created:<=2022-12-31',
        'kltn-hcmus created:<=2022-12-31',
        'datn-ptit created:<=2022-12-31',
        'kltn-ptit created:<=2022-12-31',
        'datn-ctu created:<=2022-12-31',
        'kltn-ctu created:<=2022-12-31',
        'datn-ute created:<=2022-12-31',
        'kltn-ute created:<=2022-12-31',
        'datn-haui created:<=2022-12-31',
        'datn-iuh created:<=2022-12-31',
        'doantotnghiep created:<=2022-12-31',
        'khoaluantotnghiep created:<=2022-12-31',
        'luanvantotnghiep created:<=2022-12-31',
        'baocaototnghiep created:<=2022-12-31',
        'luanvanthacsi created:<=2022-12-31',
        'lvtn created:<=2022-12-31',
        '"đồ án kỹ sư" created:<=2022-12-31',
        '"luận văn kỹ sư" created:<=2022-12-31',
        '"đồ án tốt nghiệp kỹ sư" created:<=2022-12-31',
        '"báo cáo tốt nghiệp kỹ sư" created:<=2022-12-31',
        '"khóa luận tốt nghiệp cử nhân" created:<=2022-12-31',
        '"luận văn tốt nghiệp thạc sĩ" created:<=2022-12-31',

        # 11. Các truy vấn mở rộng các trường đại học (Thẩm định mốc năm <= 2022 nghiêm ngặt trên trang bìa PDF)
        'datn hust',
        'kltn hust',
        'datn uit',
        'kltn uit',
        'datn hcmut',
        'kltn hcmus',
        'datn uet',
        'kltn uet',
        'datn ptit',
        'kltn ptit',
        'datn ctu',
        'kltn ctu',
        'datn ute',
        'kltn ute',
        'datn haui',
        'datn iuh',
        'datn tdtu',
        'kltn tdtu',
        'datn kma',
        'kltn kma',
        'datn mta',
        'kltn mta',
        'datn sgu',
        'kltn sgu',
        'datn nuce',
        'datn tlu',
        'datn vnuk',
        'datn fpt',
        'kltn fpt',
        '"báo cáo đồ án tốt nghiệp"',
        '"báo cáo khóa luận tốt nghiệp"',
        '"báo cáo tốt nghiệp" cntt',
        '"luận văn thạc sĩ" cntt',
        '"luận văn thạc sĩ" "khoa học máy tính"',
        '"luận văn thạc sĩ" "kỹ thuật phần mềm"',
        'bao_cao_datn',
        'bao_cao_kltn',
        'baocao_datn',
        'baocao_kltn',
        'cuon_bao_cao_datn',
        'cuon_bao_cao_kltn',
        'do_an_tot_nghiep cntt',
        'khoa_luan_tot_nghiep cntt',
        'luan_van_tot_nghiep cntt',
        '"đồ án tốt nghiệp" "hệ thống thông tin"',
        '"khóa luận tốt nghiệp" "hệ thống thông tin"',
        '"đồ án tốt nghiệp" "an toàn thông tin"',
        '"khóa luận tốt nghiệp" "an toàn thông tin"',
        '"đồ án tốt nghiệp" "khoa học dữ liệu"',
        '"khóa luận tốt nghiệp" "khoa học dữ liệu"',
        '"đồ án tốt nghiệp" "trí tuệ nhân tạo"',
        '"khóa luận tốt nghiệp" "trí tuệ nhân tạo"',
        '"đồ án tốt nghiệp" "xử lý ảnh"',
        '"khóa luận tốt nghiệp" "xử lý ảnh"',
        '"đồ án tốt nghiệp" "xử lý ngôn ngữ tự nhiên"',
        '"khóa luận tốt nghiệp" "xử lý ngôn ngữ tự nhiên"',
        '"đồ án tốt nghiệp" "thị giác máy tính"',
        '"khóa luận tốt nghiệp" "thị giác máy tính"',
        '"đồ án tốt nghiệp" "mạng máy tính"',
        '"khóa luận tốt nghiệp" "mạng máy tính"',
        '"đồ án tốt nghiệp" "kỹ thuật máy tính"',
        '"khóa luận tốt nghiệp" "kỹ thuật máy tính"',
        '"đồ án tốt nghiệp" "công nghệ phần mềm"',
        '"khóa luận tốt nghiệp" "công nghệ phần mềm"',
        '"đồ án tốt nghiệp" "khoa học máy tính"',
        '"khóa luận tốt nghiệp" "khoa học máy tính"',

        # 12. Mã học phần đồ án / khóa luận chuẩn các trường đại học lớn
        'CS499 created:<=2022-12-31',
        'IT4990 created:<=2022-12-31',
        'INT4012 created:<=2022-12-31',
        'INT3507 created:<=2022-12-31',
        'CO4999 created:<=2022-12-31',
        'CT550 created:<=2022-12-31',
        'SE499 created:<=2022-12-31',
        'IS499 created:<=2022-12-31',
        'kltn-soict created:<=2022-12-31',
        'datn-soict created:<=2022-12-31',
        'kltn-fit created:<=2022-12-31',
        'datn-fit created:<=2022-12-31',
        'kltn-se created:<=2022-12-31',
        'datn-se created:<=2022-12-31',
        'kltn-cs created:<=2022-12-31',
        'datn-cs created:<=2022-12-31',
        'kltn-is created:<=2022-12-31',
        'datn-is created:<=2022-12-31',

        # 13. Truy vấn đồ án theo công nghệ chuyên sâu (AI / Vision / NLP / Web / Mobile)
        '"kltn" yolo created:<=2022-12-31',
        '"datn" yolo created:<=2022-12-31',
        '"kltn" resnet created:<=2022-12-31',
        '"datn" resnet created:<=2022-12-31',
        '"kltn" bert created:<=2022-12-31',
        '"datn" bert created:<=2022-12-31',
        '"kltn" transformer created:<=2022-12-31',
        '"datn" transformer created:<=2022-12-31',
        '"kltn" cnn created:<=2022-12-31',
        '"datn" cnn created:<=2022-12-31',
        '"kltn" lstm created:<=2022-12-31',
        '"datn" lstm created:<=2022-12-31',
        '"kltn" "segmentation" created:<=2022-12-31',
        '"datn" "segmentation" created:<=2022-12-31',
        '"kltn" "classification" created:<=2022-12-31',
        '"datn" "classification" created:<=2022-12-31',
        '"kltn" "recommendation" created:<=2022-12-31',
        '"datn" "recommendation" created:<=2022-12-31',
        '"kltn" "blockchain" created:<=2022-12-31',
        '"datn" "blockchain" created:<=2022-12-31',
        '"kltn" "smart contract" created:<=2022-12-31',
        '"datn" "smart contract" created:<=2022-12-31',
        '"kltn" "face recognition" created:<=2022-12-31',
        '"datn" "face recognition" created:<=2022-12-31',
        '"kltn" "speech" created:<=2022-12-31',
        '"datn" "speech" created:<=2022-12-31',
        '"kltn" "chatbot" created:<=2022-12-31',
        '"datn" "chatbot" created:<=2022-12-31',
        '"kltn" "ocr" created:<=2022-12-31',
        '"datn" "ocr" created:<=2022-12-31',
        '"kltn" "tracking" created:<=2022-12-31',
        '"datn" "tracking" created:<=2022-12-31',
        '"kltn" "iot" created:<=2022-12-31',
        '"datn" "iot" created:<=2022-12-31',
        '"kltn" "reactjs" created:<=2022-12-31',
        '"datn" "reactjs" created:<=2022-12-31',
        '"kltn" "flutter" created:<=2022-12-31',
        '"datn" "flutter" created:<=2022-12-31',
        '"kltn" "spring boot" created:<=2022-12-31',
        '"datn" "spring boot" created:<=2022-12-31',
        '"kltn" "django" created:<=2022-12-31',
        '"datn" "django" created:<=2022-12-31',
        '"kltn" "nodejs" created:<=2022-12-31',
        '"datn" "nodejs" created:<=2022-12-31',
        '"kltn" "cybersecurity" created:<=2022-12-31',
        '"datn" "cybersecurity" created:<=2022-12-31',
        '"kltn" "an toàn thông tin" created:<=2022-12-31',
        '"datn" "an toàn thông tin" created:<=2022-12-31',
        '"kltn" "mạng máy tính" created:<=2022-12-31',
        '"datn" "mạng máy tính" created:<=2022-12-31',
        '"kltn" "xử lý ảnh" created:<=2022-12-31',
        '"datn" "xử lý ảnh" created:<=2022-12-31',
        '"kltn" "xử lý ngôn ngữ tự nhiên" created:<=2022-12-31',
        '"datn" "xử lý ngôn ngữ tự nhiên" created:<=2022-12-31',

        # 14. Kho LaTeX Template tốt nghiệp hoàn chỉnh
        'uit-thesis created:<=2022-12-31',
        'hust-thesis created:<=2022-12-31',
        'uet-thesis created:<=2022-12-31',
        'hcmut-thesis created:<=2022-12-31',
        'thesis-template uit created:<=2022-12-31',
        'thesis-template hust created:<=2022-12-31',
        'thesis-template uet created:<=2022-12-31',
        'thesis-template hcmut created:<=2022-12-31'
    ]

    # Các từ khóa nhận diện file PDF báo cáo đồ án
    THESIS_PDF_PATTERNS = [
        "kltn", "datn", "khoa_luan", "khoaluan", "do_an", "doan",
        "thesis", "luan_van", "luanvan", "bao_cao", "baocao",
        "report", "khoa-luan", "do-an", "luan-van", "capstone",
        "final_report", "final-report", "tot_nghiep", "totnghiep",
        "lvtn", "luan_an", "luanan", "do_an_tot_nghiep", "khoa_luan_tot_nghiep",
        "final_thesis", "cuon_bao_cao", "bao_cao_chinh", "cuon_datn", "cuon_kltn",
        "thuyet_minh", "thuyetminh", "full_report", "graduation", "chinh_thuc", "official"
    ]

    # Các biểu thức chính quy loại bỏ ngay (slide, tóm tắt, đề cương, bài tập, từng chương lẻ)
    BAD_FILE_REGEXES = [
        r"slide", r"presentation", r"thuyet[_\-\s]?trinh",
        r"chapter[_\-\s]?\d+", r"chuong[_\-\s]?\d+", r"part[_\-\s]?\d+", r"phan[_\-\s]?\d+",
        r"de[_\-\s]?cuong", r"outline", r"syllabus",
        r"bai[_\-\s]?tap", r"lab[_\-\s]?\d*", r"assignment", r"exercise",
        r"tom[_\-\s]?tat", r"summary",
        r"quy[_\-\s]?dinh", r"huong[_\-\s]?dan", r"mau[_\-\s]?bia",
        r"phieu", r"nhan[_\-\s]?xet", r"bien[_\-\s]?ban", r"lich[_\-\s]?trinh",
        r"cv", r"resume", r"release"
    ]

    GENERIC_TITLES = {
        "khoa luan tot nghiep", "do an tot nghiep", "luan van tot nghiep",
        "khoa luan", "do an", "luan van", "kltn", "datn", "lvtn",
        "bao cao tot nghiep", "bao cao khoa luan", "bao cao do an",
        "bao cao thuc tap", "thuc tap tot nghiep", "tieu luan",
        "thesis", "graduation thesis", "capstone project", "final report",
        "report", "bao cao"
    }

    def __init__(
        self,
        output_file: str = "json/github_theses.jsonl",
        checkpoint_file: str = "json/.checkpoint.json",
        pdf_dir: Optional[str] = r"D:\Documents\NCKH- Đồ án\NCKH về NLP, VLM, Vision\Dataset_khoaluan",
        github_token: Optional[str] = None,
        min_delay: float = 1.5,
        max_delay: float = 3.0,
        min_pages: int = 35,
        min_chars: int = 20000,
        max_year: int = 2022,
        max_workers: int = 5
    ):
        super().__init__(
            name="github_theses",
            output_file=output_file,
            checkpoint_file=checkpoint_file,
            pdf_dir=pdf_dir,
            min_delay=min_delay,
            max_delay=max_delay
        )
        self.github_token = github_token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self.min_pages = min_pages
        self.min_chars = min_chars
        self.max_year = max_year
        self.max_workers = max(1, min(max_workers, 12))
        self.crawled_count = 0
        self.seen_repos_file = os.path.join(os.path.dirname(self.output_file) or "json", "seen_github_repos.json")
        self.completed_queries_file = os.path.join(os.path.dirname(self.output_file) or "json", "completed_github_queries.json")
        self.seen_repos: Set[str] = self._load_seen_repos()
        self.completed_queries: Set[str] = self._load_completed_queries()

        if self.github_token:
            self.session.headers.update({
                "Authorization": f"token {self.github_token.strip()}",
                "Accept": "application/vnd.github.v3+json"
            })
            self.logger.info("Đã áp dụng GitHub Personal Access Token (Hạn mức: 30 search/min, 5.000 core/hr).")
        else:
            self.session.headers.update({
                "Accept": "application/vnd.github.v3+json"
            })
            self.logger.info("Chạy chế độ GitHub Hybrid (Web Tree-list không giới hạn + Adaptive Search 10/min).")

    def _load_seen_repos(self) -> Set[str]:
        """Tải danh sách các repo GitHub đã từng được kiểm tra để tránh quét lại khi restart."""
        repos: Set[str] = set()
        if os.path.exists(self.seen_repos_file):
            try:
                with open(self.seen_repos_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        repos.update(data)
            except Exception as e:
                self.logger.warning(f"Lỗi khi tải {self.seen_repos_file}: {e}")

        # Nạp thêm từ file jsonl đã lưu
        if os.path.exists(self.output_file):
            try:
                with open(self.output_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                d = json.loads(line)
                                src = d.get("source", "")
                                m = re.search(r"GitHub Repository \(([^)]+)\)", src)
                                if m:
                                    repos.add(m.group(1))
                            except Exception:
                                pass
            except Exception:
                pass

        self.logger.info(f"Đã nạp {len(repos)} repositories đã kiểm tra trước đó từ bộ nhớ đệm.")
        return repos

    def _save_seen_repos(self):
        """Ghi danh sách seen_repos xuống đĩa an toàn."""
        with self.lock:
            try:
                tmp_file = f"{self.seen_repos_file}.tmp"
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(sorted(list(self.seen_repos)), f, ensure_ascii=False, indent=1)
                os.replace(tmp_file, self.seen_repos_file)
            except Exception as e:
                self.logger.debug(f"Lỗi ghi {self.seen_repos_file}: {e}")

    def _load_completed_queries(self) -> Set[str]:
        """Tải danh sách các search query đã quét hoàn chỉnh tất cả các trang."""
        queries: Set[str] = set()
        if os.path.exists(self.completed_queries_file):
            try:
                with open(self.completed_queries_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        queries.update(data)
            except Exception as e:
                self.logger.warning(f"Lỗi khi tải {self.completed_queries_file}: {e}")
        self.logger.info(f"Đã nạp {len(queries)} queries đã hoàn thành trước đó.")
        return queries

    def _save_completed_queries(self):
        """Ghi danh sách completed_queries xuống đĩa an toàn."""
        with self.lock:
            try:
                tmp_file = f"{self.completed_queries_file}.tmp"
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(sorted(list(self.completed_queries)), f, ensure_ascii=False, indent=1)
                os.replace(tmp_file, self.completed_queries_file)
            except Exception as e:
                self.logger.debug(f"Lỗi ghi {self.completed_queries_file}: {e}")

    def _handle_rate_limit(self, response):
        """Xử lý điều tiết khi chạm ngưỡng Rate-Limit của GitHub Search API."""
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                wait_sec = int(retry_after) + 2
                self.logger.warning(f"[RATE LIMIT GITHUB] Nhận header Retry-After: tạm dừng {wait_sec}s...")
                time.sleep(wait_sec)
                return
            except Exception:
                pass

        remaining = response.headers.get("X-RateLimit-Remaining")
        if remaining is not None and int(remaining) == 0:
            reset_ts = response.headers.get("X-RateLimit-Reset")
            if reset_ts:
                wait_sec = max(int(reset_ts) - int(time.time()), 1) + 2
                wait_sec = min(wait_sec, 70)
                self.logger.warning(
                    f"[RATE LIMIT GITHUB] Đã hết lượt search quota. Tự động tạm dừng {wait_sec}s cho đến khi quota reset..."
                )
                time.sleep(wait_sec)
            else:
                self.logger.warning("[RATE LIMIT GITHUB] Tạm dừng 60s...")
                time.sleep(60)
        elif response.status_code in (403, 429):
            self.logger.warning("[RATE LIMIT GITHUB] Gặp secondary rate limit / abuse detection (HTTP 403/429). Tạm dừng 65s...")
            time.sleep(65)

    def _is_generic(self, text: str) -> bool:
        """Kiểm tra xem chuỗi có phải là nhãn chung chung (Khóa luận, Đồ án, Báo cáo) hay không."""
        if not text:
            return True
        norm = self.normalize_title_for_dedup(text)
        core = self.clean_core_title(text)
        if norm in self.GENERIC_TITLES or core in self.GENERIC_TITLES:
            return True
        if len(core) < 6:
            return True
        return False

    def _extract_cover_metadata(self, pages_text: List[str], repo_info: Dict[str, Any]) -> Dict[str, Any]:
        """Trích xuất tên trường, tên đề tài, tác giả, GVHD và năm từ các trang đầu của đồ án."""
        meta = {
            "title": "",
            "school": "Trường Đại học tại Việt Nam",
            "authors": [],
            "advisors": [],
            "year": None,
            "degree": "Khóa luận tốt nghiệp"
        }

        # Ghép 4 trang đầu để phân tích trang bìa và trang thông tin
        cover_text = "\n".join(pages_text[:4])
        clean_cover = cover_text.replace("\r", "")

        # 1. Bóc tách tên trường đại học
        school_patterns = [
            r"(TRƯỜNG\s+ĐẠI\s+HỌC\s+[^\n]+)",
            r"(ĐẠI\s+HỌC\s+QUỐC\s+GIA\s+[^\n]+)",
            r"(HỌC\s+VIỆN\s+[^\n]+)",
            r"(VIỆN\s+ĐẠI\s+HỌC\s+[^\n]+)"
        ]
        for sp in school_patterns:
            m = re.search(sp, clean_cover, re.IGNORECASE)
            if m:
                meta["school"] = m.group(1).strip()
                break

        # 2. Bóc tách bậc học / loại đồ án
        lt = clean_cover.lower()
        if "luận văn thạc sĩ" in lt or "thạc sĩ" in lt:
            meta["degree"] = "Luận văn thạc sĩ"
        elif "đồ án tốt nghiệp" in lt or "đồ án" in lt:
            meta["degree"] = "Đồ án tốt nghiệp"
        elif "khóa luận tốt nghiệp" in lt or "khóa luận" in lt:
            meta["degree"] = "Khóa luận tốt nghiệp"

        # 3. Bóc tách tên đề tài (Title) với độ ưu tiên cao
        title_candidates = []

        # Mẫu 1: Có tiền tố rõ ràng như Đề tài / Tên đề tài / Tên khóa luận...
        m1 = re.search(
            r"(?:ĐỀ\s+TÀI|TÊN\s+ĐỀ\s+TÀI|TÊN\s+KHÓA\s+LUẬN|TÊN\s+ĐỒ\s+ÁN|TÊN\s+LUẬN\s+VĂN)\s*[:：]?\s*\n*\s*([^\n\r]+(?:\n[^\n\r]+){0,2})",
            clean_cover,
            re.IGNORECASE
        )
        if m1:
            t1 = " ".join(m1.group(1).split()).strip(" :.-_\"'")
            # Cắt bớt phần nhầm sang GVHD/SVTH/Khóa luận ở cuối
            t1 = re.sub(r"\s*(?:KHÓA\s+LUẬN|ĐỒ\s+ÁN|GIÁO\s+VIÊN|GIẢNG\s+VIÊN|GVHD|SVTH).*$", "", t1, flags=re.IGNORECASE).strip()
            bad_title_words = ["giảng viên", "sinh viên", "bộ giáo dục", "cam đoan", "trung thực", "chưa công bố", "hình thức nào", "chịu trách nhiệm", "tôi xin"]
            if len(t1) >= 12 and not self._is_generic(t1) and not any(bad in t1.lower() for bad in bad_title_words):
                title_candidates.append(t1)

        # Mẫu 2: Nằm sau KHÓA LUẬN TỐT NGHIỆP / ĐỒ ÁN TỐT NGHIỆP ở dạng chữ in hoa
        m2 = re.search(
            r"(?:KHÓA\s+LUẬN\s+TỐT\s+NGHIỆP|ĐỒ\s+ÁN\s+TỐT\s+NGHIỆP|LUẬN\s+VĂN\s+THẠC\s+SĨ|BÁO\s+CÁO\s+TỐT\s+NGHIỆP)[^\n]*\n+([A-ZÀÁẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬĐÈÉẺẼẸÊẾỀỂỄỆÌÍỈĨỊÒÓỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÙÚỦŨỤƯỨỪỬỮỰỲÝỶỸỴ\s\d\-_:,\.]{15,140})",
            clean_cover
        )
        if m2:
            t2 = " ".join(m2.group(1).split()).strip(" :.-_\"'")
            t2 = re.sub(r"\s*(?:GIÁO\s+VIÊN|GIẢNG\s+VIÊN|GVHD|SVTH|SINH\s+VIÊN).*$", "", t2, flags=re.IGNORECASE).strip()
            if len(t2) >= 12 and not self._is_generic(t2) and not any(bad in t2.lower() for bad in bad_title_words):
                title_candidates.append(t2)

        # Mẫu 3: Tên đề tài trên phiếu nhận xét / đánh giá ở trang 2, 3, 4
        m3 = re.search(r"Tên\s+đề\s+tài\s*[:：]\s*([^\n\r]+)", clean_cover, re.IGNORECASE)
        if m3:
            t3 = " ".join(m3.group(1).split()).strip(" :.-_\"'")
            if len(t3) >= 12 and not self._is_generic(t3):
                title_candidates.append(t3)

        if title_candidates:
            # Chọn candidate chi tiết nhất
            meta["title"] = max(title_candidates, key=len)
        else:
            # Fallback lấy mô tả repo nếu không chung chung
            desc = repo_info.get("description") or ""
            if len(desc) >= 15 and not self._is_generic(desc) and not any(b in desc.lower() for b in ["source code", "mã nguồn", "source"]):
                meta["title"] = desc.strip()
            else:
                raw_name = repo_info.get("name", "").replace("-", " ").replace("_", " ").title()
                if not self._is_generic(raw_name):
                    meta["title"] = raw_name
                else:
                    meta["title"] = f"{raw_name} - {repo_info.get('owner', '')}"

        # 4. Bóc tách tác giả
        author_patterns = [
            r"Họ\s+và\s+tên\s+sinh\s+viên\s*[:：]\s*([^\n]+)",
            r"(?:Sinh\s+viên|Học\s+viên|Tác\s+giả|Người\s+thực\s+hiện)\s*(?:thực\s+hiện)?\s*[:：]?\s*\n*\s*([^\n]+)",
            r"(?:SVTH|HVTH)\s*[:：]?\s*\n*\s*([^\n]+)",
            r"([A-ZÀÁẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬĐÈÉẺẼẸÊẾỀỂỄỆÌÍỈĨỊÒÓỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÙÚỦŨỤƯỨỪỬỮỰỲÝỶỸỴ\s]{4,30})\s*[:：]\s*\d{7,10}"
        ]
        for ap in author_patterns:
            matches = re.finditer(ap, clean_cover, re.IGNORECASE)
            for m in matches:
                raw_a = m.group(1).strip()
                for part in re.split(r"[,;&]|\s{2,}", raw_a):
                    part = re.sub(r"-\s*\d+", "", part)
                    clean_a = re.sub(r"[^\w\s]", "", part).strip()
                    if clean_a and len(clean_a) >= 4 and not any(bad in clean_a.lower() for bad in ["mssv", "lớp", "khoa", "ngành", "bộ môn"]):
                        if clean_a not in meta["authors"]:
                            meta["authors"].append(clean_a)

        if not meta["authors"] and repo_info.get("owner"):
            meta["authors"].append(repo_info["owner"])

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
                for part in re.split(r"[,;&]|\s{2,}", raw_adv):
                    clean_adv = re.sub(r"(?:pgs\.|ts\.|ths\.|tiến\s*sĩ|thạc\s*sĩ|thầy|cô)", "", part, flags=re.IGNORECASE).strip()
                    clean_adv = re.sub(r"[^\w\s]", "", clean_adv).strip()
                    if clean_adv and len(clean_adv) >= 4 and not any(bad in clean_adv.lower() for bad in ["khoa", "bộ môn", "hướng dẫn"]):
                        meta["advisors"].append(clean_adv)
                if meta["advisors"]:
                    break

        # Chuẩn hóa khử trùng tác giả và GVHD (case-insensitive)
        unique_authors = []
        seen_a = set()
        for a in meta["authors"]:
            low = a.lower()
            if low not in seen_a:
                seen_a.add(low)
                unique_authors.append(a.title())
        meta["authors"] = unique_authors

        unique_advs = []
        seen_adv = set()
        for adv in meta["advisors"]:
            low = adv.lower()
            if low not in seen_adv:
                seen_adv.add(low)
                unique_advs.append(adv.title())
        meta["advisors"] = unique_advs

        # 6. Bóc tách năm bảo vệ
        year_matches = re.findall(r"\b(20[0-2][0-2]|201\d|200\d)\b", clean_cover)
        if year_matches:
            valid_years = [int(y) for y in year_matches if int(y) <= self.max_year]
            if valid_years:
                meta["year"] = max(valid_years)

        if not meta["year"]:
            created_at = repo_info.get("created_at") or ""
            m_yr = re.search(r"^(\d{4})", created_at)
            if m_yr:
                meta["year"] = int(m_yr.group(1))

        return meta

    def _inspect_repo_tree(self, repo_full_name: str, default_branch: str) -> List[Dict[str, Any]]:
        """Lấy danh sách các file trong repository.
        Ưu tiên sử dụng Web Tree-List Endpoint (không tốn quota 60 reqs/giờ của GitHub Core API).
        Fallback sang Git Trees API nếu có token hoặc endpoint web không khả dụng.
        """
        # 1. Thử lấy danh sách file qua GitHub Web Tree Endpoint
        try:
            web_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            }
            repo_web_url = f"https://github.com/{repo_full_name}"
            rw = requests.get(repo_web_url, headers=web_headers, timeout=10)
            if rw.status_code == 200:
                current_oid = None
                m_json = re.findall(r'<script type="application/json" data-target="react-app\.embeddedData">(.*?)</script>', rw.text)
                if m_json:
                    try:
                        ed = json.loads(m_json[0])
                        current_oid = ed.get("payload", {}).get("codeViewRepoRoute", {}).get("refInfo", {}).get("currentOid")
                    except Exception:
                        pass
                if not current_oid:
                    m_oid = re.search(r'"currentOid"\s*:\s*"([a-f0-9]{40})"', rw.text)
                    if m_oid:
                        current_oid = m_oid.group(1)

                if current_oid:
                    tl_url = f"https://github.com/{repo_full_name}/tree-list/{current_oid}"
                    r_tl = requests.get(tl_url, headers={"User-Agent": web_headers["User-Agent"], "Accept": "application/json"}, timeout=10)
                    if r_tl.status_code == 200:
                        paths = r_tl.json().get("paths", [])
                        pdf_candidates = []
                        for path in paths:
                            path_lower = path.lower()
                            if not path_lower.endswith(".pdf"):
                                continue
                            if any(re.search(bad, path_lower) for bad in self.BAD_FILE_REGEXES):
                                continue
                            if any(pat in path_lower for pat in self.THESIS_PDF_PATTERNS) or any(t in repo_full_name.lower() for t in ["kltn", "khoa-luan", "khoaluan", "datn", "do-an", "doan", "thesis", "luanvan", "luan-van", "capstone", "lvtn", "totnghiep", "tot-nghiep", "graduation", "ky-su", "kysu", "thac-si", "thacsi"]):
                                pdf_candidates.append({
                                    "path": path,
                                    "size": 0,
                                    "raw_url": f"https://raw.githubusercontent.com/{repo_full_name}/{default_branch}/{urllib.parse.quote(path)}"
                                })
                        if pdf_candidates:
                            return pdf_candidates
        except Exception as e:
            self.logger.debug(f"Web tree-list thất bại với {repo_full_name}: {e}")

        # 2. Fallback sang GitHub REST Trees API (chỉ dùng nếu có token)
        if self.github_token:
            tree_url = f"https://api.github.com/repos/{repo_full_name}/git/trees/{default_branch}?recursive=1"
            try:
                r = self.session.get(tree_url, timeout=12)
                self._handle_rate_limit(r)
                if r.status_code == 200:
                    tree_data = r.json().get("tree", [])
                    pdf_candidates = []
                    for node in tree_data:
                        path = node.get("path", "")
                        size = node.get("size", 0)
                        path_lower = path.lower()
                        if not path_lower.endswith(".pdf"):
                            continue
                        if size > 0 and (size < 600 * 1024 or size > 40 * 1024 * 1024):
                            continue
                        if any(re.search(bad, path_lower) for bad in self.BAD_FILE_REGEXES):
                            continue
                        if any(pat in path_lower for pat in self.THESIS_PDF_PATTERNS) or any(t in repo_full_name.lower() for t in ["kltn", "khoa-luan", "khoaluan", "datn", "do-an", "doan", "thesis", "luanvan", "luan-van", "capstone", "lvtn", "totnghiep", "tot-nghiep", "graduation", "ky-su", "kysu", "thac-si", "thacsi"]):
                            pdf_candidates.append({
                                "path": path,
                                "size": size,
                                "raw_url": f"https://raw.githubusercontent.com/{repo_full_name}/{default_branch}/{urllib.parse.quote(path)}"
                            })
                    return pdf_candidates
            except Exception as e:
                self.logger.debug(f"Git Tree API thất bại với {repo_full_name}: {e}")

        return []

    def _download_and_validate_pdf(
        self,
        raw_url: str,
        repo_name: str,
        file_path: str,
        repo_info: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Tải file PDF từ GitHub, kiểm định Quality Gate nghiêm ngặt (>= 25 trang, >= 10.000 ký tự)."""
        safe_repo = re.sub(r"[^\w\.-]", "_", repo_name)
        base_name = os.path.basename(file_path)
        safe_base = re.sub(r"[^\w\.-]", "_", base_name)
        pdf_filename = f"GITHUB_{safe_repo}_{safe_base}"
        if not pdf_filename.lower().endswith(".pdf"):
            pdf_filename += ".pdf"

        record_id = f"github_{safe_repo}_{safe_base}"
        save_path = os.path.join(self.pdf_dir, pdf_filename)
        with self.lock:
            if record_id in self.seen_ids or os.path.exists(save_path):
                return None

        temp_path = f"{save_path}.{threading.get_ident()}.part"

        # 1. Tải stream file PDF từ raw.githubusercontent.com
        try:
            r = self.session.get(raw_url, stream=True, timeout=30)
            if r.status_code != 200:
                self.logger.debug(f"Lỗi tải raw PDF {raw_url}: HTTP {r.status_code}")
                return None

            first_chunk = next(r.iter_content(chunk_size=32768), b"")
            if not first_chunk.startswith(b"%PDF"):
                return None

            first_100k = bytearray(first_chunk)
            is_scan = False
            max_bytes = 45 * 1024 * 1024
            downloaded = len(first_chunk)
            too_large = False

            with open(temp_path, "wb") as f:
                f.write(first_chunk)
                for chunk in r.iter_content(chunk_size=32768):
                    if chunk:
                        downloaded += len(chunk)
                        if downloaded > max_bytes:
                            too_large = True
                            break
                        f.write(chunk)
                        if len(first_100k) < 100000:
                            first_100k.extend(chunk)
                        elif not is_scan and first_100k.count(b"/Font") < 2:
                            is_scan = True
                            break

            if too_large:
                self.logger.warning(f"[LOẠI BỎ - FILE QUÁ LỚN > 45MB] {pdf_filename}. Bỏ qua!")
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                return None

            if is_scan or first_100k.count(b"/Font") < 2:
                self.logger.warning(f"[LOẠI BỎ - SCAN ẢNH] {pdf_filename} không có text font.")
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                return None

            if downloaded < 600 * 1024:
                self.logger.warning(f"[LOẠI BỎ - DUNG LƯỢNG NHỎ < 600KB] {pdf_filename} ({downloaded//1024} KB). Bỏ qua!")
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                return None

            # 2. Quality Gate chuyên sâu: Số trang >= 35 và Dung lượng text >= 20.000 ký tự
            reader = PdfReader(temp_path, strict=False)
            num_pages = len(reader.pages)
            if num_pages < self.min_pages:
                self.logger.warning(
                    f"[LOẠI BỎ - DƯỚI {self.min_pages} TRANG] {pdf_filename} chỉ có {num_pages} trang (slide/outline). Bỏ qua!"
                )
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                return None

            # Trích xuất văn bản từ các trang đầu để thẩm định chất lượng
            pages_to_extract = min(num_pages, 35)
            extracted_pages = []
            for i in range(pages_to_extract):
                try:
                    txt = reader.pages[i].extract_text() or ""
                    extracted_pages.append(txt)
                except Exception:
                    extracted_pages.append("")

            total_text = "".join(extracted_pages)
            if len(total_text.strip()) < self.min_chars:
                self.logger.warning(
                    f"[LOẠI BỎ - TEXT DƯỚI {self.min_chars} CHARS] {pdf_filename} chỉ có {len(total_text)} ký tự text layer. Bỏ qua!"
                )
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                return None

            # 3. Bắt buộc là tiếng Việt chuẩn mực (ít nhất 80 ký tự dấu tiếng Việt và có các từ tiếng Việt đặc trưng)
            vn_char_pattern = re.compile(r'[àáảãạăắằẳẵặâấầẩẫậđèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵ]')
            vn_chars_count = len(vn_char_pattern.findall(total_text.lower()))
            common_vi_words = ["và", "của", "trong", "được", "các", "cho", "với", "nghiên cứu", "đề tài", "khóa luận", "đồ án", "báo cáo", "hệ thống", "trường"]
            matched_vi_words = sum(1 for w in common_vi_words if re.search(r'\b' + w + r'\b', total_text.lower()))
            if vn_chars_count < 80 or matched_vi_words < 3:
                self.logger.warning(f"[LOẠI BỎ - KHÔNG PHẢI TIẾNG VIỆT] {pdf_filename} (Chỉ có {vn_chars_count} ký tự dấu, {matched_vi_words} từ tiếng Việt). Bỏ qua!")
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                return None

            # 4. Trích xuất thông tin metadata đồ án từ trang bìa
            meta = self._extract_cover_metadata(extracted_pages, repo_info)
            title = meta["title"]

            # Kiểm tra nghiêm ngặt mốc năm <= max_year (mặc định: 2022) để loại trừ 100% rủi ro AI/ChatGPT
            if meta.get("year"):
                if meta["year"] > self.max_year:
                    self.logger.warning(
                        f"[LOẠI BỎ - NĂM {meta['year']} > {self.max_year}] {title[:50]}. Bỏ qua để đảm bảo 100% văn bản con người viết trước kỷ nguyên AI!"
                    )
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    return None
            else:
                # Nếu trang bìa không ghi năm rõ ràng, chỉ chấp nhận nếu repo được tạo <= max_year
                created_at = repo_info.get("created_at") or ""
                m_yr = re.search(r"^(\d{4})", created_at)
                if m_yr and int(m_yr.group(1)) > self.max_year:
                    self.logger.warning(
                        f"[LOẠI BỎ - BÌA KHÔNG GHI NĂM & REPO TẠO {m_yr.group(1)} > {self.max_year}] {title[:50]}. Bỏ qua!"
                    )
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    return None

            # 5. Kiểm tra chuyên ngành CNTT
            is_it, matched_tags = is_it_topic(
                title=title,
                abstract=total_text[:2000],
                faculty=meta["school"]
            )
            if not is_it:
                self.logger.warning(f"[LOẠI BỎ - KHÔNG PHẢI CNTT] {title[:50]} (Trường: {meta['school']}). Bỏ qua!")
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                return None

            # 6. Khử trùng tiêu đề đa tầng với toàn bộ kho hiện có
            with self.lock:
                if self.is_duplicate_title(title):
                    self.logger.info(f"[TRÙNG TIÊU ĐỀ] Bỏ qua đồ án đã có: {title[:50]}...")
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    return None

                norm_t = self.normalize_title_for_dedup(title)
                if norm_t and not self._is_generic(norm_t):
                    self.reserved_titles.add(norm_t)
                core_t = self.clean_core_title(title)
                if core_t and not self._is_generic(core_t):
                    self.reserved_core_titles.add(core_t)

            # Đổi tên file tạm .part thành file chính thức an toàn
            with self.lock:
                if os.path.exists(save_path):
                    if os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except Exception:
                            pass
                    return None
                try:
                    os.rename(temp_path, save_path)
                except Exception:
                    if os.path.exists(save_path):
                        return None
                    raise
            size_bytes = os.path.getsize(save_path)

            self.logger.info(
                f"[ĐÃ TẢI THÀNH CÔNG ĐỒ ÁN GITHUB] {pdf_filename} "
                f"({num_pages} trang, {size_bytes:,} bytes, Text: {len(total_text):,} chars) -> {title[:45]}"
            )

            record_id = f"github_{safe_repo}_{safe_base}"
            record = {
                "id": record_id,
                "source": f"GitHub Repository ({repo_info.get('full_name')})",
                "title": title,
                "authors": meta["authors"],
                "advisors": meta["advisors"],
                "year": meta["year"],
                "degree": meta["degree"],
                "school_or_faculty": meta["school"],
                "keywords": matched_tags,
                "abstract": total_text[200:1500].strip(),
                "matched_it_tags": matched_tags,
                "url": repo_info.get("html_url", raw_url),
                "has_full_pdf": True,
                "num_pages": num_pages,
                "pdf_path": save_path,
                "pdf_size_bytes": size_bytes,
                "pdf_download_url": raw_url,
                "crawl_time": datetime.now().isoformat()
            }
            return record

        except Exception as e:
            self.logger.debug(f"Lỗi khi xử lý PDF {raw_url}: {e}")
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
        return None

    def _process_single_repo(self, repo_item: Dict[str, Any], limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Xử lý đơn repo: quét cây thư mục và tải/thẩm định các file PDF đồ án tiềm năng."""
        with self.lock:
            if limit is not None and self.crawled_count >= limit:
                return []

        full_name = repo_item.get("full_name")
        default_branch = repo_item.get("default_branch", "master")
        repo_info = {
            "full_name": full_name,
            "owner": repo_item.get("owner", {}).get("login", ""),
            "name": repo_item.get("name", ""),
            "description": repo_item.get("description") or "",
            "created_at": repo_item.get("created_at", ""),
            "html_url": repo_item.get("html_url", "")
        }

        # Tìm các file PDF đồ án trong Git Tree
        pdf_candidates = self._inspect_repo_tree(full_name, default_branch)
        if not pdf_candidates:
            return []

        saved = []
        for pdf_node in pdf_candidates:
            with self.lock:
                if limit is not None and self.crawled_count >= limit:
                    break

            raw_url = pdf_node["raw_url"]
            file_path = pdf_node["path"]

            rec = self._download_and_validate_pdf(
                raw_url=raw_url,
                repo_name=full_name,
                file_path=file_path,
                repo_info=repo_info
            )
            if rec:
                self.append_record(rec)
                with self.lock:
                    self.crawled_count += 1
                    cnt = self.crawled_count
                self.save_checkpoint()
                self.logger.info(
                    f"[MỚI LƯU THÀNH CÔNG #{cnt}] {rec['title'][:50]} ({rec['school_or_faculty']}) - {rec['num_pages']} trang"
                )
                saved.append(rec)
        return saved

    def crawl(self, limit: Optional[int] = None) -> int:
        """Thực hiện quét GitHub và thu thập đồ án / khóa luận tốt nghiệp CNTT <= 2022 bằng Multi-threading."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        self.logger.info(
            f"Bắt đầu thu thập Đồ án / Khóa luận tốt nghiệp CNTT từ GitHub (Multi-threading: {self.max_workers} luồng, Mục tiêu: {limit or 'Không giới hạn'} bản ghi)..."
        )
        self.crawled_count = 0

        total_queries = len(self.SEARCH_QUERIES)
        for q_idx, query in enumerate(self.SEARCH_QUERIES, 1):
            if limit is not None and self.crawled_count >= limit:
                break

            if query in self.completed_queries:
                self.logger.info(f"[{q_idx}/{total_queries}] Bỏ qua query đã quét xong trước đó: '{query}'")
                continue

            self.logger.info(f"[{q_idx}/{total_queries}] [GitHub Search] Đang truy vấn: '{query}'...")
            page = 1
            max_pages = 20
            query_interrupted = False

            while page <= max_pages:
                if limit is not None and self.crawled_count >= limit:
                    query_interrupted = True
                    break

                search_url = f"{self.SEARCH_API}?q={urllib.parse.quote(query)}&page={page}&per_page=50"
                try:
                    retry_count = 0
                    max_retries = 5
                    data = None

                    while retry_count <= max_retries:
                        r = self.session.get(search_url, timeout=15)
                        if r.status_code == 200:
                            data = r.json()
                            break
                        elif r.status_code in (403, 429):
                            retry_count += 1
                            if retry_count > max_retries:
                                self.logger.warning(
                                    f"Đã thử lại {max_retries} lần vẫn gặp Rate Limit ({r.status_code}) tại '{query}' trang {page}."
                                )
                                query_interrupted = True
                                break
                            self._handle_rate_limit(r)
                            self.logger.warning(
                                f"[GitHub Search Retry #{retry_count}] Thử lại trang {page} của '{query}'..."
                            )
                            time.sleep(2.0)
                        else:
                            self.logger.warning(f"GitHub Search lỗi HTTP {r.status_code}: {r.text[:150]}")
                            query_interrupted = True
                            break

                    if not data or query_interrupted:
                        break
                    items = data.get("items", [])
                    if not items:
                        break

                    total_found = data.get("total_count", 0)
                    if page == 1:
                        max_pages = min(20, (total_found + 49) // 50)

                    batch_repos = []
                    for repo_item in items:
                        full_name = repo_item.get("full_name")
                        if not full_name or full_name in self.seen_repos:
                            continue
                        self.seen_repos.add(full_name)
                        batch_repos.append(repo_item)

                    self.logger.info(
                        f"[GitHub Search] Trang {page}/{max_pages} ({len(batch_repos)} repos mới / Tổng {total_found}) -> Xử lý song song {self.max_workers} luồng..."
                    )

                    if batch_repos:
                        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                            future_to_repo = {
                                executor.submit(self._process_single_repo, repo, limit): repo.get("full_name")
                                for repo in batch_repos
                            }
                            for future in as_completed(future_to_repo):
                                repo_name = future_to_repo[future]
                                try:
                                    future.result()
                                except Exception as exc:
                                    self.logger.debug(f"Luồng xử lý repo {repo_name} gặp lỗi: {exc}")

                                if limit is not None and self.crawled_count >= limit:
                                    query_interrupted = True
                                    break

                        self.save_checkpoint()
                        self._save_seen_repos()

                    page += 1
                    time.sleep(1.0)

                except Exception as e:
                    self.logger.error(f"Lỗi truy vấn GitHub search ({query}): {e}")
                    time.sleep(5.0)
                    query_interrupted = True
                    break

            if not query_interrupted:
                self.completed_queries.add(query)
                self._save_completed_queries()
                self._save_seen_repos()
                self.logger.info(f"[{q_idx}/{total_queries}] ĐÃ QUÉT XONG TOÀN BỘ QUERY: '{query}' ({len(self.completed_queries)}/{total_queries} queries xong)")

        self.save_checkpoint()
        self._save_seen_repos()
        self._save_completed_queries()
        self.logger.info(f"Hoàn thành thu thập GitHub! Đã thu nạp {self.crawled_count} đồ án tốt nghiệp toàn văn mới.")
        return self.crawled_count
