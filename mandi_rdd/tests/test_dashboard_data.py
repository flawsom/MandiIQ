"""Dashboard data-access tests.

The cockpit's pages were reading the DuckDB warehouse directly. Streamlit
Community Cloud serves the repository from an immutable layer and the DuckDB
file is gitignored, so those queries return nothing there even though the API
holds the rows - which is how the landing page drew no price trend and the
Forecast page announced "no price records" for data that plainly exists.

These tests pin the API-first accessors that replace those direct reads, without
touching the network.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pandas", reason="pandas is required for dashboard data tests")

from mandi_rdd.dashboard import data_access  # noqa: E402


def test_prices_arrive_as_a_usable_frame(monkeypatch):
    """API rows must come back as a DataFrame with real dates and numbers."""
    rows = [
        {
            "state": "Keralam",
            "district": "Idukki",
            "market": "Munnar Market",
            "commodity": "Onion",
            "variety": "Small",
            "arrival_date": "2026-09-25",
            "modal_price": "8500.0",
            "min_price": 8400,
            "max_price": None,
        }
    ]
    monkeypatch.setattr(data_access, "get_prices", lambda **kwargs: rows)

    df = data_access.get_prices_frame(commodity="Onion")

    assert list(df["arrival_date"].dt.strftime("%Y-%m-%d")) == ["2026-09-25"]
    assert float(df["modal_price"].iloc[0]) == 8500.0
    assert df["district"].iloc[0] == "Idukki"


def test_a_silent_api_yields_an_empty_frame_not_a_crash(monkeypatch):
    monkeypatch.setattr(data_access, "get_prices", lambda **kwargs: [])
    df = data_access.get_prices_frame(commodity="Onion")
    assert df.empty
    assert "arrival_date" not in df.columns or len(df) == 0


def test_commodities_come_from_the_api_first(monkeypatch):
    """A picker must not depend on the local warehouse being populated."""
    monkeypatch.setattr(
        data_access,
        "get_freshness",
        lambda: [{"commodity": "onion"}, {"commodity": "Wheat"}, {"commodity": "onion"}],
    )
    assert data_access.get_commodities() == ["Onion", "Wheat"]


def test_commodities_fall_back_to_the_local_table(monkeypatch):
    monkeypatch.setattr(data_access, "get_freshness", lambda: [])
    import mandi_rdd.storage.duckdb_store as store

    monkeypatch.setattr(store, "get_curated_commodities", lambda: ["onion", "wheat"])
    assert data_access.get_commodities() == ["Onion", "Wheat"]
