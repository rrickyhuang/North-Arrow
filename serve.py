"""Local web cockpit for browsing jobs and managing application status.

    python serve.py            run the UI at http://127.0.0.1:5001

A live, write-capable companion to the read-only `report.html`. Phase 1: browse
the full DB with the same search/filter controls as the report, and change a
job's application stage / interested / dismissed state inline (HTMX swaps just
the affected card, no page reload). Reuses the existing renderers (`html_render`)
and DB logic (`db`) — this module is mostly wiring.

Mirrors the sibling apartment-hunter app's conventions (Flask + HTMX, partial
swaps, localhost-only, factory + argparse). Runs on port 5001 so it can sit
alongside the apartment hunter (5000) at the same time. See WEB_UI_PLAN.md.
"""
from __future__ import annotations

import argparse
import logging

from flask import Flask, abort, request
from markupsafe import escape

import config
import coverletter
import db
import html_render
import logutil

log = logging.getLogger("serve")

# Stage buttons offered on each card, in pipeline order. None == clear/not-applied.
_STAGE_CHOICES = ("applied", "interviewing", "offer", "denied", "withdrawn")

_BTN = ("display:inline-block;padding:3px 9px;margin:0 4px 4px 0;border-radius:6px;"
        "border:1px solid #d0d7de;background:#fff;color:#24292f;font-size:12px;"
        "cursor:pointer;font-family:inherit;")
_BTN_ON = _BTN + "background:#0969da;color:#fff;border-color:#0969da;"

# Shared <head> extras: htmx + the indicator CSS the cover-letter spinner uses.
_HEAD = ('<script src="https://unpkg.com/htmx.org@1.9.12"></script>'
         '<style>.htmx-indicator{display:none}'
         '.htmx-request .htmx-indicator,.htmx-request.htmx-indicator{display:inline}</style>')


def _actions_html(job) -> str:
    """The inline control bar appended inside a card: one button per stage plus
    interested / dismiss toggles, and a lazily-loaded cover-letter panel. Each
    posts to a route that flips state and returns the freshly rendered card,
    which HTMX swaps in place."""
    hx = ('hx-target="#job-{id}" hx-swap="outerHTML" '
          'hx-post="/job/{id}/{path}"').format
    stage_btns = "".join(
        f'<button style="{_BTN_ON if job.stage == s else _BTN}" '
        f'{hx(id=job.id, path="stage/" + s)}>{s.capitalize()}</button>'
        for s in _STAGE_CHOICES
    )
    clear = (f'<button style="{_BTN}" {hx(id=job.id, path="stage/clear")}>Clear</button>'
             if job.stage else "")
    interested = (f'<button style="{_BTN_ON if (job.saved and not job.stage) else _BTN}" '
                  f'{hx(id=job.id, path="interested")}>Interested</button>')
    dismiss = (f'<button style="{_BTN_ON if job.dismissed else _BTN}" '
               f'{hx(id=job.id, path="dismiss")}>Dismiss</button>')

    has_letter = coverletter.letter_path(job).exists()
    cl_label = "✓ Cover letter" if has_letter else "Cover letter"
    cl_btn = (f'<button style="{_BTN_ON if has_letter else _BTN}" '
              f'hx-get="/job/{job.id}/coverletter" hx-target="#cl-{job.id}" '
              f'hx-swap="innerHTML">{cl_label}</button>')

    notes_html = (
        '<div style="margin-top:8px;">'
        f'<textarea name="notes" rows="2" placeholder="Notes: recruiter, dates, '
        f'follow-ups, interview prep…" style="{_CL_INPUT}" '
        f'hx-post="/job/{job.id}/notes" hx-trigger="keyup changed delay:800ms" '
        f'hx-target="#notes-status-{job.id}" hx-swap="innerHTML">'
        f'{escape(job.notes or "")}</textarea>'
        f'<span id="notes-status-{job.id}" style="font-size:11px;color:#57606a;'
        f'margin-left:6px;"></span></div>'
    )
    return (
        '<div style="margin-top:10px;padding-top:10px;border-top:1px solid #eaeef2;">'
        f'{stage_btns}{clear}'
        '<span style="display:inline-block;width:12px;"></span>'
        f'{interested}{dismiss}'
        '<span style="display:inline-block;width:12px;"></span>'
        f'{cl_btn}'
        f'{notes_html}'
        f'<div id="cl-{job.id}"></div></div>'
    )


