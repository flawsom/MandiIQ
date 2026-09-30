"""End-to-end adapter tests for the analytics engine.

Builds a synthetic in-memory DuckDB with four years of daily mandi prices and
monthly rainfall, then exercises every analytics section exactly as the API
and dashboard do. No external data files or network access required.
"""

from __future__ import annotations

import duckdb
import numpy as np
import pytest

DISTRICTS = ["Nashik", "Pune", "Solapur", "Nagpur", "Aurangabad", "Kolhapur", "Amravati"]
SUBDIVISIONS = ["Nashik", "Pune", "Solapur", "Nagpur"]
STATE = "Maharashtra"


@pytest.fixture()
def analytics_conn(monkeypatch):
    from mandi_rdd.storage import duckdb_store

    conn = duckdb.connect(":memory:")
    duckdb_store.init_schema(conn)
    rng = np.random.default_rng(21)

    price_rows = []
    for month_index in range(48):
        year = 2024 + month_index // 12
        month = month_index % 12 + 1
        for district_index, district in enumerate(DISTRICTS):
            base = 1500 + 50 * district_index
            for day in range(1, 13):
                price = float(base + 120 * np.sin(2 * np.pi * month / 12) + rng.normal(0, 30))
                price_rows.append((
                    STATE, district, f"{district} APMC", "Onion", "Other", "FAQ",
                    f"{year}-{month:02d}-{day:02d}", price * 0.9, price * 1.1, price,
                ))
    conn.executemany(
        """
        INSERT INTO prices (state, district, market, commodity, variety, grade,
                            arrival_date, min_price, max_price, modal_price)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        price_rows,
    )

    rainfall_rows = []
    for month_index in range(48):
        year = 2024 + month_index // 12
        month = month_index % 12 + 1
        for subdivision in SUBDIVISIONS:
            rainfall_rows.append((
                subdivision, year, month, 100.0, 110.0, float(rng.normal(0, 18)),
            ))
    conn.executemany(
        """
        INSERT INTO rainfall (sub_division, year, month, rainfall_mm, normal_mm, departure_pct)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rainfall_rows,
    )
    conn.commit()

    mapping = {(STATE, d): SUBDIVISIONS[i % len(SUBDIVISIONS)] for i, d in enumerate(DISTRICTS)}
    monkeypatch.setattr(
        "mandi_rdd.ingestion.fetch_rainfall.load_district_subdivision_map",
        lambda: mapping,
    )
    yield conn
    conn.close()


def test_composite_report_builds_every_section(analytics_conn):
    from mandi_rdd.analysis.analytics import commodity_analytics

    report = commodity_analytics(analytics_conn, "Onion")

    assert report["commodity"] == "Onion"
    assert report["headline"]
    sections = report["sections"]
    for name in ("conformal", "drift", "tail_risk", "dml", "nowcast"):
        assert name in sections
    assert "error" not in sections["drift"]
    assert "error" not in sections["tail_risk"]
    assert "error" not in sections["nowcast"]
    assert "error" not in sections["dml"]


def test_drift_report_computes_psi_ks_and_quality(analytics_conn):
    from mandi_rdd.analysis.drift import drift_report

    result = drift_report(analytics_conn, "Onion")

    assert 0.0 <= result["data_quality"]["score"] <= 100.0
    assert result["psi"]["verdict"] in ("stable", "moderate_shift", "major_shift")
    assert "statistic" in result["ks"]
    assert "n_alarms" in result["page_hinkley"]


def test_tail_risk_report_wires_returns_and_drawdown(analytics_conn):
    from mandi_rdd.analysis.tail_risk import tail_risk_report

    result = tail_risk_report(analytics_conn, "Onion")

    assert result["var_95"]["var"] > 0
    assert result["var_99"]["var"] >= result["var_95"]["var"]
    assert result["max_drawdown"]["max_drawdown_pct"] <= 0


def test_dml_report_recovers_finite_estimate(analytics_conn):
    from mandi_rdd.analysis.dml import dml_report

    result = dml_report(analytics_conn, "Onion")

    assert "error" not in result
    assert result["n_obs"] >= 200
    assert result["std_error"] > 0
    assert result["ci95_lower"] < result["ci95_upper"]


def test_nowcast_report_has_ordered_interval(analytics_conn):
    from mandi_rdd.analysis.nowcast import nowcast_report

    result = nowcast_report(analytics_conn, "Onion")

    assert "error" not in result
    assert result["ci_lower"] < result["nowcast_price"] < result["ci_upper"]


def test_conformal_report_wraps_the_forecast(analytics_conn):
    from mandi_rdd.analysis.conformal import conformal_report

    result = conformal_report(analytics_conn, "Onion")

    assert "error" not in result
    assert result["half_width"] > 0
    assert len(result["lower"]) == len(result["upper"]) == result["n_forecast_points"]
