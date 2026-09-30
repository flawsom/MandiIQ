#!/usr/bin/env python3
"""Fail when the production API serves stale or impossible data.

Used by .github/workflows/refresh-live-data.yml right after an ingest run, so
"the data is live" is verified rather than assumed.

Usage:
    python -m mandi_rdd.scripts.check_production_freshness --api-base URL
      [--max-days-behind 4] [--json]

Exit codes:
    0 - fresh, no impossible dates, last run succeeded
    1 - stale, unhealthy, or unreachable
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_API = os.environ.get(
    "MANDIIQ_API_URL", "https://p01--mandiiq--x4n8x4gkmzht.code.run"
)
USER_AGENT = "MandiIQ-Freshness-Checker/1.0"


def _get(url: str, timeout: float = 30.0):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def collect(api_base: str) -> dict:
    api_base = api_base.rstrip("/")
    report: dict = {"api_base": api_base, "health": None, "data_quality": None,
                    "problems": [], "reachable": True}
    try:
        report["health"] = _get(f"{api_base}/health")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        report["reachable"] = False
        report["problems"].append(f"API unreachable: {exc}")
        return report
    except Exception as exc:  # pragma: no cover - defensive
        report["reachable"] = False
        report["problems"].append(f"API error: {exc}")
        return report

    try:
        report["data_quality"] = _get(f"{api_base}/data-quality")
    except Exception as exc:
        report["problems"].append(
            f"/data-quality unavailable ({exc}); deploy the current build"
        )
    return report


def evaluate(report: dict, max_days_behind: int = 4) -> list:
    health = report.get("health") or {}
    problems = list(report.get("problems") or [])

    days_behind = health.get("days_behind")
    if days_behind is None:
        problems.append(
            "API does not report data_max_date/days_behind - an old build is deployed"
        )
    elif days_behind < 0:
        # Negative means the newest "arrival date" is in the future: the
        # month/day mis-parse is present and the warehouse cannot be trusted.
        problems.append(
            "newest arrival date %s is %d days in the future - impossible dates "
            "are in the warehouse"
            % (health.get("data_max_date"), -days_behind)
        )
    elif days_behind > max_days_behind:
        problems.append(
            "newest arrival date %s is %d days old (>%dd)"
            % (health.get("data_max_date"), days_behind, max_days_behind)
        )

    status = health.get("status")
    if status in ("empty", "degraded"):
        problems.append(
            "/health reports status=%r (n_prices=%s)"
            % (status, health.get("n_prices"))
        )

    if health.get("n_future_dates"):
        problems.append(
            "%d price rows carry an impossible (future) arrival date" % health["n_future_dates"]
        )
    outcome = health.get("last_outcome")
    if outcome == "failure":
        problems.append("last ingest run reported failure")
    elif outcome == "degraded":
        # Ran, but a data source was unavailable: report it without pretending
        # the warehouse is as fresh as a clean run.
        problems.append("last ingest run was degraded (a source was unavailable)")

    quality = report.get("data_quality") or {}
    if quality.get("n_future_dates"):
        problems.append(
            "%d price rows carry an impossible (future) arrival date"
            % quality["n_future_dates"]
        )
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Verify production data freshness.")
    parser.add_argument("--api-base", default=DEFAULT_API)
    parser.add_argument("--max-days-behind", type=int, default=4)
    parser.add_argument("--json", action="store_true", help="Print the raw report")
    args = parser.parse_args(argv)

    report = collect(args.api_base)
    problems = evaluate(report, args.max_days_behind)
    report["problems"] = problems

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        health = report.get("health") or {}
        print("API              :", report["api_base"])
        print("version          :", health.get("version"))
        print("prices           :", health.get("n_prices"))
        print("newest data date :", health.get("data_max_date"))
        print("days behind      :", health.get("days_behind"))
        print("future dates     :", health.get("n_future_dates"))
        print("last run         :", health.get("last_run_utc"), "/", health.get("last_outcome"))
        for problem in problems:
            print(f"::error::{problem}")
        if not problems:
            print("Freshness check passed.")

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
