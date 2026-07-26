"""One-off + scheduler-friendly historical backfill for MandiRDD.

The live data.gov.in `9ef84268...` resource is a DAILY snapshot feed: it only
ever exposes the most recent day's mandi prices. That means the nightly
scheduler can never accumulate more than a few days of history on its own,
which blocks the RDD estimator (needs 3+ deficient/non-deficient rainfall
dates) and the Prophet forecast (needs >=20 daily points for a test split).

To get a real, functional, auto-updating dashboard you must seed HISTORY
first. This script ingests a bulk historical CSV (Agmarknet / data.gov.in
dataset page monthly archives) into the `prices` table. Once history exists,
the nightly scheduler keeps it fresh automatically.

Expected CSV columns (Agmarknet / data.gov.in bulk format):
    arrival_date, state, district, market, commodity, variety,
    grade, min_price, max_price, modal_price
Date format is auto-detected: dd/mm/yyyy, yyyy-mm-dd, or mm-dd-yyyy.

Usage:
    # backfill a single file
    python -m mandi_rdd.ingestion.ingest_historical_csv path/to/history.csv

    # backfill every .csv dropped into data/historical/ (used by scheduler)
    python -m mandi_rdd.ingestion.ingest_historical_csv --auto

    # custom folder + commit batch size
    python -m mandi_rdd.ingestion.ingest_historical_csv --auto --folder data/historical --batch 5000
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import logging
from datetime import datetime

logger = logging.getLogger("mandi_rdd.ingest_historical")

# Column-name aliases -> canonical DB field. Keeps the script tolerant of
# slightly different headers across Agmarknet / data.gov.in exports.
# NOTE: the live data.gov.in API returns columns with URL-encoded spaces,
# e.g. "Modal_x0020_Price", "Arrival_Date", "Commodity". We normalise
# those (strip _x0020_ and surrounding spaces) before matching.
COLUMN_ALIASES = {
    "arrival_date": "arrival_date",
    "date": "arrival_date",
    "arrivaldate": "arrival_date",
    "state": "state",
    "district": "district",
    "market": "market",
    "mandi": "market",
    "commodity": "commodity",
    "variety": "variety",
    "grade": "grade",
    "min_price": "min_price",
    "minprice": "min_price",
    "max_price": "max_price",
    "maxprice": "max_price",
    "modal_price": "modal_price",
    "modalprice": "modal_price",
    "price": "modal_price",
}


def _norm_key(k: str) -> str:
    """Normalise a CSV header for alias lookup.

    Handles data.gov.in's URL-encoded spaces ('Modal_x0020_Price')
    and stray whitespace/case so the canonical mapping still matches.
    """
    if k is None:
        return ""
    s = str(k).strip().lower()
    s = s.replace("_x0020_", "_").replace("x0020", "_")
    s = s.replace(" ", "_")
    return s

_DATE_FORMATS = ("%d/%m/%Y", "%Y-%m-%d", "%m-%d-%Y", "%d-%m-%Y", "%d/%m/%y")


def _parse_date(value: str):
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_float(value):
    if value is None:
        return None
    v = str(value).strip().replace(",", "")
    if not v or v.lower() in ("na", "nan", "none", "null", "-"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _normalize_row(row: dict) -> dict | None:
    """Map a raw CSV row to the canonical prices schema."""
    norm = {}
    for raw_key, raw_val in row.items():
        if raw_key is None:
            continue
        canon = COLUMN_ALIASES.get(_norm_key(raw_key))
        if canon and raw_val is not None:
            norm[canon] = raw_val
    if not norm.get("commodity") or not norm.get("arrival_date"):
        return None
    return {
        "state": (norm.get("state") or "").strip(),
        "district": (norm.get("district") or "").strip(),
        "market": (norm.get("market") or "").strip(),
        "commodity": norm["commodity"].strip(),
        "variety": (norm.get("variety") or "FAQ").strip(),
        "grade": (norm.get("grade") or "FAQ").strip(),
        "arrival_date": _parse_date(norm.get("arrival_date")),
        "min_price": _parse_float(norm.get("min_price")),
        "max_price": _parse_float(norm.get("max_price")),
        "modal_price": _parse_float(norm.get("modal_price")),
    }


def ingest_file(path: str, batch: int = 5000) -> int:
    """Ingest a single CSV. Returns number of rows upserted.

    Each record is tagged with ``_source`` metadata for data-lineage tracking:
        {
            "source_type": "csv",
            "source_name": os.path.basename(path),
            "resource_id": path,
        }
    """
    from mandi_rdd.storage.duckdb_store import get_connection, upsert_prices, record_lineage_batch

    conn = get_connection(read_only=False)
    try:
        total = 0
        total_new = 0
        buffer = []
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            # Ashoka CSV exports can have commodity/variety descriptions > 131 KB default limit
            csv.field_size_limit(2**30)
            reader = csv.DictReader(fh)
            for raw in reader:
                rec = _normalize_row(raw)
                if not rec or rec["arrival_date"] is None:
                    continue
                # Tag with source metadata for lineage
                rec["_source"] = {
                    "source_type": "csv",
                    "source_name": os.path.basename(path),
                    "resource_id": path,
                }
                buffer.append(rec)
                if len(buffer) >= batch:
                    n_new = upsert_prices(conn, buffer)
                    total_new += n_new
                    total += len(buffer)
                    _record_lineage(conn, buffer, n_new, os.path.basename(path), path)
                    buffer.clear()
        if buffer:
            n_new = upsert_prices(conn, buffer)
            total_new += n_new
            total += len(buffer)
            _record_lineage(conn, buffer, n_new, os.path.basename(path), path)
        conn.commit()
        logger.info(f"Ingested {total} rows ({total_new} new) from {os.path.basename(path)}")
        return total
    finally:
        conn.close()


def _record_lineage(conn, records, n_new, source_name, resource_id):
    """Record lineage for a batch of CSV-ingested records.

    Detects Ashoka-origin CSVs by filename pattern and records
    the upstream source in metadata for full provenance.
    """
    try:
        metadata = {"file_path": resource_id}
        # If the CSV was written by the Ashoka background import,
        # tag the lineage so we know the origin, not just the medium.
        if "ashoka" in source_name.lower():
            metadata["origin"] = "ashoka_ceda_api"

        record_lineage_batch(
            conn,
            source_type="csv",
            source_name=source_name,
            resource_id=resource_id,
            row_count=len(records),
            n_new=n_new,
            records=records,
            metadata=metadata,
        )
    except Exception as e:
        logger.warning(f"Failed to record lineage for {source_name}: {e}")


def run_auto(folder: str = "data/historical", batch: int = 5000) -> int:
    """Ingest every *.csv in `folder`, then delete the ones that succeeded."""
    if not os.path.isdir(folder):
        logger.info(f"No historical folder at {folder}; skipping backfill.")
        return 0
    files = sorted(f for f in os.listdir(folder) if f.lower().endswith(".csv"))
    if not files:
        logger.info(f"No historical CSVs in {folder}; skipping backfill.")
        return 0
    total = 0
    for fname in files:
        fpath = os.path.join(folder, fname)
        try:
            n = ingest_file(fpath, batch=batch)
            total += n
            os.remove(fpath)  # consumed -> avoid re-ingesting every night
            logger.info(f"Consumed historical file: {fname} ({n} rows)")
        except Exception as e:
            logger.warning(f"Failed to ingest {fname}: {e}")
    return total


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Backfill historical mandi prices from CSV.")
    parser.add_argument("csv", nargs="?", help="Path to a historical CSV file.")
    parser.add_argument("--auto", action="store_true", help="Ingest all CSVs in --folder then delete them.")
    parser.add_argument("--folder", default="data/historical", help="Folder for --auto mode.")
    parser.add_argument("--batch", type=int, default=5000, help="Upsert batch size.")
    args = parser.parse_args(argv)

    if args.auto:
        n = run_auto(args.folder, batch=args.batch)
        print(f"AUTO backfill complete: {n} rows ingested.")
        return 0
    if args.csv:
        if not os.path.isfile(args.csv):
            print(f"File not found: {args.csv}", file=sys.stderr)
            return 2
        n = ingest_file(args.csv, batch=args.batch)
        print(f"Backfill complete: {n} rows ingested from {args.csv}.")
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
