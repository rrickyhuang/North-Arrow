"""Draft answers to specific application questions — the free-text prompts
some applications ask beyond a cover letter ("Why do you want to work here?",
"Describe a time you...") — via the same `claude` CLI flow as coverletter.py
(subscription-based, not metered API tokens).

Unlike a cover letter, a job can have any number of these, added over time as
you fill out an application. Each question/answer pair is stored together in
one file per job (application_answers/), keyed by a short id so it can
be revised or deleted independently. Every draft gets the same critique pass
cover letters get (missed keywords, generic framing, unverified claims,
ignoring the actual question asked) before it's saved.

Usage:
    python appquestions.py <row-# or job-id> "<question text>" [--notes "..."] [--limit "500 words"]
    python appquestions.py list <row-# or job-id>
    python appquestions.py revise <row-# or job-id> <qa-id> "instruction"
    python appquestions.py delete <row-# or job-id> <qa-id>
"""
from __future__ import annotations

import re
import sys
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import company_research
import config
import db
import promptcommon
from coverletter import CoverLetterError, _resolve_job, run_claude
from enrichment import _profile_block

_OUT_DIR = Path(__file__).with_name("application_answers")
_BAK_DIR = _OUT_DIR / "backups"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60]


def _qa_path(job) -> Path:
    return _OUT_DIR / f"{_slug(job.company)}_{_slug(job.title)}_{job.id}.md"


@dataclass
class QA:
    id: str
    question: str
    answer: str


# Divides the job-info header from the Q&A body, same convention as
# coverletter.py's letter files.
_ANSWERS_DIVIDER = "\n<!-- answers -->\n"
_QA_RE = re.compile(r"^### (.+?)\n<!-- id:(\w+) -->\n(.*?)(?=\n### |\Z)", re.S | re.M)


def _header(job) -> str:
    lines = [
        "<!--",
        f"Job ID:   {job.id}",
        f"Title:    {job.title}",
        f"Company:  {job.company}",
        f"Location: {job.location or 'n/a'}",
    ]
    if job.url:
        lines.append(f"URL:      {job.url}")
    lines.append(f"Updated:  {date.today().strftime('%B %d, %Y')}")
    lines.append("-->")
    return "\n".join(lines)


def _serialize_qas(qas: list[QA]) -> str:
    return "\n\n".join(
        f"### {qa.question}\n<!-- id:{qa.id} -->\n{qa.answer.strip()}" for qa in qas)


def _parse_qas(body: str) -> list[QA]:
    return [QA(id=m.group(2), question=m.group(1).strip(), answer=m.group(3).strip())
            for m in _QA_RE.finditer(body)]


def _save_qas(job, qas: list[QA]) -> Path:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = _qa_path(job)
    path.write_text(f"{_header(job)}{_ANSWERS_DIVIDER}{_serialize_qas(qas)}\n",
                    encoding="utf-8")
    return path


def list_answers(job) -> list[QA]:
    """All saved question/answer pairs for a job, in the order they were
    added. Empty list if none have been drafted yet."""
    path = _qa_path(job)
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    _, _, body = text.partition(_ANSWERS_DIVIDER)
    return _parse_qas(body)


def get_answer(job, qa_id: str) -> QA | None:
    return next((qa for qa in list_answers(job) if qa.id == qa_id), None)


def _parse_limit(limit: str) -> tuple[int, str] | None:
    """Pull a number + unit out of a limit string, e.g. '500 characters',
    '500 characters or less', 'max 150 words', '<500 chars'. Returns None if
    no number/unit pair is found anywhere in the string — the limit still
    gets passed to the model as a soft instruction in that case, but nothing
    here can measure or enforce it."""
    m = re.search(r"(\d+)\s*(character|char|word)s?", limit.strip(), re.I)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    return n, ("characters" if unit.startswith("char") else "words")


def _measure(text: str, unit: str) -> int:
    return len(text) if unit == "characters" else len(text.split())


