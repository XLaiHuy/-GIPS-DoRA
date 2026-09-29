"""Text healing module for Vietnamese PDF extraction artifacts.
Specifically targets CMap space omission (ligature/kerning token fusion)
using a high-precision, non-destructive dictionary lookup.
Preserves exact capitalization pattern (lower, Title, UPPER).
Does NOT use LLMs or alter semantic content.
"""

import re

# High-frequency glued word pairs (verified unambiguous in Vietnamese)
GLUED_DICTIONARY = {
    # Nouns & Computing terms
    "dữliệu": "dữ liệu",
    "bộdữliệu": "bộ dữ liệu",
    "cơsởdữliệu": "cơ sở dữ liệu",
    "hệthống": "hệ thống",
    "kỹthuật": "kỹ thuật",
    "độchính": "độ chính",
    "độchínhxác": "độ chính xác",
    "ngữtựnhiên": "ngữ tự nhiên",
    "ngữtiếng": "ngữ tiếng",
    "ngữnghĩa": "ngữ nghĩa",
    "nguồnmở": "nguồn mở",
    "thựctếảo": "thực tế ảo",
    "môhình": "mô hình",
    "thuậttoán": "thuật toán",
    "phầnmềm": "phần mềm",
    "phầncứng": "phần cứng",
    "mạngnơron": "mạng nơ ron",
    "trítuệnhântạo": "trí tuệ nhân tạo",
    "họcmáy": "học máy",
    "họcsâu": "học sâu",
    "thịgiác": "thị giác",
    "hìnhảnh": "hình ảnh",
    "âmthanh": "âm thanh",
    "giao diện": "giao diện",
    "giaodiện": "giao diện",
    "ngườidùng": "người dùng",
    "ngườisửdụng": "người sử dụng",
    "đềtài": "đề tài",
    "khóaluận": "khóa luận",
    "luậnvăn": "luận văn",
    "báocáo": "báo cáo",
    "nghiêncứu": "nghiên cứu",
    "sựkiện": "sự kiện",
    "sốlượng": "số lượng",
    "chấtlượng": "chất lượng",
    "kếtquả": "kết quả",
    "đánhgiá": "đánh giá",
    "thựchiện": "thực hiện",
    "vịtrí": "vị trí",
    "thôngtin": "thông tin",
    "triểnkhai": "triển khai",
    "pháttriển": "phát triển",
    "ứngdụng": "ứng dụng",
    "chứcnăng": "chức năng",
    "yêucầu": "yêu cầu",
    "cấutrúc": "cấu trúc",
    "nềntảng": "nền tảng",
    "môphỏng": "mô phỏng",
    "tươngtác": "tương tác",
    "môihướng": "môi trường",
    "môitrường": "môi trường",
    "khônggian": "không gian",
    "đốitượng": "đối tượng",
    "phươngpháp": "phương pháp",
    "giảipháp": "giải pháp",
    "bàitoán": "bài toán",
    "thựctế": "thực tế",
    "thựctại": "thực tại",
    "trạngthái": "trạng thái",
    "hiệuquả": "hiệu quả",
    "hiệunăng": "hiệu năng",
    "thờigian": "thời gian",
    "khảnăng": "khả năng",
    "tiêuchí": "tiêu chí",
    "phânloại": "phân loại",
    "nhậndạng": "nhận dạng",
    "nhậndiện": "nhận diện",
    "pháthiện": "phát hiện",
    "tríchxuất": "trích xuất",
    "dựđoán": "dự đoán",
    "ước lượng": "ước lượng",
    "ướclượng": "ước lượng",
    "xửlý": "xử lý",
    "tiềnxửlý": "tiền xử lý",
    "hậuxửlý": "hậu xử lý",
    "tốiưu": "tối ưu",
    "tốiưuhóa": "tối ưu hóa",
    "chuẩnhóa": "chuẩn hóa",
    "tựđộng": "tự động",
    "đồngbộ": "đồng bộ",
    "trựctiếp": "trực tiếp",
    "giántiếp": "gián tiếp",
    "chínhxác": "chính xác",
    "phổbiến": "phổ biến",
    "quantrọng": "quan trọng",
    "cụthể": "cụ thể",
    "chitiết": "chi tiết",
    "tổngquan": "tổng quan",
    "tổnghợp": "tổng hợp",
    "tươngứng": "tương ứng",
    "liênquan": "liên quan",
    "phùhợp": "phù hợp",
    "đặctrưng": "đặc trưng",
    "thuộctính": "thuộc tính",
    "thànhphần": "thành phần",
    "nộidung": "nội dung",
    "ýnghĩa": "ý nghĩa",
    "mụctiêu": "mục tiêu",
    "nhiệmvụ": "nhiệm vụ",
    "phạmdi": "phạm vi",
    "phạmvi": "phạm vi",
    "giớihạn": "giới hạn",
    "kếthợp": "kết hợp",
    "kếthừa": "kế thừa",
    "mởrộng": "mở rộng",
    "cảitiến": "cải tiến",
    "nângcao": "nâng cao",
    "hoànthành": "hoàn thành",
    "kếttruyền": "kết nối",
    "kếtnối": "kết nối",
    "truyềntải": "truyền tải",
    "truyvấn": "truy vấn",
    "lưutrữ": "lưu trữ",
    "truyxuất": "truy xuất",
    "hỗtrợ": "hỗ trợ",
    "hợpnhất": "hợp nhất",
    "phântán": "phân tán",
    "tậptrung": "tập trung",
    "chiếnlược": "chiến lược",
    "môthức": "mô thức",
    "khungnhìn": "khung nhìn",
    "bảnvẽ": "bản vẽ",
    "mànhình": "màn hình",
    "thiếtbị": "thiết bị",
    "máyhọc": "máy học",
    "máytính": "máy tính",
    
    # Common verb/adj/particle combinations
    "sửdụng": "sử dụng",
    "sẽđược": "sẽ được",
    "đãđược": "đã được",
    "đượcápdụng": "được áp dụng",
    "đượcxem": "được xem",
    "đượctạo": "được tạo",
    "cóthể": "có thể",
    "khôngthể": "không thể",
    "cầnphải": "cần phải",
    "chophép": "cho phép",
    "giúpcải": "giúp cải",
    "giúpcho": "giúp cho",
    "đưara": "đưa ra",
    "đềxuất": "đề xuất",
    "trảlời": "trả lời",
    "thôngqua": "thông qua",
    "dựatrên": "dựa trên",
    "dựavào": "dựa vào",
    "tạora": "tạo ra",
    "xâydựng": "xây dựng",
    "thiếtkế": "thiết kế",
    "cungcấp": "cung cấp",
    "ápdụng": "áp dụng",
    "thayđổi": "thay đổi",
    "trởthành": "trở thành",
    "phụthuộc": "phụ thuộc",
    "liênkết": "liên kết",
    "quảnlý": "quản lý",
    "kiểmthử": "kiểm thử",
    "kiểmsoát": "kiểm soát",
    "phátsinh": "phát sinh",
    "thửnghiệm": "thử nghiệm",
    "trảivề": "trả về",
    "đồngthời": "đồng thời",
    "từđó": "từ đó",
    "từcác": "từ các",
    "từnhững": "từ những",
    "trongđó": "trong đó",
    "ngoàira": "ngoài ra",
    "hơnngữ": "hơn nữa",
    "hơnnữa": "hơn nữa",
    "nhưvậy": "như vậy",
    "tuy nhiên": "tuy nhiên",
    "tuynhiên": "tuy nhiên",
    "bởivì": "bởi vì",
    "mặcdù": "mặc dù",
    "quảcủa": "quả của",
    "thểhiện": "thể hiện",
    "thểđược": "thể được",
    "cảcác": "cả các",
    "đểtạo": "để tạo",
    "sẽcó": "sẽ có",
    "đãcó": "đã có",
    "nếunhư": "nếu như",
    "nhưngnó": "nhưng nó",
    "vớinhiều": "với nhiều",
    "tươngtự": "tương tự",
    "hoàntoàn": "hoàn toàn",
    "trướckhi": "trước khi",
    "saukhi": "sau khi",
    "gầnnhư": "gần như",
    "rấtnhiều": "rất nhiều",
    "củanó": "của nó",
    "củacác": "của các",
    "củangười": "của người",
    "cảđồthịkềvà": "cả đồ thị kề và",
    "đồthịkề": "đồ thị kề",
    "đồthị": "đồ thị",
    "kỹsư": "kỹ sư",
    "thựctếảo": "thực tế ảo",
    "tếảo": "tế ảo",
    "đểý": "để ý",
    "dụở": "dụ ở",
    "ởparis": "ở Paris",
    "côngthức": "công thức",
    "giátrị": "giá trị",
    "phầntử": "phần tử",
    "ngữcảnh": "ngữ cảnh",
    "lựachọn": "lựa chọn",
    "trựctuyến": "trực tuyến",
    "ngoạituyến": "ngoại tuyến",
    "họckỳ": "học kỳ",
    "họcphần": "học phần",
    "từkhóa": "từ khóa",
    "mởkhóa": "mở khóa",
    "đóngkhóa": "đóng khóa",
    "tậpcon": "tập con",
    "tậphợp": "tập hợp",
    "phântích": "phân tích",
    "thiếtkế": "thiết kế",
    "càiđặt": "cài đặt",
    "kiểmthử": "kiểm thử",
}

