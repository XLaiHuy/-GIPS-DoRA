"""Attach verified OU catalog provenance without changing candidate prose."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from human_v3.core import JsonlWriter, rows

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "human_v3_2_rebuilt_candidate_v79_20261007"
OUT = ROOT / "human_v3_2_provenance_candidate_v80_20261007"
EVIDENCE = SOURCE / "audit/ou_source_provenance_v80b.jsonl"
SPLITS = ("train", "dev", "test")


def main() -> None:
    if OUT.exists():
        raise SystemExit(f"Output exists: {OUT}")
    evidence = {row["document_id"]: row for row in rows(EVIDENCE)
                if row["decision"] in {"official_record_filename_confirmed",
                                        "official_record_prefixed_filename_confirmed"}}
    changes = Counter()
    with JsonlWriter(OUT / "documents.jsonl") as writer:
        for doc in rows(SOURCE / "documents.jsonl"):
            row = evidence.get(doc["document_id"])
            if row:
                doc = dict(doc)
                doc["source_url"] = row["official_item_url"]
                doc["catalog_pdf_filename"] = row["catalog_pdf_filenames"]
                doc["source_catalog_evidence"] = row["decision"]
                doc["source_catalog_record_id"] = row["record_id"]
                doc["source_catalog_title"] = row["catalog_title"]
                changes["documents"] += 1
            writer.write(doc)
    for split in SPLITS:
        with JsonlWriter(OUT / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl") as writer:
            for chunk in rows(SOURCE / "chunk_streams/pass_candidate/t192" / f"{split}.jsonl"):
                if chunk["document_id"] in evidence:
                    chunk = dict(chunk)
                    old = chunk.get("review_reasons", [])
                    if "source_evidence_requires_review" in old:
                        chunk["review_reasons"] = [reason for reason in old
                                                   if reason != "source_evidence_requires_review"]
                        changes["resolved_chunk_source_flags"] += 1
                    chunk["source_catalog_evidence"] = evidence[chunk["document_id"]]["decision"]
                writer.write(chunk)
        with JsonlWriter(OUT / "chunk_streams/quarantine/t192" / f"{split}.jsonl") as writer:
            for chunk in rows(SOURCE / "chunk_streams/quarantine/t192" / f"{split}.jsonl"):
                writer.write(chunk)
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    manifest.update(source=SOURCE.name, status="candidate_not_ready",
                    ou_catalog_documents_linked=changes["documents"],
                    source_flag_chunks_resolved=changes["resolved_chunk_source_flags"],
                    rights_scope="internal_research_only_owner_asserted",
                    formal_pdf_visual_attestations=0)
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)
                                       + "\n", encoding="utf-8")
    print(json.dumps({"candidate_chunks": manifest["pass_candidate_chunk_count"],
                      "documents_linked": changes["documents"],
                      "source_flags_resolved": changes["resolved_chunk_source_flags"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
