"""Shared test fixtures. Run with: pytest (see pytest.ini)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import Job

_counter = iter(range(1_000_000))


def make_job(**overrides) -> Job:
    """A minimally-valid Job with a unique id, overridable field-by-field."""
    n = next(_counter)
    defaults = dict(
        source=overrides.pop("source", "testsrc"),
        external_id=overrides.pop("external_id", str(n)),
        url=overrides.pop("url", f"https://example.com/{n}"),
        title="Urban Designer",
        company="Example Studio",
        location="Vancouver, BC",
        location_normalized="Vancouver",
        role_type="urban_design",
        employment_type="full_time",
        score=0.5,
        score_breakdown={"commute": 0.8},
    )
    defaults.update(overrides)
    return Job(**defaults)


@pytest.fixture
def job_factory():
    return make_job


def base_config() -> dict:
    """A minimal but fully-valid config dict, matching config.example.yaml's
    shape closely enough for scorer.py/commute.py/config._validate to accept."""
    return {
        "search_queries": {"keywords": ["urban designer"], "location": "Vancouver, BC"},
        "commute": {
            "home_station": "Commercial-Broadway",
            "acceptable_lines": ["Expo Line"],
            "walk_speed_kmh": 4.8,
            "minutes_per_station": 2.2,
            "interchange_penalty_min": 4,
            "score_buckets": [
                {"max_min": 20, "score": 1.0},
                {"max_min": 35, "score": 0.85},
                {"max_min": 50, "score": 0.6},
                {"max_min": 70, "score": 0.35},
                {"max_min": 9999, "score": 0.15},
            ],
            "remote_score": 0.9,
            "unknown_location_score": 0.55,
            "google_maps": {"enabled": False, "arrival_weekday_hour": 9, "correction_margin": 0.05},
        },
        "scoring": {
            "weights": {
                "commute": 0.28, "role_type": 0.18, "design_autonomy": 0.18,
                "mixed_role": 0.16, "salary": 0.12, "role_quality": 0.08,
            },
            "preferences": {
                "salary_floor": 68000, "salary_target": 85000,
                "remote_ok": True, "hybrid_ok": True,
            },
            "target_roles": ["urban_design"],
            "target_firms": [],
            "target_firm_bonus": 0.10,
            "skills_bonus": {"terms": [], "per_hit": 0.02, "cap": 0.08},
            "penalties": {
                "admin_heavy": 0.4, "role_type_admin": 0.4, "role_type_drafting_only": 0.4,
                "non_full_time": 0.5,
                "qualification": {"stretch": 0.85, "reach": 0.5, "wrong_field": 0.15},
            },
        },
        "disqualifiers": {"kill_if_outside_metro_and_onsite": True},
        "dedup": {
            "enabled": True,
            "title_similarity_threshold": 88,
            "company_similarity_threshold": 80,
            "description_similarity_threshold": 85,
            "source_priority": ["vancouver_gov", "pibc", "jobbank", "bcjobs", "indeed"],
        },
        "delivery": {"min_score_for_digest": 0.45, "max_jobs_in_digest": 25},
        "enrichment": {"model": "claude-haiku-4-5", "max_description_chars": 8000, "enabled": False},
    }


@pytest.fixture
def cfg():
    return base_config()
