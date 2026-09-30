"""Date-integrity guarantees for the price warehouse.

A price can only be filed under the day it was actually quoted. These tests
pin the two failure modes that made the live numbers untrustworthy:

1. data.gov.in publishes ``DD/MM/YYYY``; month-first parsing filed September
   records under December and rejected nothing.
2. Rows that cannot be true (a future arrival date) were stored anyway and
   polluted freshness, RDD and forecast outputs.
"""

from __future__ import annotations

import datetime as dt

import duckdb
import pytest

TODAY = dt.date(2026, 9, 30)


def test_day_first_source_dates_are_not_read_month_first():
    from mandi_rdd.core.dates import parse_arrival_date

    assert parse_arrival_date("12/09/2026", today=TODAY) == "2026-09-12"
    assert parse_arrival_date("08/09/2026", today=TODAY) == "2026-09-08"
    assert parse_arrival_date("24/09/2026", today=TODAY) == "2026-09-24"
    assert parse_arrival_date("2026-09-12", today=TODAY) == "2026-09-12"


def test_untrustworthy_dates_are_rejected_not_coerced():
    from mandi_rdd.core.dates import classify_date, parse_arrival_date

    assert parse_arrival_date("2099-12-09", today=TODAY) is None
    assert classify_date("2099-12-09", today=TODAY)[1] == "future"
    assert classify_date("", today=TODAY)[1] == "missing"
    assert classify_date(None, today=TODAY)[1] == "missing"
    assert classify_date("not-a-date", today=TODAY)[1] == "unparseable"
    assert classify_date("1899-01-01", today=TODAY)[1] == "impossible"
    # One day of slack absorbs IST/UTC skew and next-day feeds.
    assert parse_arrival_date("2026-10-01", today=TODAY) == "2026-10-01"


def test_summarize_dates_counts_every_rejection_reason():
    from mandi_rdd.core.dates import summarize_dates

    report = summarize_dates(
        ["12/09/2026", "2099-01-01", "", "garbage", "2026-09-24"], today=TODAY
    )
    assert report["ok"] == 2
    assert report["future"] == 1
    assert report["missing"] == 1
    assert report["unparseable"] == 1
    assert report["rejected"] == 3


def test_swap_month_day_inverts_the_month_first_misparse():
    from mandi_rdd.core.dates import swap_month_day

    assert swap_month_day("2026-12-09") == dt.date(2026, 9, 12)
    assert swap_month_day("2026-11-08") == dt.date(2026, 8, 11)
    assert swap_month_day("nonsense") is None


