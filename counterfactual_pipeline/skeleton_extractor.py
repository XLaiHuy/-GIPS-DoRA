"""Semantic Skeleton Extractor for GIPS-DoRA Counterfactual Generation.

Extracts:
1. Technical entities (acronyms, frameworks, algorithms, technologies).
2. Numeric constraints & metrics (percentages, versions, dimensions).
3. Core assertions & technical functional statements.
4. Target length metrics (token count bucket, sentence count bounds).

These skeletons are fed into independent AI generators (G) without any verbatim human text,
satisfying the Section 4.3 requirement of the GIPS-DoRA architecture.
"""

import re
import json
from pathlib import Path
from typing import Dict, List, Optional, Any

# Token approximation matching project standard
APPROX_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)

# Common technical acronyms in CS
ACRONYM_RE = re.compile(r"\b[A-Z0-9]{2,}(?:[-.][A-Z0-9]+)*\b")

# Hyphenated model/architecture names (e.g. ResNet-50, BERT-base, GPT-4, VGG-16, YOLO-v8)
HYPHEN_TECH_RE = re.compile(r"\b[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+\b")

# CamelCase / Mixed-case tech terms (e.g. PyTorch, ASP.NET, MySQL, MongoDB, AngularJS, VueJS)
TECH_TERM_RE = re.compile(
    r"\b(?:[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+|\.?[A-Z]+[a-z0-9]+\.[A-Za-z0-9]+|[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)+)\b"
)

# Numeric metrics, percentages, hyperparameters, versions
NUMERIC_CONSTRAINT_RE = re.compile(
    r"(?:\b\d+(?:\.\d+)?\s*(?:%|ms|s|fps|kb|mb|gb|ghz|mhz|pixels?|điểm|lần|bước|epoch|epochs|batch|k|K|M|B)\b|"
    r"\b[A-Za-z_]+\s*=\s*\d+(?:\.\d+)?|"
    r"\bv\d+(?:\.\d+)*\b|"
    r"\b\d+\.\d+\b)"
)

# Vietnamese key functional verbal cues
ASSERTION_CUES = [
    "hợp nhất", "tích hợp", "tối ưu", "cung cấp", "hỗ trợ", "xây dựng", "phát triển",
    "áp dụng", "triển khai", "đánh giá", "phân tích", "nâng cao", "cải tiến", "tự động",
    "xử lý", "kết nối", "huấn luyện", "đạt được", "thực hiện", "thiết kế", "kiểm thử"
]

STOP_TERMS = {
    "NÀY", "TRÊN", "DƯỚI", "CHÚNG", "MỘT", "CÁC", "NHỮNG", "VIỆC", "THEO",
    "TRONG", "CHO", "VỚI", "KHI", "ĐƯỢC", "LÀ", "CỦA", "VÀ", "HOẶC", "HÌNH", "BẢNG"
}


def approx_tokens(text: str) -> int:
    return len(APPROX_TOKEN_RE.findall(text))


def get_token_bucket(token_count: int) -> str:
    if token_count < 128:
        return "64-127"
    elif token_count < 256:
        return "128-255"
    elif token_count <= 512:
        return "256-512"
    else:
        return ">512"


