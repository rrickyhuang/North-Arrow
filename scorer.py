"""Weighted scoring model.

Produces a 0..1 score plus a per-component breakdown, or forces 0 with a reason
when a hard disqualifier trips. Tuned to Ricky's criteria: commute and genuine
design-role fit lead; org type carries NO penalty; the salary floor is soft.

The commute component is computed upstream (commute.py) and stashed in
``job.score_breakdown['commute']`` by the scrape pipeline; the scorer reads it
from there so it doesn't re-geocode.
"""
from __future__ import annotations

from models import Job

# Enrichment-supplied booleans map to component scores as True->1, None->0.5,
# False->0 (None = "we don't know yet", so don't punish).
_TRISTATE = {True: 1.0, None: 0.5, False: 0.0}


def _tri(v: bool | None) -> float:
    return _TRISTATE[v]


def score_job(job: Job, cfg: dict) -> tuple[float, dict, str | None]:
    sc = cfg["scoring"]
    weights = sc["weights"]
    prefs = sc["preferences"]
    dq = cfg.get("disqualifiers", {})

    # ── HARD DISQUALIFIERS ──────────────────────────────────────────────────
    if job.role_type in dq.get("role_types", []):
        return 0.0, {"disqualified": f"role type: {job.role_type}"}, f"role_type={job.role_type}"
    if dq.get("kill_if_admin_heavy") and job.is_admin_heavy:
        return 0.0, {"disqualified": "admin-heavy role"}, "admin_heavy"
    if dq.get("kill_if_outside_metro_and_onsite") and job.location_normalized == "Other":
        return 0.0, {"disqualified": "outside Vancouver metro, on-site"}, "out_of_metro"

    breakdown: dict = {}

    # ── COMMUTE (precomputed upstream) ──────────────────────────────────────
    breakdown["commute"] = float(job.score_breakdown.get("commute", 0.5)) \
        if isinstance(job.score_breakdown, dict) else 0.5

    # ── ROLE TYPE (tiered: core design > other design > adjacent) ───────────
    role_scores = sc.get("role_type_scores", {})
    default = sc.get("role_type_default", 0.3)
    breakdown["role_type"] = role_scores.get(job.role_type or "unknown", default)

    # ── DESIGN AUTONOMY (enrichment) ────────────────────────────────────────
    breakdown["design_autonomy"] = _tri(job.has_design_autonomy)

    # ── MIXED ROLE ──────────────────────────────────────────────────────────
    if job.has_mixed_role is True:
        breakdown["mixed_role"] = 1.0
    elif job.is_drafting_only or job.role_type == "pm_only":
        breakdown["mixed_role"] = 0.0
    else:
        breakdown["mixed_role"] = _tri(job.has_mixed_role)

    # ── SALARY (soft floor) ─────────────────────────────────────────────────
    floor, target = prefs["salary_floor"], prefs["salary_target"]
    if job.salary_min:
        breakdown["salary"] = min(job.salary_min / target, 1.0)
    elif job.salary_max:
        breakdown["salary"] = min(job.salary_max / target, 1.0)
    else:
        breakdown["salary"] = 0.5   # unknown: don't penalize
    # Soft penalty (not a kill) if the whole range sits below the floor.
    if job.salary_max and job.salary_max < floor:
        breakdown["salary"] = min(breakdown["salary"], 0.3)

    # ── ROLE QUALITY (variety / not admin / not drafting) ───────────────────
    quality_signals = [
        _tri(job.has_variety),
        1.0 - _tri(job.is_admin_heavy),      # invert: admin-heavy is bad
        1.0 - _tri(job.is_drafting_only),
    ]
    breakdown["role_quality"] = sum(quality_signals) / len(quality_signals)

    # ── WEIGHTED BASE ───────────────────────────────────────────────────────
    raw = sum(breakdown[k] * weights[k] for k in weights)

    # ── BONUSES (additive, capped) ──────────────────────────────────────────
    bonus = 0.0
    firms = [f.lower() for f in sc.get("target_firms", [])]
    company = (job.company or "").lower()
    if company and any(f in company or company in f for f in firms):
        bonus += sc.get("target_firm_bonus", 0.10)

    skills = sc.get("skills_bonus", {})
    desc = (job.description or "").lower()
    hits = sum(1 for t in skills.get("terms", []) if t in desc)
    if hits:
        bonus += min(hits * skills.get("per_hit", 0.02), skills.get("cap", 0.08))

    breakdown["_base"] = round(raw, 4)
    breakdown["_bonus"] = round(bonus, 4)
    total = min(raw + bonus, 1.0)

    # Soft admin-heavy penalty: heavy dock, stays visible (not disqualified).
    if job.is_admin_heavy:
        mult = sc.get("penalties", {}).get("admin_heavy", 0.4)
        total *= mult
        breakdown["_admin_penalty"] = mult

    return round(total, 4), breakdown, None
