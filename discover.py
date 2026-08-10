"""CLI companion to `.claude/skills/discover-jobs`: takes one raw posting found
via open-web browsing/search and runs it through the same pipeline as a manual
add (parse -> commute -> enrich -> score -> store -> dedup), so the skill never
touches the DB directly.

Usage:
    python discover.py add --json-file <path>

The JSON file holds one object with the same shape addjob.py builds:
    {"title": ..., "company": ..., "url": ..., "location": ...,
     "description": ..., "external_id": <optional, defaults to url>}
"""
from __future__ import annotations

import argparse
import json
import sys

import config
import db
from models import Job
from scrape import add_job

SOURCE = "web_discovery"


def add(conn, cfg: dict, json_file: str) -> None:
    with open(json_file, encoding="utf-8") as f:
        posting = json.load(f)

    title = posting.get("title", "")
    description = posting.get("description", "")
    url = posting.get("url", "")
    if not title or not description:
        print("\n  title and description are required — aborting.\n")
        sys.exit(1)

    raw = {
        "source": SOURCE,
        "external_id": posting.get("external_id") or url,
        "url": url,
        "title": title,
        "company": posting.get("company", ""),
        "location": posting.get("location", ""),
        "description": description,
        "posted_at": None,
    }
    if not raw["external_id"]:
        print("\n  external_id or url is required — aborting.\n")
        sys.exit(1)

    was_new = db.get(conn, Job.make_id(SOURCE, raw["external_id"])) is None
    job = add_job(conn, raw, cfg, force_enrich=False)
    # add_job's dedup.run() re-queries the DB fresh, so the in-memory job above
    # won't reflect it — re-fetch to report accurate duplicate status.
    stored = db.get(conn, job.id)

    print(f"\n  {'Added' if was_new else 'Updated'}. score={stored.score:.2f}"
          f"{'  DISQUALIFIED: ' + stored.disqualifier if stored.disqualifier else ''}")
    print(f"  id={stored.id}")
    print(f"  duplicate_of={stored.duplicate_of or '—'}\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    add_parser = sub.add_parser("add", help="Add one posting found via open-web discovery")
    add_parser.add_argument("--json-file", required=True)
    args = parser.parse_args()

    cfg = config.load_config()
    conn = db.connect()
    db.init_db(conn)
    try:
        if args.cmd == "add":
            add(conn, cfg, args.json_file)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
