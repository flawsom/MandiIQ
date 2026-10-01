"""The rainfall contract: GET /rainfall serves the RDD pages' input series.

The dashboard's rainfall pages read this from a local DuckDB file. That file is
gitignored, so on the hosted dashboard they rendered an empty state no matter
how healthy the pipeline was. The endpoint is what lets those pages work there,
so its shape - a JSON-safe row per subdivision-month, inside the physically
possible departure band, filterable by subdivision - is pinned here.

Each test builds a real DuckDB warehouse in a temp directory and calls the
endpoint the way the dashboard does.
"""

from __future__ import annotations

import asyncio

import pytest

from mandi_rdd.api import main
from mandi_rdd.storage import duckdb_store


def _row(sub_division: str, year: int, month: int, departure, rainfall=100.0, normal=110.0) -> dict:
    return {
        "sub_division": sub_division,
        "year": year,
        "month": month,
        "rainfall_mm": rainfall,
        "normal_mm": normal,
        "departure_pct": departure,
    }


def _build(tmp_path, monkeypatch, rows: list[dict]):
    db = tmp_path / "rainfall.duckdb"
    monkeypatch.setattr(duckdb_store, "DB_PATH", db)
    conn = duckdb_store.get_connection(db_path=db, read_only=False)
    duckdb_store.init_schema(conn)
    if rows:
        duckdb_store.upsert_rainfall(conn, rows)
    conn.close()
    return db


def _rainfall(**kwargs):
    # FastAPI resolves Query() defaults on the way in; a direct call has to pass
    # the value the HTTP layer would have supplied.
    kwargs.setdefault("limit", 5000)
    return asyncio.run(main.rainfall(**kwargs))


def test_the_endpoint_serves_the_series_the_pages_query(tmp_path, monkeypatch):
    _build(tmp_path, monkeypatch, [
        _row("Madhya Maharashtra", 2024, 6, -24.5),
        _row("Vidarbha", 2024, 7, 12.0),
    ])

    rows = _rainfall()

    assert len(rows) == 2
    assert {r["sub_division"] for r in rows} == {"Madhya Maharashtra", "Vidarbha"}
    assert set(rows[0]) == {
        "sub_division", "year", "month", "rainfall_mm", "normal_mm", "departure_pct",
    }
    # Ordered by year, month - the pages plot it as a series.
    assert [r["month"] for r in rows] == [6, 7]


def test_an_impossible_departure_is_not_published(tmp_path, monkeypatch):
    """The pages filtered -100..200 themselves; the endpoint must too."""
    _build(tmp_path, monkeypatch, [
        _row("Vidarbha", 2024, 7, -24.5),
        _row("Vidarbha", 2024, 8, -400.0),
        _row("Vidarbha", 2024, 9, 900.0),
    ])

    months = [r["month"] for r in _rainfall()]
    assert months == [7]


def test_an_unmeasured_normal_comes_back_as_null_not_nan(tmp_path, monkeypatch):
    """NaN is not JSON; a missing normal has to be null on the wire."""
    import json

    _build(tmp_path, monkeypatch, [_row("Vidarbha", 2024, 7, -24.5, normal=float("nan"))])

    rows = _rainfall()
    assert rows[0]["normal_mm"] is None
    json.dumps(rows)  # would raise on NaN with the strict encoder


def test_the_subdivision_filter_is_case_insensitive(tmp_path, monkeypatch):
    _build(tmp_path, monkeypatch, [
        _row("Vidarbha", 2024, 7, -24.5),
        _row("Marathwada", 2024, 7, -30.0),
    ])

    rows = _rainfall(sub_division="vidarbha")
    assert [r["sub_division"] for r in rows] == ["Vidarbha"]


def test_an_empty_warehouse_returns_an_empty_list(tmp_path, monkeypatch):
    _build(tmp_path, monkeypatch, [])
    assert _rainfall() == []
