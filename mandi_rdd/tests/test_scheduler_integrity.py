"""Scheduler and workflow integrity checks.

These verify the guarantees CONTRIBUTING.md makes about the ingestion path:
missing credentials fail loudly, writes are idempotent, and scheduled commits
never suppress CI.
"""

from __future__ import annotations

from pathlib import Path

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
