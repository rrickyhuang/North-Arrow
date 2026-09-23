"""Draft a cover letter for a stored job (scraped or manually added via
addjob.py) by shelling out to the `claude` CLI — this runs under your Claude
Code subscription/plan rather than metered Anthropic API tokens, unlike the
Haiku enrichment calls elsewhere in this pipeline.

Every draft (CLI or web cockpit) gets a second, fresh-context `claude -p` call
that critiques it against the job posting (missed keywords, generic framing,
unverified claims) before it's saved; if the critique flags anything, one more
call revises the letter against that feedback. See draft_letter().

Usage:
    python coverletter.py <row-#-from-show.py-or-job-id>
    python coverletter.py <row-# or id> --notes "specific points to include"
    python coverletter.py <row-# or id>              # omits --notes: prompts
                                                       # interactively instead
                                                       # (optional, skippable)

    python coverletter.py revise <row-# or id>       # iteratively tweak a saved
                                                       # letter; prompts for each
                                                       # change in a loop until you
                                                       # press Enter/q to finish
    python coverletter.py revise <row-# or id> "..."  # seed the first change, then
                                                       # continue in the same loop

Each revision overwrites the saved letter and archives the previous version,
timestamped, under digests/cover_letters/backups/ (every revision is kept).
Revisions use the same `claude` CLI (subscription, not API).
"""
from __future__ import annotations

import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import company_research
import config
import db
import promptcommon
from enrichment import _profile_block

_OUT_DIR = Path(__file__).with_name("digests") / "cover_letters"
_BAK_DIR = _OUT_DIR / "backups"

# Overridable via config.yaml's cover_letter.max_words. This is a hard
# ceiling for the critique pass to flag, not the length to write toward —
# see _target_word_range() for the actual aim handed to the model.
_DEFAULT_MAX_WORDS = 350


def _resolve_job(conn, target: str):
    if target.isdigit():
        jobs = db.query(conn, include_dismissed=True, include_duplicates=True,
                        order_by="score DESC")
        idx = int(target)
        if idx < len(jobs):
            return jobs[idx]
    return db.get(conn, target)


def _target_word_range(max_words: int) -> tuple[int, int]:
    """A short, direct target range to write toward, scaled to but well
    under `max_words` (the hard ceiling checked in the critique pass)."""
    high = min(max_words, 300)
    low = max(150, int(high * 0.75))
    return low, high


def build_prompt(job, cfg: dict, notes: str = "") -> str:
    profile = cfg.get("profile", {})
    context = promptcommon.context_block(job, "letter")
    notes_block = promptcommon.notes_block(notes, "letter")
    max_words = cfg.get("cover_letter", {}).get("max_words", _DEFAULT_MAX_WORDS)
    target_low, target_high = _target_word_range(max_words)

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
- Write a short, direct letter: 3 tight paragraphs, aiming for {target_low}-{target_high} words total. Make each sentence earn its place rather than filling space — a hiring manager should be able to read this in under a minute. ({max_words} words is a hard ceiling, not something to write toward.)
- {promptcommon.no_gap_concession()}
- Assume the resume and portfolio are already attached to the application, so the closing should simply express interest in talking further and thank them for their time — no offer to send, share, or attach a resume, portfolio, references, or work samples.
- {promptcommon.ai_tells(extra="; and a closing paragraph that just restates everything already said")}
- Output ONLY the letter text (no subject line, no markdown headers, no commentary before/after)."""


_CRITIQUE_PASS_SENTINEL = "NO CHANGES NEEDED"


def build_critique_prompt(job, letter: str, max_words: int = _DEFAULT_MAX_WORDS) -> str:
    target_low, target_high = _target_word_range(max_words)
    return f"""You are a skeptical hiring manager reviewing a cover letter against the job posting it's responding to. Be specific and unsparing — this letter will be sent as-is unless you flag something.

=== JOB POSTING ===
Title: {job.title}
Company: {job.company}
Description:
{(job.description or "")[:3000]}

=== DRAFT COVER LETTER ===
{letter}

=== YOUR TASK ===
Check for:
- Keywords/requirements from the posting that the letter ignores despite the candidate plausibly having relevant experience for them.
- Weak, generic, or boilerplate framing that could apply to any job/company.
- Claims the letter makes that aren't grounded in anything the posting or the letter itself establishes (unverifiable or invented specifics).
- Length: this should read as a short, direct letter (target {target_low}-{target_high} words). Flag it if it runs noticeably past {max_words} words, or if it's padded with sentences that restate a point already made rather than adding one.
- Inflated language: ordinary work described as if it were extraordinary (e.g. routine tasks dressed up with words like "transformative" or "profound"). Flag it and suggest describing the same experience at its actual scale.

If the letter has none of these problems, respond with exactly "{_CRITIQUE_PASS_SENTINEL}" and nothing else.

