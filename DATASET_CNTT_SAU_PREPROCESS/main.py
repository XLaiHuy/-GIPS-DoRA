# -*- coding: utf-8 -*-
"""
main.py: CLI điều khiển thu thập dataset đồ án, khóa luận, luận văn & bài báo khoa học CNTT đa nguồn.
Hỗ trợ các nguồn học thuật uy tín:
- OJS: Tạp chí Tin học & Điều khiển học (VAST) và Chuyên san KH Máy tính & Truyền thông (VNU JCSCE)
- OU: Thư viện Đại học Mở TP.HCM (Khóa luận tốt nghiệp, Luận văn Thạc sĩ)
- VNU: Kho lưu trữ số DSpace ĐHQG Hà Nội
"""
import sys
# Đảm bảo in đúng tiếng Việt UTF-8 trên Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import argparse
import logging
import os
from crawlers.vnu_crawler import VNUCrawler
from crawlers.ou_crawler import OUCrawler
from crawlers.ojs_crawler import OJSCrawler
from crawlers.ute_crawler import UTECrawler
from crawlers.dut_crawler import DUTCrawler
from crawlers.github_crawler import GitHubCrawler
from crawlers.edu_crawler import EduCrawler
from merge_datasets import merge_and_stats


def audit_dataset(pdf_dir: str):
    """Kiểm toán nhanh số lượng file PDF text và scan trong thư mục đích."""
    if not os.path.exists(pdf_dir):
        print(f"[!] Thư mục {pdf_dir} không tồn tại.")
        return 0, 0
    files = [f for f in os.listdir(pdf_dir) if f.lower().endswith(".pdf")]
    text_count = 0
    scan_count = 0
    for fn in files:
        fp = os.path.join(pdf_dir, fn)
        try:
            sz = os.path.getsize(fp)
            if sz > 35 * 1024 * 1024:
                scan_count += 1
                continue
            with open(fp, "rb") as f:
                head = f.read(min(sz, 500000))
            if head.count(b"/Font") >= 2:
                text_count += 1
            else:
                # Fallback kiểm tra text layer thực tế bằng pypdf
                try:
                    from pypdf import PdfReader
                    r = PdfReader(fp, strict=False)
                    sample = "".join([r.pages[i].extract_text() or "" for i in range(min(5, len(r.pages)))])
                    if len(sample.strip()) >= 500:
                        text_count += 1
                    else:
                        scan_count += 1
                except Exception:
                    scan_count += 1
        except Exception:
            scan_count += 1

    print("\n" + "=" * 60)
    print("           BÁO CÁO KIỂM TOÁN CHẤT LƯỢNG DATASET")
    print("=" * 60)
    print(f"[*] Thư mục: {pdf_dir}")
    print(f"[*] Tổng số file PDF: {len(files)}")
    print(f"[*] PDF DẠNG VĂN BẢN TRÍCH XUẤT ĐƯỢC (TEXT-BASED): {text_count} ({text_count/max(len(files),1)*100:.1f}%)")
    print(f"[*] PDF SCAN ẢNH / KHÔNG CÓ FONT:                  {scan_count} ({scan_count/max(len(files),1)*100:.1f}%)")
    print("=" * 60)
    return text_count, scan_count

def parse_args():
    parser = argparse.ArgumentParser(
        description="Tool cào dataset Khóa luận, Luận văn & Bài báo KH CNTT đa nguồn (OJS, OU, VNU...)"
    )
    parser.add_argument(
        "--source",
        choices=["all", "ou", "vnu", "ute", "dut", "ojs", "github", "edu"],
        default="edu",
        help="Nguồn dữ liệu: 'edu' (Đồ án/Khóa luận toàn văn trên các máy chủ giáo dục .edu.vn), 'github' (GitHub repos), 'dut' (ĐH Bách Khoa Đà Nẵng), 'ute' (ĐH Sư phạm Kỹ thuật TP.HCM), 'ou' (ĐH Mở TP.HCM), 'vnu' (DSpace ĐHQGHN), 'ojs' (Tạp chí), hoặc 'all'."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Giới hạn số lượng bản ghi mới cần cào trên mỗi nguồn (Mặc định: không giới hạn)."
    )
    parser.add_argument(
        "--delay-min",
        type=float,
        default=0.8,
        help="Độ trễ tối thiểu giữa các request (giây, mặc định: 0.8s)."
    )
    parser.add_argument(
        "--delay-max",
        type=float,
        default=1.8,
        help="Độ trễ tối đa giữa các request (giây, mặc định: 1.8s)."
    )
    parser.add_argument(
        "--download-pdf",
        action="store_true",
        default=True,
        help="Tự động tải trọn vẹn file PDF đồ án/luận văn/bài báo (Mặc định: Bật)."
    )
    parser.add_argument(
        "--pdf-dir",
        type=str,
        default=r"D:\Documents\NCKH- Đồ án\NCKH về NLP, VLM, Vision\Dataset_khoaluan",
        help="Thư mục đích lưu file PDF tải về."
    )
    parser.add_argument(
        "--no-pdf",
        action="store_true",
        help="Không tải file PDF (chỉ cào metadata và abstract)."
    )
    parser.add_argument(
        "--only-pdf",
        action="store_true",
        default=True,
        help="Chỉ lưu vào dataset những đề tài/bài báo nào tải được file PDF hoàn chỉnh có text layer."
    )
    parser.add_argument(
        "--ou-user",
        type=str,
        default=None,
        help="Tên đăng nhập thư viện ĐH Mở TP.HCM."
    )
    parser.add_argument(
        "--ou-pass",
        type=str,
        default=None,
        help="Mật khẩu tài khoản thư viện ĐH Mở TP.HCM."
    )
    parser.add_argument(
        "--github-token",
        type=str,
        default=None,
        help="GitHub Personal Access Token (tùy chọn, để tăng rate limit)."
    )
    parser.add_argument(
        "--max-year",
        type=int,
        default=2022,
        help="Giới hạn năm xuất bản tối đa (Mặc định: 2022 - đảm bảo 100%% con người viết trước kỷ nguyên ChatGPT)."
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=5,
        help="Số luồng chạy Multi-threading cho crawler (Mặc định: 5 luồng)."
    )
    parser.add_argument(
        "--auth-file",
        type=str,
        default="library_auth.json",
        help="Đường dẫn file cấu hình tài khoản / cookie thư viện các trường đại học (Mặc định: library_auth.json)."
    )
    parser.add_argument(
        "--audit-only",
        action="store_true",
        help="Chỉ kiểm toán số lượng PDF Text vs Scan hiện có rồi thoát."
    )
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="Không tự động chạy gộp và thống kê dataset sau khi cào xong."
    )
    return parser.parse_args()

