"""Nearby BC municipalities on the Oracle Taleo v2 career-site template.

Burnaby, New Westminster, and Richmond all run the same static, server-
rendered Taleo template (just a different ``org`` code each) — one generic
scraper covers all three. Listing rows are ``a.viewJobLink`` on the
``searchResults`` page; the requisition detail lives in the widest
``col-md-8`` div on ``viewRequisition``.

Not every nearby municipality uses this platform (Surrey is PeopleSoft,
Coquitlam/Port Moody are different systems) — those would need their own
scraper module if added later.
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from scrapers.base import Fetcher

log = logging.getLogger("scrapers.municipal_taleo")

SOURCE = "municipal_taleo"
_BASE = "https://tre.tbe.taleo.net/tre01/ats/careers/v2"

# (employer name, Taleo org code)
_MUNICIPALITIES = [
    ("City of Burnaby", "CITYBURNABY"),
    ("City of New Westminster", "Q8Z9AA"),
    ("City of Richmond", "TRQS8M"),
]


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


def _fetch_one(fetcher: Fetcher, employer: str, org: str) -> list[dict]:
    url = f"{_BASE}/searchResults?org={org}&cws=37"
    try:
        html = fetcher.get(url)
    except Exception as e:  # noqa: BLE001
        log.warning("%s Taleo list fetch failed: %s", employer, e)
        return []

    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    for link in soup.select("a.viewJobLink"):
        title = link.get_text(strip=True)
        href = link.get("href", "")
        if not href or not title:
            continue
        # A permanent catch-all requisition some cities keep posted, not a real job.
        if title.strip().lower() == "general application":
            continue
        job_url = href if href.startswith("http") else _BASE + "/" + href.lstrip("/")

        # No reliable posting date is shown in the listing; leave posted_at unset.
        meta = (link.find_parent("li") or link.find_parent("div") or link).get_text(" | ", strip=True)
        description = _detail(fetcher, job_url) or meta

        out.append({
            "source": SOURCE,
            "external_id": f"{org}-{_rid(job_url)}",
            "url": job_url,
            "title": title,
            "company": employer,
            "location": f"{employer.replace('City of ', '')}, BC",
            "description": description,
            "posted_at": None,
        })
    log.info("%s (Taleo): %d postings", employer, len(out))
    return out


def fetch(cfg: dict) -> list[dict]:
    fetcher = Fetcher(min_interval=2.0)
    out: list[dict] = []
    for employer, org in _MUNICIPALITIES:
        out.extend(_fetch_one(fetcher, employer, org))
    return out
