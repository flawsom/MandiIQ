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

import urllib.error
from datetime import date

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
    assert "token_rejected" not in probe, (
        "a transport failure is not a verdict about the key"
    )


def test_probe_says_the_token_was_rejected_not_that_the_host_is_down(monkeypatch):
    """A 401 is an answer, and it is answered about the key.

    Production held a CEDA token the host refused while /health reported
    ``mirror_configured: true``, so "a token is set" and "the mirror works"
    looked identical - the warehouse was read as merely starved while nothing
    backfilled. The probe has to tell those apart, and its remedy for a
    rejected token is a new token, not retrying the network.
    """
    monkeypatch.setenv("MANDIIQ_CEDA_API_KEY", "token")

    def _reject(path, body=None, timeout=20.0):
        raise urllib.error.HTTPError(
            "https://api.ceda.ashoka.edu.in/v1" + path,
            401,
            "Unauthorized",
            {},
            None,
        )

    monkeypatch.setattr(fetch_ceda, "_request", _reject)
    probe = fetch_ceda.probe(timeout=0.1)

    assert probe["configured"] is True and probe["reachable"] is False
    assert probe["token_rejected"] is True
    assert probe["http_status"] == 401
    assert "401" in probe["error"]
    assert "re-issue" in probe["hint"]
    assert "MANDIIQ_CEDA_API_KEY" in probe["hint"]


# ---------------------------------------------------------------------------
# The response envelope
#
# Every CEDA endpoint answers {"output": {"type", "message", "data"}}. The
# first version of this adapter assumed the records sat at the top level, which
# the original tests above encoded as fact - so the suite was green while the
# adapter parsed nothing and reported "0 rows" for data it had received. These
# tests pin the real shape, because a fixture that disagrees with the wire
# format is worse than no fixture at all.
# ---------------------------------------------------------------------------


def test_the_real_output_envelope_is_unwrapped(monkeypatch):
    monkeypatch.setenv("MANDIIQ_CEDA_API_KEY", "token")
    _stub_request(
        monkeypatch,
        {
            "/agmarknet/commodities": {
                "output": {
                    "type": "success",
                    "message": "Data exists",
                    "data": [{"commodity_id": 23, "commodity_name": "Onion"}],
                }
            },
            "/agmarknet/geographies": {
                "output": {
                    "type": "success",
                    "message": "Data exists",
                    "data": [
                        {"census_state_id": 29, "census_state_name": "Karnataka"},
                        {
                            "census_state_id": 29,
                            "census_state_name": "Karnataka",
                            "census_district_id": 555,
                            "census_district_name": "Belgaum",
                        },
                    ],
                }
            },
            "/agmarknet/prices": {
                "output": {
                    "type": "success",
                    "message": "Data exists",
                    "data": [
                        {
                            "date": "2026-09-30T00:00:00.000Z",
                            "commodity_id": 23,
                            "census_state_id": 29,
                            "census_district_id": 555,
                            "market_id": 3149,
                            "min_price": 1200,
                            "max_price": 2400,
                            "modal_price": 1800,
                        }
                    ],
                }
            },
        },
        [],
    )

    pages = list(fetch_ceda.iter_ceda_pages(lookback_days=3, max_calls=1))
    assert len(pages) == 1
    rows = pages[0]
    assert len(rows) == 1, "the envelope must be unwrapped, not treated as empty"
    assert rows[0]["commodity"] == "Onion"
    assert rows[0]["state"] == "Karnataka"
    assert rows[0]["district"] == "Belgaum"
    assert rows[0]["modal_price"] == 1800


def test_an_empty_envelope_is_recognisable_as_empty_not_malformed():
    payload = {"output": {"type": "success", "message": "No data exists", "data": []}}
    assert fetch_ceda._rows(payload) == []
    # The message is the only thing separating "this window has no rows" from
    # "we failed to read the response".
    assert fetch_ceda.response_message(payload) == "No data exists"


def test_response_message_reads_the_nested_message():
    assert fetch_ceda.response_message(
        {"output": {"message": "Data exists", "data": []}}
    ) == "Data exists"
    assert fetch_ceda.response_message("not a dict") is None


# ---------------------------------------------------------------------------
# Rate limiting
#
# Measured against the live mirror: a short burst of calls earns HTTP 429 with
# `Retry-After: 1833` - a half-hour lockout. Sleeping that long inside a
# pipeline tick looks like a hang, so a long wait has to surface as its own
# state that the scheduler can record and move past.
# ---------------------------------------------------------------------------


def _rate_limited(monkeypatch, retry_after: str):
    """Make urlopen answer 429 with the given Retry-After, once."""
    import urllib.error

    calls = []

    def _fake_urlopen(request, timeout=None, context=None):
        calls.append(request)
        headers = {"Retry-After": retry_after} if retry_after is not None else {}
        raise urllib.error.HTTPError(
            request.full_url, 429, "Too Many Requests",
            type("H", (), {"get": lambda _self, k, d=None: headers.get(k, d)})(),
            None,
        )

    monkeypatch.setenv("MANDIIQ_CEDA_API_KEY", "token")
    monkeypatch.setattr(fetch_ceda.urllib.request, "urlopen", _fake_urlopen)
    return calls


def test_a_long_retry_after_is_not_waited_out(monkeypatch):
    calls = _rate_limited(monkeypatch, "1833")
    with pytest.raises(fetch_ceda.CedaRateLimited) as excinfo:
        fetch_ceda._request("/agmarknet/prices")
    assert excinfo.value.retry_after_s == pytest.approx(1833, abs=1)
    # One attempt only: blocking on a 30 minute wait is the bug being prevented.
    assert len(calls) == 1


