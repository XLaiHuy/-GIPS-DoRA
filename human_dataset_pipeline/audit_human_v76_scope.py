"""High-precision computing-scope screen; ambiguous cases remain unresolved."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from human_v3.core import JsonlWriter, fold, rows
from human_v3.triage import computing_hits

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "human_v3_2_rebuild_v76_20261007"

TECHNICAL_PHRASES = (
    "phan mem", "website", "ung dung", "giao dien", "lap trinh", "ma nguon",
    "thuat toan", "co so du lieu", "machine learning", "hoc may", "deep learning",
    "mang neural", "mang no ron", "artificial neural network", "api", "server",
    "framework", "mo hinh hoc", "xu ly anh", "nhan dang", "phan doan anh",
    "he thong thong tin", "he dieu hanh", "du lieu dau vao", "huan luyen mo hinh",
    "chuc nang", "form", "modal", "delete", "mailing list", "hop thu",
    "nguoi dung", "tham so", "tac vu", "dang nhap", "dang ky tai khoan",
)
DOMAIN_TERMS = {
    "finance": ("ty suat sinh loi", "kiem dinh hausman", "kiem dinh wald", "roa",
                "roe", "co phieu", "loi nhuan", "lai suat", "gia tri so sach",
                "dau tu tren tai san", "tai san co dinh", "thi truong chung khoan"),
    "lifestyle": ("bullet journal", "du lich", "bua tiec", "phu kien",
                  "nhat ky", "ghi chep", "vat dung du lich"),
    "business_process": ("kiem tra kho", "phieu xuat kho", "bao gia", "thu kho",
                         "don hang", "khach hang", "giao hang", "nhan vien kinh doanh"),
    "hr_insurance": ("bao hiem", "boi thuong", "nhan su", "tuyen dung",
                     "nhan vien", "hop dong lao dong", "chien luoc kinh doanh"),
    "cashbook_workflow": ("phieu thu", "phieu chi", "so quy", "so nhat ky",
                          "chung tu goc", "bao cao tai chinh", "ty gia ngoai te"),
}


def classify(text: str) -> dict:
    value = fold(text)
    technical = sorted({phrase for phrase in TECHNICAL_PHRASES if phrase in value})
    domain = {name: sorted({term for term in terms if term in value})
              for name, terms in DOMAIN_TERMS.items()}
    strong = [name for name, terms in domain.items()
              if len(terms) >= (3 if name == "business_process" else 2)]
    reason = None
    # Requiring two concrete non-computing signals and no direct technical
    # explanation prioritizes precision over recall. A thesis title cannot
    # turn a finance/business paragraph into computing prose.
    if strong and not technical and not computing_hits(text):
        reason = "noncomputing_" + strong[0]
    return {"decision": "reject_high_precision" if reason else
            "technical_evidence" if technical else "unresolved",
            "reason": reason, "technical_phrases": technical,
            "domain_hits": {name: terms for name, terms in domain.items() if terms}}


def main():
    path = SOURCE / "audit/scope_screen_v3.jsonl"
    if path.exists():
        raise SystemExit(f"Output exists: {path}")
    counts = Counter()
    by_flag = Counter()
    with JsonlWriter(path) as writer:
        for split in ("train", "dev", "test"):
            for chunk in rows(SOURCE / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl"):
                result = classify(chunk["text"])
                flagged = "computing_scope_requires_review" in chunk.get("review_reasons", [])
                counts[result["decision"]] += 1
                by_flag[(str(flagged), result["decision"])] += 1
                if result["decision"] == "reject_high_precision" or flagged:
                    writer.write({"chunk_id": chunk["chunk_id"],
                                  "document_id": chunk["document_id"], "split": split,
                                  "prior_scope_flag": flagged, **result})
    summary = {"candidate": SOURCE.name, "decision_counts": dict(counts),
               "prior_flag_by_decision": {str(key): value for key, value in by_flag.items()},
               "note": "High-precision rejects are candidates for quarantine; unresolved is not accepted as verified scope."}
    (SOURCE / "audit/scope_summary_v3.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
