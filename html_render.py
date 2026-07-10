"""Shared HTML rendering for the email digest and the browser report.

Uses inline styles (email clients strip <style> blocks unpredictably), kept
simple so both Gmail and a normal browser render it the same way. The browser
report additionally gets data-* attributes + a small script for client-side
search/filtering (ignored by email clients).
"""
from __future__ import annotations

import html
import re
from datetime import date, datetime, timezone

# ── North Arrow brand ────────────────────────────────────────────────────
# Single locked-in scheme (navy/cyanotype-blueprint) — deliberately no light
# variant and no theme-switching. Structural/chrome colors — headings,
# borders, links — are kept separate from the functional status colors below,
# which encode meaning (qualification tier, pipeline stage) and must stay
# visually distinct from brand chrome.
PAPER = "#10161f"
PAPER_RAISED = "#161d29"
INK = "#e7e2d3"
BLUEPRINT = "#7fa8e8"
BLUEPRINT_BRIGHT = "#9bc0ff"
GRID = "#27384e"
GRID_FAINT = "#1c2836"
MUTED = "#8b93a6"
MUTED_LIGHT = "#6b7385"
TINT = "#233a56"  # active/highlight background, e.g. selected filter chips

FONT_SANS = "-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif"
FONT_MONO = "ui-monospace,SF Mono,Cascadia Mono,Consolas,monospace"

# A monoline pen-nib favicon (the north-up mark). Deliberately a fixed deep
# navy rather than the on-page BLUEPRINT — favicons sit in the browser's own
# (usually light) tab bar, not on our dark page background, so it needs its
# own contrast. Falls back to a solid silhouette since the outline strokes
# are too thin to render at 16x16.
FAVICON_LINK = (
    '<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 '
    'viewBox=%270 0 100 100%27%3E%3Cpolygon points=%2750,8 68,44 50,90 32,44%27 '
    'fill=%27%231e3f73%27/%3E%3C/svg%3E">'
)

# A posting is "stale" once its source hasn't listed it for this many days.
# scraped_at is refreshed on every re-scrape (see db.upsert), so it doubles as a
# "last seen" timestamp. Overridable via config delivery.stale_after_days.
STALE_AFTER_DAYS = 30


def is_stale(job, days: int = STALE_AFTER_DAYS) -> bool:
    """True if a posting probably closed: not seen on its source in `days` days
    AND not something you're actively tracking (no pipeline stage, not marked
    interested). Those are never stale — you decided to keep them."""
    if job.stage or job.saved or not job.scraped_at:
        return False
    last_seen = job.scraped_at
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last_seen).days > days


# A stage this many days old with no forward movement is worth a nudge to
# follow up. Overridable via config delivery.follow_up_after_days.
FOLLOW_UP_AFTER_DAYS = 7


def days_in_stage(job) -> int | None:
    """Days since job.stage_at, or None if there's no stage/timestamp."""
    if not job.stage_at:
        return None
    stage_at = job.stage_at
    if stage_at.tzinfo is None:
        stage_at = stage_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - stage_at).days


def is_overdue_followup(job, days: int = FOLLOW_UP_AFTER_DAYS) -> bool:
    """True if a job has sat 'applied' or 'interviewing' this many days with
    no forward movement — a nudge to check in, not a hard rule. Offer/denied/
    withdrawn/interested are never overdue: nothing to follow up on."""
    if job.stage not in ("applied", "interviewing"):
        return False
    n = days_in_stage(job)
    return n is not None and n >= days


_QUAL_COLOR = {
    "qualified": "#3fae6e",
    "stretch": "#d9a44c",
    "reach": "#d97f52",
    "overqualified": "#8aa2b4",
}

# Digest grouping: cards are bucketed by how applyable they are (enrichment's
# qualification verdict), most-actionable first, so the reader sees "apply now"
# before "long shots" instead of one flat score sort. Jobs stay score-ordered
# within each bucket. Anything with no qualification verdict falls to _OTHER.
QUAL_BUCKETS = [
    ("Apply now", {"qualified", "overqualified"}),
    ("Stretch — worth a shot", {"stretch"}),
    ("Reach — long shots", {"reach"}),
]
_OTHER_BUCKET = "Other matches"

def is_starred(job) -> bool:
    """Saved/interested, and not yet moved into a pipeline stage (once staged,
    the stage badge takes over — see job_card)."""
    return bool(job.saved) and not job.stage


def lead_sentence(text: str) -> tuple[str, str]:
    """Split prose into (first sentence, remainder) so the digest can lead with
    the verdict and truncate/tuck the rest. Tiny leading fragments (abbreviations
    like 'e.g.') are merged forward so we don't cut mid-thought."""
    text = (text or "").strip()
    if not text:
        return "", ""
    parts = re.split(r"(?<=[.!?])\s+", text)
    lead, i = parts[0], 1
    while i < len(parts) and len(lead) < 60:
        lead += " " + parts[i]
        i += 1
    return lead, " ".join(parts[i:]).strip()


def group_by_qual(jobs: list) -> list[tuple[str, list]]:
    """Partition jobs into the QUAL_BUCKETS (order preserved within each), then
    an Other bucket for unclassified ones. Empty buckets are dropped."""
    groups = []
    for label, quals in QUAL_BUCKETS:
        members = [j for j in jobs if j.qualification in quals]
        if members:
            groups.append((label, members))
    known = {q for _, quals in QUAL_BUCKETS for q in quals}
    other = [j for j in jobs if j.qualification not in known]
    if other:
        groups.append((_OTHER_BUCKET, other))
    return groups


def _section_html(label: str, jobs: list, card_fn) -> str:
    """One heading + its cards, no further grouping. Empty sections render
    nothing so callers can pass possibly-empty lists unconditionally."""
    if not jobs:
        return ""
    return (
        f'<h3 style="margin:20px 0 10px;font-size:15px;color:{INK};'
        f'border-bottom:1px solid {GRID};padding-bottom:6px;">'
        f'{_esc(label)} <span style="color:{MUTED_LIGHT};font-weight:400;">'
        f'({len(jobs)})</span></h3>'
        + "".join(card_fn(j) for j in jobs)
    )


def _is_open(job) -> bool:
    return not job.stage and not job.disqualifier and not job.duplicate_of


def _is_screened_out(job) -> bool:
    return bool(job.disqualifier or job.duplicate_of)


def bucketed_cards_html(jobs: list, card_fn) -> str:
    """Render the open (no stage, not disqualified/duplicate) subset of `jobs`
    as cards grouped into the same apply-now/stretch/reach buckets as the
    email digest (via group_by_qual), each under a heading with a count, so
    apply-now jobs float above stretch/reach/unclassified ones instead of a
    flat score sort. `card_fn(job)` renders one card's HTML.

    Takes the *full* job list and filters internally — rather than requiring
    the caller to pre-split into "open" first — so this, `staged_cards_html`,
    and the disqualified/duplicate section always partition the same source
    list into non-overlapping, exhaustive groups by construction. A caller
    that pre-filters before calling this is harmless (the filter is
    idempotent), but isn't required and shouldn't be relied on."""
    open_jobs = [j for j in jobs if _is_open(j)]
    return "".join(
        _section_html(label, members, card_fn) for label, members in group_by_qual(open_jobs)
    )


def staged_cards_html(jobs: list, card_fn) -> str:
    """Render the staged (has a stage, not disqualified/duplicate) subset of
    `jobs`, grouped like the digest's pipeline tracker: active stages
    (offer/interviewing/applied) by stage, most-advanced first, then a flat
    Closed section for denied/withdrawn. Kept separate from
    `bucketed_cards_html` so in-progress jobs don't get re-mixed into the
    apply-now/stretch/reach buckets.

    Unlike `split_by_stage` (digest-specific: drops denied/withdrawn
    entirely, since the digest only tracks active applications), this keeps
    every staged job — the cockpit/report are the full inventory, so a
    denied/withdrawn job still needs a section to render into, even if
    that section stays hidden by default. Do not feed this function
    `split_by_stage`'s `tracked` output — it already excludes denied/
    withdrawn, so this function's own Closed split would always come up
    empty."""
    staged = [j for j in jobs if j.stage and not _is_screened_out(j)]
    tracked = [j for j in staged if j.stage in ACTIVE_STAGES]
    closed = [j for j in staged if j.stage in ("denied", "withdrawn")]
    parts = [_section_html(label, members, card_fn) for _st, label, members in group_by_stage(tracked)]
    parts.append(_section_html("Closed", closed, card_fn))
    return "".join(parts)


# Application-pipeline stages that still count as "in progress": shown in a
# compact tracker at the bottom of the digest and excluded from the "apply now"
# groupings, so a job you've already applied to never resurfaces as a
# suggestion. Ordered most-advanced first. Terminal stages (denied/withdrawn)
# are dropped from the digest entirely — they're closed.
ACTIVE_STAGES = ("offer", "interviewing", "applied")
# Active stages drive the digest tracker; the terminal ones only ever show as a
# per-card badge in the full report, never in the tracker.
STAGE_LABEL = {"applied": "Applied", "interviewing": "Interviewing", "offer": "Offer",
               "denied": "Denied", "withdrawn": "Withdrawn"}
# "Applied" deliberately reuses the brand blue — applying is the one action
# the whole tool is built around. The rest are harmonized to the same
# saturation/lightness band as the qualification colors above, just a
# different hue per stage.
_STAGE_COLOR = {"applied": BLUEPRINT_BRIGHT, "interviewing": "#a98ce8", "offer": "#3fae6e",
                "denied": "#d65f70", "withdrawn": "#9aa0ac"}


def split_by_stage(jobs: list) -> tuple[list, list]:
    """(open_jobs, tracked): open_jobs have no application stage set (eligible
    for the shortlist); tracked are in an active pipeline stage (for the
    tracker). Terminal stages (denied/withdrawn) fall out of both."""
    open_jobs = [j for j in jobs if not j.stage]
    tracked = [j for j in jobs if j.stage in ACTIVE_STAGES]
    return open_jobs, tracked


