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
import scorer
import enrichment
from models import Job

_ENRICH_FIELDS = (
    "has_design_autonomy", "has_mixed_role", "has_variety", "is_admin_heavy",
    "is_drafting_only", "is_hierarchical", "skills_leverage", "autonomy_evidence",
    "fit_summary", "seniority", "required_years", "required_credentials",
    "qualification", "missing_requirements",
)
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


def _should_enrich(job: Job, cfg: dict) -> bool:
    """Skip the API call for jobs that will be disqualified regardless."""
    if not cfg.get("enrichment", {}).get("enabled"):
        return False
    if job.role_type in cfg.get("disqualifiers", {}).get("role_types", []):
        return False
    if job.location_normalized == "Other" and not job.is_remote:
        return False
    return True


def _apply_enrichment(job: Job, data: dict) -> None:
    for k in _ENRICH_FIELDS:
        if k in ("skills_leverage", "required_credentials", "missing_requirements"):
            if data.get(k):
                setattr(job, k, data[k])
        elif k in data:
            setattr(job, k, data[k])
    # The LLM read the full description, so prefer its guesses over keyword ones.
    if data.get("role_type_guess"):
        job.role_type = data["role_type_guess"]
    if data.get("org_type_guess"):
        job.org_type = data["org_type_guess"]
    if data.get("org_size_guess"):
        job.org_size = data["org_size_guess"]
    job.enriched = True


def _maybe_enrich(conn, job: Job, cfg: dict, stats: dict) -> None:
    existing = db.get(conn, job.id)
    if existing and existing.enriched:
        # Reuse prior enrichment — daily re-runs only pay for genuinely new jobs.
        for k in (*_ENRICH_FIELDS, "role_type", "org_type", "org_size"):
            setattr(job, k, getattr(existing, k))
        job.enriched = True
        return
    if not _should_enrich(job, cfg):
        return
    data = enrichment.enrich(job, cfg)
    if data:
        _apply_enrichment(job, data)
        stats["enriched"] += 1


def run(sources: list[str], cfg: dict) -> dict:
    conn = db.connect()
    db.init_db(conn)
    stats = {"fetched": 0, "new": 0, "updated": 0, "enriched": 0}
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
            _maybe_enrich(conn, job, cfg, stats)
            job.score, job.score_breakdown, job.disqualifier = scorer.score_job(job, cfg)
            is_new = db.upsert(conn, job)
            stats["new" if is_new else "updated"] += 1
    conn.close()
    return stats


def rescore(cfg: dict) -> int:
    """Re-score every stored job in place (no re-scraping). Use after tuning
    config weights or running enrichment."""
    conn = db.connect()
    db.init_db(conn)
    jobs = db.query(conn, include_dismissed=True)
    for job in jobs:
        score, breakdown, disq = scorer.score_job(job, cfg)
        db.update_score(conn, job.id, score, breakdown, disq)
    conn.close()
    return len(jobs)


def main() -> None:
    cfg = config.load_config()
    enabled = [s for s, on in cfg.get("sources", {}).items() if on]

    ap = argparse.ArgumentParser(description="Scrape and store design-field jobs.")
    ap.add_argument("--source", help="run a single source by name")
    ap.add_argument("--all", action="store_true", help="run all enabled sources")
    ap.add_argument("--rescore", action="store_true",
                    help="re-score stored jobs without scraping")
    ap.add_argument("--digest", action="store_true",
                    help="build + deliver the digest after scraping")
    args = ap.parse_args()

    if args.rescore:
        n = rescore(cfg)
        log.info("rescored %d jobs", n)
        return

    if args.source:
        sources = [args.source]
    elif args.all:
        sources = list(SOURCES.keys())
    else:
        sources = [s for s in enabled if s in SOURCES] or list(SOURCES.keys())

    stats = run(sources, cfg)
    log.info("done: fetched=%(fetched)d new=%(new)d updated=%(updated)d "
             "enriched=%(enriched)d", stats)

    if args.digest:
        import digest
        digest.run(cfg)


if __name__ == "__main__":
    main()
