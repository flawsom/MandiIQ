"""
MandiRDD — FastAPI serving layer.

Endpoints:
- GET /health — liveness check
- GET /prices — query stored prices with filters
- GET /rdd-result/{commodity} — latest RDD estimate
- GET /rdd-plot/{commodity} — binned scatter plot data
- GET /robustness/{commodity} — robustness check bundle
- GET /forecast/{commodity} — Prophet forecast
- GET /risk-score/{commodity} — XGBoost risk score
- GET /recommendation/{commodity} — procurement recommendation
- POST /ask — AI orchestrator (OpenRouter multi-model routing)
- POST /refresh — manual pipeline re-run
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import os
import json
import logging
import time

import hashlib
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
import duckdb
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
    commodities_analyzed: list[str]


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


state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load state on startup."""
    logger.info("Starting MandiRDD API...")
    conn = get_connection()
    init_schema(conn)
    
    # Check data freshness and auto-trigger pipeline if needed
    try:
        df = conn.execute("SELECT DISTINCT commodity FROM prices ORDER BY commodity").fetchdf()
        state.commodities = df["commodity"].tolist() if len(df) > 0 else []
        n_prices = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
        n_rainfall = conn.execute("SELECT COUNT(*) FROM rainfall").fetchone()[0]
        n_rdd = conn.execute("SELECT COUNT(*) FROM rdd_results").fetchone()[0]
        
        logger.info(f"Startup data check: {n_prices} prices, {n_rainfall} rainfall, {n_rdd} RDD results")
        
        # Auto-trigger pipeline if data is missing or stale
        if n_prices < 100 or n_rainfall < 10 or n_rdd < 1:
            logger.warning("Data is stale or missing - triggering auto-pipeline in background...")
            
            def _auto_pipeline():
                import time as _t
                _start = _t.time()
                try:
                    from mandi_rdd.ingestion.scheduler import run_ingestion
                    logger.info("Auto-pipeline starting...")
                    summary = run_ingestion()
                    duration = round(_t.time() - _start, 1)
                    logger.info(f"Auto-pipeline finished in {duration}s: {summary.get('status')}")
                except Exception as e:
                    logger.error(f"Auto-pipeline failed: {e}")
            
            threading.Thread(target=_auto_pipeline, daemon=True).start()
    except Exception:
        state.commodities = []
    
    conn.close()
    metrics_push.start_push_thread()
    # Warm the in-memory dashboard cache so heartbeat shows Fresh on boot
    global _dashboard_last_refresh, _dashboard_file_mtime
    if dashboard_json is not None:
        _dashboard_last_refresh = time.time()
        _dashboard_file_mtime = os.path.getmtime(_dashboard_path)
        _get_patched_dashboard("Grafana")
        logger.info("Dashboard cache warmed: %d entries", _dashboard_patch_count)
    
    # Start hourly auto-refresh scheduler
    def _hourly_refresh():
        """Run pipeline every hour to keep data fresh."""
        import time as _t
        while True:
            _t.sleep(3600)  # 1 hour
            try:
                from mandi_rdd.ingestion.scheduler import run_ingestion
                logger.info("Hourly auto-refresh starting...")
                summary = run_ingestion()
                logger.info(f"Hourly auto-refresh finished: {summary.get('status')}")
            except Exception as e:
                logger.error(f"Hourly auto-refresh failed: {e}")
    
    threading.Thread(target=_hourly_refresh, daemon=True).start()
    logger.info("Hourly auto-refresh scheduler started")

    yield


app = FastAPI(
    title="MandiRDD API",
    description="""
    Automated Mandi Price Discontinuity Engine.
    
    Pulls daily mandi prices from data.gov.in, joins with rainfall
    departure data, and runs a Regression Discontinuity Design (RDD)
    to detect price jumps around the -19% rainfall deficiency threshold.
    
    **Endpoints:**
    * `/health` — Liveness check + data counts
    * `/prices` — Query stored prices by state/district/commodity
    * `/rdd-result/{commodity}` — Latest RDD estimate for a commodity
    * `/rdd-plot/{commodity}` — Binned scatter data for the discontinuity plot
    * `/robustness/{commodity}` — Full robustness check bundle
    * `/forecast/{commodity}` — Prophet forecast with optional LSTM comparison
    * `/risk-score/{commodity}` — XGBoost price-spike risk probability
    * `/recommendation/{commodity}` — Procurement recommendation
    * `/ask` — AI orchestrator (OpenRouter multi-model routing, circuit-breaker fallback)
    * `/refresh` — Manual re-run of the full pipeline
    """,
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Endpoints ──

@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health():
    health_stats.health_count += 1
    """Liveness check with full data counts for the documentation page."""
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
    try:
        status_path = (
            Path(__file__).resolve().parent.parent / "data" / "last_ingest_status.json"
        )
        if status_path.exists():
            with open(status_path) as f:
                record = json.load(f)
            last_run_utc = record.get("last_run_utc")
            last_outcome = record.get("outcome")
    except Exception:
        pass

    conn.close()

    return HealthResponse(
        status="healthy",
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
        n_tests=71,
        last_run_utc=last_run_utc,
        last_outcome=last_outcome,
        commodities_analyzed=state.commodities[:20],
    )


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
    try:
        compressed = _r2_download()
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    except urllib.error.HTTPError as e:
        return {
            "status": "error",
            "message": f"R2 download failed (HTTP {e.code}): {e.reason}",
        }
    except (TimeoutError, urllib.error.URLError, OSError) as e:
        return {"status": "error", "message": f"R2 download failed: {e}"}
    # Decompress
    try:
        decompressed = gzip.decompress(compressed)
    except Exception as e:
        return {"status": "error", "message": f"gzip decompression failed: {e}"}
    # Replace the local DuckDB file
    try:
        from mandi_rdd.storage.duckdb_store import DB_PATH
        # Write to a temp file first, then rename (atomic on same filesystem)
        tmp = DB_PATH.with_suffix(".duckdb.tmp")
        tmp.write_bytes(decompressed)
        tmp.replace(DB_PATH)
        logger.info(
            "R2 restore: replaced %s with %d bytes from R2 backup",
            DB_PATH, len(decompressed),
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
        "bytes_downloaded": len(compressed),
        "bytes_decompressed": len(decompressed),
        "db_path": str(DB_PATH),
    }


@app.post("/admin/ingest-historical", tags=["Admin"])
async def admin_ingest_historical(file: UploadFile = File(...)):
    """Upload and ingest a historical CSV file into the prices table.
    Accepts Agmarknet, data.gov.in snapshot, or WFP/FAO food price CSVs.
    Uses DuckDB's native CSV reader for fast bulk import.
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
            import os
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
