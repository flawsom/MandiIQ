import os
# Load .env so local/unattended runs pick up secrets (DATA_GOV_IN_API_KEY, etc.)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass  # python-dotenv optional; env vars may be set directly
"""
MandiRDD — DuckDB storage layer.

Migrated from SQLite to DuckDB for analytical SQL capabilities
(window functions, CTEs) matching the Superstore pattern.

Schema mirrors the data.gov.in API fields. 5 analytical SQL queries
stored in /sql/ and loadable via run_sql_query().
"""

from pathlib import Path
from typing import Optional
import pandas as pd
import logging

logger = logging.getLogger(__name__)

try:
    import duckdb
    DUCKDB_AVAILABLE = True
except ImportError:
    DUCKDB_AVAILABLE = False
    logger.warning("DuckDB not installed. Install with: pip install duckdb")

DB_PATH = Path(os.environ.get(
    "MANDIIQ_DB_PATH",
    Path(__file__).resolve().parent.parent / "data" / "mandi_iq.duckdb"
))
SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

def get_curated_commodities(limit: int = 12) -> list[str]:
    """Return a focused, data-driven commodity list for UI dropdowns.

    Picks the commodities with the most price observations in the DB so the
    dropdowns stay meaningful (the raw DISTINCT list includes source-feed noise
    such as "Absinthe"). Falls back to a small default if the DB is empty.
    """
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT commodity, COUNT(*) AS n FROM prices "
            "GROUP BY commodity ORDER BY n DESC LIMIT ?"
        ).fetchall()
        conn.close()
        if rows:
            return [r[0].title() for r in rows[:limit]]
    except Exception:
        pass
    return ["Onion", "Tomato", "Wheat", "Potato"]



def get_connection(db_path: Optional[Path] = None, read_only: bool = False) -> "duckdb.DuckDBPyConnection":
    """Get a DuckDB connection.

    Defaults to read-write, but transparently falls back to read-only mode if
    the filesystem is read-only (e.g. Streamlit Community Cloud serves the repo
    from an immutable layer). This keeps read-only dashboard queries working
    without changing call sites.
    """
    path = db_path or DB_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = duckdb.connect(str(path), read_only=read_only)
        return conn
    except Exception as exc:
        logger.warning("Read-write open failed, trying read-only: %%s", exc)
        # Read-only filesystem (deployed dashboards): retry in read-only mode.
    try:
        conn = duckdb.connect(str(path), read_only=True)
        return conn
    except Exception:
        logger.exception(
            "Cannot open DuckDB at %s (tried read-write and read-only)", path
        )
        raise


