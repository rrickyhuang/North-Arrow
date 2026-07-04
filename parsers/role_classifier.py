"""Keyword-based first-pass role classification.

This is a cheap pre-filter; the LLM enrichment step refines it later. We weigh
the title more heavily than the body, and apply a couple of disambiguation
rules (pm_only / drafting_only only win when genuine design signals are absent).
"""
from __future__ import annotations

ROLE_SIGNALS: dict[str, list[str]] = {
    "urban_design": [
        "urban design", "urban designer", "streetscape", "public realm",
        "placemaking", "civic space", "open space design", "built environment",
    ],
    "landscape_arch": [
        "landscape architect", "landscape architecture", "bcsla", "csla",
        "planting design", "park design", "hardscape", "landscape designer",
    ],
    "planning": [
        "urban planner", "planning associate", "land use", "rezoning",
        "official community plan", "development permit", "community planner",
    ],
    "civic_innovation": [
        "civic innovation", "public engagement", "community design",
        "participatory", "civic tech", "social infrastructure",
    ],
    "architecture": [
        "architectural designer", "architectural design", "building design",
        "intern architect", "job captain", "architect",
    ],
    # ── Broader design fields (Ricky is flexible across design) ──
    "interior_design": [
        "interior design", "interior designer", "exhibition design",
        "spatial design", "environmental design", "retail design",
        "set design", "scenography", "interior architect",
    ],
    "graphic_design": [
        "graphic design", "graphic designer", "visual designer",
        "visual communication", "communication design", "brand designer",
        "branding", "wayfinding", "signage design", "editorial design",
    ],
    "industrial_design": [
        "industrial design", "industrial designer", "furniture design",
        "furniture designer", "product design", "product designer",
        "fabrication", "prototyping",
    ],
    "digital_design": [
        "ux designer", "ui designer", "ux/ui", "user experience", "web design",
        "web designer", "motion design", "game design", "digital designer",
        "interaction design",
    ],
    # ── Design embedded in ops-heavy orgs (logistics/retail/manufacturing) ──
    "ops_design": [
        "space planner", "space planning", "workplace strategist",
        "workplace design", "workplace strategy", "facilities design",
        "facilities planning", "distribution center design",
        "corporate real estate design", "warehouse design",
    ],
    # ── Design-adjacent (moving toward a design direction) ──
    "design_adjacent": [
        "design coordinator", "design assistant", "studio assistant",
        "studio coordinator", "junior designer", "design intern",
        "creative coordinator", "design research", "design technologist",
        "design operations", "design ops", "creative assistant",
    ],
    "admin": [
        "administrative assistant", "office administrator", "receptionist",
        "data entry", "clerical", "executive assistant", "office coordinator",
    ],
    "pm_only": [
        "project manager", "program manager", "project management",
    ],
    "drafting_only": [
        "drafter", "cad technician", "bim technician", "production staff",
        "technical drafting", "drafting technician",
    ],
}

# Any of these present means there IS real design content, so pm_only /
# drafting_only should not claim the posting.
_DESIGN_TERMS = (
    "design", "designer", "urban", "landscape", "placemaking", "public realm",
    "streetscape", "planning", "architect",
)


def _count_hits(terms: list[str], title: str, body: str) -> float:
    score = 0.0
    for t in terms:
        if t in title:
            score += 2.0
        if t in body:
            score += 1.0
    return score


def classify_role(title: str, description: str = "") -> str:
    title_l = (title or "").lower()
    body_l = (description or "").lower()
    has_design = any(t in title_l or t in body_l for t in _DESIGN_TERMS)

    scores = {role: _count_hits(terms, title_l, body_l)
              for role, terms in ROLE_SIGNALS.items()}

    # Disambiguation: pm_only / drafting_only only valid when no design signal.
    if has_design:
        scores["pm_only"] = 0.0
        scores["drafting_only"] = 0.0

    best = max(scores, key=scores.get)
    if scores[best] == 0.0:
        return "unknown"
    return best
