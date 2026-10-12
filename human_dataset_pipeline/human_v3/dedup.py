"""Exact and exact-verified shingle joins, followed by deterministic group splits."""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

from .core import SPLITS, fingerprint, fold, ident, sha


class UnionFind:
    def __init__(self, keys):
        self.parent = {k: k for k in keys}

    def find(self, key):
        while self.parent[key] != key:
            self.parent[key] = self.parent[self.parent[key]]
            key = self.parent[key]
        return key

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.parent[max(a, b)] = min(a, b)


def shingles(text: str, size: int) -> frozenset[str]:
    words = re.findall(r"\w+", text.casefold())
    return frozenset(" ".join(words[i:i + size]) for i in range(len(words) - size + 1))


def near_pairs(passages: list[dict], config: dict, cross_split_only: bool = False):
    """Lossless prefix candidate join for Jaccard >= threshold (not sampled LSH).

    Every generated candidate is measured with the full shingle sets. Global
    rare-first ordering reduces candidate volume without dropping common text.
    """
    threshold = config["near_duplicate_threshold"]
    # Intern each distinct shingle once. Keeping a separate Python string for
    # every occurrence across thousands of passages can consume several GB;
    # shared integer IDs preserve exact set/Jaccard semantics with far less RAM.
    size = config["near_duplicate_shingle_words"]
    interned: dict[str, int] = {}
    shingle_texts: list[str] = []
    sets = {}
    for i, passage in enumerate(passages):
        if len(passage["text"].split()) < config["near_duplicate_min_words"]:
            continue
        words = re.findall(r"\w+", passage["text"].casefold())
        ids = set()
        for start in range(len(words) - size + 1):
            value = " ".join(words[start:start + size])
            sid = interned.get(value)
            if sid is None:
                sid = len(shingle_texts)
                interned[value] = sid
                shingle_texts.append(value)
            ids.add(sid)
        if ids:
            sets[i] = frozenset(ids)
    freq = Counter(s for values in sets.values() for s in values)
    posting = defaultdict(lambda: defaultdict(list)) if cross_split_only else defaultdict(list)
    for i in sorted(sets, key=lambda j: (len(sets[j]), passages[j].get("passage_id", passages[j].get("chunk_id", str(j))))):
        current = sets[i]
        prefix = sorted(current, key=lambda s: (freq[s], shingle_texts[s]))[:len(current) - math.ceil(threshold * len(current)) + 1]
        if cross_split_only:
            current_split = passages[i].get("split")
            candidates = {j for s in prefix
                          for split, previous in posting[s].items() if split != current_split
                          for j in previous if len(sets[j]) >= threshold * len(current)}
        else:
            candidates = {j for s in prefix for j in posting[s] if len(sets[j]) >= threshold * len(current)}
        for j in sorted(candidates):
            if passages[i]["document_id"] == passages[j]["document_id"]:
                continue
            intersection = len(current & sets[j])
            score = intersection / (len(current) + len(sets[j]) - intersection)
            if score >= threshold:
                yield i, j, score
        for s in prefix:
            if cross_split_only:
                posting[s][passages[i].get("split")].append(i)
            else:
                posting[s].append(i)


def group_and_split(documents: list[dict], passages: list[dict], config: dict, sentence_records=()):
    uf = UnionFind(d["document_id"] for d in documents)
    edges, fingerprints, duplicate_of = [], {}, {}
    related = {}
    sentence_seen = {}
    for sentence in sentence_records:
        if not sentence.get("sentence_complete") or len(sentence["text"].split()) < 20:
            continue
        key = fingerprint(sentence["text"])
        did = sentence["document_id"]
        if key in sentence_seen and sentence_seen[key] != did:
            left = sentence_seen[key]
            edges.append({"kind": "exact_long_sentence", "left": left, "right": did,
                          "fingerprint": key, "resolution": "audit_only_shared_sentence"})
        sentence_seen.setdefault(key, did)
    for d in sorted(documents, key=lambda x: x["document_id"]):
        meta = d["metadata"]
        keys = []
        url = meta.get("source_url", "") or ""
        repo = re.search(r"github\.com/([^/]+/[^/#?]+)", url, re.I)
        if repo:
            keys.append("repository:" + repo.group(1).casefold())
        authors = meta.get("authors") or []
        if isinstance(authors, str):
            authors = [authors]
        title = meta.get("title") or ""
        if authors and len(title) > 20:
            keys.append("work:" + sha(fold(title) + "|" + "|".join(sorted(fold(a) for a in authors))))
        for key in keys:
            if key in related:
                uf.union(d["document_id"], related[key])
                edges.append({"kind": "related_source", "left": related[key], "right": d["document_id"],
                              "key_sha256": sha(key), "resolution": "same_group"})
            related[key] = d["document_id"]
    for p in sorted(passages, key=lambda x: x["passage_id"]):
        fp = fingerprint(p["text"])
        if fp in fingerprints:
            old = fingerprints[fp]
            duplicate_of[p["passage_id"]] = old["passage_id"]
            edges.append({"kind": "exact_passage", "left": old["document_id"], "right": p["document_id"],
                          "left_passage": old["passage_id"], "right_passage": p["passage_id"],
                          "similarity": 1.0, "resolution": "duplicate_passage_removed"})
        else:
            fingerprints[fp] = p
    members = defaultdict(list)
    for d in documents:
        members[uf.find(d["document_id"])].append(d)
    total = Counter()
    strata = {}
    for d in documents:
        m = d["metadata"]
        strata[d["document_id"]] = (m.get("domain_id", "unknown"), d.get("source_collection"),
            str(m.get("year", "unknown"))[:3])
        total[strata[d["document_id"]]] += 1
    assigned = {s: Counter() for s in SPLITS}
    counts = Counter()
    mapping = {}
    groups = sorted(members.values(), key=lambda ds: (-len(ds), sha(config["seed"] + min(d["document_id"] for d in ds))))
    for group in groups:
        group_counts = Counter(strata[d["document_id"]] for d in group)
        def cost(split):
            # Compare the increase in global and stratum squared errors.
            ratio = config["split_ratios"][split]
            target = max(1, len(documents) * ratio)
            before = counts[split] - target
            delta = ((before + len(group)) ** 2 - before ** 2) / target
            for k, n in group_counts.items():
                t = max(1, total[k] * ratio)
                old = assigned[split][k] - t
                delta += .25 * ((old + n) ** 2 - old ** 2) / t
            return delta, SPLITS.index(split)
        split = min(SPLITS, key=cost)
        counts[split] += len(group)
        assigned[split].update(group_counts)
        gid = ident("group3", sorted(d["document_id"] for d in group))
        for d in group:
            mapping[d["document_id"]] = {"group_id": gid, "split": split}
    return mapping, edges, duplicate_of
