import os

# Load .env so local/unattended runs pick up secrets (DATA_GOV_IN_API_KEY, etc.)

try:

    from dotenv import load_dotenv

    load_dotenv()

except Exception:

    pass  # python-dotenv optional; env vars may be set directly

"""

MandiRDD - DuckDB storage layer.

Migrated from SQLite to DuckDB for analytical SQL capabilities

(window functions, CTEs) matching the Superstore pattern.

Schema mirrors the data.gov.in API fields. 5 analytical SQL queries

stored in /sql/ and loadable via run_sql_query().

"""

from pathlib import Path

from typing import Optional

import pandas as pd

import logging

from mandi_rdd.core.dates import parse_arrival_date

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

LFS_POINTER_MAX_BYTES = 200  # LFS pointer files are ~100 bytes; real DuckDB is 50MB+


def _is_lfs_pointer(path: Path) -> bool:
    """Check if a file is a Git LFS pointer (not the real database).

    LFS pointer files are small text files (~100 bytes). A real DuckDB
    database is always a binary file larger than 200 bytes.
    """
    if not path.exists():
        return False
    try:
        return path.stat().st_size < LFS_POINTER_MAX_BYTES
    except OSError:
        return False


def _try_fix_lfs_pointer(path: Path) -> bool:
    """Remove a stale LFS pointer file so a fresh DuckDB can be created.

    The real database is committed via Git LFS and should be pulled at build
    time by `git lfs pull`. If LFS fails (e.g. Render free tier without
    git-lfs), this fallback deletes the pointer and init_schema() creates an
    empty database that gets populated on the next successful ingestion run.
    """
    if not _is_lfs_pointer(path):
        return False
    try:
        size = path.stat().st_size
        path.unlink(missing_ok=True)
        logger.warning(
            "Removed stale LFS pointer at %s (size=%s bytes). "
            "A fresh DuckDB will be initialized.",
            path, size,
        )
        return True
    except OSError as e:
        logger.warning("Could not remove LFS pointer %s: %s", path, e)
        return False


# DuckDB refuses to open the same file twice in one process with a different
# configuration (read-only vs read-write). Callers get here in exactly that
# state whenever an ingest run holds a writable handle and a helper opens a
# second connection, so the config clash has to be handled, never raised.
_CONFIG_MISMATCH_MARKERS = (
    "different configuration",
    "different config",
)
# A probe must never fight the write lock held by another process.
_LOCK_CONTENTION_MARKERS = (
    "conflicting lock",
    "could not set lock",
    "being used by another",
    "another process",
)
# Markers of a database-wide problem: only these justify deleting the file.
# An index-only fault must never get here - dropping the warehouse is the one
# mistake there is no coming back from.
_CORRUPTION_MARKERS = (
    "index corruption", "database has been invalidated",
    "corrupt", "cannot be used",
)
# Markers of an inconsistent ART index. Recoverable by rebuilding the table.
_INDEX_FAULT_MARKERS = (
    "failed to delete",
    "invalidated",
    "index",
)
# Files already probed successfully by this process: the first open verifies
# the ART indexes, later opens share the same instance and skip the scan.
_INTEGRITY_CHECKED: set[str] = set()


def _integrity_checked(path: Path) -> bool:
    return str(path) in _INTEGRITY_CHECKED


def _mark_integrity_checked(path: Path) -> None:
    _INTEGRITY_CHECKED.add(str(path))


def _matches(err: Exception, markers: tuple) -> bool:
    msg = str(err).lower()
    return any(kw in msg for kw in markers)


def _is_config_mismatch(err: Exception) -> bool:
    return _matches(err, _CONFIG_MISMATCH_MARKERS)


def _is_lock_contention(err: Exception) -> bool:
    return _matches(err, _LOCK_CONTENTION_MARKERS)


def _is_index_fault(err: Exception) -> bool:
    """True when an error means one of the table's ART indexes is inconsistent."""
    return _matches(err, _INDEX_FAULT_MARKERS)


