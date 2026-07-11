from __future__ import annotations

import pytest

from parsers.salary_cad import parse_salary


def test_k_range():
    assert parse_salary("Salary: $75K - $95K annually") == (75000, 95000, "$75K - $95K")


def test_full_number_range():
    lo, hi, raw = parse_salary("$75,000 - $95,000 per year")
    assert (lo, hi) == (75000, 95000)


def test_single_k_plus():
    lo, hi, raw = parse_salary("Starting at $80K+")
    assert (lo, hi) == (80000, None)


def test_hourly_range_annualizes():
    lo, hi, raw = parse_salary("$35 - $45/hour")
    assert lo == int(35 * 2080)
    assert hi == int(45 * 2080)


def test_hourly_single_annualizes():
    lo, hi, raw = parse_salary("$40/hr")
    assert lo == int(40 * 2080)
    assert hi is None


def test_non_cad_currency_is_skipped():
    assert parse_salary("$90,000 USD") == (None, None, None)
    assert parse_salary("GBP 45,000") == (None, None, None)
    assert parse_salary("salary of £45,000") == (None, None, None)
    assert parse_salary("€45,000 per year") == (None, None, None)


def test_no_salary_mentioned():
    assert parse_salary("Join our growing team of designers.") == (None, None, None)


def test_empty_text():
    assert parse_salary("") == (None, None, None)
    assert parse_salary(None) == (None, None, None)


@pytest.mark.parametrize("text", [
    "$5K",       # below plausible floor as an annual salary
    "$999K",     # above plausible ceiling
])
def test_implausible_values_are_dropped(text):
    lo, hi, raw = parse_salary(text)
    assert lo is None and hi is None
