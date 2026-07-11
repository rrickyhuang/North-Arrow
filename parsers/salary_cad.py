"""Extract an annual CAD salary range from free text.

Assumes CAD unless USD/GBP/EUR is explicitly signalled. Hourly rates are
annualized at 2080 h/yr. Returns (min, max, raw_match) where any field may be
None. Conservative: only emits numbers that land in a plausible comp range.
"""
from __future__ import annotations

import re

_NON_CAD = re.compile(r"\b(USD|US\$|GBP|EUR|AUD)\b|[£€]", re.I)

# Each pattern yields a (low, high?) pair. Order matters: most specific first.
_RANGE_PATTERNS = [
    # $75K - $95K   /  $75k–95k
    (re.compile(r"\$?\s*(\d{2,3})\s*[Kk]\s*[-–to]+\s*\$?\s*(\d{2,3})\s*[Kk]"), 1000),
    # $75,000 - $95,000
    (re.compile(r"\$?\s*(\d{2,3}),000\s*[-–to]+\s*\$?\s*(\d{2,3}),000"), 1000),
]
_SINGLE_PATTERNS = [
    (re.compile(r"\$?\s*(\d{2,3})\s*[Kk]\+?\b"), 1000),          # $80K+
    (re.compile(r"\$?\s*(\d{2,3}),000\b"), 1000),                 # $80,000
]
# Hourly: $35/hr, $35.50 per hour, $35-$45/hour
_HOURLY_RANGE = re.compile(r"\$\s*(\d{1,3}(?:\.\d{1,2})?)\s*[-–to]+\s*\$?\s*(\d{1,3}(?:\.\d{1,2})?)\s*(?:/|\s*per\s*)\s*h", re.I)
_HOURLY_SINGLE = re.compile(r"\$\s*(\d{1,3}(?:\.\d{1,2})?)\s*(?:/|\s*per\s*)\s*h", re.I)

_HOURS_PER_YEAR = 2080
_MIN_PLAUSIBLE = 25_000
_MAX_PLAUSIBLE = 400_000


def _plausible(n: int | None) -> int | None:
    if n is None:
        return None
    return n if _MIN_PLAUSIBLE <= n <= _MAX_PLAUSIBLE else None


def parse_salary(text: str) -> tuple[int | None, int | None, str | None]:
    if not text:
        return None, None, None
    if _NON_CAD.search(text):
        # Foreign currency stated — don't pretend it's CAD; skip numeric parse.
        return None, None, None

    # Hourly first (so "$40/hr" isn't misread as an annual "$40K"-ish number).
    m = _HOURLY_RANGE.search(text)
    if m:
        lo = _plausible(int(float(m.group(1)) * _HOURS_PER_YEAR))
        hi = _plausible(int(float(m.group(2)) * _HOURS_PER_YEAR))
        if lo or hi:
            return lo, hi, m.group(0).strip()
    m = _HOURLY_SINGLE.search(text)
    if m:
        val = _plausible(int(float(m.group(1)) * _HOURS_PER_YEAR))
        if val:
            return val, None, m.group(0).strip()

    for pat, mult in _RANGE_PATTERNS:
        m = pat.search(text)
        if m:
            lo = _plausible(int(m.group(1)) * mult)
            hi = _plausible(int(m.group(2)) * mult)
            if lo or hi:
                return lo, hi, m.group(0).strip()

    for pat, mult in _SINGLE_PATTERNS:
        m = pat.search(text)
        if m:
            val = _plausible(int(m.group(1)) * mult)
            if val:
                return val, None, m.group(0).strip()

    return None, None, None
