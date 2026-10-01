import functools
import os
import threading

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
# These are deliberately specific: the previous list included the bare word
# "index", so any error message that merely mentioned an index (including the
# status file's own "index_health" key and unrelated SQL) was classified as a
# fault. The marker gates a full table rebuild, so a false positive is not
# harmless - it is expensive and, when the rebuild was not atomic, destructive.
_INDEX_FAULT_MARKERS = (
    "failed to delete all rows from index",
    "database has been invalidated",
    "database instance is invalidated",
    "index corruption",
)
# Files already probed successfully by this process: the first open verifies
# the ART indexes, later opens share the same instance and skip the scan.
_INTEGRITY_CHECKED: set[str] = set()


def _integrity_checked(path: Path) -> bool:
    return str(path) in _INTEGRITY_CHECKED


def _mark_integrity_checked(path: Path) -> None:
    _INTEGRITY_CHECKED.add(str(path))


def reset_connection_state(path: Optional[Path] = None) -> None:
    """Forget that a database file has been verified.

    Callers that replace the file under the process - a restore from backup -
    must call this, otherwise the next get_connection() skips the integrity
    probe because the *old* file was verified once, and then hands out
    connections against stale state.
    """
    target = str(path or DB_PATH)
    _INTEGRITY_CHECKED.discard(target)


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


# How far a rebuild copy may fall short of the original before it is treated
# as a failed copy rather than a dedupe. A real dedupe removes a handful of
# rows; a copy interrupted by an out-of-memory kill can be arbitrarily short.
_REBUILD_MIN_COPY_RATIO = 0.9
# Rows copied per INSERT ... SELECT by the rebuild, one statement per window.
# Bounded so a 1.6M-row table does not need its working set in memory all at
# once, and so a window that fails costs that window rather than the whole copy.
_REBUILD_BATCH_ROWS = 200_000

# The two copies a rebuild can make, both plain INSERTs. `ON CONFLICT DO
# NOTHING` used to be on both of them and it is what made the rebuild
# unshippable - see the measurements in rebuild_prices_table().
#
# The plain one is a straight stream over one id window. The dedupe one walks
# business-key buckets instead: every row of a key hashes to the same bucket, so
# the collapse is global, where id windows would put one copy of a key in a
# later window and the constraint would reject it instead of collapsing it.
_REBUILD_COLUMNS = (
    "id, state, district, market, commodity, variety, grade, "
    "arrival_date, min_price, max_price, modal_price"
)
_REBUILD_PLAIN_INSERT = f"""
    INSERT INTO prices_rebuild ({_REBUILD_COLUMNS})
    SELECT {_REBUILD_COLUMNS}
    FROM prices
    WHERE id > ? AND id <= ?
    ORDER BY id
"""
_REBUILD_DEDUPE_INSERT = f"""
    INSERT INTO prices_rebuild ({_REBUILD_COLUMNS})
    SELECT {_REBUILD_COLUMNS}
    FROM (
        SELECT *, row_number() OVER (
            PARTITION BY market, commodity, variety, grade, arrival_date
            ORDER BY id
        ) AS _rn
        FROM prices
        WHERE hash(market, commodity, variety, grade, arrival_date) % ? = ?
    )
    WHERE _rn = 1
    ORDER BY id
"""


# ── Warehouse-wide maintenance ───────────────────────────────────────────────
#
# `/health` is the platform's liveness probe, and it samples warehouse counts.
# A probe that has to queue behind a table copy is a probe the platform reads as
# a dead container - which is how a repair in flight used to take the whole
# service off the edge. Anything that holds the warehouse for more than a moment
# marks itself here, and /health serves its last snapshot instead of sampling.
_MAINTENANCE_THREADS: set = set()


def maintenance_in_progress() -> bool:
    """Is a warehouse-wide write in flight on this process?"""
    return bool(_MAINTENANCE_THREADS)