def test_a_short_retry_after_is_retried(monkeypatch):
    monkeypatch.setenv("MANDIIQ_CEDA_API_KEY", "token")
    slept: list = []
    monkeypatch.setattr(fetch_ceda.time, "sleep", lambda s: slept.append(s))

    import json
    import urllib.error

    attempts = {"n": 0}
    real_endpoint_payload = {"output": {"message": "Data exists", "data": [{"modal_price": 1}]}}

    class _Resp:
        def __enter__(self_inner):
            return self_inner

        def __exit__(self_inner, *exc):
            return False

        def read(self_inner):
            return json.dumps(real_endpoint_payload).encode()

    def _fake_urlopen(request, timeout=None, context=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise urllib.error.HTTPError(
                request.full_url, 429, "Too Many Requests",
                type("H", (), {"get": lambda _self, k, d=None: "2" if k == "Retry-After" else d})(),
                None,
            )
        return _Resp()

    monkeypatch.setattr(fetch_ceda.urllib.request, "urlopen", _fake_urlopen)
    payload = fetch_ceda._request("/agmarknet/prices")
    assert fetch_ceda._rows(payload) == [{"modal_price": 1}]
    # The honoured Retry-After is among the sleeps; _pace() adds its own.
    assert 2.0 in slept, "a short Retry-After must be honoured before retrying"
    assert attempts["n"] == 2, "the call must be retried exactly once"


def test_requests_are_paced(monkeypatch):
    """Consecutive calls must not land faster than the configured interval."""
    monkeypatch.setenv("MANDIIQ_CEDA_MIN_INTERVAL_S", "0.25")
    monkeypatch.setattr(fetch_ceda, "_last_request_at", [0.0])
    slept: list = []
    monkeypatch.setattr(fetch_ceda.time, "sleep", lambda s: slept.append(s))
    fetch_ceda._pace()
    fetch_ceda._pace()
    assert slept, "the second call must wait for the interval to elapse"
    assert all(0 < s <= 0.25 for s in slept)


def test_pacing_can_be_disabled(monkeypatch):
    monkeypatch.setenv("MANDIIQ_CEDA_MIN_INTERVAL_S", "0")
    monkeypatch.setattr(fetch_ceda, "_last_request_at", [0.0])
    slept: list = []
    monkeypatch.setattr(fetch_ceda.time, "sleep", lambda s: slept.append(s))
    fetch_ceda._pace()
    assert slept == []


def test_a_window_can_be_shifted_into_the_archive(monkeypatch):
    """The trailing window is the wrong question for a frozen archive.

    CEDA's daily coverage stops around 2025-10. Asking for "the last 7 days"
    therefore always answers "No data exists", which is why arming the token
    backfilled nothing. A backfill passes the window end explicitly, and that
    is the only thing this test pins: the dates in the request.
    """
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

    list(
        fetch_ceda.iter_ceda_pages(
            lookback_days=90,
            max_calls=1,
            window_end=date(2025, 10, 1),
        )
    )
    body = [c[1] for c in calls if c[0] == "/agmarknet/prices"][0]
    assert body["to_date"] == "2025-10-01", "the window end must be the one asked for"
    # lookback_days is subtracted from the window end, so the span is
    # inclusive of both ends: 90 days before 2025-10-01 is 2025-07-03.
    assert body["from_date"] == "2025-07-03"
    # And the live walk is unaffected: with no window_end it still asks for a
    # window that ends today.
    calls.clear()
    list(fetch_ceda.iter_ceda_pages(lookback_days=7, max_calls=1))
    live = [c[1] for c in calls if c[0] == "/agmarknet/prices"][0]
    assert live["to_date"] == date.today().isoformat()


def test_a_rate_limited_call_ends_the_sweep_instead_of_burning_the_budget(monkeypatch):
    """A 429 is a lockout, not a bad cell.

    CEDA answers a burst with HTTP 429 and a Retry-After of about half an hour.
    Continuing to spend the budget after that turns one refusal into a sweep
    that cannot succeed, so the walk must stop and resume from the same pair.
    """
    monkeypatch.setenv("MANDIIQ_CEDA_API_KEY", "token")
    calls: list = []

    def _locked(path, body=None, timeout=45.0):
        calls.append((path, body))
        if path == "/agmarknet/prices":
            raise fetch_ceda.CedaRateLimited(1833.0, path)
        for key, value in {
            "/agmarknet/commodities": {
                "data": [{"commodity_id": 23, "commodity_disp_name": "Onion"}]
            },
            "/agmarknet/geographies": {
                "data": [
                    {"state_id": 29, "state_name": "Karnataka"},
                    {"state_id": 29, "district_id": 555, "district_name": "Belgaum"},
                ]
            },
        }.items():
            if path.startswith(key):
                return value
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(fetch_ceda, "_request", _locked)
    cursor: dict = {}
    list(fetch_ceda.iter_ceda_pages(max_calls=40, start_index=0, cursor_out=cursor))
    price_calls = [c for c in calls if c[0] == "/agmarknet/prices"]
    assert len(price_calls) == 1, "the sweep kept calling after a lockout"
    assert cursor["completed_pass"] is False, "a refused pass is not a completed one"
    assert cursor["offset"] == 0, "the refused pair must be retried, not skipped"
