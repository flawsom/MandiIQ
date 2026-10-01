"""Contract tests for refresh scope and restart-surviving refresh bookkeeping.

The in-container scheduler runs on the same 512 MB box that serves the API. On
2026-10-01 the primary instance restarted in a loop while its own tick ran the
analysis half of the pipeline, and the platform edge answered
`503 no healthy upstream` between restarts. Two things have to hold so that
cannot repeat silently:

* a run declares a scope, and a *light* run never starts the work that does not
  fit - the analysis recompute, the satellite fetch, the index rebuild;
* a run that is killed mid-flight leaves evidence on the volume, and the next
  boot waits longer instead of repeating the same fatal run.

No database and no network are required: the storage layer is monkeypatched.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from mandi_rdd.ingestion import scheduler

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def app_module():
    from mandi_rdd.api import main

    return main


# ── scope resolution ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "value, expected",
    [
        (None, scheduler.SCOPE_LIGHT),
        ("", scheduler.SCOPE_LIGHT),
        ("light", scheduler.SCOPE_LIGHT),
        ("FULL", scheduler.SCOPE_FULL),
        ("all", scheduler.SCOPE_FULL),
        ("heavy", scheduler.SCOPE_FULL),
    ],
)
def test_a_scope_is_read_from_the_caller_or_the_environment(monkeypatch, value, expected):
    monkeypatch.delenv("MANDIIQ_REFRESH_SCOPE", raising=False)
    assert scheduler.resolve_scope(value) == expected


def test_an_unset_environment_means_light(monkeypatch):
    monkeypatch.delenv("MANDIIQ_REFRESH_SCOPE", raising=False)
    assert scheduler.resolve_scope() == scheduler.SCOPE_LIGHT


def test_an_unrecognised_scope_falls_back_to_light(monkeypatch):
    """Guessing 'full' here would hand a free-tier box the work that kills it."""
    monkeypatch.setenv("MANDIIQ_REFRESH_SCOPE", "yolo")
    assert scheduler.resolve_scope() == scheduler.SCOPE_LIGHT


# ── a light run defers the index rebuild ────────────────────────────────────

def test_a_light_run_reports_a_pending_index_fault_instead_of_rebuilding(monkeypatch):
    from mandi_rdd.storage import duckdb_store

    calls = {"heal": 0}

    def _heal(conn):
        calls["heal"] += 1
        return {"duplicates": 0, "rebuilt": True}

    monkeypatch.setattr(duckdb_store, "index_fault_flagged", lambda: True)
    monkeypatch.setattr(duckdb_store, "find_duplicate_price_keys", lambda conn: 0)
    monkeypatch.setattr(duckdb_store, "heal_price_index", _heal)

    report = scheduler._check_price_index(None, light=True)

    assert calls["heal"] == 0, "a light run must not start the heaviest write it has"
    assert report["rebuilt"] is False
    assert report["deferred"] == "index_repair"
    assert "rebuild-prices" in report["hint"]


def test_a_full_run_is_still_allowed_to_repair_the_index(monkeypatch):
    from mandi_rdd.storage import duckdb_store

    calls = {"heal": 0}

    def _heal(conn):
        calls["heal"] += 1
        return {"duplicates": 0, "rebuilt": False, "trigger": None}

    monkeypatch.setattr(duckdb_store, "index_fault_flagged", lambda: True)
    monkeypatch.setattr(duckdb_store, "heal_price_index", _heal)
    monkeypatch.setattr(scheduler, "_last_run_had_index_fault", lambda *a, **k: False)

    report = scheduler._check_price_index(None, light=False)

    assert calls["heal"] == 1
    assert report == {"duplicates": 0, "rebuilt": False, "trigger": None}


def test_a_clean_index_is_not_rebuilt(monkeypatch):
    """No fault flag and no fault on record means the check is read-only."""
    from mandi_rdd.storage import duckdb_store

    monkeypatch.setattr(duckdb_store, "index_fault_flagged", lambda: False)
    monkeypatch.setattr(
        duckdb_store,
        "heal_price_index",
        lambda conn: {"duplicates": 0, "rebuilt": False, "trigger": None},
    )
    monkeypatch.setattr(
        duckdb_store,
        "rebuild_prices_table",
        lambda conn: pytest.fail("a clean index must not be rebuilt"),
    )
    # A full run does consult the previous run's record - that is how a marker
    # lost with a restarted process is still caught - but here it says no.
    monkeypatch.setattr(scheduler, "_last_run_had_index_fault", lambda *a, **k: False)

    report = scheduler._check_price_index(None, light=False)
    assert report["rebuilt"] is False


def test_a_light_run_ignores_a_previous_runs_fault_record(monkeypatch):
    """The last line of defence: light scope never starts a rebuild."""
    from mandi_rdd.storage import duckdb_store

    monkeypatch.setattr(duckdb_store, "index_fault_flagged", lambda: False)
    monkeypatch.setattr(
        duckdb_store,
        "heal_price_index",
        lambda conn: {"duplicates": 0, "rebuilt": False, "trigger": None},
    )
    monkeypatch.setattr(
        duckdb_store,
        "rebuild_prices_table",
        lambda conn: pytest.fail("a light run must not rebuild the prices table"),
    )
    monkeypatch.setattr(scheduler, "_last_run_had_index_fault", lambda *a, **k: True)

    report = scheduler._check_price_index(None, light=True)
    assert report["rebuilt"] is False


# ── the pipeline half a light run leaves out ────────────────────────────────

def test_the_light_pipeline_guards_the_work_that_does_not_fit():
    """Scope is only worth declaring if the expensive steps actually read it.

    The pipeline is one long function, so this pins the guards where they are
    rather than restating them: the analysis loops must iterate the scoped list,
    the satellite fetch must be behind the light check, and a light run must
    record what it left out instead of reporting an empty analysis as a clean
    one.
    """
    source = (REPO_ROOT / "mandi_rdd/ingestion/scheduler.py").read_text(encoding="utf-8")

    assert "analysis_targets = [] if light else target_commodities" in source
    assert source.count("for commodity in analysis_targets:") == 2, (
        "both the RDD loop and the narrative loop must run over the scoped list"
    )
    assert "for commodity in target_commodities:" not in source
    assert 'summary["steps"]["analysis"] = {' in source
    assert '"reason": "light scope"' in source or "'reason': 'light scope'" in source
    assert "if light:" in source and "Light run: skipping the satellite NDVI fetch" in source


def test_a_light_run_names_every_step_it_skipped():
    """`steps_skipped` is what stops a light run being read as a full one."""
    for step in ("fetch_ndvi", "rdd_analysis", "forecast_training", "nightly_narratives"):
        assert step in scheduler.LIGHT_SKIPPED_STEPS


# ── evidence of a run that did not survive ──────────────────────────────────

def test_a_run_that_is_killed_leaves_a_marker_the_next_boot_can_read(tmp_path):
    state = tmp_path / "refresh_state.json"

    scheduler.begin_refresh("light", step="index_health", path=state)
    assert state.exists()

    claim = scheduler.claim_refresh_state(state)
    assert claim["inflight"] is True
    assert claim["unclean_runs"] == 1
    assert claim["last_unclean"]["step"] == "index_health"
    assert claim["last_unclean"]["scope"] == "light"


def test_a_clean_boot_is_not_counted_as_an_unclean_run(tmp_path):
    state = tmp_path / "refresh_state.json"

    scheduler.begin_refresh("light", step="fetch_prices", path=state)
    scheduler.end_refresh(state)

    first = scheduler.claim_refresh_state(state)
    assert first["unclean_runs"] == 0 and first["inflight"] is False
    # ... and the boot after that must not re-count the same cleared marker.
    second = scheduler.claim_refresh_state(state)
    assert second["unclean_runs"] == 0


def test_an_unclean_run_is_counted_once_per_death(tmp_path):
    state = tmp_path / "refresh_state.json"

    for step in ("fetch_prices", "rdd_analysis"):
        scheduler.begin_refresh("full", step=step, path=state)
        scheduler.claim_refresh_state(state)

    claim = scheduler.claim_refresh_state(state)
    assert claim["unclean_runs"] == 2
    assert claim["last_unclean"]["step"] == "rdd_analysis"


def test_a_marker_written_by_a_running_process_names_its_step(tmp_path):
    state = tmp_path / "refresh_state.json"

    scheduler.begin_refresh("light", step="starting", path=state)
    scheduler.note_refresh_step("fetch_prices", path=state)

    claim = scheduler.claim_refresh_state(state)
    assert claim["last_unclean"]["step"] == "fetch_prices"


def test_the_boot_after_a_death_waits_longer(app_module, monkeypatch):
    """Backoff is the difference between a retry and a restart loop."""
    monkeypatch.setenv("MANDIIQ_REFRESH_INITIAL_DELAY_S", "90")
    monkeypatch.delenv("MANDIIQ_REFRESH_BACKOFF_MAX_S", raising=False)

    assert app_module._self_refresh_backoff_s(0) == 90
    assert app_module._self_refresh_backoff_s(1) == 180
    assert app_module._self_refresh_backoff_s(2) == 360
    assert app_module._self_refresh_backoff_s(50) == 1800, "the wait is bounded"

    monkeypatch.setenv("MANDIIQ_REFRESH_BACKOFF_MAX_S", "300")
    assert app_module._self_refresh_backoff_s(50) == 300


# ── /health answers while the pipeline writes ───────────────────────────────

SNAPSHOT = {
    "n_prices": 1994318, "n_commodities": 423, "n_states": 36, "n_districts": 667,
    "n_rainfall": 2314, "n_rainfall_filtered": 2228, "rainfall_below_threshold": 1077,
    "n_rdd_results": 33, "n_ndvi": 3663, "n_ndvi_districts": 605,
}


def test_health_serves_its_snapshot_while_a_run_is_in_flight(app_module, monkeypatch):
    """A probe that queues behind the work it checks reports the work, not the API."""
    monkeypatch.setattr(app_module, "_COUNT_CACHE", {"at": 0.0, "data": dict(SNAPSHOT)})
    monkeypatch.setattr(app_module, "_ingestion_running", lambda: True)
    monkeypatch.setattr(
        app_module, "_count_warehouse",
        lambda conn: pytest.fail("a probe during a run must not query the warehouse"),
    )

    counts, age = app_module._warehouse_counts(None)

    assert counts["n_prices"] == SNAPSHOT["n_prices"]
    assert age is not None and age > 0, "the caller is told how old the snapshot is"


def test_health_refreshes_its_snapshot_once_the_run_is_over(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "_COUNT_CACHE", {"at": 0.0, "data": dict(SNAPSHOT)})
    monkeypatch.setattr(app_module, "_ingestion_running", lambda: False)
    monkeypatch.setattr(app_module, "_count_warehouse",
                        lambda conn: {**SNAPSHOT, "n_prices": 2000000})

    counts, age = app_module._warehouse_counts(None)

    assert counts["n_prices"] == 2000000
    assert age == 0.0


def test_a_snapshot_for_a_different_warehouse_is_not_reused(app_module, monkeypatch):
    """A restore or a rebuild swaps the warehouse; the probe must follow it."""
    monkeypatch.setattr(
        app_module, "_COUNT_CACHE",
        {"at": time.time(), "data": dict(SNAPSHOT), "key": ("/gone/mandi_iq.duckdb", 1, 1)},
    )
    monkeypatch.setattr(app_module, "_ingestion_running", lambda: False)
    monkeypatch.setattr(app_module, "_count_warehouse",
                        lambda conn: {**SNAPSHOT, "n_prices": 0})

    counts, age = app_module._warehouse_counts(None)

    assert counts["n_prices"] == 0, "an empty warehouse must not be reported from an old snapshot"
    assert age == 0.0


def test_health_does_not_rescan_dates_while_a_run_is_in_flight(app_module, monkeypatch):
    """The date scan is the probe's other multi-row read, and it waits too."""
    quality = {"max_date": "2026-09-25", "days_behind": 6, "n_future_dates": 0}
    monkeypatch.setattr(app_module, "_QUALITY_CACHE", {"at": time.time(), "data": quality})
    monkeypatch.setattr(app_module, "_ingestion_running", lambda: True)
    monkeypatch.setattr(
        "mandi_rdd.core.dates.date_quality",
        lambda conn: pytest.fail("a probe during a run must not rescan the warehouse"),
    )

    # ttl 0 means "expired", so only the in-flight rule can be what saves it.
    assert app_module._cached_date_quality(None, ttl_seconds=0) == quality


