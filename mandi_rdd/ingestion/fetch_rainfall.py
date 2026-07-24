"""
import re
MandiRDD — rainfall departure data fetcher.

Searches data.gov.in for the sub-division-wise monthly rainfall
departure-from-normal resource. Falls back to a well-known CSV
file if the API resource isn't available.

The rainfall data is joined with mandi prices on
(district ~ sub_division, year, month) to create the running variable
for the RDD: monthly rainfall departure from normal (%).
"""

import os
import re
import csv
import io
import time
import urllib.parse
import logging
from typing import Optional

from mandi_rdd.ingestion.http_client import (
    safe_float,
    get_api_key,
    http_get,
    http_get_json,
    http_get_text,
    SSL_CTX,
)

logger = logging.getLogger(__name__)


# data.gov.in resource IDs to try (rainfall-related)
# These are common rainfall resources on the platform
RAINFALL_CANDIDATE_IDS = [
    # Try these known rainfall-related resource IDs
    "9b915b52-b840-4b4b-9f9f-8d6e7c0e1a2b",  # Sub-division rainfall (candidate)
    "a4b2e5f6-c7d8-9012-3456-7890abcdef12",  # Monthly rainfall data (candidate)
]

# Fallback: IMD's gridded rainfall data can be downloaded from:
# https://www.imdpune.gov.in/Clim_Pred_LRF_New/Grided_Data_Download.html
# But for automation, we use the data.gov.in API or a well-known CSV export.

# Well-known CSV source for Indian sub-division rainfall data:
# This is a maintained dataset of monthly rainfall departure by sub-division
FALLBACK_CSV_URL = "https://raw.githubusercontent.com/datameet/rainfall/master/data/rainfall_monthly_subdivisions.csv"


def search_rainfall_resource() -> Optional[str]:
    """
    Search data.gov.in catalog for the rainfall departure resource.
    Returns the resource ID if found, None otherwise.
    """
    logger.info("Searching for rainfall departure resource on data.gov.in...")

    # Try the catalog search API
    api_key = get_api_key("DATA_GOV_IN_API_KEY")
    search_urls = [
        f"https://api.data.gov.in/catalog?api-key={api_key}&format=json&limit=10&search=rainfall+departure+normal+monthly+sub-division",
        f"https://api.data.gov.in/catalog?api-key={api_key}&format=json&limit=10&search=sub-division+rainfall+departure",
        f"https://api.data.gov.in/catalog?api-key={api_key}&format=json&limit=10&search=imd+rainfall+monthly",
    ]

    for url in search_urls:
        try:
            data = http_get_json(url, timeout=15, max_retries=1)
            if "records" in data and len(data["records"]) > 0:
                for r in data["records"]:
                    rid = r.get("resource_id", r.get("id", ""))
                    title = r.get("title", r.get("name", ""))
                    logger.info(f"  Found: {title} (ID: {rid})")
                    return rid
        except Exception as e:
            logger.debug(f"  Search failed: {e}")
            continue

    return None


def try_rainfall_resource(resource_id: str) -> Optional[list[dict]]:
    """
    Try to pull data from a rainfall resource ID.
    Returns records if successful, None otherwise.
    """
    api_key = get_api_key("DATA_GOV_IN_API_KEY")
    url = f"https://api.data.gov.in/resource/{resource_id}?api-key={api_key}&format=json&limit=10"

    try:
        data = http_get_json(url, timeout=15, max_retries=1)
        records = data.get("records", [])
        if records:
            logger.info(f"  Resource {resource_id} returned {len(records)} records")
            logger.info(f"  Columns: {list(records[0].keys())}")
            return records
    except Exception as e:
        logger.warning(f"  Resource {resource_id} failed: {e}")

    return None


