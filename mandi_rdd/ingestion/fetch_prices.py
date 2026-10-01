"""
MandiRDD - paginated mandi price ingestion from data.gov.in API.

Features:
- Server-side filtering by state/commodity
- Paginated fetch with offset/limit
- Exponential backoff retry (3 attempts, cap ~30s)
- Parallel-probe stale-detection for 80M-row variety-wise archive
- Progress reporting
"""

import json
import logging
import os
import random
import time
import urllib.parse
from typing import Optional

from mandi_rdd.core.dates import classify_date
from mandi_rdd.ingestion.http_client import SSL_CTX

# Default public API key (rate-limited but works)

logger = logging.getLogger(__name__)

PRIMARY_RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"
PRIMARY_HOST = "api.data.gov.in"
DEFAULT_BASE_URL = f"https://{PRIMARY_HOST}/resource/{PRIMARY_RESOURCE_ID}"
BASE_URL = DEFAULT_BASE_URL


def _source_candidates(resource_id: Optional[str] = None) -> list:
    """Hosts to try, in order, for one resource.

    A single hard-coded host makes the whole warehouse depend on one DNS name:
    on 2026-09-30 api.data.gov.in began refusing connections (verified from two
    networks) and the price feed stopped dead. The other hosts below serve the
    same open data and are tried in turn when the previous one cannot be
    reached at all. A 4xx is *not* retried elsewhere - a bad key or a bad
    request will fail the same way everywhere - but a refused connection, a
    timeout or a 5xx moves to the next host.

    ``MANDIIQ_PRICE_SOURCES`` adds hosts to the end of the chain - a
    comma-separated list of ``host`` or ``host|resource_id`` entries - so an
    operator can point the pipeline at a mirror (including the NDVI instance)
    without a code change, while the open-data hosts remain the first try.

    ``agmarknet.gov.in`` is deliberately *not* in the chain. It is a portal,
    not a resource endpoint - ``agmarknet.gov.in/resource/<id>`` is a 403 for
    every caller, so listing it only spent a round trip on every fetch before
    falling through. The reachable Agmarknet mirror is the CEDA API, which
    needs its own adapter (see ``fetch_ceda``) because it speaks a different
    request and response shape; the scheduler falls back to it when this walk
    yields nothing.
    """
    rid = resource_id or PRIMARY_RESOURCE_ID
    chain = [(PRIMARY_HOST, rid)]

    override = (os.environ.get("MANDIIQ_PRICE_SOURCES") or "").strip()
    for item in override.split(","):
        item = item.strip()
        if not item:
            continue
        host, _, host_resource = item.partition("|")
        chain.append((host.strip(), host_resource.strip() or rid))
    return chain


def _fetch_url(host: str, resource_id: str, params: list) -> str:
    """A single URL for one host. Kept separate so tests can assert the chain."""
    if resource_id in host:
        return host if host.startswith("http") else f"https://{host}"
    return f"https://{host}/resource/{resource_id}?{'&'.join(params)}"

# SSL context for Windows
# SSL_CTX imported from http_client

# Map PascalCase / snake_case source fields -> DuckDB `prices` columns.
_PRICE_FIELD_SYNONYMS = {
    "state": "state", "district": "district", "market": "market",
    "commodity": "commodity", "variety": "variety", "grade": "grade",
    "arrival_date": "arrival_date",
    "min_price": "min_price", "max_price": "max_price", "modal_price": "modal_price",
    # PascalCase aliases (resource 35985678)
    "State": "state", "District": "district", "Market": "market",
    "Commodity": "commodity", "Commodity_Code": "commodity_code",
    "Variety": "variety", "Grade": "grade", "Arrival_Date": "arrival_date",
    "Min_Price": "min_price", "Max_Price": "max_price", "Modal_Price": "modal_price",
    # other observed spellings
    "state_name": "state", "district_name": "district", "market_name": "market",
}

