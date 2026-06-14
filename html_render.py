"""Shared HTML rendering for the email digest and the browser report.

Uses inline styles (email clients strip <style> blocks unpredictably), kept
simple so both Gmail and a normal browser render it the same way.
"""
from __future__ import annotations

import html
from datetime import date

_QUAL_COLOR = {
    "qualified": "#1a7f37",
    "stretch": "#9a6700",
    "reach": "#b35900",
    "overqualified": "#6e7781",
}


def _esc(s) -> str:
    return html.escape(str(s)) if s is not None else ""


def _commute(j) -> str:
    if j.is_remote:
        return "Remote"
    if j.commute_min:
        return f"~{j.commute_min} min from home ({_esc(j.nearest_station)})"
    if j.location_normalized in ("Vancouver", "Hybrid"):
        return "In metro — no precise estimate (posting gave only a city)"
    return _esc(j.location_normalized or "—")


def _salary(j) -> str:
    if j.salary_min and j.salary_max:
        return f"${j.salary_min//1000}k–${j.salary_max//1000}k CAD"
    if j.salary_min:
        return f"${j.salary_min//1000}k+ CAD"
    return "Not stated"


def _bar(score: float) -> str:
    pct = int(round(score * 100))
    color = "#1a7f37" if score >= 0.6 else ("#9a6700" if score >= 0.4 else "#b35900")
    return (
        f'<div style="background:#eaeef2;border-radius:6px;height:10px;width:160px;'
        f'display:inline-block;vertical-align:middle;overflow:hidden;">'
        f'<div style="background:{color};height:10px;width:{pct}%;"></div></div>'
        f'<span style="color:#57606a;font-size:13px;margin-left:8px;">{score:.2f}</span>'
    )


def job_card(job, rank: int | None = None, *, full_desc: bool = False) -> str:
    title = _esc(job.title)
    if rank is not None:
        title = f"{rank}. {title}"
    star = " ★" if job.score >= 0.8 else ""
    new = (' <span style="background:#1a7f37;color:#fff;font-size:11px;'
           'padding:1px 6px;border-radius:10px;">NEW</span>') if job.is_new else ""

    meta = " &nbsp;·&nbsp; ".join([
        f"<b>{_esc(job.role_type or '?')}</b>",
        _commute(job),
        _salary(job),
        f"<i>{_esc(job.source)}</i>",
    ])

    qual_html = ""
    if job.qualification:
        c = _QUAL_COLOR.get(job.qualification, "#57606a")
        yrs = f", ~{job.required_years}+ yrs" if job.required_years else ""
        gaps = ""
        if job.missing_requirements:
            items = "".join(f"<li>{_esc(g)}</li>" for g in job.missing_requirements[:4])
            gaps = (f'<ul style="margin:4px 0 0 0;padding-left:18px;color:#57606a;'
                    f'font-size:13px;">{items}</ul>')
        qual_html = (
            f'<div style="margin-top:6px;font-size:13px;">'
            f'<span style="background:{c};color:#fff;padding:1px 8px;border-radius:10px;'
            f'font-weight:600;">{_esc(job.qualification).upper()}</span> '
            f'<span style="color:#57606a;">posting seniority: {_esc(job.seniority or "?")}{yrs}</span>'
            f'{gaps}</div>'
        )

    fit = ""
    if job.fit_summary:
        fit = (f'<div style="margin-top:8px;color:#24292f;font-size:14px;'
               f'border-left:3px solid #d0d7de;padding-left:10px;">{_esc(job.fit_summary)}</div>')

    desc = ""
    if full_desc and job.description:
        body = _esc(job.description).replace("\n", "<br>")
        desc = (f'<details style="margin-top:8px;"><summary style="cursor:pointer;'
                f'color:#0969da;font-size:13px;">Full description</summary>'
                f'<div style="margin-top:6px;color:#24292f;font-size:13px;'
                f'line-height:1.5;max-height:340px;overflow:auto;">{body}</div></details>')

    dq = ""
    if job.disqualifier:
        dq = (f'<span style="background:#cf222e;color:#fff;font-size:11px;'
              f'padding:1px 6px;border-radius:10px;margin-left:6px;">disqualified: '
              f'{_esc(job.disqualifier)}</span>')

    return (
        f'<div style="border:1px solid #d0d7de;border-radius:10px;padding:14px 16px;'
        f'margin-bottom:14px;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">'
        f'<div style="font-size:16px;font-weight:600;color:#0969da;">'
        f'<a href="{_esc(job.url)}" style="color:#0969da;text-decoration:none;">{title}</a>'
        f'{star}{new}{dq}</div>'
        f'<div style="color:#57606a;font-size:13px;margin:2px 0 8px;">{_esc(job.company or "Unknown")}</div>'
        f'<div style="margin:6px 0;">{_bar(job.score)}</div>'
        f'<div style="color:#57606a;font-size:13px;">{meta}</div>'
        f'{qual_html}{fit}{desc}</div>'
    )


def page(title: str, intro: str, body: str) -> str:
    return (
        f'<!doctype html><html><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{_esc(title)}</title></head>'
        f'<body style="margin:0;background:#f6f8fa;padding:20px;">'
        f'<div style="max-width:760px;margin:0 auto;">'
        f'<h1 style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        f'font-size:22px;color:#24292f;margin:0 0 4px;">{_esc(title)}</h1>'
        f'<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        f'color:#57606a;font-size:14px;margin-bottom:20px;">{intro}</div>'
        f'{body}'
        f'<div style="color:#8b949e;font-size:12px;margin-top:20px;font-family:-apple-system,'
        f'Segoe UI,Roboto,Helvetica,Arial,sans-serif;">Generated by JobHunter · {date.today().isoformat()}</div>'
        f'</div></body></html>'
    )


def digest_html(primary: list, near: list, cfg: dict) -> str:
    thr = cfg["delivery"]["min_score_for_digest"]
    n = len(primary)
    intro = f"{n} match{'es' if n != 1 else ''} at or above score {thr}"
    body = ""
    if primary:
        body += "".join(job_card(j, i) for i, j in enumerate(primary, 1))
    else:
        body += ('<div style="color:#57606a;font-family:-apple-system,Segoe UI,Roboto,'
                 'sans-serif;">No postings cleared the bar today.</div>')
    if near:
        body += ('<h2 style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;'
                 'font-size:17px;color:#24292f;margin:24px 0 12px;">Near misses (below the bar)</h2>')
        body += "".join(job_card(j, i) for i, j in enumerate(near, 1))
    return page("JobHunter — Daily Shortlist", intro, body)


def report_html(jobs: list, cfg: dict) -> str:
    live = [j for j in jobs if not j.disqualifier]
    dead = [j for j in jobs if j.disqualifier]
    intro = f"{len(live)} scored · {len(dead)} disqualified · full database with descriptions"
    body = "".join(job_card(j, i, full_desc=True) for i, j in enumerate(live, 1))
    if dead:
        body += ('<h2 style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;'
                 'font-size:17px;color:#24292f;margin:24px 0 12px;">Disqualified</h2>')
        body += "".join(job_card(j, full_desc=True) for j in dead)
    return page("JobHunter — Full Report", intro, body)
