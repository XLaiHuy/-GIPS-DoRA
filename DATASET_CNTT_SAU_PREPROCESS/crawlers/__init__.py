"""
Crawlers package for Thesis, Dissertation & Capstone Project Dataset
"""
from .base_crawler import BaseCrawler
from .ou_crawler import OUCrawler
from .vnu_crawler import VNUCrawler
from .ojs_crawler import OJSCrawler
from .ute_crawler import UTECrawler
from .dut_crawler import DUTCrawler
from .github_crawler import GitHubCrawler
from .edu_crawler import EduCrawler

__all__ = ["BaseCrawler", "OUCrawler", "VNUCrawler", "OJSCrawler", "UTECrawler", "DUTCrawler", "GitHubCrawler", "EduCrawler"]