def group_by_stage(tracked: list) -> list[tuple[str, str, list]]:
    """Partition tracked jobs by stage into (stage, label, members), most
    advanced first. Empty stages dropped."""
    groups = []
    for st in ACTIVE_STAGES:
        members = [j for j in tracked if j.stage == st]
        if members:
            groups.append((st, STAGE_LABEL[st], members))
    return groups


def _esc(s) -> str:
    return html.escape(str(s)) if s is not None else ""


def _commute(j) -> str:
    if j.is_remote:
        return "Remote"
    if j.commute_min_precise:
        return f"~{j.commute_min_precise} min real transit ({_esc(j.nearest_station)})"
    if j.commute_min:
        return f"~{j.commute_min} min from home ({_esc(j.nearest_station)})"
    # No commute estimate: show the actual place from the posting, not the bucket.
    place = _esc(j.location) if j.location else _esc(j.location_normalized or "—")
    if j.location_normalized in ("Vancouver", "Hybrid"):
        return f"{place} (metro — no commute estimate, city only)"
    return place


def _salary(j) -> str:
    if j.salary_min and j.salary_max:
        return f"${j.salary_min//1000}k–${j.salary_max//1000}k CAD"
    if j.salary_min:
        return f"${j.salary_min//1000}k+ CAD"
    return "Not stated"


def _bar(score: float) -> str:
    pct = int(round(score * 100))
    color = (_QUAL_COLOR["qualified"] if score >= 0.6
             else (_QUAL_COLOR["stretch"] if score >= 0.4 else _QUAL_COLOR["reach"]))
    return (
        f'<div style="background:{GRID_FAINT};border-radius:6px;height:10px;width:160px;'
        f'display:inline-block;vertical-align:middle;overflow:hidden;">'
        f'<div style="background:{color};height:10px;width:{pct}%;"></div></div>'
        f'<span style="color:{MUTED};font-size:13px;margin-left:8px;">{score:.2f}</span>'
    )