def normalize_price_record(raw: dict) -> dict:
    """Normalize a raw API record (any schema) into `prices`-table columns."""
    out = {}
    for k, v in raw.items():
        key = _PRICE_FIELD_SYNONYMS.get(k, k)
        if key in ("state", "district", "market", "commodity", "variety", "grade",
                   "arrival_date", "min_price", "max_price", "modal_price"):
            out[key] = v
    for num_field in ("min_price", "max_price", "modal_price"):
        if num_field in out and out[num_field] not in (None, ""):
            try:
                out[num_field] = float(out[num_field])
            except (TypeError, ValueError):
                out[num_field] = None
    for f in ("state", "district", "market", "commodity", "variety", "grade"):
        if f in out and out[f] == "":
            out[f] = None
    if out.get("arrival_date") not in (None, ""):
        iso, status = classify_date(out["arrival_date"])
        if status == "ok":
            out["arrival_date"] = iso
        else:
            # A wrong date is worse than no date: upsert_prices drops the row
            # instead of filing it under a day it was never quoted on.
            logger.debug("Rejecting arrival_date %r (%s)", out["arrival_date"], status)
            out["arrival_date"] = None
    return out

def _try_sources(hosts, params, timeout: float = 45.0, retries: int = 3):
    """Fetch one page, trying every source host in order.

    Returns ``(payload, host)`` for the first host that answers. A 4xx that is
    not 429 means the request itself is wrong - a bad key or a bad filter - and
    would fail identically everywhere, so it is raised at once. Transient
    failures (refused connections, timeouts, 5xx) exhaust their retries on one
    host and then move on to the next, so a single host outage degrades to
    "slower" instead of "no prices".
    """
    last_error = None
    for host, resource_id in hosts:
        url = _fetch_url(host, resource_id, params)
        for attempt in range(retries):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as f:
                    return json.loads(f.read()), host
            except urllib.error.HTTPError as e:
                if 400 <= e.code < 500 and e.code != 429:
                    logger.error(
                        "Price source %s rejected the request (HTTP %s): %s",
                        host, e.code, e,
                    )
                    raise
                last_error = e
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_error = e
            if attempt < retries - 1:
                wait = min(2 ** attempt * 2, 30) + random.uniform(0, 1.5)
                logger.warning(
                    "Price source %s attempt %d/%d failed (%s); retrying in %.1fs",
                    host, attempt + 1, retries, last_error, wait,
                )
                time.sleep(min(wait, 3.0))
        logger.warning("Price source %s is unreachable; trying the next source", host)
    logger.error("Every price source failed; the last error was: %s", last_error)
    raise last_error if last_error else RuntimeError("no price sources configured")


def fetch_page_with_source(resource_id: str, offset: int = 0, limit: int = 1000,
                           filters=None, format: str = "json", extra_params=None):
    """fetch_page() that also says which host served the response."""
    api_key = _get_api_key()
    params = [
        f"api-key={api_key}", f"format={format}",
        f"limit={limit}", f"offset={offset}",
    ]
    if filters:
        for key, value in filters.items():
            if value:
                params.append(f"filters[{key}]={urllib.parse.quote(str(value))}")
    if extra_params:
        params.extend(extra_params)
    return _try_sources(_source_candidates(resource_id), params)


def fetch_page_for_resource(resource_id: str, offset: int = 0, limit: int = 1000,
                            filters=None, format: str = "json", extra_params=None) -> dict:
    """fetch_page() targeting an arbitrary resource id (e.g. 35985678)."""
    payload, _host = fetch_page_with_source(
        resource_id, offset=offset, limit=limit, filters=filters,
        format=format, extra_params=extra_params,
    )
    return payload

def _get_api_key() -> str:
    """Get API key from env; fail loudly if missing/invalid (no fallback key).

    Accepts either canonical name so a local .env using DATA_GOV_API_KEY also
    works:
      - DATA_GOV_IN_API_KEY (canonical, used by scheduler)
      - DATA_GOV_API_KEY    (alternate spelling seen in some .env files)
    """
    key = os.environ.get("DATA_GOV_IN_API_KEY") or os.environ.get("DATA_GOV_API_KEY")
    if not key:
        raise RuntimeError(
            "DATA_GOV_IN_API_KEY (or DATA_GOV_API_KEY) is not set. Refusing to "
            "fall back to a shared default key (PRD Phase 1). Set the secret in "
            "CI or local .env."
        )
    if len(key) < 16 or key.strip() in ("changeme", "<your-key>"):
        raise RuntimeError(f"DATA_GOV API key looks invalid (len={len(key)}).")
    return key

