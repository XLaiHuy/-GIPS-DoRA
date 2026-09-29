"""Sentence segmentation and alignment for counterfactual generated text."""

import re
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional

# Add project root to sys.path to import text_healing if needed
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "human_dataset_pipeline"))
try:
    from text_healing import heal_text
except ImportError:
    def heal_text(t: str) -> str:
        return t

APPROX_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)

# Common abbreviations in Vietnamese / CS academic text that should not trigger sentence split
ABBREV_RE = re.compile(
    r"\b(?:v\.v|v\.d|v\.b|vd|e\.g|i\.e|etc|al|th|tp|ts|th\.\s*s|gs|pgs|đh|cntt|vs)\.$",
    re.IGNORECASE
)

# Numbered labels like "hình 3.", "bảng 2.", "mục 1."
LABEL_DOT_RE = re.compile(r"\b(?:hình|bảng|mục|chương|phần|bước)\s+\d+\.$", re.IGNORECASE)


def approx_tokens(text: str) -> int:
    return len(APPROX_TOKEN_RE.findall(text))


def clean_llm_markdown(text: str) -> str:
    """Strip markdown code fences and extraneous quotes often emitted by LLMs."""
    cleaned = text.strip()
    
    # Strip markdown code blocks ```...```
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
        cleaned = cleaned.strip()

    # Strip surrounding quotation marks if the whole text is wrapped
    if (cleaned.startswith('"') and cleaned.endswith('"')) or (cleaned.startswith("'") and cleaned.endswith("'")):
        cleaned = cleaned[1:-1].strip()

    # Strip common preface artifacts
    cleaned = re.sub(r"^(?:Dưới đây là|Sau đây là|Đoạn văn sau khi hiệu đính:?|Đoạn văn học thuật:?)\s*", "", cleaned, flags=re.IGNORECASE)
    
    return cleaned.strip()


def segment_sentences(text: str) -> List[str]:
    """Segment Vietnamese academic text into clean, coherent sentences."""
    cleaned = clean_llm_markdown(text)
    if not cleaned:
        return []

    # Initial rough split on terminal punctuation
    raw_parts = re.split(r"([.!?…]+(?:\s+|$))", cleaned)
    
    sentences = []
    current = ""

    i = 0
    while i < len(raw_parts):
        chunk = raw_parts[i]
        if not chunk:
            i += 1
            continue

        current += chunk

        # If chunk is punctuation or followed by punctuation
        if i + 1 < len(raw_parts) and re.match(r"^[.!?…]+(?:\s+|$)", raw_parts[i + 1]):
            current += raw_parts[i + 1]
            i += 1

        curr_trimmed = current.strip()

        # Check if current ends with an abbreviation or decimal point
        is_abbrev = bool(ABBREV_RE.search(curr_trimmed)) or bool(LABEL_DOT_RE.search(curr_trimmed))
        is_decimal = bool(re.search(r"\b\d+\.$", curr_trimmed))

        if not is_abbrev and not is_decimal and re.search(r"[.!?…]$", curr_trimmed):
            if curr_trimmed:
                sentences.append(curr_trimmed)
            current = ""

        i += 1

    if current.strip():
        sentences.append(current.strip())

    # Heal broken fragments / lowercase continuations
    merged = []
    for s in sentences:
        s_clean = heal_text(s.strip())
        if not s_clean:
            continue
        
        if merged and (s_clean[0].islower() or (not merged[-1].endswith((".", "!", "?", "…")) and len(s_clean.split()) < 4)):
            merged[-1] = f"{merged[-1]} {s_clean}"
        else:
            merged.append(s_clean)

    return merged


def align_and_package_sentences(
    raw_text: str,
    record_id: str,
    label_id: int
) -> Dict[str, Any]:
    """Cleans, heals, segments, and packages sentences for a counterfactual record."""
    sentences_text = segment_sentences(raw_text)
    full_healed_text = " ".join(sentences_text)

    sentence_records = []
    for idx, s_text in enumerate(sentences_text):
        s_id = f"{record_id}_sent_{idx:03d}"
        sentence_records.append({
            "sentence_id": s_id,
            "ordinal": idx,
            "text": s_text,
            "approx_tokens": approx_tokens(s_text),
            "label_id": label_id
        })

    return {
        "text": full_healed_text,
        "approx_tokens": approx_tokens(full_healed_text),
        "sentence_count": len(sentence_records),
        "sentences": sentence_records
    }
