"""Confirms whether a job posting's URL is still actually live, via a real
HTTP request — distinct from html_render.is_stale's "hasn't been re-listed by
its source in N days" inference. A posting can go stale by that heuristic
while still being live (a slow-moving source), or vice versa; this checks the
one thing that actually matters: does the link still resolve.

Run it via `python scrape.py --check-links` (checks a batch across the whole
DB without scraping); it also runs automatically, at a smaller batch size, at
the end of every `scrape.py --all`/`--source` call — see scrape.py's
_check_links_batch. Both just call `run(conn, cfg)` below.

Confirmed-dead jobs get `link_dead` set and are never re-checked again (a
taken-down posting doesn't come back); confirmed-alive jobs get
`link_checked_at` bumped so they're not re-checked again until
recheck_after_days passes. A network error, timeout, or an ambiguous status
(e.g. 403 — bot-blocked, not necessarily gone) leaves the row untouched, so a
one-off hiccup just retries on the next run instead of wrongly burying a live
posting or permanently giving up on a dead one.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import requests

import db

log = logging.getLogger("linkcheck")

TIMEOUT = 10
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NorthArrowLinkCheck/1.0)"}
# Status codes that reliably mean "this posting is gone", not just "the site
# had a hiccup" or "a bot wall" — kept narrow on purpose so a flaky server or
# an anti-scraping block never wrongly buries a live posting.
_DEAD_STATUS = {404, 410}


def check_url(url: str) -> bool | None:
    """True = confirmed dead, False = confirmed alive, None = inconclusive.
    Callers should leave inconclusive jobs untouched rather than guessing."""
    if not url:
        return None
    try:
        resp = requests.head(url, timeout=TIMEOUT, allow_redirects=True, headers=_HEADERS)
        if resp.status_code in (405, 501):
            # Some ATSes reject HEAD outright; fall back to a real GET.
            resp = requests.get(url, timeout=TIMEOUT, allow_redirects=True, headers=_HEADERS)
    except requests.RequestException as e:
        log.debug("link check inconclusive for %s: %s", url, e)
        return None
    if resp.status_code in _DEAD_STATUS:
        return True
    if resp.status_code >= 400:
        return None  # e.g. 403 bot-blocked, 429 rate-limited — can't tell, don't guess
    return False


def _due_for_check(job, cutoff: datetime, recheck_after_days: int) -> bool:
    if not job.url or job.link_dead:
        return False
    if job.link_checked_at is None:
        return True
    checked = job.link_checked_at
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    return checked < cutoff - timedelta(days=recheck_after_days)


def run(conn, cfg: dict, *, batch_size: int = 50) -> dict:
    """Check a bounded batch of postings due for a (re)check, oldest-checked
    (or never-checked) first. Skips dismissed/duplicate jobs — already out of
    the queue, not worth spending a request on."""
    link_cfg = cfg.get("link_check", {})
    recheck_after_days = link_cfg.get("recheck_after_days", 7)
    cutoff = datetime.now(timezone.utc)

    jobs = db.query(conn, include_dismissed=False, include_duplicates=False)
    due = [j for j in jobs if _due_for_check(j, cutoff, recheck_after_days)]
    due.sort(key=lambda j: j.link_checked_at or datetime.min.replace(tzinfo=timezone.utc))
    batch = due[:batch_size]

    stats = {"checked": 0, "dead": 0, "inconclusive": 0}
    for job in batch:
        result = check_url(job.url)
        stats["checked"] += 1
        if result is True:
            db.set_link_dead(conn, job.id, True)
            stats["dead"] += 1
            log.info("posting confirmed dead: %s (%s @ %s)", job.id, job.title, job.company)
        elif result is False:
            db.set_link_dead(conn, job.id, False)
        else:
            stats["inconclusive"] += 1
    return stats
