"""The CEDA Agmarknet mirror: the reachable path for daily prices.

api.data.gov.in is unreachable from every cloud network this project runs on
(GitHub Actions, Daytona, Northflank, and the public CORS relays), so the
mirror is not an optional nicety - it is the only way the warehouse advances
while the documented host is blocked. These tests pin the contract that makes
it safe to run unattended:

  * inert without a token (no crash, no accidental unauthenticated hammering),
  * rows normalised into exactly the `prices` shape upsert_prices expects,
  * a bounded, resumable walk so a refresh tick cannot run for hours,
  * a probe that says which of the two failure modes is happening.
"""

from __future__ import annotations

import pytest

from mandi_rdd.ingestion import fetch_ceda


@pytest.fixture(autouse=True)
def _no_token(monkeypatch):
    """Every test starts with no CEDA token, like production today."""
    monkeypatch.delenv("MANDIIQ_CEDA_API_KEY", raising=False)
    monkeypatch.delenv("CEDA_API_KEY", raising=False)
    monkeypatch.setattr(fetch_ceda, "_geography_cache", None)
    monkeypatch.setattr(fetch_ceda, "_commodity_cache", None)
    yield


def test_disabled_without_a_token(monkeypatch):
    assert fetch_ceda.ceda_available() is False
    assert list(fetch_ceda.iter_ceda_pages()) == []
    probe = fetch_ceda.probe(timeout=0.1)
    assert probe["configured"] is False
    assert probe["reachable"] is False
    assert "MANDIIQ_CEDA_API_KEY" in probe["hint"]


def test_any_token_spelling_arms_it(monkeypatch):
    monkeypatch.setenv("CEDA_API_KEY", "token-from-the-catalogue")
    assert fetch_ceda.ceda_available() is True


def _stub_request(monkeypatch, payloads: dict, calls: list):
    def _fake(path, body=None, timeout=45.0):
        calls.append((path, body))
        for key, value in payloads.items():
            if path.startswith(key):
                return value
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(fetch_ceda, "_request", _fake)


def test_rows_are_normalised_into_the_prices_shape(monkeypatch):
    monkeypatch.setenv("MANDIIQ_CEDA_API_KEY", "token")
    _stub_request(
        monkeypatch,
        {
            "/agmarknet/commodities": {
                "data": [{"commodity_id": 23, "commodity_disp_name": "Onion"}]
            },
            "/agmarknet/geographies": {
                "data": [
                    {"state_id": 29, "state_name": "Karnataka"},
                    {"state_id": 29, "district_id": 555, "district_name": "Belgaum"},
                ]
            },
            "/agmarknet/prices": {
                "data": [
                    {
                        "date": "2026-09-30",
                        "commodity_id": 23,
                        "census_state_id": 29,
                        "census_district_id": 555,
                        "min_price": 1200,
                        "max_price": 2400,
                        "modal_price": 1800,
                    }
                ]
            },
        },
        [],
    )

    pages = list(fetch_ceda.iter_ceda_pages(lookback_days=3, max_calls=1))
    assert len(pages) == 1
    rows = pages[0]
    assert len(rows) == 1
    row = rows[0]
    assert row["arrival_date"] == "2026-09-30"
    assert row["state"] == "Karnataka"
    assert row["district"] == "Belgaum"
    assert row["market"] == "Belgaum"
    assert row["commodity"] == "Onion"
    assert row["modal_price"] == 1800
    # The label is what keeps these district-level rows from colliding with the
    # variety-level rows the data.gov.in feed writes for a day it does cover.
    assert row["variety"] == row["grade"] == fetch_ceda.SOURCE_LABEL
    # Lineage: every surface that shows a price must be able to name its source.
    assert row["_source"]["resource_id"] == "ceda:agmarknet"
    assert "CEDA" in row["_source"]["source_name"]


def test_the_walk_is_bounded_and_resumable(monkeypatch):
    """A refresh tick must not try to sweep the whole country in one call."""
    monkeypatch.setenv("MANDIIQ_CEDA_API_KEY", "token")
    calls: list = []
    _stub_request(
        monkeypatch,
        {
            "/agmarknet/commodities": {
                "data": [
                    {"commodity_id": 1, "commodity_disp_name": "Wheat"},
                    {"commodity_id": 2, "commodity_disp_name": "Paddy(Dhan)(Common)"},
                ]
            },
            "/agmarknet/geographies": {
                "data": [
                    {"state_id": 1, "state_name": "Jammu & Kashmir"},
                    {"state_id": 1, "district_id": 10, "district_name": "Kupwara"},
                    {"state_id": 2, "state_name": "Himachal Pradesh"},
                    {"state_id": 2, "district_id": 20, "district_name": "Chamba"},
                ]
            },
            "/agmarknet/prices": {"data": []},
        },
        calls,
    )

    cursor: dict = {}
    list(fetch_ceda.iter_ceda_pages(max_calls=2, start_index=0, cursor_out=cursor))
    price_calls = [c for c in calls if c[0] == "/agmarknet/prices"]
    assert len(price_calls) == 2, "the call budget was ignored"
    assert cursor["completed_pass"] is False
    assert cursor["offset"] == 2  # 4 (commodity, state) pairs; stopped after 2
    assert cursor["total"] == 4

    # The next pass continues from the cursor instead of starting over.
    calls.clear()
    cursor2: dict = {}
    list(
        fetch_ceda.iter_ceda_pages(
            max_calls=2, start_index=cursor["offset"], cursor_out=cursor2
        )
    )
    assert cursor2["completed_pass"] is True
    assert cursor2["offset"] == 0
    assert len([c for c in calls if c[0] == "/agmarknet/prices"]) == 2


