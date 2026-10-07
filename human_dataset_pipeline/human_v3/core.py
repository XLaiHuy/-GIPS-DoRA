"""Pure, deterministic text and lineage operations for Human v3."""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
import unicodedata
from pathlib import Path

TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)
WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
SPLITS = ("train", "dev", "test")


def hierarchical_release(config: dict) -> bool:
    """Return whether the release uses the v3.1+ hierarchy/chunk contract."""
    try:
        major, minor, *_ = (int(x) for x in str(config.get("version", "3.0")).split("."))
    except (TypeError, ValueError):
        return False
    return (major, minor) >= (3, 1)


def record_schema_version(config: dict) -> str:
    return "human-v3.2" if str(config.get("version", "3.0")).startswith("3.2") else (
        "human-v3.1" if hierarchical_release(config) else "human-v3.0")


def sha(text: str) -> str:
    # PDFs can expose isolated UTF-16 surrogates for unsupported math glyphs.
    # Hash those code units deterministically so the page can be quarantined
    # at the line/sentence level instead of aborting extraction for the file.
    return hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for part in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def ident(prefix: str, *parts) -> str:
    return prefix + "_" + sha(json.dumps(parts, ensure_ascii=False, separators=(",", ":")))[:24]


def rows(path: Path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except Exception as exc:
                    raise ValueError(f"{path}:{line_no}: invalid JSON") from exc


def dump(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class JsonlWriter:
    """Gzip bytes are deterministic: no timestamp or original filename."""
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.raw = path.open("wb")
        self.stream = (gzip.GzipFile(filename="", mode="wb", fileobj=self.raw, mtime=0)
                       if str(path).endswith(".gz") else self.raw)
        self.count = 0

    def write(self, value):
        try:
            encoded = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        except UnicodeEncodeError:
            # Keep valid Unicode readable while escaping only records that
            # contain invalid PDF text-layer code units.
            encoded = (json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")
        self.stream.write(encoded)
        self.count += 1

    def close(self):
        self.stream.close()
        self.raw.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def normalized(text: str) -> str:
    """ONLY NFC and whitespace. No word dictionaries, dehyphenation or casing."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def fingerprint(text: str) -> str:
    return sha(normalized(text).casefold())


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.casefold().replace("đ", "d"))
                   if not unicodedata.combining(c))


def approx_tokens(text: str) -> int:
    return max(1, math.ceil(len(TOKEN_RE.findall(text)) * 1.18))


def unsafe_reason(text: str) -> str | None:
    if any(unicodedata.category(c) in {"Cn", "Co", "Cs"} or c == "\ufffd" for c in text):
        return "unresolved_glyph"
    if any(unicodedata.category(c) in {"Cc", "Cf"} and c not in "\n\r\t" for c in text):
        return "unsafe_control_or_invisible"
    if "ƣ" in text or "Ƣ" in text:
        return "legacy_glyph_requires_evidence"
    if re.search(r"(?i)(?:api[_ -]?key|password|passwd|mat khau|mật khẩu|mssv|mã số sinh viên)\s*[:=]\s*\S+", text):
        return "private_or_secret_region"
    if re.search(r"\b(?:sk-[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{25,})\b", text):
        return "private_or_secret_region"
    return None


ABBREVIATIONS = {"ts", "ths", "th.s", "gs", "pgs", "pgs.ts", "tp", "dr", "mr", "mrs", "ms", "prof", "vs", "v.v", "e.g", "i.e"}


def sentence_bounds(text: str) -> list[tuple[int, int]]:
    """Do not split decimals, initialisms, URLs or periods inside IT names."""
    result, start = [], 0
    for match in re.finditer(r"[.!?…]+[\"'”’\)\]]*(?=\s|$)", text):
        end = match.end()
        prefix = text[start:match.start()]
        token = re.search(r"([^\s]+)$", prefix)
        if text[match.start()] == "." and token:
            value = token.group(1).casefold()
            if value in ABBREVIATIONS or re.fullmatch(r"(?:[a-z]\.)*[a-z]", value):
                continue
            if re.fullmatch(r"\d+(?:\.\d+)*", value) and len(prefix.split()) <= 1:
                continue
        tail = text[end:].lstrip()
        if tail and not re.match(r"[\"'“‘\(\[]*[A-ZÀ-ỸĐ0-9]", tail):
            continue
        left = start + len(text[start:end]) - len(text[start:end].lstrip())
        if left < end:
            result.append((left, end))
        start = end
    left = start + len(text[start:]) - len(text[start:].lstrip())
    if left < len(text):
        result.append((left, len(text)))
    return result


def sentence_reason(text: str) -> str | None:
    problem = unsafe_reason(text)
    if problem:
        return problem
    words = WORD_RE.findall(text)
    if len(words) < 6:
        return "not_enough_prose_evidence"
    if len(set(w.casefold() for w in words)) < 4:
        return "repeated_cells_or_symbols"
    if sum(c.isalpha() for c in text) / max(1, len(text)) < .55:
        return "non_prose_density"
    if not re.search(r"[.!?…][\"'”’\)\]]*$", text):
        return "extraction_truncated"
    first = next((c for c in text if c.isalpha()), "")
    if not first or not first.isupper():
        return "orphan_continuation"
    # Language gate is conservative; technical names are retained in Vietnamese prose.
    vi = {"và", "của", "là", "các", "được", "trong", "cho", "với", "một", "có", "để", "khi", "này", "từ", "trên", "theo", "không", "như", "bằng", "về"}
    if not any(w.casefold() in vi for w in words):
        return "language_requires_review"
    if re.match(r"(?i)^\s*(?:hình|bảng|figure|table)\s+\d+[.:]", text):
        return "caption"
    return None


def pack_sentences(sentences: list[dict], config: dict) -> tuple[list[list[dict]], list[tuple[dict, str]]]:
    """Pack contiguous verified runs; every rejected/overlong unit breaks a run."""
    groups, leftovers, pending = [], [], []

    def flush():
        nonlocal pending
        if not pending:
            return
        count = approx_tokens(" ".join(s["text"] for s in pending))
        if len(pending) >= config["min_sentences"] and count >= config["passage_min"]:
            groups.append(pending)
        elif groups and groups[-1][-1]["run_id"] == pending[0]["run_id"] and groups[-1][-1]["ordinal"] + 1 == pending[0]["ordinal"] and approx_tokens(" ".join(s["text"] for s in groups[-1] + pending)) <= config["passage_max"]:
            groups[-1].extend(pending)
        else:
            leftovers.extend((s, "short_or_single_sentence_tail") for s in pending)
        pending = []

    for s in sentences:
        if not s["sentence_complete"] or s["approx_tokens"] > config["passage_max"]:
            flush()
            leftovers.append((s, s.get("review_reason") or "overlong_sentence_preserved"))
            continue
        if pending and (s["run_id"] != pending[-1]["run_id"] or s["ordinal"] != pending[-1]["ordinal"] + 1 or approx_tokens(" ".join(x["text"] for x in pending + [s])) > config["passage_max"]):
            flush()
        pending.append(s)
        if len(pending) >= config["min_sentences"] and approx_tokens(" ".join(x["text"] for x in pending)) >= config["passage_target"]:
            flush()
    flush()
    return groups, leftovers


def validate_config(config: dict):
    if not 1 <= config["passage_min"] <= config["passage_target"] <= config["passage_max"]:
        raise ValueError("invalid passage budgets")
    if config["min_sentences"] < 2 or config.get("require_visual_review") is not True:
        raise ValueError("v3 requires two sentences and visual review")
    if set(config["split_ratios"]) != set(SPLITS) or abs(sum(config["split_ratios"].values()) - 1) > 1e-9:
        raise ValueError("invalid split ratios")
    if hierarchical_release(config):
        targets = config.get("chunk_stream_targets", [])
        if not targets or any(type(x) is not int or x < 32 for x in targets) or targets != sorted(set(targets)):
            raise ValueError("invalid multi-size chunk targets")
        minimum_overlap = 0 if config.get("chunk_packing") == "balanced_nonoverlap" else 1e-12
        if not minimum_overlap <= config.get("chunk_overlap_ratio", 0) <= 0.5:
            raise ValueError("invalid chunk overlap ratio")
        if not 1 <= config.get("chunk_overlap_max_sentences", 0) <= 3:
            raise ValueError("invalid chunk overlap sentence cap")
        if not (1 <= config.get("qa_sample_initial", 0) <= config.get("qa_sample_max", 0)
                and config.get("qa_sample_step", 0) > 0):
            raise ValueError("invalid adaptive QA sample sizes")
        if not 0.95 <= config.get("qa_min_pass_rate", 0) <= 1:
            raise ValueError("invalid QA pass-rate threshold")
        if config.get("chunk_packing") == "balanced_nonoverlap":
            if not (config.get("chunk_candidate_min_tokens", 0) <= config.get("passage_target", 0)
                    <= config.get("chunk_candidate_max_tokens", 0) <= config.get("passage_max", 0)):
                raise ValueError("invalid balanced chunk candidate budgets")
            if config.get("chunk_candidate_min_sentences", 0) < 2:
                raise ValueError("balanced chunks require at least two sentences")
