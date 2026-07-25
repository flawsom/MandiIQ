"""
MandiRDD — storage layer.

SQLite-backed storage with upsert-on-conflict deduplication.
Schema mirrors the data.gov.in API fields.
"""

import sqlite3
from pathlib import Path
from typing import Optional
import pandas as pd

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "mandi_rdd.db"

def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Get a SQLite connection with row factory."""
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def init_schema(conn: sqlite3.Connection):
    """Create tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            state TEXT NOT NULL,
            district TEXT NOT NULL,
            market TEXT NOT NULL,
            commodity TEXT NOT NULL,
            variety TEXT,
            grade TEXT,
            arrival_date TEXT NOT NULL,
            min_price REAL,
            max_price REAL,
            modal_price REAL,
            UNIQUE(market, commodity, variety, grade, arrival_date)
        );

        CREATE TABLE IF NOT EXISTS rainfall (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sub_division TEXT NOT NULL,
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            rainfall_mm REAL,
            normal_mm REAL,
            departure_pct REAL,
            UNIQUE(sub_division, year, month)
        );

        CREATE TABLE IF NOT EXISTS rdd_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            commodity TEXT NOT NULL,
            computed_at TEXT NOT NULL DEFAULT (datetime('now')),
            effect REAL,
            std_error REAL,
            p_value REAL,
            n_left INTEGER,
            n_right INTEGER,
            bandwidth_pct REAL,
            placebo_effect REAL,
            placebo_p_value REAL,
            interpretation TEXT,
            is_valid INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS classification_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            commodity TEXT NOT NULL,
            district TEXT,
            computed_at TEXT NOT NULL DEFAULT (datetime('now')),
            risk_score REAL,
            model_roc_auc REAL,
            top_features TEXT,
            n_training_rows INTEGER
        );

        CREATE TABLE IF NOT EXISTS district_map (
            state TEXT NOT NULL,
            district TEXT NOT NULL,
            sub_division TEXT,
            UNIQUE(state, district)
        );

        CREATE INDEX IF NOT EXISTS idx_prices_commodity ON prices(commodity);
        CREATE INDEX IF NOT EXISTS idx_prices_date ON prices(arrival_date);
        CREATE INDEX IF NOT EXISTS idx_rainfall_subdiv ON rainfall(sub_division);
    """)
    conn.commit()

def upsert_prices(conn: sqlite3.Connection, records: list[dict]) -> int:
    """Bulk upsert price records — idempotent, never duplicates."""
    if not records:
        return 0

    df = pd.DataFrame(records)
    df = df.where(pd.notna(df), None)

    # Map API field names to schema
    col_map = {
        "state": "state",
        "district": "district",
        "market": "market",
        "commodity": "commodity",
        "variety": "variety",
        "grade": "grade",
        "arrival_date": "arrival_date",
        "min_price": "min_price",
        "max_price": "max_price",
        "modal_price": "modal_price",
    }

    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    required = ["state", "district", "market", "commodity", "arrival_date"]
    for col in required:
        if col not in df.columns:
            df[col] = "Unknown"

    # Upsert via INSERT OR IGNORE
    count = 0
    for _, row in df.iterrows():
        try:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO prices
                   (state, district, market, commodity, variety, grade,
                    arrival_date, min_price, max_price, modal_price)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(row.get("state", "")),
                    str(row.get("district", "")),
                    str(row.get("market", "")),
                    str(row.get("commodity", "")),
                    str(row.get("variety", "")),
                    str(row.get("grade", "")),
                    str(row.get("arrival_date", "")),
                    _safe_float(row.get("min_price")),
                    _safe_float(row.get("max_price")),
                    _safe_float(row.get("modal_price")),
                ),
            )
            if cursor.rowcount > 0:
                count += 1
        except Exception:
            pass

    conn.commit()
    return count

def upsert_rainfall(conn: sqlite3.Connection, records: list[dict]) -> int:
    """Bulk upsert rainfall departure records."""
    if not records:
        return 0

    count = 0
    for r in records:
        try:
            conn.execute(
                """INSERT OR IGNORE INTO rainfall
                   (sub_division, year, month, rainfall_mm, normal_mm, departure_pct)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    str(r.get("sub_division", "")),
                    int(r.get("year", 0)),
                    int(r.get("month", 0)),
                    _safe_float(r.get("rainfall_mm")),
                    _safe_float(r.get("normal_mm")),
                    _safe_float(r.get("departure_pct")),
                ),
            )
            count += 1
        except Exception:
            pass

    conn.commit()
    return count

def get_prices(
    conn: sqlite3.Connection,
    state: Optional[str] = None,
    district: Optional[str] = None,
    commodity: Optional[str] = None,
    limit: int = 1000,
) -> pd.DataFrame:
    """Query prices with optional filters."""
    query = "SELECT * FROM prices WHERE 1=1"
    params = []

    if state:
        query += " AND state = ?"
        params.append(state)
    if district:
        query += " AND district = ?"
        params.append(district)
    if commodity:
        query += " AND commodity = ?"
        params.append(commodity)

    query += " ORDER BY arrival_date DESC LIMIT ?"
    params.append(limit)

    df = pd.read_sql_query(query, conn, params=params)
    return df

def _safe_float(val):
    """Convert to float or None."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
