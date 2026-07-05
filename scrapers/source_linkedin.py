"""LinkedIn via JobSpy (python-jobspy).

LinkedIn is confirmed working without a proxy at this search's volume
(2026-07), but JobSpy's own docs warn it's the most rate-limit-sensitive board
(around page 10 on one IP) — keep results_wanted modest (see config.yaml's
jobspy.results_wanted) rather than raising it to pull more per run. See
scrapers/_jobspy_common.py for the shared plumbing.
"""
from __future__ import annotations

from scrapers._jobspy_common import run

SOURCE = "linkedin"


def fetch(cfg: dict) -> list[dict]:
    return run(cfg, site_name="linkedin", source=SOURCE, linkedin_fetch_description=True)
