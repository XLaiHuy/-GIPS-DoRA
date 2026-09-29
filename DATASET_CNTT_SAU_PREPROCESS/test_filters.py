# -*- coding: utf-8 -*-
"""
test_filters.py: Unit test kiểm tra độ chính xác của bộ lọc chuyên ngành CNTT
"""
import unittest
from crawlers.filters import is_it_topic

class TestITFilters(unittest.TestCase):
    def test_it_faculty_match(self):
        # Thuộc khoa CNTT -> Luôn nhận
        is_it, tags = is_it_topic(
            title="Nghiên cứu kiến trúc vi điều khiển",
            faculty="Khoa Công nghệ thông tin - Đại học Quốc gia"
        )
        self.assertTrue(is_it)
        self.assertTrue(any("faculty" in t for t in tags))

    def test_it_keywords_match(self):
        # Tiêu đề chứa AI, Deep Learning
        is_it, tags = is_it_topic(
            title="Ứng dụng mô hình Transformer và Deep Learning trong nhận dạng ảnh y tế",
            faculty="Trường Đại học Công nghệ"
        )
        self.assertTrue(is_it)
        self.assertIn("deep learning", tags)
        self.assertIn("transformer", tags)

    def test_non_it_rejection(self):
        # Báo cáo tài chính, kế toán ngân hàng -> Loại bỏ
        is_it, _ = is_it_topic(
            title="Phân tích tình hình tài chính và rủi ro tín dụng tại Ngân hàng Vietcombank",
            faculty="Khoa Tài chính - Ngân hàng"
        )
        self.assertFalse(is_it)

    def test_it_applied_in_business(self):
        # Xây dựng phần mềm kế toán -> Vẫn nhận vì là sản phẩm CNTT
        is_it, tags = is_it_topic(
            title="Xây dựng phần mềm kế toán doanh nghiệp vừa và nhỏ",
            faculty="Khoa Công nghệ thông tin"
        )
        self.assertTrue(is_it)

if __name__ == "__main__":
    unittest.main()
