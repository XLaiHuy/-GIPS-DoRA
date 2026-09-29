"""Smart DataLoader & Curated View Processor for GIPS-DoRA.
Addresses:
1. Sentence oversplitting (auto-merges lowercase-initial lines and fragments into complete sentences).
2. Word-gluing (applies dictionary-based space restoration via text_healing.py).
3. Short-tail passage inflation (filters high-quality passages >= min_tokens for Counterfactual generation).
4. Topic cluster enrichment (attaches canonical CS topic clusters for Topic Invariance ablation).
"""

import json
import re
from pathlib import Path
from collections import Counter
from text_healing import heal_text

APPROX_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def approx_tokens(text: str) -> int:
    return len(APPROX_TOKEN_RE.findall(text))


HEADING_PREFIX_RE = re.compile(
    r"^(\d+(\.\d+)*\.?\s+|[IVXLCDM]+\.\s+|(?:Chương|Mục|Phần|Bước)\s+\d+|[•▪\*\+]\s*[A-ZÀ-Ỹ0-9]|\-\s+[A-ZÀ-Ỹ0-9])",
    re.UNICODE
)

BULLET_LOWER_RE = re.compile(r"^[•▪\*\+]\s*([a-zà-ỹ])")

CONTINUATION_CONJUNCTIONS = {
    "và", "hoặc", "nhưng", "mà", "được", "là", "trong", "với", "để", "do", "bởi", "thì", "tại", "như"
}


def should_merge(prev_sent: str, curr_sent: str) -> bool:
    """Determine if curr_sent is a continuation of prev_sent."""
    if not prev_sent or not curr_sent:
        return False
    prev_clean = prev_sent.strip()
    curr_clean = curr_sent.strip()
    if not prev_clean or not curr_clean:
        return False

    # Never merge if current line is a formal heading/numbered section
    if HEADING_PREFIX_RE.match(curr_clean):
        return False

    # 0. If current starts with a bullet followed by lowercase, it is a wrapped line artifact
    if BULLET_LOWER_RE.match(curr_clean):
        return True

    # 1. curr starts with a lowercase letter
    if curr_clean[0].islower():
        return True

    # 2. prev ends with a comma or hyphen
    if prev_clean[-1] in ",-":
        return True

    # 3. prev ends without terminal punctuation, merge only if curr is clearly a continuation
    if prev_clean[-1] not in ".!?…\"'”’:;":
        first_word = curr_clean.split()[0].lower() if curr_clean.split() else ""
        if curr_clean[0].islower() or first_word in CONTINUATION_CONJUNCTIONS:
            return True
        words = prev_clean.split()
        if len(words) <= 3:
            return True
        return False

    return False


RUNNING_HEADER_RE = re.compile(
    r"^(?:"
    r"LUẬN\s+VĂN\s+TỐT\s+NGHIỆP.*|"
    r"KHÓA\s+LUẬN\s+TỐT\s+NGHIỆP.*|"
    r"ĐỒ\s+ÁN\s+TỐT\s+NGHIỆP.*|"
    r"BÁO\s+CÁO\s+TỐT\s+NGHIỆP.*|"
    r"TRƯỜNG\s+ĐẠI\s+HỌC\s+.*|"
    r"ĐẠI\s+HỌC\s+QUỐC\s+GIA\s+.*|"
    r"HỌC\s+VIỆN\s+CÔNG\s+NGHỆ\s+.*|"
    r"CHƯƠNG\s+\d+.*|"
    r"Trang\s+\d+|"
    r"Page\s+\d+"
    r")$",
    re.IGNORECASE
)


def is_running_header(text: str) -> bool:
    t = text.strip()
    if len(t.split()) > 15:
        return False
    return bool(RUNNING_HEADER_RE.match(t))


