"""MandiIQ Dashboard - Data access layer with stale-data fallback warnings."""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_FALLBACK_COUNT: int = 0


def _get_api_base() -> str:
    return os.environ.get("MANDIQ_API_URL", "https://mandiiq-api.onrender.com")


def _warn_stale_fallback(endpoint: str, detail: str = ""):
    global _FALLBACK_COUNT
    _FALLBACK_COUNT += 1
    api_base = _get_api_base()
    if _FALLBACK_COUNT <= 3:
        logger.warning(
            "Stale-data fallback #%d for %s - API %s unreachable%s",
            _FALLBACK_COUNT, endpoint, api_base,
            f" ({detail})" if detail else "",
        )


def get_fallback_count() -> int:
    return _FALLBACK_COUNT


def get_prices(state=None, district=None, commodity=None, limit=100):
    import requests
    api_base = _get_api_base()
    params = {}
    if state:
        params["state"] = state
    if district:
        params["district"] = district
    if commodity:
        params["commodity"] = commodity
    params["limit"] = str(limit)
    try:
        resp = requests.get(f"{api_base}/prices", params=params, timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        _warn_stale_fallback("/prices", str(e))
        from mandi_rdd.storage.duckdb_store import get_connection, get_prices as _get_prices_db
        conn = get_connection()
        df = _get_prices_db(conn, state=state, district=district,
                           commodity=commodity, limit=limit)
        conn.close()
        return df.to_dict("records") if hasattr(df, "to_dict") else []


def get_rdd_result(commodity: str) -> dict:
    import requests
    api_base = _get_api_base()
    try:
        resp = requests.get(f"{api_base}/rdd-result/{commodity}", timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        _warn_stale_fallback(f"/rdd-result/{commodity}", str(e))
        from mandi_rdd.storage.duckdb_store import get_connection, get_latest_rdd
        conn = get_connection()
        result = get_latest_rdd(conn, commodity)
        conn.close()
        if result and result.get("effect") is not None:
            return result
        return {"error": f"No cached RDD result for {commodity}"}


def get_forecast(commodity: str) -> dict:
    import requests
    api_base = _get_api_base()
    try:
        resp = requests.get(f"{api_base}/forecast/{commodity}", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        _warn_stale_fallback(f"/forecast/{commodity}", str(e))
        return {"error": f"Forecast unavailable: {e}"}


def get_risk_score(commodity: str, district: Optional[str] = None) -> dict:
    import requests
    api_base = _get_api_base()
    params = {}
    if district:
        params["district"] = district
    try:
        resp = requests.get(f"{api_base}/risk-score/{commodity}",
                           params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        _warn_stale_fallback(f"/risk-score/{commodity}", str(e))
        return {"error": f"Risk score unavailable: {e}"}


def get_freshness(commodity: Optional[str] = None) -> list:
    """Fetch per-commodity data freshness from the API.

    Calls GET /freshness on the API server. Returns a list of dicts
    with keys: commodity, latest_date, earliest_date, row_count,
    n_districts, n_states, source_type, source_name, updated_at.

    Falls back to querying DuckDB directly if the API is unreachable.
    """
    import requests
    api_base = _get_api_base()
    params = {}
    if commodity:
        params["commodity"] = commodity
    try:
        resp = requests.get(f"{api_base}/freshness", params=params, timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        _warn_stale_fallback("/freshness", str(e))
        try:
            from mandi_rdd.storage.duckdb_store import get_connection, get_freshness as _get_freshness_db
            conn = get_connection()
            records = _get_freshness_db(conn, commodity=commodity)
            conn.close()
            if records:
                return records
        except Exception:
            pass
        # Fallback: build freshness from prices table
        try:
            from mandi_rdd.storage.duckdb_store import get_connection
            conn = get_connection()
            rows = conn.execute("""
                SELECT
                    commodity,
                    MAX(arrival_date) AS latest_date,
                    MIN(arrival_date) AS earliest_date,
                    COUNT(*) AS row_count,
                    COUNT(DISTINCT district) AS n_districts,
                    COUNT(DISTINCT state) AS n_states
                FROM prices
                GROUP BY commodity
                ORDER BY latest_date DESC
                LIMIT 200
            """).fetchall()
            conn.close()
            records = []
            cols = ["commodity", "latest_date", "earliest_date", "row_count", "n_districts", "n_states"]
            for r in rows:
                rec = dict(zip(cols, r))
                rec["source_type"] = "prices_table"
                rec["source_name"] = ""
                rec["updated_at"] = None
                records.append(rec)
            return records
        except Exception:
            return []
    import requests
    api_base = _get_api_base()
    params = {}
    if district:
        params["district"] = district
    try:
        resp = requests.get(f"{api_base}/recommendation/{commodity}",
                           params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        _warn_stale_fallback(f"/recommendation/{commodity}", str(e))
        return {"error": f"Recommendation unavailable: {e}"}
