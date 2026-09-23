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
        f"Spend the space making a positive case with the experience the candidate "
        f"does have, even where the question or posting invites reflection on "
        f"weaknesses. A confident writer just makes the case, the way they would in "
        f"conversation, without first announcing a shortfall the reader hadn't "
        f"raised. (Only address a gap directly if the candidate's notes explicitly "
        f'ask you to.) That means no lead-in like "I want to be upfront/honest", '
        f'"I\'ll be candid", or "I know the posting wants X, but..." — skip the '
        f"announcement and go straight to the case."
    )


def ai_tells(extra: str = "") -> str:
    """Instruction to steer away from generic AI writing tells, framed as
    positive habits to write toward rather than a list of bans. `extra`
    appends an additional format-specific habit (e.g. cover letters' closing
    paragraph) before the final period."""
    return (
        "Write like a real, specific person, not a generic AI assistant. Say each "
        "thing once, in plain subject-verb-object order, and move on — don't "
        "circle back to restate a point you already made or wrap a simple "
        'statement in extra clauses ("what this really means is...", "at its '
        'core..."). State things directly instead of building a sentence around '
        'a contrast ("It\'s not X, it\'s Y" — just say Y). Lead each sentence with '
        'its actual point rather than a subordinate clause ("Through my work on X, '
        'Y and Z became..." — just say "Y and Z became..."). Reach for the '
        'plainest word that\'s accurate over a fancier synonym — if "good" or '
        '"clear" is true, that beats "exceptional" or "invaluable." Describe '
        "experiences at the scale a normal person would describe them — ordinary "
        'work stays ordinary; save words like "transformative," "profound," or '
        '"passion" for things that actually warrant them. Open with the actual '
        'point, not a throat-clearing setup like "In today\'s world/landscape." '
        "Keep commentary about your own doubts or honesty out of the text "
        "entirely — just state things directly"
        f"{extra}."
    )
