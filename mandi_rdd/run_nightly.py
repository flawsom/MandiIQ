#!/usr/bin/env python3
"""
MandiRDD — Nightly pipeline runner.

Run this once daily (e.g., via cron, GitHub Actions schedule, or Render Cron Job).

Usage:
    python -m mandi_rdd.run_nightly          # Full pipeline
    python -m mandi_rdd.run_nightly --prices-only  # Prices only, skip rainfall
    python -m mandi_rdd.run_nightly --commodity Onion --commodity Tomato  # Specific commodities
"""

import sys
import time
import json
import argparse
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mandi_rdd.ingestion.scheduler import run_ingestion


def _write_status(outcome, status=None, new_price_rows=None, duration_s=None, error=None):
    """Phase 10: single source of truth for the app health dot and the job's
    real last outcome, so 'live' can never silently drift from reality."""
    record = {
        "last_run_utc": datetime.datetime.utcnow().isoformat() + "Z",
        "outcome": outcome,
        "status": status,
        "new_price_rows": new_price_rows,
        "duration_s": duration_s,
        "error": error,
    }
    try:
        out = Path(__file__).resolve().parent / "data" / "last_ingest_status.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w") as f:
            json.dump(record, f, indent=2)
    except Exception:
        pass  # never let status-writing break the pipeline run


def main():
    parser = argparse.ArgumentParser(
        description="MandiRDD — Nightly pipeline runner"
    )
    parser.add_argument(
        "--prices-only",
        action="store_true",
        help="Skip rainfall ingestion (faster, uses cached rainfall data)",
    )
    parser.add_argument(
        "--commodity",
        action="append",
        default=None,
        help="Specific commodity to analyze (can be specified multiple times)",
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="Maximum price records to pull (for testing)",
    )
    parser.add_argument(
        "--state",
        default=None,
        help="Filter by state",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Phase 11: run ingestion but do NOT write the status file to disk",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("🌾 MandiRDD — Nightly Pipeline")
    print("=" * 60)

    filters = {}
    if args.state:
        filters["state.keyword"] = args.state

    if args.commodity:
        filters["commodity"] = args.commodity[0]  # Server-side filter supports one

    start = time.time()

    try:
        summary = run_ingestion(
            filters=filters,
            max_records=args.max_records,
            skip_rainfall=args.prices_only,
        )
    except Exception as exc:  # Phase 2: a real failure must never look like success
        if not args.dry_run:
            _write_status(outcome="failure", error=str(exc))
        print(f"FATAL: ingestion raised: {exc}")
        sys.exit(1)

    duration = time.time() - start

    status = summary.get("status", "unknown")
    steps = summary.get("steps", {})
    prices_step = steps.get("prices")
    n_new = prices_step.get("new", 0) if isinstance(prices_step, dict) else 0
    outcome = "success" if status == "ok" else "failure"
    if not args.dry_run:
        _write_status(
            outcome=outcome,
            status=status,
            new_price_rows=n_new,
            duration_s=round(duration, 1),
        )

    print(f"\n{'=' * 60}")
    print(f"Pipeline Status: {status}")
    print(f"Duration: {duration:.1f}s")
    print(f"New price rows this run: {n_new}")

    for step, info in steps.items():
        print(f"  {step}: {json.dumps(info)}")

    print(f"{'=' * 60}")

    if status != "ok":
        sys.exit(1)


if __name__ == "__main__":
    main()
