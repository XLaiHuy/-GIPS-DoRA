"""Prompt Templates for GIPS-DoRA Counterfactual Generation.

Defines prompt generation for:
- P-light: Orthographic & lexical polish, strict sentence-by-sentence preservation.
- P-medium: Sentence-level academic rewrite, permits merging/splitting.
- P-heavy: Structural & stylistic restructuring of paragraph.
- G-independent: Grounded academic generation from Semantic Skeleton only.
"""

import hashlib
from typing import Dict, Any, Tuple


SYSTEM_PROMPT_POLISH = (
    "Bạn là một chuyên gia hiệu đính và biên tập văn bản khoa học tiếng Việt chuyên sâu "
    "trong lĩnh vực Khoa học Máy tính và Công nghệ Thông tin. Nhiệm vụ của bạn là hiệu đính "
    "các đoạn trích luận văn tốt nghiệp theo đúng mức độ can thiệp được yêu cầu, "
    "duy trì tuyệt đối tính trang trọng, thuật ngữ chuyên ngành chuẩn xác và tính trung thực khoa học."
)

SYSTEM_PROMPT_GENERATION = (
    "Bạn là một nhà nghiên cứu Khoa học Máy tính giàu kinh nghiệm. Nhiệm vụ của bạn là "
    "viết một đoạn văn học thuật tiếng Việt độc lập, chuẩn mực cho luận văn tốt nghiệp ngành CNTT "
    "dựa trên các thông số kỹ thuật, khái niệm và luận điểm trong khung ngữ nghĩa (Semantic Skeleton) được cung cấp."
)


def compute_prompt_hash(system_prompt: str, user_prompt: str) -> str:
    combined = f"{system_prompt}\n---SPLIT---\n{user_prompt}".encode("utf-8")
    return hashlib.sha256(combined).hexdigest()[:16]


def build_polish_prompt(
    text: str,
    level: str = "medium",
    topic_cluster: str = "software_engineering",
    thesis_title: str = ""
) -> Tuple[str, str, str]:
    """Build system_prompt, user_prompt, and prompt_hash for AI Polish (P).

    level: 'light', 'medium', or 'heavy'
    """
    context_str = f"Chủ đề chuyên ngành: {topic_cluster}"
    if thesis_title:
        context_str += f" | Tên đề tài: {thesis_title}"

    if level == "light":
        instructions = (
            "YÊU CẦU HIỆU ĐÍNH MỨC ĐỘ NHẸ (P-LIGHT):\n"
            "1. Sửa các lỗi chính tả, lỗi đánh máy, lỗi dấu câu, và chuẩn hóa các từ dùng chưa chuẩn sang thuật ngữ kỹ thuật chính xác.\n"
            "2. BẢO TOÀN NGUYÊN VẸN cấu trúc câu: giữ nguyên số lượng câu và trật tự từng câu của đoạn gốc (tuyệt đối KHÔNG gộp hoặc tách câu).\n"
            "3. Không thêm bớt ý hoặc diễn đạt dài dòng.\n"
            "4. CHỈ XUẤT RA duy nhất đoạn văn sau khi hiệu đính, không kèm lời chào, giải thích, hoặc ký hiệu phụ."
        )
    elif level == "heavy":
        instructions = (
            "YÊU CẦU TÁI CẤU TRÚC MỨC ĐỘ MẠNH (P-HEAVY):\n"
            "1. Tái cấu trúc hoàn toàn phong cách hành văn và tổ chức luận điểm của đoạn văn theo chuẩn bài báo khoa học xuất sắc.\n"
            "2. Thay đổi cấu trúc diễn đạt, nâng tầm câu chữ sang văn phong học thuật cao cấp, mạch lạc và gãy gọn.\n"
            "3. BẢO TOÀN ĐẦY ĐỦ tất cả các thông tin kỹ thuật cốt lõi: tên công nghệ, thuật toán, thông số, số liệu và mối quan hệ logic.\n"
            "4. CHỈ XUẤT RA duy nhất đoạn văn sau khi viết lại, không kèm lời chào, giải thích, hoặc ký hiệu phụ."
        )
    else:  # medium
        instructions = (
            "YÊU CẦU HIỆU ĐÍNH MỨC ĐỘ VỪA (P-MEDIUM):\n"
            "1. Viết lại ở mức câu để diễn đạt lưu loát, mượt mà, đúng chuẩn văn phong học thuật tiếng Việt.\n"
            "2. Cho phép gộp các câu ngắn vụn vặt hoặc tách câu dài phức tạp để tăng tính mạch lạc của lập luận.\n"
            "3. GIỮ NGUYÊN toàn bộ nội dung, luận điểm và các thuật ngữ kỹ thuật của đoạn văn.\n"
            "4. CHỈ XUẤT RA duy nhất đoạn văn sau khi hiệu đính, không kèm lời chào, giải thích, hoặc ký hiệu phụ."
        )

    user_prompt = (
        f"{context_str}\n\n"
        f"{instructions}\n\n"
        f"--- ĐOẠN VĂN GỐC CẦN HIỆU ĐÍNH ---\n"
        f"{text.strip()}\n"
        f"----------------------------------"
    )

    p_hash = compute_prompt_hash(SYSTEM_PROMPT_POLISH, user_prompt)
    return SYSTEM_PROMPT_POLISH, user_prompt, p_hash


