"""Read-only viewer for the jobs database.

    python show.py              ranked list (score >= 0.25 by default)
    python show.py --all        include disqualified AND dismissed jobs
    python show.py --min 0.6    only jobs at/above a score
    python show.py --min 0      no score floor (show everything, incl. low scores)
    python show.py --filter source=pibc                  substring/exact match on any Job field
    python show.py --filter role_type=landscape_arch      (repeat --filter for AND conditions)
    python show.py --filter stage!=applied               != negates the match
    python show.py --filter stage=applied --filter saved=true
    python show.py 3            detail for row #3 from the list (description truncated past 1200 chars)
    python show.py 3 --full     same, but the full description untruncated
    python show.py <job_id>     detail by id (also supports --full)

Touches nothing — just prints. Safe to run any time. To update a job's
application status (applied / interested / not interested / seen), use the
companion script: `python mark.py <row-# or id> <status>`.
"""
from __future__ import annotations

import sys
from dataclasses import fields as dataclass_fields

import config
import db
from models import Job

# Windows consoles default to cp1252 and choke on box/bar glyphs. Force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

BAR_FULL, BAR_EMPTY = "█", "░"


def _bar(score: float, width: int = 16) -> str:
    n = int(round(score * width))
    return BAR_FULL * n + BAR_EMPTY * (width - n)


_LOC_CATEGORY = {
    "Vancouver": "Vancouver metro",
    "Remote": "Remote",
    "Hybrid": "Hybrid",
    "Other": "Outside metro",
    "Unknown": "Unknown",
}


def _fmt_salary(j) -> str:
    if j.salary_min and j.salary_max:
        return f"${j.salary_min // 1000}k-${j.salary_max // 1000}k CAD"
    if j.salary_min:
        return f"${j.salary_min // 1000}k+ CAD"
    if j.salary_max:
        return f"up to ${j.salary_max // 1000}k CAD"
    return "not stated"


def _fmt_commute(j) -> str:
    if j.is_remote:
        return "remote"
    if j.commute_min_precise:
        precise = f"~{j.commute_min_precise} min real transit (via {j.nearest_station})"
        if j.commute_min and j.commute_min != j.commute_min_precise:
            precise += f" [estimate was ~{j.commute_min}]"
        return precise
    if j.commute_min:
        return f"~{j.commute_min} min from home (via {j.nearest_station})"
    place = j.location or j.location_normalized or "n/a"
    if j.location_normalized in ("Vancouver", "Hybrid"):
        return f"{place} (in metro — no estimate, city only)"
    return place


_QUAL_BADGE = {
    "qualified": "qualified",
    "stretch": "stretch",
    "reach": "reach",
    "overqualified": "overqual",
}

# Fixed column widths so rows line up regardless of terminal/font — deliberately
# ASCII-only for the flag/tag markers, since glyphs like ★/✗ render at an
# inconsistent width in the classic Windows console (conhost), breaking alignment.
_IDX_W, _FLAG_W, _SCORE_W, _BAR_W, _QUAL_W, _STATUS_W, _SRC_W, _COMPANY_W, _TITLE_W = 3, 1, 4, 16, 9, 6, 17, 24, 40

_STAGE_CODE = {
    "applied": "AP",
    "interviewing": "IV",
    "offer": "OF",
    "denied": "DN",
    "withdrawn": "WD",
}


def _qual(job) -> str:
    return _QUAL_BADGE.get(job.qualification or "", "?")


def _status_code(job) -> str:
    # Priority order: application stage is the most important thing to spot
    # at a glance, then plain "interested" if no stage has been set yet.
    if job.stage:
        return _STAGE_CODE.get(job.stage, "?")
    if job.saved:
        return "S"
    return ""


