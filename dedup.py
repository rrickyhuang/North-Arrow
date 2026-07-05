"""Cross-source duplicate detection (library module — no CLI of its own).

Run it via `python scrape.py --dedup` (re-run over the whole DB without
scraping); it also runs automatically at the end of every `scrape.py --all`/
`--source` call. Both just call `run(conn, cfg)` below.

Aggregators (PIBC/CSLA) and an org's own careers board can both list the same
posting. This groups stored jobs across DIFFERENT sources by fuzzy title +
company match (guarded by a location or description match too) and marks all
but one "keeper" per group as `duplicate_of` the keeper's id. Duplicates
aren't deleted — they're just hidden from `show.py`/the digest by default,
same treatment as `dismissed`, and `show.py --all` reveals them.
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz

import db
from models import Job

_WS_RE = re.compile(r"[^a-z0-9 ]")


def _normalize(s: str | None) -> str:
    return _WS_RE.sub(" ", (s or "").lower()).strip()


def _same_group(a: Job, b: Job, cfg: dict) -> bool:
    """Two jobs from DIFFERENT sources are the same posting if their titles
    and companies both match strongly, AND (location matches OR the
    description itself is a strong match) — the second guard keeps two
    genuinely different reqs at the same employer/title from merging."""
    if a.source == b.source:
        return False
    d = cfg.get("dedup", {})
    title_thr = d.get("title_similarity_threshold", 88)
    company_thr = d.get("company_similarity_threshold", 80)
    desc_thr = d.get("description_similarity_threshold", 85)

    if fuzz.token_sort_ratio(_normalize(a.title), _normalize(b.title)) < title_thr:
        return False

    a_co, b_co = _normalize(a.company), _normalize(b.company)
    if a_co and b_co:
        if fuzz.token_sort_ratio(a_co, b_co) < company_thr:
            return False
    elif a_co != b_co:  # one blank, one not — too uncertain to merge
        return False

    if a.location_normalized and a.location_normalized == b.location_normalized:
        return True
    desc_ratio = fuzz.token_set_ratio((a.description or "")[:500], (b.description or "")[:500])
    return desc_ratio >= desc_thr


def find_groups(jobs: list[Job], cfg: dict) -> list[list[Job]]:
    """Union-find over pairwise matches, restricted to different-source pairs."""
    parent = {j.id: j.id for j in jobs}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    by_source: dict[str, list[Job]] = {}
    for j in jobs:
        by_source.setdefault(j.source, []).append(j)
    sources = list(by_source.keys())
    for i, src_a in enumerate(sources):
        for src_b in sources[i + 1:]:
            for a in by_source[src_a]:
                for b in by_source[src_b]:
                    if _same_group(a, b, cfg):
                        union(a.id, b.id)

    groups: dict[str, list[Job]] = {}
    for j in jobs:
        groups.setdefault(find(j.id), []).append(j)
    return [g for g in groups.values() if len(g) > 1]


def _priority(job: Job, order: dict[str, int], default: int) -> int:
    return order.get(job.source, default)


def pick_keeper(group: list[Job], cfg: dict) -> Job:
    """The kept copy: prefer a direct/authoritative source (config-ranked)
    over an aggregator, then the higher score, then the lexically-lower id
    for a stable tiebreak across re-runs."""
    priority_list = cfg.get("dedup", {}).get("source_priority", [])
    order = {name: i for i, name in enumerate(priority_list)}
    default = len(priority_list)
    return min(group, key=lambda j: (_priority(j, order, default), -j.score, j.id))


def run(conn, cfg: dict) -> dict:
    if not cfg.get("dedup", {}).get("enabled", True):
        return {"groups": 0, "duplicates": 0}
    jobs = db.query(conn, include_dismissed=True, include_duplicates=True)
    groups = find_groups(jobs, cfg)

    stats = {"groups": len(groups), "duplicates": 0}
    newly_marked: set[str] = set()
    for group in groups:
        keeper = pick_keeper(group, cfg)
        for j in group:
            if j.id == keeper.id:
                continue
            if j.duplicate_of != keeper.id:
                db.set_duplicate(conn, j.id, keeper.id)
            newly_marked.add(j.id)
            stats["duplicates"] += 1

    # Clear any stale duplicate_of from jobs no longer grouped (e.g. the
    # keeper's own posting expired/changed enough to stop matching).
    for j in jobs:
        if j.duplicate_of and j.id not in newly_marked:
            db.set_duplicate(conn, j.id, None)

    return stats
