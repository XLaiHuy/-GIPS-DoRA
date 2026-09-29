"""Assign each of the 206 accepted documents in human_written_dataset_v2_15_paper
to one of the 5 canonical CS topic clusters specified in overall architect.md Section 6.3:
1. software_engineering
2. cybersecurity
3. artificial_intelligence
4. networking
5. information_systems
"""

import json
import re
from pathlib import Path
from collections import Counter

TOPIC_KEYWORDS = {
    "artificial_intelligence": [
        "trí tuệ nhân tạo", "học máy", "học sâu", "deep learning", "machine learning",
        "học có giám sát", "học không giám sát", "học tăng cường", "reinforcement learning",
        "xử lý ngôn ngữ tự nhiên", "nlp", "ngôn ngữ tự nhiên", "phân tích cảm xúc",
        "thị giác máy tính", "computer vision", "nhận dạng", "phát hiện khuôn mặt",
        "phát hiện đối tượng", "phân loại ảnh", "nhận diện", "yolo", "cnn", "transformer",
        "bert", "mạng nơ-ron", "neural network", "dự đoán", "mô hình ngôn ngữ", "llm",
        "phân cụm", "clustering", "trích xuất đặc trưng", "nhận dạng giọng nói", "speech",
        "ocr", "nhận dạng chữ viết", "khai phá văn bản", "text mining", "segmentation",
        "thực tế ảo", "vr", "tương tác người máy", "hci"
    ],
    "cybersecurity": [
        "an toàn thông tin", "an ninh mạng", "bảo mật", "mật mã", "mã hóa", "tấn công",
        "phát hiện xâm nhập", "ids", "ips", "malware", "mã độc", "phishing", "ransomware",
        "firewall", "tường lửa", "chữ ký số", "lỗ hổng", "vulnerability", "penetration",
        "xâm nhập", "bảo vệ dữ liệu", "mật mã học", "cryptography", "blockchain",
        "hợp đồng thông minh", "smart contract", "antivirus", "ddos", "xác thực", "authen"
    ],
    "networking": [
        "mạng máy tính", "mạng không dây", "truyền thông", "giao thức", "routing",
        "định tuyến", "iot", "internet of things", "mạng cảm biến", "sensor network",
        "wsn", "sdn", "software-defined", "5g", "4g", "lte", "wifi", "rfid", "lora",
        "điện toán đám mây", "cloud computing", "edge computing", "sao chép dữ liệu",
        "phân tán", "distributed system", "truyền dữ liệu", "băng thông", "trễ mạng"
    ],
    "information_systems": [
        "hệ thống thông tin", "cơ sở dữ liệu", "database", "sql", "nosql", "kho dữ liệu",
        "data warehouse", "khai phá dữ liệu", "data mining", "erp", "crm", "thương mại điện tử",
        "e-commerce", "quản lý thông tin", "truy vấn", "etl", "big data", "dữ liệu lớn",
        "quản lý bệnh viện", "quản lý nhân sự", "quản lý bán hàng", "gis", "địa lý",
        "hỗ trợ ra quyết định", "dss", "kinh doanh thông minh", "bi", "business intelligence"
    ],
    "software_engineering": [
        "kỹ thuật phần mềm", "công nghệ phần mềm", "thiết kế hệ thống", "phát triển ứng dụng",
        "microservices", "agile", "scrum", "uml", "design pattern", "kiến trúc phần mềm",
        "web", "website", "mobile", "android", "ios", "react", "flutter", "node", "django",
        "spring", "kiểm thử phần mềm", "software testing", "test automation", "refactoring",
        "game", "unity", "frontend", "backend", "fullstack", "devops", "ci/cd", "api"
    ]
}


def normalize_for_matching(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def score_document(doc: dict) -> tuple[str, float, dict[str, int], list[str]]:
    title = normalize_for_matching(doc.get("title") or "")
    abstract = normalize_for_matching(doc.get("abstract") or "")
    body_snippet = normalize_for_matching((doc.get("text") or "")[:5000])
    
    # Combined weighted text: title gets 4x weight, abstract 2x, snippet 1x
    scores = {topic: 0 for topic in TOPIC_KEYWORDS}
    matched_keywords = {topic: [] for topic in TOPIC_KEYWORDS}

    for topic, keywords in TOPIC_KEYWORDS.items():
        for kw in keywords:
            kw_norm = kw.lower()
            t_count = len(re.findall(r"\b" + re.escape(kw_norm) + r"\b", title))
            a_count = len(re.findall(r"\b" + re.escape(kw_norm) + r"\b", abstract))
            b_count = len(re.findall(r"\b" + re.escape(kw_norm) + r"\b", body_snippet))
            
            weight = t_count * 4 + a_count * 2 + b_count * 1
            if weight > 0:
                scores[topic] += weight
                matched_keywords[topic].append(f"{kw} ({weight})")

    # Pick highest topic
    best_topic = max(scores, key=lambda t: scores[t])
    best_score = scores[best_topic]
    total_score = sum(scores.values())

    confidence = round(best_score / max(1, total_score), 4) if total_score > 0 else 0.0
    if total_score == 0:
        # Default fallback: check faculty/major
        faculty = normalize_for_matching(doc.get("school_or_faculty") or "")
        if "phần mềm" in faculty:
            best_topic = "software_engineering"
        elif "an toàn" in faculty or "mật mã" in faculty:
            best_topic = "cybersecurity"
        elif "mạng" in faculty or "truyền thông" in faculty:
            best_topic = "networking"
        elif "hệ thống" in faculty:
            best_topic = "information_systems"
        else:
            best_topic = "software_engineering"  # CS generic fallback
        confidence = 0.5

    return best_topic, confidence, scores, matched_keywords[best_topic][:5]


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Assign 5 CS topic clusters to documents")
    parser.add_argument("--input", type=Path, default=Path("../human_written_dataset_v2_15_paper/documents.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("../human_written_dataset_v2_15_paper/topic_clusters_manifest.jsonl"))
    args = parser.parse_args()

    if not args.input.exists():
        raise FileNotFoundError(f"Input file not found: {args.input}")

    print(f"Reading documents from {args.input}...")
    topic_counts = Counter()
    assigned = []
    
    with open(args.input, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            doc = json.loads(line)
            doc_id = doc.get("document_id")
            title = doc.get("title", "")
            topic, conf, all_scores, top_kw = score_document(doc)
            topic_counts[topic] += 1
            assigned.append({
                "document_id": doc_id,
                "title": title,
                "topic_cluster": topic,
                "confidence": conf,
                "all_topic_scores": all_scores,
                "evidence_keywords": top_kw,
                "split": doc.get("split", "train"),
                "year": doc.get("year"),
                "institution": doc.get("institution")
            })

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as out:
        for row in assigned:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"\n=== TOPIC CLUSTER DISTRIBUTION (Total: {len(assigned)} documents) ===")
    for topic, count in topic_counts.most_common():
        pct = (count / len(assigned)) * 100
        print(f"  {topic:<25}: {count:3d} docs ({pct:5.2f}%)")
    print(f"\nManifest saved to: {args.output}")


if __name__ == "__main__":
    main()