def test_districts_are_requested_per_state_in_one_call(monkeypatch):
    """District ids arrive as a list: one call per (commodity, state)."""
    monkeypatch.setenv("MANDIIQ_CEDA_API_KEY", "token")
    calls: list = []
    _stub_request(
        monkeypatch,
        {
            "/agmarknet/commodities": {
                "data": [{"commodity_id": 23, "commodity_disp_name": "Onion"}]
            },
            "/agmarknet/geographies": {
                "data": [
                    {"state_id": 29, "state_name": "Karnataka"},
                    {"state_id": 29, "district_id": 555, "district_name": "Belgaum"},
                    {"state_id": 29, "district_id": 556, "district_name": "Bagalkot"},
                ]
            },
            "/agmarknet/prices": {"data": []},
        },
        calls,
    )
    list(fetch_ceda.iter_ceda_pages(max_calls=1))
    body = [c[1] for c in calls if c[0] == "/agmarknet/prices"][0]
    assert sorted(body["district_id"]) == [555, 556]
    assert body["state_id"] == 29
    assert body["from_date"] < body["to_date"]


def test_one_bad_cell_does_not_kill_the_sweep(monkeypatch):
    """A single failing (commodity, state) cell must not end the run."""
    monkeypatch.setenv("MANDIIQ_CEDA_API_KEY", "token")
    calls: list = []

    def _fake(path, body=None, timeout=45.0):
        calls.append(path)
        if path.startswith("/agmarknet/commodities"):
            return {"data": [{"commodity_id": 23, "commodity_disp_name": "Onion"}]}
        if path.startswith("/agmarknet/geographies"):
            return {
                "data": [
                    {"state_id": 29, "state_name": "Karnataka"},
                    {"state_id": 29, "district_id": 555, "district_name": "Belgaum"},
                    {"state_id": 30, "state_name": "Kerala"},
                    {"state_id": 30, "district_id": 600, "district_name": "Wayanad"},
                ]
            }
        if body and body["state_id"] == 29:
            raise OSError("upstream hiccup")
        return {
            "data": [
                {"date": "2026-09-30", "census_district_id": 600, "modal_price": 10}
            ]
        }

    monkeypatch.setattr(fetch_ceda, "_request", _fake)
    pages = list(fetch_ceda.iter_ceda_pages(max_calls=2))
    # The failing cell is skipped and the healthy one still yields its rows.
    assert [row["district"] for page in pages for row in page] == ["Wayanad"]


def test_the_catalogue_is_loaded_once_per_process(monkeypatch):
    """Ingest ticks repeat every hour; the id maps must not be re-fetched."""
    monkeypatch.setenv("MANDIIQ_CEDA_API_KEY", "token")
    calls: list = []
    _stub_request(
        monkeypatch,
        {
            "/agmarknet/commodities": {
                "data": [{"commodity_id": 23, "commodity_disp_name": "Onion"}]
            },
            "/agmarknet/geographies": {
                "data": [
                    {"state_id": 29, "state_name": "Karnataka"},
                    {"state_id": 29, "district_id": 555, "district_name": "Belgaum"},
                ]
            },
            "/agmarknet/prices": {"data": []},
        },
        calls,
    )
    fetch_ceda._load_commodities()
    fetch_ceda._load_commodities()
    fetch_ceda._load_geographies()
    fetch_ceda._load_geographies()
    assert len([c for c in calls if c[0] == "/agmarknet/commodities"]) == 1
    assert len([c for c in calls if c[0] == "/agmarknet/geographies"]) == 1


def test_probe_reports_a_configured_but_unreachable_mirror(monkeypatch):
    monkeypatch.setenv("MANDIIQ_CEDA_API_KEY", "token")

    def _boom(path, body=None, timeout=20.0):
        raise OSError("Connection refused")

    monkeypatch.setattr(fetch_ceda, "_request", _boom)
    probe = fetch_ceda.probe(timeout=0.1)
    assert probe["configured"] is True
    assert probe["reachable"] is False
    assert "Connection refused" in probe["error"]
