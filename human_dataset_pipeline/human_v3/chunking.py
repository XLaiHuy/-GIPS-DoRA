"""Sentence-preserving, hierarchy-aware chunk streams for Human v3.1+."""
from __future__ import annotations

from .core import approx_tokens, fingerprint, ident, record_schema_version, sha


def _source_spans(sentences: list[dict]) -> list[dict]:
    result, seen = [], set()
    for sentence in sentences:
        for span in sentence.get("source_spans", []):
            key = (span["page_index"], span["source_start"], span["source_end"])
            if key not in seen:
                seen.add(key)
                result.append(span)
    return result


def _record(document_id: str, selected: list[dict], stream_id: str, target: int,
            index: int, overlap_ids: list[str], config: dict) -> dict:
    sentence_ids = [s["sentence_id"] for s in selected]
    overlap = set(overlap_ids)
    central_ids = [sid for sid in sentence_ids if sid not in overlap]
    text = " ".join(s["text"] for s in selected)
    first = selected[0]
    heading_path = first.get("heading_path", [])
    schema = record_schema_version(config)
    prefix = "chunk32" if schema == "human-v3.2" else "chunk31"
    return {
        "schema_version": schema,
        "chunk_id": ident(prefix, document_id, stream_id, index, sentence_ids),
        "document_id": document_id,
        "group_id": first["group_id"],
        "split": first["split"],
        "paper_title": first.get("paper_title", ""),
        "section_id": first["section_id"],
        "run_id": first["run_id"],
        "heading_path": heading_path,
        "heading_path_text": [h["title"] for h in heading_path],
        "sentence_ids": sentence_ids,
        "central_sentence_ids": central_ids,
        "overlap_sentence_ids": overlap_ids,
        "sentence_count": len(selected),
        "text": text,
        "text_sha256": sha(text),
        "fingerprint_sha256": fingerprint(text),
        "approx_tokens": approx_tokens(text),
        "target_tokens": target,
        "stream_id": stream_id,
        "window_index": index,
        "source_spans": _source_spans(selected),
        "source_pdf_sha256": first["source_pdf_sha256"],
        "document_char_spans": [[s["document_char_start"], s["document_char_end"]] for s in selected],
        "transformation_ids": list(dict.fromkeys(t for s in selected for t in s.get("transformation_ids", []))),
        "offset_unit": "unicode_code_points",
        "label": "H",
        "model_input_ready": False,
        "automatic_candidate": True,
        "duplicate_of": None,
        "human_core_ready": False,
        "generation_ready": False,
        "review_reasons": [],
    }


def _windows_for_run(document_id: str, sentences: list[dict], stream_id: str,
                     target: int, overlap_ratio: float, max_overlap_sentences: int,
                     config: dict) -> list[dict]:
    windows, start, index = [], 0, 0
    while start < len(sentences):
        selected, count = [], 0
        end = start
        while end < len(sentences):
            sentence = sentences[end]
            cost = sentence["approx_tokens"]
            if selected and count + cost > target:
                break
            selected.append(sentence)
            count += cost
            end += 1
            if count >= target:
                break
        if not selected:
            raise AssertionError("chunker failed to make progress")

        sentence_ids = [s["sentence_id"] for s in selected]
        previous_ids = set(windows[-1]["sentence_ids"]) if windows else set()
        overlap_ids = [sid for sid in sentence_ids if sid in previous_ids]
        windows.append(_record(document_id, selected, stream_id, target, index, overlap_ids, config))
        index += 1
        if end >= len(sentences):
            break

        overlap_goal = max(1, round(target * overlap_ratio))
        overlap_cost, overlap_start = 0, end
        lower_bound = max(start + 1, end - max_overlap_sentences)
        candidate = end - 1
        next_cost = sentences[end]["approx_tokens"]
        while candidate >= lower_bound and overlap_cost < overlap_goal:
            proposed = overlap_cost + sentences[candidate]["approx_tokens"] + next_cost
            if proposed > target:
                break
            overlap_cost += sentences[candidate]["approx_tokens"]
            overlap_start = candidate
            candidate -= 1
        start = overlap_start if overlap_cost else end
    return windows