def test_the_date_scan_still_refreshes_when_nothing_is_running(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "_QUALITY_CACHE", {"at": 0.0, "data": {"days_behind": 99}})
    monkeypatch.setattr(app_module, "_ingestion_running", lambda: False)
    monkeypatch.setattr("mandi_rdd.core.dates.date_quality",
                        lambda conn: {"max_date": "2026-09-25", "days_behind": 6})

    assert app_module._cached_date_quality(None, ttl_seconds=0)["days_behind"] == 6


def test_the_deployed_build_can_be_told_apart(app_module):
    """`version` is how an operator knows which behaviour is actually running.

    The scope/backoff/snapshot work landed in 2.4.1. A build that reports an
    older version cannot have it, however green the repository is - which is
    the mistake that made a 2.3.0 container advertise safe_recovery.
    """
    assert app_module._version_tuple(app_module.app.version) >= (2, 4, 1)
    assert app_module.HealthResponse.model_fields["version"].default == app_module.app.version


def test_health_reports_the_scope_it_ran_under(app_module):
    """The new fields exist and their defaults do not overstate what happened."""
    fields = app_module.HealthResponse.model_fields

    assert fields["refresh_scope"].default == "light"
    assert fields["refresh_skipped_steps"].default == []
    assert fields["unclean_refresh_runs"].default == 0
    assert fields["counts_age_s"].default is None
    assert fields["first_refresh_delay_s"].default is None


