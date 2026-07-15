"""Optional real transit-time refinement via Google's Distance Matrix API.

    python commute_precise.py     refine every digest-shortlist job missing one

commute.py's free Nominatim+bucket estimate stays the single source of truth
for the persisted `score` column and for ranking everywhere outside a single
digest run, so nothing here ever depends on a paid API being configured. For
most shortlisted jobs this only adds a more accurate number *display*
alongside the free estimate. The one exception: digest.py's `select()` also
uses this to re-score jobs whose free-estimate score lands within
`google_maps.correction_margin` of the digest cutoff, so a coarse bucket
boundary can't wrongly include/exclude a near-miss job in that day's digest
(see digest.py's `_correct_score()`) — that correction is in-memory for the
digest only and is never written back to the DB. Only pays for routing
(mode=transit), not geocoding — both endpoints are already known lat/lng from
the free pipeline.

Silently does nothing if `commute.google_maps.enabled` is off in config.yaml
or `GOOGLE_MAPS_API_KEY` isn't set in `.env` — everything else works fine
without this configured.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import requests

import commute
import config
import db
import logutil
import transit_data
from models import Job

log = logging.getLogger("commute_precise")

_API_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"


def _next_weekday_departure(hour: int) -> int:
    """Unix timestamp for the next weekday (Mon-Fri) at the given hour —
    Google's transit routing needs a real future timestamp to pick an
    actual scheduled trip rather than a generic average."""
    now = datetime.now()
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:  # Sat=5, Sun=6
        candidate += timedelta(days=1)
    return int(candidate.timestamp())


def _enabled(cfg: dict) -> bool:
    return bool(cfg.get("commute", {}).get("google_maps", {}).get("enabled")
                and config.env("GOOGLE_MAPS_API_KEY"))


def _home_coords(cfg: dict) -> tuple[float | None, float | None]:
    """Prefer an exact home address (real door-to-door routing) over the home
    station's coords (which silently assumes you're already standing on the
    platform). Falls back to the station if no address is configured or it
    fails to geocode. Geocoded once and cached on disk, same as job postings."""
    address = cfg["commute"].get("home_address")
    if address:
        geo = commute._geocode(address)
        if geo:
            return geo
        log.warning("could not geocode commute.home_address %r — falling back "
                    "to home_station coords", address)
    home_station = cfg["commute"]["home_station"]
    return transit_data.STATIONS[home_station][:2]


def refine(job: Job, cfg: dict) -> int | None:
    """Real one-way transit minutes for a single job, or None if unavailable
    (feature disabled, no API key, remote/unlocated job, or the API call
    itself failed/returned no route)."""
    if not _enabled(cfg):
        return None
    if job.is_remote or job.location_lat is None or job.location_lng is None:
        return None
    if job.commute_min is None:
        # commute.py's estimate() geocoded this address but still fell back to
        # unknown_location_score (e.g. >10km from any station — bad geocode or
        # genuinely unserved; see commute.estimate's walk_km guard). Don't let
        # a real Google route silently launder an address the free estimate
        # itself refused to trust.
        return None

    home_lat, home_lng = _home_coords(cfg)
    if home_lat is None:
        return None
    arrival_hour = cfg["commute"]["google_maps"].get("arrival_weekday_hour", 9)

    params = {
        "origins": f"{home_lat},{home_lng}",
        "destinations": f"{job.location_lat},{job.location_lng}",
        "mode": "transit",
        "arrival_time": _next_weekday_departure(arrival_hour),
        "key": config.env("GOOGLE_MAPS_API_KEY"),
    }
    try:
        resp = requests.get(_API_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        log.warning("Distance Matrix call failed for %s: %s%s", job.id, type(e).__name__,
                    f" (HTTP {status})" if status else "")
        return None

    if data.get("status") != "OK":
        log.warning("Distance Matrix status %s for %s", data.get("status"), job.id)
        return None
    try:
        element = data["rows"][0]["elements"][0]
    except (KeyError, IndexError):
        log.warning("Malformed Distance Matrix response for %s", job.id)
        return None
    if element.get("status") != "OK":
        return None  # e.g. ZERO_RESULTS — no transit route found
    return round(element["duration"]["value"] / 60)


def refine_missing(conn, jobs: list[Job], cfg: dict) -> int:
    """Refine every job in the list that doesn't already have a cached precise
    time. Returns how many were newly fetched. Safe to call with the feature
    disabled — refine() just returns None for each and nothing is written."""
    if not _enabled(cfg):
        return 0
    n = 0
    for job in jobs:
        if job.commute_min_precise is not None:
            continue
        minutes = refine(job, cfg)
        if minutes is not None:
            db.set_precise_commute(conn, job.id, minutes)
            job.commute_min_precise = minutes
            n += 1
    return n


def main() -> None:
    logutil.setup_logging()
    cfg = config.load_config()
    if not _enabled(cfg):
        log.warning("commute.google_maps.enabled is false or GOOGLE_MAPS_API_KEY "
                    "is unset in .env — nothing to do.")
        return
    conn = db.connect()
    db.init_db(conn)
    import digest
    primary, near, _tracked = digest.select(conn, cfg, refine=False)
    n = refine_missing(conn, primary + near, cfg)
    conn.close()
    log.info("refined %d job(s) with real transit time", n)


if __name__ == "__main__":
    main()
