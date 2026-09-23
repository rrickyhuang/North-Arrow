"""Loads runtime configuration and secrets.

Personal settings live in `config.yaml` (gitignored); secrets live in `.env`
(gitignored). Both have committed templates (`config.example.yaml`, `.env.example`)
so a fresh clone knows what to fill in. Neither real file is ever committed.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.yaml"
ENV_PATH = ROOT / ".env"

load_dotenv(ENV_PATH)


class ConfigError(Exception):
    """config.yaml is missing or malformed. Raised eagerly at load time so a
    typo surfaces immediately instead of as an opaque KeyError deep into a run."""


# Dotted paths every downstream module indexes directly (cfg["a"]["b"], not
# cfg.get(...)) and would otherwise KeyError on far from where the mistake was
# made. Keep in sync with direct cfg[...] accesses across the codebase.
_REQUIRED_PATHS = [
    "search_queries",
    "commute",
    "commute.home_station",
    "commute.score_buckets",
    "commute.remote_score",
    "commute.unknown_location_score",
    "commute.walk_speed_kmh",
    "commute.minutes_per_station",
    "commute.google_maps",
    "scoring",
    "scoring.weights",
    "scoring.preferences",
    "scoring.preferences.salary_floor",
    "scoring.preferences.salary_target",
    "delivery",
    "delivery.min_score_for_digest",
    "enrichment",
]

# scorer.py's weighted sum (`breakdown[k] * weights[k] for k in weights`) only
# ever populates these six breakdown components — any other key under
# scoring.weights KeyErrors mid-scoring instead of at load time.
_KNOWN_WEIGHT_KEYS = {"commute", "role_type", "design_autonomy", "mixed_role", "salary", "role_quality"}


def _get_path(cfg: dict, path: str):
    node = cfg
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(path)
        node = node[part]
    return node


def _validate(cfg: dict) -> None:
    if not isinstance(cfg, dict):
        raise ConfigError("config.yaml is empty or not a mapping — check it against config.example.yaml")

    problems: list[str] = []
    for path in _REQUIRED_PATHS:
        try:
            _get_path(cfg, path)
        except KeyError:
            problems.append(f"missing {path!r}")

    try:
        weights = _get_path(cfg, "scoring.weights")
        if isinstance(weights, dict):
            unknown = sorted(set(weights) - _KNOWN_WEIGHT_KEYS)
            if unknown:
                problems.append(
                    f"scoring.weights has unrecognized key(s) {unknown} — "
                    f"expected a subset of {sorted(_KNOWN_WEIGHT_KEYS)}"
                )
    except KeyError:
        pass  # already reported above

    try:
        buckets = _get_path(cfg, "commute.score_buckets")
        if not isinstance(buckets, list) or not buckets:
            problems.append("commute.score_buckets must be a non-empty list")
        else:
            for i, b in enumerate(buckets):
                if not isinstance(b, dict) or "max_min" not in b or "score" not in b:
                    problems.append(f"commute.score_buckets[{i}] must have 'max_min' and 'score'")
    except KeyError:
        pass  # already reported above

    if problems:
        raise ConfigError(
            "config.yaml is missing or malformed:\n  - " + "\n  - ".join(problems)
            + "\nCompare against config.example.yaml."
        )


@lru_cache(maxsize=1)
def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            "config.yaml not found. Copy config.example.yaml to config.yaml "
            "and edit it for your search:\n"
            "    copy config.example.yaml config.yaml   (Windows)\n"
            "    cp config.example.yaml config.yaml      (macOS/Linux)"
        )
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    _validate(cfg)
    _prepend_resume_header(cfg)
    return cfg


def _prepend_resume_header(cfg: dict) -> None:
    """profile.resume is prose fed into AI prompts; letterhead is structured
    data for the rendered PDF header. Rather than keeping your name/contact
    info duplicated in both, letterhead is the single source of truth —
    this builds the same header line resume text used to have written out
    literally, and prepends it in memory. Does nothing if either section
    (or profile.resume) is missing."""
    letterhead = cfg.get("letterhead")
    profile = cfg.get("profile")
    if not letterhead or not profile or not profile.get("resume"):
        return
    name_line = " ".join(
        v for v in (letterhead.get("name", "").upper(), letterhead.get("pronouns")) if v)
    contact_line = " · ".join(
        v for v in (letterhead.get(k) for k in ("location", "phone", "email", "website")) if v)
    header = "\n\n".join(v for v in (name_line, contact_line) if v)
    if header:
        profile["resume"] = f"{header}\n\n{profile['resume'].strip()}"


def env(key: str, default: str | None = None, *, required: bool = False) -> str | None:
    val = os.getenv(key, default)
    if required and not val:
        raise RuntimeError(
            f"Missing required environment variable {key!r}. "
            f"Copy .env.example to .env and fill it in."
        )
    return val