def job_card(job, rank: int | None = None, *, full_desc: bool = False,
             report: bool = False, row_no: int | None = None,
             dom_id: str | None = None, actions_html: str = "",
             stale_days: int = STALE_AFTER_DAYS) -> str:
    """Render one job card. `dom_id` sets the outer div's id (so the web UI can
    target it for HTMX swaps) and `actions_html` is appended inside the card
    (the web UI's status controls) — both no-ops for the email/report paths."""
    title = _esc(job.title)
    if rank is not None:
        title = f"{rank}. {title}"
    star = f' <span style="color:{_QUAL_COLOR["stretch"]};">★</span>' if is_starred(job) else ""
    new = (f' <span style="background:{_QUAL_COLOR["qualified"]};color:#fff;font-size:11px;'
           'padding:1px 6px;border-radius:10px;white-space:nowrap;">NEW</span>'
           ) if job.is_new else ""
    stage = ""
    if job.stage:
        sc = _STAGE_COLOR.get(job.stage, "#6e7480")
        stage = (f' <span style="background:{sc};color:#fff;font-size:11px;'
                 f'padding:1px 6px;border-radius:10px;white-space:nowrap;">'
                 f'{_esc(STAGE_LABEL.get(job.stage, job.stage.title()))}</span>')

    meta = " &nbsp;·&nbsp; ".join([
        f"<b>{_esc(job.role_type or '?')}</b>",
        _commute(job),
        _salary(job),
        f"<i>{_esc(job.source)}</i>",
    ])

    qual_html = ""
    if job.qualification:
        c = _QUAL_COLOR.get(job.qualification, MUTED)
        yrs = f", ~{job.required_years}+ yrs" if job.required_years else ""
        gaps = ""
        if job.missing_requirements:
            shown = job.missing_requirements if report else job.missing_requirements[:2]
            items = "".join(f"<li>{_esc(g)}</li>" for g in shown)
            extra = len(job.missing_requirements) - len(shown)
            if extra:
                items += (f'<li style="list-style:none;color:{MUTED_LIGHT};">'
                          f'+{extra} more gap{"s" if extra != 1 else ""}</li>')
            gaps = (f'<ul style="margin:4px 0 0 0;padding-left:18px;color:{MUTED};'
                    f'font-size:13px;">{items}</ul>')
        qual_html = (
            f'<div style="margin-top:6px;font-size:13px;">'
            f'<span style="background:{c};color:#fff;padding:1px 8px;border-radius:10px;'
            f'font-weight:600;white-space:nowrap;">{_esc(job.qualification).upper()}</span> '
            f'<span style="color:{MUTED};">posting seniority: {_esc(job.seniority or "?")}{yrs}</span>'
            f'{gaps}</div>'
        )

    id_row = ""
    if row_no is not None:
        id_row = (f'<div style="color:{MUTED_LIGHT};font-size:12px;margin-top:2px;'
                  f'font-family:{FONT_MONO};">'
                  f'id <code style="user-select:all;">{_esc(job.id)}</code> '
                  f'&nbsp;·&nbsp; #{row_no}</div>')

    fit = ""
    if job.fit_summary:
        if report:
            inner = _esc(job.fit_summary)
        else:
            # Digest: lead with the verdict sentence in normal weight, then the
            # rest as dimmer, smaller supporting text. No <details> — email
            # clients (Gmail) strip the toggle and leak the literal "more".
            lead, rest = lead_sentence(job.fit_summary)
            inner = _esc(lead)
            if rest:
                inner += (f'<div style="margin-top:4px;color:{MUTED_LIGHT};font-size:13px;">'
                          f'{_esc(rest)}</div>')
        fit = (f'<div style="margin-top:8px;color:{INK};font-size:14px;'
               f'border-left:3px solid {GRID};padding-left:10px;">{inner}</div>')

    desc = ""
    if full_desc and job.description:
        body = _esc(job.description).replace("\n", "<br>")
        desc = (f'<details style="margin-top:8px;"><summary style="cursor:pointer;'
                f'color:{BLUEPRINT_BRIGHT};font-size:13px;">Full description</summary>'
                f'<div style="margin-top:6px;color:{INK};font-size:13px;'
                f'line-height:1.5;max-height:340px;overflow:auto;">{body}</div></details>')

    # Disqualified/duplicate pills each go on their OWN line so they never
    # wrap mid-title.
    dq = ""
    if job.disqualifier:
        dq = (f'<div style="margin-top:6px;"><span style="background:{_STAGE_COLOR["denied"]};color:#fff;'
              f'font-size:11px;padding:2px 8px;border-radius:10px;white-space:nowrap;">'
              f'disqualified: {_esc(job.disqualifier)}</span></div>')
    if job.duplicate_of:
        dq += (f'<div style="margin-top:6px;"><span style="background:{MUTED};color:#fff;'
               f'font-size:11px;padding:2px 8px;border-radius:10px;white-space:nowrap;">'
               f'duplicate posting</span></div>')
    stale = report and is_stale(job, stale_days)
    if stale:
        dq += (f'<div style="margin-top:6px;"><span style="background:{_QUAL_COLOR["stretch"]};color:#fff;'
               f'font-size:11px;padding:2px 8px;border-radius:10px;white-space:nowrap;">'
               f'stale — not seen in {stale_days}+ days</span></div>')

    # data-* attributes + class so the report/web-UI script can filter on them.
    data = ""
    if report:
        searchable = _esc(" ".join(filter(None, [
            job.title, job.company, job.role_type, job.description])).lower())
        data = (f' class="job-card" data-search="{searchable}" '
                f'data-source="{_esc(job.source)}" data-role="{_esc(job.role_type or "")}" '
                f'data-qual="{_esc(job.qualification or "")}" '
                f'data-stage="{_esc(job.stage or "")}" '
                f'data-dq="{1 if job.disqualifier else 0}" '
                f'data-dup="{1 if job.duplicate_of else 0}" '
                f'data-dismissed="{1 if job.dismissed else 0}" '
                f'data-stale="{1 if stale else 0}" data-score="{job.score:.4f}"')
    id_attr = f' id="{_esc(dom_id)}"' if dom_id else ""

    return (
        f'<div{id_attr}{data} style="border:1px solid {GRID};border-radius:10px;padding:14px 16px;'
        f'margin-bottom:14px;font-family:{FONT_SANS};">'
        f'<div style="font-size:16px;font-weight:600;color:{BLUEPRINT_BRIGHT};line-height:1.35;">'
        f'<a href="{_esc(job.url)}" target="_blank" rel="noopener" style="color:{BLUEPRINT_BRIGHT};text-decoration:none;">{title}</a>'
        f'{star}{new}{stage}</div>'
        f'<div style="color:{MUTED};font-size:13px;margin:2px 0 8px;">{_esc(job.company or "Unknown")}</div>'
        f'{id_row}'
        f'{dq}'
        f'<div style="margin:6px 0;">{_bar(job.score)}</div>'
        f'<div style="color:{MUTED};font-size:13px;">{meta}</div>'
        f'{qual_html}{fit}{desc}{actions_html}</div>'
    )


