"""
Ingest Kaggle + WFP historical mandi price datasets into DuckDB.

Uses DuckDB's native CSV reading for blazing-fast bulk import.

Handles three CSV formats:
1. agmarknet_india_historical_prices_2024_2025.csv (1.1M rows, Oct'24-Aug'25)
2. commodity_price.csv (2.7K rows, data.gov.in snapshot)
3. wfp_food_prices_ind.csv (205K rows, 1994-present, WFP/FAO)

Usage:
    python -m mandi_rdd.ingestion.ingest_kaggle --dir "C:\\path\\to\\csvs"
"""
import argparse
import logging
import os
import sys
from pathlib import Path

import duckdb

log = logging.getLogger("mandi_rdd.ingest_kaggle")


def create_prices_table(conn):
    """Create the prices table if it doesn't exist."""
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
            modal_price DOUBLE
        )
    """)


def ingest_agmarknet_historical(filepath, conn):
    """Ingest the large Agmarknet historical CSV using DuckDB native CSV reader."""
    log.info(f"Ingesting Agmarknet historical CSV: {filepath}")

    # Use DuckDB's native CSV reader - much faster than Python csv module
    conn.execute(f"""
        INSERT INTO prices (arrival_date, state, district, market, commodity, variety, grade,
                           min_price, max_price, modal_price)
        SELECT
            TRY_CAST("Price Date" AS DATE) AS arrival_date,
            TRIM(State) AS state,
            TRIM("District Name") AS district,
            TRIM("Market Name") AS market,
            TRIM(Commodity) AS commodity,
            TRIM(Variety) AS variety,
            TRIM(Grade) AS grade,
            TRY_CAST(REPLACE(CAST("Min Price (Rs./Quintal)" AS VARCHAR), ',', '') AS DOUBLE) AS min_price,
            TRY_CAST(REPLACE(CAST("Max Price (Rs./Quintal)" AS VARCHAR), ',', '') AS DOUBLE) AS max_price,
            TRY_CAST(REPLACE(CAST("Modal Price (Rs./Quintal)" AS VARCHAR), ',', '') AS DOUBLE) AS modal_price
        FROM read_csv_auto('{filepath}', header=true, ignore_errors=true)
        WHERE "Price Date" IS NOT NULL
          AND Commodity IS NOT NULL
          AND TRIM(Commodity) != ''
    """)

    count = conn.execute("SELECT COUNT(*) FROM prices WHERE commodity IS NOT NULL").fetchone()[0]
    log.info(f"Agmarknet historical: {count:,} total rows in DB")
    return count


def ingest_snapshot(filepath, conn):
    """Ingest the data.gov.in snapshot CSV."""
    log.info(f"Ingesting snapshot CSV: {filepath}")

    conn.execute(f"""
        INSERT INTO prices (arrival_date, state, district, market, commodity, variety, grade,
                           min_price, max_price, modal_price)
        SELECT
            TRY_CAST(Arrival_Date AS DATE) AS arrival_date,
            TRIM(State) AS state,
            TRIM(District) AS district,
            TRIM(Market) AS market,
            TRIM(Commodity) AS commodity,
            TRIM(Variety) AS variety,
            TRIM(Grade) AS grade,
            TRY_CAST(REPLACE(CAST("Min_x0020_Price" AS VARCHAR), ',', '') AS DOUBLE) AS min_price,
            TRY_CAST(REPLACE(CAST("Max_x0020_Price" AS VARCHAR), ',', '') AS DOUBLE) AS max_price,
            TRY_CAST(REPLACE(CAST("Modal_x0020_Price" AS VARCHAR), ',', '') AS DOUBLE) AS modal_price
        FROM read_csv_auto('{filepath}', header=true, ignore_errors=true)
        WHERE Arrival_Date IS NOT NULL
          AND Commodity IS NOT NULL
    """)

    count = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    log.info(f"Snapshot: {count:,} total rows in DB")
    return count


def ingest_wfp(filepath, conn):
    """Ingest WFP/FAO food price CSV from HDX HAPI."""
    log.info(f"Ingesting WFP food prices CSV: {filepath}")

    conn.execute(f"""
        INSERT INTO prices (arrival_date, state, district, market, commodity, variety, grade,
                           min_price, max_price, modal_price)
        SELECT
            TRY_CAST(date AS DATE) AS arrival_date,
            TRIM(admin1) AS state,
            TRIM(admin2) AS district,
            TRIM(market) AS market,
            TRIM(commodity) AS commodity,
            TRIM(commodity) AS variety,
            '' AS grade,
            NULL AS min_price,
            NULL AS max_price,
            TRY_CAST(price AS DOUBLE) AS modal_price
        FROM read_csv_auto('{filepath}', header=true, ignore_errors=true)
        WHERE date IS NOT NULL
          AND commodity IS NOT NULL
          AND price IS NOT NULL
    """)

    count = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    log.info(f"WFP: {count:,} total rows in DB")
    return count


def main(argv=None):
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="Ingest mandi price CSVs into DuckDB.")
    p.add_argument("--dir", required=True, help="Directory containing CSV files")
    p.add_argument("--db", default=None, help="DuckDB path")
    args = p.parse_args(argv)

    data_dir = Path(args.dir)
    if not data_dir.exists():
        log.error(f"Directory not found: {data_dir}")
        return 1

    db_path = args.db or os.environ.get(
        "MANDIIQ_DB_PATH",
        str(Path(__file__).resolve().parent.parent / "data" / "mandi_iq.duckdb")
    )

    log.info(f"Connecting to DuckDB: {db_path}")
    conn = duckdb.connect(db_path)
    create_prices_table(conn)

    # 1. Agmarknet historical (1.1M rows)
    hist_file = data_dir / "agmarknet-india-commodity-prices-2024-2025" / "agmarknet_india_historical_prices_2024_2025.csv"
    if hist_file.exists():
        ingest_agmarknet_historical(str(hist_file).replace("\\", "/"), conn)
    else:
        log.warning(f"Historical CSV not found: {hist_file}")

    # 2. data.gov.in snapshot (2.7K rows)
    snap_file = data_dir / "commodity_price.csv"
    if snap_file.exists():
        ingest_snapshot(str(snap_file).replace("\\", "/"), conn)
    else:
        log.warning(f"Snapshot CSV not found: {snap_file}")

    # 3. WFP food prices (205K rows)
    wfp_file = data_dir / "wfp_food_prices_ind.csv"
    if wfp_file.exists():
        ingest_wfp(str(wfp_file).replace("\\", "/"), conn)
    else:
        log.warning(f"WFP CSV not found: {wfp_file}")

    # Report
    n_prices = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    n_commodities = conn.execute("SELECT COUNT(DISTINCT commodity) FROM prices").fetchone()[0]
    n_states = conn.execute("SELECT COUNT(DISTINCT state) FROM prices").fetchone()[0]
    n_districts = conn.execute("SELECT COUNT(DISTINCT district) FROM prices").fetchone()[0]
    date_range = conn.execute("SELECT MIN(arrival_date), MAX(arrival_date) FROM prices").fetchone()

    conn.close()

    log.info("=" * 50)
    log.info("INGESTION COMPLETE")
    log.info("=" * 50)
    log.info(f"Total prices: {n_prices:,}")
    log.info(f"Commodities: {n_commodities}")
    log.info(f"States: {n_states}")
    log.info(f"Districts: {n_districts}")
    log.info(f"Date range: {date_range[0]} to {date_range[1]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
