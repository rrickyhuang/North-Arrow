from __future__ import annotations

import logging

import scorer
from conftest import make_job


def test_out_of_metro_onsite_is_hard_disqualified(cfg):
    job = make_job(location_normalized="Other")
    score, breakdown, disq = scorer.score_job(job, cfg)
    assert score == 0.0
    assert disq == "out_of_metro"
    assert breakdown["disqualified"]


def test_full_weight_job_scores_near_top(cfg):
    job = make_job(
        score_breakdown={"commute": 1.0},
        role_type="urban_design",
        has_design_autonomy=True,
        has_mixed_role=True,
        has_variety=True,
        is_admin_heavy=False,
        is_drafting_only=False,
        salary_min=90000,
    )
    cfg["scoring"]["role_type_scores"] = {"urban_design": 1.0}
    score, breakdown, disq = scorer.score_job(job, cfg)
    assert disq is None
    assert score > 0.9
    assert breakdown["commute"] == 1.0
    assert breakdown["salary"] == 1.0


def test_missing_commute_score_defaults_to_neutral_and_logs(cfg, caplog):
    # No "commute" key in score_breakdown at all — the case #21 fixed: this
    # used to silently default with zero visibility into why.
    job = make_job(score_breakdown={})
    with caplog.at_level(logging.WARNING, logger="scorer"):
        score, breakdown, _disq = scorer.score_job(job, cfg)
    assert breakdown["commute"] == 0.5
    assert any("no precomputed commute score" in r.message for r in caplog.records)


def test_present_commute_score_does_not_warn(cfg, caplog):
    job = make_job(score_breakdown={"commute": 0.7})
    with caplog.at_level(logging.WARNING, logger="scorer"):
        _score, breakdown, _disq = scorer.score_job(job, cfg)
    assert breakdown["commute"] == 0.7
    assert not caplog.records


def test_admin_heavy_applies_soft_penalty_not_a_kill(cfg):
    job = make_job(is_admin_heavy=True)
    score, breakdown, disq = scorer.score_job(job, cfg)
    assert disq is None
    assert breakdown["_admin_penalty"] == cfg["scoring"]["penalties"]["admin_heavy"]


def test_qualification_reach_docks_score(cfg):
    baseline = make_job(qualification=None)
    reach = make_job(qualification="reach")
    base_score, _b1, _d1 = scorer.score_job(baseline, cfg)
    reach_score, breakdown, _d2 = scorer.score_job(reach, cfg)
    assert reach_score < base_score
    assert breakdown["_qualification_penalty"] == cfg["scoring"]["penalties"]["qualification"]["reach"]


def test_salary_below_floor_is_screened_out(cfg):
    job = make_job(salary_min=None, salary_max=60000)  # below salary_floor (68000)
    score, _breakdown, disq = scorer.score_job(job, cfg)
    assert score == 0.0
    assert disq == "below_salary_floor"


def test_salary_at_or_above_floor_passes(cfg):
    job = make_job(salary_min=None, salary_max=68000)
    _score, _breakdown, disq = scorer.score_job(job, cfg)
    assert disq is None


def test_range_straddling_floor_passes(cfg):
    job = make_job(salary_min=60000, salary_max=80000)  # top of range clears the floor
    _score, _breakdown, disq = scorer.score_job(job, cfg)
    assert disq is None


def test_unknown_salary_is_not_penalized(cfg):
    job = make_job(salary_min=None, salary_max=None)
    _score, breakdown, _disq = scorer.score_job(job, cfg)
    assert breakdown["salary"] == 0.5