NON_BODY_PATTERNS = [
    re.compile(r"(\.{4,}\s*\d+|\bMỤC\s+LỤC\b|\bDANH\s+MỤC\s+(?:HÌNH|BẢNG|TỪ\s+VIẾT\s+TẮT)\b)", re.I),
    re.compile(r"(\bLỜI\s+CẢM\s+ƠN\b|\bLỜI\s+CAM\s+ĐOAN\b|\bLỜI\s+TRI\s+ÂN\b|chân\s+thành\s+cảm\s+ơn|xin\s+(?:gửi\s+lời\s+)?cảm\s+ơn|tỏ\s+lòng\s+biết\s+ơn|biết\s+ơn\s+sâu\s+sắc|tận\s+tình\s+dạy\s+dỗ)", re.I),
    re.compile(r"(\bTÀI\s+LIỆU\s+THAM\s+KHẢO\b|\bREFERENCES\b|^\[\d+\]\s+[A-ZÀ-Ỹ])", re.I | re.M),
    re.compile(r"(\bGIẢNG\s+VIÊN\s+HƯỚNG\s+DẪN\b|\bCÁN\s+BỘ\s+HƯỚNG\s+DẪN\b|\bSINH\s+VIÊN\s+THỰC\s+HIỆN\b|\bNGƯỜI\s+HƯỚNG\s+DẪN\b|\bHỘI\s+ĐỒNG\s+CHẤM\b)", re.I),
]


def is_non_body_passage(text: str) -> bool:
    return any(p.search(text) for p in NON_BODY_PATTERNS)


def merge_sentences_in_passage(sentences: list[dict]) -> list[dict]:
    """Merge broken lines/clauses within a single passage into grammatical sentences,
    stripping out leaked running headers/footers and deduplicating consecutive spans."""
    # 1. Strip leaked running headers
    clean_sents = [s for s in sentences if not is_running_header(s.get("text", ""))]
    if not clean_sents:
        return []

    # 2. De-duplicate consecutive identical lines
    deduped_sents = []
    for s in clean_sents:
        if deduped_sents and deduped_sents[-1]["text"].strip() == s["text"].strip():
            continue
        deduped_sents.append(s)

    if not deduped_sents:
        return []

    sentences = deduped_sents
    merged = []
    current = {
        "sentence_id": sentences[0]["sentence_id"],
        "constituent_ids": [sentences[0]["sentence_id"]],
        "document_id": sentences[0].get("document_id"),
        "section_id": sentences[0].get("section_id"),
        "unit_type": sentences[0].get("unit_type", "paragraph"),
        "text": sentences[0]["text"].strip(),
        "char_start": sentences[0].get("doc_char_start", sentences[0].get("char_start", 0)),
        "char_end": sentences[0].get("doc_char_end", sentences[0].get("char_end", 0)),
    }

    for nxt in sentences[1:]:
        nxt_text = nxt["text"].strip()
        if not nxt_text:
            continue

        if should_merge(current["text"], nxt_text):
            # If next line starts with bullet followed by lowercase, strip the bullet wrap artifact
            if BULLET_LOWER_RE.match(nxt_text):
                nxt_text = BULLET_LOWER_RE.sub(r"\1", nxt_text)

            # Join with space or heal soft-hyphen
            if current["text"].endswith("-") and not current["text"].endswith(" -"):
                current["text"] = f"{current['text'][:-1]}{nxt_text}"
            else:
                current["text"] = f"{current['text']} {nxt_text}"
            current["constituent_ids"].append(nxt["sentence_id"])
            current["char_end"] = nxt.get("doc_char_end", nxt.get("char_end", current["char_end"]))
        else:
            merged.append(current)
            current = {
                "sentence_id": nxt["sentence_id"],
                "constituent_ids": [nxt["sentence_id"]],
                "document_id": nxt.get("document_id"),
                "section_id": nxt.get("section_id"),
                "unit_type": nxt.get("unit_type", "paragraph"),
                "text": nxt_text,
                "char_start": nxt.get("doc_char_start", nxt.get("char_start", 0)),
                "char_end": nxt.get("doc_char_end", nxt.get("char_end", 0)),
            }

    if current["text"]:
        merged.append(current)

    # Post-process: apply text healing and compute approx_tokens
    for sent in merged:
        sent["text"] = heal_text(sent["text"])
        sent["approx_tokens"] = approx_tokens(sent["text"])
        sent["starts_with_lower"] = sent["text"][0].islower() if sent["text"] else False

    return merged


