"""Efficient historical Agmarknet fetcher via the Ashoka CEDA API.

The live data.gov.in mandi feed is a DAILY snapshot (no history). This
script pulls the Ashoka CEDA Agmarknet mirror, which serves multi-year
MONTHLY history per (state, commodity, district) cell via:

    POST https://agmarknet.ceda.ashoka.edu.in/api/prices
    body: {state_id:int, commodity_id:int, district_id:str,
            calculation_type:"m", start_date:"yyyy-mm-dd", end_date:"yyyy-mm-dd"}
    -> {data:[{t:"2025-06", cmdty, district, district_id,
                 p_min, p_max, p_modal}, ...]}

NOTE: that host's TLS cert was EXPIRED for a long time but was renewed
around July 2026. We re-enable verification. If it breaks again, set
VERIFY_TLS=False and use the unverified context.

OUTPUT: rows in the SAME schema as the Agmarknet bulk CSV so the
existing backfill (ingest_historical_csv.py) ingests it unchanged:
    arrival_date (first-of-month), state, district, market(=district),
    commodity, variety(=cmdty), grade(=cmdty),
    min_price, max_price, modal_price

    python -m mandi_rdd.ingestion.fetch_historical_ashoka --out data/historical

The nightly scheduler's backfill step then ingests it. Forecast MAPE
uses this monthly history; the daily feed still drives RDD.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import ssl
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import urllib.request
import urllib.error

log = logging.getLogger("mandi_rdd.ashoka")

BASE = "https://agmarknet.ceda.ashoka.edu.in"
VERIFY_TLS = True  # cert renewed ~July 2026
if VERIFY_TLS:
    CTX = ssl.create_default_context()
else:
    CTX = ssl.create_default_context()
    CTX.check_hostname = False
    CTX.verify_mode = ssl.CERT_NONE
HEADERS = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json",
            "Accept": "application/json"}

START = "2010-01-01"
END = datetime.now().strftime("%Y-%m-%d")
FIELDNAMES = ["arrival_date", "state", "district", "market", "commodity",
               "variety", "grade", "min_price", "max_price", "modal_price"]
_write_lock = threading.Lock()

# Default commodity set: the ~40 most-traded mandi commodities. Keeps the
# full fetch bounded (states x districts x ~40) instead of all 453.
DEFAULT_COMMODITIES = (
    "onion,potato,tomato,wheat,paddy,rice,maize,gram,arhar,moong,urd,"
    "soybean,groundnut,mustard,sugarcane,cotton,jute,rapeseed,bajra,jwar,barley,"
    "pea,garlic,ginger,turmeric,chilli,coriander,banana,mango,apple,cauliflower,"
    "cabbage,brinjal,carrot,beetroot,radish,bitter gourd,bottle gourd,ridge gourd,"
    "spinach,lady finger,green pea"
)


def _get(path):
    with urllib.request.urlopen(urllib.request.Request(BASE + path, headers=HEADERS),
                                 timeout=25, context=CTX) as f:
        return json.loads(f.read()).get("data", [])


def _post(body):
    req = urllib.request.Request(BASE + "/api/prices",
        data=json.dumps(body).encode(), headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=45, context=CTX) as f:
        return json.loads(f.read()).get("data", [])


def fetch_cell(state_id, comm_id, dist_id, start=START, end=END):
    body = {"state_id": int(state_id), "commodity_id": int(comm_id),
             "district_id": str(dist_id), "calculation_type": "m",
             "start_date": start, "end_date": end}
    try:
        rows = _post(body)
    except Exception:
        return []  # empty cell or transient error -> skip
    out = []
    for r in rows:
        t = r.get("t") or r.get("arrival_date")
        if not t:
            continue
        try:
            arrival = datetime.strptime(str(t)[:7], "%Y-%m").strftime("%Y-%m-%d")
        except Exception:
            arrival = str(t)
        cmdty = r.get("cmdty") or r.get("commodity") or ""
        dist = r.get("district") or r.get("district_name") or ""
        out.append({
            "arrival_date": arrival,
            "state": r.get("state") or r.get("state_name") or "",
            "district": dist,
            "market": dist,
            "commodity": cmdty,
            "variety": cmdty,
            "grade": cmdty,
            "min_price": r.get("p_min"),
            "max_price": r.get("p_max"),
            "modal_price": r.get("p_modal"),
        })
    return out


def iter_cells(top_commodities=None, max_workers=16):
    states = _get("/api/states")
    commodities = _get("/api/commodities")
    if top_commodities:
        commodities = [c for c in commodities
                      if (c.get("commodity_disp_name") or "").lower() in top_commodities]
    for s in states:
        sid = s["census_state_id"]
        try:
            dists = _get(f"/api/districts?state_id={sid}")
        except Exception:
            dists = []
        for d in dists:
            did = d.get("census_district_id")
            for c in commodities:
                yield (sid, c["commodity_id"], did)


def main(argv=None):
    logging.basicConfig(level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="Fetch Agmarknet history via Ashoka CEDA API.")
    p.add_argument("--out", default="data/historical/agmarknet_ashoka.csv")
    p.add_argument("--commodities", default=DEFAULT_COMMODITIES,
        help="Comma-separated lowercase commodity names to limit to (faster). "
              "Empty string = all commodities.")
    p.add_argument("--workers", type=int, default=16)
    p.add_argument("--limit", type=int, default=0, help="Max cells to fetch (0=all).")
    args = p.parse_args(argv)

    # Early health-check: if /api/prices returns 500, abort before spinning
    # up thousands of threads that will all fail identically.
    try:
        probe_body = {"state_id": 29, "commodity_id": 23, "district_id": "1",
                       "calculation_type": "m", "start_date": "2024-01-01",
                       "end_date": "2025-01-01"}
        probe_rows = _post(probe_body)
        log.info(f"Ashoka health-check OK: probe returned {len(probe_rows)} rows")
    except Exception as probe_err:
        log.error(f"Ashoka /api/prices is unreachable ({probe_err}). "
                   "Their backend database is likely down. Aborting historical fetch.")
        return 1

    top = {x.strip().lower() for x in args.commodities.split(",") if x.strip()} or None
    cells = list(iter_cells(top_commodities=top))
    if args.limit:
        cells = cells[:args.limit]
    log.info(f"Fetching {len(cells)} cells (workers={args.workers}) -> {args.out}")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    total = 0
    written = 0
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(fetch_cell, s, c, d): (s, c, d) for s, c, d in cells}
            for fut in as_completed(futs):
                rows = fut.result()
                total += 1
                if rows:
                    with _write_lock:
                        w.writerows(rows)
                    written += len(rows)
                if total % 250 == 0:
                    log.info(f"progress: {total}/{len(cells)} cells, {written} rows")
    log.info(f"DONE: {total} cells, {written} price-rows -> {args.out}")


if __name__ == "__main__":
    raise SystemExit(main())
