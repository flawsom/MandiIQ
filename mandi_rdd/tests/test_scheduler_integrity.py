"""Scheduler and workflow integrity checks.

These verify the guarantees CONTRIBUTING.md makes about the ingestion path:
missing credentials fail loudly, writes are idempotent, and scheduled commits
never suppress CI.
"""

from __future__ import annotations

from pathlib import Path

import json

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"


def test_missing_data_gov_key_fails_loudly(monkeypatch):
    from mandi_rdd.ingestion import fetch_prices

    monkeypatch.delenv("DATA_GOV_IN_API_KEY", raising=False)
    monkeypatch.delenv("DATA_GOV_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DATA_GOV_IN_API_KEY"):
        fetch_prices._get_api_key()


def test_placeholder_key_rejected(monkeypatch):
    from mandi_rdd.ingestion import fetch_prices

    monkeypatch.setenv("DATA_GOV_IN_API_KEY", "changeme")
    with pytest.raises(RuntimeError, match="invalid"):
        fetch_prices._get_api_key()


def test_upsert_prices_is_idempotent():
    duckdb = pytest.importorskip("duckdb")
    from mandi_rdd.storage import duckdb_store

    conn = duckdb.connect(":memory:")
    duckdb_store.init_schema(conn)
    record = {
        "state": "TestState",
        "district": "TestDist",
        "market": "TestMkt",
        "commodity": "TestCommodity",
        "variety": "Other",
        "grade": "FAQ",
        "arrival_date": "2024-01-01",
        "min_price": 100.0,
        "max_price": 200.0,
        "modal_price": 150.0,
    }
    inserted_first = duckdb_store.upsert_prices(conn, [record])
    inserted_second = duckdb_store.upsert_prices(conn, [record])
    total = conn.execute(
        "SELECT count(*) FROM prices WHERE commodity = 'TestCommodity'"
    ).fetchone()[0]
    conn.close()

    assert inserted_first == 1
    assert inserted_second == 0
    assert total == 1


def test_ingestion_entrypoints_exist():
    from mandi_rdd.ingestion import scheduler

    assert callable(scheduler.run_ingestion)
    assert callable(scheduler.run_once)


def test_fetch_page_retries_transient_source_failures(monkeypatch):
    """data.gov.in times out for tens of seconds at a time; one hiccup must
    not kill a whole nightly run."""
    import urllib.error

    from mandi_rdd.ingestion import fetch_prices

    monkeypatch.setenv("DATA_GOV_IN_API_KEY", "0123456789abcdef0123")
    monkeypatch.setattr(fetch_prices.time, "sleep", lambda _seconds: None)
    calls = {"n": 0}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self):
            return b'{"records": [], "total": 0}'

    def _fake_urlopen(_req, timeout=None, context=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.URLError("timed out")
        return _FakeResponse()

    monkeypatch.setattr(fetch_prices.urllib.request, "urlopen", _fake_urlopen)

    data = fetch_prices.fetch_page(limit=10)

    assert data["total"] == 0
    assert calls["n"] == 3, "transient failures should be retried"


def test_fetch_page_fails_fast_on_rejected_requests(monkeypatch):
    """A bad key or bad request will not fix itself on retry."""
    import io
    import urllib.error

    from mandi_rdd.ingestion import fetch_prices

    monkeypatch.setenv("DATA_GOV_IN_API_KEY", "0123456789abcdef0123")
    monkeypatch.setattr(fetch_prices.time, "sleep", lambda _seconds: None)
    calls = {"n": 0}

    def _fake_urlopen(req, timeout=None, context=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b""))

    monkeypatch.setattr(fetch_prices.urllib.request, "urlopen", _fake_urlopen)

    with pytest.raises(urllib.error.HTTPError):
        fetch_prices.fetch_page(limit=10)

    assert calls["n"] == 1, "4xx responses other than 429 must not be retried"


def test_source_outage_degrades_the_run_instead_of_aborting_it():
    source = (REPO_ROOT / "mandi_rdd" / "ingestion" / "scheduler.py").read_text(
        encoding="utf-8"
    )
    assert 'summary["status"] = "degraded"' in source
    assert 'outcome = "degraded"' in source
    assert "continuing with existing data" in source