def fetch_rainfall_from_github() -> list[dict]:
    """
    Fetch rainfall data from Datameet's maintained CSV on GitHub.
    This is the most reliable source for sub-division-wise monthly rainfall.
    
    Schema: sub_division, year, month, rainfall, normal, departure_pct
    """
    logger.info("Fetching rainfall data from Datameet GitHub...")

    try:
        content = http_get_text(FALLBACK_CSV_URL, timeout=30, max_retries=2)

        reader = csv.DictReader(io.StringIO(content))
        records = []

        for row in reader:
            try:
                # Normalize column names (the dataset may have varying names)
                sub_div = row.get("sub_division") or row.get("Sub_Division") or row.get("subdivision") or row.get("SUBDIVISION") or ""
                year = int(row.get("year") or row.get("Year") or 0)
                month = int(row.get("month") or row.get("Month") or 0)

                # Rainfall amount and normal
                rainfall = safe_float(row.get("rainfall") or row.get("Rainfall") or row.get("RAINFALL"))
                normal = safe_float(row.get("normal") or row.get("Normal") or row.get("NORMAL"))

                # Departure percentage
                departure = safe_float(
                    row.get("departure_pct")
                    or row.get("departure")
                    or row.get("Departure")
                    or row.get("DEPARTURE")
                    or row.get("anomaly_pct")
                    or row.get("Anomaly")
                )

                # Compute departure if not provided but rainfall+normal are available
                if departure is None and rainfall is not None and normal and normal > 0:
                    departure = ((rainfall - normal) / normal) * 100

                if sub_div and year and month:
                    records.append({
                        "sub_division": sub_div.strip(),
                        "year": year,
                        "month": month,
                        "rainfall_mm": rainfall,
                        "normal_mm": normal,
                        "departure_pct": departure,
                    })
            except (ValueError, TypeError):
                continue

        logger.info(f"  Loaded {len(records)} rainfall records from GitHub")
        return records

    except Exception as e:
        logger.error(f"  Failed to fetch rainfall data: {e}")
        return []