# Compile case-preserving regex pattern
# Sort keys by length descending to match longest phrases first (e.g. cơsởdữliệu before dữliệu)
_SORTED_KEYS = sorted(GLUED_DICTIONARY.keys(), key=len, reverse=True)
_GLUED_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _SORTED_KEYS) + r")\b",
    re.IGNORECASE
)

# High-precision patterns for PDF extraction space restoration
_RE_WORD_NUM = re.compile(
    r"\b([a-zA-Zà-ỹÀ-Ỹ]*[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ][a-zA-Zà-ỹÀ-Ỹ]*)(\d+(?:\.\d+)*)\b"
)
_RE_WORD_MATH_INDEX = re.compile(
    r"\b([a-zA-Zà-ỹÀ-Ỹ]*[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ])([a-z]{1,2}th)\b"
)
_RE_WORD_MATH_VAR = re.compile(
    r"\b(số|gồm|với|trong|cho|tổng|tập|ma trận|vectơ|vector|khoảng)([A-Z])\b"
)
_RE_ACCENT_UPPER = re.compile(
    r"([àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵ])([A-Z])"
)
_RE_STRAY_BULLET = re.compile(
    r"([a-zà-ỹ,])\s*[•▪]\s*([a-zà-ỹ])"
)


def _match_case(original: str, replacement: str) -> str:
    """Preserve lower, UPPER, or Title casing."""
    if original.isupper():
        return replacement.upper()
    if original[0].isupper():
        # Title case each word in replacement
        return " ".join(word.capitalize() for word in replacement.split())
    return replacement.lower()


