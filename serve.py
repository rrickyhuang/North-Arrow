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
import threading
import time

from flask import Flask, abort, make_response, request
from markupsafe import escape

import config
import coverletter
import db
import html_render
import logutil

log = logging.getLogger("serve")

# ── Scrape-on-demand ───────────────────────────────────────────────────────
# A single background scrape may run at a time, kicked off from the cockpit
# instead of only via the scheduled `python scrape.py`. The page polls
# /scrape/status to render the control's current state. Mirrors the sibling
# apartment-hunter app's web.py.
_scrape_state = {
    "running": False,
    "started_at": None,   # epoch seconds when the current/last scrape began
    "finished_at": None,
    "stats": None,         # last run's scrape.run() stats dict
    "error": None,          # last run's error message, if any
    "refresh_pending": False,  # consumed once by /scrape/status to fire HX-Refresh
}
_scrape_lock = threading.Lock()


def _run_scrape_thread(cfg: dict) -> None:
    """Runs the full scrape.py pipeline (scrape -> score -> dedup -> HTML
    report -> DB backup) exactly as `python scrape.py` does, just triggered
    from the web UI instead of the CLI/scheduler."""
    import scrape
    try:
        enabled = [s for s, on in cfg.get("sources", {}).items() if on]
        sources = [s for s in enabled if s in scrape.SOURCES] or list(scrape.SOURCES.keys())
        stats = scrape.run(sources, cfg)
        scrape._refresh_html_report(cfg)
        db.backup_db()
        with _scrape_lock:
            _scrape_state["stats"] = stats
            _scrape_state["error"] = None
    except Exception as e:  # noqa: BLE001 — surfaced to the UI, not fatal
        log.exception("on-demand scrape failed")
        with _scrape_lock:
            _scrape_state["error"] = str(e)
    finally:
        with _scrape_lock:
            _scrape_state["running"] = False
            _scrape_state["finished_at"] = time.time()
            _scrape_state["refresh_pending"] = True


def _trigger_scrape() -> bool:
    """Start a scrape thread if none is running. Returns True if started."""
    with _scrape_lock:
        if _scrape_state["running"]:
            return False
        _scrape_state["running"] = True
        _scrape_state["started_at"] = time.time()
        _scrape_state["error"] = None
        _scrape_state["refresh_pending"] = False
    threading.Thread(target=_run_scrape_thread, args=(config.load_config(),),
                     daemon=True).start()
    return True


def _scrape_view_state() -> dict:
    with _scrape_lock:
        s = dict(_scrape_state)
    s["elapsed"] = int(time.time() - s["started_at"]) if s["running"] and s["started_at"] else None
    return s


def _scrape_control_html(state: dict) -> str:
    if state["running"]:
        elapsed = state["elapsed"] or 0
        return (
            f'<span id="scrape-ctl" hx-get="/scrape/status" hx-trigger="every 2s" '
            f'hx-target="#scrape-ctl" hx-swap="outerHTML" '
            f'style="font-size:13px;color:#57606a;">'
            f'⏳ scraping… {elapsed}s (can take several minutes)</span>'
        )
    note = ""
    if state["error"]:
        note = (f'<span style="color:#cf222e;font-size:12px;margin-left:8px;">'
                f'last run failed: {escape(state["error"][:200])}</span>')
    elif state["stats"]:
        st = state["stats"]
        note = (f'<span style="color:#1a7f37;font-size:12px;margin-left:8px;">'
                f'last run: +{st["new"]} new, {st["updated"]} updated, '
                f'{st["duplicates"]} duplicates</span>')
    return (
        f'<span id="scrape-ctl">'
        f'<button style="{_BTN}" hx-post="/scrape" hx-target="#scrape-ctl" '
        f'hx-swap="outerHTML">Scrape now</button>{note}</span>'
    )

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