def page(title: str, intro: str, body: str, *, head_extra: str = "",
         max_width: int = 760, chrome: bool = True) -> str:
    """`chrome=True` (cockpit/report — served live by Flask, free to use CSS
    backgrounds/scripts) gets the grid-paper ground and a drafting title-block
    footer. `chrome=False` (the emailed digest) stays on a flat background and
    a plain footer line — Gmail strips embedded `background-image` gradients
    unpredictably, so the digest never relies on one."""
    grid_bg = (
        f'background-image:repeating-linear-gradient(0deg,{GRID_FAINT} 0 1px,transparent 1px 32px),'
        f'repeating-linear-gradient(90deg,{GRID_FAINT} 0 1px,transparent 1px 32px);'
    ) if chrome else ""
    view = title.split("—", 1)[1].strip() if "—" in title else title
    footer = (
        f'<div style="display:flex;justify-content:space-between;border-top:1.5px solid {BLUEPRINT};'
        f'padding-top:10px;margin-top:24px;font-family:{FONT_MONO};font-size:11px;'
        f'color:{MUTED};letter-spacing:0.04em;">'
        f'<span>NORTH ARROW &middot; {_esc(view).upper()}</span>'
        f'<span>{date.today().isoformat()}</span></div>'
    ) if chrome else (
        f'<div style="color:{MUTED_LIGHT};font-size:12px;margin-top:20px;font-family:{FONT_SANS};">'
        f'Generated by North Arrow &middot; {date.today().isoformat()}</div>'
    )
    return (
        f'<!doctype html><html><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{_esc(title)}</title>{FAVICON_LINK}{head_extra}</head>'
        f'<body style="margin:0;background:{PAPER};{grid_bg}padding:20px;">'
        f'<div style="max-width:{max_width}px;margin:0 auto;">'
        f'<h1 style="font-family:{FONT_SANS};'
        f'font-size:22px;color:{INK};margin:0 0 4px;">{_esc(title)}</h1>'
        f'<div style="font-family:{FONT_SANS};'
        f'color:{MUTED};font-size:14px;margin-bottom:16px;">{intro}</div>'
        f'{body}'
        f'{footer}'
        f'</div></body></html>'
    )


def _tracker_row(job, row_no: int | None) -> str:
    color = _STAGE_COLOR.get(job.stage, "#6e7480")
    when = (f' &nbsp;·&nbsp; since {job.stage_at.date().isoformat()}'
            if job.stage_at else "")
    rowtxt = f" &nbsp;·&nbsp; #{row_no}" if row_no is not None else ""
    return (
        f'<div style="border:1px solid {GRID_FAINT};border-radius:8px;padding:8px 12px;'
        f'margin-bottom:8px;font-family:{FONT_SANS};">'
        f'<span style="background:{color};color:#fff;font-size:11px;padding:1px 8px;'
        f'border-radius:10px;white-space:nowrap;">{_esc(STAGE_LABEL[job.stage]).upper()}</span> '
        f'<a href="{_esc(job.url)}" target="_blank" rel="noopener" style="color:{BLUEPRINT_BRIGHT};text-decoration:none;font-weight:600;'
        f'font-size:14px;">{_esc(job.title)}</a> '
        f'<span style="color:{MUTED};font-size:13px;">— {_esc(job.company or "Unknown")}</span>'
        f'<span style="color:{MUTED_LIGHT};font-size:12px;">{when}{rowtxt}</span></div>'
    )


def _tracker_html(tracked: list, row_of: dict[str, int]) -> str:
    if not tracked:
        return ""
    out = (f'<h2 style="font-family:{FONT_SANS};'
           f'font-size:17px;color:{INK};margin:28px 0 6px;">'
           f'Application pipeline <span style="color:{MUTED_LIGHT};font-weight:400;font-size:14px;">'
           f'({len(tracked)})</span></h2>'
           f'<div style="color:{MUTED_LIGHT};font-size:13px;margin-bottom:12px;'
           f'font-family:{FONT_SANS};">'
           'Already in progress — kept out of the suggestions above.</div>')
    for _st, _label, members in group_by_stage(tracked):
        out += "".join(_tracker_row(j, row_of.get(j.id)) for j in members)
    return out


def digest_html(primary: list, near: list, tracked: list, cfg: dict,
                row_of: dict[str, int] | None = None) -> str:
    row_of = row_of or {}
    thr = cfg["delivery"]["min_score_for_digest"]
    n = len(primary)
    intro = f"{n} match{'es' if n != 1 else ''} at or above score {thr}"
    body = ""
    if primary:
        for label, members in group_by_qual(primary):
            body += (f'<h2 style="font-family:{FONT_SANS};'
                     f'font-size:17px;color:{INK};margin:24px 0 12px;">'
                     f'{_esc(label)} <span style="color:{MUTED_LIGHT};font-weight:400;font-size:14px;">'
                     f'({len(members)})</span></h2>')
            body += "".join(job_card(j, i, row_no=row_of.get(j.id))
                            for i, j in enumerate(members, 1))
    else:
        body += (f'<div style="color:{MUTED};font-family:{FONT_SANS};">'
                 'No postings cleared the bar today.</div>')
    if near:
        body += (f'<h2 style="font-family:{FONT_SANS};'
                 f'font-size:17px;color:{INK};margin:24px 0 12px;">Near misses (below the bar)</h2>')
        body += "".join(job_card(j, i, row_no=row_of.get(j.id)) for i, j in enumerate(near, 1))
    body += _tracker_html(tracked, row_of)
    return page("North Arrow — Daily Shortlist", intro, body, chrome=False)


# ── Browser report: search + filter controls ────────────────────────────────
_INPUT_STYLE = (f"padding:6px 8px;border:1px solid {GRID};border-radius:6px;"
                "font-size:13px;font-family:inherit;background:#fff;")