Otherwise, respond with a short, concrete list of fixes — each one an instruction specific enough to act on directly (e.g. "Paragraph 2 doesn't mention the posting's emphasis on public engagement — work in the candidate's community-workshop experience" rather than "make it more specific"). Do not rewrite the letter yourself. Do not comment on anything not covered above."""


def build_revision_prompt(letter: str, instruction: str) -> str:
    return f"""You are revising an existing cover letter. Apply the requested change and return the full revised letter.

=== CURRENT LETTER ===
{letter}

=== REQUESTED CHANGE ===
{instruction.strip()}

=== INSTRUCTIONS ===
- Make ONLY the change requested. Leave every other sentence exactly as it is — same wording, same paragraphs, same order. Do not "improve" untouched parts.
- Keep the salutation and the "Sincerely," + name signature intact unless the change is specifically about them.
- Do not narrate the edit or add commentary. Output ONLY the full revised letter text (no markdown, no notes before or after)."""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60]


def _letter_filename_suffix(job) -> str:
    return f"{_slug(job.company)}_{_slug(job.title)}_{job.id}.md"


def _letter_path(job) -> Path:
    """Path to a job's letter file, prefixed with the date it was first
    drafted so files sort chronologically in a file browser. The date is
    fixed at first draft: an existing file (found by its job-id suffix, since
    the date prefix isn't known in advance) is reused as-is on every
    subsequent revision rather than being renamed to today's date.

    Letters saved before this dated-filename scheme existed are migrated in
    place the first time they're looked up — renamed to add a date prefix
    (the file's last-modified date, which is what its own "Drafted:" header
    line already shows, since that's rewritten on every save)."""
    suffix = _letter_filename_suffix(job)
    dated = sorted(_OUT_DIR.glob(f"*_{suffix}"))
    if dated:
        return dated[0]
    legacy = _OUT_DIR / suffix
    if legacy.exists():
        stamp = datetime.fromtimestamp(legacy.stat().st_mtime).strftime("%Y-%m-%d")
        migrated = _OUT_DIR / f"{stamp}_{suffix}"
        try:
            legacy.rename(migrated)
        except FileNotFoundError:
            # Another concurrent call already migrated it — reuse whatever
            # dated file resulted instead of racing on the rename.
            dated = sorted(_OUT_DIR.glob(f"*_{suffix}"))
            if dated:
                return dated[0]
            raise
        return migrated
    return _OUT_DIR / f"{date.today().strftime('%Y-%m-%d')}_{suffix}"


# Divides the job-info header from the letter body in a saved .md. Revisions
# split on this so only the body is ever sent to the claude CLI.
_HEADER_DIVIDER = "\n<!-- letter -->\n"


def _letter_header(job) -> str:
    lines = [
        "<!--",
        f"Job ID:   {job.id}",
        f"Title:    {job.title}",
        f"Company:  {job.company}",
        f"Location: {job.location or 'n/a'}",
    ]
    if job.url:
        lines.append(f"URL:      {job.url}")
    lines.append(f"Drafted:  {date.today().strftime('%B %d, %Y')}")
    lines.append("-->")
    return "\n".join(lines)


def _save_letter(job, body: str) -> Path:
    """Write header + body to the job's letter file and return the path."""
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = _letter_path(job)
    path.write_text(f"{_letter_header(job)}{_HEADER_DIVIDER}{body.strip()}\n",
                    encoding="utf-8")
    return path


def _split_letter(text: str) -> tuple[str, str]:
    """Return (header, body). Header is '' for older files without a divider."""
    if _HEADER_DIVIDER in text:
        header, body = text.split(_HEADER_DIVIDER, 1)
        return header, body.strip()
    return "", text.strip()


class CoverLetterError(Exception):
    """A cover-letter step failed for a reason worth showing the user (claude
    CLI missing / not logged in / timed out / no saved letter yet). Callers that
    aren't a terminal (e.g. the web UI) catch this instead of exiting."""


def run_claude(prompt: str) -> str:
    """Shell out to the claude CLI; return stdout or raise CoverLetterError with
    a user-facing message. Used by both the CLI and the web UI."""
    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True, text=True, encoding="utf-8",
            stdin=subprocess.DEVNULL, timeout=180,
        )
    except FileNotFoundError:
        raise CoverLetterError(
            "Couldn't find the `claude` CLI on PATH. Install Claude Code "
            "(https://claude.com/claude-code) and make sure `claude` runs from a terminal.")
    except subprocess.TimeoutExpired:
        raise CoverLetterError("claude CLI timed out after 180s.")

    if result.returncode != 0 or not result.stdout.strip():
        raise CoverLetterError(
            f"claude CLI failed (exit {result.returncode}): "
            f"{result.stderr.strip()[:400] or 'no output'}. "
            "Check you're logged in — run `claude` interactively once and confirm "
            "it starts without an auth error.")
    return result.stdout.strip()


def _run_claude(prompt: str) -> str:
    """CLI wrapper: same as run_claude but prints the error and exits, matching
    the terminal UX the rest of this module's CLI paths expect."""
    try:
        return run_claude(prompt)
    except CoverLetterError as e:
        print(f"\n  {e}\n")
        sys.exit(1)


def letter_path(job) -> Path:
    """Public path to a job's saved letter (may not exist yet)."""
    return _letter_path(job)


def letter_body(job) -> str | None:
    """The saved letter's body (header stripped), or None if not drafted yet."""
    path = _letter_path(job)
    if not path.exists():
        return None
    _, body = _split_letter(path.read_text(encoding="utf-8"))
    return body


def draft_letter(job, cfg: dict, notes: str = "") -> Path:
    """Draft + save a letter, returning its path. Raises CoverLetterError.

    Before drafting, a `claude -p` web-search call researches the hiring
    company (cached on the job row — see company_research.py — so a repeat
    draft/revise never re-researches it). A research failure shouldn't block
    drafting, so it's just skipped.

    A second `claude -p` call (fresh context) critiques the draft against the
    job posting before it's saved; if it flags concrete issues, one more call
    applies that critique as a revision. A critique-call failure shouldn't
    block presenting an otherwise-good draft, so it's caught rather than
    raising CoverLetterError."""
    conn = db.connect()
    db.init_db(conn)
    company_research.get_or_research(conn, job)
    conn.close()

    max_words = cfg.get("cover_letter", {}).get("max_words", _DEFAULT_MAX_WORDS)
    letter = run_claude(build_prompt(job, cfg, notes))
    try:
        critique = run_claude(build_critique_prompt(job, letter, max_words))
    except CoverLetterError:
        critique = _CRITIQUE_PASS_SENTINEL
    if _CRITIQUE_PASS_SENTINEL not in critique.upper():
        letter = run_claude(build_revision_prompt(letter, critique))
    return _save_letter(job, letter)


def revise_letter(job, instruction: str) -> Path:
    """Apply one revision to the saved letter (archiving the prior version),
    returning its path. Raises CoverLetterError if there's no letter yet."""
    path = _letter_path(job)
    if not path.exists():
        raise CoverLetterError("No saved letter to revise yet — draft one first.")
    _, letter = _split_letter(path.read_text(encoding="utf-8"))
    revised = run_claude(build_revision_prompt(letter, instruction))
    _BAK_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (_BAK_DIR / f"{path.stem}_{stamp}.md").write_text(
        path.read_text(encoding="utf-8"), encoding="utf-8")
    return _save_letter(job, revised)


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


def _resolve_or_exit(target: str):
    conn = db.connect()
    db.init_db(conn)
    job = _resolve_job(conn, target)
    conn.close()
    if not job:
        print(f"\n  No job found for {target!r}\n")
        sys.exit(1)
    return job


def _generate(args: list[str]) -> None:
    target = args[0]
    job = _resolve_or_exit(target)

    if "--notes" in args:
        i = args.index("--notes")
        notes = args[i + 1] if i + 1 < len(args) else ""
    else:
        notes = _prompt_notes()

    cfg = config.load_config()

    print(f"\n  Drafting a cover letter for: {job.title} @ {job.company}")
    print("  Shelling out to the claude CLI (uses your subscription, not API tokens)...")
    try:
        path = draft_letter(job, cfg, notes)
    except CoverLetterError as e:
        print(f"\n  {e}\n")
        sys.exit(1)
    print(f"\n  Saved: {path}\n")


def _revise(args: list[str]) -> None:
    if not args:
        print("\n  Usage: python coverletter.py revise <row-# or job-id> [\"one-shot instruction\"]\n")
        sys.exit(1)

    job = _resolve_or_exit(args[0])
    path = _letter_path(job)
    if not path.exists():
        print(f"\n  No saved letter at {path}\n  Generate it first: python coverletter.py {args[0]}\n")
        sys.exit(1)

    one_shot = args[1] if len(args) > 1 else ""
    _, letter = _split_letter(path.read_text(encoding="utf-8"))
    print(f"\n  Revising: {job.title} @ {job.company}")
    print(f"  File: {path}")

    while True:
        if one_shot:
            instruction, one_shot = one_shot, ""
        else:
            print("\n  Describe the change (or press Enter / 'q' to finish):")
            try:
                instruction = input("  > ").strip()
            except EOFError:
                break
            if instruction == "" or instruction.lower() == "q":
                break

        print("  Applying via claude CLI...")
        revised = _run_claude(build_revision_prompt(letter, instruction))

        # Archive the full current file (header + body) into backups/ with a
        # timestamp so every revision is kept, then re-save with the header
        # re-attached so revisions never touch it.
        _BAK_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = _BAK_DIR / f"{path.stem}_{stamp}.md"
        bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        _save_letter(job, revised)
        letter = revised

        print("\n  ── revised letter ──\n")
        print(revised)
        print(f"\n  Saved (previous version archived → backups/{bak.name})")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("\n  Usage:\n"
              "    python coverletter.py <row-# or job-id> [--notes \"...\"]   # generate\n"
              "    python coverletter.py revise <row-# or job-id> [\"...\"]     # revise a saved letter\n")
        sys.exit(1)

    if args[0] == "revise":
        _revise(args[1:])
    else:
        _generate(args)


if __name__ == "__main__":
    main()
