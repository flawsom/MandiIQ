"""The freshness contract: /health may not claim fresh data it does not have.

This is the regression that let MandiIQ serve July prices behind a green
checkmark for three months. The old endpoint returned the literal string
"healthy" no matter what the warehouse contained, so every monitor in the repo
happily agreed with it.

Each test below builds a real DuckDB warehouse in a temp directory, points the
API at it, and reads the health payload the way a monitor would.
"""

from __future__ import annotations

import asyncio
import datetime

import pytest

from mandi_rdd.api import main
from mandi_rdd.storage import duckdb_store


def _record(arrival_date: str, commodity: str = "Onion") -> dict:
    return {
        "state": "Maharashtra",
        "district": "Pune",
        "market": "Pune",
        "commodity": commodity,
        "variety": "Other",
        "grade": "FAQ",
        "arrival_date": arrival_date,
        "min_price": 1000.0,
        "max_price": 1500.0,
        "modal_price": 1200.0,
    }


def _build(tmp_path, monkeypatch, rows: list[dict]):
    """Create a warehouse at a temp path and aim the API at it."""
    db = tmp_path / "freshness.duckdb"
    monkeypatch.setattr(duckdb_store, "DB_PATH", db)
    main._QUALITY_CACHE.clear()
    conn = duckdb_store.get_connection(db_path=db, read_only=False)
    duckdb_store.init_schema(conn)
    if rows:
        duckdb_store.upsert_prices(conn, rows)
    conn.close()
    return db


def _health() -> main.HealthResponse:
    return asyncio.run(main.health())


@pytest.fixture(autouse=True)
def _clear_caches():
    main._QUALITY_CACHE.clear()
    yield
    main._QUALITY_CACHE.clear()


def test_health_refuses_to_call_two_month_old_prices_fresh(tmp_path, monkeypatch):
    """The exact failure this contract exists for."""
    old = (datetime.date.today() - datetime.timedelta(days=64)).isoformat()
    _build(tmp_path, monkeypatch, [_record(old)])

    payload = _health()

    assert payload.n_prices == 1, "the warehouse must be the one under test"
    assert payload.days_behind == 64
    assert payload.status == "stale"
    assert payload.status != "healthy"


def test_health_calls_data_from_yesterday_healthy(tmp_path, monkeypatch):
    """The counter-test: freshness must still be reported when it is real."""
    recent = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    _build(tmp_path, monkeypatch, [_record(recent)])

    payload = _health()

    assert payload.days_behind == 1
    assert payload.status == "healthy"


def test_health_is_degraded_by_an_impossible_arrival_date(tmp_path, monkeypatch):
    """A future date means the month/year mis-parse got past the ingest guard.

    upsert_prices refuses these anyway, so the row is written straight into the
    table the way the July data reached it.
    """
    future = (datetime.date.today() + datetime.timedelta(days=70)).isoformat()
    db = _build(tmp_path, monkeypatch, [])
    conn = duckdb_store.get_connection(db_path=db, read_only=False)
    conn.execute(
        """INSERT INTO prices
           (state, district, market, commodity, variety, grade, arrival_date,
            min_price, max_price, modal_price)
           VALUES ('Maharashtra', 'Pune', 'Pune', 'Onion', 'Other', 'FAQ', ?, 1, 2, 3)""",
        [future],
    )
    conn.close()
    main._QUALITY_CACHE.clear()

    payload = _health()

    assert payload.n_future_dates == 1
    assert payload.days_behind == -70
    assert payload.status == "degraded"


def test_health_says_empty_rather_than_healthy_for_an_empty_warehouse(tmp_path, monkeypatch):
    _build(tmp_path, monkeypatch, [])

    payload = _health()

    assert payload.n_prices == 0
    assert payload.status == "empty"


def test_health_exposes_the_freshness_numbers_a_monitor_needs(tmp_path, monkeypatch):
    """Every claim on /health must be checkable from /health itself."""
    recent = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()
    _build(tmp_path, monkeypatch, [_record(recent), _record(recent, commodity="Tomato")])

    payload = _health()

    assert payload.data_max_date == recent
    assert payload.days_behind == 2
    assert payload.n_commodities == 2
    assert payload.version == main.app.version


def test_stale_production_fails_the_external_freshness_gate():
    """The workflow gate must reject a payload that looks active but is not."""
    from mandi_rdd.scripts.check_production_freshness import evaluate


    # A pipeline that ran a minute ago, over a warehouse with no dated rows:
    # exactly the shape of the July-August outage.
    report = {
        "reachable": True,
        "health": {
            "status": "healthy",
            "days_behind": None,
            "data_max_date": None,
            "n_future_dates": 0,
            "last_outcome": "success",
            "hours_since_last_run": 0.02,
        },
    }
    problems = evaluate(report)
    assert problems, "a run that ingested nothing must not pass the gate"
    assert any("days_behind" in p for p in problems), problems


def test_fresh_production_passes_the_external_freshness_gate():
    from mandi_rdd.scripts.check_production_freshness import evaluate

    report = {
        "reachable": True,
        "health": {
            "status": "healthy",
            "days_behind": 2,
            "data_max_date": "2026-09-28",
            "n_future_dates": 0,
            "last_outcome": "success",
            "hours_since_last_run": 1.5,
        },
        "data_quality": {"n_future_dates": 0},
    }
    assert evaluate(report) == []
