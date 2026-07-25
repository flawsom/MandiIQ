#!/usr/bin/env python3
"""
MandiRDD — Phase 1 Static Proof (go/no-go gate).

Pulls a sample of data from data.gov.in for one commodity, runs the RDD,
and prints the result. This is the validation gate BEFORE building any
automation (Phase 2-4).

If there's no statistically distinguishable jump at the -19% cutoff,
stop and reconsider before building the scheduler.

Usage:
    python -m mandi_rdd.analysis.static_proof
    python -m mandi_rdd.analysis.static_proof --commodity Tomato --state Maharashtra
    python -m mandi_rdd.analysis.static_proof --max-records 5000
"""

import sys
import time
import argparse
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("static_proof")

from mandi_rdd.ingestion.fetch_prices import fetch_all_prices, fetch_page
from mandi_rdd.ingestion.fetch_rainfall import (
    fetch_and_store_all_rainfall,
    load_district_subdivision_map,
)
from mandi_rdd.storage.duckdb_store import (
    get_connection,
    init_schema,
    upsert_prices,
    upsert_rainfall,
    get_monthly_avg_prices,
)
from mandi_rdd.analysis.rdd_engine import (
    local_linear_rdd,
    bandwidth_sensitivity,
    placebo_test,
    mccrary_density_test,
    rdd_plot_data,
)
from mandi_rdd.analysis.robustness import full_robustness_report