def list_view(jobs: list, show_all: bool, filters: list[tuple[str, str]] | None = None) -> None:
    auto_dq = [j for j in jobs if j.disqualifier]
    user_dismissed = [j for j in jobs if j.dismissed and not j.disqualifier]
    duplicates = [j for j in jobs if j.duplicate_of and not j.disqualifier and not j.dismissed]
    live = [j for j in jobs if not j.disqualifier and not j.dismissed and not j.duplicate_of]
    rows = jobs if show_all else live
    if filters:
        rows = apply_filters(rows, filters)

    by_id = {j.id: i for i, j in enumerate(jobs)}

    header = (f"\n  {'#':>{_IDX_W}} {'':<{_FLAG_W}} {'score':<{_SCORE_W}} "
              f"{'fit':<{_BAR_W}} {'qual':<{_QUAL_W}} {'status':<{_STATUS_W}} "
              f"{'source':<{_SRC_W}} {'company':<{_COMPANY_W}} title")
    print(header)
    print("  " + "-" * (len(header.strip("\n")) - 2))
    for i, j in enumerate(jobs):  # index over full list so `show.py N` is stable
        if j not in rows:
            continue
        flag = "*" if j.score >= 0.8 else " "
        tags = []
        if j.disqualifier:
            tags.append(f"[X] {j.disqualifier}")
        if j.dismissed:
            tags.append("[dismissed]")
        if j.duplicate_of:
            keeper_row = by_id.get(j.duplicate_of)
            tags.append(f"[dup of #{keeper_row}]" if keeper_row is not None else "[duplicate]")
        tag = ("  " + "  ".join(tags)) if tags else ""
        qual = "" if j.disqualifier else _qual(j)
        company = j.company or ""
        print(f"  {i:>{_IDX_W}} {flag:<{_FLAG_W}} {j.score:.2f} {_bar(j.score)} "
              f"{qual:<{_QUAL_W}} {_status_code(j):<{_STATUS_W}} "
              f"{j.source[:_SRC_W]:<{_SRC_W}} {company[:_COMPANY_W]:<{_COMPANY_W}} "
              f"{j.title[:_TITLE_W]}{tag}")
    print("  " + "-" * (len(header.strip("\n")) - 2))
    print(f"  {len(rows)} shown, {len(live)} scored, {len(auto_dq)} disqualified, "
          f"{len(user_dismissed)} dismissed, {len(duplicates)} duplicates"
          + ("" if show_all else "  (use --all to see disqualified/dismissed/duplicates)"))
    if filters:
        print("  filters: " + ", ".join(f"{k}{'!=' if neg else '='}{v}" for k, v, neg in filters))
    print("  AP=applied  IV=interviewing  OF=offer  DN=denied  WD=withdrawn  S=saved (interested)")
    print("  Tip: `python show.py <#>` for full detail, `python mark.py <#> <status>` to update it.\n")


def _status_line(job) -> str:
    parts = []
    if job.stage:
        when = job.stage_at.date().isoformat() if job.stage_at else "date unknown"
        parts.append(f"{job.stage.upper()} ({when})")
    if job.saved:
        parts.append("saved (interested)")
    if job.dismissed:
        parts.append("dismissed (not interested)")
    if job.duplicate_of:
        parts.append(f"duplicate of {job.duplicate_of}")
    if not parts:
        parts.append("seen" if job.seen else "new, not yet reviewed")
    return " · ".join(parts)


def detail_view(job, index: int | None = None, full: bool = False) -> None:
    bd = job.score_breakdown if isinstance(job.score_breakdown, dict) else {}
    print(f"\n  {job.title}")
    print(f"  {job.company}  ·  {job.source}"
          + (f"  ·  #{index}" if index is not None else "")
          + f"  ·  id={job.id}")
    print(f"  {job.url}")
    print("  " + "─" * 78)
    print(f"  score        {job.score:.2f} {_bar(job.score)}")
    print(f"  status       {_status_line(job)}")
    if job.disqualifier:
        print(f"  DISQUALIFIED {job.disqualifier}")
    print(f"  role         {job.role_type}")
    print(f"  employment   {job.employment_type or 'unknown'}")
    print(f"  org          {job.org_type} ({job.org_size})")
    print(f"  location     \"{job.location}\"  →  {_LOC_CATEGORY.get(job.location_normalized, job.location_normalized)}")
    print(f"  commute      {_fmt_commute(job)}")
    print(f"  salary       {_fmt_salary(job)}")
    print("  " + "─" * 78)
    yrs = f"{job.required_years}+ yrs" if job.required_years else "yrs n/a"
    print(f"  QUALIFICATION  {(job.qualification or '?').upper()}"
          f"   (posting seniority: {job.seniority or '?'}, {yrs})")
    if job.required_credentials:
        print(f"  credentials  posting wants: {', '.join(job.required_credentials)}")
    if job.missing_requirements:
        print(f"  your gaps    {'; '.join(job.missing_requirements)}")
    print("  " + "─" * 78)
    if job.fit_summary:
        print(f"  fit summary  {job.fit_summary}")
    if job.autonomy_evidence:
        print(f"  autonomy     {job.autonomy_evidence}")
    print("  " + "─" * 78)
    print("  score breakdown:")
    for k, v in bd.items():
        if k.startswith("_") or k == "disqualified":
            continue
        print(f"    {k:16} {v:.2f} {_bar(float(v), 12)}")
    if "_base" in bd:
        print(f"    {'(base/bonus)':16} {bd.get('_base',0):.2f} + {bd.get('_bonus',0):.2f}")
    if "_admin_penalty" in bd:
        print(f"    {'(admin penalty)':16} x{bd['_admin_penalty']}")
    if "_employment_penalty" in bd:
        print(f"    {'(employment penalty)':21} x{bd['_employment_penalty']}")
    if "_qualification_penalty" in bd:
        print(f"    {'(qualification penalty)':21} x{bd['_qualification_penalty']}")
    print("  " + "─" * 78)
    desc = (job.description or "").strip()
    print("  description:\n")
    if full or len(desc) <= 1200:
        print("    " + (desc.replace("\n", "\n    ") or "—"))
    else:
        print("    " + desc[:1200].replace("\n", "\n    "))
        print(f"    … (+{len(desc) - 1200} more chars — rerun with --full to see all of it)")
    print()


