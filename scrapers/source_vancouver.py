"""City of Vancouver job board — https://jobs.vancouver.ca/go/All-jobs/2538400/

SAP SuccessFactors career site, server-rendered as a plain HTML table (no JS
needed). Listing rows live in ``tr.data-row``; the title link is
``a.jobTitle-link``. Pagination is a simple offset in the URL path
(``/go/All-jobs/2538400/{offset}/``), 15 rows per page. The board also carries
postings from City-affiliated organizations (park board societies, community
centres) — the detail page's "Organization" line is a truer employer name
than the always-"City of Vancouver" listing source.
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup
from dateutil import parser as dateparse

from scrapers.base import Fetcher

log = logging.getLogger("scrapers.vancouver")

SOURCE = "vancouver_gov"
_BASE = "https://jobs.vancouver.ca"
_LIST_BASE = f"{_BASE}/go/All-jobs/2538400"
_PAGE_SIZE = 15
_MAX_PAGES = 20  # safety cap


def _detail(fetcher: Fetcher, url: str) -> tuple[str, str]:
    """Return (description_text, organization_name)."""
    try:
        html = fetcher.get(url)
    except Exception as e:  # noqa: BLE001
        log.debug("Vancouver detail fetch failed for %s: %s", url, e)
        return "", ""
    soup = BeautifulSoup(html, "lxml")
    main = soup.select_one("div.content") or soup
    text = main.get_text(" | ", strip=True)
    m = re.search(r"Organization\s*\|\s*([^|]+)", text)
    # Direct City postings have a boilerplate paragraph here instead of a short
    # name; only trust the match when it reads like an actual org name.
    org = m.group(1).strip() if m and len(m.group(1)) < 80 else ""
    return text, org


def fetch(cfg: dict) -> list[dict]:
    fetcher = Fetcher(min_interval=2.0)
    out: list[dict] = []
    for page in range(_MAX_PAGES):
        offset = page * _PAGE_SIZE
        url = f"{_LIST_BASE}/" if offset == 0 else f"{_LIST_BASE}/{offset}/"
        try:
            html = fetcher.get(url)
        except Exception as e:  # noqa: BLE001
            log.warning("Vancouver list fetch failed at offset %d: %s", offset, e)
            break

        soup = BeautifulSoup(html, "lxml")
        rows = soup.select("tr.data-row")
        if not rows:
            break

        for row in rows:
            link = row.select_one("a.jobTitle-link")
            if not link:
                continue
            title = link.get_text(strip=True)
            href = link["href"]
            job_url = href if href.startswith("http") else _BASE + href
            ext = job_url.rstrip("/").split("/")[-1]

            date_span = row.select_one("span.jobDate")
            posted_at = None
            if date_span:
                try:
                    posted_at = dateparse.parse(date_span.get_text(strip=True), fuzzy=True)
                except (ValueError, OverflowError):
                    pass

            description, org = _detail(fetcher, job_url)
            company = org or "City of Vancouver"

            out.append({
                "source": SOURCE,
                "external_id": ext,
                "url": job_url,
                "title": title,
                "company": company,
                # City-wide board; postings rarely give a scrapeable street
                # address, so anchor location to the city for geocoding.
                "location": "Vancouver, BC",
                "description": description or row.get_text(" ", strip=True),
                "posted_at": posted_at,
            })

        if len(rows) < _PAGE_SIZE:
            break
    log.info("City of Vancouver: %d postings", len(out))
    return out