# ── Cover-letter panel (lazily loaded into #cl-<id> on demand) ────────────────
_CL_INPUT = ("width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid "
             "#d0d7de;border-radius:6px;font-size:13px;font-family:inherit;")
_CL_WORKING = ('<span class="htmx-indicator" style="color:#8250df;font-size:12px;'
               'margin-left:8px;">working… (up to ~60s)</span>')


def _cl_draft_form(job) -> str:
    return (
        '<div style="margin-top:10px;background:#f6f8fa;border:1px solid #d0d7de;'
        'border-radius:8px;padding:12px;">'
        f'<form hx-post="/job/{job.id}/coverletter/draft" hx-target="#cl-{job.id}" '
        f'hx-swap="innerHTML" hx-disabled-elt="find button">'
        f'<textarea name="notes" rows="2" placeholder="Optional: specific points '
        f'to work into this letter…" style="{_CL_INPUT}"></textarea>'
        f'<div style="margin-top:6px;"><button style="{_BTN_ON}">Draft cover letter'
        f'</button>{_CL_WORKING}</div></form></div>'
    )


def _cl_view(job, body: str) -> str:
    from markupsafe import escape
    path = coverletter.letter_path(job)
    return (
        '<div style="margin-top:10px;background:#f6f8fa;border:1px solid #d0d7de;'
        'border-radius:8px;padding:12px;">'
        f'<div style="font-size:12px;color:#57606a;margin-bottom:6px;">saved: '
        f'<code style="user-select:all;">{escape(str(path))}</code></div>'
        f'<pre style="white-space:pre-wrap;font-family:-apple-system,Segoe UI,Roboto,'
        f'sans-serif;font-size:13px;line-height:1.5;color:#24292f;margin:0 0 10px;'
        f'max-height:420px;overflow:auto;">{escape(body)}</pre>'
        f'<form hx-post="/job/{job.id}/coverletter/revise" hx-target="#cl-{job.id}" '
        f'hx-swap="innerHTML" hx-disabled-elt="find button">'
        f'<input name="instruction" placeholder="Describe a change to make…" '
        f'style="{_CL_INPUT}">'
        f'<div style="margin-top:6px;"><button style="{_BTN_ON}">Revise</button>'
        f'{_CL_WORKING}</div></form></div>'
    )


def _cl_panel(job, error: str = "") -> str:
    from markupsafe import escape
    banner = (f'<div style="margin-top:10px;background:#ffebe9;border:1px solid '
              f'#ff818266;border-radius:8px;padding:10px;color:#82071e;font-size:13px;">'
              f'{escape(error)}</div>') if error else ""
    body = coverletter.letter_body(job)
    return banner + (_cl_view(job, body) if body is not None else _cl_draft_form(job))


def _stale_days() -> int:
    return config.load_config().get("delivery", {}).get(
        "stale_after_days", html_render.STALE_AFTER_DAYS)


def _card(job, row_no: int) -> str:
    return html_render.job_card(
        job, row_no, full_desc=True, report=True, row_no=row_no,
        dom_id=f"job-{job.id}", actions_html=_actions_html(job), stale_days=_stale_days())


# ── Pipeline board (kanban) ──────────────────────────────────────────────────
# Columns across the application lifecycle. Each is (key, label, predicate).
_BOARD_COLUMNS = [
    ("interested", "Interested", lambda j: bool(j.saved) and not j.stage),
    ("applied", "Applied", lambda j: j.stage == "applied"),
    ("interviewing", "Interviewing", lambda j: j.stage == "interviewing"),
    ("offer", "Offer", lambda j: j.stage == "offer"),
    ("closed", "Closed", lambda j: j.stage in ("denied", "withdrawn")),
]
# Where each card can move to. "interested" = shortlist w/o a stage; "remove"
# = drop off the board entirely (clear stage + interested).
_BOARD_MOVES = [
    ("interested", "★ Interested"), ("applied", "Applied"),
    ("interviewing", "Interviewing"), ("offer", "Offer"),
    ("denied", "Denied"), ("withdrawn", "Withdrawn"), ("remove", "Remove"),
]
_COL_STYLE = ("flex:1;min-width:230px;background:#f6f8fa;border:1px solid #d0d7de;"
              "border-radius:10px;padding:10px;margin:0 6px 12px;")