def _filter_bar(jobs: list) -> str:
    def opts(label, values):
        os = "".join(f'<option value="{_esc(v)}">{_esc(v)}</option>' for v in values)
        return (f'<select id="f{label}" onchange="applyFilters()" style="{_INPUT_STYLE}">'
                f'<option value="">{label}: all</option>{os}</select>')
    sources = sorted({j.source for j in jobs if j.source})
    roles = sorted({j.role_type for j in jobs if j.role_type})
    quals = sorted({j.qualification for j in jobs if j.qualification})
    stages = [s for s in (*ACTIVE_STAGES, "denied", "withdrawn")
              if any(j.stage == s for j in jobs)]
    return (
        f'<div style="position:sticky;top:0;background:{PAPER};padding:12px 0;z-index:10;'
        f'display:flex;flex-wrap:wrap;gap:8px;align-items:center;border-bottom:1px solid {GRID};'
        f'margin-bottom:16px;font-family:{FONT_SANS};">'
        f'<input id="q" type="search" placeholder="Search title, company, description…" '
        f'oninput="applyFilters()" style="{_INPUT_STYLE}flex:1;min-width:220px;">'
        f'{opts("source", sources)}{opts("role", roles)}{opts("qual", quals)}{opts("stage", stages)}'
        f'<label style="font-size:13px;color:{MUTED};display:flex;align-items:center;gap:4px;">'
        '<input type="checkbox" id="fdq" onchange="applyFilters()"> show disqualified</label>'
        f'<label style="font-size:13px;color:{MUTED};display:flex;align-items:center;gap:4px;">'
        '<input type="checkbox" id="fdup" onchange="applyFilters()"> show duplicates</label>'
        f'<label style="font-size:13px;color:{MUTED};display:flex;align-items:center;gap:4px;">'
        '<input type="checkbox" id="fstale" onchange="applyFilters()"> show stale</label>'
        f'<label style="font-size:13px;color:{MUTED};display:flex;align-items:center;gap:4px;">'
        '<input type="checkbox" id="fdismissed" onchange="applyFilters()"> show dismissed</label>'
        f'<label style="font-size:13px;color:{MUTED};display:flex;align-items:center;gap:4px;">'
        '<input type="checkbox" id="fstaged" onchange="applyFilters()"> show in pipeline</label>'
        f'<span id="count" style="font-size:13px;color:{MUTED};margin-left:auto;"></span>'
        '</div>'
    )


_SCRIPT = """<script>
function applyFilters(){
  var q=document.getElementById('q').value.toLowerCase().trim();
  var src=document.getElementById('fsource').value;
  var role=document.getElementById('frole').value;
  var qual=document.getElementById('fqual').value;
  var stage=document.getElementById('fstage').value;
  var showdq=document.getElementById('fdq').checked;
  var showdup=document.getElementById('fdup').checked;
  var showstale=document.getElementById('fstale').checked;
  var showdismissed=document.getElementById('fdismissed').checked;
  var showstaged=document.getElementById('fstaged').checked;
  var n=0;
  document.querySelectorAll('.job-card').forEach(function(c){
    var ok=true;
    if(q && c.dataset.search.indexOf(q)<0) ok=false;
    if(src && c.dataset.source!==src) ok=false;
    if(role && c.dataset.role!==role) ok=false;
    if(qual && c.dataset.qual!==qual) ok=false;
    if(stage){ if(c.dataset.stage!==stage) ok=false; }
    else if(!showstaged && c.dataset.stage) ok=false;
    if(!showdq && c.dataset.dq==='1') ok=false;
    if(!showdup && c.dataset.dup==='1') ok=false;
    if(!showstale && c.dataset.stale==='1') ok=false;
    if(!showdismissed && c.dataset.dismissed==='1') ok=false;
    c.style.display = ok ? '' : 'none';
    if(ok) n++;
  });
  document.querySelectorAll('h3').forEach(function(h){
    var next=h.nextElementSibling, any=false;
    while(next && next.tagName!=='H3'){
      if(next.classList && next.classList.contains('job-card') && next.style.display!=='none'){any=true;break;}
      next=next.nextElementSibling;
    }
    h.style.display = any ? '' : 'none';
  });
  document.getElementById('count').textContent = n+' shown';
}
document.addEventListener('DOMContentLoaded', applyFilters);
// A card swapped in via HTMX (e.g. after Dismiss/Interested/stage change)
// starts fully visible and ignores whatever filters are currently active —
// re-apply them whenever HTMX finishes settling new content into the page.
document.addEventListener('htmx:afterSettle', applyFilters);
</script>"""


def report_html(jobs: list, cfg: dict) -> str:
    stale_days = cfg.get("delivery", {}).get("stale_after_days", STALE_AFTER_DAYS)
    live = [j for j in jobs if not j.disqualifier and not j.duplicate_of]
    dead = len([j for j in jobs if j.disqualifier])
    dup = len([j for j in jobs if j.duplicate_of])
    stale = len([j for j in jobs if is_stale(j, stale_days)])
    intro = (f"{len(live)} scored · {dead} disqualified · {dup} duplicates · {stale} stale "
             f"— search and filter below; disqualified/duplicates/stale/dismissed/in-pipeline hidden "
             f"until you toggle them on")
    row_of = {j.id: i for i, j in enumerate(jobs, 1)}
    card_fn = lambda j: job_card(
        j, row_of[j.id], full_desc=True, report=True, row_no=row_of[j.id], stale_days=stale_days)
    excluded = [j for j in jobs if j.disqualifier or j.duplicate_of]
    cards = (bucketed_cards_html(jobs, card_fn)
             + staged_cards_html(jobs, card_fn)
             + _section_html("Disqualified & duplicates", excluded, card_fn))
    body = _filter_bar(jobs) + f'<div id="cards">{cards}</div>' + _SCRIPT
    return page("North Arrow — Full Report", intro, body)


