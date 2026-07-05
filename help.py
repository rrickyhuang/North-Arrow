"""Prints a consolidated command reference for every script in this project.

    python help.py

Pulls each script's own docstring rather than duplicating the text here, so
this stays in sync automatically as long as a script's docstring does — if
you add a flag, update that script's docstring and this reflects it for free.
"""
from __future__ import annotations

import addjob
import commute_precise
import coverletter
import dedup
import digest
import mark
import scrape
import show

_SCRIPTS = [
    ("scrape.py", scrape, "Scrape sources, score, and store jobs. The daily driver."),
    ("show.py", show, "Read-only viewer — ranked list, detail view, filtering."),
    ("mark.py", mark, "Track application status: applied/interviewing/offer/denied/withdrawn/interested/seen."),
    ("addjob.py", addjob, "Paste in a job you found yourself (LinkedIn/Indeed/etc.), or edit one."),
    ("coverletter.py", coverletter, "Draft a cover letter for any stored job via the claude CLI."),
    ("digest.py", digest, "Build/send the ranked shortlist without re-scraping."),
    ("dedup.py", dedup, "Cross-source duplicate detection (library module; run via `scrape.py --dedup`, auto-runs after every scrape)."),
    ("commute_precise.py", commute_precise, "Optional real transit-time refinement for the digest shortlist (Google Distance Matrix)."),
]


def main() -> None:
    print("\n  JobHunter — command reference")
    print("  " + "=" * 60)
    for name, module, blurb in _SCRIPTS:
        print(f"\n  {name}")
        print(f"  {'-' * len(name)}")
        print(f"  {blurb}\n")
        doc = (module.__doc__ or "").strip("\n")
        for line in doc.splitlines():
            print(f"  {line}" if line else "")
    print("\n  " + "=" * 60 + "\n")


if __name__ == "__main__":
    main()