# Board-only <head> extras: drag-and-drop to move a card between columns,
# alongside (not instead of) the per-card move buttons — buttons still cover
# mobile (no native drag) and the ambiguous Closed column (denied/withdrawn).
# Global functions defined once in the outer page, not in the HTMX-swapped
# #board fragment, so they survive every card move.
_BOARD_HEAD = (
    '<style>.board-dropok{background:#eef6ff !important;'
    'outline:2px dashed #54aeff;outline-offset:-2px;}</style>'
    '<script>'
    "function boardDragStart(ev,id){ev.dataTransfer.setData('text/plain',id);"
    "ev.dataTransfer.effectAllowed='move';}"
    'function boardAllowDrop(ev){ev.preventDefault();}'
    "function boardDrop(ev,key){ev.preventDefault();"
    "ev.currentTarget.classList.remove('board-dropok');"
    "var id=ev.dataTransfer.getData('text/plain');if(!id)return;"
    "htmx.ajax('POST','/board/job/'+id+'/move/'+key,{target:'#board',swap:'innerHTML'});}"
    '</script>'
)


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


def _cl_close_btn(job) -> str:
    return (f'<button type="button" style="{_BTN}float:right;" '
            f'onclick="document.getElementById(\'cl-{job.id}\').innerHTML=\'\'">Hide</button>')