def init_schema(conn) -> None:
    """Create tables with DuckDB SQL syntax."""
    conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS seq_prices START 1;
        CREATE SEQUENCE IF NOT EXISTS seq_rainfall START 1;
        CREATE SEQUENCE IF NOT EXISTS seq_rdd START 1;
        CREATE SEQUENCE IF NOT EXISTS seq_classifier START 1;
        CREATE SEQUENCE IF NOT EXISTS seq_forecast START 1;
        CREATE SEQUENCE IF NOT EXISTS seq_ndvi START 1;
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_prices'),
            state VARCHAR NOT NULL,
            district VARCHAR NOT NULL,
            market VARCHAR NOT NULL,
            commodity VARCHAR NOT NULL,
            variety VARCHAR,
            grade VARCHAR,
            arrival_date DATE NOT NULL,
            min_price DOUBLE,
            max_price DOUBLE,
            modal_price DOUBLE,
            UNIQUE(market, commodity, variety, grade, arrival_date)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS rainfall (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_rainfall'),
            sub_division VARCHAR NOT NULL,
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            rainfall_mm DOUBLE,
            normal_mm DOUBLE,
            departure_pct DOUBLE,
            UNIQUE(sub_division, year, month)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS rdd_results (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_rdd'),
            commodity VARCHAR NOT NULL,
            computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            effect DOUBLE,
            std_error DOUBLE,
            p_value DOUBLE,
            n_left INTEGER,
            n_right INTEGER,
            bandwidth_pct DOUBLE,
            placebo_effect DOUBLE,
            placebo_p_value DOUBLE,
            fe_effect DOUBLE,
            fe_p_value DOUBLE,
            interpretation VARCHAR,
            is_valid INTEGER DEFAULT 1
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS classification_results (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_classifier'),
            commodity VARCHAR NOT NULL,
            district VARCHAR,
            computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            risk_score DOUBLE,
            model_roc_auc DOUBLE,
            top_features VARCHAR,
            n_training_rows INTEGER
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS narratives (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_rdd'),
            commodity VARCHAR NOT NULL,
            computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            narrative VARCHAR,
            model_used VARCHAR,
            endpoints_used VARCHAR,
            is_valid INTEGER DEFAULT 1,
            UNIQUE(commodity, computed_at)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS district_map (
            state VARCHAR NOT NULL,
            district VARCHAR NOT NULL,
            sub_division VARCHAR,
            UNIQUE(state, district)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ndvi (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_ndvi'),
            state VARCHAR NOT NULL,
            district VARCHAR NOT NULL,
            date DATE NOT NULL,
            ndvi DOUBLE,
            anomaly DOUBLE DEFAULT 0.0,
            UNIQUE(state, district, date)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS forecast_metrics (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_forecast'),
            commodity VARCHAR NOT NULL,
            computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            model VARCHAR DEFAULT 'prophet',
            test_mape DOUBLE,
            test_mae DOUBLE,
            test_rmse DOUBLE,
            n_training_months INTEGER,
            n_test_months INTEGER,
            is_valid INTEGER DEFAULT 1,
            UNIQUE(commodity, model, computed_at)
        )
    """)

    # Create indexes
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_prices_commodity ON prices(commodity)",
        "CREATE INDEX IF NOT EXISTS idx_prices_date ON prices(arrival_date)",
        "CREATE INDEX IF NOT EXISTS idx_prices_state ON prices(state)",
        "CREATE INDEX IF NOT EXISTS idx_rainfall_subdiv ON rainfall(sub_division)",
        "CREATE INDEX IF NOT EXISTS idx_rainfall_year_month ON rainfall(year, month)",
    ]:
        try:
            conn.execute(idx_sql)
        except Exception:
            pass


def upsert_prices(conn, records: list[dict]) -> int:
    """Bulk upsert price records — idempotent, never duplicates."""
    if not records or not DUCKDB_AVAILABLE:
        return 0

    df = pd.DataFrame(records)
    df = df.where(pd.notna(df), None)

    col_map = {
        "state": "state", "district": "district", "market": "market",
        "commodity": "commodity", "variety": "variety", "grade": "grade",
        "arrival_date": "arrival_date", "min_price": "min_price",
        "max_price": "max_price", "modal_price": "modal_price",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    for col in ["state", "district", "market", "commodity", "arrival_date"]:
        if col not in df.columns:
            df[col] = "Unknown"

    # Ensure optional columns exist (DuckDB SELECT requires them even if NULL)
    for col in ["variety", "grade", "min_price", "max_price", "modal_price"]:
        if col not in df.columns:
            df[col] = None

    # Parse dates
    if "arrival_date" in df.columns:
        df["arrival_date"] = pd.to_datetime(df["arrival_date"], errors="coerce")

    # Register temp table and INSERT OR IGNORE via DuckDB
    conn.register("_new_prices", df)
    result = conn.execute("""
        INSERT OR IGNORE INTO prices
            (state, district, market, commodity, variety, grade,
             arrival_date, min_price, max_price, modal_price)
        SELECT
            state, district, market, commodity, variety, grade,
            arrival_date, min_price, max_price, modal_price
        FROM _new_prices
    """)
    conn.unregister("_new_prices")
    count = result.fetchone()[0] if result else 0
    return count


def upsert_rainfall(conn, records: list[dict]) -> int:
    """Bulk upsert rainfall departure records."""
    if not records or not DUCKDB_AVAILABLE:
        return 0

    df = pd.DataFrame(records)
    df = df.where(pd.notna(df), None)

    conn.register("_new_rainfall", df)
    result = conn.execute("""
        INSERT OR IGNORE INTO rainfall
            (sub_division, year, month, rainfall_mm, normal_mm, departure_pct)
        SELECT
            sub_division, year, month, rainfall_mm, normal_mm, departure_pct
        FROM _new_rainfall
    """)
    conn.unregister("_new_rainfall")
    count = result.fetchone()[0] if result else 0
    return count


def save_rdd_result(conn, result: dict):
    """Save RDD computation result (including fixed-effects cross-check)."""
    conn.execute("""
        INSERT INTO rdd_results
            (commodity, effect, std_error, p_value, n_left, n_right,
             bandwidth_pct, placebo_effect, placebo_p_value,
             fe_effect, fe_p_value, interpretation)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        result.get("commodity", ""),
        _safe_float(result.get("effect")),
        _safe_float(result.get("std_error")),
        _safe_float(result.get("p_value")),
        int(result.get("n_left", 0)),
        int(result.get("n_right", 0)),
        _safe_float(result.get("bandwidth_pct")),
        _safe_float(result.get("placebo_effect")),
        _safe_float(result.get("placebo_p_value")),
        _safe_float(result.get("fe_effect")),
        _safe_float(result.get("fe_p_value")),
        str(result.get("interpretation", "")),
    ])


def save_classification_result(conn, result: dict):
    """Save classifier result."""
    conn.execute("""
        INSERT INTO classification_results
            (commodity, district, risk_score, model_roc_auc, top_features, n_training_rows)
        VALUES (?, ?, ?, ?, ?, ?)
    """, [
        result.get("commodity", ""),
        result.get("district", "All"),
        _safe_float(result.get("risk_score")),
        _safe_float(result.get("roc_auc")),
        str(result.get("top_features", "")),
        int(result.get("n_training_rows", 0)),
    ])


def get_latest_rdd(conn, commodity: str) -> Optional[dict]:
    """Get the most recent RDD result for a commodity."""
    result = conn.execute("""
        SELECT * FROM rdd_results
        WHERE commodity = ?
        ORDER BY computed_at DESC LIMIT 1
    """, [commodity]).fetchdf()
    if len(result) > 0:
        return result.iloc[0].to_dict()
    return None


def get_latest_classification(conn, commodity: str, district: str = None) -> Optional[dict]:
    """Get the most recent classification result."""
    query = "SELECT * FROM classification_results WHERE commodity = ?"
    params = [commodity]
    if district:
        query += " AND district = ?"
        params.append(district)
    query += " ORDER BY computed_at DESC LIMIT 1"

    result = conn.execute(query, params).fetchdf()
    if len(result) > 0:
        return result.iloc[0].to_dict()
    return None


def get_prices(conn, state=None, district=None, commodity=None, limit=1000) -> pd.DataFrame:
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
    return conn.execute(query, params).fetchdf()


def get_monthly_avg_prices(conn, commodity: str, state: str = None) -> pd.DataFrame:
    """Get monthly average modal_price for RDD join."""
    query = """
        SELECT
            state, district,
            EXTRACT(YEAR FROM arrival_date) AS year,
            EXTRACT(MONTH FROM arrival_date) AS month,
            AVG(modal_price) AS avg_modal_price,
            COUNT(*) AS n_observations
        FROM prices
        WHERE commodity = ? AND modal_price IS NOT NULL
    """
    params = [commodity]
    if state:
        query += " AND state = ?"
        params.append(state)
    query += """
        GROUP BY state, district, year, month
        HAVING COUNT(*) >= 3
        ORDER BY year, month
    """
    return conn.execute(query, params).fetchdf()


def save_narrative(conn, commodity: str, narrative: str, model_used: str = None, endpoints_used: list = None):
    """Save a nightly narrative for a commodity."""
    conn.execute("""
        INSERT INTO narratives
            (commodity, narrative, model_used, endpoints_used)
        VALUES (?, ?, ?, ?)
    """, [
        commodity,
        narrative,
        model_used or "",
        ", ".join(endpoints_used) if endpoints_used else "",
    ])


def get_latest_narrative(conn, commodity: str) -> Optional[dict]:
    """Get the most recent nightly narrative for a commodity."""
    result = conn.execute("""
        SELECT narrative, model_used, endpoints_used, computed_at
        FROM narratives
        WHERE commodity = ? AND is_valid = 1
        ORDER BY computed_at DESC LIMIT 1
    """, [commodity]).fetchdf()
    if len(result) > 0:
        return result.iloc[0].to_dict()
    return None


def get_distinct_commodities(conn) -> list[str]:
    """Get list of distinct commodities in the database."""
    result = conn.execute("SELECT DISTINCT commodity FROM prices ORDER BY commodity").fetchdf()
    return result["commodity"].tolist() if len(result) > 0 else []


def run_sql_query(conn, query_name: str, params: list = None) -> str:
    """
    Load an analytical SQL query from /sql/ and return its contents.
    Does NOT execute — used for display in the dashboard's Deep Dive page.
    """
    sql_path = SQL_DIR / query_name
    if sql_path.exists():
        return sql_path.read_text()
    return f"-- Query {query_name} not found"


def execute_sql_file(conn, filename: str, commodity: str) -> pd.DataFrame:
    """Execute a SQL file with the given commodity parameter."""
    sql_path = SQL_DIR / filename
    if not sql_path.exists():
        return pd.DataFrame({"error": [f"SQL file not found: {filename}"]})
    
    sql = sql_path.read_text()
    # Replace ? parameter with actual commodity
    result = conn.execute(sql, [commodity])
    return result.fetchdf()


def upsert_ndvi(conn, records: list[dict]) -> int:
    """Bulk upsert NDVI records into the ndvi table."""
    if not records:
        return 0
    import pandas as pd
    df = pd.DataFrame(records)
    df = df.where(pd.notna(df), None)
    conn.register("_new_ndvi", df)
    result = conn.execute("""
        INSERT OR IGNORE INTO ndvi
            (state, district, date, ndvi, anomaly)
        SELECT state, district, date, ndvi, anomaly
        FROM _new_ndvi
    """)
    conn.unregister("_new_ndvi")
    count = result.fetchone()[0] if result else 0
    return count



def _safe_float(val):
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def save_forecast_metrics(conn, commodity, test_mape=None, test_mae=None,
                          test_rmse=None, n_training_months=None,
                          n_test_months=None, model="prophet"):
    """Persist forecast accuracy metrics (MAPE/MAE/RMSE) for a commodity."""
    if not DUCKDB_AVAILABLE or not commodity:
        return None
    try:
        conn.execute(
            """INSERT INTO forecast_metrics
               (commodity, model, test_mape, test_mae, test_rmse,
                n_training_months, n_test_months)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [commodity, model, test_mape, test_mae, test_rmse,
             n_training_months, n_test_months],
        )
        conn.commit()
        return True
    except Exception as e:
        logging.getLogger("mandi_rdd.store").warning(f"save_forecast_metrics failed: {e}")
        return None


def get_latest_forecast_metrics(conn, commodity, model="prophet"):
    """Return the most recent forecast metrics row for a commodity, or None."""
    if not DUCKDB_AVAILABLE or not commodity:
        return None
    try:
        row = conn.execute(
            """SELECT commodity, computed_at, model, test_mape, test_mae,
                      test_rmse, n_training_months, n_test_months
               FROM forecast_metrics
               WHERE commodity = ? AND model = ?
               ORDER BY computed_at DESC LIMIT 1""",
            [commodity, model],
        ).fetchone()
        if not row:
            return None
        cols = ["commodity", "computed_at", "model", "test_mape", "test_mae",
                "test_rmse", "n_training_months", "n_test_months"]
        return dict(zip(cols, row))
    except Exception as e:
        logging.getLogger("mandi_rdd.store").warning(f"get_latest_forecast_metrics failed: {e}")
        return None


def get_avg_price_and_districts(conn, commodity):
    """Return (avg_modal_price, n_districts) for a commodity from live prices."""
    if not DUCKDB_AVAILABLE or not commodity:
        return (None, None)
    try:
        row = conn.execute(
            """SELECT AVG(modal_price), COUNT(DISTINCT district)
               FROM prices WHERE commodity = ? AND modal_price IS NOT NULL""",
            [commodity],
        ).fetchone()
        if not row:
            return (None, None)
        return (float(row[0]) if row[0] is not None else None,
                int(row[1]) if row[1] is not None else 0)
    except Exception as e:
        logging.getLogger("mandi_rdd.store").warning(f"get_avg_price_and_districts failed: {e}")
        return (None, None)


def get_distinct_options(field: str, limit: int = 50) -> list[str]:
    """Return distinct values for a prices column, ordered by count descending.

    Fields: district, state, market, grade, commodity, variety.
    Falls back to a small default if the DB is empty or unreachable.
    """
    defaults = {
        "district": ["Nashik", "Pune", "Lasalgaon", "Azadpur"],
        "state": ["Maharashtra", "Gujarat", "Madhya Pradesh"],
        "market": ["Lasalgaon", "Pune", "Azadpur"],
        "grade": ["FAQ", "Grade A", "Grade B"],
        "commodity": ["Onion", "Tomato", "Wheat", "Potato"],
        "variety": [],
    }
    try:
        conn = get_connection()
        rows = conn.execute(
            f"SELECT {field}, COUNT(*) AS n FROM prices "
            f"WHERE {field} IS NOT NULL AND {field} != '' "
            f"GROUP BY {field} ORDER BY n DESC LIMIT ?",
            [limit],
        ).fetchall()
        conn.close()
        if rows:
            return [str(r[0]).title() for r in rows]
    except Exception:
        pass
    return defaults.get(field, [])
