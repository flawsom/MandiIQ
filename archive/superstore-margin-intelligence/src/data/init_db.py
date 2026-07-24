"""
Initialize DuckDB database with cleaned Superstore data.

Creates the database, loads cleaned data, and runs analytical SQL queries.
"""

import duckdb
import pandas as pd
from pathlib import Path


def init_database(
    clean_csv: str = "data/processed/superstore_clean.csv",
    db_path: str = "db/superstore.duckdb",
) -> str:
    """
    Create DuckDB database, load cleaned data, and persist.
    Returns the database path.
    """
    print("=== Initializing DuckDB Database ===")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # Load cleaned data with engineered features
    df = pd.read_csv(clean_csv, parse_dates=["order_date", "ship_date"])
    
    # Add engineered features for SQL queries
    from src.features.engineer import engineer_features
    df = engineer_features(df)

    # Connect to DuckDB (creates file)
    con = duckdb.connect(db_path)

    # Register and create table
    con.execute("DROP TABLE IF EXISTS superstore_sales")
    con.execute("CREATE TABLE superstore_sales AS SELECT * FROM df")

    # Verify
    count = con.execute("SELECT COUNT(*) FROM superstore_sales").fetchone()[0]
    print(f"  Loaded {count:,} rows into DuckDB at {db_path}")

    # Run some validation queries
    print("\n  --- Database Validation ---")
    print(con.execute("SELECT COUNT(*), MIN(order_date), MAX(order_date) FROM superstore_sales").fetchdf().to_string())

    con.close()
    return db_path


def get_connection(db_path: str = "db/superstore.duckdb") -> duckdb.DuckDBPyConnection:
    """Get a DuckDB connection to the database."""
    return duckdb.connect(db_path)


def run_sql_query(query_path: str, db_path: str = "db/superstore.duckdb") -> pd.DataFrame:
    """Run a SQL file against DuckDB and return results as DataFrame."""
    with open(query_path, "r") as f:
        sql = f.read()

    con = get_connection(db_path)
    result = con.execute(sql).fetchdf()
    con.close()
    return result


if __name__ == "__main__":
    init_database()
