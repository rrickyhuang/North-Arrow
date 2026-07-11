"""BCJobs.ca — BC-focused job board.

https://www.bcjobs.ca/search-jobs

The results page itself is client-rendered (Backbone.js), but its own JS
(``assets/js/bc_job_search.js``) reveals a plain public JSON API backing it:
``/api/v1.1/public/jobs?q=&location=&page=``. That's what we hit directly —
no HTML scraping needed for the list step. ``location`` must be an exact
"City, Province" description string as returned by the site's location
autocomplete (``/api/v1.1/locations?q=...``); ``cfg.search_queries.location``
("Vancouver, BC") already matches that format. robots.txt is a generic
``Allow: /`` for default crawlers, so this is fair game.

Each listing's ``url`` is a normal server-rendered detail page that embeds a
schema.org ``JobPosting`` block in ``<script type="application/ld+json">`` —
valid JSON here (unlike Eluta's), so no cleanup needed before parsing.
"""
from __future__ import annotations

import json
import logging
import re

from bs4 import BeautifulSoup
from dateutil import parser as dateparse

from scrapers.base import Fetcher

log = logging.getLogger("scrapers.bcjobs")

SOURCE = "bcjobs"
_BASE = "https://www.bcjobs.ca"
_API = f"{_BASE}/api/v1.1/public/jobs"

_MAX_KEYWORDS = 8
_MAX_PAGES = 2


def _job_posting_jsonld(html: str) -> dict | None:
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        log.debug("BCJobs JSON-LD parse failed: %s", e)
        return None


def _salary_raw(data: dict) -> str | None:
    sal = data.get("baseSalary")
    if not isinstance(sal, dict):
        return None
    val = sal.get("value") or {}
    lo, hi = val.get("minValue"), val.get("maxValue")
    cur = sal.get("currency", "")
    unit = val.get("unitText", "")
    if lo and hi:
        return f"{cur} {lo:.0f} - {hi:.0f} {unit}".strip()
    if lo or hi:
        return f"{cur} {lo or hi:.0f} {unit}".strip()
    return None


def _detail(fetcher: Fetcher, url: str) -> dict | None:
    try:
        html = fetcher.get(url)
    except Exception as e:  # noqa: BLE001
        log.debug("BCJobs detail fetch failed for %s: %s", url, e)
        return None
    data = _job_posting_jsonld(html)
    if not data:
        return None
    desc_html = data.get("description", "")
    description = BeautifulSoup(desc_html, "lxml").get_text(" ", strip=True) if desc_html else ""
    places = data.get("jobLocation") or []
    place = places[0] if isinstance(places, list) and places else (places or {})
    addr = place.get("address", {})
    location = ", ".join(p for p in (addr.get("addressLocality"), addr.get("addressRegion")) if p)
    posted_at = None
    if data.get("datePosted"):
        try:
            posted_at = dateparse.parse(data["datePosted"])
        except (ValueError, OverflowError):
            log.debug("unparseable date %r", data["datePosted"])
    return {
        "title": data.get("title", ""),
        "company": (data.get("hiringOrganization") or {}).get("name", ""),
        "location": location,
        "description": description,
        "posted_at": posted_at,
        "salary_raw": _salary_raw(data),
    }


def fetch(cfg: dict) -> list[dict]:
    fetcher = Fetcher(min_interval=2.0)
    sq = cfg.get("search_queries", {})
    keywords = sq.get("keywords", [])[:_MAX_KEYWORDS]
    location = sq.get("location", "Vancouver, BC")

    seen: dict[str, str] = {}  # external_id -> detail path
    for kw in keywords:
        for page in range(1, _MAX_PAGES + 1):
            try:
                resp = fetcher.get(_API, params={"q": kw, "location": location, "page": page})
                data = json.loads(resp)
            except Exception as e:  # noqa: BLE001
                log.warning("BCJobs list fetch failed for %r page %d: %s", kw, page, e)
                break
            for item in data.get("data", []):
                ext = str(item["id"])
                seen.setdefault(ext, item["url"])
            if page >= data.get("paging", {}).get("pages", 0):
                break

    out: list[dict] = []
    for ext, path in seen.items():
        url = f"{_BASE}{path}"
        detail = _detail(fetcher, url)
        if not detail:
            continue
        out.append({
            "source": SOURCE,
            "external_id": ext,
            "url": url,
            "title": detail["title"],
            "company": detail["company"],
            "location": detail["location"],
            "description": detail["description"],
            "posted_at": detail["posted_at"],
            "salary_raw": detail["salary_raw"],
        })
    log.info("BCJobs: %d postings (%d keywords searched)", len(out), len(keywords))
    return out
