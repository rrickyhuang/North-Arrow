"""Weighted scoring model.

Produces a 0..1 score plus a per-component breakdown. Role-type red flags
(admin, drafting-only) are multiplicative soft penalties, not hard kills, so a
job can still surface if it's a strong match despite one bad signal. The one
remaining hard kill is genuinely out-of-metro, on-site postings (a different
city/province entirely, not commutable) — see disqualifiers.kill_if_outside_metro_and_onsite.
Tuned to Ricky's criteria: commute and genuine design-role fit lead; org type
carries NO penalty; the salary floor is soft.

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

    # ── HARD DISQUALIFIER ────────────────────────────────────────────────────
    # Genuinely out-of-metro AND on-site (a different city/province, not a
    # commute) — everything else is a soft penalty, but this one isn't
    # workable regardless of how well the role otherwise fits.
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

    # Soft penalty for role_type == "admin": same idea, but for postings whose
    # primary role IS admin rather than just admin-heavy in duties. Docked hard,
    # not killed, since these can still be a strong experience match.
    if job.role_type == "admin":
        mult = sc.get("penalties", {}).get("role_type_admin", 0.4)
        total *= mult
        breakdown["_role_type_admin_penalty"] = mult

    # Soft penalty for role_type == "drafting_only": same idea as role_type_admin.
    if job.role_type == "drafting_only":
        mult = sc.get("penalties", {}).get("role_type_drafting_only", 0.4)
        total *= mult
        breakdown["_role_type_drafting_only_penalty"] = mult

    # Soft non-full-time penalty: "unknown" isn't penalized — many genuinely
    # full-time postings never say so explicitly, so absence of a signal
    # shouldn't be treated as a part-time/casual signal.
    if job.employment_type and job.employment_type not in ("full_time", "unknown"):
        mult = sc.get("penalties", {}).get("non_full_time", 0.5)
        total *= mult
        breakdown["_employment_penalty"] = mult

    # Soft seniority/experience penalty: a "reach" director/senior role Ricky
    # can't get shouldn't outrank an entry-level role he can. The base model
    # never sees seniority, so fold in enrichment's qualification verdict here.
    # qualified/overqualified and unknown (None) are not docked.
    qual_penalties = sc.get("penalties", {}).get("qualification", {})
    if job.qualification in qual_penalties:
        mult = qual_penalties[job.qualification]
        total *= mult
        breakdown["_qualification_penalty"] = mult

    return round(total, 4), breakdown, None
