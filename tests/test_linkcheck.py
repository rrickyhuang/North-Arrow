from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import requests

import db
import linkcheck
from conftest import make_job


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()


class _FakeResp:
    def __init__(self, status_code):
        self.status_code = status_code


def _fake_head(status_code):
    return lambda *a, **kw: _FakeResp(status_code)


def _boom(*a, **kw):
    raise requests.exceptions.ConnectionError("boom")


def test_check_url_404_is_dead(monkeypatch):
    monkeypatch.setattr(linkcheck.requests, "head", _fake_head(404))
    assert linkcheck.check_url("https://example.com/job/1") is True


def test_check_url_200_is_alive(monkeypatch):
    monkeypatch.setattr(linkcheck.requests, "head", _fake_head(200))
    assert linkcheck.check_url("https://example.com/job/1") is False


def test_check_url_bot_blocked_is_inconclusive(monkeypatch):
    """403 (or any other 4xx we don't explicitly trust) must not be treated as
    dead — an anti-scraping block looks identical to a real 403 and would
    otherwise wrongly bury a live posting."""
    monkeypatch.setattr(linkcheck.requests, "head", _fake_head(403))
    assert linkcheck.check_url("https://example.com/job/1") is None


def test_check_url_network_error_is_inconclusive(monkeypatch):
    monkeypatch.setattr(linkcheck.requests, "head", _boom)
    assert linkcheck.check_url("https://example.com/job/1") is None


def test_check_url_empty_url_is_inconclusive():
    assert linkcheck.check_url("") is None


def test_check_url_falls_back_to_get_on_405(monkeypatch):
    calls = []
    monkeypatch.setattr(linkcheck.requests, "head", _fake_head(405))
    monkeypatch.setattr(linkcheck.requests, "get",
                        lambda *a, **kw: calls.append(1) or _FakeResp(200))
    assert linkcheck.check_url("https://example.com/job/1") is False
    assert calls == [1]


def test_run_marks_dead_job_and_never_rechecks_it(conn, cfg, monkeypatch):
    job = make_job()
    db.upsert(conn, job)
    calls = {"n": 0}

    def fake_check(url):
        calls["n"] += 1
        return True
    monkeypatch.setattr(linkcheck, "check_url", fake_check)

    stats = linkcheck.run(conn, cfg)
    assert stats == {"checked": 1, "dead": 1, "inconclusive": 0}
    assert db.get(conn, job.id).link_dead is True

    # A second run must skip it — link_dead jobs are never re-checked.
    stats2 = linkcheck.run(conn, cfg)
    assert stats2 == {"checked": 0, "dead": 0, "inconclusive": 0}
    assert calls["n"] == 1


def test_run_skips_jobs_checked_recently(conn, cfg, monkeypatch):
    job = make_job()
    db.upsert(conn, job)
    db.set_link_dead(conn, job.id, False)  # "confirmed alive just now"

    calls = {"n": 0}
    def fake_check(url):
        calls["n"] += 1
        return True
    monkeypatch.setattr(linkcheck, "check_url", fake_check)

    stats = linkcheck.run(conn, cfg)
    assert stats == {"checked": 0, "dead": 0, "inconclusive": 0}
    assert calls["n"] == 0


def test_run_rechecks_after_recheck_interval_elapses(conn, cfg, monkeypatch):
    job = make_job()
    db.upsert(conn, job)
    db.set_link_dead(conn, job.id, False)
    stale_check = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    conn.execute("UPDATE jobs SET link_checked_at = ? WHERE id = ?", (stale_check, job.id))
    conn.commit()

    monkeypatch.setattr(linkcheck, "check_url", lambda url: True)
    stats = linkcheck.run(conn, cfg)
    assert stats["checked"] == 1
    assert stats["dead"] == 1


def test_run_skips_dismissed_and_duplicate_jobs(conn, cfg, monkeypatch):
    dismissed = make_job()
    dup = make_job()
    keeper = make_job()
    for j in (dismissed, dup, keeper):
        db.upsert(conn, j)
    db.set_state(conn, dismissed.id, dismissed=True)
    db.set_duplicate(conn, dup.id, keeper.id)

    monkeypatch.setattr(linkcheck, "check_url", lambda url: True)
    stats = linkcheck.run(conn, cfg)
    assert stats["checked"] == 1  # only `keeper`


def test_run_leaves_inconclusive_jobs_uncheckedat(conn, cfg, monkeypatch):
    job = make_job()
    db.upsert(conn, job)

    monkeypatch.setattr(linkcheck, "check_url", lambda url: None)
    stats = linkcheck.run(conn, cfg)
    assert stats == {"checked": 1, "dead": 0, "inconclusive": 1}
    fetched = db.get(conn, job.id)
    assert fetched.link_dead is False
    assert fetched.link_checked_at is None
