"""Canadian Society of Landscape Architects job board.
https://www.csla-aapc.ca/career-resources/jobs-rfps

Server-rendered Drupal view: a flat run of ``.views-field`` divs in the order
date -> title(+link) -> company -> location, repeated per posting (the first
set are header labels and are skipped). National board, so most postings are
out-of-province; the BC ones are high-value and the commute/location scoring
naturally ranks the rest down.
"""
from __future__ import annotations

import logging

from bs4 import BeautifulSoup
from dateutil import parser as dateparse

from scrapers.base import Fetcher

log = logging.getLogger("scrapers.csla")

SOURCE = "csla"
_BASE = "https://www.csla-aapc.ca"
_LIST = f"{_BASE}/career-resources/jobs-rfps"


def _has_class(el, fragment: str) -> bool:
    return any(fragment in c for c in el.get("class", []))


def _detail(fetcher: Fetcher, url: str) -> str:
    try:
        html = fetcher.get(url)
    except Exception as e:  # noqa: BLE001
        log.debug("CSLA detail fetch failed for %s: %s", url, e)
        return ""
    soup = BeautifulSoup(html, "lxml")
    main = soup.select_one(".node__content, .content, main, #content") or soup
    return main.get_text(" ", strip=True)


def fetch(cfg: dict) -> list[dict]:
    fetcher = Fetcher(min_interval=2.0)
    try:
        html = fetcher.get(_LIST)
    except Exception as e:  # noqa: BLE001
        log.warning("CSLA list fetch failed: %s", e)
        return []

    soup = BeautifulSoup(html, "lxml")
    fields = soup.select("[class*=views-field]")

    # Walk the flat field stream, accumulating one record per title-with-link.
    out: list[dict] = []
    cur: dict = {}
    for f in fields:
        if _has_class(f, "views-field-field-date-of-posting"):
            cur = {"date": f.get_text(strip=True)}
        elif _has_class(f, "views-field-title"):
            a = f.find("a", href=True)
            if a:
                cur["title"] = a.get_text(strip=True)
                cur["href"] = a["href"]
        elif _has_class(f, "views-field-field-company-name"):
            cur["company"] = f.get_text(strip=True)
        elif _has_class(f, "views-field-field-job-location"):
            cur["location"] = f.get_text(strip=True)
            # location is the last field of a row — flush if it's a real posting
            if cur.get("href") and cur.get("title"):
                out.append(cur)
            cur = {}

    records: list[dict] = []
    for r in out:
        href = r["href"]
        url = href if href.startswith("http") else _BASE + href
        ext = href.rstrip("/").split("/")[-1]
        posted_at = None
        if r.get("date"):
            try:
                posted_at = dateparse.parse(r["date"], fuzzy=True)
            except (ValueError, OverflowError):
                log.debug("unparseable date %r", r["date"])
        description = _detail(fetcher, url) or r.get("title", "")
        records.append({
            "source": SOURCE,
            "external_id": ext,
            "url": url,
            "title": r.get("title", ""),
            "company": r.get("company", ""),
            "location": r.get("location", ""),
            "description": description,
            "posted_at": posted_at,
        })
    log.info("CSLA: %d postings", len(records))
    return records
