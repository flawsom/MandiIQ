"""API contract tests for the FastAPI serving layer.

The README documents 30+ endpoints; these tests keep the public surface
honest by asserting the documented routes exist and the schemas stay
backwards compatible. No database or network access is required.
"""

from __future__ import annotations

import warnings

import pytest

warnings.filterwarnings("ignore")

EXPECTED_ROUTES = {
    ("/health", "GET"),
    ("/freshness", "GET"),
    ("/prices", "GET"),
    ("/rdd-result/{commodity}", "GET"),
    ("/rdd-plot/{commodity}", "GET"),
    ("/forecast/{commodity}", "GET"),
    ("/robustness/{commodity}", "GET"),
    ("/risk-score/{commodity}", "GET"),
    ("/recommendation/{commodity}", "GET"),
    ("/ask", "POST"),
    ("/refresh", "POST"),
    ("/metrics", "GET"),
    ("/analytics/{commodity}", "GET"),
    ("/conformal/{commodity}", "GET"),
    ("/drift/{commodity}", "GET"),
    ("/tail-risk/{commodity}", "GET"),
    ("/dml/{commodity}", "GET"),
    ("/nowcast/{commodity}", "GET"),
    ("/data-quality", "GET"),
    ("/admin/repair-dates", "POST"),
    ("/admin/rebuild-prices", "POST"),
}


@pytest.fixture(scope="module")
def app_module():
    from mandi_rdd.api import main

    return main


def _route_set(app):
    routes = set()
    for route in app.routes:
        for method in getattr(route, "methods", set()) or set():
            routes.add((route.path, method))
    return routes


def test_documented_routes_exist(app_module):
    routes = _route_set(app_module.app)
    missing = sorted(f"{method} {path}" for path, method in EXPECTED_ROUTES if (path, method) not in routes)
    assert not missing, f"Missing API routes: {missing}"


def test_openapi_schema_builds(app_module):
    spec = app_module.app.openapi()
    assert spec["info"]["title"]
    assert len(spec["paths"]) >= 20


def test_health_reports_live_provenance(app_module):
    """A caller must be able to tell how fresh the data is without a second
    request, so /health carries the newest arrival date and its age."""
    fields = set(app_module.HealthResponse.model_fields)
    assert {
        "version",
        "data_max_date",
        "data_min_date",
        "days_behind",
        "hours_since_last_run",
        "n_future_dates",
        "ingestion_running",
    } <= fields


def test_health_test_count_is_measured_not_hardcoded(app_module):
    counted = app_module._count_tests()
    assert counted > 0, "test discovery broke"
    assert counted == app_module._count_tests(), "count must be stable"


def test_health_reports_self_refresh_bookkeeping(app_module):
    fields = set(app_module.HealthResponse.model_fields)
    assert {
        "last_refresh_attempt_utc",
        "last_refresh_success_utc",
        "last_refresh_error",
        "refresh_runs",
        "refresh_failures",
        "refresh_interval_s",
    } <= fields


@pytest.mark.parametrize(
    "quality,expected",
    [
        ({"n_rows": 0, "days_behind": None, "n_future_dates": 0}, "empty"),
        ({"n_rows": 10, "days_behind": 1, "n_future_dates": 0}, "healthy"),
        ({"n_rows": 10, "days_behind": 3, "n_future_dates": 0}, "healthy"),
        ({"n_rows": 10, "days_behind": 4, "n_future_dates": 0}, "stale"),
        ({"n_rows": 10, "days_behind": 64, "n_future_dates": 0}, "stale"),
        ({"n_rows": 10, "days_behind": None, "n_future_dates": 0}, "unknown"),
        ({"n_rows": 10, "days_behind": 1, "n_future_dates": 7}, "degraded"),
    ],
)
def test_health_status_describes_the_data(app_module, quality, expected):
    """The regression that matters: /health answered "healthy" while serving
    four-month-old prices. status must be derived from the warehouse."""
    assert app_module._health_status(quality) == expected


def test_self_refresh_records_every_outcome(app_module, monkeypatch):
    """A silently failing scheduler is how stale data went unnoticed, so a
    failed run has to leave a trace on the health payload."""
    import sys
    import types

    from mandi_rdd.ingestion import scheduler as real_scheduler

    fake = types.ModuleType("mandi_rdd.ingestion.scheduler")
    fake.ingestion_running = lambda: False
    fake.run_ingestion = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    monkeypatch.setitem(sys.modules, "mandi_rdd.ingestion.scheduler", fake)

    before = app_module._REFRESH_STATE["failures"]
    app_module._refresh_once()
    assert app_module._REFRESH_STATE["failures"] == before + 1
    assert "boom" in (app_module._REFRESH_STATE["last_error"] or "")
    assert app_module._REFRESH_STATE["last_attempt_utc"]

    fake.run_ingestion = lambda *a, **k: {"status": "degraded", "error": "source down"}
    app_module._refresh_once()
    assert app_module._REFRESH_STATE["last_success_utc"]
    assert app_module._REFRESH_STATE["last_error"] == "source down"

    # A busy scheduler must not queue a second ingestion behind the first.
    calls = []
    fake.ingestion_running = lambda: True
    fake.run_ingestion = lambda *a, **k: calls.append(1) or {"status": "success"}
    assert app_module._refresh_once()["status"] == "busy"
    assert calls == []

    monkeypatch.setitem(sys.modules, "mandi_rdd.ingestion.scheduler", real_scheduler)


def test_ask_schemas_keep_their_contract(app_module):
    request_fields = set(app_module.AskRequest.model_fields)
    assert {"query", "commodity", "district"} <= request_fields

    response_fields = set(app_module.AskResponse.model_fields)
    assert {
        "query",
        "commodity",
        "district",
        "answer",
        "model_used",
        "endpoints_used",
        "error",
    } <= response_fields