def _drop_corrupt_database(path: Path) -> None:
    """Delete an unusable DuckDB file plus its WAL/tmp siblings."""
    path.unlink(missing_ok=True)
    for suffix in (".wal", ".tmp"):
        path.with_suffix(path.suffix + suffix).unlink(missing_ok=True)


# Must mirror the `prices` DDL in init_schema(). The rebuild needs its own copy
# because CREATE TABLE IF NOT EXISTS cannot be re-pointed at a temp name.
_PRICES_DDL = """
    CREATE TABLE IF NOT EXISTS {table} (
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
"""

_PRICES_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_prices_commodity ON prices(commodity)",
    "CREATE INDEX IF NOT EXISTS idx_prices_date ON prices(arrival_date)",
    "CREATE INDEX IF NOT EXISTS idx_prices_state ON prices(state)",
)


def rebuild_prices_table(conn) -> dict:
    """Rebuild `prices`, and with it every ART index over it.

    The only reliable repair for an inconsistent index: DuckDB re-derives the
    index from the data, so whatever the old index had wrong is discarded. The
    lowest id wins for each business key, so ids stay stable and the table keeps
    being 1:1 with the records that were already there.
    """
    before = int(conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] or 0)

    conn.execute("DROP TABLE IF EXISTS prices_rebuild")
    conn.execute(_PRICES_DDL.format(table="prices_rebuild"))
    conn.execute(
        """
        INSERT INTO prices_rebuild
            (id, state, district, market, commodity, variety, grade,
             arrival_date, min_price, max_price, modal_price)
        SELECT id, state, district, market, commodity, variety, grade,
               arrival_date, min_price, max_price, modal_price
        FROM prices
        QUALIFY row_number() OVER (
            PARTITION BY market, commodity, variety, grade, arrival_date
            ORDER BY id
        ) = 1
        """
    )
    after = int(conn.execute("SELECT COUNT(*) FROM prices_rebuild").fetchone()[0] or 0)

    conn.execute("DROP TABLE prices")
    conn.execute("ALTER TABLE prices_rebuild RENAME TO prices")
    for idx_sql in _PRICES_INDEXES:
        try:
            conn.execute(idx_sql)
        except Exception as exc:  # an index is a convenience, not a contract
            logger.warning(f"Could not recreate {idx_sql}: {exc}")

    report = {
        "rows_before": before,
        "rows_after": after,
        "rows_removed": before - after,
    }
    logger.warning(
        "Rebuilt the prices table after an index fault: %d rows kept, %d "
        "duplicate or inconsistent rows removed",
        after, report["rows_removed"],
    )
    return report