def build_prompt(job, cfg: dict, question: str, notes: str = "", limit: str = "") -> str:
    profile = cfg.get("profile", {})
    context = promptcommon.context_block(job, "answer")
    notes_block = promptcommon.notes_block(notes, "answer")

    parsed = _parse_limit(limit)
    if parsed:
        n, unit = parsed
        target = int(n * 0.85)
        limit_block = (
            f"\n=== LENGTH CONSTRAINT ===\nThe application enforces a hard limit of "
            f"{n} {unit}. Aim for around {target} {unit} — comfortably under, not "
            f"padded out to the edge of it.\n"
        )
    elif limit.strip():
        limit_block = (
            f"\n=== LENGTH CONSTRAINT ===\nThe application enforces a limit of "
            f"{limit.strip()}. Stay comfortably within it.\n"
        )
    else:
        limit_block = ""

    sample = (profile.get("writing_sample") or "").strip()
    voice_block = (
        f"\n=== VOICE REFERENCE (this matters more than any style rule below) ===\n"
        f"Here is a sample of the candidate's own writing. Match its sentence "
        f"rhythm, vocabulary level, warmth, and punctuation habits as closely "
        f"as you can. Write like this person.\n\n{sample}\n"
        if sample else ""
    )

    return f"""=== THE QUESTION TO ANSWER ===
{question.strip()}

Answer this specific application question for the candidate below, as if they were filling out the application themselves. Everything after this point — the profile, the posting, the context — is material to answer THIS question with, not a general topic to write about. Ground every claim in the candidate profile — do not invent employers, projects, or credentials that aren't stated.

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
{notes_block}{limit_block}
=== INSTRUCTIONS ===
- Before writing, silently identify in one sentence what this question is actually asking. Everything you write should serve answering that one thing — this is not an opportunity to summarize the candidate's whole background.
- Answer in first person, directly and specifically — actually address what's being asked, not a generic statement about the candidate.
- Back any claim with a brief, concrete detail from the profile rather than just restating the question's own language back at it.
- {promptcommon.no_gap_concession()}
- No greeting, salutation, or signature — this is a direct answer to a form field, not a letter.
- {promptcommon.ai_tells()}
- The question, one more time, so it's the last thing you read before writing: {question.strip()}
- Output ONLY the answer text (no restated question, no markdown headers, no commentary before/after)."""


_CRITIQUE_PASS_SENTINEL = "NO CHANGES NEEDED"


def build_critique_prompt(job, question: str, answer: str, limit: str = "") -> str:
    parsed = _parse_limit(limit)
    length_check = (
        f"- Length: this must fit a hard limit of {parsed[0]} {parsed[1]}. Count "
        f"roughly — the current answer is about {_measure(answer, parsed[1])} "
        f"{parsed[1]}. Flag it if it's at or over the limit, and name specific "
        f"sentences/clauses to cut, not just a vague length note.\n"
        if parsed else ""
    )
    return f"""You are a skeptical hiring manager reviewing an application-question answer against the job posting and the question it's supposed to answer. Be specific and unsparing — this answer will be submitted as-is unless you flag something.

=== JOB POSTING ===
Title: {job.title}
Company: {job.company}
Description:
{(job.description or "")[:3000]}

=== THE QUESTION ASKED ===
{question.strip()}

=== DRAFT ANSWER ===
{answer}

=== YOUR TASK ===
Check for:
- Does the answer actually address what the question asks, or does it drift into generic self-promotion?
- Weak, generic, or boilerplate framing that could apply to any job/company/question.
- Claims that aren't grounded in anything the posting or the answer itself establishes (unverifiable or invented specifics).
{length_check}
If the answer has none of these problems, respond with exactly "{_CRITIQUE_PASS_SENTINEL}" and nothing else.

Otherwise, respond with a short, concrete list of fixes — each one specific enough to act on directly. Do not rewrite the answer yourself. Do not comment on anything not covered above."""


