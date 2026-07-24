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
import threading
import time
from typing import Optional
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
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
    try:
        df = conn.execute("SELECT DISTINCT commodity FROM prices ORDER BY commodity").fetchdf()
        state.commodities = df["commodity"].tolist() if len(df) > 0 else []
    except Exception:
        state.commodities = []
    conn.close()

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
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    
    from mandi_rdd.analysis.forecast import get_forecast_summary
    result = get_forecast_summary(conn, commodity=commodity)
    conn.close()
    
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    
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
    """Manually trigger a full pipeline re-run.
    
    After the pipeline finishes, also generates the nightly narrative
    via the AI orchestrator (if OPENROUTER_API_KEY is set).
    
    Args:
        commodity: Optional commodity filter to limit the pipeline run.
    """
    import time
    start = time.time()
    
    try:
        from mandi_rdd.ingestion.scheduler import run_ingestion
        filters = {}
        if commodity:
            filters["commodity"] = commodity
        summary = run_ingestion(filters=filters if commodity else None)
        
        # Generate nightly narrative if AI is configured (Gemini free or OpenRouter)
        from mandi_rdd.ai.router import get_api_key as _get_llm_key
        _llm_key = _get_llm_key()
        narrative_status = "skipped"
        if _llm_key:
            try:
                from mandi_rdd.ai.orchestrator import generate_nightly_narrative
                target = commodity or "Onion"
                narrative = generate_nightly_narrative(commodity=target)
                narrative_status = "generated" if not narrative.get("error") else "failed"
                logger.info(f"Nightly narrative for {target}: {narrative_status}")
            except Exception as e:
                narrative_status = f"error: {e}"
                logger.warning(f"Nightly narrative generation failed: {e}")
        
        summary["nightly_narrative"] = narrative_status
        duration = round(time.time() - start, 1)
        
        return RefreshResponse(
            status="ok" if summary.get("status") == "ok" else "partial",
            message=f"Pipeline complete: {summary.get('steps', {})}. Narrative: {narrative_status}",
            duration_seconds=duration,
        )
    except Exception as e:
        logger.error(f"Refresh failed: {e}")
        return RefreshResponse(
            status="error",
            message=f"Pipeline failed: {e}",
        )


# ── Admin reset endpoint ──

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

    body = "\n".join(lines) + "\n"
    return Response(content=body, media_type=PROMETHEUS_METRICS_HEADER["Content-Type"])


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("mandi_rdd.api.main:app", host="0.0.0.0", port=port, reload=True)