# ── Cockpit inbox view ───────────────────────────────────────────────────────
# The live cockpit list is a *decision queue*, not the report's catalog: it
# leads with what still needs a decision, groups by novelty (new to triage vs.
# backlog) rather than by qualification, and pushes everything you've already
# handled or that's been screened out into quiet, collapsible sections. Acting
# on a card (apply/dismiss/…) drops it out of the queue — see INBOX_SCRIPT.
#
# One partition, used for both the counts and the cards, so the header numbers
# can never disagree with what's rendered below them.

def _first_seen_after(job, cutoff: datetime | None) -> bool:
    """True if the posting first arrived after `cutoff` (tz-safe). False when
    there's no cutoff or no arrival time — callers treat that as 'not new'."""
    if cutoff is None or not job.first_seen_at:
        return False
    seen = job.first_seen_at
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return seen > cutoff


def inbox_partition(jobs: list, stale_days: int = STALE_AFTER_DAYS, *,
                    new_since: datetime | None = None):
    """Assign every job to exactly one inbox bucket, most-actionable first.

    Returns (queue_groups, noise_groups, pipeline) where:
      - queue_groups: [(label, members)] shown by default — Saved, then New to
        triage, then the Backlog. These are the "still needs a decision" jobs.
      - noise_groups: [(label, members)] rendered but hidden until "Show
        everything" — screened out, duplicates, dismissed, likely-closed.
      - pipeline: jobs already in an application stage. NOT rendered in the
        list at all (the pipeline board owns them); returned only so the caller
        can show a count + link.

    `new_since` is the "you last looked at the cockpit around here" cutoff: a
    job first seen after it lands in "New to triage" instead of the backlog.
    When None (no cutoff known), nothing counts as new and those jobs fall to
    the backlog — the queue is unaffected, just unsplit.

    Buckets are checked in priority order so each job lands in one place and
    the groups stay mutually exclusive and exhaustive. A saved job outranks
    every noise bucket — a job you explicitly flagged is never hidden."""
    saved, new, backlog = [], [], []
    screened, dupes, dismissed_, stale_ = [], [], [], []
    pipeline = []
    for j in jobs:
        if j.stage:
            pipeline.append(j)
        elif j.saved:
            saved.append(j)
        elif j.disqualifier:
            screened.append(j)
        elif j.duplicate_of:
            dupes.append(j)
        elif j.dismissed:
            dismissed_.append(j)
        elif is_stale(j, stale_days):
            stale_.append(j)
        elif _first_seen_after(j, new_since):
            new.append(j)
        else:
            backlog.append(j)
    queue_groups = [g for g in (
        ("★ Saved", saved),
        ("New to triage", new),
        ("Backlog", backlog),
    ) if g[1]]
    noise_groups = [g for g in (
        ("Screened out", screened),
        ("Duplicates", dupes),
        ("Dismissed", dismissed_),
        ("Likely closed (stale)", stale_),
    ) if g[1]]
    return queue_groups, noise_groups, pipeline


def _grp_header(label: str, count: int, *, noise: bool) -> str:
    return (
        f'<h3 class="grp" data-noise="{0 if not noise else 1}" '
        f'style="margin:22px 0 10px;font-size:15px;color:{INK};'
        f'border-bottom:1px solid {GRID};padding-bottom:6px;">'
        f'{_esc(label)} <span class="grp-n" style="color:{MUTED_LIGHT};font-weight:400;">'
        f'({count})</span></h3>'
    )


def inbox_cards_html(queue_groups, noise_groups, card_fn) -> str:
    """Render the queue groups (visible) then the noise groups (hidden by
    default via INBOX_SCRIPT). Counts in the headers are live — the script
    rewrites them as cards drain — so the server-rendered number is just the
    starting value."""
    parts = []
    for groups, noise in ((queue_groups, False), (noise_groups, True)):
        for label, members in groups:
            parts.append(_grp_header(label, len(members), noise=noise))
            parts.append("".join(card_fn(j) for j in members))
    return "".join(parts)


