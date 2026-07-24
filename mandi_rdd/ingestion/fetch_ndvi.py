"""Rewrite fetch_ndvi.py with the correct, tested Statistical API format."""
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import ssl
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SSL_CTX = ssl.create_default_context()

SENTINEL_AUTH_URL = "https://services.sentinel-hub.com/oauth/token"
SENTINEL_STATS_URL = "https://services.sentinel-hub.com/api/v1/statistics"

COORDS_CACHE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "district_coords.json"
)

# Sentinel-2 L2A evalscript — NDVI (Red=Band4, NIR=Band8) with dataMask
NDVI_EVALSCRIPT = """
//VERSION=3
function setup() {
  return {
    input: ["B04", "B08", "dataMask"],
    output: [
      { id: "default", bands: 1, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1 }
    ]
  };
}
function evaluatePixel(sample) {
  let ndvi = (sample.B08 - sample.B04) / (sample.B08 + sample.B04);
  return {
    default: [ndvi],
    dataMask: [sample.dataMask]
  };
}
"""


# ── Auth ──

def _get_client_credentials() -> tuple[str, str]:
    client_id = os.environ.get("SENTINEL_CLIENT_ID")
    client_secret = os.environ.get("SENTINEL_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "SENTINEL_CLIENT_ID and SENTINEL_CLIENT_SECRET must be set. "
            "Get free credentials at https://www.sentinel-hub.com/pricing/"
        )
    return client_id, client_secret