def load_district_subdivision_map() -> dict:
    """
    Load a mapping of (state, district) -> sub_division for joining
    mandi prices (which have state+district) to rainfall (which has
    sub_division).
    
    Returns dict with (state, district) keys and sub_division values.
    """
    # Built-in mapping for major districts. In production, this should
    # be loaded from a lookup table or external dataset.
    mapping = {}

    # Common mappings for rain-sensitive commodity regions
    # Maharashtra
    maharashtra_subdivs = {
        "Ahmednagar": "Madhya Maharashtra",
        "Akola": "Vidarbha",
        "Amravati": "Vidarbha",
        "Aurangabad": "Marathwada",
        "Beed": "Marathwada",
        "Buldhana": "Vidarbha",
        "Chandrapur": "Vidarbha",
        "Dhule": "Madhya Maharashtra",
        "Gadchiroli": "Vidarbha",
        "Gondia": "Vidarbha",
        "Hingoli": "Marathwada",
        "Jalgaon": "Madhya Maharashtra",
        "Jalna": "Marathwada",
        "Kolhapur": "Madhya Maharashtra",
        "Latur": "Marathwada",
        "Mumbai": "Konkan & Goa",
        "Nagpur": "Vidarbha",
        "Nanded": "Marathwada",
        "Nandurbar": "Madhya Maharashtra",
        "Nashik": "Madhya Maharashtra",
        "Osmanabad": "Marathwada",
        "Palghar": "Konkan & Goa",
        "Parbhani": "Marathwada",
        "Pune": "Madhya Maharashtra",
        "Raigad": "Konkan & Goa",
        "Ratnagiri": "Konkan & Goa",
        "Sangli": "Madhya Maharashtra",
        "Satara": "Madhya Maharashtra",
        "Sindhudurg": "Konkan & Goa",
        "Solapur": "Marathwada",
        "Thane": "Konkan & Goa",
        "Wardha": "Vidarbha",
        "Washim": "Vidarbha",
        "Yavatmal": "Vidarbha",
    }
    for d, s in maharashtra_subdivs.items():
        mapping[("Maharashtra", d)] = s

    # Karnataka
    karnataka_subdivs = {
        "Bagalkote": "North Interior Karnataka",
        "Bagalkot": "North Interior Karnataka",
        "Bangalore": "South Interior Karnataka",
        "Bengaluru": "South Interior Karnataka",
        "Belagavi": "North Interior Karnataka",
        "Bellary": "North Interior Karnataka",
        "Bidar": "North Interior Karnataka",
        "Chamarajanagar": "South Interior Karnataka",
        "Chikkaballapura": "South Interior Karnataka",
        "Chikkaballapur": "South Interior Karnataka",
        "Chikkamagaluru": "South Interior Karnataka",
        "Chitradurga": "South Interior Karnataka",
        "Dakshina Kannada": "Coastal Karnataka",
        "Davanagere": "South Interior Karnataka",
        "Dharwad": "North Interior Karnataka",
        "Gadag": "North Interior Karnataka",
        "Gulbarga": "North Interior Karnataka",
        "Kalaburagi": "North Interior Karnataka",
        "Hassan": "South Interior Karnataka",
        "Haveri": "North Interior Karnataka",
        "Hubli": "North Interior Karnataka",
        "Kodagu": "South Interior Karnataka",
        "Kolar": "South Interior Karnataka",
        "Koppal": "North Interior Karnataka",
        "Mandya": "South Interior Karnataka",
        "Mysore": "South Interior Karnataka",
        "Mysuru": "South Interior Karnataka",
        "Raichur": "North Interior Karnataka",
        "Ramanagara": "South Interior Karnataka",
        "Shimoga": "South Interior Karnataka",
        "Shivamogga": "South Interior Karnataka",
        "Tumkur": "South Interior Karnataka",
        "Tumakuru": "South Interior Karnataka",
        "Udupi": "Coastal Karnataka",
        "Uttara Kannada": "Coastal Karnataka",
        "Vijayapura": "North Interior Karnataka",
        "Vijayanagara": "South Interior Karnataka",
        "Yadgir": "North Interior Karnataka",
    }
    for d, s in karnataka_subdivs.items():
        mapping[("Karnataka", d)] = s

    # Gujarat
    gujarat_subdivs = {
        "Ahmedabad": "Gujarat",
        "Amreli": "Gujarat",
        "Anand": "Gujarat",
        "Banaskantha": "Gujarat",
        "Bharuch": "Gujarat",
        "Bhavnagar": "Gujarat",
        "Dahod": "Gujarat",
        "Gandhinagar": "Gujarat",
        "Jamnagar": "Gujarat",
        "Junagadh": "Gujarat",
        "Kutch": "Gujarat",
        "Kheda": "Gujarat",
        "Mehsana": "Gujarat",
        "Narmada": "Gujarat",
        "Navsari": "Gujarat",
        "Panchmahal": "Gujarat",
        "Patan": "Gujarat",
        "Porbandar": "Gujarat",
        "Rajkot": "Gujarat",
        "Sabarkantha": "Gujarat",
        "Surat": "Gujarat",
        "Surendranagar": "Gujarat",
        "Tapi": "Gujarat",
        "Vadodara": "Gujarat",
        "Valsad": "Gujarat",
    }
    for d, s in gujarat_subdivs.items():
        mapping[("Gujarat", d)] = s

    # Andhra Pradesh / Telangana
    ap_subdivs = {
        "Anantapur": "Rayalaseema",
        "Chittoor": "Rayalaseema",
        "East Godavari": "Coastal Andhra Pradesh",
        "Guntur": "Coastal Andhra Pradesh",
        "Kadapa": "Rayalaseema",
        "Krishna": "Coastal Andhra Pradesh",
        "Kurnool": "Rayalaseema",
        "Nellore": "Coastal Andhra Pradesh",
        "Prakasam": "Coastal Andhra Pradesh",
        "Srikakulam": "Coastal Andhra Pradesh",
        "Visakhapatnam": "Coastal Andhra Pradesh",
        "Vizianagaram": "Coastal Andhra Pradesh",
        "West Godavari": "Coastal Andhra Pradesh",
    }
    for d, s in ap_subdivs.items():
        mapping[("Andhra Pradesh", d)] = s
        mapping[("Telangana", d)] = "Telangana"

    # Rajasthan
    rajasthan_subdivs = {
        "Ajmer": "West Rajasthan",
        "Alwar": "East Rajasthan",
        "Banswara": "East Rajasthan",
        "Baran": "East Rajasthan",
        "Barmer": "West Rajasthan",
        "Bharatpur": "East Rajasthan",
        "Bhilwara": "East Rajasthan",
        "Bikaner": "West Rajasthan",
        "Bundi": "East Rajasthan",
        "Chittorgarh": "East Rajasthan",
        "Churu": "West Rajasthan",
        "Dausa": "East Rajasthan",
        "Dholpur": "East Rajasthan",
        "Dungarpur": "East Rajasthan",
        "Ganganagar": "West Rajasthan",
        "Hanumangarh": "West Rajasthan",
        "Jaipur": "East Rajasthan",
        "Jaisalmer": "West Rajasthan",
        "Jalore": "West Rajasthan",
        "Jhalawar": "East Rajasthan",
        "Jhunjhunu": "East Rajasthan",
        "Jodhpur": "West Rajasthan",
        "Karauli": "East Rajasthan",
        "Kota": "East Rajasthan",
        "Nagaur": "West Rajasthan",
        "Pali": "West Rajasthan",
        "Pratapgarh": "East Rajasthan",
        "Rajsamand": "East Rajasthan",
        "Sawai Madhopur": "East Rajasthan",
        "Sikar": "East Rajasthan",
        "Sirohi": "West Rajasthan",
        "Tonk": "East Rajasthan",
        "Udaipur": "East Rajasthan",
    }
    for d, s in rajasthan_subdivs.items():
        mapping[("Rajasthan", d)] = s

    # Madhya Pradesh
    mp_subdivs = {
        "Bhopal": "West Madhya Pradesh",
        "Gwalior": "East Madhya Pradesh",
        "Indore": "West Madhya Pradesh",
        "Jabalpur": "East Madhya Pradesh",
        "Ujjain": "West Madhya Pradesh",
    }
    for d, s in mp_subdivs.items():
        mapping[("Madhya Pradesh", d)] = s

    # Uttar Pradesh
    up_subdivs = {
        "Agra": "West Uttar Pradesh",
        "Aligarh": "West Uttar Pradesh",
        "Allahabad": "East Uttar Pradesh",
        "Bareilly": "West Uttar Pradesh",
        "Gorakhpur": "East Uttar Pradesh",
        "Kanpur": "West Uttar Pradesh",
        "Lucknow": "East Uttar Pradesh",
        "Meerut": "West Uttar Pradesh",
        "Moradabad": "West Uttar Pradesh",
        "Saharanpur": "West Uttar Pradesh",
        "Varanasi": "East Uttar Pradesh",
    }
    for d, s in up_subdivs.items():
        mapping[("Uttar Pradesh", d)] = s

    # Bihar
    bihar_subdivs = {
        "Patna": "Bihar",
        "Gaya": "Bihar",
        "Muzaffarpur": "Bihar",
    }
    for d, s in bihar_subdivs.items():
        mapping[("Bihar", d)] = s

    # Tamil Nadu
    tn_subdivs = {
        "Chennai": "Tamil Nadu & Puducherry",
        "Coimbatore": "Tamil Nadu & Puducherry",
        "Cuddalore": "Tamil Nadu & Puducherry",
        "Dharmapuri": "Tamil Nadu & Puducherry",
        "Erode": "Tamil Nadu & Puducherry",
        "Kanchipuram": "Tamil Nadu & Puducherry",
        "Madurai": "Tamil Nadu & Puducherry",
        "Nagapattinam": "Tamil Nadu & Puducherry",
        "Namakkal": "Tamil Nadu & Puducherry",
        "Perambalur": "Tamil Nadu & Puducherry",
        "Pudukkottai": "Tamil Nadu & Puducherry",
        "Ramanathapuram": "Tamil Nadu & Puducherry",
        "Salem": "Tamil Nadu & Puducherry",
        "Sivaganga": "Tamil Nadu & Puducherry",
        "Thanjavur": "Tamil Nadu & Puducherry",
        "Theni": "Tamil Nadu & Puducherry",
        "Thoothukudi": "Tamil Nadu & Puducherry",
        "Tiruchirappalli": "Tamil Nadu & Puducherry",
        "Tirunelveli": "Tamil Nadu & Puducherry",
        "Tiruvallur": "Tamil Nadu & Puducherry",
        "Tiruvannamalai": "Tamil Nadu & Puducherry",
        "Vellore": "Tamil Nadu & Puducherry",
        "Villupuram": "Tamil Nadu & Puducherry",
        "Virudhunagar": "Tamil Nadu & Puducherry",
    }
    for d, s in tn_subdivs.items():
        mapping[("Tamil Nadu", d)] = s

    return mapping



