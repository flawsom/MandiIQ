"""The daily push has to be safe to repeat, and honest about doing nothing.

The production container cannot read api.data.gov.in (it resets TLS from every
cloud network this project runs on), so the newest arrival date only moves when
a network that *can* read it hands the rows over. That hand-off is
``scripts/push_live_prices.py``. These tests pin the two things that make it safe
to run every day:

* the body it uploads is the canonical shape the ingest endpoint detects, and
* a source that cannot be read is reported as "nothing to push" - never as a
  push that worked.
"""

from __future__ import annotations

import urllib.error

from mandi_rdd.scripts import push_live_prices

RECORD = {
    "arrival_date": "2026-10-02",
    "state": "Maharashtra",
    "district": "Pune",
    "market": "Pune",
    "commodity": "Onion",
    "variety": "FAQ",
    "grade": "FAQ",
    "min_price": 1200.0,
    "max_price": 1800.0,
    "modal_price": 1500.0,
    # Extras the fetcher attaches: the upload must not carry them.
    "_source": {"source_type": "api", "resource_id": "9ef84268"},
}


def test_the_upload_body_is_the_canonical_ten_columns():
    body = push_live_prices.build_csv([RECORD])
    lines = body.decode("utf-8").splitlines()

    assert lines[0] == ",".join(push_live_prices.CANONICAL_COLUMNS), (
        "the endpoint detects the canonical shape by its header"
    )
    assert len(lines[1].split(",")) == len(push_live_prices.CANONICAL_COLUMNS)
    assert lines[1].startswith("2026-10-02,Maharashtra,Pune,Pune,Onion")
    assert "_source" not in lines[0] and "source_type" not in body.decode("utf-8"), (
        "fetcher metadata is not a price column"
    )


def test_a_missing_or_nan_value_is_written_empty_not_dropped():
    """A row with no variety is still a price, and NaN is not a number."""
    body = push_live_prices.build_csv(
        [{"arrival_date": "2026-10-02", "commodity": "Onion", "modal_price": float("nan")}]
    )
    row = body.decode("utf-8").splitlines()[1].split(",")

    assert row[0] == "2026-10-02"
    assert row[4] == "Onion"
    assert row[9] == "", "NaN must not be uploaded as 'nan'"
    assert len(row) == len(push_live_prices.CANONICAL_COLUMNS)


def test_the_body_the_push_writes_is_a_shape_the_endpoint_knows():
    """Half of this contract lives on each side; this is where they meet."""
    from mandi_rdd.api import main as api_main

    header = push_live_prices.build_csv([RECORD]).decode("utf-8").splitlines()[0]

    assert api_main._detect_historical_format(header, "mandiiq-live-snapshot.csv") == "canonical"
    sql = api_main._historical_projection_sql("canonical", "/tmp/upload.csv")
    for column in push_live_prices.CANONICAL_COLUMNS:
        assert f"AS {column}" in sql, f"the canonical projection does not name {column}"


def test_an_unreadable_source_is_not_reported_as_a_push(monkeypatch, capsys):
    """The common failure on a cloud network must be a notice, not a green run."""

    def unreachable(max_records=None):
        raise urllib.error.URLError("<urlopen error [Errno 111] Connection refused>")

    monkeypatch.setattr(push_live_prices, "_source_records", unreachable)
    monkeypatch.setattr(push_live_prices, "freshness", lambda *a, **k: {})

    assert push_live_prices.main(["--api", "http://127.0.0.1:9"]) == 2

    out = capsys.readouterr().out
    assert "nothing to push" in out
    assert "does not answer one" in out, "the operator is told why, and what to do"


def test_a_dry_run_uploads_nothing(monkeypatch, tmp_path, capsys):
    csv_path = tmp_path / "snapshot.csv"
    csv_path.write_text(
        "arrival_date,state,district,market,commodity,variety,grade,min_price,max_price,modal_price\n"
        "2026-10-02,Maharashtra,Pune,Pune,Onion,FAQ,FAQ,1200,1800,1500\n",
        encoding="utf-8",
    )

    def must_not_run(*args, **kwargs):
        raise AssertionError("a dry run must not push")

    monkeypatch.setattr(push_live_prices, "push", must_not_run)
    monkeypatch.setattr(push_live_prices, "freshness", lambda *a, **k: {})

    assert push_live_prices.main(["--file", str(csv_path), "--dry-run"]) == 0
    assert "dry run: nothing sent" in capsys.readouterr().out
