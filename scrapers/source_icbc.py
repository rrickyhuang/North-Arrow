"""ICBC (Insurance Corporation of British Columbia) careers —
https://careers.icbc.com/go/All-Current-Job-Opportunities/2681517/

Same SAP SuccessFactors template as ``source_vancouver.py`` (server-rendered
``tr.data-row`` / ``a.jobTitle-link``, ``div.content`` detail body) — kept as
its own module rather than folded into that one since ICBC is a distinct
employer, not a City-of-Vancouver-affiliated org, matching how north_shore/
port_moody/coquitlam each get their own file despite platform overlap.

Small board (~20-30 open postings total), so — unlike UBC's Workday board —
this fetches everything rather than searching by keyword.
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from scrapers.base import Fetcher

log = logging.getLogger("scrapers.icbc")

SOURCE = "icbc"
_BASE = "https://careers.icbc.com"
_LIST_BASE = f"{_BASE}/go/All-Current-Job-Opportunities/2681517"
_PAGE_SIZE = 25
_MAX_PAGES = 10  # safety cap; board typically lists well under 50 open jobs
_COMPANY = "ICBC"


def _detail(fetcher: Fetcher, url: str) -> str:
    try:
        html = fetcher.get(url)
    except Exception as e:  # noqa: BLE001
        log.debug("ICBC detail fetch failed for %s: %s", url, e)
        return ""
    soup = BeautifulSoup(html, "lxml")
    main = soup.select_one("div.content") or soup
    return main.get_text(" | ", strip=True)


def fetch(cfg: dict) -> list[dict]:
    fetcher = Fetcher(min_interval=2.0)
    out: list[dict] = []
    for page in range(_MAX_PAGES):
        offset = page * _PAGE_SIZE
        url = f"{_LIST_BASE}/" if offset == 0 else f"{_LIST_BASE}/{offset}/"
        try:
            html = fetcher.get(url)
        except Exception as e:  # noqa: BLE001
            log.warning("ICBC list fetch failed at offset %d: %s", offset, e)
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
            m = re.search(r"/(\d+)/?$", job_url.rstrip("/"))
            ext = m.group(1) if m else job_url.rstrip("/").split("/")[-1]

            loc_cell = row.select_one("td.colLocation span.jobLocation")
            location = loc_cell.get_text(strip=True) if loc_cell else "British Columbia, Canada"

            description = _detail(fetcher, job_url) or row.get_text(" | ", strip=True)

            out.append({
                "source": SOURCE,
                "external_id": ext,
                "url": job_url,
                "title": title,
                "company": _COMPANY,
                "location": location,
                "description": description,
                "posted_at": None,
            })

        if len(rows) < _PAGE_SIZE:
            break
    log.info("ICBC: %d postings", len(out))
    return out
