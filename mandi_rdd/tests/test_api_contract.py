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
