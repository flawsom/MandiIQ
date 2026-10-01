"""Agmarknet daily prices via the CEDA API (Ashoka University, India).

Why this exists
===============
``api.data.gov.in`` - the documented source for ``9ef84268`` - is unreachable
from cloud networks. Verified on 2026-10-01 from three independent networks
(a GitHub Actions runner, a Daytona sandbox and a Northflank container):

  * ``api.data.gov.in`` (164.100.61.198) completes the TCP handshake and then
    drops every TLS handshake (``unexpected eof``) - no curl TLS version or
    cipher set gets in;
  * ``www.data.gov.in`` is fronted by Akamai and answers
    ``503 Service Unavailable - Fail to connect``: the edge cannot reach the
    origin either, so this is not something a client can work around;
  * the public CORS relays answer ``522`` for the same URL, which rules out a
    Cloudflare/Worker side-channel: only an Indian network reaches the host.

That is why the warehouse stopped advancing. It is not an ingest bug, and no
amount of retrying fixes it.

The reachable mirror is CEDA (the Centre for Economic Data and Analysis at
Ashoka University), which republishes Agmarknet over an India-hosted API:

    POST https://api.ceda.ashoka.edu.in/v1/agmarknet/prices
    body: {commodity_id:int, state_id:int, district_id:[int], market_id:[int],
           from_date:"yyyy-mm-dd", to_date:"yyyy-mm-dd"}
    -> {"output": {"type": "success", "message": "Data exists",
                    "data": [{date, commodity_id, census_state_id,
                              census_district_id, market_id,
                              min_price, max_price, modal_price}]}}

Note the envelope: the records live under ``output.data``, not at the top
level, and ``message`` is the only signal distinguishing "no rows matched"
from "the archive does not cover this window".

plus ``/agmarknet/commodities`` and ``/agmarknet/geographies`` for the id ->
name maps, and ``/agmarknet/markets`` for market names. District ids are
passed as a list, so one call covers every district of a state.

It needs an API token (the endpoint answers 401 "no api key passed" without
one). Set it as ``MANDIIQ_CEDA_API_KEY`` (``CEDA_API_KEY`` also works) and the
scheduler starts filling the daily gap autonomously; leave it unset and the
module is inert. Token request: https://api.ceda.ashoka.edu.in/documentation/

Rows are normalised into the same shape as the live feed so ``upsert_prices``
ingests them unchanged. ``market`` is the district name and ``variety``/
``grade`` carry a constant label, because this feed is district-level: saying
so explicitly keeps these rows from colliding with the variety-level rows the
data.gov.in feed writes for a day it does cover.
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("MANDIIQ_CEDA_BASE_URL", "https://api.ceda.ashoka.edu.in/v1")
DEFAULT_LOOKBACK_DAYS = 7
# The mirror answers 429 once calls come faster than it likes. Requests are
# therefore spaced by _min_interval() and a 429 is retried with backoff rather
# than raised: an unthrottled sweep would otherwise abort the whole ingest step
# on the first rate limit and report it as a source failure.
_MIN_INTERVAL_DEFAULT_S = 1.0
_RATE_LIMIT_MAX_RETRIES = 4
# Observed in production use: a burst of calls earns `429` with
# `Retry-After: 1833`, i.e. a 30 minute lockout. Honour short waits inline, but
# never sleep for half an hour inside a pipeline tick - a run that blocks that
# long looks like a hang. Anything longer is surfaced as CedaRateLimited so the
# caller can record "mirror busy" and let the next tick try again.
_RATE_LIMIT_MAX_WAIT_S = 5.0


class CedaRateLimited(RuntimeError):
    """The mirror asked us to wait longer than a pipeline tick can afford."""

    def __init__(self, retry_after_s: float, path: str):
        self.retry_after_s = retry_after_s
        self.path = path
        super().__init__(
            f"CEDA rate-limited {path}: retry after {retry_after_s:.0f}s"
        )
_pace_lock = threading.Lock()
_last_request_at = [0.0]


def _min_interval() -> float:
    try:
        return max(0.0, float(os.environ.get("MANDIIQ_CEDA_MIN_INTERVAL_S", _MIN_INTERVAL_DEFAULT_S)))
    except (TypeError, ValueError):
        return _MIN_INTERVAL_DEFAULT_S


def _pace() -> None:
    """Keep consecutive calls at least _min_interval() apart."""
    gap = _min_interval()
    if gap <= 0:
        return
    with _pace_lock:
        wait = gap - (time.monotonic() - _last_request_at[0])
        if wait > 0:
            time.sleep(wait)
        _last_request_at[0] = time.monotonic()
# Label written into variety/grade. Constant, so re-reading a day upserts onto
# itself instead of accumulating near-duplicates.
SOURCE_LABEL = "Agmarknet daily (CEDA)"
_SSL_CTX = ssl.create_default_context()

_cache_lock = threading.Lock()
_geography_cache: Optional[dict] = None
_commodity_cache: Optional[dict] = None


def ceda_api_key() -> Optional[str]:
    """The configured CEDA token, if any. Never logged."""
    key = os.environ.get("MANDIIQ_CEDA_API_KEY") or os.environ.get("CEDA_API_KEY")
    return key.strip() if key and key.strip() else None


def ceda_available() -> bool:
    """True when a token is configured, i.e. this source can be used."""
    return ceda_api_key() is not None


def _request(path: str, body: Optional[dict] = None, timeout: float = 45.0) -> dict:
    """One call to the CEDA API, authenticated with the configured token.

    The docs describe a bearer token; the 401 body says "no api key passed", so
    both spellings are tried before giving up. A 4xx other than 401/403 is
    raised as-is: it means the request itself is wrong and retrying cannot fix
    it.
    """
    key = ceda_api_key()
    if not key:
        raise RuntimeError(
            "MANDIIQ_CEDA_API_KEY is not set; the CEDA mirror is disabled"
        )
    data = json.dumps(body).encode() if body is not None else None
    header_variants = [
        {"Authorization": f"Bearer {key}"},
        {"api-key": key},
        {"x-api-key": key},
    ]
    last_error: Optional[Exception] = None
    for headers in header_variants + [None]:
        for attempt in range(_RATE_LIMIT_MAX_RETRIES + 1):
            url = f"{BASE_URL}{path}"
            if headers is None:
                # Last resort: the query-parameter spelling.
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}api_key={urllib.parse.quote(key)}"
                send_headers = {}
            else:
                send_headers = headers
            request = urllib.request.Request(
                url,
                data=data,
                method="POST" if body is not None else "GET",
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    **send_headers,
                },
            )
            _pace()
            try:
                with urllib.request.urlopen(request, timeout=timeout, context=_SSL_CTX) as f:
                    return json.loads(f.read())
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code in (401, 403):
                    break  # wrong token spelling - try the next one
                if exc.code in (429, 500, 502, 503, 504) and attempt < _RATE_LIMIT_MAX_RETRIES:
                    # Respect Retry-After when it is set, otherwise back off
                    # exponentially. 429 is a pacing signal, not a failure.
                    hinted = exc.headers.get("Retry-After") if exc.headers else None
                    try:
                        delay = float(hinted) if hinted else 0.0
                    except (TypeError, ValueError):
                        delay = 0.0
                    if delay <= 0:
                        delay = min(30.0, 2.0 ** attempt)
                    if delay > _RATE_LIMIT_MAX_WAIT_S:
                        raise CedaRateLimited(delay, path) from exc
                    logger.warning(
                        "CEDA %s on %s; retrying in %.1fs (attempt %d/%d)",
                        exc.code, path, delay, attempt + 1, _RATE_LIMIT_MAX_RETRIES,
                    )
                    time.sleep(delay)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                # A transport-level failure is not a token problem: trying the
                # other header spellings would just fail the same way.
                last_error = exc
                break
    if last_error is not None:
        raise last_error
    raise RuntimeError("CEDA request failed for an unknown reason")


def _rows(payload) -> list:
    """The record list out of a CEDA response, whatever wrapper it uses.

    Every CEDA endpoint answers ``{"output": {"type", "message", "data"}}``,
    and ``data`` is only present when the query matched something. ``output``
    therefore has to be searched like any other wrapper: without it the
    extraction found no list, every endpoint looked empty, and the mirror
    reported "0 rows" for data it had actually received - a silent failure
    that reads exactly like "the archive has nothing for this window".

    The empty case is deliberately indistinguishable here (``[]``); callers
    that need to tell "no rows" from "malformed response" should inspect
    ``message`` via :func:`response_message`.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("output", "data", "records", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                inner = _rows(value)
                if inner:
                    return inner
    return []


def response_message(payload) -> Optional[str]:
    """CEDA's own ``message`` field, e.g. "Data exists" or "No data exists"."""
    node = payload
    for _ in range(3):
        if not isinstance(node, dict):
            return None
        message = node.get("message")
        if isinstance(message, str):
            return message
        node = node.get("output", node.get("data"))
    return None


def _first(mapping: dict, *names):
    for name in names:
        if mapping.get(name) is not None:
            return mapping[name]
    return None


def _load_geographies() -> dict:
    """``{"states": {id: name}, "districts": {id: (name, state_id)}}``.

    Cached for the life of the process: the geography of India does not change
    between refreshes, and these two calls would otherwise repeat on every
    ingest tick.
    """
    global _geography_cache
    with _cache_lock:
        if _geography_cache is not None:
            return _geography_cache

    states: dict = {}
    districts: dict = {}
    payload = _request("/agmarknet/geographies")
    for row in _rows(payload):
        if not isinstance(row, dict):
            continue
        state_id = _first(row, "state_id", "census_state_id", "id")
        state_name = _first(row, "state_name", "census_state_name", "state", "name")
        district_id = _first(row, "district_id", "census_district_id")
        district_name = _first(row, "district_name", "census_district_name", "district", "name")
        # Two shapes are common: one row per state, or one row per district
        # carrying its state. Both are handled without knowing which arrives.
        if state_id is not None and state_name:
            states[int(state_id)] = str(state_name)
        if district_id is not None and district_name:
            parent = _first(row, "state_id", "census_state_id", "parent_state_id")
            districts[int(district_id)] = (
                str(district_name),
                int(parent) if parent is not None else None,
            )
    report = {"states": states, "districts": districts}
    with _cache_lock:
        if states or districts:
            _geography_cache = report
    logger.info(
        "CEDA geographies: %d states, %d districts", len(states), len(districts)
    )
    return report


def _load_commodities() -> dict:
    """``{commodity_id: display name}``, cached for the process."""
    global _commodity_cache
    with _cache_lock:
        if _commodity_cache is not None:
            return _commodity_cache
    commodities: dict = {}
    for row in _rows(_request("/agmarknet/commodities")):
        if not isinstance(row, dict):
            continue
        commodity_id = _first(row, "commodity_id", "id")
        name = _first(row, "commodity_disp_name", "commodity_name", "commodity", "name")
        if commodity_id is not None and name:
            commodities[int(commodity_id)] = str(name)
    with _cache_lock:
        if commodities:
            _commodity_cache = commodities
    logger.info("CEDA commodities: %d", len(commodities))
    return commodities


def _district_ids_by_state() -> dict:
    """``{state_id: [district_id, ...]}`` from the cached geographies."""
    out: dict = {}
    for district_id, (_name, state_id) in _load_geographies()["districts"].items():
        if state_id is None:
            continue
        out.setdefault(int(state_id), []).append(int(district_id))
    return out


def iter_ceda_pages(
    lookback_days: Optional[int] = None,
    max_calls: Optional[int] = None,
    start_index: int = 0,
    cursor_out: Optional[dict] = None,
    commodity_names: Optional[set] = None,
    window_end: Optional[date] = None,
):
    """Yield pages of Agmarknet daily rows for a window ending ``window_end``.

    One call per (commodity, state) pair covers every district in that state,
    so a full sweep of ~26 commodities x 36 states is ~900 calls - too many for
    a refresh tick, which is why ``max_calls`` bounds a pass and the walk
    resumes where it stopped on the next one (same contract as the data.gov.in
    walk: the cursor only moves forward, and a completed pass wraps to 0).

    ``window_end`` shifts the whole window into the past. The default (today)
    is the right question for a live mirror and the wrong one for this archive:
    CEDA's daily coverage stops around 2025-10, so "the last 7 days" always
    answers "No data exists" and arming the token changed nothing. A backfill
    passes the day before the oldest arrival date the warehouse holds instead,
    which walks the archive backwards while leaving the live question alone.

    Yields lists of dicts already shaped like ``prices`` rows.
    """
    if not ceda_available():
        return
    lookback = int(
        lookback_days
        if lookback_days is not None
        else os.environ.get("MANDIIQ_CEDA_LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS)
    )
    budget = int(
        max_calls
        if max_calls is not None
        else os.environ.get("MANDIIQ_CEDA_MAX_CALLS", "150")
    )
    to_date = window_end or date.today()
    from_date = to_date - timedelta(days=max(1, lookback))

    try:
        commodities = _load_commodities()
        districts_by_state = _district_ids_by_state()
    except Exception as exc:
        logger.warning("CEDA mirror unavailable while loading its catalogue: %s", exc)
        if cursor_out is not None:
            cursor_out.update({"offset": start_index, "completed_pass": False})
        return
    if not commodities or not districts_by_state:
        logger.warning("CEDA mirror returned no commodities or districts")
        return

    wanted = list(commodities.items())
    if commodity_names:
        wanted = [(cid, name) for cid, name in wanted if name.lower() in commodity_names]

    pairs = [
        (commodity_id, state_id, districts)
        for commodity_id, _name in wanted
        for state_id, districts in sorted(districts_by_state.items())
    ]
    if not pairs:
        return

    index = max(0, int(start_index or 0)) % len(pairs)
    calls = 0
    kept = 0
    interrupted = False
    while calls < budget:
        commodity_id, state_id, districts = pairs[index]
        try:
            payload = _request(
                "/agmarknet/prices",
                {
                    "commodity_id": int(commodity_id),
                    "state_id": int(state_id),
                    "district_id": [int(d) for d in districts],
                    "from_date": from_date.isoformat(),
                    "to_date": to_date.isoformat(),
                },
            )
            rows = _rows(payload)
            message = response_message(payload)
            if message and cursor_out is not None:
                # CEDA's own words for an empty response: "No data exists" for a
                # window its archive does not cover is the difference between an
                # armed mirror doing its job and an operator hunting a bug.
                cursor_out["last_message"] = message
        except CedaRateLimited as exc:
            # The host locked us out - usually a ~30 minute Retry-After. Every
            # remaining call in this pass would be refused too, so stop asking
            # and let the next tick resume from the same index. Spending the
            # rest of the budget on guaranteed refusals is how a sweep turns
            # into a self-inflicted lockout.
            logger.warning(
                "CEDA refused the sweep mid-pass (%s); resuming at index %d later",
                exc, index,
            )
            interrupted = True
            break
        except Exception as exc:
            # One bad cell must not kill the sweep; the next tick retries it.
            logger.warning(
                "CEDA prices call failed for commodity=%s state=%s: %s",
                commodity_id, state_id, exc,
            )
            rows = []
        calls += 1
        normalized = _normalize_rows(rows, commodity_id, state_id)
        kept += len(normalized)
        index = (index + 1) % len(pairs)
        if normalized:
            yield normalized
    # Wrapping back to the start is what "this pass covered everything from
    # where it began" looks like - not a call count, which is capped by the
    # budget and says nothing about coverage.
    completed = index == 0 and not interrupted
    logger.info(
        "CEDA mirror: %d calls, %d rows, next index %d (pass %s)",
        calls, kept, index,
        "rate-limited" if interrupted else ("complete" if completed else "partial"),
    )
    if cursor_out is not None:
        cursor_out.update(
            {
                "offset": 0 if completed else index,
                "total": len(pairs),
                "completed_pass": completed,
            }
        )


def _normalize_rows(rows, commodity_id: int, state_id: int) -> list:
    """Turn CEDA price rows into `prices`-shaped dicts."""
    geographies = _load_geographies()
    commodity_name = _load_commodities().get(int(commodity_id)) or f"id:{commodity_id}"
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        arrival = _first(row, "date", "arrival_date", "t")
        if not arrival:
            continue
        district_id = _first(row, "census_district_id", "district_id")
        district_name = None
        if district_id is not None:
            name, _parent = geographies["districts"].get(int(district_id), (None, None))
            district_name = name
        state_name = geographies["states"].get(int(state_id))
        out.append(
            {
                "arrival_date": str(arrival)[:10],
                "state": state_name,
                "district": district_name or (
                    f"district {district_id}" if district_id is not None else None
                ),
                "market": district_name or (
                    f"district {district_id}" if district_id is not None else None
                ),
                "commodity": commodity_name,
                "variety": SOURCE_LABEL,
                "grade": SOURCE_LABEL,
                "min_price": _first(row, "min_price", "p_min"),
                "max_price": _first(row, "max_price", "p_max"),
                "modal_price": _first(row, "modal_price", "p_modal"),
                "_source": {
                    "source_type": "api",
                    "source_name": f"Agmarknet via CEDA ({BASE_URL})",
                    "resource_id": "ceda:agmarknet",
                },
            }
        )
    return out


def probe(timeout: float = 20.0) -> dict:
    """Is the CEDA mirror configured, reachable and answering? For /health."""
    if not ceda_available():
        return {
            "configured": False,
            "reachable": False,
            "hint": "set MANDIIQ_CEDA_API_KEY to enable the CEDA mirror",
        }
    started = time.monotonic()
    try:
        key = ceda_api_key()
        out = {}
        for path, body in (
            ("/agmarknet/commodities", None),
            (
                "/agmarknet/prices",
                {
                    "commodity_id": 23,
                    "state_id": 29,
                    "district_id": [],
                    "from_date": (date.today() - timedelta(days=7)).isoformat(),
                    "to_date": date.today().isoformat(),
                },
            ),
        ):
            payload = _request(path, body, timeout=timeout)
            out[path] = len(_rows(payload))
        return {
            "configured": True,
            "reachable": True,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "key_len": len(key or ""),
            "counts": out,
        }
    except urllib.error.HTTPError as exc:
        # A 401/403 is not "the host is unreachable": the host answered, and it
        # answered that this token is not acceptable. The two have different
        # fixes - a network versus a key - and while they read the same, an
        # operator holding a stale token sees "configured: true" and concludes
        # the mirror is armed. Nothing backfills while the host refuses it, so
        # the probe says which one happened and what to do about it.
        rejected = exc.code in (401, 403)
        out = {
            "configured": True,
            "reachable": False,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "http_status": exc.code,
            "error": f"HTTPError: HTTP Error {exc.code}",
        }
        if rejected:
            out["token_rejected"] = True
            out["hint"] = (
                "the mirror refused the configured token - re-issue it at "
                "https://api.ceda.ashoka.edu.in/documentation/ and re-paste "
                "MANDIIQ_CEDA_API_KEY. A token the host rejects is not an armed "
                "mirror: no rows arrive while it is set."
            )
        return out
    except Exception as exc:
        return {
            "configured": True,
            "reachable": False,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }
