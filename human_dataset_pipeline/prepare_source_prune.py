#!/usr/bin/env python3
"""Create a verified manifest for source PDFs absent from an active release."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def jsonl(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare a source-PDF removal manifest.")
    parser.add_argument("--pdf-root", required=True, type=Path)
    parser.add_argument("--active-documents", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    root = args.pdf_root.resolve(strict=True)
    active = {row["source_path"] for row in jsonl(args.active_documents.resolve(strict=True))}
    inventory = {row["source_path"]: row for row in jsonl(args.inventory.resolve(strict=True))}
    disk = {path.name: path.resolve(strict=True) for path in root.glob("*.pdf")}

    missing = sorted(active - disk.keys())
    if missing:
        raise SystemExit(f"Refusing prune: {len(missing)} active PDFs are missing")

    targets = sorted(set(disk) - active)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        for name in targets:
            path = disk[name]
            if path.parent != root or path.suffix.lower() != ".pdf":
                raise SystemExit(f"Unsafe target: {path}")
            source = inventory.get(name, {})
            digest = sha256(path)
            if source.get("sha256") and source["sha256"] != digest:
                raise SystemExit(f"Checksum changed since inventory: {name}")
            record = {
                "source_path": name,
                "absolute_path": str(path),
                "sha256": digest,
                "bytes": path.stat().st_size,
                "inventory_outcome": source.get("outcome"),
                "inventory_document_id": source.get("document_id"),
                "removal_mode": "windows_recycle_bin",
            }
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    print(json.dumps({
        "pdf_root": str(root),
        "active_pdfs": len(active),
        "removal_targets": len(targets),
        "removal_bytes": sum(disk[name].stat().st_size for name in targets),
        "manifest": str(output),
    }, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
