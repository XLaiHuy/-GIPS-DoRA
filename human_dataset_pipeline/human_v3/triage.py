"""Deterministic document-level triage for Human v3.2.

The rules can resolve conservative metadata warnings, but never manufacture
missing provenance or visual evidence.
"""
from __future__ import annotations

from collections import Counter

from .core import fold

COMPUTING_TERMS = {
    "software": ("phan mem", "lap trinh", "ma nguon", "kiem thu phan mem"),
    "web": ("website", "trang web", "web app", "reactjs", "nodejs", "expressjs", "laravel", "asp.net", ".net core", "php", "html", "xml", "angular", "vuejs"),
    "mobile": ("ung dung di dong", "android", "windows phone", "ios", "react native", "flutter"),
    "database": ("co so du lieu", "database", "sql server", "mysql", "postgresql", "nosql", "mongodb", "oracle database", "firebase"),
    "algorithm": ("thuat toan", "cau truc du lieu", "do phuc tap tinh toan", "k-nearest", "apriori", "cay quyet dinh"),
    "ai": ("hoc may", "machine learning", "tri tue nhan tao", "deep learning", "mang no ron", "mang neural", "convolutional", "yolov", "random forest", "support vector machine"),
    "nlp_cv": ("xu ly ngon ngu", "thi giac may tinh", "nhan dang khuon mat", "nhan dang giong noi", "phan doan anh", "xu ly anh", "opencv"),
    "data": ("khai pha du lieu", "data mining", "kho du lieu", "big data"),
    "network": ("mang may tinh", "giao thuc mang", "quan tri mang", "may chu", "client server"),
    "security": ("an toan thong tin", "bao mat thong tin", "tan cong tu choi dich vu", "firewall", "xam nhap mang", "ma hoa", "xac thuc", "blockchain"),
    "cloud_iot": ("dien toan dam may", "cloud computing", "internet of things", "iot", "oneM2M", "ao hoa", "docker", "kubernetes"),
    "information_system": ("he thong thong tin", "phan tich thiet ke he thong", "uml", "erp", "crm"),
    "operating_system": ("he dieu hanh", "linux", "android os"),
    "programming_language": ("python", "java", "javascript", "typescript", "c#", "c++"),
    "software_architecture": ("rest api", "web service", "microservice", "mvc", "dependency injection", "entity framework"),
    "geospatial": ("he thong thong tin dia ly", "gis", "vien tham"),
}

IMPLEMENTATION_MARKERS = (
    "xay dung", "phat trien", "thiet ke", "trien khai", "cai dat", "lap trinh",
    "danh gia hieu nang", "de xuat thuat toan", "nghien cuu thuat toan",
)

BEHAVIOURAL_MARKERS = (
    "y dinh mua", "y dinh su dung", "nguoi tieu dung", "long tin", "thai do",
    "hanh vi mua", "yeu to anh huong", "marketing", "tang truong kinh te",
    "phap luat", "su thoa man cong viec",
)

ESSENTIAL_RISKS = {
    "year_conflict", "source_evidence_requires_review", "body_boundaries_require_review",
    "missing_authors", "missing_title", "missing_year", "no_complete_prose_sentences",
    "legacy_exclusion:incomplete_body_extraction", "rights_review_pending",
}


def computing_hits(text: str) -> dict[str, list[str]]:
    value = fold(text)
    return {category: [term for term in terms if fold(term) in value]
            for category, terms in COMPUTING_TERMS.items()
            if any(fold(term) in value for term in terms)}


def chunk_scope_profile(chunk: dict) -> dict:
    """Require multiple technical signals before rescuing an interdisciplinary chunk."""
    context = " ".join([*(chunk.get("heading_path_text") or []), chunk.get("text", "")])
    context_folded = fold(context)
    hits = computing_hits(context)
    terms = sorted({term for values in hits.values() for term in values})
    behavioural = any(marker in context_folded for marker in BEHAVIOURAL_MARKERS)
    high_confidence = ((len(hits) >= 2 or len(terms) >= 2)
                       and (not behavioural or len(hits) >= 3))
    return {
        "status": "high_confidence_computing" if high_confidence else "computing_scope_unresolved",
        "categories": sorted(hits),
        "terms": terms,
        "behavioural_language": behavioural,
    }


