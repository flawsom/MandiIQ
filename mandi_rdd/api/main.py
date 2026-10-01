"""
MandiRDD - FastAPI serving layer.

Endpoints:
- GET /health - liveness check
- GET /prices - query stored prices with filters
- GET /rdd-result/{commodity} - latest RDD estimate
- GET /rdd-plot/{commodity} - binned scatter plot data
- GET /robustness/{commodity} - robustness check bundle
- GET /forecast/{commodity} - Prophet forecast
- GET /risk-score/{commodity} - XGBoost risk score
- GET /recommendation/{commodity} - procurement recommendation
- POST /ask - AI orchestrator (OpenRouter multi-model routing)
- POST /refresh - manual pipeline re-run
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import os
import json
import logging
import time

import hashlib
import functools
import gzip
import shutil
import hmac
import datetime
import urllib.error
import urllib.request
from typing import Optional
from contextlib import asynccontextmanager

import uvicorn
import threading
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from mandi_rdd.storage.duckdb_store import (
    get_connection,
    init_schema,
    get_prices,
    get_latest_rdd,
    get_monthly_avg_prices,
)
from mandi_rdd.ai.router import (
    clear_cool_down,
    get_llm_fallback_count,
    reset_llm_fallback_count,
)
from mandi_rdd.api import metrics_push

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── Pydantic schemas ──

class HealthResponse(BaseModel):
    status: str
    version: str = "2.4.0"
    llm_fallback_count: int = 0
    n_prices: int
    n_commodities: int
    n_states: int
    n_districts: int
    n_rainfall: int
    n_rainfall_filtered: int
    rainfall_below_threshold: int
    n_rdd_results: int
    n_ndvi: Optional[int] = None
    n_ndvi_districts: Optional[int] = None
    n_tests: int = 71
    last_run_utc: Optional[str] = None
    last_outcome: Optional[str] = None
    # Live-data provenance: the newest arrival date in the warehouse and how
    # far behind today it is, so no surface can claim "live" while stale.
    data_max_date: Optional[str] = None
    data_min_date: Optional[str] = None
    days_behind: Optional[int] = None
    hours_since_last_run: Optional[float] = None
    n_future_dates: int = 0
    ingestion_running: bool = False
    # Self-refresh bookkeeping. The container refreshes itself; these fields are
    # how a caller can tell "quiet because everything is fine" from "quiet
    # because the scheduler has been throwing on every tick since July".
    last_refresh_attempt_utc: Optional[str] = None
    last_refresh_success_utc: Optional[str] = None
    last_refresh_error: Optional[str] = None
    last_index_repair: Optional[dict] = None
    # Every index check lands here, repaired or not, so "checked and clean" is
    # distinguishable from "never checked". `index_fault_pending` is the one
    # that matters operationally: a recorded fault that has not been repaired
    # yet (it clears on the next successful rebuild).
    last_index_check: Optional[dict] = None
    index_fault_pending: bool = False
    # Capability flag: a caller that wants to run an automated recovery needs
    # to know the deployed build can actually do it. This one says the rebuild
    # is atomic and the R2 restore streams. It defaults to False so an older
    # build - one that would rebuild a table non-atomically or decompress the
    # backup in RAM - can never be mistaken for a safe recovery target just
    # because the field is absent from its response.
    safe_recovery: bool = False
    # Which source actually served the last price fetch, and whether the CEDA
    # Agmarknet mirror (the one host reachable from cloud networks) is armed.
    # Without the mirror, an outage of api.data.gov.in is unfixable from here -
    # these two fields say so without the caller having to run a probe.
    last_price_source: Optional[str] = None
    mirror_configured: bool = False
    refresh_runs: int = 0
    refresh_failures: int = 0
    refresh_interval_s: int = 0
    commodities_analyzed: list[str] = []



class PriceRecord(BaseModel):
    state: str
    district: str
    market: str
    commodity: str
    variety: Optional[str] = None
    arrival_date: str
    modal_price: Optional[float] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None


class RDDResult(BaseModel):
    commodity: str
    effect: Optional[float]
    p_value: Optional[float]
    std_error: Optional[float]
    n_left: Optional[int]
    n_right: Optional[int]
    interpretation: Optional[str]
    error: Optional[str]


class RDDPlotData(BaseModel):
    raw_x: list
    raw_y: list
    bin_centers: list
    bin_means: list
    bin_stds: list
    left_x: list
    left_y: list
    right_x: list
    right_y: list
    cutoff: float


class ForecastResponse(BaseModel):
    commodity: str
    forecast: list
    metrics: dict
    n_training_months: int


class RefreshResponse(BaseModel):
    status: str
    message: str
    duration_seconds: Optional[float] = None


# ── Phase 11: AI Orchestrator schemas ──

class AskRequest(BaseModel):
    query: str
    commodity: Optional[str] = None
    district: Optional[str] = None


class AskResponse(BaseModel):
    query: str
    commodity: str
    district: str
    answer: str
    model_used: Optional[str] = None
    endpoints_used: list[str] = []
    error: Optional[str] = None


# ── App state ──

class HealthStats:
    """Simple state for /metrics endpoint tracking."""
    def __init__(self):
        self.start_time = time.time()
        self.health_count = 0
        self.cold_start = 1  # resets on each server start
health_stats = HealthStats()
# ── Deploy endpoint state ──
_last_deploy_ts: float = 0.0
_DEPLOY_COOLDOWN_S: float = 60.0
# Load Grafana dashboard template
_dashboard_path = os.path.join(os.path.dirname(__file__), "..", "..", "dashboards", "mandiiq-pipeline.json")
_dashboard_path = os.path.abspath(_dashboard_path)
if os.path.exists(_dashboard_path):
    with open(_dashboard_path, "r") as f: _raw = json.load(f)
    dashboard_json = _raw.get("dashboard", _raw)
    _dashboard_export = _raw
else:
    dashboard_json = None
    _dashboard_export = None
_dashboard_last_refresh: float = 0.0
_dashboard_file_mtime: float = 0.0

class AppState:
    def __init__(self):
        self.commodities = []


_QUALITY_CACHE: dict = {"at": 0.0, "data": None}


def _cached_date_quality(conn, ttl_seconds: float = 60.0) -> dict:
    """Date-integrity snapshot, memoised so /health stays cheap to poll."""
    now = time.time()
    cached = _QUALITY_CACHE.get("data")
    if cached is not None and now - float(_QUALITY_CACHE.get("at") or 0.0) < ttl_seconds:
        return cached
    try:
        from mandi_rdd.core.dates import date_quality
        data = date_quality(conn)
    except Exception as exc:
        logger.warning("Date quality report unavailable: %s", exc)
        return cached or {}
    _QUALITY_CACHE["at"] = now
    _QUALITY_CACHE["data"] = data
    return data


def _hours_since(timestamp: Optional[str]) -> Optional[float]:
    """Whole hours between an ISO-8601 UTC stamp and now, else None."""
    if not timestamp:
        return None
    try:
        parsed = datetime.datetime.strptime(str(timestamp)[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    delta = datetime.datetime.utcnow() - parsed
    return round(delta.total_seconds() / 3600.0, 2)


def _ingestion_running() -> bool:
    try:
        from mandi_rdd.ingestion.scheduler import ingestion_running
        return bool(ingestion_running())
    except Exception:
        return False


# Data older than this many days is reported as stale rather than healthy.
# The upstream source publishes with a 1-2 day lag, so 3 is the tightest bound
# that a working pipeline can still satisfy.
STALE_AFTER_DAYS = 3


# Self-refresh bookkeeping, written by the background loop in `lifespan`.
_REFRESH_STATE: dict = {
    "last_attempt_utc": None,
    "last_success_utc": None,
    "last_error": None,
    "last_index_repair": None,
    "last_index_check": None,
    "runs": 0,
    "failures": 0,
    "interval_s": 0,
}


def _self_refresh_enabled() -> bool:
    return os.environ.get("MANDIIQ_SELF_REFRESH", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _self_refresh_interval_s() -> int:
    try:
        minutes = int(os.environ.get("MANDIIQ_REFRESH_INTERVAL_MINUTES", "60"))
    except ValueError:
        minutes = 60
    return max(5, minutes) * 60


def _self_refresh_initial_delay_s() -> int:
    """Seconds to wait after boot before the first refresh.

    Deliberately short: a restart is the moment the warehouse is most likely
    to be behind, and sleeping a full interval first would leave the API
    serving yesterday's numbers until the next tick.
    """
    try:
        seconds = int(os.environ.get("MANDIIQ_REFRESH_INITIAL_DELAY_S", "90"))
    except ValueError:
        seconds = 90
    return max(5, seconds)


def _utcnow_iso() -> str:
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _note_index_health(report, source: str) -> None:
    """Record what the price-index check found, so /health can prove it ran.

    ``last_index_repair`` only appears when a rebuild actually happened, which
    made an unremarkable "checked and clean" indistinguishable from "never
    checked" - and made a heal that ran outside this process invisible. Every
    check now lands in ``last_index_check``, and a rebuild also lands in
    ``last_index_repair`` with the trigger that caused it.
    """
    if not isinstance(report, dict):
        return
    record = dict(report)
    record.setdefault("rebuilt", False)
    record["checked_at"] = _utcnow_iso()
    record["source"] = source
    _REFRESH_STATE["last_index_check"] = record
    if record["rebuilt"]:
        _REFRESH_STATE["last_index_repair"] = record
        logger.error(
            "Price index repaired (%s): %d rows kept, %d removed, trigger=%s",
            source, record.get("rows_after") or 0, record.get("rows_removed") or 0,
            record.get("trigger"),
        )


def _persisted_index_health(status_path: Optional[Path] = None) -> Optional[dict]:
    """The index record from the last pipeline run, so a restart keeps proof.

    The in-process state is wiped by the restart that a fatal index fault
    itself causes; the status file the scheduler writes survives it.
    """
    try:
        status_path = status_path or (
            Path(__file__).resolve().parent.parent / "data" / "last_ingest_status.json"
        )
        if not status_path.exists():
            return None
        with open(status_path) as f:
            record = json.load(f)
        health = record.get("index_health")
        return health if isinstance(health, dict) else None
    except Exception:
        return None


def _index_fault_pending() -> bool:
    """True while a recorded index fault has not been repaired."""
    try:
        from mandi_rdd.storage.duckdb_store import index_fault_flagged
        return bool(index_fault_flagged())
    except Exception:
        return False


def _ceda_configured() -> bool:
    """Is the CEDA Agmarknet mirror armed with a token?

    Kept out of /health's cost: this only reads the environment, it never calls
    the mirror. Use /admin/source-probe to actually reach it.
    """
    try:
        from mandi_rdd.ingestion.fetch_ceda import ceda_available
        return bool(ceda_available())
    except Exception:
        return False


def _refresh_once() -> dict:
    """Run the pipeline once, record the outcome, and never raise."""
    _REFRESH_STATE["last_attempt_utc"] = _utcnow_iso()
    _REFRESH_STATE["runs"] += 1
    try:
        from mandi_rdd.ingestion.scheduler import ingestion_running, run_ingestion
    except Exception as e:  # pragma: no cover - import smoke test
        _REFRESH_STATE["failures"] += 1
        _REFRESH_STATE["last_error"] = f"import failed: {e}"
        return {"status": "error", "error": str(e)}

    if ingestion_running():
        # A /refresh call or an earlier tick is already working; queueing a
        # second run behind it would only serialise more waiting.
        logger.info("Self-refresh skipped: an ingestion is already running")
        return {"status": "busy"}

    _verify_price_index_once()

    try:
        summary = run_ingestion()
    except Exception as e:
        _REFRESH_STATE["failures"] += 1
        _REFRESH_STATE["last_error"] = f"{type(e).__name__}: {e}"
        logger.error(f"Self-refresh failed: {e}")
        # The pipeline died before it could report: if the cause was an index
        # fault, leave the marker on the volume so the next tick rebuilds
        # instead of rediscovering the fault the hard way.
        try:
            from mandi_rdd.storage.duckdb_store import note_index_fault
            note_index_fault(e)
        except Exception:
            pass
        return {"status": "error", "error": str(e)}

    status = summary.get("status") or "unknown"
    if status in ("success", "degraded"):
        _REFRESH_STATE["last_success_utc"] = _utcnow_iso()
    else:
        _REFRESH_STATE["failures"] += 1
    _REFRESH_STATE["last_error"] = summary.get("error")
    # The scheduler runs its own index check inside the pipeline; recording it
    # here means a heal that happened mid-run is visible on /health even when
    # this process never had to repair anything itself.
    steps = summary.get("steps") or {}
    _note_index_health(steps.get("index_health"), source="pipeline")
    return summary


# One verification per process: the probe writes two statements, and repeating
# it on every hourly tick would be pointless work on a healthy warehouse.
_INDEX_VERIFIED = False


def _verify_price_index_once() -> Optional[dict]:
    """Make sure the prices index works before the pipeline writes anything.

    Runs ahead of the pipeline rather than inside it because the first write of
    a run is what dies, and it happens once per process so a restart is itself
    the trigger. The marker lives on the data volume, so a process killed by
    the probe leaves its successor a rebuild instead of the same failure.
    """
    global _INDEX_VERIFIED
    if _INDEX_VERIFIED:
        return None
    try:
        from mandi_rdd.storage.duckdb_store import (
            get_connection,
            index_fault_flagged,
            note_index_fault,
            verify_price_index,
        )
        conn = get_connection()
        try:
            init_schema(conn)
            report = verify_price_index(conn)
        finally:
            try:
                conn.close()
            except Exception:
                pass
        _INDEX_VERIFIED = True
        _note_index_health(report, source="startup_probe")
        if index_fault_flagged():
            # The probe succeeded but the marker survived, which means we could
            # not record the outcome. Say so rather than claim a clean bill.
            logger.warning("Price index probed clean but the fault marker persists")
        return report if report.get("rebuilt") else None
    except Exception as exc:
        logger.error(f"Price-index verification failed: {exc}")
        # "Failed to delete all rows from index" is FATAL: DuckDB invalidates
        # this process's database instance, so the connection that just died
        # cannot repair anything. Record the fault first (it is what the next
        # process - or the fresh connection below - needs), then try the
        # rebuild here so the heal is not silently deferred to a restart that
        # never reports it.
        try:
            note_index_fault(exc)
        except Exception:
            pass
        repaired = _rebuild_price_index_after_probe_failure(exc)
        if repaired is None:
            _note_index_health(
                {"rebuilt": False, "trigger": None, "error": str(exc)[:200]},
                source="startup_probe_failed",
            )
            return {"error": str(exc)}
        _INDEX_VERIFIED = True
        return repaired


def _rebuild_price_index_after_probe_failure(cause: Exception) -> Optional[dict]:
    """Repair the prices table on a brand-new connection.

    Returns the rebuild report, or None when even the fresh connection cannot
    be opened - in which case the fault marker stays put and the next tick
    retries, rather than a failed repair being reported as a clean index.
    """
    try:
        from mandi_rdd.storage.duckdb_store import (
            ensure_price_index,
            get_connection,
            index_fault_flagged,
        )
        conn = get_connection()
        try:
            init_schema(conn)
            report = ensure_price_index(conn, force=True)
        finally:
            try:
                conn.close()
            except Exception:
                pass
        if index_fault_flagged():
            logger.warning("The rebuild completed but the fault marker persists")
        report["cause"] = str(cause)[:200]
        _note_index_health(report, source="startup_rebuild")
        return report
    except Exception as exc:
        logger.error(f"Price-index rebuild after a failed probe did not complete: {exc}")
        return None


def _health_status(quality: dict) -> str:
    """Turn the warehouse's own state into the /health `status` field.

    This used to be the literal string "healthy", which is how the deployment
    served July prices with a green checkmark. `status` now describes the data;
    `last_outcome` still describes the most recent pipeline run.
    """
    if not quality or not quality.get("n_rows"):
        return "empty"
    if int(quality.get("n_future_dates") or 0) > 0:
        return "degraded"
    days_behind = quality.get("days_behind")
    if days_behind is None:
        return "unknown"
    return "stale" if int(days_behind) > STALE_AFTER_DAYS else "healthy"


@functools.lru_cache(maxsize=1)
def _count_tests() -> int:
    """Count the test functions shipped with this build.

    /health must not report a number nobody can verify, so the count is read
    from the test suite instead of being hardcoded.
    """
    tests_dir = Path(__file__).resolve().parent.parent / "tests"
    try:
        return sum(
            1
            for path in tests_dir.glob("*.py")
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.lstrip().startswith("def test_")
        )
    except Exception:
        return 0


state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load state on startup."""
    logger.info("Starting MandiRDD API...")
    conn = get_connection()
    init_schema(conn)
    
    # Check data freshness and auto-trigger pipeline if needed
    should_trigger_pipeline = False
    try:
        df = conn.execute("SELECT DISTINCT commodity FROM prices ORDER BY commodity").fetchdf()
        state.commodities = df["commodity"].tolist() if len(df) > 0 else []
        n_prices = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
        n_rainfall = conn.execute("SELECT COUNT(*) FROM rainfall").fetchone()[0]
        n_rdd = conn.execute("SELECT COUNT(*) FROM rdd_results").fetchone()[0]
        
        logger.info(f"Startup data check: {n_prices} prices, {n_rainfall} rainfall, {n_rdd} RDD results")
        should_trigger_pipeline = (n_prices < 100 or n_rainfall < 10 or n_rdd < 1)
    except Exception:
        state.commodities = []
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if should_trigger_pipeline:
        # This used to start the FULL pipeline in its own thread the moment the
        # warehouse looked empty - which is exactly when the container can least
        # afford it. On the 512 MB free tier that is the heaviest thing this
        # service does, and it ran before the readiness probe could pass, so an
        # OOM there is a crash loop the platform edge reports as
        # `503 no healthy upstream` on every route.
        #
        # Repairing an empty warehouse is POST /refresh (bounded by
        # MANDIIQ_PRICE_FETCH_MAX_SECONDS), POST /admin/rebuild-prices and POST
        # /admin/restore-from-r2 - all of which run while the container is
        # already serving. The self-refresh scheduler below picks the work up on
        # its own schedule, so nothing is lost by not racing the health check.
        if _self_refresh_enabled():
            logger.warning(
                "Data is stale or missing - the self-refresh scheduler will run "
                "the pipeline in %ss, after the readiness probe can pass",
                _self_refresh_initial_delay_s(),
            )
        else:
            logger.warning(
                "Data is stale or missing and MANDIIQ_SELF_REFRESH=0, so this "
                "container will not run a pipeline; POST /refresh to run one by "
                "hand, or POST /admin/restore-from-r2 to restore the warehouse"
            )

    metrics_push.start_push_thread()

    # Warm the in-memory dashboard cache so heartbeat shows Fresh on boot
    global _dashboard_last_refresh, _dashboard_file_mtime
    if dashboard_json is not None:
        _dashboard_last_refresh = time.time()
        _dashboard_file_mtime = os.path.getmtime(_dashboard_path)
        _get_patched_dashboard("Grafana")
        logger.info("Dashboard cache warmed: %d entries", _dashboard_patch_count)
    
    # Start the self-refresh scheduler. This container is the only place with
    # the production credentials and the durable volume, so it keeps its own
    # data current rather than waiting for an external cron to nudge it.
    interval_s = _self_refresh_interval_s()
    _REFRESH_STATE["interval_s"] = interval_s

    if _self_refresh_enabled():
        def _self_refresh_loop():
            import time as _t
            _t.sleep(_self_refresh_initial_delay_s())
            while True:
                try:
                    summary = _refresh_once()
                    logger.info(f"Self-refresh finished: {summary.get('status')}")
                except Exception as e:
                    # The loop must outlive any single bad run.
                    logger.error(f"Self-refresh loop error: {e}")
                _t.sleep(interval_s)

        threading.Thread(target=_self_refresh_loop, daemon=True).start()
        logger.info(
            f"Self-refresh scheduler started: first run in "
            f"{_self_refresh_initial_delay_s()}s, then every {interval_s}s"
        )
    else:
        logger.info("Self-refresh scheduler disabled (MANDIIQ_SELF_REFRESH=0)")

    yield


