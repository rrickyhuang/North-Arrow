"""LLM enrichment via Claude Haiku.

One call per job returns two things:
  1. FIT signals (design autonomy, mixed role, variety, admin/drafting flags,
     refined role/org guesses) — these feed the score.
  2. QUALIFICATION assessment (seniority, required years/credentials, a verdict,
     and missing requirements) — display-only; it never changes the score.

Reads the candidate profile from config so the model judges fit/qualification
against the real person. Designed to fail soft: any error returns an empty dict
and the pipeline keeps the keyword-based values.
"""
from __future__ import annotations

import json
import logging
import re

import config

log = logging.getLogger("enrichment")

ROLE_TYPES = (
    "urban_design, landscape_arch, planning, civic_innovation, architecture, "
    "interior_design, graphic_design, industrial_design, digital_design, "
    "ops_design, design_adjacent, pm_only, admin, drafting_only, unknown"
)

_VALID_ROLE = set(r.strip() for r in ROLE_TYPES.split(","))
_VALID_QUAL = {"qualified", "stretch", "reach", "overqualified"}
_VALID_SENIORITY = {"entry", "junior", "intermediate", "senior", "director"}


def _profile_block(profile: dict) -> str:
    def fmt(v):
        return "\n".join(f"  - {x}" for x in v) if isinstance(v, list) else f"  {v}".strip()
    parts = []
    for key in ("summary", "experience_level", "credentials", "target_seniority",
                "wants", "avoids", "design_software", "differentiator"):
        if key in profile and profile[key]:
            parts.append(f"{key.upper()}:\n{fmt(profile[key])}")
    return "\n".join(parts)


def build_prompt(job, cfg: dict) -> str:
    profile = cfg.get("profile", {})
    max_chars = cfg["enrichment"].get("max_description_chars", 3000)
    desc = (job.description or "")[:max_chars]
    return f"""You assess whether a job posting fits a specific candidate AND whether the candidate is qualified for it. Be honest and grounded in the posting text.

=== CANDIDATE PROFILE ===
{_profile_block(profile)}

=== QUALIFICATION RUBRIC ===
Map the posting's seniority to a verdict for THIS candidate (an early-career 2025 design grad, ~1 yr non-design pro experience, no professional registration):
  - entry / junior / intern / coordinator / "designer I"  -> "qualified"
  - intermediate / "2-4 yrs"                               -> "stretch"
  - senior / principal / director / "5+ yrs"               -> "reach"
  - clearly far below their training                       -> "overqualified"
Roles hard-requiring registration (RPP/MCIP, BCSLA) the candidate lacks: keep the verdict but add the credential to missing_requirements (do NOT auto-fail).

=== JOB POSTING ===
Title: {job.title}
Company: {job.company}
Location: {job.location}
Description:
{desc}

=== RESPOND WITH JSON ONLY (no prose, no markdown fences) ===
{{
  "has_design_autonomy": true/false/null,
  "has_mixed_role": true/false/null,
  "has_variety": true/false/null,
  "is_admin_heavy": true/false/null,
  "is_drafting_only": true/false/null,
  "is_hierarchical": true/false/null,
  "skills_leverage": ["which of the candidate's skills the role uses"],
  "role_type_guess": "one of: {ROLE_TYPES} (ops_design = in-house design/space-planning role at a logistics, retail, or manufacturing company rather than a design studio)",
  "org_type_guess": "studio_consultancy|municipal_govt|provincial_govt|large_eng_firm|developer|nonprofit_civic|unknown",
  "org_size_guess": "small|mid|large|unknown",
  "seniority": "entry|junior|intermediate|senior|director",
  "required_years": number or null,
  "required_credentials": ["e.g. RPP", "BCSLA"],
  "qualification": "qualified|stretch|reach|overqualified",
  "missing_requirements": ["specific gaps for this candidate"],
  "autonomy_evidence": "one sentence quoting/paraphrasing the design-latitude signal (or its absence)",
  "fit_summary": "2-sentence plain verdict on fit for this candidate"
}}"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("no JSON object in response")
    return json.loads(m.group(0))


def _coerce(data: dict) -> dict:
    """Validate/normalize the model output so bad values don't poison the DB."""
    out: dict = {}
    for k in ("has_design_autonomy", "has_mixed_role", "has_variety",
              "is_admin_heavy", "is_drafting_only", "is_hierarchical"):
        v = data.get(k)
        out[k] = v if v in (True, False, None) else None
    out["skills_leverage"] = [str(s) for s in data.get("skills_leverage", []) if s][:10]
    out["required_credentials"] = [str(s) for s in data.get("required_credentials", []) if s][:8]
    out["missing_requirements"] = [str(s) for s in data.get("missing_requirements", []) if s][:8]

    rt = data.get("role_type_guess")
    out["role_type_guess"] = rt if rt in _VALID_ROLE else None
    q = data.get("qualification")
    out["qualification"] = q if q in _VALID_QUAL else None
    sen = data.get("seniority")
    out["seniority"] = sen if sen in _VALID_SENIORITY else None

    ry = data.get("required_years")
    out["required_years"] = int(ry) if isinstance(ry, (int, float)) else None
    out["org_type_guess"] = data.get("org_type_guess") or None
    out["org_size_guess"] = data.get("org_size_guess") or None
    out["autonomy_evidence"] = (data.get("autonomy_evidence") or None)
    out["fit_summary"] = (data.get("fit_summary") or None)
    return out


_client = None


def _get_client():
    global _client
    if _client is None:
        from anthropic import Anthropic
        _client = Anthropic(api_key=config.env("ANTHROPIC_API_KEY", required=True))
    return _client


def enrich(job, cfg: dict) -> dict:
    """Return a dict of enrichment fields, or {} on any failure (fail-soft)."""
    try:
        client = _get_client()
        model = cfg["enrichment"].get("model", "claude-haiku-4-5")
        resp = client.messages.create(
            model=model,
            max_tokens=700,
            messages=[{"role": "user", "content": build_prompt(job, cfg)}],
        )
        data = _extract_json(resp.content[0].text)
        return _coerce(data)
    except Exception as e:  # noqa: BLE001 — enrichment is best-effort
        log.warning("enrichment failed for %r: %s", job.title[:40], e)
        return {}