def test_an_automated_rebuild_is_off_until_an_operator_arms_it(app_module, monkeypatch):
    """The rebuild is the heaviest write there is; nobody gets it by default."""
    monkeypatch.delenv("MANDIIQ_ALLOW_AUTO_REBUILD", raising=False)
    assert app_module._auto_rebuild_allowed() is False
    assert app_module.HealthResponse.model_fields["auto_rebuild_allowed"].default is False

    monkeypatch.setenv("MANDIIQ_ALLOW_AUTO_REBUILD", "1")
    assert app_module._auto_rebuild_allowed() is True


def test_the_automated_recovery_waits_for_that_flag():
    """Version says the build can; the flag says the instance can afford it."""
    source = (REPO_ROOT / ".github/workflows/refresh-live-data.yml").read_text(encoding="utf-8")

    assert "not health.get(\"auto_rebuild_allowed\")" in source
    assert source.index('not health.get("auto_rebuild_allowed")') < source.index(
        'post("/admin/rebuild-prices")'
    )


def test_the_ingest_status_records_what_the_run_left_out(tmp_path):
    """A light run has to be legible as one from the file /health reads."""
    status = tmp_path / "last_ingest_status.json"

    scheduler._write_ingest_status(
        {
            "status": "degraded",
            "scope": "light",
            "steps_skipped": ["rdd_analysis", "nightly_narratives"],
            "duration_seconds": 12.5,
            "steps": {"date_integrity": {"max_date": "2026-09-25", "days_behind": 6}},
        },
        status_path=status,
    )

    record = json.loads(status.read_text(encoding="utf-8"))
    assert record["scope"] == "light"
    assert record["steps_skipped"] == ["rdd_analysis", "nightly_narratives"]
    assert record["outcome"] == "degraded"


def test_the_refresh_endpoint_can_ask_for_a_full_run():
    """External callers with the memory for it must still be able to run it all."""
    source = (REPO_ROOT / "mandi_rdd/api/main.py").read_text(encoding="utf-8")

    assert re.search(r'async def refresh\(commodity: Optional\[str\] = None, scope: Optional\[str\] = None\)', source)
    assert "run_ingestion(\n                filters=filters if commodity_filter else None, scope=run_scope\n            )" in source