def _get_access_token(client_id: str, client_secret: str) -> str:
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()
    req = urllib.request.Request(
        SENTINEL_AUTH_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as f:
            resp = json.loads(f.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"Sentinel Hub auth failed ({e.code}): {body[:300]}")
    token = resp.get("access_token")
    if not token:
        raise RuntimeError(f"Auth response missing access_token: {resp}")
    return token


# ── Geocoding (Nominatim with caching) ──

def _load_coords_cache() -> dict:
    if COORDS_CACHE_PATH.exists():
        with open(COORDS_CACHE_PATH) as f:
            return json.load(f)
    return {}


def _save_coords_cache(cache: dict):
    COORDS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(COORDS_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)


def geocode_district(district: str, state: str) -> Optional[tuple[float, float]]:
    cache = _load_coords_cache()
    key = f"{state}|{district}"
    if key in cache:
        return tuple(cache[key])

    query = f"{district}, {state}, India"
    url = (
        "https://nominatim.openstreetmap.org/search"
        f"?q={urllib.parse.quote(query)}&format=json&limit=1"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MandiIQ/1.0"})
        with urllib.request.urlopen(req, timeout=10, context=SSL_CTX) as f:
            results = json.loads(f.read())
    except Exception as e:
        logger.warning("Geocode HTTP error for %s: %s", key, e)
        results = []

    time.sleep(1.1)  # Nominatim rate limit

    if results:
        lat = float(results[0]["lat"])
        lng = float(results[0]["lon"])
        cache[key] = [lat, lng]
        _save_coords_cache(cache)
        return (lat, lng)

    logger.warning("No geocode result for %s — skipping", key)
    return None


# ── Sentinel Hub Statistical API ──

def query_ndvi_stats(
    token: str,
    lat: float,
    lng: float,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    bbox_deg: float = 0.1,
) -> Optional[dict]:
    today = date.today()
    if date_to is None:
        date_to = today.isoformat()
    if date_from is None:
        date_from = (today - timedelta(days=180)).isoformat()

    body = json.dumps({
        "input": {
            "bounds": {
                "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"},
                "bbox": [lng - bbox_deg, lat - bbox_deg,
                         lng + bbox_deg, lat + bbox_deg],
            },
            "data": [{
                "type": "sentinel-2-l2a",
                "dataFilter": {
                    "timeRange": {
                        "from": f"{date_from}T00:00:00Z",
                        "to": f"{date_to}T23:59:59Z",
                    },
                    "maxCloudCoverage": 50,
                },
            }],
        },
        "aggregation": {
            "timeRange": {
                "from": f"{date_from}T00:00:00Z",
                "to": f"{date_to}T23:59:59Z",
            },
            "aggregationInterval": {"of": "P1M"},
            "evalscript": NDVI_EVALSCRIPT,
            "width": 100,
            "height": 100,
        },
    })

    req = urllib.request.Request(
        SENTINEL_STATS_URL,
        data=body.encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45, context=SSL_CTX) as f:
            return json.loads(f.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()
        logger.warning("Sentinel API error %s: %s", e.code, body_text[:300])
        return None
    except Exception as e:
        logger.warning("Sentinel API request failed: %s", e)
        return None


def parse_stats_response(response: dict) -> list[dict]:
    """Extract (date, mean_ndvi) pairs from a Statistical API response.

    Returns a list of dicts with keys ``date`` and ``ndvi``.
    Only includes intervals where at least one valid pixel was found.
    """
    records: list[dict] = []
    for entry in response.get("data", []):
        interval = entry.get("interval", {})
        dt = interval.get("from", "")[:10]
        stats = (
            entry.get("outputs", {})
            .get("default", {})
            .get("bands", {})
            .get("B0", {})
            .get("stats", {})
        )
        mean_val = stats.get("mean")
        sample_count = stats.get("sampleCount", 0)
        no_data_count = stats.get("noDataCount", 0)
        valid_pixels = sample_count - no_data_count
        if mean_val not in (None, "NaN") and valid_pixels > 0:
            records.append({
                "date": dt,
                "ndvi": round(float(mean_val), 4),
                "valid_pixels": valid_pixels,
            })
    return records


# ── Main Pipeline ──

def fetch_and_store_all_ndvi() -> int:
    """Geocode districts, query Sentinel Hub NDVI, store in DuckDB.

    Returns number of NDVI records stored.
    """
    from mandi_rdd.storage.duckdb_store import get_connection, init_schema, upsert_ndvi

    # 1. Get district list
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT DISTINCT state, district FROM prices ORDER BY state, district"
        ).fetchall()
        conn.close()
    except Exception as e:
        logger.error("Cannot read district list: %s", e)
        return 0

    districts = [(r[0], r[1]) for r in rows]
    logger.info("Found %d districts in prices table", len(districts))
    if not districts:
        return 0

    # 2. Geocode
    coords: dict[str, tuple[float, float]] = {}
    cached = _load_coords_cache()
    to_geocode = []
    for state, district in districts:
        key = f"{state}|{district}"
        if key in cached:
            coords[key] = tuple(cached[key])
        else:
            to_geocode.append((state, district, key))

    if to_geocode:
        logger.info("Geocoding %d uncached districts…", len(to_geocode))
        for i, (state, district, key) in enumerate(to_geocode):
            c = geocode_district(district, state)
            if c:
                coords[key] = c
            if (i + 1) % 50 == 0:
                logger.info("  Geocoded %d / %d", len(coords), len(districts))

    logger.info("Mapped %d / %d districts", len(coords), len(districts))
    if not coords:
        logger.error("No geocoded districts — cannot fetch NDVI")
        return 0

    # 3. Auth
    client_id, client_secret = _get_client_credentials()
    token = _get_access_token(client_id, client_secret)
    logger.info("Sentinel Hub authenticated — token valid ~60 min")

    # 4. Query NDVI per district
    all_records: list[dict] = []
    keys = list(coords.keys())
    batch_size = 10  # Conservative for free tier

    for i in range(0, len(keys), batch_size):
        batch = keys[i: i + batch_size]
        for key in batch:
            state, district = key.split("|", 1)
            lat, lng = coords[key]
            resp = query_ndvi_stats(token, lat, lng)
            if resp:
                parsed = parse_stats_response(resp)
                for r in parsed:
                    all_records.append({
                        "state": state,
                        "district": district,
                        "date": r["date"],
                        "ndvi": r["ndvi"],
                        "anomaly": 0.0,
                    })
            time.sleep(0.3)

        logger.info("  Batch %d/%d — %d NDVI records",
                     i // batch_size + 1, (len(keys) - 1) // batch_size + 1,
                     len(all_records))

    logger.info("Total NDVI records fetched: %d", len(all_records))

    # 5. Store
    if all_records:
        conn = get_connection()
        init_schema(conn)
        stored = upsert_ndvi(conn, all_records)
        conn.close()
        logger.info("Stored %d NDVI records", stored)
        # Export JSON for git-tracked persistence
        _export_ndvi_json()
        return stored
    return 0



def _export_ndvi_json():
    """Export the ndvi table as a JSON file tracked in git.

    The DuckDB is gitignored, so this JSON copy is what the daily GitHub Action
    commits back to the repo. The satellite dashboard reads this file if DuckDB
    has no local data yet.
    """
    from mandi_rdd.storage.duckdb_store import get_connection
    import json
    try:
        conn = get_connection()
        df = conn.execute("""
            SELECT state, district, date, ndvi, anomaly
            FROM ndvi
            ORDER BY state, district, date
        """).fetchdf()
        conn.close()
        # Convert date column to ISO string
        if "date" in df.columns:
            df["date"] = df["date"].astype(str)
        records = df.to_dict(orient="records")
        export_path = Path(__file__).resolve().parent.parent / "data" / "ndvi_latest.json"
        export_path.parent.mkdir(parents=True, exist_ok=True)
        with open(export_path, "w") as f:
            json.dump({"last_updated": __import__("datetime").datetime.utcnow().isoformat() + "Z",
                       "n_records": len(records), "records": records}, f, indent=2)
        logger.info("Exported %d NDVI records to %s", len(records), export_path)
    except Exception as e:
        logger.warning("Failed to export NDVI JSON: %s", e)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # Load .env if available
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    count = fetch_and_store_all_ndvi()
    print(f"\nDone — stored {count} NDVI records")