def _during_maintenance(fn):
    """Mark a warehouse-wide write for as long as it runs.

    Keyed by thread, so the flag cannot outlive the operation that set it - a
    raise inside the operation still clears it - and two callers cannot clear
    each other's mark.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        thread_id = threading.get_ident()
        _MAINTENANCE_THREADS.add(thread_id)
        try:
            return fn(*args, **kwargs)
        finally:
            _MAINTENANCE_THREADS.discard(thread_id)

    return wrapper


# DuckDB sizes its default memory limit from the host's RAM, not the
# container's cgroup limit. On a 512 MB instance that means it allocates until
# the kernel kills the process - which is what got the first restore attempt
# OOM-killed, and what a full-table rebuild does too if it is left unbounded.
# 192MB leaves room for the serving process, the gzipped backup buffers and the
# page cache on a 512 MB instance; the same cap the historical ingest uses.
_BULK_MEMORY_LIMIT = os.environ.get("MANDIIQ_DUCKDB_MEMORY_LIMIT", "192MB")


def _configure_bulk_memory(conn) -> None:
    """Bound and spill a full-table operation so it cannot be OOM-killed.

    A hard memory limit plus a temp directory makes DuckDB use disk for the
    sorts and intermediate tables a rebuild needs. Without it the rebuild is
    free to allocate until the container dies - the failure mode that has cost
    this warehouse its rows more than once. Single-threaded and no insertion
    order, because both cut peak memory and the rebuild does not care about
    row order (the final table's order comes from ORDER BY id).
    """
    # The container's own scratch space first: it is far larger than the
    # volume, and filling the volume would break the database itself. The
    # volume is only a fallback for hosts whose /tmp is tiny or read-only.
    import tempfile
    spill_dir = None
    for candidate in (
        Path(tempfile.gettempdir()) / "mandiiq_duckdb_spill",
        Path(DB_PATH).parent / ".duckdb_spill",
    ):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            spill_dir = candidate
            break
        except OSError:
            continue

    statements = [
        f"SET memory_limit='{_BULK_MEMORY_LIMIT}'",
        "SET threads=1",
        "SET preserve_insertion_order=false",
    ]
    if spill_dir is not None:
        statements.append(f"SET temp_directory='{spill_dir}'")
    for statement in statements:
        try:
            conn.execute(statement)
        except Exception as exc:  # a missing limit is not worth aborting a repair
            logger.warning(f"Could not apply {statement!r}: {exc}")


@_during_maintenance
def rebuild_prices_table(conn) -> dict:
    """Rebuild `prices`, and with it every ART index over it - atomically.

    The only reliable repair for an inconsistent index: DuckDB re-derives the
    index from the data, so whatever the old index had wrong is discarded. The
    lowest id wins for each business key, so ids stay stable and the table keeps
    being 1:1 with the records that were already there.

    This used to be destructive: the staging copy was inserted, committed, then
    `prices` was dropped while the rename happened afterwards. A copy that ran
    out of memory was therefore published as an empty table, because the DROP
    had already committed by the time the failure surfaced. Nothing is copied
    into `prices` itself now: the copy goes to a staging table and is verified
    before the swap, and the swap - the drop and the rename - is the one
    statement sequence that has to be atomic, so it is the one that runs in a
    transaction. A copy that dies half-way leaves the original table exactly as
    it is.

    How long the copy takes is a recovery feature, not a detail, and so is what
    it does to memory. Measured on 2026-10-01 against a 2,000,001-row
    warehouse, under the memory cap and the single thread this sets:

      * a 200k-row window copied in 0.9s, and the next one in 0.9s;
      * the same two windows with `ON CONFLICT DO NOTHING` took 0.9s and then
        132s, growing with the table (the conflict check probes the unique
        index once per row, ~0.65ms/row);
      * a copy into a constraint-free table in 0.4s per window;
      * and copying every window inside ONE transaction - which is what this
        function did - ran out of memory at the eighth window: `could not
        allocate block of size 256.0 KiB (183.0 MiB/183.1 MiB used)` against
        this function's own 192MB cap;
      * one statement over the whole table does not fit either, which is why the
        dedupe copy walks key buckets instead of the whole table at once.

    So a 2M-row rebuild was tens of minutes of one-thread index probing that
    also ran the warehouse into its memory cap, and that is the repair the
    operator reaches for while the index is already broken: it kept being
    killed mid-copy, the fault marker stayed put, and `/health` was starved long
    enough for the platform to de-route the container. The copy clauses are now
    plain INSERTs, committed one window at a time. The plain copy is safe
    because a healthy-looking index is checked for duplicates first (and    the constraint would only ever see the rows it already holds); the dedupe copy
    collapses each key inside its own bucket before it is inserted, so the
    constraint has nothing to reject either way. On 2026-10-01 the whole repair
    then took 12.7s against a 2,000,001-row warehouse, keeping every row.
    """
    before = int(conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] or 0)
    _configure_bulk_memory(conn)

    # The usual fault is a corrupt index, not duplicate rows, and collapsing
    # duplicates costs a full sort per batch. Only pay for it when the broken
    # index actually let duplicates through.
    try:
        duplicates = find_duplicate_price_keys(conn)
    except Exception as exc:
        logger.warning(f"Could not count duplicate price keys before rebuilding: {exc}")
        duplicates = 0
    logger.warning(
        "Rebuilding the prices table (%d rows, %d duplicate business keys); %s",
        before, duplicates,
        "collapsing duplicates by key bucket" if duplicates else "streaming copy, no dedupe needed",
    )

    conn.execute("DROP TABLE IF EXISTS prices_rebuild")
    conn.execute(_PRICES_DDL.format(table="prices_rebuild"))

    # One window per transaction, deliberately. A single transaction around the
    # whole copy accumulates every change in the transaction's local storage and
    # runs into the memory cap (see the measurements above), and the copy does
    # not need the transaction: it writes to a staging table nobody reads, and a
    # copy that dies half-way leaves the live warehouse exactly as it was.
    if duplicates:
        # Dedupe by business-key bucket, one bucket per statement: a key has to
        # sit entirely inside one pass for the collapse to be global, and one
        # statement over the whole table does not fit in the cap either -
        # measured on 2026-10-01, 1,000,200 rows with 200 duplicate keys failed
        # with `Failed to commit: failed to pin block of size 256.0 KiB
        # (183.0 MiB/183.1 MiB used)`. The bucket count keeps a pass around a
        # quarter of a copy batch. `hash()` is defined for NULL, so a row with
        # an unknown variety or grade still lands in exactly one bucket.
        buckets = max(8, min(128, (before // _REBUILD_BATCH_ROWS) * 4))
        logger.debug(
            "Prices rebuild: collapsing duplicates across %d key buckets", buckets
        )
        for bucket in range(buckets):
            conn.execute(_REBUILD_DEDUPE_INSERT, [buckets, bucket])
    else:
        # Window boundaries come from the source table, so the copy advances
        # through the whole id space. A window shorter than one batch on its own
        # is still a complete window, which is what stops the loop from ending
        # early.
        bounds = [
            int(r[0])
            for r in conn.execute(
                "SELECT MAX(id) FROM prices GROUP BY id // ? ORDER BY 1",
                [_REBUILD_BATCH_ROWS],
            ).fetchall()
            if r[0] is not None
        ]
        last_id = 0
        for upper in bounds:
            cursor = conn.execute(_REBUILD_PLAIN_INSERT, [last_id, upper])
            copied = int(cursor.fetchone()[0] or 0)
            last_id = upper
            logger.debug("Prices rebuild: window ending at %s copied %s rows", upper, copied)

    after = int(conn.execute("SELECT COUNT(*) FROM prices_rebuild").fetchone()[0] or 0)

    # Refuse to publish a short copy. A real dedupe removes a few rows; a copy
    # killed by memory pressure can lose most of the table, and that must be
    # rejected rather than become the warehouse.
    if before and after < int(before * _REBUILD_MIN_COPY_RATIO):
        raise RuntimeError(
            f"refusing to publish a rebuild that copied {after} of {before} "
            f"rows (below {_REBUILD_MIN_COPY_RATIO:.0%} of the original)"
        )

    # The publish is the one step that has to be all-or-nothing: after it, the
    # staging copy is the warehouse.
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute("DROP TABLE prices")
        conn.execute("ALTER TABLE prices_rebuild RENAME TO prices")
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception as rollback_err:  # the connection itself may be dead
            logger.error(f"Could not roll back the prices rebuild: {rollback_err}")
        raise

    for idx_sql in _PRICES_INDEXES:
        try:
            conn.execute(idx_sql)
        except Exception as exc:  # an index is a convenience, not a contract
            logger.warning(f"Could not recreate {idx_sql}: {exc}")

    # Prove the new index works before calling the repair done. The probe uses
    # the index the way a write does, on a connection that has already survived
    # the rebuild, so a rebuild that produced another broken index is not
    # reported as a heal.
    probed = False
    try:
        probe_price_index(conn)
        probed = True
        clear_index_fault()
    except Exception as exc:
        logger.error(
            "The prices index still fails a write probe after rebuilding; "
            "leaving the fault recorded (%s)",
            exc,
        )

    report = {
        "rows_before": before,
        "rows_after": after,
        "rows_removed": before - after,
        "probed": probed,
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
        #   the *host* RAM it can see, which is why a cap exists at all. The
        #   fix is a cap with room above the index plus a spill directory on
        #   the volume, and a pipeline that never buffers more than one page -
        #   not a 200 MB cap with nowhere to spill.
        if not read_only:

            limit = os.environ.get("MANDIIQ_MEMORY_LIMIT", "256MB").strip()
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

    # Where a resumable walk of a paginated source stopped last time. Without
    # it every run restarts from offset 0 and only ever re-reads the newest
    # pages: the archive's tail is never reached no matter how many runs pass.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ingest_cursors (
            source VARCHAR PRIMARY KEY,
            cursor_offset INTEGER NOT NULL DEFAULT 0,
            total_records INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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


def get_ingest_cursor(conn, source: str) -> dict:
    """Where the previous walk of a paginated source stopped.

    Returns ``{"offset": int, "total": int}`` and treats a missing row as the
    start of a pass. The API's page ordering is not guaranteed to be stable,
    so an offset is a hint that may re-read a page - never a promise to skip
    one. Anything that changes the source (new rows land at the top) shifts
    later offsets, which is exactly why the walk has to advance monotonically
    and wrap rather than trust an offset to mean the same record twice.
    """
    try:
        row = conn.execute(
            "SELECT cursor_offset, total_records FROM ingest_cursors WHERE source = ?",
            [source],
        ).fetchone()
    except Exception:
        return {"offset": 0, "total": 0}
    if not row:
        return {"offset": 0, "total": 0}
    return {"offset": int(row[0] or 0), "total": int(row[1] or 0)}


def set_ingest_cursor(conn, source: str, offset: int, total: int = 0) -> None:
    """Persist where the next walk of `source` should start."""
    try:
        conn.execute(
            """
            INSERT INTO ingest_cursors (source, cursor_offset, total_records, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (source) DO UPDATE SET
                cursor_offset = excluded.cursor_offset,
                total_records = excluded.total_records,
                updated_at = excluded.updated_at
            """,
            [source, int(offset), int(total)],
        )
    except Exception as exc:
        logger.warning("Could not persist the %s ingest cursor: %s", source, exc)


FAULT_FLAG_NAME = "index_fault.flag"


def index_fault_flag_path() -> Optional[Path]:
    """Where the index-fault marker lives.

    Next to the database, because that directory is the mounted volume. A
    marker kept beside the code does not survive the container restart that
    the fault itself causes, which is how the repair kept missing its cue.
    """
    try:
        return Path(DB_PATH).parent / FAULT_FLAG_NAME
    except Exception:
        return None


def note_index_fault(error: object) -> bool:
    """Record that a write died on an inconsistent index. Returns True if so."""
    try:
        message = str(error)
    except Exception:
        return False
    if not _is_index_fault(Exception(message)):
        return False
    path = index_fault_flag_path()
    try:
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(message[:2000], encoding="utf-8")
        logger.error(
            "An index fault was recorded%s; the prices table will be rebuilt "
            "before the next write",
            f" at {path}" if path else "",
        )
        return True
    except OSError as exc:
        logger.warning(f"Could not record the index fault: {exc}")
        return True


def flag_index_fault(reason: str) -> None:
    """Record a fault unconditionally.

    Used *before* a write probe: if the probe is what kills this process, the
    marker is already on the volume and the next process rebuilds instead of
    rediscovering the fault the hard way.
    """
    path = index_fault_flag_path()
    try:
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(reason)[:2000], encoding="utf-8")
    except OSError as exc:
        logger.warning(f"Could not record the index fault: {exc}")


def index_fault_flagged() -> bool:
    """Is there a recorded index fault that has not been repaired yet?"""
    path = index_fault_flag_path()
    try:
        return bool(path is not None and path.exists())
    except OSError:
        return False


PROBE_MARKET = "__mandiiq_index_probe__"


def probe_price_index(conn) -> None:
    """Find out whether the unique index still works by using it.

    A read cannot tell a working index from one that has stopped enforcing, so
    the only honest test is to write. The row uses market names nobody trades
    under and is removed again in the same call, so a healthy warehouse is
    left exactly as it was; a broken one raises here, which is the point.
    """
    conn.execute(
        """
        INSERT INTO prices
            (state, district, market, commodity, variety, grade, arrival_date,
             min_price, max_price, modal_price)
        VALUES
            ('__probe__', '__probe__', ?, '__probe__', '__probe__', '__probe__',
             CURRENT_DATE, 0.0, 0.0, 0.0)
        """,
        [PROBE_MARKET],
    )
    # The delete is the other half of the test: "Failed to delete all rows from
    # index" is raised here, not by the insert.
    conn.execute("DELETE FROM prices WHERE market = ?", [PROBE_MARKET])


def clear_index_fault() -> None:
    """Drop the marker once the table has been rebuilt."""
    path = index_fault_flag_path()
    try:
        if path is not None:
            path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(f"Could not clear the index-fault marker: {exc}")


def ensure_price_index(conn, force: bool = False) -> dict:
    """Rebuild the prices table when its unique index has stopped working.

    Cheap enough to call on boot: duplicates are impossible while the index is
    healthy, so the common case is one aggregate query that finds nothing.
    ``force`` skips that check - used when a recorded fault says the index is
    broken in a way a read cannot see.
    """
    duplicates = 0 if force else find_duplicate_price_keys(conn)
    if not duplicates and not force:
        return {"duplicates": 0, "rebuilt": False, "trigger": None}
    if force:
        logger.error(
            "Rebuilding the prices table: a previous write died on an "
            "inconsistent index"
        )
    else:
        logger.warning(
            "Found %d duplicate price keys - the UNIQUE index is not enforcing, "
            "rebuilding the prices table", duplicates,
        )
    report = rebuild_prices_table(conn)
    report["duplicates"] = duplicates
    report["rebuilt"] = True
    report["trigger"] = "recorded_index_fault" if force else "duplicate_keys"
    report["duplicates_after"] = find_duplicate_price_keys(conn)
    if report.get("probed"):
        # rebuild_prices_table already proved the new index with a write probe.
        clear_index_fault()
    return report


def verify_price_index(conn, probe: bool = True) -> dict:
    """Decide whether the prices index needs rebuilding, and rebuild it.

    Three sources of evidence, cheapest first:

      1. a marker recorded by an earlier run (survives restarts on the volume),
      2. duplicate business keys, which a working UNIQUE index cannot permit,
      3. a write probe that uses the index - the only way to see a fault that
         leaves the index self-consistent.

    The marker is written *before* the probe, so a process that dies probing
    teaches its successor to rebuild rather than repeat the failure. On the
    healthy path the marker is cleared immediately and the warehouse keeps its
    rows.
    """
    if index_fault_flagged():
        return ensure_price_index(conn, force=True)

    duplicates = find_duplicate_price_keys(conn)
    if duplicates:
        return ensure_price_index(conn)

    if not probe:
        return {"duplicates": 0, "rebuilt": False, "trigger": None, "probed": False}

    flag_index_fault("write probe did not complete")
    try:
        probe_price_index(conn)
    except Exception as exc:
        logger.error(
            f"The prices index failed a write probe ({exc}); rebuilding the table"
        )
        report = rebuild_prices_table(conn)
        report["duplicates"] = 0
        report["rebuilt"] = True
        report["trigger"] = "write_probe"
        report["duplicates_after"] = find_duplicate_price_keys(conn)
        if report.get("probed"):
            clear_index_fault()
        return report

    clear_index_fault()
    return {"duplicates": 0, "rebuilt": False, "trigger": None, "probed": True}


def heal_price_index(conn) -> dict:
    """Repair the index if anything says it needs it, else do nothing."""
    if index_fault_flagged():
        return ensure_price_index(conn, force=True)
    return ensure_price_index(conn)


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
        # Record the fault on the volume before attempting anything else. A
        # "Failed to delete all rows from index" error is fatal - DuckDB
        # invalidates the whole database instance, so the connection this
        # failed on can never repair or retry - and the marker is what tells
        # the next run to rebuild.
        note_index_fault(exc)
        logger.error(
            f"Price insert hit an inconsistent index ({exc}); rebuilding the "
            f"prices table and retrying once"
        )
        try:
            rebuilt = rebuild_prices_table(conn)
        except Exception as rebuild_err:
            # Dead connection or a failed rebuild: nothing more can be done
            # with this connection. The recorded fault makes the next run's
            # pre-flight heal the table before it writes.
            logger.error(
                "Could not rebuild the prices table on this connection (%s); "
                "the recorded fault will be healed before the next run writes",
                rebuild_err,
            )
            raise
        logger.info(
            "Rebuilt %s rows in place (%s removed); retrying the insert once",
            rebuilt.get("rows_after"), rebuilt.get("rows_removed"),
        )
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

