"""Mark a job's status: application-pipeline stage (applied, interviewing,
offer, denied, withdrawn), or interested/not-interested/seen. Works on any
job — scraped or manual. Takes one or more row-#s/ids before the status to
mark several jobs in one command.

Usage:
    python mark.py <row-# or id> [<row-# or id> ...] applied
    python mark.py <row-# or id> [<row-# or id> ...] interviewing
    python mark.py <row-# or id> [<row-# or id> ...] offer
    python mark.py <row-# or id> [<row-# or id> ...] denied
    python mark.py <row-# or id> [<row-# or id> ...] withdrawn
    python mark.py <row-# or id> [<row-# or id> ...] interested
    python mark.py <row-# or id> [<row-# or id> ...] not-interested
    python mark.py <row-# or id> [<row-# or id> ...] seen
    python mark.py <row-# or id> [<row-# or id> ...] <status> --clear   # undo

Setting any pipeline stage replaces whatever stage was set before — it's a
single progression, not independent flags. --clear on any stage word resets
it back to "not applied" regardless of which stage word you used.

Dismissed ("not interested") jobs are hidden from `show.py`'s default list —
use `show.py --all` to see them too.
"""
from __future__ import annotations

import logging
import sys

import db
import logutil

logutil.setup_logging()
log = logging.getLogger("mark")

_STAGE_STATUSES = set(db.STAGES)
_BOOL_STATUSES = {
    "interested": "saved",
    "not-interested": "dismissed",
    "seen": "seen",
}
_ALL_STATUSES = sorted(_STAGE_STATUSES | set(_BOOL_STATUSES))


def _resolve_job(conn, target: str):
    if target.isdigit():
        jobs = db.query(conn, include_dismissed=True, include_duplicates=True,
                        order_by="score DESC")
        idx = int(target)
        if idx < len(jobs):
            return jobs[idx]
    return db.get(conn, target)


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--clear"]
    clear = "--clear" in sys.argv[1:]
    if len(args) < 2:
        print(f"\n  Usage: python mark.py <row-# or id> [<row-# or id> ...] "
              f"<{'|'.join(_ALL_STATUSES)}> [--clear]\n")
        sys.exit(1)

    *targets, status = args
    if status not in _STAGE_STATUSES and status not in _BOOL_STATUSES:
        print(f"\n  Unknown status {status!r}. Choose one of: {', '.join(_ALL_STATUSES)}\n")
        sys.exit(1)

    conn = db.connect()
    db.init_db(conn)
    verb = "Cleared" if clear else "Marked"
    print()
    updated = 0
    for target in targets:
        job = _resolve_job(conn, target)
        if not job:
            print(f"  No job found for {target!r} - skipped")
            continue
        if status in _STAGE_STATUSES:
            db.set_stage(conn, job.id, None if clear else status)
        else:
            db.set_state(conn, job.id, **{_BOOL_STATUSES[status]: not clear})
        print(f"  {verb}: {status} - {job.title} @ {job.company}")
        log.info("%s %s on %s (%s @ %s)", verb.lower(), status, job.id,
                 job.title, job.company)
        updated += 1
    conn.close()
    print(f"\n  {updated}/{len(targets)} updated.\n")


if __name__ == "__main__":
    main()