def heal_text(text: str) -> str:
    """Restore spaces to glued words and normalize legacy font glyphs (U+01A2/U+01A3 -> Ư/ư)."""
    if not text:
        return text

    # 1. Normalize legacy pseudo-Unicode font artifacts (e.g. VNU / old VNTimes converted fonts)
    if "\u01a2" in text or "\u01a3" in text:
        text = text.replace("\u01a2", "\u01af").replace("\u01a3", "\u01b0")

    # 2. Glued dictionary lookup
    def repl(m: re.Match) -> str:
        matched = m.group(0)
        target = GLUED_DICTIONARY.get(matched.lower(), matched)
        return _match_case(matched, target)

    text = _GLUED_PATTERN.sub(repl, text)

    # 3. Vietnamese word glued to number (e.g. thứ3 -> thứ 3, thức3.2 -> thức 3.2, hình4.1 -> hình 4.1)
    text = _RE_WORD_NUM.sub(r"\1 \2", text)

    # 4. Math index glued to Vietnamese word (e.g. thứkth -> thứ kth, ảnhith -> ảnh ith)
    text = _RE_WORD_MATH_INDEX.sub(r"\1 \2", text)

    # 5. Math variable glued to quantity word (e.g. sốN -> số N, gồmN -> gồm N)
    text = _RE_WORD_MATH_VAR.sub(r"\1 \2", text)

    # 6. Accented vowel glued to uppercase Latin (e.g. ởParis -> ở Paris)
    text = _RE_ACCENT_UPPER.sub(r"\1 \2", text)

    # 7. Stray bullet points inside sentences (e.g. các • hàm -> các hàm)
    text = _RE_STRAY_BULLET.sub(r"\1 \2", text)

    return text