def test_price_pages_are_yielded_lazily(monkeypatch):
    """The pipeline must never hold the whole archive in memory.

    Buffering every page into one list is what puts a fetch-heavy ingest on a
    collision course with the container's memory limit, and the ingest runs
    inside the API process.
    """
    from mandi_rdd.ingestion import fetch_prices

    monkeypatch.setenv("DATA_GOV_IN_API_KEY", "0123456789abcdef0123")
    monkeypatch.setattr(fetch_prices.time, "sleep", lambda _seconds: None)
    pages = [
        {"records": [{"commodity": f"C{i}"} for i in range(3)], "total": 9},
        {"records": [{"commodity": f"D{i}"} for i in range(3)], "total": 9},
        {"records": [{"commodity": f"E{i}"} for i in range(3)], "total": 9},
    ]
    calls = {"n": 0}

    def _fake_fetch_page(offset=0, limit=1000, filters=None, format="json"):
        calls["n"] += 1
        return pages.pop(0)

    monkeypatch.setattr(fetch_prices, "fetch_page", _fake_fetch_page)

    iterator = fetch_prices.iter_price_pages(page_size=3)
    first = next(iterator)
    assert len(first) == 3
    assert calls["n"] == 1, "the second page must not be requested before it is needed"
    assert first[0]["_source"]["resource_id"] == fetch_prices.PRIMARY_RESOURCE_ID

    rest = [page for page in iterator]
    assert len(rest) == 2


def test_price_pages_respect_the_record_budget(monkeypatch):
    from mandi_rdd.ingestion import fetch_prices

    monkeypatch.setenv("DATA_GOV_IN_API_KEY", "0123456789abcdef0123")
    monkeypatch.setattr(fetch_prices.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        fetch_prices,
        "fetch_page",
        lambda offset=0, limit=1000, filters=None, format="json": {
            "records": [{"commodity": "Onion"}], "total": 10_000,
        },
    )
    pages = list(fetch_prices.iter_price_pages(page_size=1, max_records=3))
    assert sum(len(p) for p in pages) == 3


def test_price_pages_stop_at_the_time_budget(monkeypatch):
    """A slow source must not keep an ingest open forever."""
    from mandi_rdd.ingestion import fetch_prices

    monkeypatch.setenv("DATA_GOV_IN_API_KEY", "0123456789abcdef0123")
    monkeypatch.setattr(fetch_prices.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        fetch_prices,
        "fetch_page",
        lambda offset=0, limit=1000, filters=None, format="json": {
            "records": [{"commodity": "Onion"}], "total": 10_000,
        },
    )
    clock = {"t": 0.0}

    def _monotonic():
        clock["t"] += 100.0
        return clock["t"]

    monkeypatch.setattr(fetch_prices.time, "monotonic", _monotonic)
    pages = list(fetch_prices.iter_price_pages(page_size=1, max_run_seconds=150.0))
    assert len(pages) <= 3, f"time budget ignored: {len(pages)} pages"


def test_fetch_all_prices_still_honours_max_records(monkeypatch):
    """The list-returning wrapper is used by the CLI and must behave as before."""
    from mandi_rdd.ingestion import fetch_prices

    monkeypatch.setenv("DATA_GOV_IN_API_KEY", "0123456789abcdef0123")
    monkeypatch.setattr(fetch_prices.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        fetch_prices,
        "fetch_page",
        lambda offset=0, limit=1000, filters=None, format="json": {
            "records": [{"commodity": "Onion"}] * 4, "total": 100,
        },
    )
    records = fetch_prices.fetch_all_prices(page_size=4, max_records=8)
    assert len(records) == 8