def _board_card(job) -> str:
    since = ""
    if job.stage_at:
        since = f' · since {escape(job.stage_at.date().isoformat())}'
    elif job.stage in ("denied", "withdrawn"):
        since = f' · {escape(job.stage)}'
    moves = "".join(
        f'<button style="{_BTN}padding:2px 6px;font-size:11px;" '
        f'hx-post="/board/job/{job.id}/move/{key}" hx-target="#board" '
        f'hx-swap="innerHTML">{label}</button>'
        for key, label in _BOARD_MOVES if key != job.stage
    )
    return (
        '<div style="background:#fff;border:1px solid #d0d7de;border-radius:8px;'
        'padding:9px 11px;margin-bottom:9px;">'
        f'<div style="font-size:14px;font-weight:600;line-height:1.3;">'
        f'<a href="{escape(job.url)}" style="color:#0969da;text-decoration:none;">'
        f'{escape(job.title)}</a></div>'
        f'<div style="color:#57606a;font-size:12px;margin:1px 0 6px;">'
        f'{escape(job.company or "Unknown")}{since}</div>'
        f'<div>{moves}</div></div>'
    )


def _board_html(conn) -> str:
    jobs = db.query(conn, include_dismissed=True, include_duplicates=True,
                    order_by="score DESC")
    cols = ""
    for _key, label, pred in _BOARD_COLUMNS:
        members = [j for j in jobs if pred(j)]
        cards = "".join(_board_card(j) for j in members) or (
            '<div style="color:#8b949e;font-size:12px;padding:6px;">—</div>')
        cols += (
            f'<div style="{_COL_STYLE}">'
            f'<div style="font-weight:600;font-size:13px;color:#24292f;margin-bottom:8px;">'
            f'{label} <span style="color:#8b949e;font-weight:400;">({len(members)})</span></div>'
            f'{cards}</div>'
        )
    return f'<div style="display:flex;flex-wrap:wrap;align-items:flex-start;">{cols}</div>'


def _nav(active: str) -> str:
    def link(href, label, key):
        on = key == active
        style = ("padding:6px 12px;border-radius:6px;text-decoration:none;font-size:14px;"
                 + ("background:#0969da;color:#fff;" if on else "color:#0969da;"))
        return f'<a href="{href}" style="{style}">{label}</a>'
    return ('<div style="margin-bottom:16px;display:flex;gap:8px;'
            'font-family:-apple-system,Segoe UI,Roboto,sans-serif;">'
            f'{link("/", "List", "list")}{link("/board", "Pipeline board", "board")}</div>')


