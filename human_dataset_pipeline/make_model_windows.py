#!/usr/bin/env python3
"""Create tokenizer-specific, sentence-preserving context windows."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from transformers import AutoTokenizer


def stable_id(*parts: object) -> str:
    raw = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return "win_" + hashlib.sha256(raw).hexdigest()[:20]


def rows(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def encoded(tokenizer, text: str, offsets: bool = False) -> dict:
    return tokenizer(text, add_special_tokens=False, return_offsets_mapping=offsets, truncation=False)


def tokenizer_units(sentences: list[dict], tokenizer, usable_tokens: int) -> list[dict]:
    """Keep sentences whole; token-fragment only a pathological overlong unit."""
    units: list[dict] = []
    for sentence in sentences:
        result = encoded(tokenizer, sentence["text"], offsets=True)
        ids, offsets = result["input_ids"], result["offset_mapping"]
        if len(ids) <= usable_tokens:
            units.append({**sentence, "unit_id": sentence["sentence_id"], "model_fragment": False, "model_token_count": len(ids)})
            continue
        fragments = []
        for start in range(0, len(ids), usable_tokens):
            end = min(len(ids), start + usable_tokens)
            char_start, char_end = offsets[start][0], offsets[end - 1][1]
            fragments.append((char_start, char_end, sentence["text"][char_start:char_end], end - start))
        for index, (local_start, local_end, text, count) in enumerate(fragments):
            units.append({
                **sentence,
                "unit_id": f"{sentence['sentence_id']}#model-fragment-{index}",
                "text": text,
                "char_start": sentence["char_start"] + local_start,
                "char_end": sentence["char_start"] + local_end,
                "model_fragment": True,
                "model_token_count": count,
            })
    return units


def central_ranges(units: list[dict], central_budget: int) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < len(units):
        end, total = start, 0
        while end < len(units):
            cost = units[end]["model_token_count"] + (1 if end > start else 0)
            if end > start and total + cost > central_budget:
                break
            total += cost
            end += 1
            if total >= central_budget:
                break
        ranges.append((start, max(start + 1, end)))
        start = max(start + 1, end)
    return ranges


def pack_sentence_windows(sentences: list[dict], tokenizer, max_tokens: int, central_ratio: float = 0.75) -> list[dict]:
    special_tokens = len(tokenizer.build_inputs_with_special_tokens([]))
    usable = max_tokens - special_tokens
    if usable < 8:
        raise ValueError("max_tokens leaves no usable text budget")
    central_budget = max(1, int(usable * central_ratio))
    side_budget = max(0, (usable - central_budget) // 2)
    windows: list[dict] = []
    grouped: defaultdict[tuple[str, str], list[dict]] = defaultdict(list)
    for sentence in sentences:
        if sentence.get("split") in {"train", "dev", "test"}:
            grouped[(sentence["document_id"], sentence["section_id"])].append(sentence)

    for (document_id, section_id), group in grouped.items():
        group.sort(key=lambda row: row["ordinal"])
        units = tokenizer_units(group, tokenizer, usable)
        position = {unit["unit_id"]: index for index, unit in enumerate(units)}
        for central_start, central_end in central_ranges(units, central_budget):
            left, used = central_start, 0
            while left > 0:
                cost = units[left - 1]["model_token_count"] + 1
                if used + cost > side_budget:
                    break
                left -= 1
                used += cost
            right, used = central_end, 0
            while right < len(units):
                cost = units[right]["model_token_count"] + 1
                if used + cost > side_budget:
                    break
                right += 1
                used += cost

            chosen = units[left:right]
            text = " ".join(unit["text"] for unit in chosen)
            tokenized = encoded(tokenizer, text, offsets=True)
            while len(tokenized["input_ids"]) > usable and (left < central_start or right > central_end):
                if right > central_end:
                    right -= 1
                else:
                    left += 1
                chosen = units[left:right]
                text = " ".join(unit["text"] for unit in chosen)
                tokenized = encoded(tokenizer, text, offsets=True)
            if len(tokenized["input_ids"]) > usable:
                raise AssertionError(f"central units exceed tokenizer budget: {document_id}:{section_id}")

            char_spans, cursor = [], 0
            for index, unit in enumerate(chosen):
                if index:
                    cursor += 1
                start = cursor
                cursor += len(unit["text"])
                char_spans.append([start, cursor])
            offsets = tokenized["offset_mapping"]
            token_spans = []
            for char_start, char_end in char_spans:
                indices = [i for i, (start, end) in enumerate(offsets) if end > char_start and start < char_end]
                token_spans.append([min(indices), max(indices) + 1] if indices else [0, 0])
            loss_mask = [central_start <= position[unit["unit_id"]] < central_end for unit in chosen]
            first = chosen[0]
            windows.append({
                "window_id": stable_id(document_id, section_id, chosen[0]["unit_id"], chosen[-1]["unit_id"], max_tokens),
                "document_id": document_id,
                "group_id": first["group_id"],
                "split": first["split"],
                "provenance_status": first["provenance_status"],
                "section_id": section_id,
                "token_count": len(tokenized["input_ids"]) + special_tokens,
                "max_tokens": max_tokens,
                "sentence_ids": [unit["sentence_id"] for unit in chosen],
                "unit_ids": [unit["unit_id"] for unit in chosen],
                "sentence_char_spans": char_spans,
                "sentence_token_spans": token_spans,
                "document_char_spans": [[unit["char_start"], unit["char_end"]] for unit in chosen],
                "context_mask": [not value for value in loss_mask],
                "loss_mask": loss_mask,
                "labels": [unit.get("label", "H") for unit in chosen],
                "model_fragment_mask": [unit["model_fragment"] for unit in chosen],
                "text": text,
            })
    return windows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--tokenizer", required=True, help="Local tokenizer path or cached HF model id")
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--central-ratio", type=float, default=0.75)
    args = parser.parse_args()
    if not 0.50 <= args.central_ratio <= 1.0:
        parser.error("central-ratio must be in [0.50, 1.0]")

    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, local_files_only=True, use_fast=True)
    windows = pack_sentence_windows(list(rows(args.dataset / "sentences.jsonl")), tokenizer, args.max_tokens, args.central_ratio)
    out_dir = args.dataset / "model_views" / Path(str(args.tokenizer)).name / f"context_{args.max_tokens}"
    out_dir.mkdir(parents=True, exist_ok=True)
    handles = {name: (out_dir / f"{name}_windows.jsonl").open("w", encoding="utf-8", newline="\n") for name in ("train", "dev", "test", "recent_or_uncertain")}
    counts, central_seen = Counter(), Counter()
    try:
        for window in windows:
            target = window["split"] if window["provenance_status"] == "high_confidence_human" else "recent_or_uncertain"
            handles[target].write(json.dumps(window, ensure_ascii=False, separators=(",", ":")) + "\n")
            counts[target] += 1
            for unit_id, use_loss in zip(window["unit_ids"], window["loss_mask"]):
                if use_loss:
                    central_seen[unit_id] += 1
    finally:
        for handle in handles.values():
            handle.close()
    violations = [unit_id for unit_id, count in central_seen.items() if count != 1]
    if violations:
        raise AssertionError(f"central loss coverage violation for {len(violations)} units")
    with (out_dir / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump({"tokenizer": str(args.tokenizer), "max_tokens": args.max_tokens, "central_ratio": args.central_ratio, "counts": counts}, handle, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