def scope_profile(document: dict, chunks: list[dict]) -> dict:
    """Estimate computing scope from independent title and body evidence."""
    title = document.get("metadata", {}).get("title") or ""
    title_folded = fold(title)
    title_hits = computing_hits(title)
    hit_chunks = 0
    body_categories: Counter[str] = Counter()
    body_terms: set[str] = set()
    for chunk in chunks:
        hits = computing_hits(chunk.get("text", ""))
        if hits:
            hit_chunks += 1
        body_categories.update(hits.keys())
        body_terms.update(term for values in hits.values() for term in values)
    total = len(chunks)
    ratio = hit_chunks / max(1, total)
    implementation = any(marker in title_folded for marker in IMPLEMENTATION_MARKERS)
    behavioural = any(marker in title_folded for marker in BEHAVIOURAL_MARKERS)
    metadata_cs = document.get("metadata", {}).get("domain_id") == "computer_science"
    body_support = total >= 3 and hit_chunks >= 3 and ratio >= 0.15 and len(body_terms) >= 4
    technical_title = bool(title_hits)
    # Non-CS metadata requires two independent signals. Behavioural studies
    # about technology need stronger evidence than merely mentioning an app.
    inferred = (technical_title and body_support and implementation
                and (not behavioural or len(title_hits) >= 2))
    metadata_supported = metadata_cs and not behavioural and (technical_title or body_support)
    high_confidence = metadata_supported or inferred
    return {
        "status": "high_confidence_computing" if high_confidence else "computing_scope_unresolved",
        "metadata_computer_science": metadata_cs,
        "metadata_scope_supported_by_text": metadata_supported,
        "title_categories": sorted(title_hits),
        "title_terms": sorted({term for values in title_hits.values() for term in values}),
        "implementation_title": implementation,
        "behavioural_title": behavioural,
        "candidate_chunks": total,
        "technical_candidate_chunks": hit_chunks,
        "technical_chunk_ratio": round(ratio, 5),
        "body_categories": sorted(body_categories),
        "distinct_body_terms": len(body_terms),
    }


def triage_document(document: dict, chunks: list[dict]) -> dict:
    scope = scope_profile(document, chunks)
    hard = list(document.get("hard_exclusion_reasons", []))
    risks = set(document.get("risk_reasons", []))
    resolved, warnings = [], []

    if "institution_is_repository_placeholder" in risks:
        # This is a metadata quality issue, not evidence that the PDF text is
        # non-human. Keep it visible without blocking otherwise valid prose.
        risks.remove("institution_is_repository_placeholder")
        resolved.append("institution_placeholder_does_not_block_text")
        warnings.append("institution_metadata_unresolved")
    if "computing_scope_requires_review" in risks and scope["status"] == "high_confidence_computing":
        risks.remove("computing_scope_requires_review")
        resolved.append("computing_scope_supported_by_title_and_body")
    if scope["status"] != "high_confidence_computing" and "computing_scope_requires_review" not in risks:
        risks.add("automatic_scope_evidence_insufficient")
    if "body_boundaries_require_review" in risks:
        bounds = document.get("extraction", {}).get("bounds", {})
        # A legacy fallback warning is obsolete when the new extractor found
        # both boundaries independently. Missing either side remains blocked.
        if (bounds.get("start_detected") and bounds.get("end_detected")
                and not bounds.get("requires_review")):
            risks.remove("body_boundaries_require_review")
            resolved.append("new_extractor_detected_both_body_boundaries")
    if "language_requires_review" in risks:
        language = document.get("metadata", {}).get("language_detected")
        # Every exported candidate sentence already passed the Vietnamese
        # sentence gate. Mixed documents remain usable only when enough such
        # chunks exist; unknown/English documents stay unresolved.
        if language == "mixed" and len(chunks) >= 5:
            risks.remove("language_requires_review")
            resolved.append("vietnamese_candidate_regions_in_mixed_document")
            warnings.append("mixed_language_document")

    unresolved = sorted(risks)
    if hard:
        status = "hard_exclude"
    elif any(reason in ESSENTIAL_RISKS for reason in unresolved):
        status = "quarantine"
    elif "computing_scope_requires_review" in unresolved or "language_requires_review" in unresolved:
        status = "quarantine"
    elif unresolved:
        status = "quarantine"
    else:
        status = "auto_accept"
    return {
        "document_id": document["document_id"],
        "source_pdf_sha256": document["source_pdf_sha256"],
        "split": document["split"],
        "triage_status": status,
        "hard_exclusion_reasons": sorted(hard),
        "unresolved_reasons": unresolved,
        "resolved_reasons": sorted(resolved),
        "warnings": sorted(warnings),
        "scope": scope,
    }
