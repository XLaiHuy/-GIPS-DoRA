import unittest
from text_healing import heal_text

class TestTextHealing(unittest.TestCase):
    def test_lowercase(self):
        self.assertEqual(heal_text("dữliệu được sửdụng"), "dữ liệu được sử dụng")
        self.assertEqual(heal_text("bộdữliệu cơsởdữliệu"), "bộ dữ liệu cơ sở dữ liệu")

    def test_titlecase(self):
        self.assertEqual(heal_text("Dữliệu lớn và Kỹthuật"), "Dữ Liệu lớn và Kỹ Thuật")

    def test_uppercase(self):
        self.assertEqual(heal_text("HỆTHỐNG DỮLIỆU"), "HỆ THỐNG DỮ LIỆU")

    def test_unrelated_words_intact(self):
        self.assertEqual(heal_text("dữ dội và sử sách"), "dữ dội và sử sách")
        self.assertEqual(heal_text("máy tính cá nhân"), "máy tính cá nhân")

if __name__ == "__main__":
    unittest.main()
