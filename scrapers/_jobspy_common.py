"""Shared plumbing for JobSpy-backed sources (Indeed, LinkedIn).

JobSpy (python-jobspy) scrapes these boards directly with no proxy needed for
our volume — the hand-rolled requests/BeautifulSoup approach in this repo's
history got Cloudflare-walled on both, but JobSpy's own request handling gets
through cleanly as of 2026-07. Treat that as a fact about JobSpy's current
approach, not a guarantee; if a board starts blocking it, this degrades the
same way any other source does (log + skip, doesn't crash the run).
"""
from __future__ import annotations

import logging
from datetime import date, datetime

log = logging.getLogger("scrapers.jobspy")


def _posted_at(v) -> datetime | None:
    # JobSpy returns a plain datetime.date, not datetime — the rest of the
    # pipeline (models.Job.posted_at, db.py's ISO serialization) expects datetime.
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime.combine(v, datetime.min.time())
    return None


def _num(v):
    """pandas gives NaN (a float), not None, for a missing numeric cell."""
    import math
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else v


def _str(v) -> str:
    """Same NaN issue for string columns — NaN is truthy, so `v or ''` doesn't
    catch it and a bare NaN would leak into a Job field as a float."""
    n = _num(v)
    return str(n) if n is not None else ""


def _salary_raw(row) -> str | None:
    lo, hi = _num(row.get("min_amount")), _num(row.get("max_amount"))
    if lo is None and hi is None:
        return None
    cur = row.get("currency") or ""
    interval = row.get("interval") or ""
    if lo is not None and hi is not None:
        return f"{cur} {lo:.0f} - {hi:.0f} {interval}".strip()
    amount = lo if lo is not None else hi
    return f"{cur} {amount:.0f} {interval}".strip()


def run(cfg: dict, *, site_name: str, source: str, linkedin_fetch_description: bool = False) -> list[dict]:
    try:
        from jobspy import scrape_jobs
    except ImportError:
        log.warning("%s: python-jobspy not installed (pip install python-jobspy) — skipping", source)
        return []

    sq = cfg["search_queries"]
    # JobSpy takes one search term, not a list. A plain space-joined string is
    # read as one long AND'd phrase (returns ~nothing) — quoted-OR is what
    # Indeed's/LinkedIn's search actually treats as alternatives.
    term = " OR ".join(f'"{k}"' for k in sq["keywords"][:6])

    kwargs = dict(
        site_name=[site_name],
        search_term=term,
        location=sq.get("location", "Vancouver, BC"),
        distance=sq.get("location_radius_km", 40),
        results_wanted=cfg.get("jobspy", {}).get("results_wanted", 30),
        hours_old=cfg.get("jobspy", {}).get("hours_old", 168),
        description_format="markdown",
    )
    if site_name == "indeed":
        kwargs["country_indeed"] = "Canada"
    if site_name == "linkedin":
        kwargs["linkedin_fetch_description"] = linkedin_fetch_description

    try:
        df = scrape_jobs(**kwargs)
    except Exception as e:  # noqa: BLE001 — never let one source kill the run
        log.warning("%s: JobSpy scrape failed: %s", source, e)
        return []

    out: list[dict] = []
    for _, row in df.iterrows():
        title = _str(row.get("title"))
        ext_id = _str(row.get("id")) or _str(row.get("job_url"))
        if not (title and ext_id):
            continue
        out.append({
            "source": source,
            "external_id": ext_id,
            "url": _str(row.get("job_url")),
            "title": title,
            "company": _str(row.get("company")),
            "location": _str(row.get("location")),
            "description": _str(row.get("description")),
            "posted_at": _posted_at(row.get("date_posted")),
            "salary_raw": _salary_raw(row),
        })
    log.info("%s (via JobSpy): %d postings", source, len(out))
    return out