def fetch_district_daily_rainfall(resource_id: str, districts: list[str],
                                  max_rows_per_district: int = 4000) -> list[dict]:
    """
    Fetch daily district-wise rainfall from a data.gov.in resource.

    The resource (e.g. 6c05cd1b-... "Daily District-wise Rainfall") exposes
    columns: State, District, Date, Year, Month, Avg_rainfall, Agency_name.
    We pull per-district daily rows (filtered), bounded so CI stays fast.
    Returns a flat list of raw records.
    """
    api_key = get_api_key("DATA_GOV_IN_API_KEY")
    all_recs: list[dict] = []
    for dist in districts:
        offset = 0
        fetched = 0
        while fetched < max_rows_per_district:
            limit = min(1000, max_rows_per_district - fetched)
            url = (
                f"https://api.data.gov.in/resource/{resource_id}"
                f"?api-key={api_key}&format=json&limit={limit}&offset={offset}"
                f"&filters[District]={urllib.parse.quote(str(dist))}"
            )
            try:
                data = http_get_json(url, timeout=25, max_retries=1)
            except Exception as e:
                logger.warning(f"  Rainfall fetch failed for {dist}: {e}")
                break
            recs = data.get("records", [])
            if not recs:
                break
            all_recs.extend(recs)
            fetched += len(recs)
            # Stop if the API returned fewer than the page size (last page).
            if len(recs) < limit:
                break
            offset += limit
            time.sleep(0.2)  # be gentle with the free tier
        logger.info(f"  Fetched {fetched} daily rows for {dist}")
    return all_recs