def get_connection(db_path: Optional[Path] = None, read_only: bool = False) -> "duckdb.DuckDBPyConnection":

    """Get a DuckDB connection.

    Defaults to read-write, but transparently falls back to read-only mode if

    the filesystem is read-only (e.g. Streamlit Community Cloud serves the repo

    from an immutable layer). This keeps read-only dashboard queries working

    without changing call sites.
    If the file is a stale Git LFS pointer (~100 bytes text file), removes it
    so a fresh database can be created by init_schema().

    """

    path = db_path or DB_PATH

    # Detect and clean up stale LFS pointer before DuckDB tries to open it
    _try_fix_lfs_pointer(path)

    # ── Corruption recovery ──────────────────────────────────────────
    # DuckDB's ART UNIQUE index can become invalidated during large bulk
    # inserts under memory pressure.  If the DB file is corrupted we must
    # delete it so init_schema() can create a fresh one.

    if path.exists() and not _integrity_checked(path):

        try:

            probe = duckdb.connect(str(path), read_only=True)
            probe.execute("SELECT 1")
            tables = [r[0] for r in probe.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
            if "prices" in tables:

                probe.execute("SELECT COUNT(*) FROM prices")

            probe.close()

            _mark_integrity_checked(path)
        except Exception as probe_err:
            try: probe.close()
            except Exception: pass
            if _is_index_fault(probe_err):
                # A bad index is repairable with rebuild_prices_table(); the
                # previous code deleted the entire warehouse here instead.
                logger.error(
                    "DuckDB index fault at %s (%s). The file is kept - repair "
                    "it with POST /admin/rebuild-prices or rebuild_prices_table()",
                    path, probe_err,
                )
                _mark_integrity_checked(path)
            elif _matches(probe_err, _CORRUPTION_MARKERS):
                logger.warning(
                    "Corrupted DuckDB detected (%s); deleting %s",
                    probe_err, path,
                )
                _drop_corrupt_database(path)
            elif (_is_config_mismatch(probe_err)
                  or _is_lock_contention(probe_err)):
                # Another handle (or another process) already owns this file.
                # Skip the probe instead of failing the whole run; the real
                # open below reports any genuine problem.
                logger.info(
                    "Skipping DuckDB integrity probe for %s (%s)", path, probe_err,
                )
                _mark_integrity_checked(path)
            else:

                raise

    try:

        path.parent.mkdir(parents=True, exist_ok=True)

        conn = duckdb.connect(str(path), read_only=read_only)        # DuckDB's ART index needs real headroom. This used to hard-code
        # `SET memory_limit = '200MB'` - a comment claiming it was increasing
        # the limit while doing the opposite - and a bulk load that ran out of
        # room under that cap is what left the UNIQUE(market, commodity,
        # variety, grade, arrival_date) index inconsistent. Every later write
        # to those keys then failed with "Failed to delete all rows from
        # index", which is how ingestion stayed dead from July until now.
        # DuckDB's own default (80% of detected RAM) is the safe choice, so the
        # limit is only overridden when an operator sets it explicitly.
        #   The container is 512 MB and DuckDB sizes its default limit from
        #   the *host* RAM it can see, which is why the cap exists at all. The
        #   fix is a cap that leaves the index room to work plus a spill
        #   directory on the volume, not a cap of 200 MB with nowhere to go.
        if not read_only:

            limit = os.environ.get("MANDIIQ_MEMORY_LIMIT", "384MB").strip()
            try:

                conn.execute(f"SET memory_limit = '{limit}'")

            except Exception:

                logger.warning(f"Ignoring invalid MANDIIQ_MEMORY_LIMIT={limit!r}")

            try:

                # Spill to the data volume rather than to /tmp, which on these
                # hosts is small or memory-backed.
                spill_dir = os.environ.get("MANDIIQ_SPILL_DIR") or str(path.parent / "duckdb_spill")
                os.makedirs(spill_dir, exist_ok=True)
                conn.execute(f"SET temp_directory = '{spill_dir}'")
                conn.execute("SET threads = 2")

            except Exception as exc:

                logger.warning(f"Could not configure the DuckDB spill directory: {exc}")


        return conn

    except Exception as exc:

        if _is_config_mismatch(exc):
            # A handle to this file is already open in this process with the
            # opposite access mode. DuckDB forbids mixing configurations, so
            # join the live instance rather than failing the call.
            logger.debug("Joining the existing DuckDB instance: %s", exc)
            try:
                return duckdb.connect(str(path), read_only=not read_only)
            except Exception as join_err:
                logger.warning("Could not join existing DuckDB instance: %s", join_err)

        logger.warning("Read-write open failed, trying read-only: %s", exc)

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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS data_lineage (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_prices'),
            source_type VARCHAR NOT NULL,
            source_name VARCHAR NOT NULL,
            resource_id VARCHAR,
            batch_fingerprint VARCHAR,
            row_count INTEGER DEFAULT 0,
            n_new INTEGER DEFAULT 0,
            commodity_list VARCHAR,
            state_list VARCHAR,
            first_date DATE,
            last_date DATE,
            ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            metadata_json VARCHAR
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS freshness_by_commodity (
            commodity VARCHAR PRIMARY KEY,
            latest_date DATE,
            earliest_date DATE,
            row_count INTEGER DEFAULT 0,
            n_districts INTEGER DEFAULT 0,
            n_states INTEGER DEFAULT 0,
            source_type VARCHAR,
            source_name VARCHAR,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Create indexes

    for idx_sql in [

        "CREATE INDEX IF NOT EXISTS idx_prices_commodity ON prices(commodity)",

        "CREATE INDEX IF NOT EXISTS idx_prices_date ON prices(arrival_date)",

        "CREATE INDEX IF NOT EXISTS idx_prices_state ON prices(state)",

        "CREATE INDEX IF NOT EXISTS idx_rainfall_subdiv ON rainfall(sub_division)",

        "CREATE INDEX IF NOT EXISTS idx_rainfall_year_month ON rainfall(year, month)",

        "CREATE INDEX IF NOT EXISTS idx_lineage_type ON data_lineage(source_type)",
        "CREATE INDEX IF NOT EXISTS idx_lineage_ingested ON data_lineage(ingested_at)",
        "CREATE INDEX IF NOT EXISTS idx_lineage_resource ON data_lineage(resource_id)",
        "CREATE INDEX IF NOT EXISTS idx_freshness_commodity ON freshness_by_commodity(commodity)",

    ]:

        try:

            conn.execute(idx_sql)

        except Exception:

            pass

def find_duplicate_price_keys(conn) -> int:
    """Count business keys that appear more than once in `prices`.

    UNIQUE(market, commodity, variety, grade, arrival_date) cannot permit these,
    so a non-zero count is proof that the index is no longer enforcing the
    constraint - the state that makes writes fail with "Failed to delete all
    rows from index". Rows with a NULL variety or grade are excluded because
    SQL uniqueness treats NULLs as distinct, so those duplicates are legal.
    """
    row = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT market, commodity, variety, grade, arrival_date
            FROM prices
            WHERE variety IS NOT NULL AND grade IS NOT NULL
            GROUP BY market, commodity, variety, grade, arrival_date
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()
    return int(row[0] or 0)


def ensure_price_index(conn) -> dict:
    """Rebuild the prices table when its unique index has stopped working.

    Cheap enough to call on boot: duplicates are impossible while the index is
    healthy, so the common case is one aggregate query that finds nothing.
    """
    duplicates = find_duplicate_price_keys(conn)
    if not duplicates:
        return {"duplicates": 0, "rebuilt": False}
    logger.warning(
        "Found %d duplicate price keys - the UNIQUE index is not enforcing, "
        "rebuilding the prices table", duplicates,
    )
    report = rebuild_prices_table(conn)
    report["duplicates"] = duplicates
    report["rebuilt"] = True
    report["duplicates_after"] = find_duplicate_price_keys(conn)
    return report


def upsert_prices(conn, records: list[dict]) -> int:

    """Bulk upsert price records - idempotent, never duplicates."""

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

    # Parse dates explicitly (day-first) and reject impossible ones.
    # pd.to_datetime guesses month-first for "12/09/2026", which is how future
    # dated rows used to reach the warehouse; see mandi_rdd.core.dates.
    if "arrival_date" in df.columns:
        parsed = [parse_arrival_date(v) for v in df["arrival_date"]]
        df["arrival_date"] = [iso for iso in parsed]
        n_rejected = sum(1 for iso in parsed if iso is None)
        if n_rejected:
            logger.warning(
                "upsert_prices: dropped %d/%d records with missing, unparseable "
                "or future arrival dates",
                n_rejected, len(parsed),
            )
            df = df[df["arrival_date"].notna()]
        if df.empty:
            logger.warning("upsert_prices: no rows left after date validation")
            return 0

    # Register temp table and INSERT OR IGNORE via DuckDB

    # INSERT OR IGNORE tolerated a repeated business key inside one batch, so
    # the batch has to be deduplicated before the anti-join runs - the join
    # cannot see rows its own statement has not inserted yet.
    business_key = ["market", "commodity", "variety", "grade", "arrival_date"]
    before_dedupe = len(df)
    df = df.drop_duplicates(subset=business_key, keep="first")
    if len(df) != before_dedupe:
        logger.info(
            f"upsert_prices: collapsed {before_dedupe - len(df)} repeated key(s) "
            f"within the batch"
        )

    # Anti-join rather than INSERT OR IGNORE. Both are idempotent, but OR IGNORE
    # relies on the UNIQUE index's conflict path - the exact machinery that was
    # failing - while NOT EXISTS only ever reads the index.
    insert_sql = """
        INSERT INTO prices
            (state, district, market, commodity, variety, grade,
             arrival_date, min_price, max_price, modal_price)
        SELECT
            state, district, market, commodity, variety, grade,
            arrival_date, min_price, max_price, modal_price
        FROM _new_prices n
        WHERE NOT EXISTS (
            SELECT 1 FROM prices p
            WHERE p.market = n.market
              AND p.commodity = n.commodity
              AND p.variety IS NOT DISTINCT FROM n.variety
              AND p.grade IS NOT DISTINCT FROM n.grade
              AND p.arrival_date = n.arrival_date
        )
    """

    def _unregister() -> None:
        # A statement that failed with a FATAL error can leave the connection
        # unable to answer anything else, including this.
        try:
            conn.unregister("_new_prices")
        except Exception:
            pass

    conn.register("_new_prices", df)

    try:
        result = conn.execute(insert_sql)
    except Exception as exc:
        _unregister()
        if not _is_index_fault(exc):
            raise
        logger.error(
            f"Price insert hit an inconsistent index ({exc}); rebuilding the "
            f"prices table and retrying once"
        )
        rebuild_prices_table(conn)
        conn.register("_new_prices", df)
        result = conn.execute(insert_sql)

    _unregister()

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

        HAVING COUNT(*) >= 1

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

def get_distinct_commodities(conn) -> list[str]:

    """Get list of distinct commodities in the database."""

    result = conn.execute("SELECT DISTINCT commodity FROM prices ORDER BY commodity").fetchdf()

    return result["commodity"].tolist() if len(result) > 0 else []

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


# ── Data Lineage ──


def record_lineage_batch(
    conn,
    source_type: str,
    source_name: str,
    resource_id: str = None,
    row_count: int = 0,
    n_new: int = 0,
    records: list[dict] = None,
    metadata: dict = None,
) -> int:
    """Record a data lineage entry for a batch of ingested records.

    Args:
        conn: DuckDB connection.
        source_type: 'api', 'csv', 'ashoka', 'varietywise', 'historical_backfill'
        source_name: Human-readable source description.
        resource_id: data.gov.in resource UUID or CSV filename.
        row_count: Total rows in the batch.
        n_new: Rows that were actually inserted (post-dedup).
        records: The actual record dicts (used to compute fingerprint + dates).
        metadata: Optional extra JSON-serializable metadata.

    Returns:
        The id of the inserted lineage row.
    """
    import hashlib, json

    # Compute batch fingerprint from a hash of the records
    fingerprint = None
    first_date = None
    last_date = None
    commodities = set()
    states = set()

    if records:
        payload = json.dumps([{k: v for k, v in r.items() if k in (
            "commodity", "state", "arrival_date")} for r in records[:100]],
            sort_keys=True, default=str)
        fingerprint = hashlib.sha256(payload.encode()).hexdigest()[:16]

        for r in records:
            c = r.get("commodity")
            s = r.get("state")
            ad = r.get("arrival_date")
            if c:
                commodities.add(str(c).title())
            if s:
                states.add(str(s).title())
            if ad:
                try:
                    d = pd.to_datetime(ad)
                    if first_date is None or d < first_date:
                        first_date = d
                    if last_date is None or d > last_date:
                        last_date = d
                except Exception:
                    pass

    commodity_str = ", ".join(sorted(commodities)[:50])
    state_str = ", ".join(sorted(states)[:20])
    meta_str = json.dumps(metadata) if metadata else None

    result = conn.execute("""
        INSERT INTO data_lineage
            (source_type, source_name, resource_id, batch_fingerprint,
             row_count, n_new, commodity_list, state_list,
             first_date, last_date, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
    """, [
        source_type, source_name, resource_id, fingerprint,
        row_count, n_new, commodity_str, state_str,
        first_date, last_date, meta_str,
    ])
    row_id = result.fetchone()[0]

    # Refresh freshness_by_commodity for each commodity in this batch
    for comm in commodities:
        _refresh_freshness(conn, comm)

    logger.info(
        "Lineage: %s/%s - %d rows (%d new), %d commodities, %s → %s",
        source_type, source_name, row_count, n_new,
        len(commodities), first_date, last_date,
    )
    return row_id


def _refresh_freshness(conn, commodity: str):
    """Update the freshness_by_commodity row for a single commodity."""
    row = conn.execute("""
        SELECT
            MAX(arrival_date) AS latest_date,
            MIN(arrival_date) AS earliest_date,
            COUNT(*) AS row_count,
            COUNT(DISTINCT district) AS n_districts,
            COUNT(DISTINCT state) AS n_states
        FROM prices
        WHERE LOWER(commodity) = LOWER(?)
    """, [commodity]).fetchone()

    if not row or row[0] is None:
        return

    # Find the most recent source that contributed to this commodity
    source_row = conn.execute("""
        SELECT source_type, source_name
        FROM data_lineage
        WHERE commodity_list LIKE '%' || ? || '%'
        ORDER BY ingested_at DESC LIMIT 1
    """, [commodity]).fetchone()

    source_type = source_row[0] if source_row else "unknown"
    source_name = source_row[1] if source_row else ""

    # DuckDB's ON CONFLICT DO UPDATE SET doesn't support
    # CURRENT_TIMESTAMP as an expression in all versions.
    # Use a two-step UPSERT: DELETE + INSERT.
    conn.execute("DELETE FROM freshness_by_commodity WHERE commodity = ?", [commodity])
    conn.execute("""
        INSERT INTO freshness_by_commodity
            (commodity, latest_date, earliest_date, row_count,
             n_districts, n_states, source_type, source_name,
             updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, now())
    """, [
        commodity, row[0], row[1], row[2], row[3], row[4],
        source_type, source_name,
    ])


def get_freshness(conn, commodity: str = None) -> list[dict]:
    """Get freshness stats per commodity. Optionally filter by commodity name."""
    query = "SELECT * FROM freshness_by_commodity"
    params = []
    if commodity:
        query += " WHERE LOWER(commodity) = LOWER(?)"
        params.append(commodity)
    query += " ORDER BY latest_date DESC NULLS LAST"
    df = conn.execute(query, params).fetchdf()
    if len(df) == 0:
        return []
    return df.to_dict("records")


def get_lineage(
    conn,
    source_type: str = None,
    limit: int = 50,
    since_hours: int = None,
) -> list[dict]:
    """Get recent lineage entries.

    Args:
        conn: DuckDB connection.
        source_type: Optional filter ('api', 'csv', 'ashoka', etc.).
        limit: Max rows to return.
        since_hours: Only return entries from the last N hours.
    """
    query = "SELECT * FROM data_lineage"
    conditions = []
    params = []

    if source_type:
        conditions.append("source_type = ?")
        params.append(source_type)
    # DuckDB supports INTERVAL with a bind parameter via CAST
    if since_hours is not None:
        conditions.append("ingested_at >= CURRENT_TIMESTAMP - (INTERVAL '1' HOUR) * ?")
        params.append(since_hours)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY ingested_at DESC LIMIT ?"
    params.append(limit)

    df = conn.execute(query, params).fetchdf()
    if len(df) == 0:
        return []
    return df.to_dict("records")

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

