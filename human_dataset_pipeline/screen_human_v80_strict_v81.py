"""Quarantine every unresolved release flag in v80 without claiming readiness."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from human_v3.core import JsonlWriter, rows

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "human_v3_2_provenance_candidate_v80_20261007"
OUT = ROOT / "human_v3_2_strict_candidate_v81_20261007"
SPLITS = ("train", "dev", "test")
BLOCKING_REASONS = {
    "computing_scope_requires_review",
    "body_boundaries_require_review",
    "language_requires_review",
    "source_evidence_requires_review",
    "year_conflict",
    "institution_is_repository_placeholder",
    "legacy_exclusion:incomplete_body_extraction",
}


def main() -> None:
    if (OUT / "manifest.json").exists():
        raise SystemExit(f"Output already exists: {OUT}")
    counts = Counter()
    reasons = Counter()
    document_ids = set()
    for split in SPLITS:
        pass_path = OUT / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl"
        quarantine_path = OUT / "chunk_streams/quarantine/t192" / f"{split}.jsonl"
        with JsonlWriter(pass_path) as pass_writer, JsonlWriter(quarantine_path) as quarantine_writer:
            for chunk in rows(SOURCE / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl"):
                blockers = sorted(BLOCKING_REASONS.intersection(chunk.get("review_reasons", [])))
                if blockers:
                    quarantined = dict(chunk)
                    quarantined["release_status"] = "quarantine"
                    quarantined["candidate_review_status"] = "blocked_by_unresolved_release_flags"
                    quarantined["generation_ready"] = False
                    quarantined["model_input_ready"] = False
                    quarantined["strict_screening_reasons"] = blockers
                    quarantine_writer.write(quarantined)
                    reasons.update(blockers)
                    counts["newly_quarantined"] += 1
                else:
                    pass_writer.write(chunk)
                    document_ids.add(chunk["document_id"])
                    counts[f"pass_{split}"] += 1
            for chunk in rows(SOURCE / "chunk_streams/quarantine/t192" / f"{split}.jsonl"):
                quarantine_writer.write(chunk)
                counts["preexisting_quarantine"] += 1
    with JsonlWriter(OUT / "documents.jsonl") as writer:
        for doc in rows(SOURCE / "documents.jsonl"):
            writer.write(doc)
    source_manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    manifest = {
        **source_manifest,
        "source": SOURCE.name,
        "status": "candidate_not_ready",
        "pass_candidate_chunk_count": sum(counts[f"pass_{split}"] for split in SPLITS),
        "quarantine_chunk_count": counts["newly_quarantined"] + counts["preexisting_quarantine"],
        "split_counts": {split: counts[f"pass_{split}"] for split in SPLITS},
        "document_count": len(document_ids),
        "scope_review_flag_chunks": 0,
        "body_boundary_review_flag_chunks": 0,
        "formal_pdf_visual_attestations": 0,
        "strict_screening_reasons": dict(reasons),
        "strict_screening_newly_quarantined": counts["newly_quarantined"],
        "known_limits": [
            "Below 20000 unflagged chunks; not ready for bulk AI generation.",
            "No final 400-card PDF visual review or 40 independent crosschecks.",
            "Absence of known release flags is not a PDF visual attestation.",
        ],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                        encoding="utf-8")
    print(json.dumps({"candidate": OUT.name, "pass_chunks": manifest["pass_candidate_chunk_count"],
                      "documents": len(document_ids), "quarantine_chunks": manifest["quarantine_chunk_count"],
                      "newly_quarantined": counts["newly_quarantined"], "reasons": dict(reasons)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