def source_diagnostics(timeout: float = 20.0) -> list:
    """Probe every configured price source once and report what happened.

    Built for ``GET /admin/source-probe``: when the warehouse stops advancing,
    the first question is "is the source unreachable, or is it answering with
    stale data?" - and the only way to tell the two apart is to ask the source
    from the same network the ingest runs on. Reports the newest arrival date
    each host serves, so "upstream has nothing newer" and "we cannot reach
    upstream" stop looking identical.
    """
    try:
        api_key = _get_api_key()
    except Exception as exc:
        return [
            {
                "host": PRIMARY_HOST,
                "ok": False,
                "error": f"no usable API key configured ({exc})",
            }
        ]
    params = [f"api-key={api_key}", "format=json", "limit=1", "offset=0"]
    results = []
    for host, resource_id in _source_candidates():
        url = _fetch_url(host, resource_id, params)
        started = time.monotonic()
        entry = {"host": host, "resource_id": resource_id}
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(
                request, timeout=timeout, context=SSL_CTX
            ) as response:
                payload = json.loads(response.read())
            records = payload.get("records") or []
            dates = [
                r.get("arrival_date") for r in records if r.get("arrival_date")
            ]
            entry.update(
                {
                    "ok": True,
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "total": payload.get("total"),
                    "sample_records": len(records),
                    "newest_in_sample": max(dates) if dates else None,
                }
            )
        except urllib.error.HTTPError as exc:
            entry.update(
                {
                    "ok": False,
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "http_status": exc.code,
                    "error": f"HTTP {exc.code}",
                }
            )
        except Exception as exc:
            entry.update(
                {
                    "ok": False,
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        results.append(entry)
    return results


def fetch_page(
    offset: int = 0,
    limit: int = 1000,
    filters: Optional[dict] = None,
    format: str = "json",
) -> dict:
    """
    Fetch a single page of mandi prices from data.gov.in.

    Args:
        offset: Record offset for pagination
        limit: Records per page (max 1000)
        filters: Dict of server-side filters, e.g.
                {"state.keyword": "Maharashtra", "commodity": "Onion"}
        format: Response format (json or csv)

    Returns:
        Dict with 'records', 'total', 'count', 'limit', 'offset' keys
    """
    api_key = _get_api_key()
    params = [
        f"api-key={api_key}",
        f"format={format}",
        f"limit={limit}",
        f"offset={offset}",
    ]

    # Add server-side filters
    if filters:
        for key, value in filters.items():
            if value:  # Skip empty filters
                params.append(f"filters[{key}]={urllib.parse.quote(str(value))}")

    payload, _host = _try_sources(_source_candidates(), params, retries=5)
    return payload

def iter_price_pages(
    filters: Optional[dict] = None,
    max_records: Optional[int] = None,
    page_size: int = 1000,
    progress_callback=None,
    max_run_seconds: Optional[float] = None,
    start_offset: int = 0,
    cursor_out: Optional[dict] = None,
):
    """Yield pages of price records instead of returning one giant list.

    The pipeline that serves live traffic must not hold the whole archive in
    memory: a container that fetches every page into a list before writing any
    of it is one bad day away from being OOM-killed mid-ingest. Callers upsert
    each page and drop it, so peak memory is one page regardless of how far the
    walk gets.

    ``max_run_seconds`` bounds a single walk so a slow source cannot keep an
    ingest open indefinitely. The walk resumes from ``start_offset`` (last
    run's cursor) and writes where it stopped into ``cursor_out`` - without
    that, every run restarted at offset 0 and only ever re-read the newest
    pages, so the archive's tail was never reached. A pass that makes it to the
    end wraps back to 0; a pass cut short by the time budget keeps its place.
    It does not trust an offset to mean the same record twice: new rows land at
    the top of the API's ordering and shift every later offset, so a resumed
    page may be re-read. Re-reading is cheap (upserts are idempotent) and is
    the only safe assumption for a source with no stable cursor.
    """
    # Graceful skip if no API key - allows pipeline to run RDD on existing data
    try:
        _get_api_key()
    except RuntimeError:
        logger.info(
            "DATA_GOV_IN_API_KEY not set - skipping price fetch. "
            "RDD analysis can still run on existing data."
        )
        return

    started = time.monotonic()
    offset = max(0, int(start_offset or 0))
    completed = False
    served_by = None
    fetched = 0
    total = 0
    while True:
        payload, host = fetch_page_with_source(
            PRIMARY_RESOURCE_ID, offset=offset, limit=page_size, filters=filters,
        )
        served_by = host
        records = payload.get("records", []) or []
        total = payload.get("total", 0) or 0

        for record in records:
            record["_source"] = {
                "source_type": "api",
                "source_name": f"mandi prices via {host}",
                "resource_id": PRIMARY_RESOURCE_ID,
            }

        fetched += len(records)
        if progress_callback:
            progress_callback(fetched, total)
        if records:
            yield records

        if not records:
            completed = True
            break
        if max_records and fetched >= max_records:
            logger.info(f"Price fetch stopping at the {max_records}-record budget")
            break
        if max_run_seconds and (time.monotonic() - started) >= max_run_seconds:
            logger.info(
                f"Price fetch stopping after {max_run_seconds:.0f}s "
                f"({fetched} records at offset {offset}); the next run resumes "
                "from here rather than restarting at the newest page"
            )
            break
        if offset + page_size >= total:
            completed = True
            break

        offset += page_size
        # Small delay to be polite to the API
        time.sleep(0.2)

    if cursor_out is not None:
        cursor_out["offset"] = 0 if completed else offset
        cursor_out["total"] = total
        cursor_out["completed_pass"] = completed
        cursor_out["served_by"] = served_by


def fetch_all_prices(
    filters: Optional[dict] = None,
    max_records: Optional[int] = None,
    page_size: int = 5000,
    progress_callback=None,
) -> list[dict]:
    """
    Fetch ALL mandi price records via pagination.

    If DATA_GOV_IN_API_KEY is not set, returns [] immediately so the
    pipeline can still run RDD analysis on existing data.

    Returns a list of dicts; each dict includes a ``_source`` key with
    metadata for data-lineage tracking:
        {
            "source_type": "api",
            "source_name": "data.gov.in daily mandi prices",
            "resource_id": "9ef84268-...",
        }

    Args:
        filters: Server-side filters to narrow results
        max_records: Limit total records (None = all)
        page_size: Records per API call (max 1000)
        progress_callback: Optional fn(records_so_far, total)

    Returns:
        List of record dicts, each with a ``_source`` metadata key.
    """
    # Kept for callers that genuinely want one list (CLI one-shots, tests).
    # The pipeline uses iter_price_pages so it can write each page as it lands.
    all_records: list[dict] = []
    for page in iter_price_pages(
        filters=filters,
        max_records=max_records,
        page_size=page_size,
        progress_callback=progress_callback,
    ):
        all_records.extend(page)

    if max_records:
        all_records = all_records[:max_records]
    return all_records

def fetch_commodities() -> list[str]:
    """Get unique commodity list from a small sample pull."""
    data = fetch_page(limit=100)
    commodities = sorted(set(
        r.get("commodity", "") for r in data.get("records", []) if r.get("commodity")
    ))
    return commodities

def fetch_states() -> list[str]:
    """Get unique state list."""
    data = fetch_page(limit=100)
    states = sorted(set(
        r.get("state", "") for r in data.get("records", []) if r.get("state")
    ))
    return states

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Quick test: pull 5 records and print
    test = fetch_page(limit=5)
    print(f"API test: {test.get('count', 0)} records (total={test.get('total', '?')})")
    for r in test.get("records", [])[:3]:
        print(f"  {r.get('state')} | {r.get('district')} | {r.get('commodity')} | {r.get('arrival_date')} | modal={r.get('modal_price')}")

    # List available commodities
    comms = fetch_commodities()
    print(f"\nAvailable commodities ({len(comms)}): {comms[:10]}...")
    print(f"Available states: {fetch_states()}")
