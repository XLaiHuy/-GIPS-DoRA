# -*- coding: utf-8 -*-
"""
filters.py: Bộ lọc đa tầng nhận diện đề tài Công nghệ Thông tin (CNTT)
"""
import re
from typing import Tuple, List, Optional

# Danh sách khoa / đơn vị chuyên ngành CNTT (Ưu tiên số 1)
IT_FACULTIES = [
    "công nghệ thông tin",
    "khoa học máy tính",
    "kỹ thuật phần mềm",
    "hệ thống thông tin",
    "mạng máy tính",
    "an toàn thông tin",
    "khoa học dữ liệu",
    "trí tuệ nhân tạo",
    "toán - tin",
    "toán tin",
    "điện tử viễn thông",
    "information technology",
    "computer science",
    "software engineering",
    "information systems",
    "data science",
    "artificial intelligence",
    "cyber security",
]

# Danh sách từ khóa chuyên ngành CNTT (Whitelist)
IT_KEYWORDS = [
    # Thuật ngữ chung
    "công nghệ thông tin", "khoa học máy tính", "kỹ thuật phần mềm", "hệ thống thông tin",
    "an toàn thông tin", "an ninh mạng", "mạng máy tính", "khoa học dữ liệu",
    "information technology", "computer science", "software engineering",
    
    # Trí tuệ nhân tạo & Học máy
    "trí tuệ nhân tạo", "học máy", "học sâu", "mạng nơ ron", "mạng neural",
    "xử lý ngôn ngữ tự nhiên", "thị giác máy tính", "nhận dạng tiếng nói",
    "nhận dạng khuôn mặt", "nhận dạng chữ", "phân lớp văn bản", "tóm tắt văn bản",
    "mô hình ngôn ngữ", "mô hình sinh", "học tăng cường", "hệ khuyến nghị", "hệ gợi ý", "hệ chuyên gia",
    "artificial intelligence", "machine learning", "deep learning", "neural network",
    "natural language processing", "computer vision", "speech recognition",
    "image classification", "object detection", "segmentation", "reinforcement learning",
    "transformer", "bert", "gpt", "llm", "cnn", "rnn", "lstm", "yolo", "mạng gan", "generative adversarial", "diffusion",
    
    # Phát triển phần mềm, Kiến trúc & Kiểm thử
    "phát triển ứng dụng", "phần mềm", "website", "web application", "app mobile",
    "ứng dụng di động", "lập trình", "microservices", "kiến trúc hệ thống", "kiến trúc phần mềm",
    "kỹ thuật phần mềm", "công nghệ phần mềm", "kiểm thử phần mềm", "kiểm thử", "software testing",
    "phần mềm nguồn mở", "mã nguồn mở", "open source", "mẫu thiết kế", "design pattern",
    "front-end", "back-end", "full-stack", "rest api", "graphql", "devops",
    "docker", "kubernetes", "ci/cd", "spring boot", "reactjs", "nodejs", "flutter",
    
    # Hệ điều hành, Bảo mật & Mật mã học
    "hệ điều hành", "bảo mật hệ điều hành", "operating system", "linux", "kernel",
    "mật mã học", "mật mã", "mã hóa", "giải mã", "chữ ký số", "hạ tầng khóa công khai", "pki",
    "cryptography", "an ninh mạng", "an toàn thông tin", "an toàn mạng", "bảo mật mạng",
    "lỗ hổng bảo mật", "lỗ hổng", "vulnerability", "mã độc", "malware", "phần mềm độc hại",
    "phát hiện xâm nhập", "ids", "ips", "tường lửa", "firewall", "tấn công mạng", "phòng thủ mạng",
    "chống tấn công", "ddos", "điều tra số", "truy vết số", "digital forensics", "bảo mật web",
    
    # Đồ họa máy tính, Thị giác & Đa phương tiện
    "đồ họa máy tính", "đồ họa", "computer graphics", "dựng hình 3d", "mô hình 3d", "render 3d",
    "thực tế ảo", "virtual reality", "thực tế tăng cường", "augmented reality",
    "xử lý ảnh", "image processing", "xử lý video", "xử lý tín hiệu số", "xử lý âm thanh",
    "nhận dạng mẫu", "nhận dạng sinh trắc học", "ocr",
    
    # Dữ liệu & Cơ sở dữ liệu
    "cơ sở dữ liệu", "hệ quản trị cơ sở dữ liệu", "kho dữ liệu", "khai phá dữ liệu",
    "dữ liệu lớn", "data mining", "big data", "database", "data warehouse",
    "khoa học dữ liệu", "data science", "phân tích dữ liệu", "trực quan hóa dữ liệu",
    "sql", "nosql", "mongodb", "postgresql", "mysql", "hadoop", "spark",
    
    # Hạ tầng, Mạng & Hệ phân tán
    "mạng máy tính", "mạng không dây", "wireless", "wifi", "giao thức mạng", "định tuyến",
    "truyền thông dữ liệu", "hệ phân tán", "hệ thống phân tán", "tính toán song song",
    "tính toán lưới", "điện toán phân tán", "ảo hóa", "virtualization", "sdn",
    "điện toán đám mây", "cloud computing", "iot", "internet of things", "internet vạn vật",
    "blockchain", "chuỗi khối", "hợp đồng thông minh", "smart contract",
    "hệ thống nhúng", "embedded system", "vi điều khiển", "microcontroller",
    "robotics", "robot", "tự động hóa", "thuật toán", "cấu trúc dữ liệu", "thuật toán tối ưu"
]

