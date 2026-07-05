"""Draft a cover letter for a stored job (scraped or manually added via
addjob.py) by shelling out to the `claude` CLI — this runs under your Claude
Code subscription/plan rather than metered Anthropic API tokens, unlike the
Haiku enrichment calls elsewhere in this pipeline.

Usage:
    python coverletter.py <row-#-from-show.py-or-job-id>
    python coverletter.py <row-# or id> --notes "specific points to include"
    python coverletter.py <row-# or id>              # omits --notes: prompts
                                                       # interactively instead
                                                       # (optional, skippable)
"""
from __future__ import annotations

import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import config
import db
from enrichment import _profile_block

_OUT_DIR = Path(__file__).with_name("digests") / "cover_letters"


def _resolve_job(conn, target: str):
    if target.isdigit():
        jobs = db.query(conn, include_dismissed=True, include_duplicates=True,
                        order_by="score DESC")
        idx = int(target)
        if idx < len(jobs):
            return jobs[idx]
    return db.get(conn, target)


def build_prompt(job, cfg: dict, notes: str = "") -> str:
    profile = cfg.get("profile", {})
    context_lines = []
    if job.fit_summary:
        context_lines.append(f"Fit assessment: {job.fit_summary}")
    if job.autonomy_evidence:
        context_lines.append(f"Design-autonomy evidence in the posting: {job.autonomy_evidence}")
    if job.missing_requirements:
        context_lines.append(f"Known gaps to navigate carefully (do not lie about these): "
                              f"{'; '.join(job.missing_requirements)}")
    context = "\n".join(context_lines)

    notes_block = (
        f"\n=== CANDIDATE'S NOTES FOR THIS SPECIFIC APPLICATION ===\n"
        f"The candidate asked specifically for these points/experiences to be "
        f"worked into this letter — prioritize them over anything you'd "
        f"otherwise pick from the general profile:\n\n{notes.strip()}\n"
        if notes.strip() else ""
    )

    sample = (profile.get("writing_sample") or "").strip()
    voice_block = (
        f"\n=== VOICE REFERENCE (this matters more than any style rule below) ===\n"
        f"Here is a sample of the candidate's own writing. This is the single most "
        f"important guide for how the letter should read. Match its sentence rhythm, "
        f"vocabulary level, warmth, and punctuation habits as closely as you can — "
        f"including how it opens and how it flows. Write like this person. If any "
        f"instruction below seems to conflict with sounding like this sample, the "
        f"sample wins.\n\n{sample}\n"
        if sample else ""
    )

    return f"""Write a complete, ready-to-send cover letter for this candidate applying to this job. Ground every claim in the candidate profile below — do not invent employers, projects, or credentials that aren't stated.

=== CANDIDATE PROFILE ===
{_profile_block(profile)}
{voice_block}
=== JOB POSTING ===
Title: {job.title}
Company: {job.company}
Location: {job.location}
Description:
{(job.description or "")[:3000]}

=== ADDITIONAL CONTEXT ===
{context or "(none)"}
{notes_block}
=== LETTER HEADER ===
- Date of writing: {date.today().strftime("%B %d, %Y")}
- Recipient / organization: {job.company or "the organization"} — use a named contact only if the posting actually gives one; otherwise address it to "Dear Hiring Committee," or "Dear {job.company} Hiring Team,".

=== A ROUGH ARC TO FOLLOW (a guide, not a rigid template — let the voice sample shape how it actually opens and flows) ===
- Somewhere early, make clear which exact position this is for and, if there's a genuine hook (a personal connection, a sharp read of what they need), lead with that rather than a boilerplate "I am writing to apply" opener.
- Show you understand what this organization actually does and, where the posting hints at it, why they're hiring for this role right now — the problem or gap behind the opening, not a generic description of the org.
- Make the case that this candidate answers that need. Back it with a brief, concrete story from real experience, not just a list of claims restated from the posting. If notes were supplied above, prioritize weaving those in here.
- Close with genuine interest in a conversation and appreciation for their time.

=== OTHER INSTRUCTIONS ===
- Open with a proper salutation and end with a signature line ("Sincerely," + candidate name) — don't skip the greeting or the closing.
- Roughly 3-4 paragraphs. Length and paragraphing should feel like the voice sample rather than hitting a fixed word count.
- If there's a qualification gap (e.g. missing registration), don't hide it, but don't dwell on it either — frame it naturally if relevant.
- Write like a real, specific person, not a generic AI assistant. The voice sample is your guide for that. Avoid hollow filler openers like "In today's world/landscape" and closings that just restate everything you already said.
- Output ONLY the letter text (no subject line, no markdown headers, no commentary before/after)."""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60]


_END_SENTINEL = "END"


def _prompt_notes() -> str:
    print("\n  Any specific points, experiences, or other info to work into "
          "this letter? Optional — press Enter to skip, or paste your notes "
          f"and finish with {_END_SENTINEL} on its own line:")
    try:
        first = input()
    except EOFError:
        return ""
    if first.strip() == "":
        return ""
    lines = [first]
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == _END_SENTINEL:
            break
        lines.append(line)
    return "\n".join(lines).strip()


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("\n  Usage: python coverletter.py <row-# or job-id> [--notes \"...\"]\n")
        sys.exit(1)

    target = args[0]
    has_notes_flag = "--notes" in args

    conn = db.connect()
    db.init_db(conn)
    job = _resolve_job(conn, target)
    conn.close()
    if not job:
        print(f"\n  No job found for {target!r}\n")
        sys.exit(1)

    if has_notes_flag:
        i = args.index("--notes")
        notes = args[i + 1] if i + 1 < len(args) else ""
    else:
        notes = _prompt_notes()

    cfg = config.load_config()
    prompt = build_prompt(job, cfg, notes)

    print(f"\n  Drafting a cover letter for: {job.title} @ {job.company}")
    print("  Shelling out to the claude CLI (uses your subscription, not API tokens)...")
    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True, text=True, encoding="utf-8",
            stdin=subprocess.DEVNULL, timeout=180,
        )
    except FileNotFoundError:
        print("\n  Couldn't find the `claude` CLI on PATH. Install Claude Code "
              "(https://claude.com/claude-code) and make sure `claude` runs from a terminal.\n")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("\n  claude CLI timed out after 180s.\n")
        sys.exit(1)

    if result.returncode != 0 or not result.stdout.strip():
        print(f"\n  claude CLI failed (exit {result.returncode}):\n  {result.stderr.strip()}")
        print("  Check you're logged in: run `claude` interactively once and confirm "
              "it starts without an auth error.\n")
        sys.exit(1)

    letter = result.stdout.strip()
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = _OUT_DIR / f"{_slug(job.company)}_{_slug(job.title)}_{job.id}.md"
    path.write_text(letter, encoding="utf-8")
    print(f"\n  Saved: {path}\n")


if __name__ == "__main__":
    main()
