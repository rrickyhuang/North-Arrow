"""Shared HTTP plumbing for scrapers: a polite session, throttling, retries,
and crude bot-wall detection.

Each source module exposes ``fetch(cfg) -> list[dict]`` returning raw posting
dicts with at least: source, external_id, url, title, company, location,
description. Optional: posted_at (datetime), salary_raw.
"""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger("scrapers")

# A realistic desktop UA. Not an attempt to evade — just to avoid being treated
# as a generic bot by servers that 403 the python-requests default UA.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-CA,en;q=0.9",
    "Connection": "keep-alive",
}

# Markers that indicate we hit a bot wall / CAPTCHA rather than real content.
_BLOCK_MARKERS = (
    "captcha", "verify you are human", "are you a robot",
    "px-captcha", "cf-challenge", "unusual traffic", "access denied",
)


class BlockedError(RuntimeError):
    """Raised when a source appears to be serving a bot wall."""


class Fetcher:
    def __init__(self, *, min_interval: float = 2.0, retries: int = 3,
                 timeout: int = 20):
        self.session = requests.Session()
        self.session.headers.update(_HEADERS)
        self.min_interval = min_interval
        self.retries = retries
        self.timeout = timeout
        self._last = 0.0

    def _throttle(self) -> None:
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)

    def post_json(self, url: str, json_body: dict) -> dict:
        """POST a JSON body with throttle + backoff, returning the parsed JSON
        response. For APIs like Workday's ``/wday/cxs/...`` search endpoint
        that don't accept a plain GET."""
        last_exc: Exception | None = None
        for attempt in range(1, self.retries + 1):
            self._throttle()
            try:
                resp = self.session.post(url, json=json_body, timeout=self.timeout)
                self._last = time.time()
                if resp.status_code in (403, 429) or resp.status_code >= 500:
                    last_exc = requests.HTTPError(f"{resp.status_code} for {resp.url}")
                    log.warning("attempt %d: HTTP %d for %s", attempt,
                                resp.status_code, resp.url)
                    time.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as e:
                last_exc = e
                log.warning("attempt %d failed for POST %s: %s", attempt, url, e)
                time.sleep(2 ** attempt)
        raise last_exc or requests.HTTPError(f"failed to POST {url}")

    def get(self, url: str, params: dict | None = None) -> str:
        """GET with throttle + backoff. Raises BlockedError on a detected wall,
        or requests.HTTPError after exhausting retries."""
        last_exc: Exception | None = None
        for attempt in range(1, self.retries + 1):
            self._throttle()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                self._last = time.time()
                if resp.status_code in (403, 429) or resp.status_code >= 500:
                    last_exc = requests.HTTPError(f"{resp.status_code} for {resp.url}")
                    log.warning("attempt %d: HTTP %d for %s", attempt,
                                resp.status_code, resp.url)
                    time.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                low = resp.text[:6000].lower()
                if any(m in low for m in _BLOCK_MARKERS):
                    raise BlockedError(f"bot wall detected at {resp.url}")
                return resp.text
            except requests.RequestException as e:
                last_exc = e
                log.warning("attempt %d failed for %s: %s", attempt, url, e)
                time.sleep(2 ** attempt)
        raise last_exc or requests.HTTPError(f"failed to GET {url}")
