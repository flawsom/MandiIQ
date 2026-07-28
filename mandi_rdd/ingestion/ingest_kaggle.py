"""
Ingest Kaggle historical mandi price datasets into DuckDB.

Handles two CSV formats:
1. agmarknet_india_historical_prices_2024_2025.csv (1.1M rows, Oct'24-Aug'25)
   Columns: Sl no., District Name, Market Name, Commodity, Variety, Grade,
            Min Price (Rs./Quintal), Max Price (Rs./Quintal), Modal Price (Rs./Quintal),
            Price Date, State
2. commodity_price.csv (2.7K rows, May'25 snapshot from data.gov.in)
   Columns: State, District, Market, Commodity, Variety, Grade,
            Arrival_Date, Min_x0020_Price, Max_x0020_Price, Modal_x0020_Price

Usage:
    python -m mandi_rdd.ingestion.ingest_kaggle --dir "C:\\Users\\sibap\\Downloads\\archive (1)"
"""
import argparse
import csv
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import duckdb

log = logging.getLogger("mandi_rdd.ingest_kaggle")

# Column mapping for the historical Kaggle dataset
HISTORICAL_COL_MAP = {
    "Sl no.": None,  # skip
    "District Name": "district",
    "Market Name": "market",
    "Commodity": "commodity",
    "Variety": "variety",
    "Grade": "grade",
    "Min Price (Rs./Quintal)": "min_price",
    "Max Price (Rs./Quintal)": "max_price",
    "Modal Price (Rs./Quintal)": "modal_price",
    "Price Date": "arrival_date",
    "State": "state",
}

# Column mapping for the commodity_price.csv (data.gov.in snapshot)
SNAPSHOT_COL_MAP = {
    "State": "state",
    "District": "district",
    "Market": "market",
    "Commodity": "commodity",
    "Variety": "variety",
    "Grade": "grade",
    "Arrival_Date": "arrival_date",
    "Min_x0020_Price": "min_price",
    "Max_x0020_Price": "max_price",
    "Modal_x0020_Price": "modal_price",
}

# Column mapping for WFP food prices (HDX HAPI)
WFP_COL_MAP = {
    "date": "arrival_date",
    "admin1": "state",
    "admin2": "district",
    "market": "market",
    "commodity": "commodity",
    "price": "modal_price",
    "usdprice": None,  # skip
    "market_id": None,
    "latitude": None,
    "longitude": None,
    "category": None,
    "commodity_id": None,
    "unit": None,
    "priceflag": None,
    "pricetype": None,
    "currency": None,
}

TARGET_FIELDS = ["arrival_date", "state", "district", "market", "commodity",
                 "variety", "grade", "min_price", "max_price", "modal_price"]


def parse_date(date_str: str) -> str:
    """Parse various date formats to YYYY-MM-DD."""
    if not date_str or not date_str.strip():
        return ""
    date_str = date_str.strip()
    # Try DD/MM/YYYY
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return date_str


def safe_float(val) -> float | None:
    """Convert to float, returning None on failure."""
    if val is None or str(val).strip() == "":
        return None
    try:
        return float(str(val).strip().replace(",", ""))
    except (ValueError, TypeError):
        return None


def ingest_historical_csv(filepath: str, conn: duckdb.DuckDBPyConnection) -> int:
    """Ingest the large historical Kaggle CSV."""
    log.info(f"Ingesting historical CSV: {filepath}")
    count = 0
    batch = []
    batch_size = 5000

    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            record = {}
            for src_col, dst_col in HISTORICAL_COL_MAP.items():
                if dst_col is None:
                    continue
                val = row.get(src_col, "").strip() if row.get(src_col) else ""
                if dst_col == "arrival_date":
                    val = parse_date(val)
                elif dst_col in ("min_price", "max_price", "modal_price"):
                    val = safe_float(val)
                record[dst_col] = val

            if record.get("arrival_date") and record.get("commodity"):
                batch.append(record)
                count += 1
                if len(batch) >= batch_size:
                    _insert_batch(conn, batch)
                    batch = []
                    if count % 100000 == 0:
                        log.info(f"  ... {count:,} rows ingested")

    if batch:
        _insert_batch(conn, batch)

    log.info(f"Historical CSV: {count:,} rows ingested")
    return count


def ingest_snapshot_csv(filepath: str, conn: duckdb.DuckDBPyConnection) -> int:
    """Ingest the data.gov.in snapshot CSV."""
    log.info(f"Ingesting snapshot CSV: {filepath}")
    count = 0
    batch = []
    batch_size = 1000

    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            record = {}
            for src_col, dst_col in SNAPSHOT_COL_MAP.items():
                val = row.get(src_col, "").strip() if row.get(src_col) else ""
                if dst_col == "arrival_date":
                    val = parse_date(val)
                elif dst_col in ("min_price", "max_price", "modal_price"):
                    val = safe_float(val)
                record[dst_col] = val

            if record.get("arrival_date") and record.get("commodity"):
                batch.append(record)
                count += 1
                if len(batch) >= batch_size:
                    _insert_batch(conn, batch)
                    batch = []

    if batch:
        _insert_batch(conn, batch)

    log.info(f"Snapshot CSV: {count:,} rows ingested")
    return count