def process_curated_view(
    dataset_dir: Path,
    output_dir: Path,
    min_tokens: int = 64,
    min_sentences: int = 2
) -> dict:
    passages_path = dataset_dir / "passages" / "all.jsonl"
    sentences_path = dataset_dir / "sentences.jsonl"
    topic_path = dataset_dir / "manifest" / "topic_clusters_manifest.jsonl"
    if not topic_path.exists():
        topic_path = dataset_dir / "topic_clusters_manifest.jsonl"

    print(f"Loading topic clusters from {topic_path}...")
    topic_map = {}
    if topic_path.exists():
        with open(topic_path, "r", encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                topic_map[row["document_id"]] = row.get("topic_cluster", "software_engineering")

    print(f"Loading raw sentences index from {sentences_path}...")
    sentence_by_id = {}
    with open(sentences_path, "r", encoding="utf-8") as f:
        for line in f:
            sent = json.loads(line)
            sentence_by_id[sent["sentence_id"]] = sent

    print(f"Processing passages from {passages_path}...")
    output_dir.mkdir(parents=True, exist_ok=True)
    passages_out_file = output_dir / "passages_gips_ready.jsonl"
    sentences_out_file = output_dir / "sentences_gips_ready.jsonl"

    stats = {
        "total_input_passages": 0,
        "eligible_gips_passages": 0,
        "filtered_short_passages": 0,
        "filtered_non_body_passages": 0,
        "raw_sentences_in_eligible": 0,
        "merged_sentences_in_eligible": 0,
        "raw_lowercase_sentences": 0,
        "merged_lowercase_sentences": 0,
        "topic_counts": Counter(),
        "token_distribution": {
            "<64": 0,
            "64-127": 0,
            "128-255": 0,
            "256-512": 0,
            ">512": 0
        }
    }

    with open(passages_path, "r", encoding="utf-8") as p_in, \
         open(passages_out_file, "w", encoding="utf-8") as p_out, \
         open(sentences_out_file, "w", encoding="utf-8") as s_out:

        for line in p_in:
            if not line.strip():
                continue
            passage = json.loads(line)
            stats["total_input_passages"] += 1
            doc_id = passage.get("document_id") or passage.get("source_document_id")
            sec_id = passage.get("section_id") or (passage.get("section_ids", [None])[0] if passage.get("section_ids") else None)

            # Collect raw sentences for this passage
            raw_sents = [sentence_by_id[sid] for sid in passage.get("sentence_ids", []) if sid in sentence_by_id]
            
            # Merge broken sentences
            merged_sents = merge_sentences_in_passage(raw_sents)

            # Healed passage text
            healed_passage_text = " ".join(s["text"] for s in merged_sents)
            p_tokens = approx_tokens(healed_passage_text)

            # Token distribution bucket
            if p_tokens < 64:
                stats["token_distribution"]["<64"] += 1
            elif p_tokens < 128:
                stats["token_distribution"]["64-127"] += 1
            elif p_tokens < 256:
                stats["token_distribution"]["128-255"] += 1
            elif p_tokens <= 512:
                stats["token_distribution"]["256-512"] += 1
            else:
                stats["token_distribution"][">512"] += 1

            # Quality criteria: token bound, min sentences, and pure scientific body (no TOC, ACK, REF, COVER)
            is_non_body = is_non_body_passage(healed_passage_text)
            if is_non_body:
                stats["filtered_non_body_passages"] += 1

            is_eligible = (p_tokens >= min_tokens) and (len(merged_sents) >= min_sentences) and (not is_non_body)

            if is_eligible:
                stats["eligible_gips_passages"] += 1
                stats["raw_sentences_in_eligible"] += len(raw_sents)
                stats["merged_sentences_in_eligible"] += len(merged_sents)
                
                stats["raw_lowercase_sentences"] += sum(
                    1 for s in raw_sents if s.get("text", "").strip() and s["text"].strip()[0].islower()
                )
                stats["merged_lowercase_sentences"] += sum(1 for s in merged_sents if s["starts_with_lower"])

                topic = topic_map.get(doc_id, "unknown")
                stats["topic_counts"][topic] += 1

                # Construct clean passage record
                clean_passage = {
                    "passage_id": passage["passage_id"],
                    "source_document_id": doc_id,
                    "section_id": sec_id,
                    "section_heading": passage.get("section_heading"),
                    "split": passage.get("split"),
                    "topic_cluster": topic,
                    "approx_tokens": p_tokens,
                    "sentence_count": len(merged_sents),
                    "merged_sentence_ids": [s["sentence_id"] for s in merged_sents],
                    "raw_sentence_ids": passage.get("sentence_ids", []),
                    "char_start": passage.get("char_start"),
                    "char_end": passage.get("char_end"),
                    "text": healed_passage_text,
                    "gips_eligible": True
                }
                p_out.write(json.dumps(clean_passage, ensure_ascii=False) + "\n")

                # Write out corresponding merged sentences
                for ord_idx, sent in enumerate(merged_sents):
                    sent_record = {
                        "sentence_id": sent["sentence_id"],
                        "passage_id": passage["passage_id"],
                        "source_document_id": doc_id,
                        "ordinal_in_passage": ord_idx,
                        "section_id": sent.get("section_id") or sec_id,
                        "topic_cluster": topic,
                        "split": passage.get("split"),
                        "approx_tokens": sent["approx_tokens"],
                        "constituent_raw_ids": sent["constituent_ids"],
                        "text": sent["text"]
                    }
                    s_out.write(json.dumps(sent_record, ensure_ascii=False) + "\n")
            else:
                stats["filtered_short_passages"] += 1

    # Materialize splits/ and sentences_by_split/ for downstream loaders
    splits_dir = output_dir / "splits"
    sentences_by_split_dir = output_dir / "sentences_by_split"
    splits_dir.mkdir(parents=True, exist_ok=True)
    sentences_by_split_dir.mkdir(parents=True, exist_ok=True)

    split_handles = {
        split: open(splits_dir / f"{split}.jsonl", "w", encoding="utf-8")
        for split in ("train", "dev", "test")
    }
    sent_split_handles = {
        split: open(sentences_by_split_dir / f"{split}.jsonl", "w", encoding="utf-8")
        for split in ("train", "dev", "test")
    }

    with open(passages_out_file, "r", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            sp = rec.get("split", "train")
            if sp in split_handles:
                split_handles[sp].write(line)

    with open(sentences_out_file, "r", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            sp = rec.get("split", "train")
            if sp in sent_split_handles:
                sent_split_handles[sp].write(line)

    for h in split_handles.values():
        h.close()
    for h in sent_split_handles.values():
        h.close()

    summary_file = output_dir / "curation_summary.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        # Convert Counter to dict for json
        export_stats = dict(stats)
        export_stats["topic_counts"] = dict(stats["topic_counts"])
        json.dump(export_stats, f, ensure_ascii=False, indent=2)

    return stats


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Build curated training view for GIPS-DoRA")
    parser.add_argument("--dataset", type=Path, default=Path("../human_written_dataset_v2_15_paper"))
    parser.add_argument("--output", type=Path, default=Path("../human_written_dataset_v2_15_paper/gips_curated"))
    parser.add_argument("--min_tokens", type=int, default=64)
    parser.add_argument("--min_sentences", type=int, default=2)
    args = parser.parse_args()

    print("=== STARTING GIPS CURATION PIPELINE ===")
    stats = process_curated_view(args.dataset, args.output, args.min_tokens, args.min_sentences)

    print("\n=== CURATION RESULTS ===")
    print(f"Total Input Passages     : {stats['total_input_passages']:,}")
    print(f"Eligible GIPS Passages   : {stats['eligible_gips_passages']:,} ({stats['eligible_gips_passages']/stats['total_input_passages']*100:.2f}%)")
    print(f"Filtered Short Passages  : {stats['filtered_short_passages']:,} ({stats['filtered_short_passages']/stats['total_input_passages']*100:.2f}%)")
    print(f"Raw Sentences in Passages: {stats['raw_sentences_in_eligible']:,}")
    print(f"Merged Sentences         : {stats['merged_sentences_in_eligible']:,} (Consolidation: -{stats['raw_sentences_in_eligible'] - stats['merged_sentences_in_eligible']:,} lines)")
    print(f"Raw Lowercase Sentences  : {stats['raw_lowercase_sentences']:,} ({stats['raw_lowercase_sentences']/max(1, stats['raw_sentences_in_eligible'])*100:.2f}%)")
    print(f"Merged Lowercase Sentences: {stats['merged_lowercase_sentences']:,} ({stats['merged_lowercase_sentences']/max(1, stats['merged_sentences_in_eligible'])*100:.2f}%)")
    print("\n=== TOKEN BUCKET DISTRIBUTION ===")
    for bucket, count in stats['token_distribution'].items():
        pct = (count / stats['total_input_passages']) * 100
        print(f"  Tokens {bucket:<10}: {count:6,d} ({pct:5.2f}%)")
    print("\n=== ELIGIBLE PASSAGES BY TOPIC ===")
    for topic, count in stats['topic_counts'].most_common():
        pct = (count / stats['eligible_gips_passages']) * 100
        print(f"  {topic:<25}: {count:6,d} ({pct:5.2f}%)")
    print(f"\nCurated dataset ready at: {args.output}")


if __name__ == "__main__":
    main()