def _balanced_groups(sentences: list[dict], target: int, config: dict) -> list[list[dict]]:
    """Partition a run globally so a short final tail is rebalanced when possible.

    The dynamic program first minimizes invalid groups, then distance from the
    target. It never changes sentence text/order and never crosses a run.
    """
    n = len(sentences)
    minimum = config["chunk_candidate_min_tokens"]
    maximum = config["chunk_candidate_max_tokens"]
    min_sentences = config["chunk_candidate_min_sentences"]
    best: list[tuple[int, int, int, list[int]] | None] = [None] * (n + 1)
    best[n] = (0, 0, 0, [])
    for i in range(n - 1, -1, -1):
        choices = []
        for j in range(i + 1, n + 1):
            token_count = approx_tokens(" ".join(s["text"] for s in sentences[i:j]))
            if token_count > maximum and j > i + 1:
                break
            suffix = best[j]
            if suffix is None:
                continue
            valid = j - i >= min_sentences and minimum <= token_count <= maximum
            invalid = suffix[0] + (0 if valid else 1)
            deviation = suffix[1] + abs(token_count - target)
            choices.append((invalid, deviation, suffix[2] + 1, [j, *suffix[3]]))
            if token_count > maximum:
                break
        best[i] = min(choices, default=None, key=lambda x: (x[0], x[1], x[2], x[3]))
    if best[0] is None:
        return [[s] for s in sentences]
    groups, start = [], 0
    for end in best[0][3]:
        groups.append(sentences[start:end])
        start = end
    return groups


def _balanced_windows_for_run(document_id: str, sentences: list[dict], stream_id: str,
                              target: int, config: dict) -> list[dict]:
    return [_record(document_id, group, stream_id, target, index, [], config)
            for index, group in enumerate(_balanced_groups(sentences, target, config))]


def build_chunk_streams(sentences: list[dict], config: dict) -> list[dict]:
    """Build overlapping views from complete prose sentences after document splits are fixed."""
    targets = config.get("chunk_stream_targets", [])
    if not targets:
        return []
    overlap_ratio = config.get("chunk_overlap_ratio", 0.25)
    max_overlap_sentences = config.get("chunk_overlap_max_sentences", 3)
    ordered = sorted(sentences, key=lambda s: s["ordinal"])
    runs, current = [], []

    def flush():
        nonlocal current
        if current:
            runs.append(current)
            current = []

    previous = None
    for sentence in ordered:
        eligible = sentence.get("sentence_complete") and sentence.get("content_type") == "prose"
        if not eligible:
            flush()
            previous = None
            continue
        if previous and (sentence["run_id"] != previous["run_id"]
                         or sentence["section_id"] != previous["section_id"]
                         or sentence["ordinal"] != previous["ordinal"] + 1):
            flush()
        current.append(sentence)
        previous = sentence
    flush()

    result = []
    for target in targets:
        stream_id = f"t{target}"
        run_window_counts = {}
        for run in runs:
            if config.get("chunk_packing") == "balanced_nonoverlap":
                windows = _balanced_windows_for_run(run[0]["document_id"], run, stream_id, target, config)
            else:
                windows = _windows_for_run(run[0]["document_id"], run, stream_id, target,
                                           overlap_ratio, max_overlap_sentences, config)
            for window in windows:
                key = (window["document_id"], window["run_id"])
                window["window_index"] = run_window_counts.get(key, 0)
                prefix = "chunk32" if window["schema_version"] == "human-v3.2" else "chunk31"
                window["chunk_id"] = ident(prefix, window["document_id"], stream_id,
                                           window["window_index"], window["sentence_ids"])
                run_window_counts[key] = window["window_index"] + 1
                result.append(window)
    return result