def aggregate_daily_to_monthly(records: list[dict]) -> list[dict]:
    """
    Aggregate raw daily district rainfall into monthly sub-division records
    matching the `rainfall` table schema:
        sub_division, year, month, rainfall_mm, normal_mm, departure_pct

    departure_pct is computed against each district's own climatology
    (mean monthly rainfall across all observed years) — a robust,
    data-driven "normal" without needing an external normals table.
    District -> sub_division uses the bundled mandi mapping (reversed).
    """
    from mandi_rdd.ingestion.fetch_rainfall import load_district_subdivision_map  # noqa: F811
    dmap = load_district_subdivision_map()
    # Reverse: District (lower) -> sub_division
    dist_to_subdiv = {}
    for (state, district), subdiv in dmap.items():
        dist_to_subdiv.setdefault(district.lower(), subdiv)

    # group: (district, year, month) -> list of Avg_rainfall
    groups: dict = {}
    for r in records:
        dist = (r.get("District") or "").strip()
        yr = r.get("Year")
        mo = r.get("Month")
        val = safe_float(r.get("Avg_rainfall"))
        if not dist or val is None or yr is None or mo is None:
            continue
        try:
            yr = int(yr); mo = int(mo)
        except (ValueError, TypeError):
            continue
        groups.setdefault((dist.lower(), yr, mo), []).append(val)

    # Compute monthly averages
    monthly = {}  # (district, yr, mo) -> avg
    dist_month_vals: dict = {}  # district -> {month: [avgs across years]}
    for (dist, yr, mo), vals in groups.items():
        avg = sum(vals) / len(vals)
        monthly[(dist, yr, mo)] = avg
        dist_month_vals.setdefault(dist, {}).setdefault(mo, []).append(avg)

    # Climatology per district-month
    normal = {}
    for dist, monthmap in dist_month_vals.items():
        for mo, avgs in monthmap.items():
            normal[(dist, mo)] = sum(avgs) / len(avgs) if avgs else 0.0

    out = []
    for (dist, yr, mo), avg in monthly.items():
        subdiv = dist_to_subdiv.get(dist)
        if not subdiv:
            continue  # no RDD mapping -> skip (RDD joins on sub_division)
        nrm = normal.get((dist, mo), 0.0)
        departure = ((avg - nrm) / nrm * 100.0) if nrm > 0 else 0.0
        out.append({
            "sub_division": subdiv,
            "year": yr,
            "month": mo,
            "rainfall_mm": round(avg, 2),
            "normal_mm": round(nrm, 2),
            "departure_pct": round(departure, 2),
        })
    return out

