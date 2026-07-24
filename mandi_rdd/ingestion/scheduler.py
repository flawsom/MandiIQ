"""
MandiRDD — daily ingestion scheduler.

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

import os
import sys
import time
import logging
from pathlib import Path

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

from mandi_rdd.storage.duckdb_store import (
    get_connection,
    init_schema,
    upsert_prices,
    upsert_rainfall,
    save_rdd_result,
)
from mandi_rdd.ingestion.fetch_prices import fetch_all_prices, fetch_page
from mandi_rdd.ingestion.ingest_historical_csv import run_auto as run_historical_backfill
from mandi_rdd.ingestion.fetch_ndvi import fetch_and_store_all_ndvi
from mandi_rdd.ingestion.fetch_rainfall import (
    fetch_and_store_all_rainfall,
    load_district_subdivision_map,
)
from mandi_rdd.analysis.rdd_engine import run_rdd
from mandi_rdd.analysis.forecast import train_forecast

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mandi_rdd.scheduler")


def run_ingestion(
    filters: dict = None,
    max_records: int = None,
    skip_rainfall: bool = False,
) -> dict:
    """
    Run the full nightly pipeline.
    
    Returns summary dict with counts and timing.
    """
    start = time.time()
    # 0. Consume any historical CSVs dropped into data/historical/ so the
    #    dashboard can build a real time-series (the live API is daily-only).
    try:
        n_hist = run_historical_backfill(folder="data/historical")
        if n_hist:
            logger.info(f"Historical backfill ingested {n_hist} rows.")
    except Exception as e:
        logger.warning(f"Historical backfill skipped: {e}")

    summary = {"status": "ok", "steps": {}}

    # 1. Initialize storage
    conn = get_connection()
    init_schema(conn)
    logger.info("Storage initialized")

    # 2. Ingest mandi prices
    logger.info("Fetching mandi prices from data.gov.in...")
    price_records = fetch_all_prices(
        filters=filters,
        max_records=max_records,
        progress_callback=lambda done, total: logger.info(
            f"  Prices: {done}/{total} records"
        ),
    )
    n_prices = len(price_records)
    n_new = upsert_prices(conn, price_records)

    # 2b. Supplementary variety-wise recent-price feed (resource 35985678).
    # Bounded + best-effort: never blocks the main pipeline if it fails.
    try:
        from mandi_rdd.ingestion.fetch_prices import fetch_varietywise_recent
        variety_records = fetch_varietywise_recent(days=60, max_records=20000)
        if variety_records:
            n_var_new = upsert_prices(conn, variety_records)
            summary["steps"]["prices_varietywise"] = {
                "fetched": len(variety_records), "new": n_var_new
            }
            logger.info(f"Variety-wise prices: {len(variety_records)} fetched, {n_var_new} new")
        else:
            summary["steps"]["prices_varietywise"] = {"fetched": 0, "new": 0}
    except Exception as e:
        logger.warning(f"Variety-wise supplement skipped: {e}")
        summary["steps"]["prices_varietywise"] = {"status": "error", "error": str(e)}

    summary["steps"]["prices"] = {"fetched": n_prices, "new": n_new}
    logger.info(f"Prices: {n_prices} fetched, {n_new} new")

    # 3. Load district-subdivision mapping (always, regardless of rainfall)
    district_map = load_district_subdivision_map()
    conn.executemany(
        "INSERT OR IGNORE INTO district_map (state, district, sub_division) VALUES (?, ?, ?)",
        [(s, d, sub) for (s, d), sub in district_map.items()],
    )
    conn.commit()
    logger.info(f"District-subdivision mappings: {len(district_map)}")

    # 4. Ingest rainfall
    if not skip_rainfall:
        logger.info("Fetching rainfall data...")
        rainfall_records = fetch_and_store_all_rainfall()
        if rainfall_records:
            n_rain = len(rainfall_records)
            n_rain_new = upsert_rainfall(conn, rainfall_records)
            summary["steps"]["rainfall"] = {"fetched": n_rain, "new": n_rain_new}
            logger.info(f"Rainfall: {n_rain} records, {n_rain_new} new")
        else:
            summary["steps"]["rainfall"] = {"status": "skipped", "reason": "no data source"}

    # 4b. Ingest satellite NDVI (if Sentinel Hub credentials are set)
    import os as _os
    if _os.environ.get("SENTINEL_CLIENT_ID") and _os.environ.get("SENTINEL_CLIENT_SECRET"):
        logger.info("Fetching satellite NDVI data...")
        try:
            n_ndvi = fetch_and_store_all_ndvi()
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

    # Focus on rain-sensitive commodities for the MVP
    rain_sensitive = ["Onion", "Tomato", "Potato", "Cabbage", "Cauliflower"]
    target_commodities = [c for c in rain_sensitive if c in all_commodities]

    rdd_results = []
    fe_results = []
    classifier_results = []
    
    for commodity in target_commodities:
        # RDD + Fixed-effects
        logger.info(f"Running RDD + FE for {commodity}...")
        try:
            result = run_rdd(conn, commodity=commodity)
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
        except Exception as e:
            logger.error(f"  RDD failed for {commodity}: {e}")
        
        # Forecast (persist MAPE for the dashboard KPI)
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
                # "Insufficient feature rows" is expected early on (not enough history) -> info-level
                if "Insufficient feature rows" in err_msg or "Insufficient data points" in err_msg:
                    logger.info(f"  Classifier skipped for {commodity}: {err_msg}")
                else:
                    logger.warning(f"  Classifier skipped: {err_msg}")
        except Exception as e:
            logger.warning(f"  Classifier skipped: {e}")

        # Forecast (persist MAPE so the dashboard shows a live accuracy metric)
        logger.info(f"Training forecast + persisting MAPE for {commodity}...")
        try:
            from mandi_rdd.storage.duckdb_store import (
                save_forecast_metrics,
                get_avg_price_and_districts,
            )
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
    #    Provider-agnostic: works with GEMINI_API_KEY (free) or OPENROUTER_API_KEY
    from mandi_rdd.ai.router import get_api_key as _get_llm_key
    _llm_key = _get_llm_key()
    narrative_results = []
    if _llm_key:
        logger.info("Generating nightly narratives via AI orchestrator...")
        try:
            from mandi_rdd.ai.orchestrator import generate_nightly_narrative
        except ImportError:
            logger.warning("AI orchestrator not available — install openai and pyyaml")
            generate_nightly_narrative = None
        
        for commodity in target_commodities:
            if not generate_nightly_narrative:
                break
            try:
                narrative = generate_nightly_narrative(commodity=commodity)
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
        logger.info("No LLM provider key set (GEMINI_API_KEY or OPENROUTER_API_KEY) — skipping nightly narratives")

    summary["steps"]["narratives"] = {"generated": len(narrative_results), "commodities": narrative_results}
    summary["duration_seconds"] = round(time.time() - start, 1)
    summary["commodities_analyzed"] = target_commodities

    conn.close()
    logger.info(f"Pipeline complete in {summary['duration_seconds']}s")
    return summary


def run_once():
    """One-shot ingestion + RDD run. Suitable for cron."""
    summary = run_ingestion()

    if summary.get("status") == "ok":
        print(f"\n{'='*50}")
        print(f"[OK] Pipeline complete ({summary['duration_seconds']}s)")
        for step, info in summary["steps"].items():
            print(f"  {step}: {info}")
        print(f"{'='*50}")
    else:
        print(f"\n[FAIL] Pipeline failed: {summary.get('error', 'unknown')}")
        sys.exit(1)


if __name__ == "__main__":
    run_once()
