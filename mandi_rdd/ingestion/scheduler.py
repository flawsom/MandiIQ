"""
MandiRDD - daily ingestion scheduler.

Runs once daily (matching the API's own update cadence):
1. Pulls fresh mandi prices from data.gov.in
2. Pulls/updates rainfall data
3. Stores both in SQLite via upsert
4. Triggers RDD recomputation

Use cases:
- Python: scheduler.run_once()
- CLI: python -m mandi_rdd.ingestion.scheduler
- Cron: 0 6 * * * cd /app && python -m mandi_rdd.ingestion.scheduler
"""

import datetime
import json
import os
import sys
import time
import logging
import threading
from pathlib import Path
from typing import Optional

# Defensive: ensure stdout/stderr never crash on Unicode (e.g. cp1252 consoles)
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from mandi_rdd.core.metrics import pipeline_metrics
from mandi_rdd.storage.duckdb_store import (
    get_connection,
    init_schema,
    upsert_prices,
    upsert_rainfall,
    save_rdd_result,
)
# iter_price_pages is imported inside the pipeline step: the fetch layer is
# optional at import time so the scheduler can still run RDD on existing data
# when the price source is unavailable.
from mandi_rdd.ingestion.ingest_historical_csv import run_auto as run_historical_backfill
from mandi_rdd.ingestion.fetch_ndvi import fetch_and_store_all_ndvi
from mandi_rdd.ingestion.fetch_rainfall import (
    fetch_and_store_all_rainfall,
    load_district_subdivision_map,
)
from mandi_rdd.analysis.rdd_engine import run_rdd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mandi_rdd.scheduler")


_RUN_LOCK = threading.Lock()


def ingestion_running() -> bool:
    """True while a pipeline run is in flight in this process."""
    return _RUN_LOCK.locked()


def _env_float(name: str, default: float) -> float:
    """Read a float from the environment, falling back on anything unusable."""
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# ── Run scope ───────────────────────────────────────────────────────────────
#
# The in-container scheduler and an external workflow trigger the same
# pipeline, but they do not have the same budget. The analysis half of a run
# (RDD over every commodity, then Prophet and XGBoost training per commodity)
# is a recompute over data the price and rainfall steps have already stored,
# and it needs far more memory than a free-tier container has: on 2026-10-01
# the primary instance restarted in a loop while its own tick ran it, and the
# platform edge answered `503 no healthy upstream` between restarts.
#
# So a run declares a scope. `light` keeps the warehouse current - storage
# integrity, arrival dates, prices, rainfall, state backfill - and deliberately
# leaves the analysis, the satellite fetch and the index rebuild to a run that
# asked for them. `full` is the whole pipeline, for a caller with the memory
# for it: a larger instance, CI, or a laptop.
SCOPE_LIGHT = "light"
SCOPE_FULL = "full"

# Steps a light run leaves out, by their names in the summary.
LIGHT_SKIPPED_STEPS = (
    "fetch_ndvi", "rdd_analysis", "forecast_training",
    "classifier_training", "nightly_narratives",
)


def resolve_scope(value: object = None) -> str:
    """Read a run scope from a caller, `MANDIIQ_REFRESH_SCOPE`, or the default.

    Unknown values fall back to light rather than guessing: the cost of being
    wrong is a container that restarts, and the cost of being light is an
    analysis that a full run recomputes later.
    """
    raw = value if value is not None else os.environ.get("MANDIIQ_REFRESH_SCOPE")
    text = str(raw or "").strip().lower()
    if text in ("full", "all", "heavy", "complete"):
        return SCOPE_FULL
    if text in ("", "light", "core", "safe"):
        return SCOPE_LIGHT
    logger.warning("Unknown refresh scope %r; running %s", raw, SCOPE_LIGHT)
    return SCOPE_LIGHT


# ── Bookkeeping that survives the restart it describes ──────────────────────
#
# A run leaves a marker beside the database while it is in flight and clears it
# when it returns. A marker still there at boot means the previous process died
# mid-run - an OOM kill, or the platform killing a container whose health probe
# stopped answering - which is the only honest evidence that the work itself is
# what killed it. /health reports it, and the next boot waits longer before
# trying again, so a fatal run cannot repeat every 90 seconds forever.

REFRESH_STATE_NAME = "refresh_state.json"


def refresh_state_path() -> Optional[Path]:
    """On the volume, beside the warehouse: that directory survives a restart."""
    try:
        from mandi_rdd.storage.duckdb_store import DB_PATH
        return Path(DB_PATH).parent / REFRESH_STATE_NAME
    except Exception:
        return None


def _read_refresh_state(path=None) -> dict:
    target = Path(path) if path is not None else refresh_state_path()
    if target is None:
        return {}
    try:
        if target.exists():
            record = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(record, dict):
                return record
    except Exception as exc:
        logger.debug("Refresh state unreadable: %s", exc)
    return {}