def build_generation_prompt(skeleton: Dict[str, Any]) -> Tuple[str, str, str]:
    """Build system_prompt, user_prompt, and prompt_hash for Independent Generation (G).

    Strictly satisfies Section 4.3: NO verbatim human text is included in the prompt.
    """
    topic = skeleton.get("topic_cluster", "software_engineering")
    thesis_title = skeleton.get("thesis_title") or "Nghiên cứu ứng dụng Công nghệ Thông tin"
    section_heading = skeleton.get("section_heading") or "Nội dung kỹ thuật trọng tâm"
    
    entities = ", ".join(skeleton.get("key_entities", [])) or "các công nghệ và kiến trúc liên quan"
    assertions = "\n".join(f"- {a}" for a in skeleton.get("core_assertions", [])) or "- Trình bày cơ chế hoạt động và lợi ích kỹ thuật"
    
    constraints = skeleton.get("numeric_constraints", [])
    constraints_str = ", ".join(constraints) if constraints else "Không có ràng buộc số liệu cụ thể"

    target_tokens = skeleton.get("target_tokens", 150)
    min_tokens = max(50, int(target_tokens * 0.75))
    max_tokens = int(target_tokens * 1.25)
    
    target_sents = skeleton.get("target_sentences", 5)
    min_sents = max(2, target_sents - 2)
    max_sents = target_sents + 3

    user_prompt = (
        f"Hãy viết một đoạn văn phong học thuật (academic tone) tiếng Việt cho luận văn tốt nghiệp ngành CNTT.\n\n"
        f"--- KHUNG NGỮ NGHĨA (SEMANTIC SKELETON) ---\n"
        f"• Chuyên ngành: {topic}\n"
        f"• Đề tài luận văn: {thesis_title}\n"
        f"• Tiêu đề mục / ngữ cảnh: {section_heading}\n"
        f"• Các khái niệm / thực thể công nghệ bắt buộc đề cập: {entities}\n"
        f"• Các luận điểm / sự kiện kỹ thuật cần chuyển tải:\n{assertions}\n"
        f"• Các số liệu / ràng buộc kỹ thuật (nếu có): {constraints_str}\n"
        f"--------------------------------------------\n\n"
        f"YÊU CẦU ĐỊNH DẠNG VÀ PHONG CÁCH:\n"
        f"1. Tự triển khai hành văn và liên kết ý theo phong cách học thuật tiếng Việt nghiêm túc, gãy gọn, mạch lạc.\n"
        f"2. Độ dài mong muốn: khoảng {min_tokens} đến {max_tokens} từ (khoảng {min_sents} đến {max_sents} câu).\n"
        f"3. Đảm bảo toàn bộ các thực thể công nghệ và luận điểm trong khung ngữ nghĩa được tích hợp hài hòa, logic.\n"
        f"4. CHỈ XUẤT RA duy nhất nội dung đoạn văn, không thêm tiêu đề, lời mở đầu, hoặc chú thích ngoài lề."
    )

    p_hash = compute_prompt_hash(SYSTEM_PROMPT_GENERATION, user_prompt)
    return SYSTEM_PROMPT_GENERATION, user_prompt, p_hash
