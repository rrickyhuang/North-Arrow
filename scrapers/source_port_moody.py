"""City of Port Moody career site — a Govstack/njoyn (WCF ``Xweb.asp``) board.
URLs carry a ``tbtoken`` session parameter, but it isn't actually required:
requesting the page with just ``clid`` + ``page=joblisting`` redirects to a
fresh, working token, so no cookie/session handling is needed. Listing rows
are a plain ``<table>``; the detail page's ``og:description`` meta tag holds
the full posting text (HTML-tagged, stripped here).
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup
from dateutil import parser as dateparse

from scrapers.base import Fetcher

log = logging.getLogger("scrapers.port_moody")

SOURCE = "port_moody"
_BASE = "https://careers.portmoody.ca/cl3/xweb/xweb.asp"
_LIST_URL = f"{_BASE}?clid=56828&page=joblisting"


def _detail(fetcher: Fetcher, url: str) -> str:
    try:
        html = fetcher.get(url)
    except Exception as e:  # noqa: BLE001
        log.debug("Port Moody detail fetch failed for %s: %s", url, e)
        return ""
    soup = BeautifulSoup(html, "lxml")
    # The page carries a generic site-description og:description near the top
    # and the actual job description further down — take the last one.
    metas = soup.select('meta[property="og:description"]')
    if not metas or not metas[-1].get("content"):
        return ""
    return BeautifulSoup(metas[-1]["content"], "lxml").get_text(" ", strip=True)


def fetch(cfg: dict) -> list[dict]:
    fetcher = Fetcher(min_interval=2.0)
    try:
        html = fetcher.get(_LIST_URL)
    except Exception as e:  # noqa: BLE001
        log.warning("Port Moody list fetch failed: %s", e)
        return []

    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    for row in soup.select("tr"):
        cells = row.find_all("td")
        if len(cells) != 7:
            continue
        link = cells[0].find("a")
        if not link or not link.get("href"):
            continue
        job_id = link.get_text(strip=True)
        title = cells[1].get_text(strip=True)
        location = cells[4].get_text(strip=True)
        posted_raw = cells[5].get_text(strip=True)

        href = link["href"]
        job_url = href if href.startswith("http") else f"{_BASE.rsplit('/', 1)[0]}/{href.lstrip('/')}"

        posted_at = None
        if posted_raw and posted_raw.upper() != "N/A":
            try:
                posted_at = dateparse.parse(posted_raw, fuzzy=True)
            except (ValueError, OverflowError):
                log.debug("unparseable date %r", posted_raw)

        description = _detail(fetcher, job_url) or row.get_text(" | ", strip=True)

        out.append({
            "source": SOURCE,
            "external_id": job_id,
            "url": job_url,
            "title": title,
            "company": "City of Port Moody",
            "location": f"{location}, Port Moody, BC" if location and location.upper() != "N/A"
                        else "Port Moody, BC",
            "description": description,
            "posted_at": posted_at,
        })
    log.info("City of Port Moody: %d postings", len(out))
    return out
