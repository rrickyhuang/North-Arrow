"""Indeed.ca via JobSpy (python-jobspy).

A prior hand-rolled requests/BeautifulSoup scraper got Cloudflare-walled here;
JobSpy's own request handling gets through cleanly as of 2026-07 with no proxy
needed for this search's volume. See scrapers/_jobspy_common.py for the shared
plumbing and scrapers/source_linkedin.py for the other JobSpy-backed source.
"""
from __future__ import annotations

from scrapers._jobspy_common import run

SOURCE = "indeed"


def fetch(cfg: dict) -> list[dict]:
    return run(cfg, site_name="indeed", source=SOURCE)