app = FastAPI(
    title="MandiRDD API",
    description="""
    Automated Mandi Price Discontinuity Engine.
    
    Pulls daily mandi prices from data.gov.in, joins with rainfall
    departure data, and runs a Regression Discontinuity Design (RDD)
    to detect price jumps around the -19% rainfall deficiency threshold.
    
    **Endpoints:**
    * `/health` - Liveness check + data counts
    * `/prices` - Query stored prices by state/district/commodity
    * `/rdd-result/{commodity}` - Latest RDD estimate for a commodity
    * `/rdd-plot/{commodity}` - Binned scatter data for the discontinuity plot
    * `/robustness/{commodity}` - Full robustness check bundle
    * `/forecast/{commodity}` - Prophet forecast with optional LSTM comparison
    * `/risk-score/{commodity}` - XGBoost price-spike risk probability
    * `/recommendation/{commodity}` - Procurement recommendation
    * `/ask` - AI orchestrator (OpenRouter multi-model routing, circuit-breaker fallback)
    * `/refresh` - Manual re-run of the full pipeline
    """,
    version="2.4.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Recovery capability ──

# 2.4.0 is the first build that can repair the warehouse without risking it:
# the prices rebuild is atomic and memory-capped (mandi_rdd/storage/
# duckdb_store.py) and the R2 restore streams to disk instead of holding the
# archive in RAM. The auto-heal workflow refuses to trigger either operation
# unless the running build advertises this.
#
# `safe_recovery` used to be the literal `True` on every build, so the workflow
# handed a non-atomic 2.3.0 container a rebuild it could not survive. On
# 2026-10-01 that container crash-looped and the platform edge answered
# `503 no healthy upstream` on /health, / and /docs. A capability that gates a
# destructive repair has to be derived from the build that is actually running.
SAFE_RECOVERY_VERSION = (2, 4, 0)


def _version_tuple(value) -> tuple:
    """Parse "2.4.0" / "2.4.0-rc1" into a comparable (major, minor, patch)."""
    parts = []
    for chunk in str(value or "").split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _recovery_is_safe() -> bool:
    """Whether THIS build may rebuild or restore the warehouse unattended."""
    return _version_tuple(getattr(app, "version", None)) >= SAFE_RECOVERY_VERSION


# ── Endpoints ──

@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health():
    health_stats.health_count += 1
    """Liveness check with full data counts for the documentation page."""
    conn = None
    try:
        conn = get_connection()
        init_schema(conn)
        
        n_prices = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
        n_commodities = conn.execute("SELECT COUNT(DISTINCT commodity) FROM prices").fetchone()[0]
        n_states = conn.execute("SELECT COUNT(DISTINCT state) FROM prices").fetchone()[0]
        n_districts = conn.execute("SELECT COUNT(DISTINCT district) FROM prices").fetchone()[0]
        n_rainfall = conn.execute("SELECT COUNT(*) FROM rainfall").fetchone()[0]
        n_rainfall_filtered = conn.execute(
            "SELECT COUNT(*) FROM rainfall WHERE departure_pct BETWEEN -100 AND 200"
        ).fetchone()[0]
        rainfall_below = conn.execute(
            "SELECT COUNT(*) FROM rainfall WHERE departure_pct < -19"
        ).fetchone()[0]
        n_rdd = conn.execute("SELECT COUNT(*) FROM rdd_results").fetchone()[0]

        n_ndvi = None
        n_ndvi_districts = None
        try:
            n_ndvi = conn.execute("SELECT COUNT(*) FROM ndvi").fetchone()[0]
            n_ndvi_districts = conn.execute(
                "SELECT COUNT(DISTINCT district) FROM ndvi"
            ).fetchone()[0]
        except Exception:
            pass

        # Read last ingest status
        last_run_utc = None
        last_outcome = None
        last_price_source = None
        try:
            status_path = (
                Path(__file__).resolve().parent.parent / "data" / "last_ingest_status.json"
            )
            if status_path.exists():
                with open(status_path) as f:
                    record = json.load(f)
                last_run_utc = record.get("last_run_utc")
                last_outcome = record.get("outcome")
                last_price_source = record.get("price_source")
        except Exception:
            pass

        quality = _cached_date_quality(conn)

        # The in-process record is lost on the restart a fatal index fault
        # causes, so fall back to the record the scheduler persisted with the
        # last pipeline run before reporting anything about the price index.
        index_check = _REFRESH_STATE.get("last_index_check") or _persisted_index_health()
        index_repair = _REFRESH_STATE.get("last_index_repair")
        if index_repair is None and isinstance(index_check, dict) and index_check.get("rebuilt"):
            index_repair = index_check

        return HealthResponse(
            status=_health_status(quality),
            llm_fallback_count=get_llm_fallback_count(),
            n_prices=n_prices,
            n_commodities=n_commodities,
            n_states=n_states,
            n_districts=n_districts,
            n_rainfall=n_rainfall,
            n_rainfall_filtered=n_rainfall_filtered,
            rainfall_below_threshold=rainfall_below,
            n_rdd_results=n_rdd,
            n_ndvi=n_ndvi,
            n_ndvi_districts=n_ndvi_districts,
            last_run_utc=last_run_utc,
            last_outcome=last_outcome,
            version=app.version,
            n_tests=_count_tests(),
            data_max_date=quality.get("max_date"),
            data_min_date=quality.get("min_date"),
            days_behind=quality.get("days_behind"),
            hours_since_last_run=_hours_since(last_run_utc),
            n_future_dates=int(quality.get("n_future_dates") or 0),
            ingestion_running=_ingestion_running(),
            last_refresh_attempt_utc=_REFRESH_STATE["last_attempt_utc"],
            last_refresh_success_utc=_REFRESH_STATE["last_success_utc"],
            last_refresh_error=_REFRESH_STATE["last_error"],
            last_index_repair=index_repair,
            last_index_check=index_check,
            index_fault_pending=_index_fault_pending(),
            safe_recovery=_recovery_is_safe(),
            last_price_source=last_price_source,
            mirror_configured=_ceda_configured(),
            refresh_runs=int(_REFRESH_STATE["runs"]),
            refresh_failures=int(_REFRESH_STATE["failures"]),
            refresh_interval_s=int(_REFRESH_STATE["interval_s"]),
            commodities_analyzed=state.commodities[:20],
        )
    except Exception:
        return HealthResponse(
            status="degraded",
            llm_fallback_count=get_llm_fallback_count(),
            n_prices=0, n_commodities=0, n_states=0,
            n_districts=0, n_rainfall=0, n_rainfall_filtered=0,
            rainfall_below_threshold=0, n_rdd_results=0,
            n_ndvi=None, n_ndvi_districts=None,
            last_run_utc=None, last_outcome="error",
            commodities_analyzed=[],
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


@app.get("/data-quality", tags=["System"])
async def data_quality_endpoint():
    """Warehouse date integrity and live-data provenance.

    Reports the newest arrival date actually present, how many days behind
    today that is, and how many rows carried an impossible (future) arrival
    date. Every surface that claims "live" can be audited against this.
    """
    conn = get_connection()
    init_schema(conn)
    try:
        from mandi_rdd.core.dates import date_quality as _date_quality
        report = _date_quality(conn)
        report["n_prices"] = int(
            conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] or 0
        )
        report["ingestion_running"] = _ingestion_running()
        report["version"] = app.version
        return report
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.post("/admin/repair-dates", tags=["Admin"])
async def admin_repair_dates(dry_run: bool = Query(True)):
    """Correct price rows whose arrival date cannot be true.

    A future arrival date is the inverse of a month-first mis-parse of a
    DD/MM/YYYY source, so the day and month are swapped back
    (2026-12-09 -> 2026-09-12). Rows that stay impossible are dropped.
    Pass ``dry_run=false`` to apply; defaults to a report-only dry run.
    """
    conn = get_connection()
    init_schema(conn)
    try:
        from mandi_rdd.core.dates import date_quality as _date_quality
        from mandi_rdd.core.dates import repair_future_dates
        report = repair_future_dates(conn, dry_run=dry_run)
        report["quality_after"] = _date_quality(conn)
        return report
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/fdr", tags=["Analytics"])
async def fdr(alpha: float = Query(0.05, ge=0.001, le=0.5)):
    """Benjamini-Hochberg control across every stored commodity estimate.

    With 400+ commodities each fitted at p < 0.05, roughly one in twenty looks
    significant by chance. q-values say how many survive that correction.

    Collapsed fits (a numerically zero discontinuity, ~1e-12 rupees, with a
    zero-variance standard error) are not hypotheses and are excluded from the
    family; they are listed under ``degenerate`` with a count so the exclusion
    is visible rather than silent.
    """
    conn = get_connection()
    init_schema(conn)
    try:
        from mandi_rdd.analysis.spec_curve import fdr_report
        return fdr_report(conn, alpha=alpha)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/spec-curve/{commodity}", tags=["Analytics"])
async def spec_curve_endpoint(commodity: str, cutoff: float = Query(-19.0)):
    """Fit the RDD across bandwidths, kernels and polynomial orders.

    A headline effect is one point in a space of defensible choices; the curve
    reports the distribution so a fragile result cannot pass as a firm one.
    """
    conn = get_connection()
    init_schema(conn)
    try:
        from mandi_rdd.analysis.spec_curve import spec_curve_report
        report = spec_curve_report(conn, commodity, cutoff=cutoff)
        if report.get("error"):
            raise HTTPException(status_code=404, detail=report["error"])
        return report
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.post("/admin/backfill-ceda", tags=["Admin"])
async def admin_backfill_ceda(
    max_calls: int = Query(25, ge=1, le=400, description="Calls to spend in this pass (paced ~1/s)."),
    window_days: int = Query(90, ge=1, le=730, description="Days covered by each window."),
    until_date: Optional[str] = Query(None, description="Stop once the window would end before this date (YYYY-MM-DD)."),
    commodity: Optional[str] = Query(None, description="Limit the pass to one commodity name."),
    dry_run: bool = Query(False, description="Read the archive without writing rows."),
):
    """Fill history from the CEDA archive, walking backwards from the oldest row.

    The scheduled CEDA step asks for the trailing ``MANDIIQ_CEDA_LOOKBACK_DAYS``
    window, which is the right question for a live mirror and the wrong one for
    this one: CEDA's daily coverage stops around 2025-10, so "the last seven
    days" is a window the archive can never answer. Arming the token therefore
    changed nothing - every tick asked about the future of a frozen archive.

    A backfill has to ask about the past, so this walks backwards. The window
    ends the day before the oldest arrival date the warehouse holds; a pass
    that lands rows moves that date, which is what makes the next pass continue
    where this one stopped. There is no cursor to drift - the warehouse is the
    cursor - and re-reading a window is harmless because upserts are idempotent.
    ``until_date`` (or MANDIIQ_CEDA_BACKFILL_FLOOR, default 2001-01-01) is the
    floor where the walk stops and reports itself complete.

    Rows land with market = district name and grade "Agmarknet daily (CEDA)",
    so they are never confused with the variety-level rows the primary feed
    writes. This endpoint cannot make the newest date current, and does not
    claim to: it only moves the oldest one.
    """
    if not _ceda_configured():
        raise HTTPException(
            status_code=400,
            detail=(
                "MANDIIQ_CEDA_API_KEY is not set, so the CEDA archive cannot be "
                "read. A token comes from "
                "https://api.ceda.ashoka.edu.in/documentation/"
            ),
        )

    from mandi_rdd.ingestion.fetch_ceda import iter_ceda_pages
    from mandi_rdd.storage.duckdb_store import upsert_prices

    conn = get_connection()
    init_schema(conn)
    try:
        before = _cached_date_quality(conn) or {}
        oldest_raw = before.get("min_date")
        if not oldest_raw:
            return {
                "status": "no_anchor",
                "reason": "the warehouse holds no dated price rows to walk back from",
                "oldest_date": None,
            }

        oldest = datetime.date.fromisoformat(str(oldest_raw)[:10])
        window_end = oldest - datetime.timedelta(days=1)
        floor = datetime.date.fromisoformat(
            until_date
            or os.environ.get("MANDIIQ_CEDA_BACKFILL_FLOOR", "2001-01-01")
        )
        if window_end < floor:
            return {
                "status": "complete",
                "reason": (
                    f"the oldest row ({oldest.isoformat()}) is at or before the "
                    f"floor ({floor.isoformat()}) - nothing older to fetch"
                ),
                "oldest_date": oldest.isoformat(),
                "floor": floor.isoformat(),
            }

        window_start = window_end - datetime.timedelta(days=max(1, window_days) - 1)
        cursor: dict = {}
        seen = 0
        inserted = 0
        for page in iter_ceda_pages(
            lookback_days=window_days,
            max_calls=max_calls,
            start_index=0,
            cursor_out=cursor,
            commodity_names={commodity.lower()} if commodity else None,
            window_end=window_end,
        ):
            seen += len(page)
            if not dry_run:
                inserted += upsert_prices(conn, page)

        after = before if dry_run else (_cached_date_quality(conn) or {})
        newest_oldest = after.get("min_date")
        return {
            "status": "dry_run" if dry_run else "ok",
            "window": {"from": window_start.isoformat(), "to": window_end.isoformat()},
            "floor": floor.isoformat(),
            "oldest_date_before": oldest.isoformat(),
            "oldest_date_after": newest_oldest,
            "rows_returned": seen,
            "rows_written": inserted,
            # The walk always spends its whole budget unless the host refused
            # it, so an empty cursor is the signal that it never ran.
            "requests": max_calls if cursor else 0,
            "pairs_per_pass": cursor.get("total"),
            "next_index": cursor.get("offset"),
            "next_window_end": (
                (datetime.date.fromisoformat(str(newest_oldest)[:10]) - datetime.timedelta(days=1)).isoformat()
                if newest_oldest
                else None
            ),
            "hint": (
                "Call this again to keep walking backwards; each pass moves the "
                "oldest arrival date in the warehouse. It cannot advance the "
                "newest date - that needs a live source."
            ),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"backfill failed: {e}")
    finally:
        conn.close()


@app.post("/admin/rebuild-prices", tags=["Admin"])
async def admin_rebuild_prices():
    """Rebuild the prices table to clear an inconsistent ART index.

    DuckDB's unique index can be left inconsistent by a bulk load that runs out
    of memory; every later write touching those keys then fails with
    "Failed to delete all rows from index" and ingestion cannot make progress.
    Rebuilding re-derives the index from the data. Duplicate business keys are
    collapsed to their lowest id, so the row count can only fall.

    This runs the same verified heal the pipeline runs: it forces the rebuild,
    proves the new index with a write probe, and only then clears the recorded
    fault. It used to call rebuild_prices_table() directly, which never cleared
    the marker - so a manual heal left /health reporting a fault that was
    already repaired, and the next run rebuilt the whole table a second time.
    """
    conn = get_connection()
    init_schema(conn)
    try:
        from mandi_rdd.storage.duckdb_store import (
            ensure_price_index,
            index_fault_flagged,
        )
        report = ensure_price_index(conn, force=True)
        report["data_max_date"] = _cached_date_quality(conn).get("max_date")
        # There is no separate marker state to report: a successful rebuild
        # with a passing probe clears it, so this is False after a clean heal.
        report["index_fault_pending"] = index_fault_flagged()
        _note_index_health(report, source="admin_rebuild")
        return report
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


def _probe_enam(timeout: float = 15.0) -> dict:
    """Probe eNAM, the last live candidate that answers a cloud network.

    The dashboard (``enam.gov.in/dashboard/agmarknet``) returns HTTP 200 and
    carries the current date at render time, so the page alone looks like a
    live feed. The data behind it does not. The controller the dashboard's own
    JavaScript calls is ``Agm_ctrl`` - ``Ajax_ctrl`` only serves the CSV export
    form - and both answer HTTP 500 with an empty body on every request shape
    available from outside India: POST and GET, with and without Referer,
    Origin, X-Requested-With and Accept, and with the ``ci_session`` cookie
    minted by that same dashboard a moment earlier. A 500 that fast, on every
    shape, at every path spelling (``/index.php/...`` and a trailing slash
    included), is the application refusing the caller rather than the request
    being malformed.

    So this probe mints the session and posts the page's own price query, and
    reports what came back instead of calling a reachable page a reachable
    feed. A JSON body means eNAM can serve rows and an adapter is worth
    writing; an empty 500 is the evidence that it cannot.
    """
    page_url = "https://enam.gov.in/dashboard/agmarknet"
    data_url = "https://enam.gov.in/Agm_ctrl/trade_data_list"
    agent = "Mozilla/5.0 (compatible; MandiIQ-source-probe)"

    started = time.monotonic()
    cookie = None
    try:
        page = urllib.request.Request(page_url, headers={"User-Agent": agent})
        with urllib.request.urlopen(page, timeout=timeout) as response:
            for raw in response.headers.get_all("Set-Cookie") or []:
                if raw.startswith("ci_session="):
                    cookie = raw.split(";", 1)[0]
                    break
    except Exception as exc:
        return {
            "ok": False,
            "status": None,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "serves_json": False,
            "cookie_minted": False,
            "error": f"{type(exc).__name__}: {exc}",
            "verdict": "the eNAM dashboard itself is unreachable from here",
        }

    # The window the dashboard sends: the last seven days as DD-MM-YYYY. Every
    # filter empty, which is what the page does on first paint.
    today = datetime.date.today()
    body = (
        "language=en&stateName=&districtName=&apmcName=&commodityName="
        f"&fromDate={(today - datetime.timedelta(days=7)).strftime('%d-%m-%Y')}"
        f"&toDate={today.strftime('%d-%m-%Y')}"
    ).encode()
    headers = {
        "User-Agent": agent,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": page_url,
        "Origin": "https://enam.gov.in",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if cookie:
        headers["Cookie"] = cookie

    request = urllib.request.Request(data_url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read(262144)
            elapsed = int((time.monotonic() - started) * 1000)
            text = payload.decode("utf-8", "replace").lstrip()
            is_json = text[:1] in "{["
            rows = None
            if is_json:
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict) and isinstance(parsed.get("data"), list):
                        rows = len(parsed["data"])
                except Exception:
                    rows = None
            return {
                "ok": bool(is_json),
                "status": response.status,
                "latency_ms": elapsed,
                "serves_json": is_json,
                "cookie_minted": bool(cookie),
                "rows": rows,
                "controller": "Agm_ctrl/trade_data_list",
                "verdict": (
                    f"live candidate - eNAM served {rows if rows is not None else 'a JSON'} "
                    "row(s); an adapter for it would make the warehouse current"
                    if is_json
                    else "answered a page, not data"
                ),
            }
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "status": exc.code,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "serves_json": False,
            "cookie_minted": bool(cookie),
            "controller": "Agm_ctrl/trade_data_list",
            "error": f"HTTP {exc.code}",
            "verdict": (
                "the dashboard is up but every data call returns an empty "
                f"HTTP {exc.code}, session cookie included - not a usable live source"
                if exc.code >= 500
                else f"blocked with HTTP {exc.code}"
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": None,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "serves_json": False,
            "cookie_minted": bool(cookie),
            "controller": "Agm_ctrl/trade_data_list",
            "error": f"{type(exc).__name__}: {exc}",
            "verdict": "the data controller is unreachable from this network",
        }


@app.get("/admin/source-probe", tags=["Admin"])
async def admin_source_probe(timeout: float = 20.0):
    """Ask every configured price source whether it can be reached from here.

    Written because "the data is stale" has two completely different causes -
    the upstream has nothing newer, or we cannot reach the upstream at all -
    and they used to look identical from the outside. The probe runs from the
    same network (and the same container) as the ingest, reports the newest
    arrival date each host serves, and checks the CEDA mirror, which is the one
    Agmarknet host that answers cloud networks.

    curl -sS "$API/admin/source-probe" | python3 -m json.tool
    """
    import anyio

    def run() -> dict:
        report: dict = {
            "checked_at": datetime.datetime.utcnow().isoformat() + "Z",
            "sources": [],
            "ceda": {},
            "live_paths": {
                "data_gov_in": "primary - unreachable from cloud networks",
                "ceda_mirror": (
                    "reachable, needs MANDIIQ_CEDA_API_KEY - but ARCHIVE ONLY: "
                    "measured coverage ends around 2025-10, so it backfills "
                    "history and cannot make the newest date current"
                ),
                "enam": "reachable live dashboard; data endpoints session-gated",
            },
        }
        try:
            from mandi_rdd.ingestion.fetch_prices import source_diagnostics
            report["sources"] = source_diagnostics(timeout=timeout)
        except Exception as exc:
            report["sources"] = [
                {"host": "unknown", "ok": False, "error": f"{type(exc).__name__}: {exc}"}
            ]
        try:
            from mandi_rdd.ingestion.fetch_ceda import probe as ceda_probe
            report["ceda"] = ceda_probe(timeout=timeout)
        except Exception as exc:
            report["ceda"] = {
                "configured": False,
                "reachable": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        try:
            report["enam"] = _probe_enam(timeout=min(timeout, 15.0))
        except Exception as exc:  # pragma: no cover - defensive
            report["enam"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        try:
            status_path = (
                Path(__file__).resolve().parent.parent / "data" / "last_ingest_status.json"
            )
            if status_path.exists():
                with open(status_path) as f:
                    record = json.load(f)
                report["last_ingest"] = {
                    key: record.get(key)
                    for key in (
                        "last_run_utc", "outcome", "error", "price_source",
                        "data_max_date", "days_behind",
                    )
                }
        except Exception:
            pass
        # The verdict the caller actually needs, split in two because the
        # sources are not interchangeable: one set can move today's date, the
        # other can only fill the past. Reporting them as a single "can ingest"
        # boolean is how "the mirror is armed" got mistaken for "the data will
        # be current".
        data_gov_ok = any(s.get("ok") for s in report["sources"])
        ceda = report.get("ceda") or {}
        ceda_ok = bool(ceda.get("configured") and ceda.get("reachable"))
        enam = report.get("enam") or {}
        enam_ok = bool(enam.get("ok"))

        report["can_ingest_live_data"] = bool(data_gov_ok or enam_ok)
        report["can_backfill_history_only"] = bool(ceda_ok and not report["can_ingest_live_data"])

        if not report["can_ingest_live_data"]:
            parts = [
                "No source measured here can advance the newest date. "
                "api.data.gov.in is blocked from cloud networks."
            ]
            if ceda_ok:
                parts.append(
                    "The CEDA mirror is armed and reachable, but its archive ends "
                    "around 2025-10 - it backfills history and will not close the "
                    "gap to today."
                )
            else:
                parts.append(
                    "CEDA is not armed; setting MANDIIQ_CEDA_API_KEY lets it "
                    "backfill history (token from "
                    "https://api.ceda.ashoka.edu.in/documentation/)."
                )
            enam_verdict = enam.get("verdict")
            if enam_verdict:
                parts.append(f"eNAM: {enam_verdict}.")
            report["hint"] = " ".join(parts)
        return report

    return await anyio.to_thread.run_sync(run)


@app.get("/freshness", tags=["System"])
async def freshness(commodity: Optional[str] = None):
    """Per-commodity data freshness: latest date, row count, district coverage."""
    conn = get_connection()
    init_schema(conn)
    try:
        where = ""
        params = []
        if commodity:
            where = "WHERE LOWER(commodity) = LOWER(?)"
            params = [commodity]
        rows = conn.execute(f"""
            SELECT
                commodity,
                MAX(arrival_date) AS latest_date,
                MIN(arrival_date) AS earliest_date,
                COUNT(*) AS row_count,
                COUNT(DISTINCT district) AS n_districts,
                COUNT(DISTINCT state) AS n_states
            FROM prices
            {where}
            GROUP BY commodity
            ORDER BY latest_date DESC
            LIMIT 200
        """, params).fetchall()
        records = []
        cols = ["commodity", "latest_date", "earliest_date", "row_count", "n_districts", "n_states"]
        for r in rows:
            rec = dict(zip(cols, r))
            rec["source_type"] = "prices_table"
            rec["source_name"] = ""
            rec["updated_at"] = None
            records.append(rec)
        return records
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/prices", response_model=list[PriceRecord], tags=["Data"])
async def prices(
    state: Optional[str] = Query(None),
    district: Optional[str] = Query(None),
    commodity: Optional[str] = Query(None),
    limit: int = Query(100, le=5000),
):
    """Query stored prices with optional filters."""
    conn = get_connection()
    init_schema(conn)
    
    df = get_prices(conn, state=state, district=district, commodity=commodity, limit=limit)
    conn.close()
    
    records = df.to_dict("records")
    for r in records:
        ad = r.get("arrival_date")
        if ad is not None and hasattr(ad, "strftime"):
            r["arrival_date"] = ad.strftime("%Y-%m-%d")
    return [
        PriceRecord(
            state=r["state"],
            district=r["district"],
            market=r["market"],
            commodity=r["commodity"],
            variety=r.get("variety"),
            arrival_date=r["arrival_date"],
            modal_price=r.get("modal_price"),
            min_price=r.get("min_price"),
            max_price=r.get("max_price"),
        )
        for r in records
    ]


@app.get("/rdd-result/{commodity}", response_model=RDDResult, tags=["Analysis"])
async def rdd_result(commodity: str):
    """Get the latest RDD estimate for a commodity."""
    conn = get_connection()
    init_schema(conn)
    
    # Try to get cached result first
    cached = get_latest_rdd(conn, commodity)
    
    if cached and cached.get("effect") is not None:
        conn.close()
        return RDDResult(
            commodity=commodity,
            effect=cached["effect"],
            p_value=cached["p_value"],
            std_error=cached["std_error"],
            n_left=cached["n_left"],
            n_right=cached["n_right"],
            interpretation=cached.get("interpretation", ""),
            error=None,
        )
    
    # Run fresh RDD
    try:
        from mandi_rdd.analysis.rdd_engine import run_rdd
        result = run_rdd(conn, commodity=commodity)
        conn.close()
        
        return RDDResult(
            commodity=commodity,
            effect=result.get("effect"),
            p_value=result.get("p_value"),
            std_error=result.get("std_error"),
            n_left=result.get("n_left"),
            n_right=result.get("n_right"),
            interpretation=result.get("interpretation", ""),
            error=result.get("error"),
        )
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/rdd-plot/{commodity}", tags=["Analysis"])
async def rdd_plot(commodity: str):
    """Get binned scatter plot data for the RDD discontinuity chart."""
    conn = get_connection()
    init_schema(conn)
    
    try:
        price_df = get_monthly_avg_prices(conn, commodity=commodity)
        
        if len(price_df) < 20:
            conn.close()
            return {"error": f"Insufficient data: {len(price_df)} monthly observations"}
        
        from mandi_rdd.ingestion.fetch_rainfall import load_district_subdivision_map
        district_map = load_district_subdivision_map()
        price_df["sub_division"] = price_df.apply(
            lambda r: district_map.get((r["state"], r["district"]), None),
            axis=1,
        )
        price_df = price_df.dropna(subset=["sub_division"])
        
        rainfall_df = conn.execute("SELECT * FROM rainfall").fetchdf()
        merged = price_df.merge(
            rainfall_df,
            on=["sub_division", "year", "month"],
            how="inner",
        )
        merged = merged.dropna(subset=["departure_pct", "avg_modal_price"])
        conn.close()
        
        if len(merged) < 20:
            return {"error": f"Insufficient matched data: {len(merged)} observations"}
        
        x = merged["departure_pct"].values
        y = merged["avg_modal_price"].values
        
        from mandi_rdd.analysis.rdd_engine import rdd_plot_data
        plot_data = rdd_plot_data(x, y, cutoff=-19.0)
        return plot_data
        
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/forecast/{commodity}", tags=["Forecast"])
async def forecast(
    commodity: str,
    state: Optional[str] = None,
    compare: bool = Query(False, description="If true, returns Prophet vs LSTM side-by-side comparison"),
):
    """
    Get a Prophet forecast for a commodity's modal price.
    
    When `compare=true`, returns Prophet vs LSTM side-by-side metrics
    with an honest winner callout and explanation.
    """
    conn = get_connection()
    init_schema(conn)
    
    if compare:
        from mandi_rdd.analysis.forecast import compare_forecast_models
        result = compare_forecast_models(conn, commodity=commodity, state=state)
        conn.close()
        if "error" in result:
            return {"status": "unavailable", "commodity": commodity, "reason": result["error"], "forecast": [], "metrics": {}}
        return result
    
    from mandi_rdd.analysis.forecast import get_forecast_summary
    result = get_forecast_summary(conn, commodity=commodity)
    conn.close()
    
    if "error" in result:
        return {"status": "unavailable", "commodity": commodity, "reason": result["error"], "forecast": [], "metrics": {}}
    
    return result


@app.get("/robustness/{commodity}", tags=["Analysis"])
async def robustness(commodity: str):
    """Get the full robustness check bundle for a commodity."""
    conn = get_connection()
    init_schema(conn)
    
    from mandi_rdd.analysis.rdd_engine import run_rdd
    result = run_rdd(conn, commodity=commodity)
    conn.close()
    
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    
    return {
        "commodity": commodity,
        "main_effect": result.get("effect"),
        "p_value": result.get("p_value"),
        "bandwidth_sensitivity": result.get("bandwidth_sensitivity", []),
        "placebo_tests": result.get("placebo_tests", []),
        "density_test": result.get("density_test", {}),
        "covariate_balance": result.get("covariate_balance", {}),
        "fe_effect": result.get("fe_effect"),
        "fe_p_value": result.get("fe_p_value"),
    }


@app.get("/risk-score/{commodity}", tags=["Predictions"])
async def risk_score(
    commodity: str,
    district: Optional[str] = Query(None),
):
    """Get price-spike risk score for a commodity."""
    conn = get_connection()
    init_schema(conn)
    
    try:
        from mandi_rdd.analysis.classifier import predict_spike_risk
        result = predict_spike_risk(conn, commodity=commodity, district=district)
        conn.close()
        return result
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/recommendation/{commodity}", tags=["Predictions"])
async def recommendation(
    commodity: str,
    district: Optional[str] = Query(None),
):
    """Get a procurement recommendation for a commodity."""
    conn = get_connection()
    init_schema(conn)
    
    try:
        from mandi_rdd.analysis.prescriptive import compute_recommendation
        result = compute_recommendation(conn, commodity=commodity, district=district)
        conn.close()
        return result
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))


# ── Analytics Engine: uncertainty, drift, tail risk, DML, nowcast ──

@app.get("/analytics/{commodity}", tags=["Analytics"])
async def analytics_report(commodity: str):
    """
    Composite analyst deep-dive for one commodity.

    Runs all five analytics modules in one call: conformal prediction
    intervals, drift + data-quality monitoring, EVT tail risk, cross-fitted
    debiased ML rainfall sensitivity, and a Kalman month-end nowcast. Each
    section degrades independently.
    """
    conn = get_connection()
    init_schema(conn)
    try:
        from mandi_rdd.analysis.analytics import commodity_analytics
        return commodity_analytics(conn, commodity)
    except Exception as e:
        logger.error(f"Analytics report failed for {commodity}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/conformal/{commodity}", tags=["Analytics"])
async def conformal_intervals(commodity: str):
    """Distribution-free prediction intervals (split conformal) around the forecast."""
    conn = get_connection()
    init_schema(conn)
    try:
        from mandi_rdd.analysis.conformal import conformal_report
        return conformal_report(conn, commodity)
    except Exception as e:
        logger.error(f"Conformal report failed for {commodity}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/drift/{commodity}", tags=["Analytics"])
async def drift_report_endpoint(commodity: str):
    """PSI / KS / Page-Hinkley / EWMA drift plus a 0-100 data-quality score."""
    conn = get_connection()
    init_schema(conn)
    try:
        from mandi_rdd.analysis.drift import drift_report
        return drift_report(conn, commodity)
    except Exception as e:
        logger.error(f"Drift report failed for {commodity}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/tail-risk/{commodity}", tags=["Analytics"])
async def tail_risk_endpoint(commodity: str):
    """Historical VaR/CVaR, EVT tail fits and maximum drawdown."""
    conn = get_connection()
    init_schema(conn)
    try:
        from mandi_rdd.analysis.tail_risk import tail_risk_report
        return tail_risk_report(conn, commodity)
    except Exception as e:
        logger.error(f"Tail risk report failed for {commodity}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/dml/{commodity}", tags=["Analytics"])
async def dml_endpoint(commodity: str):
    """Cross-fitted debiased ML estimate of rainfall-price sensitivity."""
    conn = get_connection()
    init_schema(conn)
    try:
        from mandi_rdd.analysis.dml import dml_report
        return dml_report(conn, commodity)
    except Exception as e:
        logger.error(f"DML report failed for {commodity}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/nowcast/{commodity}", tags=["Analytics"])
async def nowcast_endpoint(commodity: str):
    """Kalman-filter month-end nowcast for incomplete reporting months."""
    conn = get_connection()
    init_schema(conn)
    try:
        from mandi_rdd.analysis.nowcast import nowcast_report
        return nowcast_report(conn, commodity)
    except Exception as e:
        logger.error(f"Nowcast failed for {commodity}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


# ── Phase 11: AI Orchestrator Endpoint ──

@app.post("/ask", response_model=AskResponse, tags=["AI Orchestrator"])
async def ask_question(request: AskRequest):
    """
    Ask a free-text procurement question to the AI orchestrator.
    
    The orchestrator:
    1. Detects the commodity and district from the query
    2. Calls the relevant internal analysis tools (RDD, forecast, risk score, etc.)
    3. Routes the question + tool results through the OpenRouter free-tier
       multi-model chain with circuit-breaker fallback
    4. Returns a grounded answer that only uses numbers from the tool calls
    
    **Example queries:**
    - "Should I lock in onion procurement in Nashik next month?"
    - "What's the price-spike risk for tomato in Maharashtra?"
    - "Summarize what changed this week for onion"
    - "How robust is the RDD finding for potato?"
    """
    try:
        from mandi_rdd.ai.orchestrator import answer_question
        
        result = answer_question(
            query=request.query,
            commodity=request.commodity,
            district=request.district,
        )
        
        return AskResponse(
            query=result.get("query", request.query),
            commodity=result.get("commodity", request.commodity or "Onion"),
            district=result.get("district", request.district or "All"),
            answer=result.get("answer", "Unable to generate an answer at this time."),
            model_used=result.get("model_used"),
            endpoints_used=result.get("endpoints_used", []),
            error=result.get("error"),
        )
    except ImportError as e:
        logger.error(f"AI orchestrator import failed: {e}")
        return AskResponse(
            query=request.query,
            commodity=request.commodity or "Onion",
            district=request.district or "All",
            answer="The AI orchestrator module is not available. "
                   "Install dependencies: pip install openai",
            model_used=None,
            endpoints_used=[],
            error=f"AI module not available: {e}",
        )
    except Exception as e:
        logger.error(f"Ask endpoint error: {e}")
        return AskResponse(
            query=request.query,
            commodity=request.commodity or "Onion",
            district=request.district or "All",
            answer="An error occurred while processing your question.",
            model_used=None,
            endpoints_used=[],
            error=str(e),
        )


@app.post("/refresh", response_model=RefreshResponse, tags=["System"])
async def refresh(commodity: Optional[str] = None):
    """Kick off a full pipeline re-run in the background.

    Because the pipeline (fetching prices, rainfall, RDD, forecast) can take
    several minutes, the task runs as a background job and this endpoint
    returns immediately. Track progress via GET /health (n_prices, last_run_utc)
    or GET /metrics.

    Args:
        commodity: Optional commodity filter to limit the pipeline run.
    """
    try:
        from mandi_rdd.ingestion.scheduler import run_ingestion

        def _run_pipeline(commodity_filter: str | None = None):
            import time as _t
            _start = _t.time()
            filters = {}
            if commodity_filter:
                filters["commodity"] = commodity_filter
            logger.info(f"Background pipeline starting (commodity={commodity_filter or 'all'})...")
            summary = run_ingestion(filters=filters if commodity_filter else None)

            # Generate nightly narrative if AI is configured
            from mandi_rdd.ai.router import get_api_key as _get_llm_key
            _llm_key = _get_llm_key()
            narrative_status = "skipped"
            if _llm_key:
                try:
                    from mandi_rdd.ai.orchestrator import generate_nightly_narrative
                    target = commodity_filter or "Onion"
                    narrative = generate_nightly_narrative(commodity=target)
                    narrative_status = "generated" if not narrative.get("error") else "failed"
                    logger.info(f"Nightly narrative for {target}: {narrative_status}")
                except Exception as e:
                    narrative_status = f"error: {e}"
                    logger.warning(f"Nightly narrative generation failed: {e}")
            duration = round(_t.time() - _start, 1)
            logger.info(f"Background pipeline finished in {duration}s: {summary}")

        threading.Thread(target=_run_pipeline, args=(commodity,), daemon=True).start()
        return RefreshResponse(
            status="ok",
            message=f"Pipeline started in background (commodity={commodity or 'all'}). Check /health or /metrics for progress.",
            duration_seconds=None,
        )
    except Exception as e:
        logger.error(f"Failed to start background pipeline: {e}")
        return RefreshResponse(
            status="error",
            message=f"Failed to start pipeline: {e}",
            duration_seconds=None,
        )


@app.get("/debug/rainfall-test", tags=["System"])
async def debug_rainfall_test():
    """Test Open-Meteo rainfall fetch connectivity and return diagnostic info.
    
    This endpoint helps debug rainfall fetch issues by testing Open-Meteo
    connectivity from the server and returning detailed diagnostics.
    """
    import urllib.request
    import json as _json
    from pathlib import Path as _Path
    
    results = {
        "coords_file_exists": False,
        "coords_file_path": "",
        "coords_count": 0,
        "district_mapping_count": 0,
        "open_meteo_test_url": "",
        "open_meteo_response": None,
        "open_meteo_error": None,
        "sample_subdivisions": [],
    }
    
    # Test 1: Check if district_coords.json exists
    try:
        coords_path = _Path(__file__).resolve().parent.parent.parent / "data" / "district_coords.json"
        results["coords_file_path"] = str(coords_path)
        results["coords_file_exists"] = coords_path.exists()
        if coords_path.exists():
            with open(coords_path) as f:
                coords = _json.load(f)
            results["coords_count"] = len(coords)
    except Exception as e:
        results["coords_error"] = str(e)
    
    # Test 2: Check district-subdivision mapping
    try:
        from mandi_rdd.ingestion.fetch_rainfall import load_district_subdivision_map
        dmap = load_district_subdivision_map()
        results["district_mapping_count"] = len(dmap)
    except Exception as e:
        results["mapping_error"] = str(e)
    
    # Test 3: Test Open-Meteo connectivity with a single coordinate
    try:
        test_lat, test_lon = 19.0760, 72.8777  # Mumbai
        test_url = (
            f"https://archive-api.open-meteo.com/v1/archive"
            f"?latitude={test_lat}&longitude={test_lon}"
            f"&start_date=2024-01-01&end_date=2024-01-31"
            f"&daily=precipitation_sum&timezone=Asia%2FKolkata"
        )
        results["open_meteo_test_url"] = test_url
        
        req = urllib.request.Request(test_url, headers={"User-Agent": "MandiIQ/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = _json.loads(resp.read())
            daily = data.get("daily", {})
            times = daily.get("time", [])
            precip = daily.get("precipitation_sum", [])
            results["open_meteo_response"] = {
                "status": "success",
                "days_returned": len(times),
                "sample_dates": times[:3] if times else [],
                "sample_precip": precip[:3] if precip else [],
            }
    except Exception as e:
        results["open_meteo_error"] = str(e)
    
    # Test 4: Try a mini rainfall fetch for 3 sub-divisions
    try:
        from mandi_rdd.ingestion.fetch_rainfall import fetch_rainfall_from_open_meteo
        import logging
        logging.basicConfig(level=logging.DEBUG)
        
        # This will fetch a small sample
        records = fetch_rainfall_from_open_meteo()
        results["rainfall_fetch_result"] = {
            "total_records": len(records),
            "sample_records": records[:2] if records else [],
        }
        if records:
            subdivs = list(set(r.get("sub_division") for r in records[:10]))
            results["sample_subdivisions"] = subdivs[:5]
    except Exception as e:
        results["rainfall_fetch_error"] = str(e)
    
    return results


@app.post("/run-rainfall-rdd", tags=["System"])
async def run_rainfall_rdd():
    """Fetch rainfall data and run RDD analysis directly.
    
    This is a targeted endpoint that skips price fetch (since prices
    are already loaded) and directly fetches rainfall, stores it,
    and runs RDD analysis for rain-sensitive commodities.
    """
    import threading
    
    def _do_rainfall_rdd():
        import time as _t
        _start = _t.time()
        try:
            from mandi_rdd.ingestion.fetch_rainfall import fetch_and_store_all_rainfall
            from mandi_rdd.storage.duckdb_store import get_connection, upsert_rainfall, save_rdd_result
            from mandi_rdd.analysis.rdd_engine import run_rdd
            
            logger.info("Rainfall+RDD: Starting rainfall fetch...")
            rainfall = fetch_and_store_all_rainfall()
            logger.info(f"Rainfall+RDD: Fetched {len(rainfall)} rainfall records")
            
            if rainfall:
                conn = get_connection()
                n_new = upsert_rainfall(conn, rainfall)
                conn.commit()
                logger.info(f"Rainfall+RDD: Stored {n_new} new rainfall records")
                
                # Run RDD for rain-sensitive + high-volume commodities
                rain_sensitive = ["Onion", "Tomato", "Potato", "Cabbage", "Cauliflower"]
                high_volume = ["Wheat", "Rice", "Paddy(Common)", "Paddy(Dhan)(Common)",
                    "Maize", "Soyabean", "Mustard", "Groundnut",
                    "Banana", "Mango", "Apple", "Grapes",
                    "Garlic", "Ginger (Dry)", "Chili Red", "Turmeric",
                    "Bajra(Pearl Millet/Cumbu)", "Jowar (Sorghum)",
                    "Bengal Gram (Gram)(Whole)", "Red Gram",
                    "Green Gram (Moong)(Whole)", "Black Gram (Urad Beans)(Whole)",
                    "Sugarcane", "Cotton"]
                # Only run for commodities that exist in DB
                all_comms = set()
                try:
                    df_c = conn.execute("SELECT DISTINCT commodity FROM prices").fetchdf()
                    all_comms = set(df_c["commodity"].tolist())
                except Exception:
                    pass
                target_comms = [c for c in rain_sensitive + high_volume if c in all_comms]
                rdd_count = 0
                for commodity in target_comms:
                    try:
                        result = run_rdd(conn, commodity)
                        if result and result.get("effect") is not None:
                            save_rdd_result(conn, result)
                            rdd_count += 1
                            logger.info(f"Rainfall+RDD: {commodity} effect={result.get('effect'):.4f} p={result.get('p_value'):.4f}")
                    except Exception as e:
                        logger.warning(f"Rainfall+RDD: {commodity} failed: {e}")
                
                conn.commit()
                conn.close()
                duration = round(_t.time() - _start, 1)
                logger.info(f"Rainfall+RDD: Complete in {duration}s - {len(rainfall)} rainfall, {rdd_count} RDD results")
            else:
                logger.warning("Rainfall+RDD: No rainfall data fetched")
        except Exception as e:
            logger.error(f"Rainfall+RDD failed: {e}")
    
    threading.Thread(target=_do_rainfall_rdd, daemon=True).start()
    return {"status": "ok", "message": "Rainfall fetch + RDD analysis started in background"}


@app.post("/backfill-historical", tags=["System"])
async def backfill_historical():
    """Fetch historical monthly prices from Ashoka CEDA and store in DuckDB."""
    import threading
    
    def _do_backfill():
        import time as _t
        _start = _t.time()
        try:
            from mandi_rdd.ingestion.fetch_historical_ashoka import main as ashoka_main
            import os
            
            hist_dir = Path(__file__).resolve().parent.parent / "data" / "historical"
            hist_dir.mkdir(parents=True, exist_ok=True)
            out_path = str(hist_dir / "agmarknet_ashoka.csv")
            
            logger.info("Historical backfill: Starting Ashoka CEDA fetch...")
            ashoka_main(["--out", out_path, "--workers", "8"])
            
            if os.path.exists(out_path):
                from mandi_rdd.ingestion.ingest_historical_csv import ingest_csv
                from mandi_rdd.storage.duckdb_store import get_connection
                conn = get_connection()
                n = ingest_csv(conn, out_path)
                conn.commit()
                conn.close()
                duration = round(_t.time() - _start, 1)
                logger.info(f"Historical backfill: Done in {duration}s - {n} rows")
                try:
                    os.remove(out_path)
                except Exception:
                    pass
            else:
                logger.warning("Historical backfill: No CSV produced")
        except Exception as e:
            logger.error(f"Historical backfill failed: {e}")
    
    threading.Thread(target=_do_backfill, daemon=True).start()
    return {"status": "ok", "message": "Historical backfill started in background"}


# ── R2 restore helpers ──────────────────────────────────────────────

def _r2_download() -> bytes:
    """Download the latest DuckDB backup from Cloudflare R2.
    Uses the S3-compatible API with AWS Signature V4 auth via
    urllib.request (no extra dependencies).
    Returns the raw gzip-compressed bytes from R2.
    Raises:
        ValueError: If R2 credentials are not configured.
        urllib.error.URLError: If the download fails.
    """
    bucket = os.environ.get("R2_BUCKET") or os.environ.get("R2_BUCKET_NAME") or ""
    account_id = os.environ.get("R2_ACCOUNT_ID") or ""
    access_key = os.environ.get("R2_ACCESS_KEY_ID") or ""
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY") or ""
    if not all([bucket, account_id, access_key, secret_key]):
        missing = [k for k, v in [
            ("R2_BUCKET", bucket), ("R2_ACCOUNT_ID", account_id),
            ("R2_ACCESS_KEY_ID", access_key), ("R2_SECRET_ACCESS_KEY", secret_key),
        ] if not v]
        raise ValueError(f"R2 restore: missing credentials: {', '.join(missing)}")
    endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
    key = "mandi_iq.duckdb.gz"
    url = f"{endpoint}/{bucket}/{key}"
    # AWS Signature V4 for S3 GET request
    service = "s3"
    region = "auto"
    now = datetime.datetime.utcnow()
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    # Step 1: Create canonical request
    method = "GET"
    canonical_uri = f"/{bucket}/{key}"
    canonical_querystring = ""
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    payload_hash = hashlib.sha256(b"").hexdigest()
    canonical_headers = (
        f"host:{account_id}.r2.cloudflarestorage.com\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
    )
    canonical_request = (
        f"{method}\n{canonical_uri}\n{canonical_querystring}\n"
        f"{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )
    # Step 2: Create string to sign
    algorithm = "AWS4-HMAC-SHA256"
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = (
        f"{algorithm}\n{amz_date}\n{credential_scope}\n"
        f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
    )
    # Step 3: Derive signing key
    def _sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()
    k_secret = f"AWS4{secret_key}".encode()
    k_date = _sign(k_secret, date_stamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    k_signing = _sign(k_service, "aws4_request")
    signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()
    # Step 4: Build authorization header
    auth_header = (
        f"{algorithm} Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    # Step 5: Make the request
    req = urllib.request.Request(url, headers={
        "Host": f"{account_id}.r2.cloudflarestorage.com",
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "Authorization": auth_header,
        "User-Agent": "MandiIQ/1.0",
    })
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    logger.info("R2 restore: downloaded %d bytes from s3://%s/%s", len(data), bucket, key)
    return data


def _r2_download_to(dest: Path) -> int:
    """Stream the R2 backup to `dest` without holding it in memory.

    The whole-file download this replaces held the compressed backup in RAM and
    then decompressed it in one gulp - roughly twice the database size at peak
    on a 512 MB container, which is why the first restore attempt was killed
    and answered 503. This writes 8 MB at a time to disk instead.
    """
    import urllib.request
    import hmac
    import hashlib

    bucket = os.environ.get("R2_BUCKET") or os.environ.get("R2_BUCKET_NAME") or ""
    account_id = os.environ.get("R2_ACCOUNT_ID") or ""
    access_key = os.environ.get("R2_ACCESS_KEY_ID") or ""
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY") or ""
    if not all([bucket, account_id, access_key, secret_key]):
        missing = [k for k, v in [
            ("R2_BUCKET", bucket), ("R2_ACCOUNT_ID", account_id),
            ("R2_ACCESS_KEY_ID", access_key), ("R2_SECRET_ACCESS_KEY", secret_key),
        ] if not v]
        raise ValueError(f"R2 restore: missing credentials: {', '.join(missing)}")

    endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
    key = "mandi_iq.duckdb.gz"
    url = f"{endpoint}/{bucket}/{key}"
    region = "auto"
    service = "s3"
    now = datetime.datetime.utcnow()
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(b"").hexdigest()
    canonical_headers = (
        f"host:{account_id}.r2.cloudflarestorage.com\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = (
        f"GET\n/{bucket}/{key}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )
    algorithm = "AWS4-HMAC-SHA256"
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = (
        f"{algorithm}\n{amz_date}\n{credential_scope}\n"
        f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
    )

    def _sign(key_bytes: bytes, msg: str) -> bytes:
        return hmac.new(key_bytes, msg.encode(), hashlib.sha256).digest()

    k_signing = _sign(
        _sign(_sign(_sign(f"AWS4{secret_key}".encode(), date_stamp), region), service),
        "aws4_request",
    )
    signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()
    auth_header = (
        f"{algorithm} Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    req = urllib.request.Request(url, headers={
        "Host": f"{account_id}.r2.cloudflarestorage.com",
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "Authorization": auth_header,
        "User-Agent": "MandiIQ/1.0",
    })
    written = 0
    with urllib.request.urlopen(req, timeout=600) as resp, open(dest, "wb") as out:
        while True:
            chunk = resp.read(8 * 1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            written += len(chunk)
    logger.info("R2 restore: streamed %d bytes from s3://%s/%s", written, bucket, key)
    return written


@app.post("/admin/restore-from-r2", tags=["Admin"])
async def admin_restore_from_r2():
    """Restore the DuckDB database from the latest Cloudflare R2 backup.
    Downloads mandi_iq.duckdb.gz from R2, decompresses it, and replaces
    the local DuckDB file. Existing connections to the old database will
    continue working until closed; subsequent calls to get_connection()
    will open the restored database.
    Useful for disaster recovery after data corruption or when the git LFS
    object is unavailable on a fresh deploy. Requires R2 credentials
    (R2_BUCKET, R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY)
    to be configured as environment variables.
    Returns:
        dict with status, message, bytes downloaded, and file size.
    """
    import gzip
    import shutil as _shutil
    from mandi_rdd.storage.duckdb_store import (DB_PATH, get_connection,
                                                reset_connection_state)

    gz_path = DB_PATH.with_suffix(".duckdb.gz.download")
    staged = DB_PATH.with_suffix(".duckdb.restored")
    try:
        bytes_downloaded = _r2_download_to(gz_path)
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    except urllib.error.HTTPError as e:
        return {
            "status": "error",
            "message": f"R2 download failed (HTTP {e.code}): {e.reason}",
        }
    except (TimeoutError, urllib.error.URLError, OSError) as e:
        return {"status": "error", "message": f"R2 download failed: {e}"}

    # Decompress in a stream, not in one gulp: the old code held the whole
    # compressed file and then the whole database in memory at once, which is
    # what killed the container mid-restore.
    try:
        with gzip.open(gz_path, "rb") as src, open(staged, "wb") as out:
            _shutil.copyfileobj(src, out, length=8 * 1024 * 1024)
        bytes_decompressed = staged.stat().st_size
    except Exception as e:
        return {"status": "error", "message": f"gzip decompression failed: {e}"}
    finally:
        try:
            gz_path.unlink(missing_ok=True)
        except OSError:
            pass

    # Verify the backup actually holds prices before it is allowed to replace
    # the live warehouse. A truncated or empty backup must never be published
    # as the database, which is the same mistake the old rebuild made.
    staged_rows = 0
    try:
        check = get_connection(db_path=staged, read_only=True)
        try:
            check.execute("SELECT 1")
            tables = [r[0] for r in check.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
            if "prices" in tables:
                staged_rows = int(
                    check.execute("SELECT COUNT(*) FROM prices").fetchone()[0] or 0
                )
        finally:
            check.close()
    except Exception as e:
        return {
            "status": "error",
            "message": f"the backup could not be opened: {e}",
        }
    if staged_rows <= 0:
        return {
            "status": "error",
            "message": (
                "the backup contains no price rows; refusing to replace the "
                "live warehouse with it"
            ),
            "bytes_decompressed": bytes_decompressed,
        }

    # Move the live file aside, then swap. Keeping the old file (rather than
    # unlinking it) means a failed restart can still be rolled back by hand.
    backup_path = DB_PATH.with_suffix(".duckdb.before-restore")
    try:
        if DB_PATH.exists():
            DB_PATH.replace(backup_path)
        staged.replace(DB_PATH)
        reset_connection_state(DB_PATH)
        logger.info(
            "R2 restore: replaced %s with %d bytes / %d price rows from R2",
            DB_PATH, bytes_decompressed, staged_rows,
        )
    except Exception as e:
        return {"status": "error", "message": f"File replacement failed: {e}"}

    # Refresh the commodity list for the health endpoint
    try:
        conn = get_connection()
        init_schema(conn)
        df = conn.execute("SELECT DISTINCT commodity FROM prices ORDER BY commodity").fetchdf()
        state.commodities = df["commodity"].tolist() if len(df) > 0 else []
        conn.close()
    except Exception as e:
        logger.warning("R2 restore: could not refresh commodity list: %s", e)
    return {
        "status": "ok",
        "message": "Database restored from R2 backup.",
        "bytes_downloaded": bytes_downloaded,
        "bytes_decompressed": bytes_decompressed,
        "price_rows": staged_rows,
        "db_path": str(DB_PATH),
    }


@app.post("/admin/ingest-historical", tags=["Admin"])
def admin_ingest_historical(file: UploadFile = File(...)):
    """Upload and ingest a historical CSV file into the prices table.
    Accepts Agmarknet, data.gov.in snapshot, or WFP/FAO food price CSVs.
    Uses DuckDB's native CSV reader for fast bulk import.

    Declared sync (not async) so FastAPI runs it in a worker thread,
    keeping /health responsive during long-running inserts - otherwise
    Northflank health checks fail and the container is killed mid-ingest.
    """
    import tempfile
    import shutil

    try:
        # Save uploaded file to temp location
        suffix = ".csv"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = tmp.name

        # Determine CSV format and ingest
        conn = get_connection()
        try:
            # Cap DuckDB memory: container has 512MB but DuckDB sizes its
            # limit from host RAM, causing OOM kills on bulk inserts.
            # Spill to the /data volume instead of holding everything in RAM.
            try:
                conn.execute("SET memory_limit='150MB'")
                conn.execute("SET threads=1")
                conn.execute("SET preserve_insertion_order=false")
                spill_dir = os.path.join(tempfile.gettempdir(), "duckdb_spill")
                os.makedirs(spill_dir, exist_ok=True)
                conn.execute(f"SET temp_directory='{spill_dir}'")
            except Exception as e:
                logger.warning("Could not set DuckDB memory limits: %s", e)
            # Ensure prices table exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS prices (
                    arrival_date DATE, state VARCHAR, district VARCHAR, market VARCHAR,
                    commodity VARCHAR, variety VARCHAR, grade VARCHAR,
                    min_price DOUBLE, max_price DOUBLE, modal_price DOUBLE
                )
            """)

            # Read first line to detect format
            with open(tmp_path, "r", encoding="utf-8") as f:
                header = f.readline()

            if "Price Date" in header or "District Name" in header:
                # Agmarknet historical format (date: "05 Apr 2025")
                conn.execute(f"""
                    INSERT OR IGNORE INTO prices (arrival_date, state, district, market, commodity, variety, grade,
                                       min_price, max_price, modal_price)
                    SELECT
                        COALESCE(TRY_CAST("Price Date" AS DATE), TRY_STRPTIME("Price Date", '%d %b %Y')),
                        TRIM(State), TRIM("District Name"),
                        TRIM("Market Name"), TRIM(Commodity), TRIM(Variety), TRIM(Grade),
                        TRY_CAST(REPLACE(CAST("Min Price (Rs./Quintal)" AS VARCHAR), ',', '') AS DOUBLE),
                        TRY_CAST(REPLACE(CAST("Max Price (Rs./Quintal)" AS VARCHAR), ',', '') AS DOUBLE),
                        TRY_CAST(REPLACE(CAST("Modal Price (Rs./Quintal)" AS VARCHAR), ',', '') AS DOUBLE)
                    FROM read_csv_auto('{tmp_path}', header=true, ignore_errors=true)
                    WHERE "Price Date" IS NOT NULL AND TRIM("Price Date") != '' AND Commodity IS NOT NULL
                """)
                fmt = "agmarknet_historical"
            elif "date,admin1" in header or ("admin1" in header and file.filename and "wfp" in file.filename.lower()):
                # WFP food prices format
                conn.execute(f"""
                    INSERT OR IGNORE INTO prices (arrival_date, state, district, market, commodity, variety, grade,
                                       min_price, max_price, modal_price)
                    SELECT
                        TRY_CAST(date AS DATE), TRIM(admin1), TRIM(admin2), TRIM(market),
                        TRIM(commodity), TRIM(commodity), '', NULL, NULL,
                        TRY_CAST(price AS DOUBLE)
                    FROM read_csv_auto('{tmp_path}', header=true, ignore_errors=true)
                    WHERE date IS NOT NULL AND commodity IS NOT NULL AND price IS NOT NULL
                      AND admin1 IS NOT NULL AND TRIM(admin1) != ''
                """)
                fmt = "wfp_food_prices"
            elif "Arrival_Date" in header:
                # data.gov.in snapshot format
                conn.execute(f"""
                    INSERT OR IGNORE INTO prices (arrival_date, state, district, market, commodity, variety, grade,
                                       min_price, max_price, modal_price)
                    SELECT
                        TRY_CAST(Arrival_Date AS DATE), TRIM(State), TRIM(District), TRIM(Market),
                        TRIM(Commodity), TRIM(Variety), TRIM(Grade),
                        TRY_CAST(REPLACE(CAST("Min_x0020_Price" AS VARCHAR), ',', '') AS DOUBLE),
                        TRY_CAST(REPLACE(CAST("Max_x0020_Price" AS VARCHAR), ',', '') AS DOUBLE),
                        TRY_CAST(REPLACE(CAST("Modal_x0020_Price" AS VARCHAR), ',', '') AS DOUBLE)
                    FROM read_csv_auto('{tmp_path}', header=true, ignore_errors=true)
                    WHERE Arrival_Date IS NOT NULL AND Commodity IS NOT NULL
                """)
                fmt = "data_gov_in_snapshot"
            else:
                conn.close()
                return {"status": "error", "message": f"Unrecognized CSV format. Header: {header[:100]}"}

            n_prices = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
            n_commodities = conn.execute("SELECT COUNT(DISTINCT commodity) FROM prices").fetchone()[0]
            conn.close()

            return {
                "status": "ok",
                "format": fmt,
                "filename": file.filename,
                "total_prices": n_prices,
                "total_commodities": n_commodities,
            }
        finally:
            conn.close()
            os.unlink(tmp_path)

    except Exception as e:
        logger.error("Historical ingestion failed: %s", e)
        return {"status": "error", "message": str(e)}


@app.post("/admin/backup-to-r2", tags=["Admin"])
async def admin_backup_to_r2():
    """Upload the current DuckDB database to Cloudflare R2 as a gzipped backup.
    Reads the local DuckDB file, compresses it, and uploads to R2 as
    mandi_iq.duckdb.gz. Requires R2 credentials configured as environment variables.
    Returns:
        dict with status, message, bytes uploaded, and compression ratio.
    """
    try:
        from mandi_rdd.storage.duckdb_store import DB_PATH
        import gzip
        import urllib.request
        import hmac
        import hashlib
        import datetime

        if not DB_PATH.exists():
            return {"status": "error", "message": f"Database file not found: {DB_PATH}"}

        # Read and compress
        raw = DB_PATH.read_bytes()
        compressed = gzip.compress(raw, compresslevel=6)

        # R2 credentials
        bucket = os.environ.get("R2_BUCKET") or os.environ.get("R2_BUCKET_NAME") or ""
        account_id = os.environ.get("R2_ACCOUNT_ID") or ""
        access_key = os.environ.get("R2_ACCESS_KEY_ID") or ""
        secret_key = os.environ.get("R2_SECRET_ACCESS_KEY") or ""

        if not all([bucket, account_id, access_key, secret_key]):
            missing = [k for k, v in [
                ("R2_BUCKET/R2_BUCKET_NAME", bucket), ("R2_ACCOUNT_ID", account_id),
                ("R2_ACCESS_KEY_ID", access_key), ("R2_SECRET_ACCESS_KEY", secret_key),
            ] if not v]
            return {"status": "error", "message": "Missing R2 credentials: " + ", ".join(missing)}

        # Build S3 request
        endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
        key = "mandi_iq.duckdb.gz"
        url = f"{endpoint}/{bucket}/{key}"

        # AWS SigV4 signing
        now = datetime.datetime.utcnow()
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        region = "auto"
        service = "s3"
        algorithm = "AWS4-HMAC-SHA256"
        credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
        credential = f"{access_key}/{credential_scope}"

        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        content_sha256 = hashlib.sha256(compressed).hexdigest()

        canonical_request = (
            "PUT\n"
            f"/{bucket}/{key}\n"
            "\n"
            f"host:{account_id}.r2.cloudflarestorage.com\n"
            f"x-amz-content-sha256:{content_sha256}\n"
            f"x-amz-date:{amz_date}\n"
            "\n"
            f"{signed_headers}\n"
            f"{content_sha256}"
        )

        string_to_sign = (
            f"{algorithm}\n"
            f"{amz_date}\n"
            f"{credential_scope}\n"
            f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
        )

        def sign(key, msg):
            return hmac.new(key, msg.encode(), hashlib.sha256).digest()

        k_date = sign(("AWS4" + secret_key).encode(), date_stamp)
        k_region = sign(k_date, region)
        k_service = sign(k_region, service)
        k_signing = sign(k_service, "aws4_request")
        signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()

        auth_header = (
            f"{algorithm} Credential={credential}, SignedHeaders={signed_headers}, Signature={signature}"
        )

        headers = {
            "Host": f"{account_id}.r2.cloudflarestorage.com",
            "X-Amz-Content-Sha256": content_sha256,
            "X-Amz-Date": amz_date,
            "Authorization": auth_header,
            "Content-Type": "application/gzip",
        }

        req = urllib.request.Request(url, data=compressed, headers=headers, method="PUT")
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp.read()

        logger.info("R2 backup: uploaded %d bytes (compressed from %d) to s3://%s/%s",
                    len(compressed), len(raw), bucket, key)

        return {
            "status": "ok",
            "message": "Database backed up to R2.",
            "bytes_uploaded": len(compressed),
            "bytes_original": len(raw),
            "compression_pct": round(100 * (1 - len(compressed) / len(raw)), 1),
            "r2_key": key,
        }

    except urllib.error.HTTPError as e:
        return {"status": "error", "message": "R2 upload failed (HTTP " + str(e.code) + "): " + e.reason}
    except (TimeoutError, urllib.error.URLError, OSError) as e:
        return {"status": "error", "message": "R2 upload failed: " + str(e)}
    except Exception as e:
        return {"status": "error", "message": "Backup failed: " + str(e)}


@app.post("/admin/reset-metrics", tags=["Admin"])
async def admin_reset_metrics():
    """Reset LLM fallback counter and clear all model cool-down states.

    Useful for recovering from a stuck state after a free-tier rate limit
    penalty has expired. Does not affect any other system state.
    """
    reset_llm_fallback_count()
    clear_cool_down()
    return {
        "status": "ok",
        "llm_fallback_count": get_llm_fallback_count(),
        "message": "LLM metrics reset: counter zeroed, all models taken out of cool-down.",
    }



# -- Dashboard patcher with manual hit counter --
_dashboard_patch_count: int = 0

def _get_patched_dashboard(datasource_name: str, version: str = "") -> dict:
    global _dashboard_patch_count
    _dashboard_patch_count += 1
    import copy as _copy
    source = _dashboard_export if _dashboard_export is not None else dashboard_json
    result = _copy.deepcopy(source)
    for inp in result.get("__inputs", []):
        if inp.get("type") == "datasource":
            inp["name"] = datasource_name
            inp["label"] = datasource_name
    dash = result.get("dashboard", result)
    for item in dash.get("templating", {}).get("list", []):
        if item.get("type") == "datasource":
            item["current"] = {"value": datasource_name, "text": datasource_name}
            item["query"] = datasource_name
    return result


@app.get("/grafana-dashboard", tags=["System"])
async def grafana_dashboard(
    datasource: str = Query("DS_PROMETHEUS", description="Pre-bind the datasource name."),
    v: str = Query("", description="Cache-busting version string."),
):
    if dashboard_json is None:
        raise HTTPException(status_code=404, detail="Dashboard template not found")
    if datasource != "DS_PROMETHEUS" or v:
        return _get_patched_dashboard(datasource, v)
    return dashboard_json


@app.post("/admin/refresh-dashboard-cache", tags=["Admin"])
async def admin_refresh_dashboard_cache():
    global dashboard_json, _dashboard_export, _dashboard_last_refresh, _dashboard_file_mtime
    if os.path.exists(_dashboard_path):
        with open(_dashboard_path, "r") as f:
            _raw = json.load(f)
        dashboard_json = _raw.get("dashboard", _raw)
        _dashboard_export = _raw
        _dashboard_last_refresh = time.time()
        _dashboard_file_mtime = os.path.getmtime(_dashboard_path)
        # Warm the dashboard patch counter
        _get_patched_dashboard("Grafana")
        return {"status": "ok", "message": "Dashboard cache cleared and JSON reloaded from disk."}
    return {"status": "error", "message": f"Dashboard file not found at {_dashboard_path}"}


@app.get("/admin/dashboard-status", tags=["Admin"])
async def admin_dashboard_status():
    result = {"path": _dashboard_path, "file_exists": os.path.exists(_dashboard_path), "json_loaded": dashboard_json is not None}
    result["cache_size"] = _dashboard_patch_count if dashboard_json is not None else 0
    if os.path.exists(_dashboard_path):
        try:
            s = os.stat(_dashboard_path)
            from datetime import datetime, timezone
            result["file_mtime_utc"] = datetime.fromtimestamp(s.st_mtime, tz=timezone.utc).isoformat()
            result["file_size_bytes"] = s.st_size
            with open(_dashboard_path, "rb") as f:
                result["md5_hash"] = hashlib.md5(f.read()).hexdigest()
        except OSError as e:
            result["stat_error"] = str(e)
    if _dashboard_last_refresh > 0:
        from datetime import datetime, timezone
        result["last_refresh_utc"] = datetime.fromtimestamp(_dashboard_last_refresh, tz=timezone.utc).isoformat()
    if _dashboard_file_mtime > 0:
        from datetime import datetime, timezone
        result["last_refresh_file_mtime_utc"] = datetime.fromtimestamp(_dashboard_file_mtime, tz=timezone.utc).isoformat()
        result["stale"] = os.path.getmtime(_dashboard_path) > _dashboard_file_mtime
    return result


@app.post("/webhook/grafana-dashboard-update", tags=["Webhook"])
async def webhook_grafana_dashboard_update(
    payload: dict = {},
    x_webhook_secret: str = Header(None, alias="X-Webhook-Secret"),
):
    _secret = os.environ.get("WEBHOOK_SECRET", "")
    if _secret:
        if not x_webhook_secret or x_webhook_secret != _secret:
            logger.warning("Webhook auth failed: header=%s", "***present***" if x_webhook_secret else "***missing***")
            raise HTTPException(status_code=403, detail="Forbidden: invalid or missing X-Webhook-Secret header.")
    event_name = payload.get("event", "unknown")
    logger.info("Webhook received: event=%(event)s", {"event": event_name})
    result = await admin_refresh_dashboard_cache()
    if isinstance(result, dict):
        result["event"] = event_name
    return result



@app.get("/historical-import-status", tags=["Data"])
async def historical_import_status():
    """Get the current status of the background Ashoka CEDA historical import."""
    try:
        from mandi_rdd.ingestion.ashoka_background_import import get_status
        return get_status()
    except Exception as e:
        return {"state": "error", "error": str(e)}


@app.post("/trigger-ashoka-import", tags=["Data"])
async def trigger_ashoka_import(all_commodities: bool = True, workers: int = 2):
    """Start or resume the Ashoka CEDA historical import in the background.
    
    The import fetches multi-year monthly price history for ALL commodities
    (default) or the top 40. It runs as a daemon thread on the API server
    (no timeout), saves checkpoints every 10 cells for resume, and
    automatically backfills into DuckDB when complete.
    
    Track progress via GET /historical-import-status.
    """
    try:
        from mandi_rdd.ingestion.ashoka_background_import import trigger
        result = trigger(all_commodities=all_commodities, workers=workers)
        return result
    except Exception as e:
        return {"error": str(e)}


@app.post("/trigger-backfill", tags=["Data"])
async def trigger_backfill():
    """Run historical CSV backfill on any Ashoka CSV already on disk.
    
    Use this after a restart to consume a previously-fetched CSV into DuckDB
    without re-running the full Ashoka API fetch.
    """
    try:
        from mandi_rdd.ingestion.ashoka_background_import import trigger_backfill_only
        return trigger_backfill_only()
    except Exception as e:
        return {"error": str(e)}



# ── Prometheus /metrics endpoint ──
# Exposes lightweight service metrics in Prometheus text exposition format.
# No prometheus_client dependency required.

PROMETHEUS_METRICS_HEADER = {"Content-Type": "text/plain; version=0.0.4"}

@app.get("/metrics", tags=["System"], include_in_schema=False)
async def metrics():
    """Prometheus-compatible metrics endpoint (no prometheus_client library).

    Exposes service-level metrics in the Prometheus text exposition format
    so the service can be scraped by Prometheus, Grafana Agent, or any
    OpenMetrics-compatible collector.

    Adding new metrics:
        1. Define a gauge/counter line in the TEXT block below.
        2. Populate its value from the relevant module function.
        3. Ensure the metric name follows Prometheus naming conventions.
    """
    _uptime_sec = time.time() - health_stats.start_time
    _llm_fb = get_llm_fallback_count()

    lines = [
        "# HELP mandiiq_uptime_seconds Time since the API server started.",
        "# TYPE mandiiq_uptime_seconds gauge",
        f"mandiiq_uptime_seconds {_uptime_sec}",
        "",
        "# HELP mandiiq_llm_fallback_total Number of times call_llm() exhausted all models.",
        "# TYPE mandiiq_llm_fallback_total counter",
        f"mandiiq_llm_fallback_total {_llm_fb}",
        "",
        "# HELP mandiiq_health_checks_total Total health check requests.",
        "# TYPE mandiiq_health_checks_total counter",
        f"mandiiq_health_checks_total {health_stats.health_count}",
        "",
        "# HELP mandiiq_cold_starts_total Number of cold starts (server restarts) detected.",
        "# TYPE mandiiq_cold_starts_total counter",
        f"mandiiq_cold_starts_total {health_stats.cold_start}",
        "",
        "# HELP mandiiq_prices_count Current number of price records in the database.",
        "# TYPE mandiiq_prices_count gauge",
    ]

    # Try to read live prices count; emit -1 on failure (graceful degradation)
    try:
        conn = get_connection()
        n = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
        conn.close()
        lines.append(f"mandiiq_prices_count {n}")
    except Exception:
        lines.append("mandiiq_prices_count -1")

    # ---- Dashboard cache metrics ----
    lines.append("")
    lines.append("# HELP mandiiq_dashboard_cache_loaded Whether dashboard JSON is loaded (1=yes, 0=no).")
    lines.append("# TYPE mandiiq_dashboard_cache_loaded gauge")
    lines.append(f"mandiiq_dashboard_cache_loaded {1 if dashboard_json is not None else 0}")
    lines.append("# HELP mandiiq_dashboard_cache_last_refresh_timestamp_seconds Unix timestamp of last cache refresh.")
    lines.append("# TYPE mandiiq_dashboard_cache_last_refresh_timestamp_seconds gauge")
    lines.append(f"mandiiq_dashboard_cache_last_refresh_timestamp_seconds {_dashboard_last_refresh}")
    lines.append("# HELP mandiiq_dashboard_cache_file_mtime_timestamp_seconds Unix timestamp of dashboard file modification.")
    lines.append("# TYPE mandiiq_dashboard_cache_file_mtime_timestamp_seconds gauge")
    lines.append(f"mandiiq_dashboard_cache_file_mtime_timestamp_seconds {_dashboard_file_mtime}")
    lines.append("# HELP mandiiq_dashboard_cache_stale Whether file on disk is newer than loaded cache (1=stale, 0=fresh).")
    lines.append("# TYPE mandiiq_dashboard_cache_stale gauge")
    _stale = 0
    if dashboard_json is not None and _dashboard_file_mtime > 0 and os.path.exists(_dashboard_path):
        _stale = 1 if os.path.getmtime(_dashboard_path) > _dashboard_file_mtime else 0
    lines.append(f"mandiiq_dashboard_cache_stale {_stale}")
    lines.append("# HELP mandiiq_dashboard_cache_size Number of entries in the LRU dashboard cache.")
    lines.append("# TYPE mandiiq_dashboard_cache_size gauge")
    lines.append(f"mandiiq_dashboard_cache_size {_dashboard_patch_count if dashboard_json is not None else 0}")
    # ---- Disk usage metrics ----
    lines.append("")
    lines.append("# HELP mandiiq_disk_bytes Disk space usage for the mandiiq-api service filesystem.")
    lines.append("# TYPE mandiiq_disk_bytes gauge")
    try:
        _usage = shutil.disk_usage(".")
        lines.append(f'mandiiq_disk_bytes{{kind="total"}} {_usage.total}')
        lines.append(f'mandiiq_disk_bytes{{kind="used"}} {_usage.used}')
        lines.append(f'mandiiq_disk_bytes{{kind="free"}} {_usage.free}')
        _pct = round(_usage.used / _usage.total * 100, 2) if _usage.total > 0 else 0
        lines.append("# HELP mandiiq_disk_usage_percent Disk usage percentage for the mandiiq-api service.")
        lines.append("# TYPE mandiiq_disk_usage_percent gauge")
        lines.append(f"mandiiq_disk_usage_percent {_pct}")
    except Exception:
        lines.append('mandiiq_disk_bytes{kind="total"} -1')
        lines.append('mandiiq_disk_bytes{kind="used"} -1')
        lines.append('mandiiq_disk_bytes{kind="free"} -1')
        lines.append("# HELP mandiiq_disk_usage_percent Disk usage percentage for the mandiiq-api service.")
        lines.append("# TYPE mandiiq_disk_usage_percent gauge")
        lines.append("mandiiq_disk_usage_percent -1")

    # ---- R2 backup metrics ----
    lines.append("")
    lines.append("# HELP mandiiq_r2_backup_raw_bytes Size of the DuckDB before gzip compression.")
    lines.append("# TYPE mandiiq_r2_backup_raw_bytes gauge")
    lines.append("# HELP mandiiq_r2_backup_compressed_bytes Size of the gzip-compressed DuckDB backup in R2.")
    lines.append("# TYPE mandiiq_r2_backup_compressed_bytes gauge")
    lines.append("# HELP mandiiq_r2_backup_compression_pct Percentage size reduction from gzip compression.")
    lines.append("# TYPE mandiiq_r2_backup_compression_pct gauge")
    lines.append("# HELP mandiiq_r2_backup_timestamp_seconds Unix epoch of the last successful R2 backup.")
    lines.append("# TYPE mandiiq_r2_backup_timestamp_seconds gauge")
    try:
        _r2_path = Path(__file__).resolve().parent.parent / "data" / "r2_backup_metrics.json"
        if _r2_path.exists():
            with open(_r2_path) as _f:
                _r2_meta = json.load(_f)
            lines.append(f"mandiiq_r2_backup_raw_bytes {_r2_meta.get('raw_bytes', -1)}")
            lines.append(f"mandiiq_r2_backup_compressed_bytes {_r2_meta.get('compressed_bytes', -1)}")
            lines.append(f"mandiiq_r2_backup_compression_pct {_r2_meta.get('compression_pct', -1)}")
            _ts = _r2_meta.get('timestamp_epoch', -1)
            lines.append(f"mandiiq_r2_backup_timestamp_seconds {_ts}")
        else:
            lines.append("mandiiq_r2_backup_raw_bytes -1")
            lines.append("mandiiq_r2_backup_compressed_bytes -1")
            lines.append("mandiiq_r2_backup_compression_pct -1")
            lines.append("mandiiq_r2_backup_timestamp_seconds -1")
    except Exception:
        lines.append("mandiiq_r2_backup_raw_bytes -1")
        lines.append("mandiiq_r2_backup_compressed_bytes -1")
        lines.append("mandiiq_r2_backup_compression_pct -1")
        lines.append("mandiiq_r2_backup_timestamp_seconds -1")
    body = "\n".join(lines) + "\n"
    return Response(content=body, media_type=PROMETHEUS_METRICS_HEADER["Content-Type"])



@app.post("/deploy", tags=["System"])
async def deploy():
    """Trigger a Render deploy via the RENDER_DEPLOY_HOOK_URL.
    POSTs to the Render deploy hook URL set in the RENDER_DEPLOY_HOOK_URL
    environment variable. This triggers a new deploy of the mandiiq-api
    service on Render, picking up the latest DuckDB from git.
    The deploy hook URL is a one-time generated secret URL from the Render
    dashboard (Settings -> Deploy Hooks). If not set, returns a warning.
    Returns:
        dict with status, message, and optional HTTP status code from Render.
    """
    global _last_deploy_ts
    now = time.time()
    if now - _last_deploy_ts < _DEPLOY_COOLDOWN_S:
        remaining = round(_DEPLOY_COOLDOWN_S - (now - _last_deploy_ts), 1)
        return {
            "status": "cooldown",
            "message": f"Deploy skipped: {remaining}s remaining in cooldown ({_DEPLOY_COOLDOWN_S}s)",
        }
    hook_url = os.environ.get("RENDER_DEPLOY_HOOK_URL", "")
    if not hook_url:
        logger.warning("Deploy requested but RENDER_DEPLOY_HOOK_URL not set")
        return {
            "status": "skipped",
            "message": "RENDER_DEPLOY_HOOK_URL not set. Generate one at "
                       "dashboard.render.com and add it as an env var.",
        }
    try:
        req = urllib.request.Request(
            hook_url,
            data=b"{}",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            _last_deploy_ts = time.time()
            logger.info("Deploy triggered via /deploy endpoint (HTTP %s)", resp.status)
            return {
                "status": "ok",
                "message": "Render deploy triggered successfully.",
                "http_status": resp.status,
                "response": body[:500] if body else "",
            }
    except urllib.error.HTTPError as e:
        return {
            "status": "error",
            "message": f"Render returned HTTP {e.code}: {e.reason}",
            "http_status": e.code,
        }
    except (TimeoutError, urllib.error.URLError, OSError) as e:
        return {
            "status": "error",
            "message": f"Failed to reach Render deploy hook: {e}",
        }


@app.get('/proxy/github/{path:path}', tags=['Proxy'])
def proxy_github(path: str, request: Request):
    """Proxy requests to GitHub API to avoid CORS issues from browser."""
    query = request.url.query
    github_token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    headers = {
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'MandiIQ-API/1.0',
    }
    if github_token:
        headers['Authorization'] = f'Bearer {github_token}'
    url = f'https://api.github.com/{path}'
    if query:
        url += '?' + query
    try:
        req = urllib.request.Request(url, headers=headers, method='GET')
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode('utf-8')
            return JSONResponse(content=json.loads(body))
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode('utf-8'))
        except Exception:
            err_body = {'error': e.reason}
        return JSONResponse(status_code=e.code, content=err_body)
    except Exception as e:
        return JSONResponse(status_code=502, content={'error': str(e)})
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("mandi_rdd.api.main:app", host="0.0.0.0", port=port, reload=True)
