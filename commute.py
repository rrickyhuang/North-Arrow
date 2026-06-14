"""Estimate transit commute from home to a job posting — free, no paid API.

Pipeline: geocode the posting's location (OpenStreetMap Nominatim) -> find the
nearest acceptable SkyTrain station -> estimate door-to-door minutes as
``walk-to-station + ride-to-home`` -> map minutes to a 0..1 score via config
buckets. Geocode results are cached on disk so re-runs don't re-hit Nominatim.

Remote roles get a flat ``remote_score``. Postings that only name a city (no
street address) are too imprecise to score on commute, so they get the neutral
``unknown_location_score``.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from geopy.distance import geodesic
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderServiceError, GeocoderTimedOut

import transit_data

_CACHE_PATH = Path(__file__).with_name(".geocode_cache.json")
_geolocator = Nominatim(user_agent="jobhunter (personal use)")
_last_call = 0.0  # Nominatim asks for <=1 req/sec; we self-throttle.


@dataclass
class CommuteResult:
    score: float
    commute_min: int | None = None
    nearest_station: str | None = None
    lat: float | None = None
    lng: float | None = None
    is_remote: bool = False


# ── geocode cache ────────────────────────────────────────────────────────────
def _load_cache() -> dict:
    if _CACHE_PATH.exists():
        try:
            return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    try:
        _CACHE_PATH.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass


_cache = _load_cache()


def _geocode(location: str) -> tuple[float, float] | None:
    global _last_call
    key = location.strip().lower()
    if key in _cache:
        v = _cache[key]
        return (v[0], v[1]) if v else None
    # Throttle to be a polite Nominatim citizen.
    wait = 1.1 - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    try:
        loc = _geolocator.geocode(location, timeout=15, country_codes="ca")
        _last_call = time.time()
    except (GeocoderTimedOut, GeocoderServiceError):
        return None  # transient; don't poison the cache
    result = (loc.latitude, loc.longitude) if loc else None
    _cache[key] = list(result) if result else None
    _save_cache(_cache)
    return result


# ── precision heuristic ──────────────────────────────────────────────────────
_STREET_RE = re.compile(r"\d{2,5}\s+\w|\b[A-Z]\d[A-Z]\s?\d[A-Z]\d\b", re.I)


def _is_precise(location: str) -> bool:
    """True if the string looks like a street address / postal code, not just a city."""
    return bool(_STREET_RE.search(location))


# ── station math ─────────────────────────────────────────────────────────────
def _nearest_station(lat: float, lng: float) -> tuple[str, float]:
    best_name, best_km = "", float("inf")
    for name, (slat, slng, _lines, _stops) in transit_data.STATIONS.items():
        km = geodesic((lat, lng), (slat, slng)).km
        if km < best_km:
            best_name, best_km = name, km
    return best_name, best_km


def _bucket_score(minutes: float, buckets: list[dict]) -> float:
    for b in buckets:
        if minutes <= b["max_min"]:
            return float(b["score"])
    return float(buckets[-1]["score"])


# ── public API ───────────────────────────────────────────────────────────────
def estimate(location: str, location_normalized: str, is_remote: bool,
             cfg: dict) -> CommuteResult:
    c = cfg["commute"]
    if is_remote or location_normalized == "Remote":
        return CommuteResult(score=c["remote_score"], is_remote=True)

    # Out of metro: a SkyTrain commute is meaningless. Score 0 — the scorer
    # disqualifies "Other" anyway. Never geocode these (avoids phantom rides
    # like a Calgary address measured to a Vancouver station).
    if location_normalized == "Other":
        return CommuteResult(score=0.0)

    if not location or not _is_precise(location):
        # City-level only ("Vancouver, BC") -> can't judge commute meaningfully.
        return CommuteResult(score=c["unknown_location_score"])

    geo = _geocode(location)
    if geo is None:
        return CommuteResult(score=c["unknown_location_score"])

    lat, lng = geo
    station, walk_km = _nearest_station(lat, lng)
    if walk_km > 10:
        # Geocoded far from any Expo/Millennium station: either a bad geocode or
        # genuinely unserved. Don't emit a huge commute — treat as unknown.
        return CommuteResult(score=c["unknown_location_score"], lat=lat, lng=lng)
    walk_min = (walk_km / c["walk_speed_kmh"]) * 60
    stops = transit_data.STATIONS[station][3]
    ride_min = stops * c["minutes_per_station"]
    total = round(walk_min + ride_min)
    score = _bucket_score(total, c["score_buckets"])
    return CommuteResult(
        score=score, commute_min=total, nearest_station=station, lat=lat, lng=lng
    )
