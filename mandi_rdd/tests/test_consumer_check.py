"""Staleness must be attributed before it is graded.

``days_behind`` on its own cannot tell "data.gov.in has not published a new day
yet" from "our ingest dropped the rows upstream is serving". The first is a
fact about the source and only deserves a warning; the second is a defect in
this project and deserves a blocker. These tests pin both shapes, plus the
shape in between (commodities stopped on different dates), so a future edit
cannot quietly turn an upstream holiday into a red pipeline - or the reverse.
"""

from __future__ import annotations

from mandi_rdd.scripts.check_production_freshness import evaluate
from mandi_rdd.scripts.consumer_check import (
    check_provenance,
    pipeline_failure_signals,
    upstream_lag_signature,
)

SHARED_DATE = "2026-09-25"


def _health(**overrides) -> dict:
    health = {
        "status": "stale",
        "days_behind": 5,
        "data_max_date": SHARED_DATE,
        "n_future_dates": 0,
        "last_outcome": "success",
        "refresh_runs": 3,
        "refresh_failures": 0,
        "last_refresh_error": None,
    }
    health.update(overrides)
    return health


def _freshness(dates: list[str]) -> list[dict]:
    return [
        {"commodity": f"Commodity {i}", "latest_date": date}
        for i, date in enumerate(dates)
    ]


def _levels(findings: list[dict]) -> list[str]:
    return [finding["level"] for finding in findings]


def test_upstream_lag_signature_requires_one_shared_date():
    rows = _freshness([SHARED_DATE, SHARED_DATE, SHARED_DATE])
    assert upstream_lag_signature(rows, SHARED_DATE) == {
        "n_commodities": 3,
        "latest_date": SHARED_DATE,
    }
    # One commodity ahead of the rest means rows exist upstream that we missed.
    mixed = _freshness([SHARED_DATE, "2026-09-24", SHARED_DATE])
    assert upstream_lag_signature(mixed, SHARED_DATE) is None
    # A shared date that disagrees with /health is not an upstream signal.
    assert upstream_lag_signature(rows, "2026-09-24") is None


def test_upstream_lag_signature_ignores_missing_or_malformed_rows():
    assert upstream_lag_signature(None, SHARED_DATE) is None
    assert upstream_lag_signature([], SHARED_DATE) is None
    assert upstream_lag_signature([{"commodity": "Onion"}], SHARED_DATE) is None
    assert upstream_lag_signature("not-a-list", SHARED_DATE) is None


def test_days_behind_from_upstream_is_a_warning_not_a_blocker():
    findings = check_provenance(
        _health(), {}, _freshness([SHARED_DATE] * 200)
    )
    assert "blocker" not in _levels(findings), findings
    assert any(
        finding["level"] == "warning" and "upstream publication lag" in finding["message"]
        for finding in findings
    ), findings


def test_pipeline_failure_remains_a_blocker_even_when_dates_align():
    findings = check_provenance(
        _health(
            last_outcome="failure",
            refresh_runs=1,
            refresh_failures=1,
            last_refresh_error="Invalid Input Error: Failed to delete all rows from index",
        ),
        {},
        _freshness([SHARED_DATE] * 200),
    )
    blockers = [f for f in findings if f["level"] == "blocker"]
    assert blockers, findings
    assert any("pipeline stall" in f["message"] for f in blockers), blockers


def test_divergent_commodity_dates_are_a_pipeline_blocker():
    findings = check_provenance(
        _health(), {}, _freshness([SHARED_DATE, "2026-09-24", SHARED_DATE])
    )
    assert any(
        finding["level"] == "blocker" and "ingest is behind upstream" in finding["message"]
        for finding in findings
    ), findings


def test_pipeline_failure_signals_are_empty_for_a_clean_health_payload():
    assert pipeline_failure_signals(_health()) == []
    signals = pipeline_failure_signals(
        _health(last_outcome="failure", refresh_failures=3)
    )
    assert any("last ingest run failed" in signal for signal in signals), signals
    assert any("self-refresh" in signal for signal in signals), signals


def test_a_recorded_index_fault_is_a_blocker_not_a_warning():
    """A fault the heal has not cleared yet means writes are failing, and the
    consumer sees numbers that stop moving - that is a stall, not a detail."""
    findings = check_provenance(
        _health(index_fault_pending=True), {}, _freshness([SHARED_DATE] * 200)
    )
    assert any(
        finding["level"] == "blocker" and "inconsistent prices index" in finding["message"]
        for finding in findings
    ), findings


def test_a_clean_index_report_stays_quiet():
    findings = check_provenance(
        _health(
            index_fault_pending=False,
            last_index_check={"rebuilt": False, "trigger": None, "source": "startup_probe"},
        ),
        {},
        _freshness([SHARED_DATE] * 200),
    )
    assert "blocker" not in _levels(findings), findings


def test_freshness_gate_notices_upstream_lag_without_failing():
    report = {
        "health": _health(days_behind=6),
        "freshness": _freshness([SHARED_DATE] * 436),
        "problems": [],
    }
    problems = evaluate(report)
    assert problems == [], problems
    assert any("upstream publication lag" in note for note in report["notes"]), report


def test_freshness_gate_still_fails_when_our_ingest_is_the_cause():
    report = {
        "health": _health(days_behind=6, last_outcome="failure", refresh_runs=1,
                          refresh_failures=1, last_refresh_error="index fault"),
        "freshness": _freshness([SHARED_DATE] * 436),
        "problems": [],
    }
    problems = evaluate(report)
    assert any("pipeline stall" in problem for problem in problems), problems


def test_freshness_gate_blocks_when_commodities_stop_on_different_dates():
    report = {
        "health": _health(days_behind=6),
        "freshness": _freshness([SHARED_DATE, "2026-09-24"]),
        "problems": [],
    }
    problems = evaluate(report)
    assert any("ingest is behind upstream" in problem for problem in problems), problems
