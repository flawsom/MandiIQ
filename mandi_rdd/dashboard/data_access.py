"""MandiIQ Dashboard - Data access layer with stale-data fallback warnings."""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_FALLBACK_COUNT: int = 0


def _get_api_base() -> str:
    return os.environ.get("MANDIQ_API_URL") or os.environ.get("MANDIIQ_API_URL") or "https://p01--mandiiq--x4n8x4gkmzht.code.run"


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


def get_prices_frame(commodity=None, state=None, district=None, limit=5000):
    """Prices as a DataFrame: the API first, the local warehouse as fallback.

    Pages used to query DuckDB directly for this. Streamlit Community Cloud
    serves the repository from an immutable layer and the DuckDB file is
    gitignored, so that query returns nothing there even though the API holds
    the rows - and the page then told the visitor "no price records" about data
    that plainly exists. The API returns at most 5000 rows (newest first), which
    is a full recent window for a trend or a district distribution.
    """
    import pandas as pd

    rows = get_prices(state=state, district=district, commodity=commodity, limit=limit)
    df = pd.DataFrame(rows or [])
    if df.empty:
        return df
    if "arrival_date" in df.columns:
        df["arrival_date"] = pd.to_datetime(df["arrival_date"], errors="coerce")
    for col in ("modal_price", "min_price", "max_price"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def get_rainfall_frame(sub_division=None, limit=5000):
    """Rainfall departures as a DataFrame: the API first, local warehouse second.

    Same reasoning as get_prices_frame - the hosted dashboard has no warehouse,
    so a rainfall page that queries DuckDB directly has nothing to draw. The
    API's /rainfall endpoint carries the series.
    """
    import pandas as pd

    api_base = _get_api_base()
    params = {"limit": limit}
    if sub_division:
        params["sub_division"] = sub_division
    rows = []
    try:
        import requests
        resp = requests.get(f"{api_base}/rainfall", params=params, timeout=8)
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, list):
            rows = payload
    except Exception as e:
        _warn_stale_fallback("/rainfall", str(e))

    if not rows:
        try:
            from mandi_rdd.storage.duckdb_store import get_connection
            conn = get_connection(read_only=True)
            df = conn.execute(
                """
                SELECT sub_division, year, month, rainfall_mm, normal_mm, departure_pct
                FROM rainfall
                WHERE departure_pct BETWEEN -100 AND 200
                LIMIT ?
                """,
                [limit],
            ).fetchdf()
            conn.close()
            rows = df.to_dict("records") if hasattr(df, "to_dict") else []
        except Exception:
            rows = []

    df = pd.DataFrame(rows or [])
    if df.empty:
        return df
    for col in ("rainfall_mm", "normal_mm", "departure_pct"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def get_commodities(limit: int = 300) -> list:
    """Commodity names that have data: the API first, local tables as fallback.

    A commodity picker built only from the local warehouse is empty on the
    hosted dashboard, which is how the Forecast page came up with nothing to
    select and nothing to draw.
    """
    names = []
    try:
        for rec in get_freshness() or []:
            name = (rec or {}).get("commodity")
            if name:
                names.append(str(name).title())
    except Exception:
        names = []
    if not names:
        try:
            from mandi_rdd.storage.duckdb_store import get_curated_commodities
            names = [str(r).title() for r in (get_curated_commodities() or [])]
        except Exception:
            names = []
    return sorted(set(names))[:limit]


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


def get_health() -> dict:
    """Fetch /health - live counts, newest data date and last pipeline run.

    Streamlit Cloud serves the repository from an immutable layer, so the local
    last_ingest_status.json can be stale; the API is the source of truth.
    """
    import requests
    api_base = _get_api_base()
    try:
        resp = requests.get(f"{api_base}/health", timeout=8)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        _warn_stale_fallback("/health", str(e))
        return {}


def get_data_quality() -> dict:
    """Fetch the live date-integrity report, falling back to local DuckDB."""
    import requests
    api_base = _get_api_base()
    try:
        resp = requests.get(f"{api_base}/data-quality", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        _warn_stale_fallback("/data-quality", str(e))
        try:
            from mandi_rdd.core.dates import date_quality
            from mandi_rdd.storage.duckdb_store import get_connection
            conn = get_connection(read_only=True)
            report = date_quality(conn)
            conn.close()
            return report
        except Exception as fallback_error:
            return {"error": f"Data quality unavailable: {e} ({fallback_error})"}


def get_analytics(commodity: str) -> dict:
    """Fetch the composite analytics deep-dive, falling back to local DuckDB."""
    import requests
    api_base = _get_api_base()
    try:
        resp = requests.get(f"{api_base}/analytics/{commodity}", timeout=60)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        _warn_stale_fallback(f"/analytics/{commodity}", str(e))
        try:
            from mandi_rdd.storage.duckdb_store import get_connection
            from mandi_rdd.analysis.analytics import commodity_analytics
            conn = get_connection(read_only=True)
            report = commodity_analytics(conn, commodity)
            conn.close()
            return report
        except Exception as fallback_error:
            return {"error": f"Analytics unavailable: {e} ({fallback_error})"}


def get_recommendation(commodity: str, district: Optional[str] = None) -> dict:
    """Fetch a procurement recommendation from the API."""
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
