"""Deterministic Mock LLM Provider for offline testing, CI, and smoke validation."""

import re
from typing import Dict, Any, Optional
from counterfactual_pipeline.providers.base import BaseLLMProvider


class MockLLMProvider(BaseLLMProvider):
    def __init__(self, family: str = "mock", model_name: str = "mock-generator-v1"):
        super().__init__(family=family, model_name=model_name, rpm_limit=1000)

    def _call_api(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int
    ) -> str:
        # Check if this is an AI Polish prompt (contains raw text block)
        if "--- ĐOẠN VĂN GỐC CẦN HIỆU ĐÍNH ---" in user_prompt:
            raw_text = user_prompt.split("--- ĐOẠN VĂN GỐC CẦN HIỆU ĐÍNH ---")[1].split("----------------------------------")[0].strip()
            
            if "P-LIGHT" in user_prompt:
                # Simulate light polish: fix casing, clean spaces, minor synonym replacement
                polished = raw_text.replace("ui", "UI").replace("api", "API").replace("web", "Web")
                return polished
            elif "P-HEAVY" in user_prompt:
                # Simulate heavy polish: scholarly preamble + restructured synthesis
                return (
                    f"Từ góc độ phát triển hệ thống hiện đại, việc ứng dụng các giải pháp kiến trúc tiên tiến mang lại "
                    f"hiệu năng vượt trội và tính mở rộng cao. Cụ thể, các kết quả khảo sát cho thấy rằng: {raw_text} "
                    f"Những cải tiến này đóng vai trò then chốt trong việc tối ưu hóa hạ tầng triển khai phần mềm quy mô lớn."
                )
            else:  # P-medium
                # Simulate medium polish: smooth transitions
                return (
                    f"Trong bối cảnh kỹ thuật hiện đại, {raw_text} "
                    f"Cơ chế này góp phần củng cố tính toàn vẹn và nâng cao năng lực vận hành của toàn bộ hệ sinh thái ứng dụng."
                )

        # Check if this is an Independent Generation (G) prompt from Skeleton
        elif "KHUNG NGỮ NGHĨA (SEMANTIC SKELETON)" in user_prompt:
            # Extract key entities or topics from prompt
            entities_match = re.search(r"bắt buộc đề cập:\s*([^\n]+)", user_prompt)
            entities_str = entities_match.group(1).strip() if entities_match else "các công nghệ trọng tâm"
            
            topic_match = re.search(r"Chuyên ngành:\s*([^\n]+)", user_prompt)
            topic_str = topic_match.group(1).strip() if topic_match else "Công nghệ Thông tin"

            return (
                f"Trong khuôn khổ nghiên cứu chuyên ngành {topic_str}, việc thiết kế và hiện thực hóa các giải pháp "
                f"dựa trên {entities_str} đóng vai trò đặc biệt quan trọng. Hệ thống được xây dựng nhằm đáp ứng "
                f"các yêu cầu nghiêm ngặt về hiệu năng tính toán, độ trễ xử lý và khả năng thích ứng linh hoạt trong môi trường phân tán. "
                f"Thông qua quá trình thực nghiệm và kiểm thử đa diện, các mô hình thành phần đã chứng minh được tính ổn định "
                f"và hiệu quả vượt trội, mở ra hướng ứng dụng thực tiễn bền vững cho các bài toán khoa học dữ liệu và kỹ thuật phần mềm."
            )

        # Fallback generic academic response
        return (
            "Nghiên cứu này đề xuất một giải pháp tổng thể nhằm giải quyết triệt để bài toán kỹ thuật đặt ra. "
            "Các kết quả đánh giá thực nghiệm cho thấy phương pháp đạt được hiệu năng cao và độ chính xác vượt trội."
        )
