"""Manually add or edit a job you found yourself (LinkedIn, Indeed, a firm
site not scraped, etc.) into the same pipeline as scraped postings: parsed,
commute-scored, LLM-enriched, and ranked alongside everything else in
`show.py`.

Usage:
    python addjob.py                       add a new manual job
    python addjob.py --edit <row-# or id>  edit an existing manual job
"""
from __future__ import annotations

import sys
import time

import config
import db
from scrape import add_job

SOURCE = "manual"


def _prompt(label: str) -> str:
    return input(f"{label}: ").strip()


def _prompt_default(label: str, current: str) -> str:
    val = input(f"{label} [{current or '—'}]: ").strip()
    return val if val else current


_END_SENTINEL = "END"


def _prompt_multiline(label: str) -> str:
    # A blank-line terminator breaks on real job postings, which almost always
    # have blank lines between paragraphs — an explicit sentinel is unambiguous
    # regardless of how the pasted text is formatted.
    print(f"{label} (paste it, then type {_END_SENTINEL} on its own line and press Enter):")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == _END_SENTINEL:
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _prompt_multiline_default(label: str, current: str) -> str:
    preview = current[:80] + "…" if current and len(current) > 80 else (current or "—")
    print(f"{label} — current: {preview}")
    choice = input("  Keep as-is? [Y/n]: ").strip().lower()
    if choice in ("", "y", "yes"):
        return current
    return _prompt_multiline(label)


def _resolve_job(conn, target: str):
    if target.isdigit():
        jobs = db.query(conn, include_dismissed=True, include_duplicates=True,
                        order_by="score DESC")
        idx = int(target)
        if idx < len(jobs):
            return jobs[idx]
    return db.get(conn, target)


def _save(conn, raw: dict, cfg: dict, *, force_enrich: bool) -> None:
    job = add_job(conn, raw, cfg, force_enrich=force_enrich)

    print(f"\n  Saved. score={job.score:.2f}"
          f"{'  DISQUALIFIED: ' + job.disqualifier if job.disqualifier else ''}")
    print(f"  id={job.id}")
    print(f"  View it:    python show.py {job.id}")
    print(f"  Cover letter: python coverletter.py {job.id}\n")


def add(conn, cfg: dict) -> None:
    print("\n  Add a manually-found job\n  " + "-" * 40)
    title = _prompt("Title")
    company = _prompt("Company")
    url = _prompt("URL (where you found it)")
    location = _prompt("Location (city, or 'Remote')")
    description = _prompt_multiline("Description")

    if not title or not description:
        print("\n  Title and description are required — aborting.\n")
        sys.exit(1)

    raw = {
        "source": SOURCE,
        # Same URL pasted twice updates the existing row instead of duplicating.
        "external_id": url or f"{title}|{company}|{int(time.time())}",
        "url": url,
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "posted_at": None,
    }
    _save(conn, raw, cfg, force_enrich=False)


def edit(conn, cfg: dict, target: str) -> None:
    job = _resolve_job(conn, target)
    if not job:
        print(f"\n  No job found for {target!r}\n")
        sys.exit(1)
    if job.source != SOURCE:
        print(f"\n  Job {job.id} came from source={job.source!r}, not a manual "
              f"entry — editing it here would just be overwritten by the next "
              f"scrape of that source. Skipping.\n")
        sys.exit(1)

    print(f"\n  Editing: {job.title} @ {job.company}\n  " + "-" * 40)
    title = _prompt_default("Title", job.title)
    company = _prompt_default("Company", job.company)
    location = _prompt_default("Location (city, or 'Remote')", job.location)
    description = _prompt_multiline_default("Description", job.description)

    raw = {
        "source": SOURCE,
        "external_id": job.external_id,  # identity stays fixed — URL isn't editable here
        "url": job.url,
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "posted_at": None,
    }
    # Edits can change fit-relevant facts, so re-run enrichment rather than
    # reusing the stale read from before the edit.
    _save(conn, raw, cfg, force_enrich=True)


def main() -> None:
    args = sys.argv[1:]
    cfg = config.load_config()
    conn = db.connect()
    db.init_db(conn)
    try:
        if args and args[0] == "--edit":
            if len(args) < 2:
                print("\n  Usage: python addjob.py --edit <row-# or id>\n")
                sys.exit(1)
            edit(conn, cfg, args[1])
        else:
            add(conn, cfg)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
