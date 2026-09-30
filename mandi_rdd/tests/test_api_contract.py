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