def test_repair_rewrites_future_rows_and_drops_impossible_ones():
    from mandi_rdd.core.dates import date_quality, repair_future_dates
    from mandi_rdd.storage import duckdb_store

    conn = duckdb.connect(":memory:")
    duckdb_store.init_schema(conn)
    conn.executemany(
        """
        INSERT INTO prices (state, district, market, commodity, variety, grade,
                            arrival_date, min_price, max_price, modal_price)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("TN", "Salem", "Salem APMC", "Tomato", "Other", "FAQ", "2026-12-09", 10, 20, 15),
            ("TN", "Salem", "Salem APMC", "Tomato", "Other", "FAQ", "2026-09-24", 10, 20, 16),
            ("MH", "Nashik", "Nashik APMC", "Onion", "Other", "FAQ", "2099-05-05", 10, 20, 17),
        ],
    )

    report = repair_future_dates(conn, today=TODAY)
    assert report["n_future"] == 2
    assert report["repaired"] == 1
    assert report["dropped"] == 1

    dates = [
        str(row[0])
        for row in conn.execute(
            "SELECT arrival_date FROM prices ORDER BY arrival_date"
        ).fetchall()
    ]
    assert dates == ["2026-09-12", "2026-09-24"]

    quality = date_quality(conn, today=TODAY)
    assert quality["status"] == "ok"
    assert quality["n_future_dates"] == 0
    assert quality["max_date"] == "2026-09-24"
    assert quality["days_behind"] == 6
    conn.close()


def test_repair_dry_run_changes_nothing():
    from mandi_rdd.core.dates import repair_future_dates
    from mandi_rdd.storage import duckdb_store

    conn = duckdb.connect(":memory:")
    duckdb_store.init_schema(conn)
    conn.execute(
        """
        INSERT INTO prices (state, district, market, commodity, variety, grade,
                            arrival_date, min_price, max_price, modal_price)
        VALUES ('TN', 'Salem', 'Salem APMC', 'Tomato', 'Other', 'FAQ', '2099-01-01', 1, 2, 1.5)
        """
    )
    report = repair_future_dates(conn, today=TODAY, dry_run=True)
    assert report["n_future"] == 1
    assert report["repaired"] == 0
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 1
    conn.close()


def test_upsert_prices_refuses_untrustworthy_arrival_dates():
    from mandi_rdd.storage import duckdb_store

    conn = duckdb.connect(":memory:")
    duckdb_store.init_schema(conn)
    base = {
        "state": "TN",
        "district": "Salem",
        "market": "Salem APMC",
        "commodity": "Tomato",
        "variety": "Other",
        "grade": "FAQ",
        "min_price": 1.0,
        "max_price": 2.0,
        "modal_price": 1.5,
    }
    inserted = duckdb_store.upsert_prices(conn, [
        {**base, "arrival_date": "2099-12-09"},
        {**base, "arrival_date": "garbage"},
        {**base, "arrival_date": "24/09/2026", "market": "Salem Uzhavar"},
    ])
    stored = conn.execute(
        "SELECT market, arrival_date FROM prices ORDER BY market"
    ).fetchall()
    conn.close()

    assert inserted == 1
    assert [(m, str(d)) for m, d in stored] == [("Salem Uzhavar", "2026-09-24")]


def test_get_connection_joins_an_open_handle_instead_of_raising(tmp_path):
    from mandi_rdd.storage import duckdb_store

    db_path = tmp_path / "mandi_iq.duckdb"
    write_conn = duckdb_store.get_connection(db_path=db_path)
    try:
        # Second handle, same mode.
        second = duckdb_store.get_connection(db_path=db_path)
        assert second.execute("SELECT 42").fetchone()[0] == 42
        second.close()

        # Opposite mode in the same process: DuckDB refuses to mix
        # configurations, so get_connection must join the live instance
        # rather than propagating the ConnectionException.
        reader = duckdb_store.get_connection(db_path=db_path, read_only=True)
        assert reader.execute("SELECT 1").fetchone()[0] == 1
        reader.close()
    finally:
        write_conn.close()


def test_scheduler_feeds_backfill_a_lookup_and_records_ingest_status():
    from pathlib import Path

    source = Path("mandi_rdd/ingestion/scheduler.py").read_text(encoding="utf-8")

    assert "backfill(conn)" not in source, "backfill() takes a lookup dict, not a connection"
    assert "backfill(build_lookup())" in source
    assert "_write_ingest_status(summary)" in source, "/health must see the real last run"
    assert "repair_future_dates(conn)" in source, "pipeline must heal arrival dates"


def test_ingestion_runs_are_serialised():
    from mandi_rdd.ingestion import scheduler

    lock = scheduler._RUN_LOCK
    assert lock.acquire(blocking=False)
    try:
        summary = scheduler.run_ingestion()
        assert summary["status"] == "busy"
        assert scheduler.ingestion_running() is True
    finally:
        lock.release()
    assert scheduler.ingestion_running() is False


@pytest.mark.parametrize("value", [None, "", "nan", "NaT"])
def test_blank_dates_are_classified_as_missing(value):
    from mandi_rdd.core.dates import classify_date

    assert classify_date(value, today=TODAY) == (None, "missing")
