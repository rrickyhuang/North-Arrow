"""SQLite persistence for jobs.

One table, `jobs`, keyed by our stable `id`. Complex fields (lists/dicts/datetimes)
are JSON-encoded on the way in and decoded on the way out, so a row round-trips
back into a `Job` dataclass cleanly.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from dataclasses import fields as dataclass_fields
from pathlib import Path

from dateutil import parser as date_parser

from models import Job

DB_PATH = Path(__file__).with_name("jobs.db")

# Fields that need JSON or ISO encoding rather than raw scalar storage.
_JSON_FIELDS = {"skills_leverage", "score_breakdown",
                "required_credentials", "missing_requirements"}
_DATETIME_FIELDS = {"posted_at", "scraped_at", "stage_at"}
_BOOL_FIELDS = {
    "is_remote", "has_design_autonomy", "has_mixed_role", "has_variety",
    "is_admin_heavy", "is_drafting_only", "is_hierarchical",
    "enriched", "is_new", "seen", "saved", "dismissed",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                  TEXT PRIMARY KEY,
    source              TEXT NOT NULL,
    external_id         TEXT NOT NULL,
    url                 TEXT,
    title               TEXT,
    company             TEXT,
    location            TEXT,
    location_normalized TEXT,
    location_lat        REAL,
    location_lng        REAL,
    nearest_station     TEXT,
    commute_min         INTEGER,
    commute_min_precise INTEGER,
    is_remote           INTEGER,
    salary_min          INTEGER,
    salary_max          INTEGER,
    salary_raw          TEXT,
    role_type           TEXT,
    employment_type     TEXT,
    org_type            TEXT,
    org_size            TEXT,
    has_design_autonomy INTEGER,
    has_mixed_role      INTEGER,
    has_variety         INTEGER,
    is_admin_heavy      INTEGER,
    is_drafting_only    INTEGER,
    is_hierarchical     INTEGER,
    skills_leverage     TEXT,
    autonomy_evidence   TEXT,
    fit_summary         TEXT,
    seniority           TEXT,
    required_years      INTEGER,
    required_credentials TEXT,
    qualification       TEXT,
    missing_requirements TEXT,
    posted_at           TEXT,
    scraped_at          TEXT,
    description         TEXT,
    score               REAL,
    score_breakdown     TEXT,
    disqualifier        TEXT,
    enriched            INTEGER,
    is_new              INTEGER,
    seen                INTEGER,
    saved               INTEGER,
    dismissed           INTEGER,
    stage               TEXT,
    stage_at            TEXT,
    notes               TEXT NOT NULL DEFAULT '',
    duplicate_of        TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_new ON jobs(is_new);
"""

