"""Core data model for a job posting — the single `Job` dataclass that defines
what a posting is throughout the pipeline. Holds no logic, just the shape of the
data: db.py persists these fields as SQLite columns, scrape.py builds Job objects,
and show.py/digest.py/html_render.py read them.

Notable, search-specific fields:
  - org-type is informational only (no scoring penalty).
  - Commute is two-tier: `commute_min` is a free, deterministic estimate used in
    scoring; `commute_min_precise` is the real one-way transit time from the
    Google Distance Matrix API, fetched lazily by commute_precise.py only for
    jobs that reach the digest shortlist, and never fed back into scoring.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


@dataclass
class Job:
    # ── Identity ─────────────────────────────────────────────────────────────
    source: str                      # "indeed" | "archinect" | "pibc" | ...
    external_id: str                 # source's own id, or a hash of the url
    url: str
    id: str = ""                     # our stable id; derived in __post_init__

    # ── Core ─────────────────────────────────────────────────────────────────
    title: str = ""
    company: str = ""
    location: str = ""               # raw location string from the posting
    location_normalized: str = "Unknown"  # Vancouver | Remote | Hybrid | Other | Unknown

    # ── Commute (from Commercial-Broadway, Expo/Millennium lines) ────────────
    location_lat: float | None = None
    location_lng: float | None = None
    nearest_station: str | None = None
    commute_min: int | None = None   # estimated one-way door-to-door minutes
    # Real transit time from Google Distance Matrix (minutes), fetched lazily —
    # only for jobs that actually reach the digest shortlist, never used in
    # scoring (stays free/deterministic). None until fetched; see commute_precise.py.
    commute_min_precise: int | None = None
    is_remote: bool | None = None

    # ── Compensation (CAD) ───────────────────────────────────────────────────
    salary_min: int | None = None
    salary_max: int | None = None
    salary_raw: str | None = None

    # ── Role classification ──────────────────────────────────────────────────
    role_type: str | None = None
    # urban_design | landscape_arch | planning | civic_innovation
    # | architecture | admin | pm_only | drafting_only | unknown

    # ── Employment type (soft-penalized, not disqualifying — see scorer.py) ──
    employment_type: str | None = None
    # full_time | part_time | casual | on_call | seasonal | temporary | unknown

    # ── Org classification (informational only — no scoring penalty) ─────────
    org_type: str | None = None
    org_size: str | None = None      # small | mid | large | unknown

    # ── Role quality signals (LLM-enriched) ──────────────────────────────────
    has_design_autonomy: bool | None = None
    has_mixed_role: bool | None = None
    has_variety: bool | None = None
    is_admin_heavy: bool | None = None
    is_drafting_only: bool | None = None
    is_hierarchical: bool | None = None
    skills_leverage: list[str] = field(default_factory=list)
    autonomy_evidence: str | None = None
    fit_summary: str | None = None

    # ── Qualification (display-only; does NOT affect score or ranking) ────────
    seniority: str | None = None              # entry|junior|intermediate|senior|director
    required_years: int | None = None
    required_credentials: list[str] = field(default_factory=list)
    qualification: str | None = None          # qualified|stretch|reach|overqualified
    missing_requirements: list[str] = field(default_factory=list)

    # ── Meta ─────────────────────────────────────────────────────────────────
    posted_at: datetime | None = None
    scraped_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    description: str = ""

    # ── Computed ─────────────────────────────────────────────────────────────
    score: float = 0.0
    score_breakdown: dict = field(default_factory=dict)
    disqualifier: str | None = None

    # ── User/workflow state ──────────────────────────────────────────────────
    enriched: bool = False
    is_new: bool = True
    seen: bool = False
    saved: bool = False          # "interested" / shortlisted
    dismissed: bool = False      # "not interested"
    # Application pipeline: None (not applied) -> applied -> interviewing ->
    # offer | denied | withdrawn. See mark.py for the CLI to set these.
    stage: str | None = None
    stage_at: datetime | None = None   # when the current stage was set
    # Free-text application notes (recruiter, dates, follow-ups, interview prep).
    # User state — never overwritten on re-scrape. Editable from the web cockpit.
    notes: str = ""

    # ── Cross-source dedup ───────────────────────────────────────────────────
    # Set by dedup.py when this job is judged a re-post of another job (e.g. the
    # same posting scraped via an aggregator AND its originating org's own
    # board). Holds the *other* job's id; the duplicate is hidden by default
    # everywhere the "live" list shows up, same treatment as `dismissed`.
    duplicate_of: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            self.id = self.make_id(self.source, self.external_id)

    @staticmethod
    def make_id(source: str, external_id: str) -> str:
        return hashlib.sha1(f"{source}:{external_id}".encode("utf-8")).hexdigest()[:16]

    def to_row(self) -> dict:
        """Flatten to a dict of SQLite-storable scalars (handled by db.py)."""
        return asdict(self)
