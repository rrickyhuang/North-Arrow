"""UBC staff careers — hosted on Workday (``ubc.wd10.myworkdayjobs.com``).

Workday's frontend is a JS SPA, but it's backed by a plain, unauthenticated
JSON API that we hit directly instead of rendering the page:

- List:   ``POST /wday/cxs/ubc/ubcstaffjobs/jobs``
          body ``{"appliedFacets": {}, "limit": N, "offset": N, "searchText": kw}``
- Detail: ``GET /wday/cxs/ubc/ubcstaffjobs{externalPath}``
          returns ``jobPostingInfo`` with the full HTML job description and a
          real ``startDate`` (the list view only has a relative "Posted N Days
          Ago" string, no use for a real posted_at).

UBC's board spans the whole university (research, medicine, trades, IT, ...),
so — like bcjobs/eluta/jobbank — this searches by ``search_queries.keywords``
rather than fetching everything.
"""
from __future__ import annotations

import json
import logging

from bs4 import BeautifulSoup
from dateutil import parser as dateparse

from scrapers.base import Fetcher

log = logging.getLogger("scrapers.ubc_workday")

SOURCE = "ubc_workday"
_TENANT_URL = "https://ubc.wd10.myworkdayjobs.com/wday/cxs/ubc/ubcstaffjobs"
_COMPANY = "University of British Columbia"

_MAX_KEYWORDS = 8
_PAGE_SIZE = 20
_MAX_PAGES = 3  # safety cap per keyword


def _detail(fetcher: Fetcher, external_path: str) -> dict | None:
    url = f"{_TENANT_URL}{external_path}"
    try:
        raw = fetcher.get(url)
    except Exception as e:  # noqa: BLE001
        log.debug("UBC Workday detail fetch failed for %s: %s", url, e)
        return None
    try:
        return json.loads(raw).get("jobPostingInfo")
    except json.JSONDecodeError as e:
        log.debug("UBC Workday detail JSON parse failed for %s: %s", url, e)
        return None


def fetch(cfg: dict) -> list[dict]:
    fetcher = Fetcher(min_interval=2.0)
    sq = cfg.get("search_queries", {})
    keywords = sq.get("keywords", [])[:_MAX_KEYWORDS]

    seen: set[str] = set()  # externalPath — Workday's own unique job-URL key
    for kw in keywords:
        for page in range(_MAX_PAGES):
            offset = page * _PAGE_SIZE
            try:
                data = fetcher.post_json(f"{_TENANT_URL}/jobs", {
                    "appliedFacets": {},
                    "limit": _PAGE_SIZE,
                    "offset": offset,
                    "searchText": kw,
                })
            except Exception as e:  # noqa: BLE001
                log.warning("UBC Workday list fetch failed for %r offset %d: %s", kw, offset, e)
                break
            postings = data.get("jobPostings", [])
            if not postings:
                break
            for p in postings:
                path = p.get("externalPath", "")
                if path:
                    seen.add(path)
            if offset + _PAGE_SIZE >= data.get("total", 0):
                break

    out: list[dict] = []
    for path in seen:
        info = _detail(fetcher, path)
        if not info:
            continue
        # jobReqId (e.g. "JR25238") comes from the detail payload, not the list
        # view's bulletFields — the latter is a UI-configured display bullet,
        # not documented as a stable/unique identifier.
        req_id = info.get("jobReqId") or path
        description = BeautifulSoup(info.get("jobDescription", ""), "lxml").get_text(" | ", strip=True)
        posted_at = None
        if info.get("startDate"):
            try:
                posted_at = dateparse.parse(info["startDate"])
            except (ValueError, OverflowError):
                log.debug("unparseable date %r", info["startDate"])

        out.append({
            "source": SOURCE,
            "external_id": req_id,
            "url": info.get("externalUrl") or f"{_TENANT_URL}{path}",
            "title": info.get("title", ""),
            "company": _COMPANY,
            "location": info.get("location", "Vancouver, BC"),
            "description": description,
            "posted_at": posted_at,
        })
    log.info("UBC Workday: %d postings (%d keywords searched)", len(out), len(keywords))
    return out
