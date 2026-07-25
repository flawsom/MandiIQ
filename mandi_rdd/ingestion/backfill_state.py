"""Backfill empty state names in the prices table."""
from __future__ import annotations

import argparse
import json
import logging
import os
import ssl
import urllib.request

log = logging.getLogger("mandi_rdd.backfill_state")

MANUAL_OVERRIDES: dict[str, str] = {
    # Coords entries with empty state (35 districts)
    "Ambala": "Haryana",
    "Amritsar": "Punjab",
    "Anantnag": "Jammu & Kashmir",
    "Badgam": "Jammu & Kashmir",
    "Baramula": "Jammu & Kashmir",
    "Barnala": "Punjab",
    "Bathinda": "Punjab",
    "Bhiwani": "Haryana",
    "Bilaspur": "Himachal Pradesh",
    "Chamba": "Himachal Pradesh",
    "Champawat": "Uttarakhand",
    "Chandigarh": "Chandigarh",
    "Dehradun": "Uttarakhand",
    "Faridabad": "Haryana",
    "Faridkot": "Punjab",
    "Fatehabad": "Haryana",
    "Fatehgarh Sahib": "Punjab",
    "Firozpur": "Punjab",
    "Ganderbal": "Jammu & Kashmir",
    "Garhwal": "Uttarakhand",
    "Gurdaspur": "Punjab",
    "Gurgaon": "Haryana",
    "Hamirpur": "Himachal Pradesh",
    "Hardwar": "Uttarakhand",
    "Hisar": "Haryana",
    "Hoshiarpur": "Punjab",
    "Jalandhar": "Punjab",
    "Jammu": "Jammu & Kashmir",
    "Jhajjar": "Haryana",
    "Jind": "Haryana",
    "Kaithal": "Haryana",
    "Kangra": "Himachal Pradesh",
    "Kapurthala": "Punjab",
    "Karnal": "Haryana",
    "Kathua": "Jammu & Kashmir",
    # Alternate / old district names
    "Allahabad": "Uttar Pradesh",
    "Ambedkar Nagar": "Uttar Pradesh",
    "Banswara": "Rajasthan",
    "Bara Banki": "Uttar Pradesh",
    "Budaun": "Uttar Pradesh",
    "Bulandshahr": "Uttar Pradesh",
    "Chitrakoot": "Uttar Pradesh",
    "Chittaurgarh": "Rajasthan",
    "Dhaulpur": "Rajasthan",
    "Faizabad": "Uttar Pradesh",
    "Farrukhabad": "Uttar Pradesh",
    "Gautam Buddha Nagar": "Uttar Pradesh",
    "Jhunjhunun": "Rajasthan",
    "Jyotiba Phule Nagar": "Uttar Pradesh",
    "Kannauj": "Uttar Pradesh",
    "Kanpur Nagar": "Uttar Pradesh",
    "Kheri": "Uttar Pradesh",
    "Mahamaya Nagar": "Uttar Pradesh",
    "Moradabad": "Uttar Pradesh",
    "North": "NCT of Delhi",
    "North East": "NCT of Delhi",
    "Pilibhit": "Uttar Pradesh",
    "Rae Bareli": "Uttar Pradesh",
    "Sawai Madhopur": "Rajasthan",
    "Shahid Bhagat Singh Nagar": "Punjab",
    "Shupiyan": "Jammu & Kashmir",
    "South West": "NCT of Delhi",
    "Sultanpur": "Uttar Pradesh",
    "West": "NCT of Delhi",
    # Delhi districts
    "Central Delhi": "NCT of Delhi",
    "East Delhi": "NCT of Delhi",
    "New Delhi": "NCT of Delhi",
    "North Delhi": "NCT of Delhi",
    "North West Delhi": "NCT of Delhi",
    "Shahdara": "NCT of Delhi",
    "South Delhi": "NCT of Delhi",
    "South East Delhi": "NCT of Delhi",
    "South West Delhi": "NCT of Delhi",
    "West Delhi": "NCT of Delhi",
}


def _load_coord_mapping(path: str = "data/district_coords.json") -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not os.path.isfile(path):
        log.warning("Coords file not found at %s", path)
        return mapping
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for key in data:
        parts = key.split("|", 1)
        if len(parts) == 2:
            state, district = parts[0].strip(), parts[1].strip()
            if state:  # skip entries with empty state
                mapping[district.lower()] = state
    log.info("Loaded %d mappings from coords file", len(mapping))
    return mapping


def _fetch_ashoka_mapping() -> dict[str, str]:
    mapping: dict[str, str] = {}
    BASE = "https://agmarknet.ceda.ashoka.edu.in"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

    def _get(path: str):
        req = urllib.request.Request(BASE + path, headers=headers)
        with urllib.request.urlopen(req, timeout=20, context=ctx) as f:
            return json.loads(f.read()).get("data", [])

    try:
        states = _get("/api/states")
    except Exception as e:
        log.warning("Could not fetch Ashoka states: %s", e)
        return mapping

    for s in states:
        sid = s["census_state_id"]
        sname = s.get("census_state_name", "")
        try:
            dists = _get(f"/api/districts?state_id={sid}")
        except Exception:
            continue
        for d in dists:
            dname = d.get("census_district_name", "")
            if dname:
                mapping[dname.lower()] = sname

    log.info("Fetched %d mappings from Ashoka API", len(mapping))
    return mapping


def build_lookup(use_ashoka: bool = False) -> dict[str, str]:
    lookup: dict[str, str] = {}
    lookup.update(_load_coord_mapping())
    if use_ashoka:
        lookup.update(_fetch_ashoka_mapping())
    for district, state in MANUAL_OVERRIDES.items():
        lookup[district.lower()] = state
    log.info("Unified lookup has %d entries", len(lookup))
    return lookup


def backfill(lookup: dict[str, str], dry_run: bool = False) -> int:
    from mandi_rdd.storage.duckdb_store import get_connection

    conn = get_connection(read_only=False)
    try:
        rows = conn.execute(
            "SELECT DISTINCT district FROM prices "
            "WHERE (state IS NULL OR state = '') AND district != '' "
            "ORDER BY district"
        ).fetchall()
        total_districts = len(rows)
        matched = 0
        unmatched: list[str] = []

        for (district,) in rows:
            state = lookup.get(district.lower().strip())
            if state:
                if not dry_run:
                    conn.execute(
                        "UPDATE prices SET state = ? "
                        "WHERE LOWER(TRIM(district)) = LOWER(TRIM(?)) "
                        "AND (state IS NULL OR state = '')",
                        [state, district],
                    )
                matched += 1
            else:
                unmatched.append(district)

        conn.commit()
        updated_rows = conn.execute(
            "SELECT count(*) FROM prices WHERE state IS NOT NULL AND state != ''"
        ).fetchone()[0]

        msg = "Dry-run" if dry_run else "Backfill complete"
        log.info(msg)
        log.info("  Districts matched: %d / %d", matched, total_districts)
        log.info("  Rows with state now: %d", updated_rows)
        if unmatched:
            log.info("  Unmatched districts (%d):", len(unmatched))
            for d in unmatched[:20]:
                log.info("    - %s", d)
        return matched
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Backfill empty state names.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without making changes.")
    parser.add_argument("--with-ashoka", action="store_true",
                        help="Also fetch from Ashoka API (slow).")
    args = parser.parse_args(argv)

    log.info("Building district->state lookup...")
    lookup = build_lookup(use_ashoka=args.with_ashoka)

    log.info("Backfilling (dry_run=%s)...", args.dry_run)
    backfill(lookup, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