def _insert_batch(conn: duckdb.DuckDBPyConnection, batch: list[dict]):
    """Insert a batch of records into the prices table using executemany."""
    if not batch:
        return
    rows = [tuple(rec.get(f) for f in TARGET_FIELDS) for rec in batch]
    try:
        conn.executemany(
            """INSERT OR IGNORE INTO prices
               (arrival_date, state, district, market, commodity, variety, grade,
                min_price, max_price, modal_price)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
    except Exception as e:
        log.debug(f"Batch insert error: {e}")


def ingest_wfp_csv(filepath: str, conn: duckdb.DuckDBPyConnection) -> int:
    """Ingest WFP/FAO food price CSV from HDX HAPI."""
    log.info(f"Ingesting WFP food prices CSV: {filepath}")
    count = 0
    batch = []
    batch_size = 5000

    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            date_str = row.get("date", "").strip()
            commodity = row.get("commodity", "").strip()
            if not date_str or not commodity:
                continue
            # Parse date (YYYY-MM-DD format)
            try:
                arrival_date = datetime.strptime(date_str[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
            except ValueError:
                continue
            price = safe_float(row.get("price"))
            if price is None:
                continue
            record = {
                "arrival_date": arrival_date,
                "state": row.get("admin1", "").strip(),
                "district": row.get("admin2", "").strip(),
                "market": row.get("market", "").strip(),
                "commodity": commodity,
                "variety": commodity,  # WFP doesn't have variety
                "grade": "",  # WFP doesn't have grade
                "min_price": None,
                "max_price": None,
                "modal_price": price,
            }
            batch.append(record)
            count += 1
            if len(batch) >= batch_size:
                _insert_batch(conn, batch)
                batch = []
                if count % 50000 == 0:
                    log.info(f"  ... {count:,} WFP rows ingested")

    if batch:
        _insert_batch(conn, batch)

    log.info(f"WFP CSV: {count:,} rows ingested")
    return count


def main(argv=None):
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="Ingest Kaggle mandi price CSVs into DuckDB.")
    p.add_argument("--dir", required=True, help="Directory containing the CSV files")
    p.add_argument("--db", default=None, help="DuckDB path (default: production DB)")
    args = p.parse_args(argv)

    data_dir = Path(args.dir)
    if not data_dir.exists():
        log.error(f"Directory not found: {data_dir}")
        return 1

    # Connect to DuckDB
    if args.db:
        db_path = args.db
    else:
        db_path = os.environ.get("MANDIIQ_DB_PATH",
                                  str(Path(__file__).resolve().parent.parent / "data" / "mandi_iq.duckdb"))

    log.info(f"Connecting to DuckDB: {db_path}")
    conn = duckdb.connect(db_path)

    # Ensure prices table exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            arrival_date DATE,
            state VARCHAR,
            district VARCHAR,
            market VARCHAR,
            commodity VARCHAR,
            variety VARCHAR,
            grade VARCHAR,
            min_price DOUBLE,
            max_price DOUBLE,
            modal_price DOUBLE,
            UNIQUE(arrival_date, state, district, market, commodity, variety, grade)
        )
    """)

    total = 0

    # Find and ingest historical CSV
    hist_file = data_dir / "agmarknet-india-commodity-prices-2024-2025" / "agmarknet_india_historical_prices_2024_2025.csv"
    if hist_file.exists():
        total += ingest_historical_csv(str(hist_file), conn)
    else:
        log.warning(f"Historical CSV not found: {hist_file}")

    # Find and ingest snapshot CSV
    snap_file = data_dir / "commodity_price.csv"
    if snap_file.exists():
        total += ingest_snapshot_csv(str(snap_file), conn)
    else:
        log.warning(f"Snapshot CSV not found: {snap_file}")

    # Find and ingest WFP food prices CSV
    wfp_file = data_dir / "wfp_food_prices_ind.csv"
    if wfp_file.exists():
        total += ingest_wfp_csv(str(wfp_file), conn)
    else:
        log.warning(f"WFP food prices CSV not found: {wfp_file}")

    # Report final counts
    n_prices = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    n_commodities = conn.execute("SELECT COUNT(DISTINCT commodity) FROM prices").fetchone()[0]
    n_states = conn.execute("SELECT COUNT(DISTINCT state) FROM prices").fetchone()[0]
    n_districts = conn.execute("SELECT COUNT(DISTINCT district) FROM prices").fetchone()[0]

    conn.close()

    log.info(f"=== Ingestion Complete ===")
    log.info(f"Rows inserted this run: {total:,}")
    log.info(f"Total prices in DB: {n_prices:,}")
    log.info(f"Commodities: {n_commodities}")
    log.info(f"States: {n_states}")
    log.info(f"Districts: {n_districts}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
