"""Eluta.ca — Canadian job search engine.

https://www.eluta.ca/search

Server-rendered results list (``div.organic-job``, ``data-url`` attr points at
an Eluta-hosted detail page under ``/spl/...``). robots.txt disallows
``/search/`` (a directory) but not ``/search?q=...`` (a query string on the
bare path), so search results are fair game on paper.

In practice Eluta throws up a reCAPTCHA "User Verification" wall (served from
``/sandbox?...``, itself disallowed in robots.txt) after roughly 10 requests
in one run — this is a genuine bot-wall, not a parsing bug, and we don't try
to work around it. ``sources.eluta`` defaults to ``false`` in config; each run
just fetches whatever it can before getting walled and logs+skips the rest,
same as any other source hitting an error. Safe to flip on for a manual,
low-keyword-count run (see ``_MAX_KEYWORDS``/``_MAX_PAGES``), but not reliable
for the full daily keyword list.

Each ``/spl/`` detail page embeds a schema.org ``JobPosting`` block in a
``<script type="application/ld+json">`` tag — far more reliable than scraping
visible markup, since it gives us structured salary/location/employer plus the
canonical external posting URL. The one wrinkle: Eluta's template leaves stray
JS-style ``// comment`` fragments inside the JSON (invalid per spec), so those
are stripped before parsing.
"""
from __future__ import annotations

import json
import logging
import re

from bs4 import BeautifulSoup
from dateutil import parser as dateparse

from scrapers.base import Fetcher

log = logging.getLogger("scrapers.eluta")

SOURCE = "eluta"
_BASE = "https://www.eluta.ca"
_SEARCH = f"{_BASE}/search"

_MAX_KEYWORDS = 4  # kept low: the bot-wall tends to trip well before 8 keywords x 2 pages
_MAX_PAGES = 1
# Strips Eluta's stray `// word` trailing comments from an otherwise-JSON line
# without touching `http://`/`https://` URLs inside string values (those never
# end the line right after the `//`).
_TRAILING_COMMENT_RE = re.compile(r"[ \t]*//[ \t]*[A-Za-z_][A-Za-z0-9_]*[ \t]*$", re.M)


def _slug(data_url: str) -> str:
    return data_url.split("?", 1)[0].removeprefix("spl/")


def _job_posting_jsonld(html: str) -> dict | None:
    m = re.search(r"<script type='application/ld\+json'>(.*?)</script>", html, re.S)
    if not m:
        return None
    cleaned = _TRAILING_COMMENT_RE.sub("", m.group(1))
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        log.debug("Eluta JSON-LD parse failed: %s", e)
        return None


def _salary_raw(data: dict) -> str | None:
    sal = data.get("baseSalary", {}).get("value") if isinstance(data.get("baseSalary"), dict) else None
    if not sal:
        return None
    lo, hi = sal.get("minValue"), sal.get("maxValue")
    cur = data["baseSalary"].get("currency", "")
    unit = sal.get("unitText", "")
    if lo and hi:
        return f"{cur} {lo:.0f} - {hi:.0f} {unit}".strip()
    if lo or hi:
        return f"{cur} {lo or hi:.0f} {unit}".strip()
    return None


def _detail(fetcher: Fetcher, url: str) -> dict | None:
    try:
        html = fetcher.get(url)
    except Exception as e:  # noqa: BLE001
        log.debug("Eluta detail fetch failed for %s: %s", url, e)
        return None
    data = _job_posting_jsonld(html)
    if not data:
        return None
    desc_html = data.get("description", "")
    description = BeautifulSoup(desc_html, "lxml").get_text(" ", strip=True) if desc_html else ""
    addr = (data.get("jobLocation") or {}).get("address", {})
    location = ", ".join(p for p in (addr.get("addressLocality"), addr.get("addressRegion")) if p)
    posted_at = None
    if data.get("datePosted"):
        try:
            posted_at = dateparse.parse(data["datePosted"])
        except (ValueError, OverflowError):
            log.debug("unparseable date %r", data["datePosted"])
    return {
        "url": data.get("identifier") or url,
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

    seen: dict[str, str] = {}  # slug -> eluta detail URL
    for kw in keywords:
        for page in range(1, _MAX_PAGES + 1):
            params = {"q": kw, "l": location}
            if page > 1:
                params["pg"] = page
            try:
                html = fetcher.get(_SEARCH, params=params)
            except Exception as e:  # noqa: BLE001
                log.warning("Eluta list fetch failed for %r page %d: %s", kw, page, e)
                break
            soup = BeautifulSoup(html, "lxml")
            cards = soup.select("div.organic-job")
            if not cards:
                break
            for card in cards:
                data_url = card.get("data-url")
                if not data_url:
                    continue
                slug = _slug(data_url)
                seen.setdefault(slug, f"{_BASE}/{data_url}")

    out: list[dict] = []
    for slug, detail_url in seen.items():
        detail = _detail(fetcher, detail_url)
        if not detail:
            continue
        out.append({
            "source": SOURCE,
            "external_id": slug,
            "url": detail["url"],
            "title": detail["title"],
            "company": detail["company"],
            "location": detail["location"],
            "description": detail["description"],
            "posted_at": detail["posted_at"],
            "salary_raw": detail["salary_raw"],
        })
    log.info("Eluta: %d postings (%d keywords searched)", len(out), len(keywords))
    return out
