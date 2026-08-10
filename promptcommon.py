"""Prompt fragments shared between coverletter.py and appquestions.py, so a
wording fix (e.g. banning a new AI-tell phrase) lands in one place instead of
drifting between the two prompts that both draft candidate-facing text from
the same profile/job context.
"""
from __future__ import annotations


def context_block(job, doc_noun: str) -> str:
    """The '=== ADDITIONAL CONTEXT ===' body: company research, fit
    assessment, autonomy evidence, and a caveat about qualification gaps
    (for the model's awareness only — never meant to be surfaced in the
    output). `doc_noun` names what's being written ("letter" or "answer")
    for that caveat's wording."""
    lines = []
    if job.company_research:
        lines.append(f"Company research: {job.company_research}")
    if job.fit_summary:
        lines.append(f"Fit assessment: {job.fit_summary}")
    if job.autonomy_evidence:
        lines.append(f"Design-autonomy evidence in the posting: {job.autonomy_evidence}")
    if job.missing_requirements:
        lines.append(f"Areas where the candidate is light (for your awareness only — "
                      f"do NOT mention, concede, or apologize for these in the {doc_noun}; "
                      f"just don't claim strength the candidate lacks): "
                      f"{'; '.join(job.missing_requirements)}")
    return "\n".join(lines)


def notes_block(notes: str, doc_noun: str) -> str:
    """The '=== CANDIDATE'S NOTES ===' block, or '' if none were given."""
    if not notes.strip():
        return ""
    return (
        f"\n=== CANDIDATE'S NOTES FOR THIS {doc_noun.upper()} ===\n"
        f"The candidate asked specifically for these points/experiences to be "
        f"worked into this {doc_noun} — prioritize them over anything you'd "
        f"otherwise pick from the general profile:\n\n{notes.strip()}\n"
    )


def no_gap_concession() -> str:
    """Instruction banning both conceding qualification gaps and the
    'let me be honest/candid about...' AI tell that tends to sneak in when a
    model is told not to hide a weakness. Every phrase listed here was caught
    in an actual generated draft at some point — extend this list rather than
    letting a fix land in only one of the two prompts that use it."""
    return (
        f'Do NOT proactively raise, name, or apologize for qualification gaps, even '
        f'if the question or posting invites reflection on weaknesses. Don\'t concede '
        f"what the candidate lacks — spend the space making a positive case with the "
        f"experience the candidate does have. (Only address a gap directly if the "
        f"candidate's notes explicitly ask you to.) Never narrate your own honesty "
        f'about it — no "I want to be upfront/straightforward/honest", no "I\'ll be '
        f'candid", no "I\'ll be equally straight/candid/upfront about...", no "I\'d '
        f'rather name that plainly than dress it up", no "I know the posting wants '
        f'X, but...". A confident writer just makes the case; they don\'t announce a '
        f"shortfall the reader hadn't raised."
    )


def ai_tells(extra: str = "") -> str:
    """Instruction to avoid generic AI writing tells. `extra` appends
    additional format-specific tells (e.g. cover letters' restating closing
    paragraph) before the final period."""
    return (
        "Write like a real, specific person, not a generic AI assistant. Avoid "
        'these tells: meta-commentary addressed to the reader about your own '
        'doubts or honesty; hedges like "more than you might expect" or "you '
        'might be wondering"; hollow openers like "In today\'s world/landscape"'
        f"{extra}."
    )