_COLUMNS = [f.name for f in dataclass_fields(Job)]


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def backup_db(db_path: Path | str = DB_PATH, *, keep: int = 14) -> Path | None:
    """Snapshot the DB to `backups/jobs-YYYYMMDD-HHMMSS.db`, keeping the newest
    `keep` snapshots. Uses SQLite's online-backup API, so it's safe to run
    against a live/in-use database. Returns the snapshot path, or None if the
    source DB doesn't exist yet. Snapshots match `*.db` so they're gitignored.

    This is the durability net for application-tracking data (stages, notes,
    interested/dismissed flags) that can't be re-scraped — call it after any
    run that may have changed it."""
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    backup_dir = db_path.with_name("backups")
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"{db_path.stem}-{stamp}.db"

    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    # Rotate: keep only the newest `keep` snapshots (sorted by name == by time).
    snapshots = sorted(backup_dir.glob(f"{db_path.stem}-*.db"))
    for old in snapshots[:-keep]:
        old.unlink()
    return dest


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add any columns missing from an older jobs.db (CREATE IF NOT EXISTS won't
    alter an existing table). Keeps existing data intact."""
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
    added = {
        "seniority": "TEXT", "required_years": "INTEGER",
        "required_credentials": "TEXT", "qualification": "TEXT",
        "missing_requirements": "TEXT",
        "stage": "TEXT", "stage_at": "TEXT",
        "employment_type": "TEXT",
        "duplicate_of": "TEXT",
        "commute_min_precise": "INTEGER",
        "notes": "TEXT NOT NULL DEFAULT ''",
    }
    stage_is_new = "stage" not in existing
    for col, typ in added.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typ}")
    # One-time backfill: older DBs tracked a plain applied/applied_at pair
    # before the fuller applied->interviewing->offer/denied/withdrawn pipeline.
    if stage_is_new and "applied" in existing:
        conn.execute(
            "UPDATE jobs SET stage = 'applied', stage_at = applied_at "
            "WHERE applied = 1"
        )
    conn.commit()


# ── encode / decode ──────────────────────────────────────────────────────────
def _encode(job: Job) -> dict:
    raw = job.to_row()
    out: dict = {}
    for k, v in raw.items():
        if k in _JSON_FIELDS:
            out[k] = json.dumps(v) if v is not None else None
        elif k in _DATETIME_FIELDS:
            out[k] = v.isoformat() if isinstance(v, datetime) else (v or None)
        elif k in _BOOL_FIELDS:
            out[k] = None if v is None else int(bool(v))
        else:
            out[k] = v
    return out


def _decode(row: sqlite3.Row) -> Job:
    d = dict(row)
    for k in _JSON_FIELDS:
        d[k] = json.loads(d[k]) if d.get(k) else ([] if k == "skills_leverage" else {})
    for k in _DATETIME_FIELDS:
        if d.get(k):
            try:
                d[k] = date_parser.isoparse(d[k])
            except (ValueError, TypeError):
                d[k] = None
    for k in _BOOL_FIELDS:
        if d.get(k) is not None:
            d[k] = bool(d[k])
    return Job(**{k: d.get(k) for k in _COLUMNS})


# ── operations ───────────────────────────────────────────────────────────────
def upsert(conn: sqlite3.Connection, job: Job) -> bool:
    """Insert a new job or update mutable fields of an existing one.

    Returns True if this was a brand-new row, False if it already existed.
    Preserves user state (seen/saved/dismissed) on update.
    """
    existing = conn.execute("SELECT id FROM jobs WHERE id = ?", (job.id,)).fetchone()
    data = _encode(job)
    if existing is None:
        cols = ", ".join(data.keys())
        placeholders = ", ".join(f":{k}" for k in data.keys())
        conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({placeholders})", data)
        conn.commit()
        return True
    # Update everything EXCEPT user/workflow state and is_new.
    # duplicate_of is dedup.py's call, not a re-scrape's — a job's own fields
    # can change on re-scrape without that verdict needing to be recomputed.
    protected = {"id", "seen", "saved", "dismissed", "is_new", "stage", "stage_at",
                 "notes", "duplicate_of", "commute_min_precise"}
    updates = {k: v for k, v in data.items() if k not in protected}
    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    updates["id"] = job.id
    conn.execute(f"UPDATE jobs SET {set_clause} WHERE id = :id", updates)
    conn.commit()
    return False


def get(conn: sqlite3.Connection, job_id: str) -> Job | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _decode(row) if row else None


def query(
    conn: sqlite3.Connection,
    *,
    min_score: float | None = None,
    new_only: bool = False,
    include_dismissed: bool = False,
    include_duplicates: bool = False,
    order_by: str = "score DESC",
    limit: int | None = None,
) -> list[Job]:
    sql = "SELECT * FROM jobs WHERE 1=1"
    params: list = []
    if min_score is not None:
        sql += " AND score >= ?"
        params.append(min_score)
    if new_only:
        sql += " AND is_new = 1"
    if not include_dismissed:
        sql += " AND dismissed = 0"
    if not include_duplicates:
        sql += " AND duplicate_of IS NULL"
    sql += f" ORDER BY {order_by}"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return [_decode(r) for r in conn.execute(sql, params).fetchall()]


def set_state(conn: sqlite3.Connection, job_id: str, **flags) -> None:
    """Update workflow flags, e.g. set_state(conn, id, seen=True, saved=True)."""
    allowed = {"seen", "saved", "dismissed", "is_new", "enriched"}
    updates = {k: int(bool(v)) for k, v in flags.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE jobs SET {set_clause} WHERE id = ?",
        (*updates.values(), job_id),
    )
    conn.commit()


STAGES = ("applied", "interviewing", "offer", "denied", "withdrawn")


def set_stage(conn: sqlite3.Connection, job_id: str, stage: str | None) -> None:
    """Set the application-pipeline stage (or None to clear it), stamping
    stage_at with when this stage was set."""
    when = datetime.now(timezone.utc).isoformat() if stage else None
    conn.execute(
        "UPDATE jobs SET stage = ?, stage_at = ? WHERE id = ?",
        (stage, when, job_id),
    )
    conn.commit()


def set_duplicate(conn: sqlite3.Connection, job_id: str, duplicate_of: str | None) -> None:
    """Mark (or clear, with None) job_id as a re-post of another stored job.
    Set by dedup.py; see models.Job.duplicate_of."""
    conn.execute(
        "UPDATE jobs SET duplicate_of = ? WHERE id = ?",
        (duplicate_of, job_id),
    )
    conn.commit()


def set_precise_commute(conn: sqlite3.Connection, job_id: str, minutes: int) -> None:
    """Cache a real Google transit time for a job. See models.Job.commute_min_precise."""
    conn.execute(
        "UPDATE jobs SET commute_min_precise = ? WHERE id = ?",
        (minutes, job_id),
    )
    conn.commit()


def set_notes(conn: sqlite3.Connection, job_id: str, notes: str) -> None:
    """Save free-text application notes for a job. See models.Job.notes."""
    conn.execute(
        "UPDATE jobs SET notes = ? WHERE id = ?",
        (notes or "", job_id),
    )
    conn.commit()


def update_score(conn: sqlite3.Connection, job_id: str, score: float,
                 breakdown: dict, disqualifier: str | None) -> None:
    conn.execute(
        "UPDATE jobs SET score = ?, score_breakdown = ?, disqualifier = ? WHERE id = ?",
        (score, json.dumps(breakdown), disqualifier, job_id),
    )
    conn.commit()


def clear_new_flags(conn: sqlite3.Connection) -> None:
    """Call after a digest is delivered so next run's 'new' is accurate."""
    conn.execute("UPDATE jobs SET is_new = 0 WHERE is_new = 1")
    conn.commit()


def all_external_ids(conn: sqlite3.Connection, source: str) -> set[str]:
    rows = conn.execute("SELECT external_id FROM jobs WHERE source = ?", (source,))
    return {r["external_id"] for r in rows}
