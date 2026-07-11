"""Job Bank (jobbank.gc.ca) — Government of Canada's national job board.

https://www.jobbank.gc.ca/jobsearch

Server-rendered results list; each posting is an ``article.action-buttons``
wrapping a single ``a.resultJobItem`` that holds title/company/location/salary
in a flat ``ul.list-unstyled``. robots.txt sets Crawl-delay: 5, so this source
throttles slower than the 2s default the other custom scrapers use.

Nationwide index — like CSLA, most hits are out-of-province; commute/location
scoring naturally ranks those down. A broad keyword list returns thousands of
hits per search, so to keep request volume sane we (a) cap how many search
terms we spend requests on and (b) only fetch the full detail page for
BC/remote-looking postings, falling back to the card summary text for
everything else (still enough for scoring, just a thinner description).
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup
from dateutil import parser as dateparse

from scrapers.base import Fetcher

log = logging.getLogger("scrapers.jobbank")

SOURCE = "jobbank"
_BASE = "https://www.jobbank.gc.ca"
_SEARCH = f"{_BASE}/jobsearch/jobsearch"

_MAX_KEYWORDS = 8
_EXT_ID_RE = re.compile(r"/jobposting/(\d+)")


def _detail(fetcher: Fetcher, url: str) -> str:
    try:
        html = fetcher.get(url)
    except Exception as e:  # noqa: BLE001
        log.debug("Job Bank detail fetch failed for %s: %s", url, e)
        return ""
    soup = BeautifulSoup(html, "lxml")
    # The details body also holds page chrome (loading spinners, apply/share
    # widgets, "Employer details" boilerplate) around the actual content, so
    # target the specific requirements block rather than the whole container.
    main = soup.select_one(".main-job-posting-detail.job-posting-detail-requirements")
    if main is None:
        # Postings sourced from a third party (e.g. Indeed) don't get a full
        # detail page on Job Bank — fall back to the brief info list instead
        # of the surrounding chrome.
        main = soup.select_one(".job-posting-brief")
    if main is None:
        return ""
    return main.get_text(" ", strip=True)


def _parse_card(card) -> dict | None:
    link = card.select_one("a.resultJobItem[href*='/jobposting/']")
    if not link:
        return None
    m = _EXT_ID_RE.search(link["href"])
    if not m:
        return None
    ext = m.group(1)
    url = f"{_BASE}/jobsearch/jobposting/{ext}"

    title_el = link.select_one("h3.title .noctitle")
    title = title_el.get_text(strip=True) if title_el else ""
    company_el = link.select_one("li.business")
    company = company_el.get_text(strip=True) if company_el else ""
    location_el = link.select_one("li.location")
    location = location_el.get_text(" ", strip=True) if location_el else ""
    location = re.sub(r"^\s*Location\s*", "", location).strip()
    date_el = link.select_one("li.date")
    posted_raw = date_el.get_text(strip=True) if date_el else ""
    posted_at = None
    if posted_raw:
        try:
            posted_at = dateparse.parse(posted_raw, fuzzy=True)
        except (ValueError, OverflowError):
            log.debug("unparseable date %r", posted_raw)
    salary_el = link.select_one("li.salary")
    salary_raw = None
    if salary_el:
        salary_raw = re.sub(r"^\s*Salary\s*", "", salary_el.get_text(" ", strip=True)).strip()

    return {
        "ext": ext, "url": url, "title": title, "company": company,
        "location": location, "posted_at": posted_at, "salary_raw": salary_raw,
    }


def fetch(cfg: dict) -> list[dict]:
    fetcher = Fetcher(min_interval=5.0)
    sq = cfg.get("search_queries", {})
    keywords = sq.get("keywords", [])[:_MAX_KEYWORDS]
    location = sq.get("location", "Vancouver, BC")

    seen: dict[str, dict] = {}
    for kw in keywords:
        try:
            html = fetcher.get(_SEARCH, params={
                "searchstring": kw,
                "locationstring": location,
                "sort": "D",
            })
        except Exception as e:  # noqa: BLE001
            log.warning("Job Bank list fetch failed for %r: %s", kw, e)
            continue
        soup = BeautifulSoup(html, "lxml")
        for card in soup.select("article.action-buttons"):
            parsed = _parse_card(card)
            if parsed and parsed["ext"] not in seen:
                seen[parsed["ext"]] = parsed

    out: list[dict] = []
    for parsed in seen.values():
        is_bc_or_remote = "(BC)" in parsed["location"] or "remote" in parsed["location"].lower()
        card_summary = f"{parsed['title']} — {parsed['company']} — {parsed['location']}"
        description = (_detail(fetcher, parsed["url"]) if is_bc_or_remote else "") or card_summary
        out.append({
            "source": SOURCE,
            "external_id": parsed["ext"],
            "url": parsed["url"],
            "title": parsed["title"],
            "company": parsed["company"],
            "location": parsed["location"],
            "description": description,
            "posted_at": parsed["posted_at"],
            "salary_raw": parsed["salary_raw"],
        })
    log.info("Job Bank: %d postings (%d keywords searched)", len(out), len(keywords))
    return out
