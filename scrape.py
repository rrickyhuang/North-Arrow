"""CLI entry point: scrape -> normalize/parse -> commute -> store.

Scoring (Phase 3), enrichment (Phase 4), and the digest (Phase 7) hook into
``run()`` later. For now this proves real data flows end-to-end into SQLite.

Usage:
    python scrape.py --all
    python scrape.py --source indeed
"""
from __future__ import annotations

import argparse
import logging

import config
import db
import commute
from models import Job
from parsers.salary_cad import parse_salary
from parsers.role_classifier import classify_role
from parsers.org_classifier import classify_org
from parsers.normalize import normalize_location

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("scrape")

# Registry of available sources -> their fetch(cfg) callables.
from scrapers import source_indeed, source_pibc, source_csla

SOURCES = {
    "pibc": source_pibc.fetch,
    "csla": source_csla.fetch,
    "indeed": source_indeed.fetch,   # shelved: Indeed serves 403, handled gracefully
    # archinect / idealist / firm_direct land in later phases
}


def raw_to_job(raw: dict, cfg: dict) -> Job:
    """Convert a scraper's raw dict into a parsed, commute-scored Job (unscored)."""
    title = raw.get("title", "")
    company = raw.get("company", "")
    location = raw.get("location", "")
    description = raw.get("description", "")
    blob = f"{title}\n{description}"

    salary_min, salary_max, salary_raw = parse_salary(raw.get("salary_raw") or blob)
    role_type = classify_role(title, description)
    org_type, org_size = classify_org(company, description)
    loc_norm, is_remote = normalize_location(location, description)
    com = commute.estimate(location, loc_norm, is_remote, cfg)

    return Job(
        source=raw["source"],
        external_id=str(raw["external_id"]),
        url=raw.get("url", ""),
        title=title,
        company=company,
        location=location,
        location_normalized=loc_norm,
        is_remote=com.is_remote,
        location_lat=com.lat,
        location_lng=com.lng,
        nearest_station=com.nearest_station,
        commute_min=com.commute_min,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_raw=salary_raw,
        role_type=role_type,
        org_type=org_type,
        org_size=org_size,
        posted_at=raw.get("posted_at"),
        description=description,
        # commute score is stashed in the breakdown until the Phase 3 scorer runs
        score_breakdown={"commute": com.score},
    )


def run(sources: list[str], cfg: dict) -> dict:
    conn = db.connect()
    db.init_db(conn)
    stats = {"fetched": 0, "new": 0, "updated": 0}
    for name in sources:
        fetch = SOURCES.get(name)
        if not fetch:
            log.warning("unknown/unimplemented source: %s", name)
            continue
        log.info("running source: %s", name)
        try:
            raws = fetch(cfg)
        except Exception as e:  # noqa: BLE001
            log.error("source %s crashed: %s", name, e)
            continue
        for raw in raws:
            stats["fetched"] += 1
            job = raw_to_job(raw, cfg)
            is_new = db.upsert(conn, job)
            stats["new" if is_new else "updated"] += 1
    conn.close()
    return stats


def main() -> None:
    cfg = config.load_config()
    enabled = [s for s, on in cfg.get("sources", {}).items() if on]

    ap = argparse.ArgumentParser(description="Scrape and store design-field jobs.")
    ap.add_argument("--source", help="run a single source by name")
    ap.add_argument("--all", action="store_true", help="run all enabled sources")
    args = ap.parse_args()

    if args.source:
        sources = [args.source]
    elif args.all:
        sources = list(SOURCES.keys())
    else:
        sources = [s for s in enabled if s in SOURCES] or list(SOURCES.keys())

    stats = run(sources, cfg)
    log.info("done: fetched=%(fetched)d new=%(new)d updated=%(updated)d", stats)


if __name__ == "__main__":
    main()
