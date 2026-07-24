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


def get_recommendation(commodity: str, district: Optional[str] = None) -> dict:
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