def _write_refresh_state(record: dict, path=None) -> None:
    target = Path(path) if path is not None else refresh_state_path()
    if target is None:
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(record, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("Could not write the refresh state: %s", exc)


def begin_refresh(scope: str, step: str = "starting", path=None) -> None:
    """Mark a run as in flight. Left behind if this process dies during it."""
    record = _read_refresh_state(path)
    record["inflight"] = {
        "scope": scope,
        "step": step,
        "pid": os.getpid(),
        "started_utc": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    _write_refresh_state(record, path)


def note_refresh_step(step: str, path=None) -> None:
    """Name the step the run is inside, so a death during it is attributable."""
    record = _read_refresh_state(path)
    inflight = record.get("inflight")
    if not isinstance(inflight, dict):
        return
    inflight["step"] = step
    record["inflight"] = inflight
    _write_refresh_state(record, path)


def end_refresh(path=None) -> None:
    """Clear the marker: this run returned, so the next boot is a clean one."""
    record = _read_refresh_state(path)
    if "inflight" not in record:
        return
    record["inflight"] = None
    _write_refresh_state(record, path)


def claim_refresh_state(path=None) -> dict:
    """Read the volume's refresh state at boot, booking an unclean run if due.

    Returns ``{"unclean_runs": n, "last_unclean": {...}|None, "inflight": bool}``.
    Called once per process, because the question it answers - "did the process
    before me die in the middle of a run?" - is about the boot, not the tick.
    """
    record = _read_refresh_state(path)
    inflight = record.get("inflight")
    left_behind = isinstance(inflight, dict) and bool(inflight)
    if left_behind:
        last_unclean = dict(inflight)
        last_unclean["detected_utc"] = (
            datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        )
        record["unclean_runs"] = int(record.get("unclean_runs") or 0) + 1
        record["last_unclean"] = last_unclean
        record["inflight"] = None
        _write_refresh_state(record, path)
        logger.error(
            "The previous run died during %r (scope=%s) and did not come back; "
            "unclean runs recorded: %d",
            last_unclean.get("step"), last_unclean.get("scope"), record["unclean_runs"],
        )
    return {
        "unclean_runs": int(record.get("unclean_runs") or 0),
        "last_unclean": record.get("last_unclean"),
        "inflight": bool(left_behind),
    }


def run_ingestion(

    filters: dict = None,

    max_records: int = None,

    skip_rainfall: bool = False,

    scope: object = None,

) -> dict:
    """Serialise pipeline runs, then execute one.

    The hourly in-process loop, POST /refresh and external schedulers all
    trigger the same pipeline. Overlapping runs fight over the DuckDB write
    lock and can corrupt the ART indexes, so concurrent calls are refused
    instead of queued.

    ``scope`` is "light" or "full" (see SCOPE_LIGHT/SCOPE_FULL); it defaults to
    `MANDIIQ_REFRESH_SCOPE`, which defaults to light.
    """
    scope_name = resolve_scope(scope)
    if not _RUN_LOCK.acquire(blocking=False):
        logger.warning("Ingestion already running; skipping this trigger")
        return {
            "status": "busy",
            "steps": {},
            "error": "ingestion already running",
        }
    begin_refresh(scope_name)
    try:
        return _run_ingestion_locked(filters, max_records, skip_rainfall, scope_name)
    except Exception as exc:
        logger.exception("Ingestion run failed: %s", exc)
        _write_ingest_status({"status": "failed", "error": str(exc), "steps": {}})
        # On the volume, not beside the code: the container is restarted by the
        # fault itself, and an image-layer marker goes with it.
        try:
            from mandi_rdd.storage.duckdb_store import note_index_fault
            note_index_fault(exc)
        except Exception:
            pass
        raise
    finally:
        end_refresh()
        _RUN_LOCK.release()


def _check_price_index(conn, light: bool) -> dict:
    """Look at the prices index, and repair it only when this run may.

    A rebuild copies the whole table, so it is the single heaviest thing the
    pipeline does. ``heal_price_index()`` forces one whenever a fault is on
    record, which is right for a run that has the memory for it and wrong for a
    tick on a free-tier container that can be killed mid-copy: the copy is
    atomic, so the warehouse survives, but the container restarts and the edge
    reports `503 no healthy upstream` while it does. A light run therefore
    reports the fault and defers it - the warehouse keeps serving stale rows,
    which is recoverable, and the container keeps serving at all.
    """
    from mandi_rdd.storage.duckdb_store import (
        clear_index_fault,
        find_duplicate_price_keys,
        heal_price_index,
        index_fault_flagged,
        rebuild_prices_table,
    )

    if light and index_fault_flagged():
        logger.warning(
            "A price-index repair is pending and this run is scope=light, so "
            "the rebuild is deferred instead of started here"
        )
        return {
            "duplicates": find_duplicate_price_keys(conn),
            "rebuilt": False,
            "trigger": None,
            "deferred": "index_repair",
            "hint": (
                "POST /admin/rebuild-prices, or POST /refresh?scope=full on an "
                "instance with the memory for it"
            ),
        }

    report = heal_price_index(conn)
    if not light and not report.get("rebuilt") and _last_run_had_index_fault():
        # heal_price_index() forces the rebuild whenever the fault marker
        # is present; this covers a marker that was lost, and the
        # previous run's recorded error is the only remaining evidence.
        logger.error(
            "The previous ingestion run died on an inconsistent prices "
            "index; rebuilding the table before touching it again"
        )
        report = rebuild_prices_table(conn)
        report["rebuilt"] = True
        report["trigger"] = "previous_run_index_fault"
        if report.get("probed"):
            clear_index_fault()
    return report


def _run_ingestion_locked(

    filters: dict = None,
    max_records: int = None,
    skip_rainfall: bool = False,
    scope: str = SCOPE_LIGHT,
) -> dict:
    """
    Run the nightly pipeline at the requested scope.

    A light run does everything that keeps the warehouse current and current-
    looking: storage and index integrity, arrival dates, prices, rainfall and
    the state backfill. It skips the satellite fetch, the index rebuild and the
    analysis recompute, and records that it did so, so nothing about a light run
    is presented as a full one.

    Returns summary dict with counts and timing.
    """
    start = time.time()
    light = scope != SCOPE_FULL
    summary = {"status": "ok", "steps": {}, "scope": scope}
    logger.info("Pipeline run starting: scope=%s", scope)

    # 1. Initialize storage
    conn = get_connection()
    init_schema(conn)
    logger.info("Storage initialized")

    # 1a. Make sure the UNIQUE index over prices is still enforcing. A bulk load
    #     that ran out of memory can leave it inconsistent, after which every
    #     write touching those keys fails and ingestion cannot make progress.
    index_report: dict = {}
    fault_flagged = False
    with pipeline_metrics.step("index_health"):
        try:
            from mandi_rdd.storage.duckdb_store import index_fault_flagged
            # The duplicate check catches an index that is no longer enforcing.
            # A fault that leaves the index self-consistent is only visible by
            # writing to it, and probing with a write is itself how the process
            # dies - so the recorded fault is the trigger. That turns a crash
            # loop into one bad run followed by a repair, and a light run
            # reports the fault instead of starting the repair.
            note_refresh_step("index_health")
            index_report = _check_price_index(conn, light=light)
            fault_flagged = index_fault_flagged()
            index_report["fault_flagged"] = fault_flagged
            summary["steps"]["index_health"] = index_report
        except Exception as e:
            logger.warning(f"Price index check skipped: {e}")

    # 1a-bis. A fault this run may not repair has to stop the *writes* too, not
    #         only the repair. Deferring the rebuild is the light-scope policy;
    #         deferring the write is what makes that policy survivable. The
    #         first write against a broken prices index raises
    #         "Failed to delete all rows from index", and DuckDB invalidates the
    #         whole database instance when it does - so every statement after it
    #         in the same run raises as well, and a run that meant to degrade
    #         instead fails. Measured on 2026-10-02: the light tick logged the
    #         fault in index_health, then died in the state backfill and
    #         reported `last_outcome: failure` while the warehouse sat frozen.
    #         Every step below that writes to `prices` is skipped instead, and
    #         the run reports the remedy that would actually fix it.
    price_writes_blocked = bool(fault_flagged) and not index_report.get("rebuilt")
    if price_writes_blocked:
        blocked_steps = [
            "historical_backfill", "fetch_prices", "prices_ceda",
            "prices_varietywise", "backfill_state",
        ]
        summary["steps_skipped"] = list(dict.fromkeys(
            list(summary.get("steps_skipped") or []) + blocked_steps
        ))
        summary["status"] = "degraded"
        summary["error"] = (
            "price writes deferred: a prices-index fault is on record and this "
            "run may not rebuild the table; a write on a broken index is fatal "
            "and invalidates the DuckDB instance for the rest of the run. "
            "POST /admin/rebuild-prices (or POST /refresh?scope=full) repairs it"
        )
        for _blocked in blocked_steps:
            summary["steps"][_blocked] = {
                "status": "skipped", "reason": "index_fault_pending",
            }
        logger.error(
            "Price-index fault pending: skipping the price write phase this run "
            "(historical backfill, live fetch, CEDA mirror, variety-wise feed, "
            "state backfill). Repair with POST /admin/rebuild-prices."
        )

    # 0. Consume any historical CSVs dropped into data/historical/ so the
    #    dashboard can build a real time-series (the live API is daily-only).
    #    It writes to `prices`, so it runs after the index check, not before it.
    if not price_writes_blocked:
        with pipeline_metrics.step("historical_backfill"):
            try:
                n_hist = run_historical_backfill(folder="mandi_rdd/data/historical")
                if n_hist:
                    pipeline_metrics.record_rows("historical_backfill", n_hist, n_hist)
                    logger.info(f"Historical backfill ingested {n_hist} rows.")
            except Exception as e:
                logger.warning(f"Historical backfill skipped: {e}")

    # 1b. Heal arrival dates before anything analytical runs. Future-dated
    #     rows are month-first mis-parses of a DD/MM/YYYY source, and they
    #     poison freshness, RDD, forecasts and nowcasts alike.
    with pipeline_metrics.step("date_integrity"):
        try:
            from mandi_rdd.core.dates import date_quality, repair_future_dates
            repair = repair_future_dates(conn)
            quality = date_quality(conn)
            summary["steps"]["date_integrity"] = {**repair, **quality}
            if repair.get("repaired") or repair.get("dropped"):
                logger.warning("Date integrity repair applied: %s", repair)
        except Exception as e:
            logger.warning(f"Date integrity check skipped: {e}")

    # 2. Ingest mandi prices.
    #
    # Pages are written as they arrive rather than collected into one list:
    # the whole archive is far larger than the container's memory, and a run
    # that buffered it would be OOM-killed mid-ingest - taking the API down
    # with it, because ingestion runs inside the serving process.
    logger.info("Fetching mandi prices from data.gov.in...")
    price_fetch_error = None
    n_prices = 0
    n_new = 0
    price_write_error = None
    source_info = None
    run_budget_s = _env_float("MANDIIQ_PRICE_FETCH_MAX_SECONDS", 900.0)
    page_size = int(_env_float("MANDIIQ_PRICE_PAGE_SIZE", 1000.0))
    with pipeline_metrics.step("fetch_prices"):
        _t0 = time.monotonic()
        note_refresh_step("fetch_prices")
        try:
            from mandi_rdd.ingestion.fetch_prices import iter_price_pages
            # Resume where the last run stopped. Without this every run walked
            # from offset 0 and spent its whole budget re-reading the newest
            # pages, so a backfill never reached the older archive no matter
            # how many runs passed. Only written back when this run made it
            # past the page the cursor named - never backwards, so a failed
            # run cannot reset the walk.
            try:
                from mandi_rdd.storage.duckdb_store import get_ingest_cursor
                cursor_state = get_ingest_cursor(conn, "prices")["offset"]
            except Exception:
                cursor_state = 0
            cursor_out: dict = {}
            logger.info("Price fetch resuming from cursor offset %s", cursor_state)
            _price_pages = iter_price_pages(
                filters=filters,
                max_records=max_records,
                page_size=page_size,
                max_run_seconds=run_budget_s,
                start_offset=cursor_state,
                cursor_out=cursor_out,
                progress_callback=lambda done, total: logger.info(
                    f"  Prices: {done}/{total} records"
                ),
            )
            if price_writes_blocked:
                # iter_price_pages is a generator: nothing is fetched until it
                # is walked, so this is a real skip and not a fetch we discard.
                _price_pages = iter(())
            for page in _price_pages:
                for record in page:
                    source = record.pop("_source", None)
                    if source is not None and source_info is None:
                        source_info = source
                n_prices += len(page)
                try:
                    n_new += upsert_prices(conn, page)
                except Exception as e:
                    # A write fault must not take rainfall, RDD and forecast
                    # down with it. upsert_prices already rebuilds the table
                    # and retries once on an index fault; if it still failed,
                    # report a degraded run and let POST /admin/rebuild-prices
                    # finish the job.
                    logger.error(f"Price upsert failed; continuing with existing data: {e}")
                    summary["steps"]["upsert_prices"] = {
                        "status": "error", "error": str(e),
                        "hint": "POST /admin/rebuild-prices rebuilds the table and its index",
                    }
                    price_write_error = str(e)
                    break
                pipeline_metrics.record_rows("fetch_prices", n_prices, n_new)
            if not price_writes_blocked:
                pipeline_metrics.record_api_call(
                    "data.gov.in", time.monotonic() - _t0, True
                )
            # Persist the walk's position so the next run continues the pass.
            # The cursor only ever moves forward, except when a pass completes
            # and deliberately wraps back to 0 for the next sweep.
            if cursor_out:
                next_offset = int(cursor_out.get("offset") or 0)
                if next_offset > cursor_state or cursor_out.get("completed_pass"):
                    try:
                        from mandi_rdd.storage.duckdb_store import set_ingest_cursor
                        set_ingest_cursor(
                            conn, "prices", next_offset,
                            int(cursor_out.get("total") or 0),
                        )
                        logger.info(
                            "Price fetch cursor saved at offset %s (pass %s)",
                            next_offset,
                            "complete" if cursor_out.get("completed_pass") else "partial",
                        )
                    except Exception as exc:
                        logger.warning(f"Could not persist the price fetch cursor: {exc}")
        except Exception as e:
            # A slow or briefly unreachable source must not abort the rainfall,
            # RDD and forecast work that can still run on the existing
            # warehouse. The run is reported as "degraded" instead.
            logger.error(f"Price fetch failed, continuing with existing data: {e}")
            price_fetch_error = str(e)
            pipeline_metrics.record_api_call("data.gov.in", time.monotonic() - _t0, False)

    # Record lineage for primary price fetch
    try:
        from mandi_rdd.storage.duckdb_store import record_lineage_batch
        if source_info:
            record_lineage_batch(
                conn,
                source_type=source_info.get("source_type", "api"),
                source_name=source_info.get("source_name", "data.gov.in"),
                resource_id=source_info.get("resource_id"),
                row_count=n_prices,
                n_new=n_new,
                records=[],
                metadata={"filters": filters, "streamed": True},
            )
    except Exception as e:
        logger.warning(f"Failed to record lineage for prices: {e}")

    # 2a. CEDA/Ashoka mirror, when the documented feed yields nothing.
    #     api.data.gov.in is unreachable from cloud networks (the TLS handshake
    #     is dropped and the data.gov.in origin is unreachable from its own
    #     CDN), so a run that fetched 0 rows is the normal case, not an error
    #     to retry harder. CEDA republishes Agmarknet from an India-hosted API
    #     that does answer, and needs only a token; without the token this step
    #     is inert and the run stays degraded exactly as before.
    served_by = source_info.get("source_name") if source_info else None
    if n_prices == 0 and not price_writes_blocked:
        with pipeline_metrics.step("prices_ceda"):
            n_ceda = 0
            n_ceda_new = 0
            try:
                from mandi_rdd.ingestion.fetch_ceda import (
                    ceda_available,
                    iter_ceda_pages,
                )

                if not ceda_available():
                    summary["steps"]["prices_ceda"] = {
                        "status": "skipped",
                        "reason": "MANDIIQ_CEDA_API_KEY not set",
                    }
                    logger.warning(
                        "The data.gov.in feed returned nothing and no CEDA "
                        "token is configured. Note that CEDA is an ARCHIVE "
                        "(measured: daily coverage ends around 2025-10), not a "
                        "live feed - arming it backfills history, it does not "
                        "make the newest date current. See NORTHFLANK_DEPLOY.md."
                    )
                else:
                    from mandi_rdd.storage.duckdb_store import (
                        get_ingest_cursor,
                        set_ingest_cursor,
                    )

                    ceda_cursor = get_ingest_cursor(conn, "prices_ceda")["offset"]
                    ceda_out: dict = {}
                    logger.info(
                        "Price fetch from data.gov.in returned nothing; "
                        "filling the last days from the CEDA Agmarknet mirror "
                        "(resuming at pair %s)",
                        ceda_cursor,
                    )
                    for page in iter_ceda_pages(
                        start_index=ceda_cursor, cursor_out=ceda_out
                    ):
                        for record in page:
                            source = record.pop("_source", None)
                            if source is not None and served_by is None:
                                served_by = source.get("source_name")
                        n_ceda += len(page)
                        try:
                            n_ceda_new += upsert_prices(conn, page)
                            pipeline_metrics.record_rows("prices_ceda", n_ceda, n_ceda_new)
                        except Exception as ceda_write_error:
                            logger.error(
                                "CEDA price upsert failed; keeping the rows already "
                                "written: %s",
                                ceda_write_error,
                            )
                            summary["steps"]["prices_ceda"] = {
                                "status": "error",
                                "error": str(ceda_write_error),
                                "fetched": n_ceda,
                            }
                            break
                    summary["steps"].setdefault("prices_ceda", {})
                    summary["steps"]["prices_ceda"].update(
                        {"fetched": n_ceda, "new": n_ceda_new}
                    )
                    # Why a zero is a zero. The mirror answers "No data exists"
                    # for a window its archive does not cover - which is every
                    # live window, because its daily coverage ends around
                    # 2025-10 - so an armed mirror that can never answer a live
                    # request must not read as a broken one.
                    if ceda_out.get("last_message"):
                        summary["steps"]["prices_ceda"]["note"] = str(
                            ceda_out["last_message"]
                        )[:200]
                    elif n_ceda == 0:
                        summary["steps"]["prices_ceda"]["note"] = (
                            "the mirror returned no rows and no message for "
                            "this window"
                        )
                    if ceda_out:
                        next_index = int(ceda_out.get("offset") or 0)
                        if next_index != ceda_cursor or ceda_out.get("completed_pass"):
                            set_ingest_cursor(
                                conn, "prices_ceda", next_index,
                                int(ceda_out.get("total") or 0),
                            )
                    if n_ceda:
                        logger.info(
                            "CEDA mirror filled %d rows (%d new)", n_ceda, n_ceda_new
                        )
            except Exception as ceda_error:
                # The mirror throttles hard (a burst earns `429` with
                # `Retry-After` in the half-hour range), so being locked out is
                # a normal, expected state rather than a failure to report.
                try:
                    from mandi_rdd.ingestion.fetch_ceda import CedaRateLimited
                except Exception:  # pragma: no cover - import cannot fail here
                    CedaRateLimited = ()  # type: ignore[assignment]
                if isinstance(ceda_error, CedaRateLimited):
                    logger.info(
                        "CEDA mirror is rate-limited (%s); leaving it to a later tick",
                        ceda_error,
                    )
                    summary["steps"]["prices_ceda"] = {
                        "status": "rate_limited",
                        "retry_after_s": int(getattr(ceda_error, "retry_after_s", 0)),
                    }
                else:
                    logger.warning(f"CEDA mirror fallback skipped: {ceda_error}")
                    step = {"status": "error", "error": str(ceda_error)}
                    # An HTTP status is the difference between "the network is
                    # down" and "this token is not acceptable", and only the
                    # second one is fixed by re-issuing the key. Recorded, so
                    # the cockpit repeats the remedy that actually applies.
                    code = getattr(ceda_error, "code", None)
                    if isinstance(code, int):
                        step["http_status"] = code
                        if code in (401, 403):
                            step["token_rejected"] = True
                    summary["steps"]["prices_ceda"] = step

    # 2b. Supplementary variety-wise recent-price feed (resource 35985678).
    # Bounded + best-effort: never blocks the main pipeline if it fails.
    with pipeline_metrics.step("prices_varietywise"):
        try:
            from mandi_rdd.ingestion.archive_scanner import fetch_varietywise_recent
            _t0 = time.monotonic()
            variety_records = fetch_varietywise_recent(days=60, max_records=20000)
            pipeline_metrics.record_api_call("varietywise_archive", time.monotonic() - _t0, True)
            if variety_records and not price_writes_blocked:
                n_var_new = upsert_prices(conn, variety_records)
                pipeline_metrics.record_rows("prices_varietywise", len(variety_records), n_var_new)
                summary["steps"]["prices_varietywise"] = {
                    "fetched": len(variety_records), "new": n_var_new
                }
                logger.info(f"Variety-wise prices: {len(variety_records)} fetched, {n_var_new} new")
            else:
                n_var_new = 0
                summary["steps"]["prices_varietywise"] = {"fetched": 0, "new": 0}
            # Record lineage for variety-wise supplement
            try:
                src_info = None
                for r in variety_records:
                    src = r.pop("_source", None)
                    if src is not None:
                        src_info = src
                        break
                if src_info:
                    record_lineage_batch(
                        conn,
                        source_type=src_info.get("source_type", "varietywise"),
                        source_name=src_info.get("source_name", "variety-wise archive"),
                        resource_id=src_info.get("resource_id"),
                        row_count=len(variety_records),
                        n_new=n_var_new,
                        records=variety_records,
                    )
            except Exception as lve:
                logger.warning(f"Failed to record lineage for variety-wise: {lve}")
        except Exception as e:
            logger.warning(f"Variety-wise supplement skipped: {e}")
            summary["steps"]["prices_varietywise"] = {"status": "error", "error": str(e)}

    # Which source actually served this run, so /health can name it and a
    # reader can tell "the documented feed is working" from "the mirror is
    # carrying us". None when nothing answered.
    summary["steps"]["prices"] = {
        "fetched": n_prices,
        "new": n_new,
        "served_by": served_by,
    }
    if price_writes_blocked:
        summary["steps"]["prices"]["deferred"] = "index_fault_pending"
        summary["steps"]["prices"]["hint"] = (
            "POST /admin/rebuild-prices rebuilds the prices table and its index"
        )
    if price_fetch_error:
        summary["steps"]["prices"]["error"] = price_fetch_error
        summary["status"] = "degraded"
        # Name the fill when the mirror carried the run: "degraded" should say
        # whether the warehouse still advanced, because a reader who only sees
        # "price source unavailable" cannot tell data loss from a fallback.
        mirror_rows = (summary["steps"].get("prices_ceda") or {}).get("fetched") or 0
        summary["error"] = f"price source unavailable: {price_fetch_error}"
        if mirror_rows:
            summary["error"] += (
                f"; filled {mirror_rows} rows from the CEDA Agmarknet mirror"
            )
    if price_write_error:
        summary["steps"]["prices"]["write_error"] = price_write_error
        summary["status"] = "degraded"
        previous = summary.get("error")
        summary["error"] = (
            f"{previous}; price write failed: {price_write_error}"
            if previous else f"price write failed: {price_write_error}"
        )
    logger.info(f"Prices: {n_prices} fetched, {n_new} new")

    # 3. Load district-subdivision mapping (always, regardless of rainfall)
    district_map = load_district_subdivision_map()
    conn.executemany(
        "INSERT OR IGNORE INTO district_map (state, district, sub_division) VALUES (?, ?, ?)",
        [(s, d, sub) for (s, d), sub in district_map.items()],
    )
    conn.commit()
    logger.info(f"District-subdivision mappings: {len(district_map)}")

    # 3.5. Backfill state fields in prices using district map
    # The state backfill rewrites `state` on the price rows it matches, i.e. an
    # UPDATE - which DuckDB runs as a delete-and-insert through the same index a
    # recorded fault has broken. It is therefore part of the write phase, and it
    # is also wrapped: a write fault here must degrade the run, not abort the
    # rainfall and analysis work that can still run on the existing warehouse.
    if not price_writes_blocked:
        with pipeline_metrics.step("backfill_state"):
            try:
                logger.info("Backfilling state fields using district-to-state mapping...")
                from mandi_rdd.ingestion.backfill_state import backfill, build_lookup
                # backfill() takes a district->state lookup, not the connection
                # it will open itself; passing the connection here used to
                # abort every run.
                n_updated = backfill(build_lookup())
                summary["steps"]["backfill_state"] = {"updated": n_updated}
                logger.info(f"State fields backfilled: {n_updated} records")
            except Exception as e:
                logger.warning(f"State backfill skipped: {e}")
                summary["steps"]["backfill_state"] = {
                    "status": "error", "error": str(e),
                }

    # 4. Ingest rainfall
    if not skip_rainfall:
        with pipeline_metrics.step("fetch_rainfall"):
            logger.info("Fetching rainfall data...")
            _t0 = time.monotonic()
            rainfall_records = fetch_and_store_all_rainfall()
            pipeline_metrics.record_api_call("rainfall_api", time.monotonic() - _t0, True)
            n_rain = 0
            n_rain_new = 0
            if rainfall_records:
                n_rain = len(rainfall_records)
                n_rain_new = upsert_rainfall(conn, rainfall_records)
                pipeline_metrics.record_rows("fetch_rainfall", n_rain, n_rain_new)
            summary["steps"]["rainfall"] = {"fetched": n_rain, "new": n_rain_new}
            logger.info(f"Rainfall: {n_rain} records, {n_rain_new} new")
            # Record lineage for rainfall
            try:
                from mandi_rdd.storage.duckdb_store import record_lineage_batch
                record_lineage_batch(
                    conn,
                    source_type="rainfall",
                    source_name="data.gov.in / Datameet rainfall",
                    resource_id=os.environ.get("RAINFALL_RESOURCE_ID", "fallback"),
                    row_count=n_rain,
                    n_new=n_rain_new,
                    records=None,
                    metadata={"source": fetch_and_store_all_rainfall.__name__},
                )
            except Exception as le:
                logger.warning(f"Failed to record rainfall lineage: {le}")
    else:
        summary["steps"]["rainfall"] = {"status": "skipped", "reason": "no data source"}

    # 4b. Ingest satellite NDVI (if Sentinel Hub credentials are set)
    import os as _os
    if light:
        logger.info("Light run: skipping the satellite NDVI fetch")
        summary["steps"]["ndvi"] = {"status": "skipped", "reason": "light scope"}
    elif _os.environ.get("SENTINEL_CLIENT_ID") and _os.environ.get("SENTINEL_CLIENT_SECRET"):
        with pipeline_metrics.step("fetch_ndvi"):
            logger.info("Fetching satellite NDVI data...")
            note_refresh_step("fetch_ndvi")
            try:
                _t0 = time.monotonic()
                n_ndvi = fetch_and_store_all_ndvi()
                pipeline_metrics.record_api_call("sentinel_hub", time.monotonic() - _t0, True)
                pipeline_metrics.record_rows("fetch_ndvi", n_ndvi, n_ndvi)
                summary["steps"]["ndvi"] = {"stored": n_ndvi}
                logger.info(f"NDVI: {n_ndvi} records stored")
            except Exception as e:
                logger.warning(f"NDVI ingestion failed: {e}")
                summary["steps"]["ndvi"] = {"status": "error", "error": str(e)}
    else:
        logger.info("No Sentinel Hub credentials  -  skipping NDVI ingestion")
        summary["steps"]["ndvi"] = {"status": "skipped", "reason": "no SENTINEL_CLIENT_ID/SECRET"}

    # 5. Run analysis pipeline for all available commodities
    price_df = conn.execute(
        "SELECT DISTINCT commodity FROM prices ORDER BY commodity"
    ).fetchdf()
    all_commodities = price_df["commodity"].tolist() if len(price_df) > 0 else []

    # Focus on rain-sensitive commodities for the MVP, plus high-volume
    # staples that have enough data points for meaningful RDD analysis.
    rain_sensitive = ["Onion", "Tomato", "Potato", "Cabbage", "Cauliflower"]
    high_volume_staples = [
        "Wheat", "Rice", "Paddy(Common)", "Paddy(Dhan)(Common)",
        "Maize", "Soyabean", "Mustard", "Groundnut",
        "Banana", "Mango", "Apple", "Grapes",
        "Garlic", "Ginger (Dry)", "Chili Red", "Turmeric",
        "Bajra(Pearl Millet/Cumbu)", "Jowar (Sorghum)",
        "Bengal Gram (Gram)(Whole)", "Red Gram",
        "Green Gram (Moong)(Whole)", "Black Gram (Urad Beans)(Whole)",
        "Sugarcane", "Cotton",
    ]
    target_commodities = [c for c in rain_sensitive if c in all_commodities]
    for c in high_volume_staples:
        if c in all_commodities and c not in target_commodities:
            target_commodities.append(c)

    # The analysis half of the run is a recompute over data the steps above
    # have already stored, and it is the part that does not fit a free-tier
    # container. A light run leaves it to a run that asked for it, and records
    # what it left out rather than reporting an empty analysis as a clean one.
    analysis_targets = [] if light else target_commodities
    if light:
        logger.info(
            "Light run: deferring RDD/forecast/classifier for %d commodities",
            len(target_commodities),
        )
        summary["steps"]["analysis"] = {
            "status": "skipped",
            "reason": "light scope",
            "would_cover": len(target_commodities),
        }

    rdd_results = []
    fe_results = []
    classifier_results = []        # Wrap analysis loop in a step timer
    with pipeline_metrics.step("rdd_analysis"):
        if analysis_targets:
            note_refresh_step("rdd_analysis")
        for commodity in analysis_targets:
            # RDD + Fixed-effects
            logger.info(f"Running RDD + FE for {commodity}...")
            try:
                result = run_rdd(conn, commodity=commodity)
            except Exception as e:
                logger.error(f"  RDD failed for {commodity}: {e}")
                result = None
            if result:
                # Fixed-effects cross-check
                try:
                    from mandi_rdd.analysis.fixed_effects import run_fe_crosscheck
                    fe_result = run_fe_crosscheck(conn, commodity=commodity)
                    if fe_result and fe_result.get("coefficient") is not None:
                        result["fe_effect"] = fe_result["coefficient"]
                        result["fe_p_value"] = fe_result["p_value"]
                        fe_results.append(fe_result)
                        logger.info(f"  FE {commodity}: coeff={fe_result['coefficient']:.2f}, p={fe_result['p_value']:.4f}")
                except Exception as fe_err:
                    logger.warning(f"  FE cross-check skipped: {fe_err}")
                
                if result.get("effect") is not None:
                    save_rdd_result(conn, result)
                    rdd_results.append(result)
                    logger.info(f"  RDD {commodity}: effect={result.get('effect', '?')}, p={result.get('p_value', '?')}")
                else:
                    logger.info(f"  RDD skipped for {commodity}: insufficient history "
                                 f"(need >=3 dates with both deficient and non-deficient rainfall)")
            else:
                logger.info(f"  RDD skipped for {commodity}: run_rdd returned no result")

            # Forecast
            with pipeline_metrics.step("forecast_training"):
                logger.info(f"Training forecast for {commodity}...")
                try:
                    from mandi_rdd.analysis.forecast import train_forecast
                    from mandi_rdd.storage.duckdb_store import save_forecast_metrics
                    fc_res = train_forecast(conn, commodity=commodity, state=None, periods=6)
                    m = fc_res.get("metrics") or {}
                    if m.get("mape") is not None:
                        save_forecast_metrics(
                            conn, commodity,
                            test_mape=m["mape"], test_mae=m.get("mae"),
                            test_rmse=m.get("rmse"),
                            n_training_months=fc_res.get("n_training_months"),
                            n_test_months=fc_res.get("n_test_months"), model="prophet",
                        )
                        logger.info(f"  Forecast {commodity}: MAPE={m['mape']:.2f}%")
                    else:
                        logger.info(f"  Forecast skipped for {commodity}: {fc_res.get('error')}")
                except Exception as e:
                    logger.error(f"  Forecast failed for {commodity}: {e}")

            # Classifier
            with pipeline_metrics.step("classifier_training"):
                logger.info(f"Running spike classifier for {commodity}...")
                try:
                    from mandi_rdd.analysis.classifier import train_spike_classifier
                    cls_result = train_spike_classifier(conn, commodity=commodity)
                    if "error" not in cls_result:
                        from mandi_rdd.storage.duckdb_store import save_classification_result
                        save_classification_result(conn, cls_result)
                        classifier_results.append(cls_result)
                        logger.info(f"  Classifier {commodity}: ROC-AUC={cls_result.get('roc_auc', '?'):.4f}")
                    else:
                        err_msg = cls_result["error"]
                        # "Insufficient feature rows" is expected early on -> info-level
                        if "Insufficient feature rows" in err_msg or "Insufficient data points" in err_msg:
                            logger.info(f"  Classifier skipped for {commodity}: {err_msg}")
                        else:
                            logger.warning(f"  Classifier skipped: {err_msg}")
                except Exception as e:
                    logger.warning(f"  Classifier skipped: {e}")

            # Forecast (persist MAPE so the dashboard shows a live accuracy metric)
            with pipeline_metrics.step("forecast_persist"):
                logger.info(f"Training forecast + persisting MAPE for {commodity}...")
                try:
                    from mandi_rdd.storage.duckdb_store import save_forecast_metrics
                    fc = train_forecast(conn, commodity=commodity, periods=12)
                    if fc and fc.get("metrics"):
                        m = fc["metrics"]
                        save_forecast_metrics(
                            conn,
                            commodity,
                            test_mape=m.get("mape"),
                            test_mae=m.get("mae"),
                            test_rmse=m.get("rmse"),
                            n_training_months=m.get("train_points"),
                            n_test_months=m.get("test_points"),
                        )
                        logger.info(f"  Forecast {commodity}: MAPE={m.get('mape')}")
                    else:
                        logger.info(f"  Forecast skipped for {commodity}: insufficient history")
                except Exception as e:
                    logger.warning(f"  Forecast skipped: {e}")

    summary["steps"]["rdd"] = {"commodities_run": len(rdd_results)}
    summary["steps"]["fe_crosscheck"] = {"commodities_run": len(fe_results)}
    summary["steps"]["classifier"] = {"commodities_run": len(classifier_results)}

    # 5. Generate nightly narratives for all tracked commodities
    with pipeline_metrics.step("nightly_narratives"):
        from mandi_rdd.ai.router import get_api_key as _get_llm_key
        _llm_key = _get_llm_key()
        narrative_results = []
        if _llm_key:
            logger.info("Generating nightly narratives via AI orchestrator...")
            try:
                from mandi_rdd.ai.orchestrator import generate_nightly_narrative
            except ImportError:
                logger.warning("AI orchestrator not available - install openai and pyyaml")
                generate_nightly_narrative = None

            for commodity in analysis_targets:
                if not generate_nightly_narrative:
                    break
                try:
                    _t0 = time.monotonic()
                    narrative = generate_nightly_narrative(commodity=commodity)
                    pipeline_metrics.record_api_call(f"llm_narrative_{commodity}", time.monotonic() - _t0, True)
                    if narrative and narrative.get("answer"):
                        from mandi_rdd.storage.duckdb_store import save_narrative
                        save_narrative(
                            conn,
                            commodity=commodity,
                            narrative=narrative["answer"],
                            model_used=narrative.get("model_used"),
                            endpoints_used=narrative.get("endpoints_used", []),
                        )
                        narrative_results.append(commodity)
                        logger.info(f"  Narrative generated for {commodity}")
                    else:
                        logger.warning(f"  Narrative skipped for {commodity}: {narrative.get('error', 'empty')}")
                except Exception as e:
                    logger.warning(f"  Narrative failed for {commodity}: {e}")
        else:
            logger.info("No LLM provider key set (GEMINI_API_KEY or OPENROUTER_API_KEY) - skipping nightly narratives")

    summary["steps"]["narratives"] = {"generated": len(narrative_results), "commodities": narrative_results}
    summary["duration_seconds"] = round(time.time() - start, 1)
    summary["commodities_analyzed"] = analysis_targets
    # Merge, do not overwrite: a run that deferred its price writes because of a
    # recorded index fault named those steps in `steps_skipped` earlier, and a
    # reader has to see both kinds of omission to know what this run did not do.
    summary["steps_skipped"] = sorted(
        set(summary.get("steps_skipped") or [])
        | (set(LIGHT_SKIPPED_STEPS) if light else set())
    )
    if light:
        summary["analysis_deferred_for"] = target_commodities

    # Record the full pipeline run in pipeline_metrics
    pipeline_metrics.record_pipeline_run(summary)

    # /health reads this file: without it "last_run_utc" silently freezes at
    # the last CLI run even while ingestion keeps running in-process.
    _write_ingest_status(summary)

    conn.close()
    logger.info(f"Pipeline complete in {summary['duration_seconds']}s")
    return summary



# One source of truth: the storage layer's list, because a false positive here
# triggers a full prices-table rebuild. This module used to keep its own copy,
# and the storage layer's broader list included the bare word "index", so any
# status record that mentioned an index - including the record's own
# "index_health" key - counted as a fatal fault.
try:
    from mandi_rdd.storage.duckdb_store import _INDEX_FAULT_MARKERS as INDEX_FAULT_MARKERS
except Exception:  # pragma: no cover - storage always imports in production
    INDEX_FAULT_MARKERS = (
        "failed to delete all rows from index",
        "database has been invalidated",
        "database instance is invalidated",
        "index corruption",
    )


def _last_run_had_index_fault(status_path: Path = None) -> bool:
    """Did the previous run die on an inconsistent prices index?"""
    if status_path is None:
        try:
            from mandi_rdd.storage.duckdb_store import index_fault_flagged
            if index_fault_flagged():
                return True
        except Exception:
            pass
    try:
        out = status_path or (
            Path(__file__).resolve().parent.parent / "data" / "last_ingest_status.json"
        )
        if not out.exists():
            return False
        record = json.loads(out.read_text(encoding="utf-8"))
        blob = " ".join(
            str(record.get(key) or "") for key in ("error", "status", "outcome")
        ).lower()
        return any(marker in blob for marker in INDEX_FAULT_MARKERS)
    except Exception as exc:
        logger.debug("Could not read the previous ingest status: %s", exc)
        return False


def _write_ingest_status(summary: dict, status_path: Path = None) -> None:
    """Write last_ingest_status.json so /health sees latest status."""
    status = summary.get("status", "unknown")
    steps = summary.get("steps", {})
    prices_step = steps.get("prices", {})
    n_new = prices_step.get("new", 0) if isinstance(prices_step, dict) else 0
    if status == "ok":
        outcome = "success"
    elif status == "degraded":
        # Ran, produced useful output, but a source was unavailable: say so
        # instead of claiming either a clean run or a total failure.
        outcome = "degraded"
    else:
        outcome = "failure"
    import datetime
    quality = steps.get("date_integrity") or {}
    # Persist the price-index check with the run. The process that hit a fatal
    # index fault is killed by it, so an in-memory record would be gone exactly
    # when /health is asked to prove the heal happened.
    # The mirror step is persisted too, because "a token is configured" and
    # "the mirror works" are different facts and only the second one fills
    # rows. A run whose last_ceda says error/401 is a mirror that is armed and
    # refused - the state this file used to hide behind outcome "degraded".
    ceda = steps.get("prices_ceda")
    ceda_record = None
    if isinstance(ceda, dict):
        ceda_record = {
            key: ceda.get(key)
            for key in (
                "status", "error", "note", "fetched", "new", "retry_after_s",
                "http_status", "token_rejected",
            )
            if ceda.get(key) is not None
        }
    index = steps.get("index_health")
    index_health = None
    if isinstance(index, dict):
        index_health = {
            key: index.get(key)
            for key in (
                "rebuilt", "trigger", "rows_before", "rows_after", "rows_removed",
                "duplicates", "duplicates_after", "fault_flagged",
            )
            if index.get(key) is not None
        }
        index_health["checked_at"] = datetime.datetime.utcnow().isoformat() + "Z"
    record = {
        "last_run_utc": datetime.datetime.utcnow().isoformat() + "Z",
        "outcome": outcome,
        "status": status,
        "scope": summary.get("scope"),
        "steps_skipped": summary.get("steps_skipped") or [],
        "new_price_rows": n_new,
        "duration_s": summary.get("duration_seconds"),
        "error": None if status == "ok" else summary.get("error"),
        "price_source": prices_step.get("served_by") if isinstance(prices_step, dict) else None,
        "data_max_date": quality.get("max_date"),
        "days_behind": quality.get("days_behind"),
        "n_future_dates": quality.get("n_future_dates"),
        "index_health": index_health,
        "ceda": ceda_record,
    }
    try:
        out = status_path or (
            Path(__file__).resolve().parent.parent / "data" / "last_ingest_status.json"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w") as f:
            json.dump(record, f, indent=2)
    except Exception:
        pass

def run_once():
    """One-shot ingestion + RDD run. Suitable for cron."""
    summary = run_ingestion()

    if summary.get("status") == "ok":
        print(f"\n{'='*50}")
        print(f"[OK] Pipeline complete ({summary['duration_seconds']}s)")
        for step, info in summary["steps"].items():
            print(f"  {step}: {info}")
        print(f"{'='*50}")
    elif summary.get("status") == "degraded":
        # Exit 0 so a transient source outage does not paint the whole job red,
        # but print a GitHub annotation and record "degraded" in
        # last_ingest_status.json so /health reports it honestly.
        print(f"\n::warning::Pipeline ran degraded: {summary.get('error', 'unknown')}")
        for step, info in summary["steps"].items():
            print(f"  {step}: {info}")
        sys.exit(0)
    else:
        print(f"\n[FAIL] Pipeline failed: {summary.get('error', 'unknown')}")
        sys.exit(1)


if __name__ == "__main__":
    run_once()