_FILTERABLE_FIELDS = {f.name for f in dataclass_fields(Job)}


def _matches(job, key: str, value: str) -> bool:
    actual = getattr(job, key)
    if isinstance(actual, bool):
        return actual == (value.strip().lower() in ("1", "true", "yes", "y"))
    if actual is None:
        return value == ""
    if isinstance(actual, str):
        return value.lower() in actual.lower()
    return str(actual).lower() == value.lower()


def apply_filters(jobs: list, filters: list[tuple[str, str, bool]]) -> list:
    """AND-combine one or more field=value (or field!=value) filters against
    any Job field. Strings match by case-insensitive substring; bools/others
    by exact match. != negates whichever of those a field would use."""
    for key, value, negate in filters:
        if key not in _FILTERABLE_FIELDS:
            print(f"\n  Unknown field {key!r}. Valid fields: "
                  f"{', '.join(sorted(_FILTERABLE_FIELDS))}\n")
            sys.exit(1)
        jobs = [j for j in jobs if _matches(j, key, value) != negate]
    return jobs


def html_report() -> None:
    """Write a full-DB HTML report and open it in the browser."""
    import webbrowser
    from pathlib import Path
    import html_render

    conn = db.connect()
    db.init_db(conn)
    cfg = config.load_config()
    jobs = db.query(conn, include_dismissed=True, include_duplicates=True, order_by="score DESC")
    out_dir = Path(__file__).with_name(cfg.get("delivery", {}).get("digest_dir", "digests"))
    out_dir.mkdir(exist_ok=True)
    path = out_dir / "report.html"
    path.write_text(html_render.report_html(jobs, cfg), encoding="utf-8")
    print(f"  wrote {path}")
    webbrowser.open(path.as_uri())


def main() -> None:
    args = sys.argv[1:]
    if "--html" in args:
        html_report()
        return
    show_all = "--all" in args
    full = "--full" in args
    min_score = 0.25
    filters: list[tuple[str, str, bool]] = []
    consumed: set[int] = set()  # arg indices consumed as a flag's value, not a target
    for i, a in enumerate(args):
        if a == "--min" and i + 1 < len(args):
            min_score = float(args[i + 1])
            consumed.add(i + 1)
        elif a == "--filter" and i + 1 < len(args):
            raw = args[i + 1]
            if "!=" in raw:
                key, _, value = raw.partition("!=")
                filters.append((key, value, True))
            else:
                key, _, value = raw.partition("=")
                filters.append((key, value, False))
            consumed.add(i + 1)
    positional = [a for i, a in enumerate(args)
                  if i not in consumed and not a.startswith("--")]

    conn = db.connect()
    db.init_db(conn)
    jobs = db.query(conn, include_dismissed=True, include_duplicates=True,
                    min_score=min_score or None, order_by="score DESC")

    # Detail request: a bare integer (row #) or a job id. Row numbers always
    # refer to this full, unfiltered list — see list_view's own comment on why.
    target = positional[0] if positional else None
    if target is not None:
        if target.isdigit() and int(target) < len(jobs):
            index = int(target)
            detail_view(jobs[index], index, full)
        else:
            job = db.get(conn, target)
            if not job:
                print(f"  no job with index/id {target!r}")
            else:
                index = next((i for i, j in enumerate(jobs) if j.id == job.id), None)
                detail_view(job, index, full)
        return

    if not jobs:
        print("\n  No jobs in the database yet. Run:  python scrape.py --all\n")
        return
    list_view(jobs, show_all, filters)


if __name__ == "__main__":
    main()