def inbox_controls(jobs: list, *, new_count: int, queue_count: int,
                   pipeline_count: int, board_href: str = "/board") -> str:
    """The status line ('X new · Y awaiting decision · Z in your pipeline →')
    plus a lean filter bar: search, a 'Fit' allow-list of qualification chips,
    a source dropdown, and a single 'Show everything' escape hatch that reveals
    the noise sections. Deliberately fewer knobs than the report's filter bar —
    the queue's whole point is that the useful default needs no fiddling."""
    sources = sorted({j.source for j in jobs if j.source})
    src_opts = "".join(f'<option value="{_esc(s)}">{_esc(s)}</option>' for s in sources)
    # Qualification allow-list chips: none selected = no filter (show all fits);
    # selecting some narrows to just those. Order = most- to least-applyable.
    chip_style = (f"padding:3px 10px;border-radius:12px;border:1px solid {GRID};"
                  f"background:#fff;font-size:12px;cursor:pointer;font-family:inherit;"
                  f"color:{INK};opacity:0.55;")
    chips = "".join(
        f'<button type="button" class="qchip" data-qual="{q}" data-on="0" '
        f'onclick="qchipToggle(this)" style="{chip_style}">{label}</button>'
        for q, label in (("qualified", "Qualified"), ("overqualified", "Overqualified"),
                         ("stretch", "Stretch"), ("reach", "Reach"))
    )
    status = (
        f'<div style="font-size:14px;color:{INK};margin-bottom:10px;'
        f'font-family:{FONT_SANS};">'
        f'<b>{new_count}</b> new · <b id="queue-count">{queue_count}</b> awaiting decision · '
        f'<a href="{board_href}" style="color:{BLUEPRINT_BRIGHT};text-decoration:none;">'
        f'{pipeline_count} in your pipeline &rarr;</a></div>'
    )
    bar = (
        f'<div style="position:sticky;top:0;background:{PAPER};padding:12px 0;z-index:10;'
        f'display:flex;flex-wrap:wrap;gap:8px;align-items:center;border-bottom:1px solid {GRID};'
        f'margin-bottom:8px;font-family:{FONT_SANS};">'
        f'<input id="q" type="search" placeholder="Search title, company, description…" '
        f'oninput="inboxFilter()" style="{_INPUT_STYLE}flex:1;min-width:200px;">'
        f'<span style="font-size:12px;color:{MUTED};">Fit:</span>{chips}'
        f'<select id="fsource" onchange="inboxFilter()" style="{_INPUT_STYLE}">'
        f'<option value="">source: all</option>{src_opts}</select>'
        f'<label style="font-size:13px;color:{MUTED};display:flex;align-items:center;gap:4px;">'
        '<input type="checkbox" id="fall" onchange="inboxFilter()"> Show everything</label>'
        '</div>'
    )
    return status + bar


INBOX_SCRIPT = """<script>
function qchipToggle(el){
  el.dataset.on = el.dataset.on==='1' ? '0' : '1';
  el.style.opacity = el.dataset.on==='1' ? '1' : '0.55';
  el.style.background = el.dataset.on==='1' ? '__TINT__' : '#fff';
  el.style.borderColor = el.dataset.on==='1' ? '__BLUEPRINT_BRIGHT__' : '__GRID__';
  inboxFilter();
}
function inboxFilter(){
  var q=(document.getElementById('q').value||'').toLowerCase().trim();
  var src=document.getElementById('fsource').value;
  var showAll=document.getElementById('fall').checked;
  var quals=[];
  document.querySelectorAll('.qchip[data-on="1"]').forEach(function(c){quals.push(c.dataset.qual);});
  document.querySelectorAll('.job-card').forEach(function(c){
    var ok=true;
    // A staged job belongs to the pipeline board, never the queue — so a card
    // that just became staged via an action hides itself (the "drain").
    if(c.dataset.stage) ok=false;
    if(q && c.dataset.search.indexOf(q)<0) ok=false;
    if(src && c.dataset.source!==src) ok=false;
    if(quals.length && quals.indexOf(c.dataset.qual)<0) ok=false;
    if(!showAll && (c.dataset.dq==='1'||c.dataset.dup==='1'||
                    c.dataset.dismissed==='1'||c.dataset.stale==='1')) ok=false;
    c.style.display = ok ? '' : 'none';
  });
  // Live-update each group's header count from what's actually visible, hide
  // emptied groups, and keep the 'awaiting decision' total honest as cards drain.
  var queueN=0;
  document.querySelectorAll('h3.grp').forEach(function(h){
    var n=0, next=h.nextElementSibling;
    while(next && !(next.tagName==='H3' && next.classList.contains('grp'))){
      if(next.classList && next.classList.contains('job-card') && next.style.display!=='none') n++;
      next=next.nextElementSibling;
    }
    var span=h.querySelector('.grp-n'); if(span) span.textContent='('+n+')';
    h.style.display = n ? '' : 'none';
    if(h.dataset.noise==='0') queueN+=n;
  });
  var qc=document.getElementById('queue-count'); if(qc) qc.textContent=queueN;
}
document.addEventListener('DOMContentLoaded', inboxFilter);
// Cards swapped in via HTMX (apply/dismiss/interested/…) start visible and
// ignore the active filters — re-run once HTMX settles so the queue drains.
document.addEventListener('htmx:afterSettle', inboxFilter);
</script>""".replace("__TINT__", TINT).replace("__BLUEPRINT_BRIGHT__", BLUEPRINT_BRIGHT).replace("__GRID__", GRID)