def main():
    args = parse_args()

    if args.audit_only:
        audit_dataset(args.pdf_dir)
        return

    print("=" * 60)
    print("   HỆ THỐNG THU THẬP DATASET CNTT ĐA NGUỒN (VAST, VNU, OU)")
    print("=" * 60)
    print(f"[*] Nguồn mục tiêu: {args.source.upper()}")
    print(f"[*] Giới hạn mỗi nguồn: {args.limit if args.limit is not None else 'Toàn bộ (Unlimited)'}")
    print(f"[*] Thư mục lưu PDF: {args.pdf_dir}")
    print(f"[*] Năm xuất bản tối đa: <= {args.max_year} (Pure Human Text, Zero AI Contamination)")
    print(f"[*] Cơ chế chống ban: Jitter Delay ngẫu nhiên từ {args.delay_min}s đến {args.delay_max}s")
    print(f"[*] Tự động Resume: Bật (qua json/.checkpoint.json)")
    download_pdf = not args.no_pdf
    print(f"[*] Chế độ tải Full PDF: {'BẬT' if download_pdf else 'TẮT'}")
    if args.only_pdf:
        print(f"[*] Bộ lọc nghiêm ngặt: 100%% Digital Text PDF (Tự động loại bỏ file Scan ảnh thuần)!")
    print("=" * 60)

    total_crawled = 0

    # 1. Chạy OJS Crawler (Chỉ khi người dùng yêu cầu riêng qua --source ojs)
    if args.source == "ojs":
        try:
            ojs = OJSCrawler(
                output_file="json/academic_papers_it.jsonl",
                checkpoint_file="json/.checkpoint.json",
                pdf_dir=args.pdf_dir,
                min_delay=args.delay_min,
                max_delay=args.delay_max,
                download_pdf=download_pdf,
                only_pdf=args.only_pdf,
                max_year=args.max_year
            )
            count = ojs.crawl(limit=args.limit)
            total_crawled += count
        except KeyboardInterrupt:
            print("\n[!] Đã dừng OJS Crawler theo yêu cầu người dùng. Checkpoint đã được lưu an toàn.")
        except Exception as e:
            logging.error(f"Lỗi không mong muốn trong quá trình cào OJS: {e}", exc_info=True)

    # 2. Chạy OU Crawler (Khóa luận tốt nghiệp & Luận văn Thạc sĩ ĐH Mở TP.HCM)
    if args.source in ["all", "ou"]:
        try:
            ou = OUCrawler(
                output_file="json/ou_it_theses.jsonl",
                checkpoint_file="json/.checkpoint.json",
                pdf_dir=args.pdf_dir,
                min_delay=args.delay_min,
                max_delay=args.delay_max,
                download_pdf=download_pdf,
                only_pdf=args.only_pdf,
                ou_user=args.ou_user,
                ou_pass=args.ou_pass,
                max_year=args.max_year
            )
            count = ou.crawl(limit=args.limit)
            total_crawled += count
        except KeyboardInterrupt:
            print("\n[!] Đã dừng OU Crawler theo yêu cầu người dùng. Checkpoint đã được lưu an toàn.")
        except Exception as e:
            logging.error(f"Lỗi không mong muốn trong quá trình cào OU: {e}", exc_info=True)

    # 3. Chạy VNU Crawler (DSpace ĐHQG Hà Nội)
    if args.source in ["all", "vnu"]:
        try:
            vnu = VNUCrawler(
                output_file="json/vnu_it_theses.jsonl",
                checkpoint_file="json/.checkpoint.json",
                pdf_dir=args.pdf_dir,
                min_delay=args.delay_min,
                max_delay=args.delay_max,
                download_pdf=download_pdf,
                only_pdf=args.only_pdf,
                max_year=args.max_year,
                auth_file=args.auth_file
            )
            count = vnu.crawl(limit=args.limit)
            total_crawled += count
        except KeyboardInterrupt:
            print("\n[!] Đã dừng VNU Crawler theo yêu cầu người dùng. Checkpoint đã được lưu an toàn.")
        except Exception as e:
            logging.error(f"Lỗi không mong muốn trong quá trình cào VNU: {e}", exc_info=True)

    # 4. Chạy UTE Crawler (Đồ án, Khóa luận & Luận văn ĐH Sư phạm Kỹ thuật TP.HCM)
    if args.source in ["all", "ute"]:
        try:
            ute = UTECrawler(
                output_file="json/ute_it_theses.jsonl",
                checkpoint_file="json/.checkpoint.json",
                pdf_dir=args.pdf_dir,
                min_delay=args.delay_min,
                max_delay=args.delay_max,
                download_pdf=download_pdf,
                only_pdf=args.only_pdf,
                max_year=args.max_year,
                auth_file=args.auth_file
            )
            count = ute.crawl(limit=args.limit)
            total_crawled += count
        except KeyboardInterrupt:
            print("\n[!] Đã dừng UTE Crawler theo yêu cầu người dùng. Checkpoint đã được lưu an toàn.")
        except Exception as e:
            logging.error(f"Lỗi không mong muốn trong quá trình cào UTE: {e}", exc_info=True)

    # 5. Chạy DUT Crawler (Đồ án, Khóa luận & Luận văn ĐH Bách Khoa - ĐH Đà Nẵng)
    if args.source in ["all", "dut"]:
        try:
            dut = DUTCrawler(
                output_file="json/dut_it_theses.jsonl",
                checkpoint_file="json/.checkpoint.json",
                pdf_dir=args.pdf_dir,
                min_delay=args.delay_min,
                max_delay=args.delay_max,
                download_pdf=download_pdf,
                only_pdf=args.only_pdf,
                max_year=args.max_year
            )
            count = dut.crawl(limit=args.limit)
            total_crawled += count
        except KeyboardInterrupt:
            print("\n[!] Đã dừng DUT Crawler theo yêu cầu người dùng. Checkpoint đã được lưu an toàn.")
        except Exception as e:
            logging.error(f"Lỗi không mong muốn trong quá trình cào DUT: {e}", exc_info=True)

    # 6. Chạy GitHub Crawler (Đồ án, Khóa luận & Luận văn CNTT trên GitHub <= 2022)
    if args.source in ["all", "github"]:
        try:
            gh = GitHubCrawler(
                output_file="json/github_theses.jsonl",
                checkpoint_file="json/.checkpoint.json",
                pdf_dir=args.pdf_dir,
                github_token=args.github_token,
                min_delay=args.delay_min,
                max_delay=args.delay_max,
                max_year=args.max_year,
                max_workers=args.workers
            )
            count = gh.crawl(limit=args.limit)
            total_crawled += count
        except KeyboardInterrupt:
            print("\n[!] Đã dừng GitHub Crawler theo yêu cầu người dùng. Checkpoint đã được lưu an toàn.")
        except Exception as e:
            logging.error(f"Lỗi không mong muốn trong quá trình cào GitHub: {e}", exc_info=True)

    # 7. Chạy Edu Crawler (Đồ án / Khóa luận toàn văn trên tên miền giáo dục .edu.vn)
    if args.source in ["all", "edu"]:
        try:
            edu = EduCrawler(
                output_file="json/edu_theses.jsonl",
                checkpoint_file="json/.checkpoint.json",
                pdf_dir=args.pdf_dir,
                min_delay=args.delay_min,
                max_delay=args.delay_max,
                max_year=args.max_year,
                max_workers=args.workers
            )
            count = edu.crawl(limit=args.limit)
            total_crawled += count
        except KeyboardInterrupt:
            print("\n[!] Đã dừng Edu Crawler theo yêu cầu người dùng. Checkpoint đã được lưu an toàn.")
        except Exception as e:
            logging.error(f"Lỗi không mong muốn trong quá trình cào Edu: {e}", exc_info=True)

    print("\n" + "=" * 60)
    print(f"[+] TỔNG SỐ BẢN GHI MỚI ĐÃ THU THẬP ĐƯỢC: {total_crawled}")
    print("=" * 60)

    # 4. Tự động gộp và xuất thống kê
    if not args.no_merge and total_crawled > 0:
        try:
            print("\n[*] Đang tổng hợp toàn bộ các nguồn dữ liệu vào file master...")
            merge_and_stats()
        except Exception as e:
            logging.error(f"Lỗi khi chạy merge_datasets: {e}")

    # 5. Kiểm toán chất lượng dataset hiện tại
    audit_dataset(args.pdf_dir)

if __name__ == "__main__":
    main()