def _cl_draft_form(job) -> str:
    return (
        '<div style="margin-top:10px;background:#f6f8fa;border:1px solid #d0d7de;'
        'border-radius:8px;padding:12px;">'
        f'{_cl_close_btn(job)}'
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
        f'{_cl_close_btn(job)}'
        f'<div style="font-size:12px;color:#57606a;margin-bottom:6px;">saved: '
        f'<code style="user-select:all;word-break:break-all;overflow-wrap:anywhere;">'
        f'{escape(str(path))}</code></div>'
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
# Columns across the application lifecycle: (key, label, predicate, drop_target).
# drop_target is the /board/job/<id>/move/<target> value a card dropped into
# this column should move to, or None if the column is ambiguous as a drag
# target (Closed covers both denied/withdrawn — use the per-card buttons).
_BOARD_COLUMNS = [
    ("interested", "Interested", lambda j: bool(j.saved) and not j.stage, "interested"),
    ("applied", "Applied", lambda j: j.stage == "applied", "applied"),
    ("interviewing", "Interviewing", lambda j: j.stage == "interviewing", "interviewing"),
    ("offer", "Offer", lambda j: j.stage == "offer", "offer"),
    ("closed", "Closed", lambda j: j.stage in ("denied", "withdrawn"), None),
]
# Where each card can move to. "interested" = shortlist w/o a stage; "remove"
# = drop off the board entirely (clear stage + interested).
_BOARD_MOVES = [
    ("interested", "★ Interested"), ("applied", "Applied"),
    ("interviewing", "Interviewing"), ("offer", "Offer"),
    ("denied", "Denied"), ("withdrawn", "Withdrawn"), ("remove", "Remove"),
]
# Fixed-width columns (no grow/shrink) so they sit side by side like a real
# kanban; the row itself scrolls horizontally when they don't all fit (desktop
# overflow / mobile).
_COL_STYLE = ("flex:0 0 264px;background:#f6f8fa;border:1px solid #d0d7de;"
              "border-radius:10px;padding:10px;")


def _followup_days() -> int:
    return config.load_config().get("delivery", {}).get(
        "follow_up_after_days", html_render.FOLLOW_UP_AFTER_DAYS)


def _board_card(job, followup_days: int) -> str:
    since = ""
    days = html_render.days_in_stage(job)
    if days is not None:
        ago = "today" if days == 0 else f"{days}d ago"
        since = f' · {ago}'
    elif job.stage in ("denied", "withdrawn"):
        since = f' · {escape(job.stage)}'
    overdue = html_render.is_overdue_followup(job, followup_days)
    followup = (f'<div style="color:#9a6700;font-size:11px;font-weight:600;'
                f'margin:2px 0 4px;">⚠ follow up — {days}d with no update</div>'
                if overdue else "")
    moves = "".join(
        f'<button style="{_BTN}padding:2px 6px;font-size:11px;" '
        f'hx-post="/board/job/{job.id}/move/{key}" hx-target="#board" '
        f'hx-swap="innerHTML">{label}</button>'
        for key, label in _BOARD_MOVES if key != job.stage
    )
    border = "border:1px solid #d4a72c;" if overdue else "border:1px solid #d0d7de;"
    return (
        f'<div draggable="true" ondragstart="boardDragStart(event, \'{job.id}\')" '
        f'style="background:#fff;{border}border-radius:8px;cursor:grab;'
        'padding:9px 11px;margin-bottom:9px;">'
        f'<div style="font-size:14px;font-weight:600;line-height:1.3;">'
        f'<a href="{escape(job.url)}" style="color:#0969da;text-decoration:none;">'
        f'{escape(job.title)}</a></div>'
        f'<div style="color:#57606a;font-size:12px;margin:1px 0 6px;">'
        f'{escape(job.company or "Unknown")}{since}</div>'
        f'{followup}'
        f'<div>{moves}</div></div>'
    )


def _board_html(conn) -> str:
    jobs = db.query(conn, include_dismissed=True, include_duplicates=True,
                    order_by="score DESC")
    followup_days = _followup_days()
    cols = ""
    for _key, label, pred, drop_target in _BOARD_COLUMNS:
        members = [j for j in jobs if pred(j)]
        overdue_n = sum(1 for j in members if html_render.is_overdue_followup(j, followup_days))
        overdue_badge = (f' <span style="color:#9a6700;font-weight:600;">⚠{overdue_n}</span>'
                         if overdue_n else "")
        cards = "".join(_board_card(j, followup_days) for j in members) or (
            '<div style="color:#8b949e;font-size:12px;padding:6px;">—</div>')
        # Draggable-drop columns get handlers + a data-drop-target attribute
        # dragenter/leave toggle a highlight on (styled via a class, not the
        # inline style attribute, so it doesn't clobber it).
        drop_attrs = (
            f'ondragover="boardAllowDrop(event)" ondrop="boardDrop(event, \'{drop_target}\')" '
            f'ondragenter="this.classList.add(\'board-dropok\')" '
            f'ondragleave="this.classList.remove(\'board-dropok\')"'
        ) if drop_target else ""
        cols += (
            f'<div style="{_COL_STYLE}" {drop_attrs}>'
            f'<div style="font-weight:600;font-size:13px;color:#24292f;margin-bottom:8px;">'
            f'{label} <span style="color:#8b949e;font-weight:400;">({len(members)})</span>'
            f'{overdue_badge}</div>'
            f'{cards}</div>'
        )
    return (
        '<div style="display:flex;flex-wrap:nowrap;align-items:flex-start;gap:12px;'
        'overflow-x:auto;padding-bottom:10px;">'
        f'{cols}</div>'
    )


def _nav(active: str) -> str:
    def link(href, label, key):
        on = key == active
        style = ("padding:6px 12px;border-radius:6px;text-decoration:none;font-size:14px;"
                 + ("background:#0969da;color:#fff;" if on else "color:#0969da;"))
        return f'<a href="{href}" style="{style}">{label}</a>'
    return ('<div style="margin-bottom:16px;display:flex;gap:8px;align-items:center;'
            'font-family:-apple-system,Segoe UI,Roboto,sans-serif;">'
            f'{link("/", "List", "list")}{link("/board", "Pipeline board", "board")}'
            '<span style="margin-left:auto;">'
            f'{_scrape_control_html(_scrape_view_state())}</span></div>')


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
        intro = "Your application pipeline — drag a card (or click a button) to move it."
        # Wider container so the 5 columns sit side by side on desktop; the board
        # row scrolls horizontally when they don't fit (e.g. on a phone).
        return html_render.page("JobHunter — Pipeline", intro, body,
                                head_extra=_HEAD + _BOARD_HEAD, max_width=1400)

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

    @app.route("/scrape", methods=["POST"])
    def scrape_start():
        """Kick off a background scrape if one isn't already running."""
        _trigger_scrape()
        return _scrape_control_html(_scrape_view_state())

    @app.route("/scrape/status")
    def scrape_status():
        state = _scrape_view_state()
        # Consume refresh_pending — fires HX-Refresh exactly once, reloading
        # the current page (list or board) with the freshly scraped jobs.
        should_refresh = False
        with _scrape_lock:
            if _scrape_state["refresh_pending"]:
                _scrape_state["refresh_pending"] = False
                should_refresh = True
        resp = make_response(_scrape_control_html(state))
        if should_refresh:
            resp.headers["HX-Refresh"] = "true"
        return resp

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
    # threaded so a slow request (a ~40s cover-letter draft shelling out to the
    # claude CLI) doesn't freeze the whole UI for other tabs/actions meanwhile.
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
