"""Read-only viewer for the jobs database.

    python show.py              ranked list (scored jobs only)
    python show.py --all        include disqualified jobs
    python show.py --min 0.6    only jobs at/above a score
    python show.py 3            full detail for row #3 from the list
    python show.py <job_id>     full detail by id

Touches nothing — just prints. Safe to run any time.
"""
from __future__ import annotations

import sys

import config
import db

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
    if j.commute_min:
        return f"~{j.commute_min} min from home (via {j.nearest_station})"
    place = j.location or j.location_normalized or "n/a"
    if j.location_normalized in ("Vancouver", "Hybrid"):
        return f"{place} (in metro — no estimate, city only)"
    return place


_QUAL_BADGE = {
    "qualified": "qualified",
    "stretch": "stretch  ",
    "reach": "reach    ",
    "overqualified": "overqual ",
}


def _qual(job) -> str:
    return _QUAL_BADGE.get(job.qualification or "", "    ?    ")


def list_view(jobs: list, show_all: bool) -> None:
    live = [j for j in jobs if not j.disqualifier]
    dead = [j for j in jobs if j.disqualifier]
    rows = jobs if show_all else live

    print(f"\n  {'#':>2}  score              qual       source   title")
    print("  " + "─" * 84)
    for i, j in enumerate(jobs):  # index over full list so `show.py N` is stable
        if j not in rows:
            continue
        flag = "★" if j.score >= 0.8 else " "
        tag = f"  ✗ {j.disqualifier}" if j.disqualifier else ""
        qual = "         " if j.disqualifier else _qual(j)
        print(f"  {i:>2}{flag} {j.score:.2f} {_bar(j.score)} {qual} {j.source:<8} {j.title[:36]}{tag}")
    print("  " + "─" * 78)
    print(f"  {len(live)} scored · {len(dead)} disqualified"
          + ("" if show_all else "  (use --all to see disqualified)"))
    print("  Tip: `python show.py <#>` for full detail.\n")


def detail_view(job) -> None:
    bd = job.score_breakdown if isinstance(job.score_breakdown, dict) else {}
    print(f"\n  {job.title}")
    print(f"  {job.company}  ·  {job.source}")
    print(f"  {job.url}")
    print("  " + "─" * 78)
    print(f"  score        {job.score:.2f} {_bar(job.score)}")
    if job.disqualifier:
        print(f"  DISQUALIFIED {job.disqualifier}")
    print(f"  role         {job.role_type}")
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
    print("  " + "─" * 78)
    desc = (job.description or "").strip()
    print("  description:\n")
    print("    " + (desc[:1200].replace("\n", "\n    ") or "—"))
    if len(desc) > 1200:
        print(f"    … (+{len(desc) - 1200} more chars)")
    print()


def html_report() -> None:
    """Write a full-DB HTML report and open it in the browser."""
    import webbrowser
    from pathlib import Path
    import html_render

    conn = db.connect()
    db.init_db(conn)
    cfg = config.load_config()
    jobs = db.query(conn, include_dismissed=True, order_by="score DESC")
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
    min_score = 0.0
    if "--min" in args:
        i = args.index("--min")
        min_score = float(args[i + 1])
    positional = [a for a in args if not a.startswith("--")
                  and not (a.replace(".", "").isdigit() and args[args.index(a) - 1] == "--min")]

    conn = db.connect()
    db.init_db(conn)
    jobs = db.query(conn, include_dismissed=True, min_score=min_score or None,
                    order_by="score DESC")

    # Detail request: a bare integer (row #) or a job id.
    target = next((a for a in positional if a not in ("--all",)), None)
    if target is not None:
        if target.isdigit() and int(target) < len(jobs):
            detail_view(jobs[int(target)])
        else:
            job = db.get(conn, target)
            detail_view(job) if job else print(f"  no job with index/id {target!r}")
        return

    if not jobs:
        print("\n  No jobs in the database yet. Run:  python scrape.py --all\n")
        return
    list_view(jobs, show_all)


if __name__ == "__main__":
    main()
