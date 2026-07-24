"""
MandiRDD — paginated mandi price ingestion from data.gov.in API.

Features:
- Server-side filtering by state/commodity
- Paginated fetch with offset/limit
- Exponential backoff retry (3 attempts, cap ~30s)
- Progress reporting
"""

import os
import json
import datetime
import urllib.parse
import time
import urllib.request
import urllib.error
import ssl
import logging
from typing import Optional

# Default public API key (rate-limited but works)

logger = logging.getLogger(__name__)


BASE_URL = "https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070"

# SSL context for Windows
SSL_CTX = ssl.create_default_context()

# Supplementary "variety-wise" price archive (data.gov.in resource 35985678-...).
# 80M+ historical rows; used as a best-effort SUPPLEMENTARY feed for recent
# variety-level prices. Paginating the full archive is infeasible, so we rely on
# a server-side arrival-date filter + a hard record cap (see fetch_varietywise_recent).
VARIETYWISE_RESOURCE_ID = "35985678-0d79-46b4-9ed6-6f13308a1d24"

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
    return out

def fetch_page_for_resource(resource_id: str, offset: int = 0, limit: int = 1000,
                            filters=None, format: str = "json", extra_params=None) -> dict:
    """fetch_page() targeting an arbitrary resource id (e.g. 35985678)."""
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
    url = f"https://api.data.gov.in/resource/{resource_id}?{'&'.join(params)}"
    max_retries = 3
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as f:
                return json.loads(f.read())
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt * 2
                logger.warning(f"Attempt {attempt + 1} failed for {resource_id}: {e}. Retrying in {wait}s...")
                time.sleep(wait)
            else:
                logger.error(f"All {max_retries} attempts failed for {resource_id}: {e}")
                raise

def _iso_boundary_days_ago(days: int) -> str:
    return (datetime.datetime.utcnow() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")

def fetch_varietywise_recent(days: int = 60, max_records: int = 20000) -> list:
    """Best-effort supplementary feed from the 80M-row variety-wise archive.

    Uses a server-side Arrival_Date >= (today-days) filter, capped at
    `max_records`. If the API ignores the filter (oldest-first ordering), we
    detect out-of-window pages and stop early so we NEVER scan the full 80M rows.
    Always returns a list of normalized `prices`-schema dicts (may be empty).
    """
    try:
        _get_api_key()
    except RuntimeError:
        logger.info("DATA_GOV_IN_API_KEY not set — skipping variety-wise supplement.")
        return []
    since = _iso_boundary_days_ago(days)
    since_date = datetime.datetime.strptime(since, "%Y-%m-%d").date()
    extra = [f"filters[Arrival_Date][>=]={urllib.parse.quote(since)}"]
    page_size = 1000
    out, offset, seen_dates, window_ok = [], 0, 0, 0
    while len(out) < max_records:
        try:
            data = fetch_page_for_resource(
                VARIETYWISE_RESOURCE_ID, offset=offset, limit=page_size, extra_params=extra
            )
        except Exception as e:
            logger.warning(f"Variety-wise fetch failed at offset {offset}: {e}")
            break
        records = data.get("records", [])
        total = data.get("total", 0)
        if not records:
            break
        window_ok = 0
        for r in records:
            rec = normalize_price_record(r)
            ad = rec.get("arrival_date")
            if ad:
                ad_str = str(ad)[:10]
                try:
                    rec_date = datetime.datetime.strptime(ad_str, "%Y-%m-%d").date()
                except ValueError:
                    rec_date = None
                if rec_date is not None:
                    seen_dates += 1
                    if rec_date >= since_date:
                        window_ok += 1
                        out.append(rec)
                else:
                    out.append(rec)
            else:
                out.append(rec)
            if len(out) >= max_records:
                break
        if offset + page_size >= total:
            break
        if window_ok == 0 and seen_dates >= page_size:
            logger.info("Variety-wise: date filter not honored (oldest-first) — stopping to avoid full 80M scan.")
            break
        offset += page_size
        time.sleep(0.3)
    logger.info("Variety-wise supplement: collected %d recent rows (since %s).", len(out), since)
    return out


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

    url = f"{BASE_URL}?{'&'.join(params)}"

    # Retry with exponential backoff
    max_retries = 3
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as f:
                data = json.loads(f.read())
            return data
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                OSError) as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt * 2  # 2s, 4s, 8s
                logger.warning(f"Attempt {attempt + 1} failed: {e}. Retrying in {wait}s...")
                time.sleep(wait)
            else:
                logger.error(f"All {max_retries} attempts failed for offset={offset}: {e}")
                raise


def fetch_all_prices(
    filters: Optional[dict] = None,
    max_records: Optional[int] = None,
    page_size: int = 1000,
    progress_callback=None,
) -> list[dict]:
    """
    Fetch ALL mandi price records via pagination.

    If DATA_GOV_IN_API_KEY is not set, returns [] immediately so the
    pipeline can still run RDD analysis on existing data.

    Args:
        filters: Server-side filters to narrow results
        max_records: Limit total records (None = all)
        page_size: Records per API call (max 1000)
        progress_callback: Optional fn(records_so_far, total)

    Returns:
        List of all record dicts
    """
    # Graceful skip if no API key — allows pipeline to run RDD on existing data
    try:
        _get_api_key()
    except RuntimeError:
        logger.info("DATA_GOV_IN_API_KEY not set — skipping price fetch. RDD analysis can still run on existing data.")
        return []

    all_records = []
    offset = 0
    total = None

    while True:
        data = fetch_page(offset=offset, limit=page_size, filters=filters)
        records = data.get("records", [])
        total = data.get("total", 0)

        all_records.extend(records)

        if progress_callback:
            progress_callback(len(all_records), total)

        # Check termination conditions
        if max_records and len(all_records) >= max_records:
            all_records = all_records[:max_records]
            break

        if offset + page_size >= total:
            break

        offset += page_size
        # Small delay to be polite to the API
        time.sleep(0.5)

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
