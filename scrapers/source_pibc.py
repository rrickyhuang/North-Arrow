"""Planning Institute of BC job board — https://www.pibc.bc.ca/all-jobs

Server-rendered, clean. Listing cards live in ``.content_job1`` with the title
in ``a.title_c`` and labelled fields ("Employer/Organization", "Posted") in the
card text. We fetch each detail page for the full description (needed for salary
parsing and LLM enrichment). Hyper-targeted to BC/Yukon planning — the bullseye
source for the planning side of this search.
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup
from dateutil import parser as dateparse

from scrapers.base import Fetcher

log = logging.getLogger("scrapers.pibc")

SOURCE = "pibc"
_BASE = "https://www.pibc.bc.ca"
_LIST = f"{_BASE}/all-jobs"


def _field(text: str, label: str) -> str | None:
    m = re.search(rf"{label}\s*:?\s*\|?\s*([^|]+?)(?:\s*\||$)", text)
    return m.group(1).strip() if m else None


def _detail(fetcher: Fetcher, url: str) -> str:
    try:
        html = fetcher.get(url)
    except Exception as e:  # noqa: BLE001
        log.debug("PIBC detail fetch failed for %s: %s", url, e)
        return ""
    soup = BeautifulSoup(html, "lxml")
    main = soup.select_one("main, #content, article") or soup
    return main.get_text(" ", strip=True)


def fetch(cfg: dict) -> list[dict]:
    fetcher = Fetcher(min_interval=2.0)
    try:
        html = fetcher.get(_LIST)
    except Exception as e:  # noqa: BLE001
        log.warning("PIBC list fetch failed: %s", e)
        return []

    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    for card in soup.select(".content_job1"):
        # The styled a.title_c anchor is empty; the title text is in the sibling
        # job link (the second anchor, which carries hreflang).
        anchors = [a for a in card.select("a[href*='/jobs/']")
                   if "/jobs/add-a-job" not in a.get("href", "")]
        link = next((a for a in anchors if a.get_text(strip=True)), None)
        if not link:
            continue
        title = link.get_text(strip=True)
        href = link["href"]
        url = href if href.startswith("http") else _BASE + href
        ext = href.rstrip("/").split("/")[-1]

        card_text = card.get_text(" | ", strip=True)
        company = _field(card_text, "Employer/Organization") or ""
        posted_raw = _field(card_text, "Posted")
        posted_at = None
        if posted_raw:
            try:
                posted_at = dateparse.parse(posted_raw, fuzzy=True)
            except (ValueError, OverflowError):
                log.debug("unparseable date %r", posted_raw)

        description = _detail(fetcher, url) or card_text
        # Location: PIBC rarely gives a street address; fall back to the employer
        # (e.g. "City of Richmond"), which the normalizer maps to metro/Other.
        location = company

        out.append({
            "source": SOURCE,
            "external_id": ext,
            "url": url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "posted_at": posted_at,
        })
    log.info("PIBC: %d postings", len(out))
    return out
