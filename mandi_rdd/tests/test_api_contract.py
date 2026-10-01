"""API contract tests for the FastAPI serving layer.

The README documents 30+ endpoints; these tests keep the public surface
honest by asserting the documented routes exist and the schemas stay
backwards compatible. No database or network access is required.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

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
    ("/fdr", "GET"),
    ("/spec-curve/{commodity}", "GET"),
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


def test_recovery_is_only_claimed_by_builds_that_can_survive_it(app_module):
    """`safe_recovery` is the gate in front of an unattended rebuild of the
    prices table and a restore from R2. It used to be the literal `True` on
    every build, so on 2026-10-01 the workflow handed a non-atomic 2.3.0
    container a rebuild it could not survive (OOM, then `503 no healthy
    upstream` on every route). It has to come from the running build."""
    assert app_module._recovery_is_safe() is True  # master advertises 2.4.0
    floor = app_module.SAFE_RECOVERY_VERSION
    assert floor == (2, 4, 0)
    assert app_module._version_tuple("2.3.0") < floor
    assert app_module._version_tuple("2.4.0") >= floor
    assert app_module._version_tuple("2.10.0") >= floor
    assert app_module._version_tuple("2.3.9") < floor
    assert app_module._version_tuple("3.0.0") >= floor
    # An unreadable version must fail closed, not inherit the old optimism.
    assert app_module._version_tuple("") < floor
    assert app_module._version_tuple(None) < floor


def test_auto_heal_refuses_builds_that_cannot_survive_a_rebuild():
    """The workflow must check the deployed version, not just the flag: a flag
    already deployed on an old build cannot be corrected by editing the API."""
    workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/refresh-live-data.yml").read_text(encoding="utf-8")
    assert "safe_recovery" in workflow
    assert "if build < (2, 4, 0):" in workflow, "the version gate was removed"
    assert "/admin/rebuild-prices" in workflow
    assert "/admin/restore-from-r2" in workflow


def test_the_boot_path_does_not_race_the_health_check():
    """An empty warehouse used to start the full pipeline in its own thread
    from `lifespan`, i.e. before the readiness probe could pass. That is the
    heaviest work this service does, and on the 512 MB tier the resulting OOM
    is a crash loop - which the platform edge reports as `503 no healthy
    upstream` on every route. The repair belongs to the scheduled refresh and
    to the admin endpoints, both of which run while the container is serving,
    and `MANDIIQ_SELF_REFRESH=0` has to mean nothing runs in this container."""
    source = (
        Path(__file__).resolve().parents[2] / "mandi_rdd/api/main.py"
    ).read_text(encoding="utf-8")
    assert "_auto_pipeline" not in source, "the boot pipeline thread came back"
    boot = source.split("if should_trigger_pipeline:", 1)[1].split(
        "metrics_push.start_push_thread()", 1
    )[0]
    assert "_self_refresh_enabled()" in boot, "the boot path ignores the self-refresh switch"
    assert "_self_refresh_initial_delay_s()" in boot, "the boot path no longer defers"
    assert "run_ingestion" not in boot, "the boot path must not run the pipeline itself"


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


# --- price-index heal reporting --------------------------------------------


def _keep_refresh_state(app_module):
    """Snapshot the module-level refresh state so a test cannot leak into the next."""
    return dict(app_module._REFRESH_STATE)


def _restore_refresh_state(app_module, saved):
    app_module._REFRESH_STATE.clear()
    app_module._REFRESH_STATE.update(saved)


def test_health_reports_the_index_check_even_when_nothing_was_repaired(app_module):
    """`last_index_repair: null` was ambiguous: checked-and-clean looked exactly
    like never-checked, which is how a heal could happen without ever showing up
    on /health. Every check now leaves a record."""
    saved = _keep_refresh_state(app_module)
    try:
        app_module._note_index_health(
            {"duplicates": 0, "rebuilt": False, "trigger": None, "probed": True},
            source="startup_probe",
        )
        check = app_module._REFRESH_STATE["last_index_check"]
        assert check["source"] == "startup_probe"
        assert check["rebuilt"] is False
        assert check["checked_at"]
        assert app_module._REFRESH_STATE["last_index_repair"] is None, (
            "a clean check must never be reported as a repair"
        )
    finally:
        _restore_refresh_state(app_module, saved)


def test_a_repair_is_provable_on_the_health_payload(app_module):
    saved = _keep_refresh_state(app_module)
    try:
        app_module._note_index_health(
            {"rebuilt": True, "trigger": "write_probe", "rows_before": 12,
             "rows_after": 10, "rows_removed": 2},
            source="startup_rebuild",
        )
        repair = app_module._REFRESH_STATE["last_index_repair"]
        assert repair["trigger"] == "write_probe"
        assert repair["rows_removed"] == 2
        assert repair["source"] == "startup_rebuild"
        assert app_module._REFRESH_STATE["last_index_check"]["rebuilt"] is True
    finally:
        _restore_refresh_state(app_module, saved)


def test_the_index_record_survives_the_restart_it_caused(app_module, tmp_path):
    """A fatal index fault kills the process, taking the in-memory record with
    it. The record the scheduler persisted has to be readable afterwards."""
    record = {
        "last_run_utc": "2026-09-30T18:16:36Z",
        "outcome": "failure",
        "index_health": {
            "rebuilt": True,
            "trigger": "previous_run_index_fault",
            "rows_after": 10,
            "fault_flagged": False,
        },
    }
    path = tmp_path / "last_ingest_status.json"
    path.write_text(json.dumps(record), encoding="utf-8")

    health = app_module._persisted_index_health(path)
    assert health["rebuilt"] is True
    assert health["trigger"] == "previous_run_index_fault"

    path.write_text("{}", encoding="utf-8")
    assert app_module._persisted_index_health(path) is None, "no record means no claim"
    assert app_module._persisted_index_health(tmp_path / "missing.json") is None


def test_health_exposes_a_pending_fault_and_the_check(app_module, tmp_path, monkeypatch):
    """An unrepaired fault must be visible immediately, not only once someone
    notices that the numbers stopped moving."""
    from mandi_rdd.storage import duckdb_store

    monkeypatch.setattr(duckdb_store, "DB_PATH", tmp_path / "vol" / "mandi_iq.duckdb")
    fields = set(app_module.HealthResponse.model_fields)
    assert {"last_index_check", "index_fault_pending"} <= fields

    assert app_module._index_fault_pending() is False
    duckdb_store.note_index_fault(
        Exception("FATAL Error: Failed to delete all rows from index")
    )
    assert app_module._index_fault_pending() is True
    duckdb_store.clear_index_fault()
    assert app_module._index_fault_pending() is False


def test_a_fatal_probe_is_recorded_and_healed_in_the_same_tick(
    app_module, tmp_path, monkeypatch
):
    """The trace this fixes: the write probe dies with a FATAL index fault,
    which invalidates the connection it ran on, so the heal could not use that
    connection - it was deferred to a restart that never reported it. The API
    must now record the fault and rebuild on a fresh connection immediately."""
    from mandi_rdd.storage import duckdb_store

    monkeypatch.setattr(duckdb_store, "DB_PATH", tmp_path / "vol" / "mandi_iq.duckdb")
    monkeypatch.setattr(app_module, "_INDEX_VERIFIED", False)
    saved = _keep_refresh_state(app_module)
    fault = Exception(
        "FatalException: FATAL Error: Invalid Input Error: Failed to delete all "
        "rows from index. Only deleted 0 out of 2017 rows."
    )

    def _die(_conn, probe=True):
        raise fault

    monkeypatch.setattr(duckdb_store, "verify_price_index", _die)
    try:
        report = app_module._verify_price_index_once()
        assert report["rebuilt"] is True, "the heal must not be deferred to a restart"
        assert report["trigger"] == "recorded_index_fault"
        assert report["cause"].startswith("FatalException")
        assert app_module._REFRESH_STATE["last_index_repair"]["rebuilt"] is True
        assert app_module._REFRESH_STATE["last_index_check"]["source"] == "startup_rebuild"
        assert duckdb_store.index_fault_flagged() is False, (
            "the marker must be cleared once the rebuild succeeded"
        )
    finally:
        _restore_refresh_state(app_module, saved)