# Danh sách từ khóa phủ định (Blacklist - loại trừ các ngành hoàn toàn không liên quan)
NON_IT_KEYWORDS = [
    # Tài chính - Kinh tế - Quản trị
    "báo cáo tài chính", "kế toán tài chính", "kiểm toán", "thuế thu nhập",
    "quản trị nhân lực", "cho vay tiêu dùng", "tín dụng ngân hàng", "thẩm định giá",
    "nợ xấu", "thị trường chứng khoán", "cổ phần hóa", "bất động sản",
    
    # Luật & Xã hội
    "luật hôn nhân", "luật hình sự", "luật dân sự", "tố tụng dân sự",
    "sư phạm mầm non", "phương pháp dạy học ngữ văn",
    
    # Y sinh - Sinh học - Dược - Thủy hải sản - Nông nghiệp
    "công nghệ sinh học", "vi sinh", "viêm gan", "enzym", "enzyme",
    "vi khuẩn", "chế phẩm sinh học", "genotype", "kiểu gen", "real-time pcr",
    "tế bào gốc", "nuôi cấy mô", "nuôi trồng thủy sản", "tôm thẻ", "nuôi cấy vi khuẩn",
    "kháng sinh", "lên men vi khuẩn", "bacillus", "vibrio", "cellulase", "mô bệnh học",
    "dược lý", "dược liệu", "y học cổ truyền", "chữa bệnh", "nhi khoa",
    "trồng trọt", "chăn nuôi", "thổ nhưỡng"
]

def normalize_text(text: str) -> str:
    """Chuẩn hóa văn bản về chữ thường không dấu / có dấu phục vụ so khớp."""
    if not text:
        return ""
    return text.lower().strip()

def is_it_topic(
    title: str = "",
    abstract: str = "",
    keywords: Optional[List[str]] = None,
    faculty: str = ""
) -> Tuple[bool, List[str]]:
    """
    Kiểm tra một tài liệu có thuộc đề tài Công nghệ Thông tin hay không.
    Trả về: (is_it, matched_tags)
    """
    norm_title = normalize_text(title)
    norm_abstract = normalize_text(abstract)
    norm_faculty = normalize_text(faculty)
    
    norm_keywords = [normalize_text(k) for k in (keywords or [])]
    full_content = f"{norm_title} {norm_abstract} {' '.join(norm_keywords)}"
    
    matched_tags = []
    
    # 1. Kiểm tra đơn vị đào tạo (Khoa / Trường)
    for fac in IT_FACULTIES:
        if fac in norm_faculty:
            matched_tags.append(f"faculty:{fac}")
            return True, matched_tags

    # 2. Kiểm tra Blacklist nếu tiêu đề thuần ngành khác (ví dụ: "Phân tích báo cáo tài chính...")
    is_pure_non_it = False
    for bad_kw in NON_IT_KEYWORDS:
        if bad_kw in norm_title:
            is_pure_non_it = True
            break
            
    # 3. Kiểm tra Whitelist từ khóa chuyên ngành
    for kw in IT_KEYWORDS:
        # Regex kiểm tra từ nguyên vẹn để tránh match chuỗi con sai lệch
        pattern = r"(?:\b|_)" + re.escape(kw) + r"(?:\b|_)"
        if re.search(pattern, full_content, re.IGNORECASE):
            matched_tags.append(kw)
            
    # Nếu có từ khóa CNTT rõ rệt
    if matched_tags:
        # Nếu dính blacklist nhưng tiêu đề có từ khóa phần mềm/website rõ ràng -> Vẫn giữ (VD: "Phần mềm quản lý kế toán")
        if is_pure_non_it:
            it_overrides = ["hệ thống", "phần mềm", "ứng dụng", "website", "xây dựng hệ thống", "thuật toán", "áp dụng"]
            if any(override in norm_title for override in it_overrides):
                return True, matched_tags
            return False, []
        return True, matched_tags

    return False, []