def build_revision_prompt(answer: str, instruction: str) -> str:
    return f"""You are revising an existing application-question answer. Apply the requested change and return the full revised answer.

=== CURRENT ANSWER ===
{answer}

=== REQUESTED CHANGE ===
{instruction.strip()}

=== INSTRUCTIONS ===
- Make ONLY the change requested. Leave every other sentence exactly as it is.
- Do not narrate the edit or add commentary. Output ONLY the full revised answer text (no markdown, no notes before or after)."""


def build_trim_prompt(answer: str, n: int, unit: str) -> str:
    """Last-resort length pass, mirroring coverletter.py's build_trim_prompt:
    used only when the answer is still over the hard limit after the
    critique-driven revision, so a single soft revision instruction can't
    leave an over-limit answer as the final saved version."""
    return f"""This application answer is over its hard length limit. Cut it down to under {n} {unit}.

=== CURRENT ANSWER ===
{answer}

=== HOW TO CUT ===
- Cut whole sentences and clauses, starting with anything that restates a point already made, or elaborates past the point where the reader already gets it.
- Don't cut the specific, concrete detail the answer's case actually rests on just to hit the count faster.
- Do not narrate the edit or add commentary. Output ONLY the trimmed answer text (no markdown, no notes before or after)."""


def draft_answer(job, cfg: dict, question: str, notes: str = "", limit: str = "") -> QA:
    """Draft + save an answer to `question`, returning the QA. Raises
    CoverLetterError. Re-drafting the same question text updates its existing
    entry in place rather than duplicating it.

    Same research/critique flow as coverletter.draft_letter: company research
    is cached on the job row, and a fresh-context critique call revises the
    draft if it flags concrete issues before saving. If a --limit was given
    and is parseable (e.g. "500 characters"), a critique-driven revision
    doesn't reliably land under it on its own, so a dedicated trim pass runs
    as a last resort when the answer is still over afterward."""
    conn = db.connect()
    db.init_db(conn)
    company_research.get_or_research(conn, job)
    conn.close()

    answer = run_claude(build_prompt(job, cfg, question, notes, limit))
    try:
        critique = run_claude(build_critique_prompt(job, question, answer, limit))
    except CoverLetterError:
        critique = _CRITIQUE_PASS_SENTINEL
    if _CRITIQUE_PASS_SENTINEL not in critique.upper():
        answer = run_claude(build_revision_prompt(answer, critique))

    parsed = _parse_limit(limit)
    if parsed:
        n, unit = parsed
        if _measure(answer, unit) > n:
            try:
                answer = run_claude(build_trim_prompt(answer, n, unit))
            except CoverLetterError:
                pass

    qas = list_answers(job)
    existing = next((qa for qa in qas
                     if qa.question.strip().lower() == question.strip().lower()), None)
    qa_id = existing.id if existing else uuid.uuid4().hex[:8]
    qas = [qa for qa in qas if qa.id != qa_id]
    new_qa = QA(id=qa_id, question=question.strip(), answer=answer)
    qas.append(new_qa)
    _save_qas(job, qas)
    return new_qa


def revise_answer(job, qa_id: str, instruction: str) -> QA:
    """Apply one revision to a saved answer (archiving the prior version of
    the whole file), returning the updated QA. Raises CoverLetterError if
    there's no saved answer with that id."""
    qas = list_answers(job)
    qa = next((q for q in qas if q.id == qa_id), None)
    if qa is None:
        raise CoverLetterError("No saved answer found for that question — draft one first.")

    revised = run_claude(build_revision_prompt(qa.answer, instruction))

    path = _qa_path(job)
    _BAK_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (_BAK_DIR / f"{path.stem}_{stamp}.md").write_text(
        path.read_text(encoding="utf-8"), encoding="utf-8")

    new_qa = QA(id=qa_id, question=qa.question, answer=revised)
    qas = [new_qa if q.id == qa_id else q for q in qas]
    _save_qas(job, qas)
    return new_qa