class SemanticSkeletonExtractor:
    def __init__(self, doc_metadata_path: Optional[Path] = None):
        self.doc_titles: Dict[str, str] = {}
        if doc_metadata_path and doc_metadata_path.exists():
            self._load_doc_titles(doc_metadata_path)

    def _load_doc_titles(self, doc_metadata_path: Path):
        with open(doc_metadata_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    doc = json.loads(line)
                    doc_id = doc.get("document_id")
                    if doc_id:
                        self.doc_titles[doc_id] = doc.get("title", "")
                except Exception:
                    continue

    def extract_entities(self, text: str) -> List[str]:
        """Extract technical terms, acronyms, and frameworks."""
        entities = set()
        
        # 1. Acronyms (e.g. API, DI, IIS, CNN, LSTM)
        for m in ACRONYM_RE.findall(text):
            if m.upper() not in STOP_TERMS and len(m) >= 2 and not m.isdigit():
                entities.add(m)

        # 2. Hyphenated technical terms (e.g. ResNet-50, BERT-base, GPT-4)
        for m in HYPHEN_TECH_RE.findall(text):
            if any(c.isalpha() for c in m) and len(m) >= 3:
                entities.add(m)

        # 3. Tech terms (e.g. ASP.NET, .NET Core, PyTorch)
        for m in TECH_TERM_RE.findall(text):
            if len(m) >= 3 and not m.isdigit():
                entities.add(m)

        # 3. Quoted or parenthesized terms (often key technical concepts)
        for m in re.findall(r"[\"']([^\"']{2,40})[\"']", text):
            if m.strip():
                entities.add(m.strip())

        for m in re.findall(r"\(([^)]{2,40})\)", text):
            clean_m = m.strip()
            # If it looks like an acronym or technical abbreviation
            if any(c.isupper() for c in clean_m) and len(clean_m.split()) <= 4:
                entities.add(clean_m)

        # Filter out numbers and generic single characters
        clean_entities = [
            e for e in sorted(entities)
            if not e.isnumeric() and len(e) > 1 and e.upper() not in STOP_TERMS
        ]
        return clean_entities[:15]

    def extract_numeric_constraints(self, text: str) -> List[str]:
        """Extract quantitative benchmarks, parameters, and versions."""
        matches = NUMERIC_CONSTRAINT_RE.findall(text)
        seen = set()
        results = []
        for m in matches:
            m_clean = m.strip()
            if m_clean not in seen:
                seen.add(m_clean)
                results.append(m_clean)
        return results[:8]

    def extract_assertions(self, text: str, sentences: Optional[List[str]] = None) -> List[str]:
        """Extract key technical assertions or capabilities."""
        if not sentences:
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]

        assertions = []
        for s in sentences:
            # Check if sentence contains functional cues
            s_lower = s.lower()
            if any(cue in s_lower for cue in ASSERTION_CUES):
                # Clean up leading numbers/bullets if any
                clean_s = re.sub(r"^(?:\d+[\.\)]|\-|\*|•)\s*", "", s).strip()
                if len(clean_s.split()) >= 4 and len(clean_s) < 200:
                    assertions.append(clean_s)

        # If too few found, fallback to first and key sentences
        if not assertions and sentences:
            assertions = [sentences[0]]

        return assertions[:6]

    def extract_skeleton(self, passage: Dict[str, Any]) -> Dict[str, Any]:
        """Extract a complete Semantic Skeleton from a human passage record."""
        passage_id = passage.get("passage_id", "")
        doc_id = passage.get("source_document_id", passage.get("document_id", ""))
        topic = passage.get("topic_cluster", "software_engineering")
        text = passage.get("text", "")
        
        thesis_title = passage.get("thesis_title") or self.doc_titles.get(doc_id, "")
        section_heading = passage.get("section_heading")

        tok_count = passage.get("approx_tokens") or approx_tokens(text)
        sent_count = passage.get("sentence_count") or max(1, len(re.split(r"(?<=[.!?])\s+", text.strip())))

        entities = self.extract_entities(text)
        constraints = self.extract_numeric_constraints(text)
        assertions = self.extract_assertions(text)

        bucket = get_token_bucket(tok_count)

        return {
            "source_passage_id": passage_id,
            "source_document_id": doc_id,
            "thesis_title": thesis_title if thesis_title else None,
            "section_heading": section_heading if section_heading else None,
            "topic_cluster": topic,
            "target_length_bucket": bucket,
            "target_tokens": tok_count,
            "target_sentences": sent_count,
            "key_entities": entities,
            "core_assertions": assertions,
            "numeric_constraints": constraints,
            "extractor_version": "v1.0"
        }