def test_index_fault_marker_reads_the_previous_failure(tmp_path):
    """A fault the read-only duplicate check cannot see must still be repaired.

    The evidence is the last run's error text: probing with a write is how the
    process dies in the first place.
    """
    from mandi_rdd.ingestion import scheduler

    assert scheduler._last_run_had_index_fault(tmp_path / "missing.json") is False

    record = tmp_path / "last_ingest_status.json"
    record.write_text(json.dumps({
        "outcome": "failure",
        "status": "failed",
        "error": "FatalException: FATAL Error: Invalid Input Error: Failed to "
                 "delete all rows from index. Only deleted 0 out of 12 rows.",
    }), encoding="utf-8")
    assert scheduler._last_run_had_index_fault(record) is True

    record.write_text(json.dumps({
        "outcome": "degraded", "status": "degraded",
        "error": "price source unavailable: <urlopen error timed out>",
    }), encoding="utf-8")
    assert scheduler._last_run_had_index_fault(record) is False, (
        "a transient source outage must not trigger a table rebuild"
    )

    record.write_text("not json", encoding="utf-8")
    assert scheduler._last_run_had_index_fault(record) is False


def test_the_run_persists_what_the_index_check_found(tmp_path):
    """A fatal index fault kills the process that detected it, so the check's
    result has to be written to the status file /health reads - otherwise a
    heal that did happen is indistinguishable from one that never ran."""
    from mandi_rdd.ingestion import scheduler

    out = tmp_path / "last_ingest_status.json"
    scheduler._write_ingest_status(
        {
            "status": "ok",
            "steps": {
                "index_health": {
                    "rebuilt": True,
                    "trigger": "previous_run_index_fault",
                    "rows_before": 12,
                    "rows_after": 10,
                    "rows_removed": 2,
                    "fault_flagged": False,
                },
                "prices": {"fetched": 5, "new": 2},
            },
            "duration_seconds": 12.5,
        },
        status_path=out,
    )

    record = json.loads(out.read_text(encoding="utf-8"))
    assert record["outcome"] == "success"
    assert record["index_health"]["rebuilt"] is True
    assert record["index_health"]["trigger"] == "previous_run_index_fault"
    assert record["index_health"]["rows_removed"] == 2
    assert record["index_health"]["checked_at"]

    # A run with no index step must not invent one.
    scheduler._write_ingest_status({"status": "ok", "steps": {}}, status_path=out)
    assert json.loads(out.read_text(encoding="utf-8"))["index_health"] is None


def test_pipeline_repairs_a_fault_it_can_only_see_from_the_last_failure():
    source = (REPO_ROOT / "mandi_rdd" / "ingestion" / "scheduler.py").read_text(
        encoding="utf-8"
    )
    assert "_last_run_had_index_fault()" in source
    assert "previous_run_index_fault" in source
    # The repair has to happen before anything else writes to the table.
    assert source.index('step("index_health")') < source.index('step("date_integrity")')
    assert source.index('step("index_health")') < source.index('step("fetch_prices")')


def test_pipeline_upserts_each_page_instead_of_buffering(monkeypatch):
    source = (REPO_ROOT / "mandi_rdd" / "ingestion" / "scheduler.py").read_text(
        encoding="utf-8"
    )
    assert "for page in iter_price_pages(" in source
    assert "n_new += upsert_prices(conn, page)" in source
    assert "MANDIIQ_PRICE_FETCH_MAX_SECONDS" in source
    assert "price_records" not in source, "the buffered fetch path is still there"


def test_every_workflow_is_valid_yaml():
    paths = sorted(WORKFLOWS_DIR.glob("*.yml"))
    assert paths, "No workflow files found"
    for path in paths:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(doc, dict), f"{path.name} is not a YAML mapping"


def test_ingest_workflow_schedule_secret_and_commit_policy():
    path = WORKFLOWS_DIR / "nightly-ingest.yml"
    text = path.read_text(encoding="utf-8")
    doc = yaml.safe_load(text)

    # PyYAML resolves the unquoted "on:" key to the boolean True.
    triggers = doc.get("on") or doc.get(True) or {}
    crons = [entry.get("cron") for entry in triggers.get("schedule", [])]
    assert "30 5 * * *" in crons
    assert "secrets.DATA_GOV_IN_API_KEY" in text
    assert "chore: nightly ingestion update" in text


def test_no_workflow_suppresses_ci():
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        text = path.read_text(encoding="utf-8").lower()
        assert "[skip ci]" not in text, f"{path.name} suppresses CI"
