"""Nearby BC municipalities (plus SFU) on the Oracle Taleo v2 career-site
template.

Burnaby, New Westminster, Richmond, and Simon Fraser University all run the
same static, server-rendered Taleo template (just a different ``org`` code
each) — one generic scraper covers all of them. Listing rows are
``a.viewJobLink`` on the ``searchResults`` page; the requisition detail lives
in the widest ``col-md-8`` div on ``viewRequisition``. SFU's own board is
small (~30 postings) so no keyword filtering is applied here either — same
as the municipalities, everything is fetched and downstream scoring/
enrichment does the filtering.

Not every nearby municipality/institution uses this platform (Surrey is
PeopleSoft, Coquitlam/Port Moody are different systems, UBC is Workday) —
those need their own scraper module.
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from scrapers.base import Fetcher

log = logging.getLogger("scrapers.municipal_taleo")

SOURCE = "municipal_taleo"
_BASE = "https://tre.tbe.taleo.net/tre01/ats/careers/v2"
_PAGE_SIZE = 10
_MAX_PAGES = 10  # safety cap; SFU (the largest board here) runs ~30 postings

# (employer name, Taleo org code, fallback location if per-posting extraction fails)
_EMPLOYERS = [
    ("City of Burnaby", "CITYBURNABY", "Burnaby, BC"),
    ("City of New Westminster", "Q8Z9AA", "New Westminster, BC"),
    ("City of Richmond", "TRQS8M", "Richmond, BC"),
    ("Simon Fraser University", "SIMOFRAS", "Burnaby, BC"),  # main campus
]

# The head-info block's sibling divs, in order — everything after title/h4.
_INFO_FIELDS = ("department", "duration", "location")


def _detail(fetcher: Fetcher, url: str) -> str:
    try:
        html = fetcher.get(url)
    except Exception as e:  # noqa: BLE001
        log.debug("Taleo detail fetch failed for %s: %s", url, e)
        return ""
    soup = BeautifulSoup(html, "lxml")
    body = soup.select_one("div.col-xs-12.col-sm-12.col-md-8") or soup
    return body.get_text(" | ", strip=True)


def _rid(url: str) -> str:
    m = re.search(r"[?&]rid=(\d+)", url)
    return m.group(1) if m else url


def _fetch_one(fetcher: Fetcher, employer: str, org: str, default_location: str) -> list[dict]:
    out: list[dict] = []
    for page in range(_MAX_PAGES):
        row_from = page * _PAGE_SIZE
        url = f"{_BASE}/searchResults?org={org}&cws=37" + (f"&rowFrom={row_from}" if row_from else "")
        try:
            html = fetcher.get(url)
        except Exception as e:  # noqa: BLE001
            log.warning("%s Taleo list fetch failed at rowFrom %d: %s", employer, row_from, e)
            break

        soup = BeautifulSoup(html, "lxml")
        links = soup.select("a.viewJobLink")
        if not links:
            break

        for link in links:
            title = link.get_text(strip=True)
            href = link.get("href", "")
            if not href or not title:
                continue
            # A permanent catch-all requisition some cities keep posted, not a real job.
            if title.strip().lower() == "general application":
                continue
            job_url = href if href.startswith("http") else _BASE + "/" + href.lstrip("/")

            # The head-info block holds title (h4) then department, employment
            # duration, and location as three sibling divs, in that order — the
            # last one is the real per-posting location (needed for SFU, which
            # spans Burnaby/Surrey/Vancouver campuses, unlike the single-city
            # municipalities where a fallback per-employer default is good
            # enough). Only trust the extracted value when the expected number
            # of sibling divs is actually present — a differently-shaped block
            # (a field missing/reordered) falls back rather than silently
            # mislabeling department/duration text as the location.
            info = link.find_parent(class_="oracletaleocwsv2-accordion-head-info")
            location = default_location
            if info:
                divs = info.find_all("div", recursive=False)
                if len(divs) == len(_INFO_FIELDS):
                    location = divs[-1].get_text(strip=True) or location

            # No reliable posting date is shown in the listing; leave posted_at unset.
            meta = (info or link.find_parent("li") or link.find_parent("div") or link).get_text(" | ", strip=True)
            description = _detail(fetcher, job_url) or meta

            out.append({
                "source": SOURCE,
                "external_id": f"{org}-{_rid(job_url)}",
                "url": job_url,
                "title": title,
                "company": employer,
                "location": location,
                "description": description,
                "posted_at": None,
            })

        if len(links) < _PAGE_SIZE:
            break
    log.info("%s (Taleo): %d postings", employer, len(out))
    return out


def fetch(cfg: dict) -> list[dict]:
    fetcher = Fetcher(min_interval=2.0)
    out: list[dict] = []
    for employer, org, default_location in _EMPLOYERS:
        out.extend(_fetch_one(fetcher, employer, org, default_location))
    return out
