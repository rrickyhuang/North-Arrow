"""Shared HTML rendering for the email digest and the browser report.

Uses inline styles (email clients strip <style> blocks unpredictably), kept
simple so both Gmail and a normal browser render it the same way. The browser
report additionally gets data-* attributes + a small script for client-side
search/filtering (ignored by email clients).
"""
from __future__ import annotations

import html
import re
from datetime import date

_QUAL_COLOR = {
    "qualified": "#1a7f37",
    "stretch": "#9a6700",
    "reach": "#b35900",
    "overqualified": "#6e7781",
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

# A "top pick" star means apply-now-worthy: Ricky meets the bar AND it scores
# well — not just a high raw score (which used to star senior/reach roles he
# can't get).
_STAR_MIN_SCORE = 0.7


def is_top_pick(job) -> bool:
    return (job.qualification in ("qualified", "overqualified")
            and job.score >= _STAR_MIN_SCORE)


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
    color = "#1a7f37" if score >= 0.6 else ("#9a6700" if score >= 0.4 else "#b35900")
    return (
        f'<div style="background:#eaeef2;border-radius:6px;height:10px;width:160px;'
        f'display:inline-block;vertical-align:middle;overflow:hidden;">'
        f'<div style="background:{color};height:10px;width:{pct}%;"></div></div>'
        f'<span style="color:#57606a;font-size:13px;margin-left:8px;">{score:.2f}</span>'
    )


def job_card(job, rank: int | None = None, *, full_desc: bool = False,
             report: bool = False, row_no: int | None = None) -> str:
    title = _esc(job.title)
    if rank is not None:
        title = f"{rank}. {title}"
    star = ' <span style="color:#bf8700;">★</span>' if is_top_pick(job) else ""
    new = (' <span style="background:#1a7f37;color:#fff;font-size:11px;'
           'padding:1px 6px;border-radius:10px;white-space:nowrap;">NEW</span>'
           ) if job.is_new else ""

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
            shown = job.missing_requirements if report else job.missing_requirements[:2]
            items = "".join(f"<li>{_esc(g)}</li>" for g in shown)
            extra = len(job.missing_requirements) - len(shown)
            if extra:
                items += (f'<li style="list-style:none;color:#8b949e;">'
                          f'+{extra} more gap{"s" if extra != 1 else ""}</li>')
            gaps = (f'<ul style="margin:4px 0 0 0;padding-left:18px;color:#57606a;'
                    f'font-size:13px;">{items}</ul>')
        qual_html = (
            f'<div style="margin-top:6px;font-size:13px;">'
            f'<span style="background:{c};color:#fff;padding:1px 8px;border-radius:10px;'
            f'font-weight:600;white-space:nowrap;">{_esc(job.qualification).upper()}</span> '
            f'<span style="color:#57606a;">posting seniority: {_esc(job.seniority or "?")}{yrs}</span>'
            f'{gaps}</div>'
        )

    id_row = ""
    if row_no is not None:
        id_row = (f'<div style="color:#8b949e;font-size:12px;margin-top:2px;">'
                  f'id <code style="user-select:all;">{_esc(job.id)}</code> '
                  f'&nbsp;·&nbsp; #{row_no}</div>')

    fit = ""
    if job.fit_summary:
        if report:
            inner = _esc(job.fit_summary)
        else:
            # Digest: lead with the verdict sentence, tuck the rest behind a
            # <details>. Email clients that ignore <details> just show it all
            # (same as before) — graceful, no lost text.
            lead, rest = lead_sentence(job.fit_summary)
            inner = _esc(lead)
            if rest:
                inner += (f'<details style="margin-top:4px;"><summary style="cursor:pointer;'
                          f'color:#0969da;font-size:12px;">more</summary>'
                          f'<div style="margin-top:4px;color:#57606a;font-size:13px;">'
                          f'{_esc(rest)}</div></details>')
        fit = (f'<div style="margin-top:8px;color:#24292f;font-size:14px;'
               f'border-left:3px solid #d0d7de;padding-left:10px;">{inner}</div>')

    desc = ""
    if full_desc and job.description:
        body = _esc(job.description).replace("\n", "<br>")
        desc = (f'<details style="margin-top:8px;"><summary style="cursor:pointer;'
                f'color:#0969da;font-size:13px;">Full description</summary>'
                f'<div style="margin-top:6px;color:#24292f;font-size:13px;'
                f'line-height:1.5;max-height:340px;overflow:auto;">{body}</div></details>')

    # Disqualified/duplicate pills each go on their OWN line so they never
    # wrap mid-title.
    dq = ""
    if job.disqualifier:
        dq = (f'<div style="margin-top:6px;"><span style="background:#cf222e;color:#fff;'
              f'font-size:11px;padding:2px 8px;border-radius:10px;white-space:nowrap;">'
              f'disqualified: {_esc(job.disqualifier)}</span></div>')
    if job.duplicate_of:
        dq += (f'<div style="margin-top:6px;"><span style="background:#57606a;color:#fff;'
               f'font-size:11px;padding:2px 8px;border-radius:10px;white-space:nowrap;">'
               f'duplicate posting</span></div>')

    # data-* attributes + class so the report script can filter on them.
    attrs = ' style="'
    data = ""
    if report:
        searchable = _esc(" ".join(filter(None, [
            job.title, job.company, job.role_type, job.description])).lower())
        data = (f' class="job-card" data-search="{searchable}" '
                f'data-source="{_esc(job.source)}" data-role="{_esc(job.role_type or "")}" '
                f'data-qual="{_esc(job.qualification or "")}" '
                f'data-dq="{1 if job.disqualifier else 0}" '
                f'data-dup="{1 if job.duplicate_of else 0}" data-score="{job.score:.4f}"')

    return (
        f'<div{data} style="border:1px solid #d0d7de;border-radius:10px;padding:14px 16px;'
        f'margin-bottom:14px;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">'
        f'<div style="font-size:16px;font-weight:600;color:#0969da;line-height:1.35;">'
        f'<a href="{_esc(job.url)}" style="color:#0969da;text-decoration:none;">{title}</a>'
        f'{star}{new}</div>'
        f'<div style="color:#57606a;font-size:13px;margin:2px 0 8px;">{_esc(job.company or "Unknown")}</div>'
        f'{id_row}'
        f'{dq}'
        f'<div style="margin:6px 0;">{_bar(job.score)}</div>'
        f'<div style="color:#57606a;font-size:13px;">{meta}</div>'
        f'{qual_html}{fit}{desc}</div>'
    )


def page(title: str, intro: str, body: str, *, head_extra: str = "") -> str:
    return (
        f'<!doctype html><html><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{_esc(title)}</title>{head_extra}</head>'
        f'<body style="margin:0;background:#f6f8fa;padding:20px;">'
        f'<div style="max-width:760px;margin:0 auto;">'
        f'<h1 style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        f'font-size:22px;color:#24292f;margin:0 0 4px;">{_esc(title)}</h1>'
        f'<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        f'color:#57606a;font-size:14px;margin-bottom:16px;">{intro}</div>'
        f'{body}'
        f'<div style="color:#8b949e;font-size:12px;margin-top:20px;font-family:-apple-system,'
        f'Segoe UI,Roboto,Helvetica,Arial,sans-serif;">Generated by JobHunter · {date.today().isoformat()}</div>'
        f'</div></body></html>'
    )


def digest_html(primary: list, near: list, cfg: dict, row_of: dict[str, int] | None = None) -> str:
    row_of = row_of or {}
    thr = cfg["delivery"]["min_score_for_digest"]
    n = len(primary)
    intro = f"{n} match{'es' if n != 1 else ''} at or above score {thr}"
    body = ""
    if primary:
        for label, members in group_by_qual(primary):
            body += ('<h2 style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;'
                     'font-size:17px;color:#24292f;margin:24px 0 12px;">'
                     f'{_esc(label)} <span style="color:#8b949e;font-weight:400;font-size:14px;">'
                     f'({len(members)})</span></h2>')
            body += "".join(job_card(j, i, row_no=row_of.get(j.id))
                            for i, j in enumerate(members, 1))
    else:
        body += ('<div style="color:#57606a;font-family:-apple-system,Segoe UI,Roboto,'
                 'sans-serif;">No postings cleared the bar today.</div>')
    if near:
        body += ('<h2 style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;'
                 'font-size:17px;color:#24292f;margin:24px 0 12px;">Near misses (below the bar)</h2>')
        body += "".join(job_card(j, i, row_no=row_of.get(j.id)) for i, j in enumerate(near, 1))
    return page("JobHunter — Daily Shortlist", intro, body)


# ── Browser report: search + filter controls ────────────────────────────────
_INPUT_STYLE = ("padding:6px 8px;border:1px solid #d0d7de;border-radius:6px;"
                "font-size:13px;font-family:inherit;background:#fff;")


def _filter_bar(jobs: list) -> str:
    def opts(label, values):
        os = "".join(f'<option value="{_esc(v)}">{_esc(v)}</option>' for v in values)
        return (f'<select id="f{label}" onchange="applyFilters()" style="{_INPUT_STYLE}">'
                f'<option value="">{label}: all</option>{os}</select>')
    sources = sorted({j.source for j in jobs if j.source})
    roles = sorted({j.role_type for j in jobs if j.role_type})
    quals = sorted({j.qualification for j in jobs if j.qualification})
    return (
        '<div style="position:sticky;top:0;background:#f6f8fa;padding:12px 0;z-index:10;'
        'display:flex;flex-wrap:wrap;gap:8px;align-items:center;border-bottom:1px solid #d0d7de;'
        'margin-bottom:16px;font-family:-apple-system,Segoe UI,Roboto,sans-serif;">'
        f'<input id="q" type="search" placeholder="Search title, company, description…" '
        f'oninput="applyFilters()" style="{_INPUT_STYLE}flex:1;min-width:220px;">'
        f'{opts("source", sources)}{opts("role", roles)}{opts("qual", quals)}'
        '<label style="font-size:13px;color:#57606a;display:flex;align-items:center;gap:4px;">'
        '<input type="checkbox" id="fdq" onchange="applyFilters()"> show disqualified</label>'
        '<label style="font-size:13px;color:#57606a;display:flex;align-items:center;gap:4px;">'
        '<input type="checkbox" id="fdup" onchange="applyFilters()"> show duplicates</label>'
        '<span id="count" style="font-size:13px;color:#57606a;margin-left:auto;"></span>'
        '</div>'
    )


_SCRIPT = """<script>
function applyFilters(){
  var q=document.getElementById('q').value.toLowerCase().trim();
  var src=document.getElementById('fsource').value;
  var role=document.getElementById('frole').value;
  var qual=document.getElementById('fqual').value;
  var showdq=document.getElementById('fdq').checked;
  var showdup=document.getElementById('fdup').checked;
  var n=0;
  document.querySelectorAll('.job-card').forEach(function(c){
    var ok=true;
    if(q && c.dataset.search.indexOf(q)<0) ok=false;
    if(src && c.dataset.source!==src) ok=false;
    if(role && c.dataset.role!==role) ok=false;
    if(qual && c.dataset.qual!==qual) ok=false;
    if(!showdq && c.dataset.dq==='1') ok=false;
    if(!showdup && c.dataset.dup==='1') ok=false;
    c.style.display = ok ? '' : 'none';
    if(ok) n++;
  });
  document.getElementById('count').textContent = n+' shown';
}
document.addEventListener('DOMContentLoaded', applyFilters);
</script>"""


def report_html(jobs: list, cfg: dict) -> str:
    live = [j for j in jobs if not j.disqualifier and not j.duplicate_of]
    dead = len([j for j in jobs if j.disqualifier])
    dup = len([j for j in jobs if j.duplicate_of])
    intro = (f"{len(live)} scored · {dead} disqualified · {dup} duplicates — search and "
             f"filter below; disqualified/duplicates hidden until you toggle them on")
    cards = "".join(job_card(j, i, full_desc=True, report=True)
                    for i, j in enumerate(jobs, 1))
    body = _filter_bar(jobs) + f'<div id="cards">{cards}</div>' + _SCRIPT
    return page("JobHunter — Full Report", intro, body)
