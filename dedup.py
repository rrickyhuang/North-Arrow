"""Cross-source duplicate detection (library module — no CLI of its own).

Run it via `python scrape.py --dedup` (re-run over the whole DB without
scraping); it also runs automatically at the end of every `scrape.py --all`/
`--source` call. Both just call `run(conn, cfg)` below.

Aggregators (PIBC/CSLA) and an org's own careers board can both list the same
posting, and a single aggregator can also list the same posting twice (e.g.
re-posted under a new external id). This groups stored jobs — same- or
cross-source — by fuzzy title match (guarded by a company or description
match, and a location or description match) and marks all but one "keeper"
per group as `duplicate_of` the keeper's id. Duplicates aren't deleted —
they're just hidden from `show.py`/the digest by default, same treatment as
`dismissed`, and `show.py --all` reveals them.
"""
from __future__ import annotations

import re
from itertools import combinations

from rapidfuzz import fuzz

import db
from models import Job

_WS_RE = re.compile(r"[^a-z0-9 ]")
# Job Bank descriptions are prefixed with page-loading/navigation chrome
# ("64100 25 Loading, please wait... Cancel ... Employer details ... Save to
# favourites ... Job details") that's near-identical across every posting.
# Left in, it inflates description similarity between unrelated jobs; strip
# everything up to and including the literal "Job details" marker.
_JOBBANK_CHROME_RE = re.compile(r".*?\bjob details\b", re.IGNORECASE | re.DOTALL)
# Below this length a description is either just this chrome with nothing
# real left after stripping, or (for some Job Bank listings) a stub that's
# nothing but "{title} - {company} - {location}" restated — no independent
# signal, so it shouldn't be trusted to confirm a match on its own.
_MIN_MEANINGFUL_DESC_LEN = 200


def _normalize(s: str | None) -> str:
    return _WS_RE.sub(" ", (s or "").lower()).strip()


def _clean_description(desc: str | None) -> str:
    text = (desc or "").strip()
    stripped = _JOBBANK_CHROME_RE.sub("", text, count=1).strip()
    return stripped if stripped else text


def _same_group(a: Job, b: Job, cfg: dict) -> bool:
    """Two jobs (same- or cross-source) are the same posting if their titles
    match strongly, AND (companies match OR the description itself is a
    strong match — aggregators/agencies sometimes surface a reseller's name
    instead of the actual employer's, so a weak company match alone
    shouldn't rule out a merge), AND (location matches OR the description is
    a strong match — this second guard keeps two genuinely different reqs at
    the same employer/title from merging)."""
    d = cfg.get("dedup", {})
    title_thr = d.get("title_similarity_threshold", 88)
    company_thr = d.get("company_similarity_threshold", 80)
    desc_thr = d.get("description_similarity_threshold", 85)

    if fuzz.token_sort_ratio(_normalize(a.title), _normalize(b.title)) < title_thr:
        return False

    a_desc, b_desc = _clean_description(a.description), _clean_description(b.description)
    desc_meaningful = (
        len(a_desc) >= _MIN_MEANINGFUL_DESC_LEN and len(b_desc) >= _MIN_MEANINGFUL_DESC_LEN
    )
    desc_strong = desc_meaningful and (
        fuzz.token_set_ratio(a_desc[:500], b_desc[:500]) >= desc_thr
    )

    a_co, b_co = _normalize(a.company), _normalize(b.company)
    if a_co and b_co:
        company_match = fuzz.token_sort_ratio(a_co, b_co) >= company_thr
    else:
        company_match = a_co == b_co  # both blank counts as a match
    if not company_match and not desc_strong:
        return False

    if a.location_normalized and a.location_normalized == b.location_normalized:
        return True
    return desc_strong


def find_groups(jobs: list[Job], cfg: dict) -> list[list[Job]]:
    """Union-find over all pairwise matches (same- and cross-source)."""
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

    for a, b in combinations(jobs, 2):
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