def create_app(db_path=db.DB_PATH) -> Flask:
    app = Flask(__name__)
    conn0 = db.connect(db_path)
    db.init_db(conn0)
    conn0.close()

    def get_conn():
        return db.connect(db_path)

    def _job_or_404(conn, job_id: str):
        job = db.get(conn, job_id)
        if not job:
            abort(404)
        return job

    def _row_no(conn, job_id: str) -> int:
        """Position of a job in the full ranked list — the same '#' the report
        and show.py use, so it stays consistent across a status change."""
        jobs = db.query(conn, include_dismissed=True, include_duplicates=True,
                        order_by="score DESC")
        for i, j in enumerate(jobs, 1):
            if j.id == job_id:
                return i
        return 0

    @app.route("/")
    def index():
        cfg = config.load_config()
        conn = get_conn()
        jobs = db.query(conn, include_dismissed=True, include_duplicates=True,
                        order_by="score DESC")
        live = [j for j in jobs if not j.disqualifier and not j.duplicate_of]
        dead = sum(1 for j in jobs if j.disqualifier)
        dup = sum(1 for j in jobs if j.duplicate_of)
        intro = (f"{len(live)} scored · {dead} disqualified · {dup} duplicates — "
                 "click a status on any card to update it instantly")
        cards = "".join(_card(j, i) for i, j in enumerate(jobs, 1))
        body = (_nav("list")
                + html_render._filter_bar(jobs)
                + f'<div id="cards">{cards}</div>'
                + html_render._SCRIPT)
        conn.close()
        return html_render.page("JobHunter — Cockpit", intro, body, head_extra=_HEAD)

    @app.route("/board")
    def board():
        conn = get_conn()
        body = _nav("board") + f'<div id="board">{_board_html(conn)}</div>'
        conn.close()
        intro = "Your application pipeline — click a button on a card to move it."
        return html_render.page("JobHunter — Pipeline", intro, body, head_extra=_HEAD)

    @app.route("/board/job/<job_id>/move/<target>", methods=["POST"])
    def board_move(job_id, target):
        conn = get_conn()
        job = _job_or_404(conn, job_id)
        if target == "interested":
            db.set_stage(conn, job_id, None)
            db.set_state(conn, job_id, saved=True)
        elif target == "remove":
            db.set_stage(conn, job_id, None)
            db.set_state(conn, job_id, saved=False)
        elif target in db.STAGES:
            db.set_stage(conn, job_id, target)
        else:
            conn.close()
            abort(400)
        log.info("board move -> %s on %s (%s @ %s)", target, job_id, job.title, job.company)
        html = _board_html(conn)
        conn.close()
        return html

    @app.route("/job/<job_id>/stage/<stage>", methods=["POST"])
    def set_stage(job_id, stage):
        if stage != "clear" and stage not in db.STAGES:
            abort(400)
        conn = get_conn()
        job = _job_or_404(conn, job_id)
        new_stage = None if stage == "clear" else stage
        db.set_stage(conn, job_id, new_stage)
        log.info("stage -> %s on %s (%s @ %s)", new_stage or "cleared",
                 job_id, job.title, job.company)
        job = db.get(conn, job_id)
        row_no = _row_no(conn, job_id)
        conn.close()
        return _card(job, row_no)

    @app.route("/job/<job_id>/interested", methods=["POST"])
    def toggle_interested(job_id):
        conn = get_conn()
        job = _job_or_404(conn, job_id)
        db.set_state(conn, job_id, saved=not job.saved)
        log.info("interested -> %s on %s (%s @ %s)", not job.saved,
                 job_id, job.title, job.company)
        job = db.get(conn, job_id)
        row_no = _row_no(conn, job_id)
        conn.close()
        return _card(job, row_no)

    @app.route("/job/<job_id>/dismiss", methods=["POST"])
    def toggle_dismiss(job_id):
        conn = get_conn()
        job = _job_or_404(conn, job_id)
        db.set_state(conn, job_id, dismissed=not job.dismissed)
        log.info("dismissed -> %s on %s (%s @ %s)", not job.dismissed,
                 job_id, job.title, job.company)
        job = db.get(conn, job_id)
        row_no = _row_no(conn, job_id)
        conn.close()
        return _card(job, row_no)

    @app.route("/job/<job_id>/notes", methods=["POST"])
    def save_notes(job_id):
        conn = get_conn()
        _job_or_404(conn, job_id)
        db.set_notes(conn, job_id, request.form.get("notes", ""))
        conn.close()
        return '<span style="color:#1a7f37;">saved ✓</span>'

    @app.route("/job/<job_id>/coverletter")
    def cl_panel(job_id):
        conn = get_conn()
        job = _job_or_404(conn, job_id)
        conn.close()
        return _cl_panel(job)

    @app.route("/job/<job_id>/coverletter/draft", methods=["POST"])
    def cl_draft(job_id):
        conn = get_conn()
        job = _job_or_404(conn, job_id)
        conn.close()
        notes = request.form.get("notes", "")
        try:
            path = coverletter.draft_letter(job, config.load_config(), notes)
        except coverletter.CoverLetterError as e:
            log.warning("cover-letter draft failed for %s: %s", job_id, e)
            return _cl_panel(job, error=str(e))
        log.info("drafted cover letter for %s (%s @ %s) -> %s",
                 job_id, job.title, job.company, path)
        return _cl_panel(job)

    @app.route("/job/<job_id>/coverletter/revise", methods=["POST"])
    def cl_revise(job_id):
        conn = get_conn()
        job = _job_or_404(conn, job_id)
        conn.close()
        instruction = request.form.get("instruction", "").strip()
        if not instruction:
            return _cl_panel(job)
        try:
            coverletter.revise_letter(job, instruction)
        except coverletter.CoverLetterError as e:
            log.warning("cover-letter revise failed for %s: %s", job_id, e)
            return _cl_panel(job, error=str(e))
        log.info("revised cover letter for %s (%s @ %s): %s",
                 job_id, job.title, job.company, instruction)
        return _cl_panel(job)

    return app


def main() -> None:
    logutil.setup_logging()
    ap = argparse.ArgumentParser(description="JobHunter web cockpit.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5001)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    app = create_app()
    print(f"\n  JobHunter cockpit -> http://{args.host}:{args.port}\n")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