def delete_answer(job, qa_id: str) -> bool:
    """Remove one saved answer. Returns False if no answer had that id."""
    qas = list_answers(job)
    remaining = [q for q in qas if q.id != qa_id]
    if len(remaining) == len(qas):
        return False
    if remaining:
        _save_qas(job, remaining)
    else:
        _qa_path(job).unlink(missing_ok=True)
    return True


def _resolve_or_exit(target: str):
    conn = db.connect()
    db.init_db(conn)
    job = _resolve_job(conn, target)
    conn.close()
    if not job:
        print(f"\n  No job found for {target!r}\n")
        sys.exit(1)
    return job


def _run_claude_or_exit(prompt: str) -> str:
    try:
        return run_claude(prompt)
    except CoverLetterError as e:
        print(f"\n  {e}\n")
        sys.exit(1)


def _list(args: list[str]) -> None:
    if not args:
        print("\n  Usage: python appquestions.py list <row-# or job-id>\n")
        sys.exit(1)
    job = _resolve_or_exit(args[0])
    qas = list_answers(job)
    if not qas:
        print(f"\n  No saved answers yet for: {job.title} @ {job.company}\n")
        return
    print(f"\n  {job.title} @ {job.company}\n")
    for qa in qas:
        print(f"  [{qa.id}] {qa.question}\n")
        print("  " + qa.answer.replace("\n", "\n  "))
        print()


def _draft(args: list[str]) -> None:
    target, question = args[0], args[1]
    job = _resolve_or_exit(target)

    notes, limit = "", ""
    if "--notes" in args:
        i = args.index("--notes")
        notes = args[i + 1] if i + 1 < len(args) else ""
    if "--limit" in args:
        i = args.index("--limit")
        limit = args[i + 1] if i + 1 < len(args) else ""

    cfg = config.load_config()
    print(f"\n  Drafting an answer for: {job.title} @ {job.company}")
    print("  Shelling out to the claude CLI (uses your subscription, not API tokens)...")
    try:
        qa = draft_answer(job, cfg, question, notes, limit)
    except CoverLetterError as e:
        print(f"\n  {e}\n")
        sys.exit(1)
    print(f"\n  Saved [{qa.id}]: {_qa_path(job)}\n")
    print(qa.answer)
    print()


def _revise(args: list[str]) -> None:
    if len(args) < 3:
        print("\n  Usage: python appquestions.py revise <row-# or job-id> <qa-id> \"instruction\"\n")
        sys.exit(1)
    job = _resolve_or_exit(args[0])
    qa_id, instruction = args[1], args[2]
    try:
        qa = revise_answer(job, qa_id, instruction)
    except CoverLetterError as e:
        print(f"\n  {e}\n")
        sys.exit(1)
    print(f"\n  Revised [{qa.id}]: {_qa_path(job)}\n")
    print(qa.answer)
    print()


def _delete(args: list[str]) -> None:
    if len(args) < 2:
        print("\n  Usage: python appquestions.py delete <row-# or job-id> <qa-id>\n")
        sys.exit(1)
    job = _resolve_or_exit(args[0])
    if delete_answer(job, args[1]):
        print(f"\n  Deleted [{args[1]}]\n")
    else:
        print(f"\n  No saved answer with id {args[1]!r}\n")
        sys.exit(1)


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("\n  Usage:\n"
              "    python appquestions.py <row-# or job-id> \"<question>\" [--notes \"...\"] [--limit \"...\"]\n"
              "    python appquestions.py list <row-# or job-id>\n"
              "    python appquestions.py revise <row-# or job-id> <qa-id> \"instruction\"\n"
              "    python appquestions.py delete <row-# or job-id> <qa-id>\n")
        sys.exit(1)

    if args[0] == "list":
        _list(args[1:])
    elif args[0] == "revise":
        _revise(args[1:])
    elif args[0] == "delete":
        _delete(args[1:])
    else:
        if len(args) < 2:
            print("\n  Usage: python appquestions.py <row-# or job-id> \"<question>\" [--notes \"...\"] [--limit \"...\"]\n")
            sys.exit(1)
        _draft(args)


if __name__ == "__main__":
    main()