def run_static_proof(
    commodity: str = "Onion",
    state: str = None,
    max_price_records: int = 10000,
    cutoff: float = -19.0,
):
    """
    Run a static RDD proof for one commodity.
    
    This is the Phase 1 go/no-go gate. Returns a summary dict.
    """
    print("=" * 60)
    print(f"🌾 MandiRDD — Phase 1 Static Proof")
    print(f"   Commodity: {commodity}")
    print(f"   State:     {state or 'All India'}")
    print(f"   Cutoff:    {cutoff}% (IMD deficient rainfall)")
    print("=" * 60)

    start = time.time()

    # 1. Pull sample price data
    print(f"\n📥 Pulling price data for {commodity}...")
    filters = {"commodity": commodity}
    if state:
        filters["state.keyword"] = state

    records = fetch_all_prices(
        filters=filters,
        max_records=max_price_records,
        progress_callback=lambda done, total: print(
            f"   Prices: {done}/{total or '?'}", end="\r"
        ),
    )
    print(f"\n   ✓ {len(records):,} price records fetched")

    # 2. Store in temporary database
    conn = get_connection()
    init_schema(conn)
    n_new = upsert_prices(conn, records)
    print(f"   ✓ {n_new:,} new records stored")

    # 3. Fetch rainfall data
    print(f"\n📊 Fetching rainfall data...")
    rainfall_records = fetch_and_store_all_rainfall()
    if rainfall_records:
        n_rain = upsert_rainfall(conn, rainfall_records)
        print(f"   ✓ {n_rain:,} rainfall records stored")
    else:
        print("   ❌ No rainfall data available!")
        conn.close()
        return {"status": "fail", "reason": "No rainfall data"}

    # 4. Load district mapping
    print(f"\n🗺️  Loading district→sub-division mapping...")
    district_map = load_district_subdivision_map()
    print(f"   ✓ {len(district_map):,} mappings loaded")

    # 5. Get monthly average prices and join with rainfall
    print(f"\n🔗 Joining prices with rainfall data...")
    price_df = get_monthly_avg_prices(conn, commodity=commodity, state=state)
    print(f"   ✓ {len(price_df):,} monthly price aggregates")

    price_df["sub_division"] = price_df.apply(
        lambda r: district_map.get((r["state"], r["district"]), None),
        axis=1,
    )
    price_df = price_df.dropna(subset=["sub_division"])
    print(f"   ✓ {len(price_df):,} rows after district mapping")

    rainfall_df = conn.execute("SELECT * FROM rainfall").fetchdf()
    merged = price_df.merge(
        rainfall_df,
        on=["sub_division", "year", "month"],
        how="inner",
    )
    merged = merged.dropna(subset=["departure_pct", "avg_modal_price"])
    print(f"   ✓ {len(merged):,} matched observations")
    conn.close()

    if len(merged) < 20:
        print(f"\n❌ Only {len(merged)} matched observations — insufficient for RDD")
        return {"status": "fail", "reason": f"Insufficient data: {len(merged)} obs"}

    x = merged["departure_pct"].values
    y = merged["avg_modal_price"].values

    # 6. Run main RDD
    print(f"\n🔬 Running RDD at cutoff={cutoff}%...")
    main_result = local_linear_rdd(x, y, cutoff, bandwidth=20)

    effect = main_result.get("effect")
    p_value = main_result.get("p_value")
    se = main_result.get("std_error")

    if effect is None:
        print(f"   ❌ RDD failed: {main_result.get('error', 'unknown')}")
        return {"status": "fail", "reason": main_result.get("error")}

    print(f"\n{'='*60}")
    print(f"📊 RDD RESULT")
    print(f"{'='*60}")
    print(f"   Discontinuity Effect:  ₹{effect:.2f}")
    print(f"   Standard Error:        ₹{se:.2f}")
    print(f"   P-Value:               {p_value:.4f}")
    print(f"   Observations:          {len(merged):,}")
    print(f"   Districts:             {merged['district'].nunique():,}")
    print(f"   Left of cutoff:        {main_result['n_left']}")
    print(f"   Right of cutoff:       {main_result['n_right']}")

    if p_value is not None:
        if p_value < 0.05:
            print(f"\n   ✅ STATISTICALLY SIGNIFICANT (p={p_value:.4f})")
            print(f"   ⬆  GO decision — proceed to Phase 2 (automation)")
        elif p_value < 0.1:
            print(f"\n   ⚠️  MARGINALLY SIGNIFICANT (p={p_value:.4f})")
            print(f"   → Proceed with caution. Consider expanding to more commodities.")
        else:
            print(f"\n   ❌ NOT SIGNIFICANT (p={p_value:.4f})")
            print(f"   → NO-GO decision. The -19% cutoff doesn't explain {commodity} prices.")
            print(f"     Try different commodity, state, or cutoff value.")

    # 7. Bandwidth sensitivity
    print(f"\n{'='*60}")
    print(f"🔬 BANDWIDTH SENSITIVITY")
    print(f"{'='*60}")
    bw_results = bandwidth_sensitivity(x, y, cutoff, bandwidths=[10, 15, 20, 25, 30])
    for r in bw_results:
        eff = r.get("effect")
        p = r.get("p_value")
        sig = "✅" if (p is not None and p < 0.05) else "❌"
        if eff is not None:
            print(f"   BW={r['bandwidth']:2d}%  Effect=₹{eff:8.2f}  SE={r.get('std_error','?'):>8}  P={p:.4f if p else '?':>8}  {sig}")
        else:
            print(f"   BW={r['bandwidth']:2d}%  {r.get('error', 'N/A')}")

    # 8. Placebo tests
    print(f"\n{'='*60}")
    print(f"🎭 PLACEBO TESTS")
    print(f"{'='*60}")
    placebos = placebo_test(x, y, cutoff)
    for p in placebos[:4]:
        pc = p.get("placebo_cutoff", "?")
        pe = p.get("effect")
        pp = p.get("p_value")
        sig = "⚠️" if (pp is not None and pp < 0.05) else "✅"
        if pe is not None:
            print(f"   Placebo at {pc:6.1f}%  Effect=₹{pe:8.2f}  P={pp:.4f if pp else '?':>8}  {sig}")
        else:
            print(f"   Placebo at {pc:6.1f}%  {p.get('error', 'N/A')}")

    # 9. Density test
    print(f"\n{'='*60}")
    print(f"📊 MCCRARY DENSITY TEST")
    print(f"{'='*60}")
    density = mccrary_density_test(x, y, cutoff)
    d_p = density.get("density_p_value")
    d_jump = density.get("density_jump")
    if d_p is not None:
        passed = d_p > 0.05
        print(f"   Density discontinuity: {d_jump:.4f} (p={d_p:.4f})")
        print(f"   {'✅ No evidence of manipulation' if passed else '⚠️ Possible manipulation detected'}")

    # 10. Summary
    elapsed = round(time.time() - start, 1)
    print(f"\n{'='*60}")
    print(f"📋 SUMMARY")
    print(f"{'='*60}")
    print(f"   Status:     {'GO ✅' if (p_value is not None and p_value < 0.1) else 'NO-GO ❌'}")
    print(f"   Duration:   {elapsed}s")
    print(f"   Data:       {len(merged):,} matched observations")
    print(f"   Next step:  {'Proceed to Phase 2 (ingestion automation)' if (p_value is not None and p_value < 0.1) else 'Reconsider approach'}")
    print(f"{'='*60}")

    return {
        "status": "go" if (p_value is not None and p_value < 0.1) else "no-go",
        "commodity": commodity,
        "effect": effect,
        "p_value": p_value,
        "n_observations": len(merged),
        "duration_s": elapsed,
        "main_result": main_result,
        "bandwidth_sensitivity": bw_results,
        "placebo_tests": placebos,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MandiRDD Phase 1 static proof")
    parser.add_argument("--commodity", default="Onion", help="Commodity to analyze")
    parser.add_argument("--state", default=None, help="State filter")
    parser.add_argument("--max-records", type=int, default=10000, help="Max price records to pull")
    parser.add_argument("--cutoff", type=float, default=-19.0, help="RDD cutoff value")

    args = parser.parse_args()
    result = run_static_proof(
        commodity=args.commodity,
        state=args.state,
        max_price_records=args.max_records,
        cutoff=args.cutoff,
    )

    sys.exit(0 if result.get("status") == "go" else 1)
