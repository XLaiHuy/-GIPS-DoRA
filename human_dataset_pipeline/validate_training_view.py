#!/usr/bin/env python3
"""Validate metadata-rich documents and fine-tuning chunk invariants."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def rows(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise AssertionError(f"Invalid JSON at {path}:{line_number}: {exc}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()
    root = args.dataset
    docs = {row["document_id"]: row for row in rows(root / "documents.jsonl")}
    errors: list[str] = []
    ids: set[str] = set()
    fingerprints: dict[str, str] = {}
    group_splits: dict[str, str] = {}
    counts = Counter()
    for document_id, doc in docs.items():
        if not doc.get("institution_id") or not doc.get("institution_name"):
            errors.append(f"missing canonical institution: {document_id}")
        required_missing = [field for field in ("title", "authors", "year", "institution_id", "institution_name") if not doc.get(field)]
        if doc.get("training_eligible") and required_missing:
            errors.append(f"eligible document missing required metadata: {document_id} -> {required_missing}")
        if doc.get("training_eligible"):
            if doc.get("split") not in {"train", "dev", "test"}:
                errors.append(f"eligible document in invalid split: {document_id}")
            if doc.get("quality_tier") not in {"gold", "silver"}:
                errors.append(f"eligible document has invalid quality: {document_id}")
            if doc.get("clean_character_count", 0) < 5000 or len(doc.get("text", "")) < 5000:
                errors.append(f"eligible document is too short: {document_id}")
            if doc.get("language_detected") not in {"vi", "en", "mixed"}:
                errors.append(f"invalid detected language: {document_id}")
        if doc.get("document_type_id") not in {"bachelor_thesis", "capstone_project", "master_thesis", "doctoral_dissertation", "research_report", "internship_report", "other"}:
            errors.append(f"invalid document type: {document_id}")
        if not doc.get("domain_id"):
            errors.append(f"missing domain id: {document_id}")
        if doc.get("core_human_eligible") and (
            not doc.get("training_eligible") or doc.get("provenance_status") != "high_confidence_human"
        ):
            errors.append(f"invalid core-human eligibility: {document_id}")
        group_id, split = doc.get("group_id"), doc.get("split")
        # Duplicate/non-representative documents are deliberately assigned to
        # `excluded`; only active train/dev/test membership can leak.
        if split != "excluded":
            if group_id in group_splits and group_splits[group_id] != split:
                errors.append(f"group leakage across active splits: {group_id}")
            group_splits[group_id] = split
    for chunk in rows(root / "chunks" / "all.jsonl"):
        chunk_id = chunk["chunk_id"]
        counts[chunk["subset"]] += 1
        if chunk_id in ids:
            errors.append(f"duplicate chunk_id: {chunk_id}")
        ids.add(chunk_id)
        doc = docs.get(chunk["document_id"])
        if not doc:
            errors.append(f"missing document: {chunk_id}")
            continue
        if chunk["split"] != doc["split"]:
            errors.append(f"split mismatch: {chunk_id}")
        if chunk.get("group_id") != doc.get("group_id"):
            errors.append(f"group mismatch: {chunk_id}")
        if chunk["subset"] == "core_human":
            if chunk["provenance_status"] != "high_confidence_human":
                errors.append(f"non-core provenance in core set: {chunk_id}")
            valid_length = 128 <= chunk["approx_tokens"] <= 512
            valid_short_tail = chunk.get("short_tail") and chunk["approx_tokens"] < 128
            if not (valid_length or valid_short_tail):
                errors.append(f"token bound: {chunk_id}")
            fingerprint = hashlib.sha256(" ".join(chunk["text"].casefold().split()).encode()).hexdigest()
            if fingerprint in fingerprints:
                errors.append(f"exact duplicate core chunks: {chunk_id}, {fingerprints[fingerprint]}")
            fingerprints[fingerprint] = chunk_id
            if not doc.get("core_human_eligible"):
                errors.append(f"core chunk from ineligible document: {chunk_id}")
        elif chunk["subset"] == "recent_or_uncertain":
            if not doc.get("training_eligible") or chunk.get("provenance_status") == "high_confidence_human":
                errors.append(f"invalid recent/uncertain chunk: {chunk_id}")
        elif chunk["subset"] == "excluded" and doc.get("training_eligible") and not chunk.get("exclusion_reason"):
            errors.append(f"excluded chunk without chunk-level reason: {chunk_id}")
        start_char = chunk.get("start_char", chunk.get("document_char_start"))
        end_char = chunk.get("end_char", chunk.get("document_char_end"))
        body = doc.get("text", "")
        if (type(start_char) is not int or type(end_char) is not int or
                not (0 <= start_char < end_char <= len(body)) or
                body[start_char:end_char] != chunk["text"]):
            pieces = [body[start:end] for start, end in chunk["document_char_spans"]]
            if " ".join(pieces) != chunk["text"]:
                errors.append(f"source span mismatch: {chunk_id}")
        if len(chunk.get("section_ids", [])) > 1:
            errors.append(f"passage crosses sections: {chunk_id}")

    sentence_count = 0
    central_sentence_ids: set[str] = set()
    for sentence in rows(root / "sentences.jsonl"):
        sentence_count += 1
        doc = docs.get(sentence["document_id"])
        if not doc:
            errors.append(f"sentence missing document: {sentence['sentence_id']}")
            continue
        if doc["text"][sentence["char_start"]:sentence["char_end"]] != sentence["text"]:
            errors.append(f"sentence source span mismatch: {sentence['sentence_id']}")
        if sentence.get("is_fragment") and not sentence.get("parent_sentence_id"):
            errors.append(f"fragment without parent sentence: {sentence['sentence_id']}")
        if sentence["split"] in {"train", "dev", "test"}:
            central_sentence_ids.add(sentence["sentence_id"])

    passage_mirror = sum(1 for _ in rows(root / "passages" / "all.jsonl"))
    if passage_mirror != len(ids):
        errors.append(f"passage/chunk mirror count mismatch: {passage_mirror} != {len(ids)}")
    report = {
        "valid": not errors,
        "error_count": len(errors),
        "errors": errors[:100],
        "documents": len(docs),
        "chunks": len(ids),
        "sentences": sentence_count,
        "subsets": dict(counts),
        "institutions": dict(Counter(doc["institution_id"] for doc in docs.values())),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