def fetch_and_store_all_rainfall() -> list[dict]:
    """
    Fetch rainfall data from the most reliable available source.
    Order: env RAINFALL_RESOURCE_ID -> data.gov.in search -> candidate IDs -> GitHub mirror.

    Never raises: if every source fails, returns [] so the nightly ingestion can
    still commit prices + precomputed RDD results.
    """
    # Step 0: Explicit resource ID from environment. Set RAINFALL_RESOURCE_ID to a
    # valid data.gov.in rainfall resource (e.g. daily district-wise rainfall) to
    # enable live causal RDD analysis.
    # Support one or more comma/semicolon-separated rainfall resource IDs.
    explicit_raw = os.environ.get("RAINFALL_RESOURCE_ID", "")
    explicit_ids = [s.strip() for s in re.split(r"[,;]", explicit_raw) if s.strip()]
    if explicit_ids:
        try:
            from mandi_rdd.storage.duckdb_store import get_connection
            from mandi_rdd.ingestion.fetch_rainfall import load_district_subdivision_map  # noqa: F811
            dmap = load_district_subdivision_map()
            try:
                conn = get_connection()
                price_districts = [r[0] for r in conn.execute(
                    "SELECT DISTINCT district FROM prices"
                ).fetchall()]
                conn.close()
            except Exception:
                price_districts = []
            # Pick ONE representative price district per subdivision so we cover
            # every subdivision the RDD needs without fetching all ~500 districts.
            needed_subdivs = set()
            for pd_ in price_districts:
                for (state, district), subdiv in dmap.items():
                    if district.lower() == pd_.lower():
                        needed_subdivs.add(subdiv)
                        break
            rep_by_subdiv = {}
            for pd_ in price_districts:
                for (state, district), subdiv in dmap.items():
                    if district.lower() == pd_.lower() and subdiv not in rep_by_subdiv:
                        rep_by_subdiv[subdiv] = pd_
            # Fallback: if no mapping matched, just use all price districts (capped).
            if rep_by_subdiv:
                districts = list(rep_by_subdiv.values())
            else:
                districts = price_districts[:60]
            for explicit in explicit_ids:
                logger.info(f"Using RAINFALL_RESOURCE_ID: {explicit} -> "
                            f"{len(needed_subdivs)} subdivisions, {len(districts)} representative districts")
                try:
                    raw = fetch_district_daily_rainfall(explicit, districts)
                except Exception as e2:
                    logger.warning(f"RAINFALL_RESOURCE_ID {explicit} fetch failed: {e2}")
                    raw = None
                if raw:
                    monthly = aggregate_daily_to_monthly(raw)
                    if monthly:
                        logger.info(f"Aggregated to {len(monthly)} monthly rainfall rows from {explicit}")
                        return monthly
        except Exception as e:
            logger.warning(f"RAINFALL_RESOURCE_ID ingestion failed: {e}")

    # Step 1: Try data.gov.in rainfall resources
    try:
        resource_id = search_rainfall_resource()
        if resource_id:
            records = try_rainfall_resource(resource_id)
            if records and len(records) > 50:
                logger.info(f"Using data.gov.in rainfall resource: {resource_id}")
                return records
    except Exception as e:
        logger.warning(f"data.gov.in rainfall search failed: {e}")

    # Step 2: Try candidate IDs
    for rid in RAINFALL_CANDIDATE_IDS:
        try:
            records = try_rainfall_resource(rid)
            if records and len(records) > 50:
                logger.info(f"Using rainfall resource: {rid}")
                return records
        except Exception:
            continue

    # Step 3: Fall back to GitHub Datameet CSV (mirror)
    try:
        logger.info("Falling back to Datameet GitHub rainfall dataset...")
        records = fetch_rainfall_from_github()
        if records:
            return records
    except Exception as e:
        logger.warning(f"Datameet rainfall fetch failed: {e}")

    logger.warning(
        "No rainfall data source available. RDD causal analysis (rainfall "
        "controls) will be skipped until RAINFALL_RESOURCE_ID is configured."
    )
    return []



def fetch_all_india_monsoon(resource_id: str, api_key: str | None = None) -> list[dict]:
    """
    Fetch the all-India monsoon rainfall series (1901-2019) from data.gov.in
    resource af34a228 (Rainfall in all India and its departure from normal
    during Monsoon (June-Sept) 1901-2019).

    Returns list of {"year", "jun", "jul", "aug", "sep", "jun_sep"} dicts.
    Never raises: returns [] on any failure so callers can degrade gracefully.
    """
    if not resource_id:
        return []
    key = api_key or os.environ.get("ALL_INDIA_RAINFALL_API_KEY")
    if not key:
        logger.warning("ALL_INDIA_RAINFALL_API_KEY not set; skipping all-India monsoon fetch.")
        return []
    url = (
        f"https://api.data.gov.in/resource/{resource_id}"
        f"?api-key={key}&format=json&limit=500"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=25, context=SSL_CTX) as f:
            data = json.loads(f.read())
        recs = data.get("records", [])
        out = []
        for r in recs:
            try:
                out.append({
                    "year": int(r.get("year", 0)),
                    "jun": safe_float(r.get("jun")),
                    "jul": safe_float(r.get("jul")),
                    "aug": safe_float(r.get("aug")),
                    "sep": safe_float(r.get("sep")),
                    "jun_sep": safe_float(r.get("jun_sep")),
                })
            except (ValueError, TypeError):
                continue
        out.sort(key=lambda x: x["year"])
        logger.info(f"Fetched {len(out)} all-India monsoon rows (1901-2019)")
        return out
    except Exception as e:
        logger.warning(f"All-India monsoon fetch failed: {e}")
        return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    records = fetch_and_store_all_rainfall()
    print(f"Total rainfall records: {len(records)}")

    if records:
        print(f"Sample columns: {list(records[0].keys())}")
        print(f"Sample: {records[0]}")
        print(f"Last: {records[-1]}")

        # Show departure range
        deps = [r.get("departure_pct") for r in records if r.get("departure_pct") is not None]
        if deps:
            print(f"Departure range: {min(deps):.1f}% to {max(deps):.1f}%")
            print(f"Below -19% (deficient): {sum(1 for d in deps if d < -19)} / {len(deps)}")

    # Test district mapping
    mapping = load_district_subdivision_map()
    print(f"\nDistrict-subdivision mappings: {len(mapping)}")
    sample = list(mapping.items())[:3]
    for (s, d), sub in sample:
        print(f"  {s} / {d} → {sub}")
