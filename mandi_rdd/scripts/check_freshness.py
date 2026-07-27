#!/usr/bin/env python3
"""
MandiIQ — Data Freshness Checker.

CLI script that queries the production API for per-commodity freshness,
flags any of the top 10 commodities that haven't received new data in
48 hours, fetches their lineage trace, and outputs a JSON report with
a pre-formatted GitHub issue body.

Intended to be run as a GitHub Actions cron job. Exits with code 0
if all commodities are fresh, code 1 if any commodity is stale (so
the workflow can detect and file an issue).

Usage:
    python -m mandi_rdd.scripts.check_freshness \\
        --api-url https://p01--mandiiq--x4n8x4gkmzht.code.run \
        --output freshness_report.json
"""

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

# ── Top 10 commodities tracked by MandiIQ ──
# Ordered by importance (matches the analysis pipeline focus).
TOP_COMMODITIES = [
    "Onion",
    "Tomato",
    "Potato",
    "Cabbage",
    "Cauliflower",
    "Wheat",
    "Rice",
    "Brinjal",
    "Banana",
    "Mango",
]

# Stale threshold: 48 hours without a new record
STALE_HOURS = 48


def http_get_json(url: str, timeout: int = 15) -> Any:
    """Simple HTTP GET returning parsed JSON. No external deps."""
    req = urllib.request.Request(url, headers={"User-Agent": "MandiIQ-Freshness-Checker/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def iso_now() -> str:
    """Current UTC time as ISO 8601 string."""
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def build_issue_body(
    stale: list[dict],
    api_url: str,
    lineage_map: dict[str, list[dict]],
    run_ts: str,
) -> str:
    """Build a Markdown GitHub issue body for stale commodities."""
    lines = [
        f"## ⏰ Data Freshness Alert — {len(stale)} commodity/ies stale",
        "",
        f"**Checked at:** `{run_ts}`",
        f"**API:** `{api_url}`",
        f"**Threshold:** No new data in >{STALE_HOURS} hours",
        "",
        "---",
        "",
    ]

    for entry in stale:
        commodity = entry.get("commodity", "Unknown")
        latest = entry.get("latest_date", "—")
        earliest = entry.get("earliest_date", "—")
        row_count = entry.get("row_count", 0)
        n_districts = entry.get("n_districts", 0)
        n_states = entry.get("n_states", 0)
        source_type = entry.get("source_type", "unknown")
        source_name = entry.get("source_name", "")

        # Compute how many hours stale (rough estimate from latest_date)
        stale_hours = "?"
        if latest and latest != "—":
            try:
                latest_dt = datetime.datetime.strptime(str(latest)[:10], "%Y-%m-%d")
                stale_hours = round((datetime.datetime.utcnow() - latest_dt).total_seconds() / 3600)
            except ValueError:
                pass

        lines.append(f"### 🔴 {commodity}")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Last record | `{latest}` ({stale_hours}h ago) |")
        lines.append(f"| Earliest record | `{earliest}` |")
        lines.append(f"| Total rows | {row_count:,} |")
        lines.append(f"| Districts covered | {n_districts} |")
        lines.append(f"| States covered | {n_states} |")
        lines.append(f"| Last source | `{source_type}` — {source_name} |")
        lines.append("")

        # Append lineage trace if available
        lineage_records = lineage_map.get(commodity, [])
        if lineage_records:
            lines.append("#### 🛠 Recent Pipeline Activity (last 5 ingestion batches)")
            lines.append("")
            lines.append("Shows the most recent batches the ingestion engine processed. "
                         "Note: these are global batches, not specifically filtered to this commodity. "
                         "The `commodity_list` field shows which commodities the batch covered.")
            lines.append("")
            lines.append("| # | Source | Commodities | Rows | New | Ingestion Time |")
            lines.append("|---|--------|-------------|------|-----|----------------|")
            for i, lr in enumerate(lineage_records[:5], 1):
                l_source = lr.get("source_type", "?")
                l_commas = (lr.get("commodity_list") or "—")[:40]
                if len(l_commas) >= 40:
                    l_commas += "…"
                l_rows = lr.get("row_count", 0)
                l_new = lr.get("n_new", 0)
                l_ts = lr.get("ingested_at", "?")
                if hasattr(l_ts, "strftime"):
                    l_ts = l_ts.strftime("%Y-%m-%d %H:%M")
                else:
                    l_ts = str(l_ts)[:19]
                lines.append(f"| {i} | `{l_source}` | {l_commas} | {l_rows} | {l_new} | {l_ts} |")
            lines.append("")
        else:
            lines.append("> No recent pipeline activity records. Ingestion may not have run yet.\n")
            lines.append("")

        # Suggested actions
        lines.append("**Suggested actions:**")
        lines.append("1. Check the [Render Dashboard](https://dashboard.render.com) for API/server health")
        lines.append("2. [Trigger a manual refresh]"
                     f"({api_url.rstrip('/')}/docs#/System/refresh_refresh_post) via Swagger UI")
        lines.append("3. Verify DATA_GOV_IN_API_KEY is still valid in Render env vars")
        lines.append("4. Check [GitHub Actions]"
                     "(https://github.com/flawsom/MandiIQ/actions) for pipeline failures")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Footer with action link
    lines.append("---")
    lines.append("")
    lines.append("_This issue was automatically generated by the "
                 "`check-freshness.yml` workflow._")
    lines.append("")
    lines.append("<!-- freshness-check-trigger -->")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check commodity freshness and report stale data."
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("MANDIIQ_API_URL", "https://p01--mandiiq--x4n8x4gkmzht.code.run"),
        help="Base URL of the MandiIQ API (default: https://p01--mandiiq--x4n8x4gkmzht.code.run)",
    )
    parser.add_argument(
        "--output",
        default="freshness_report.json",
        help="Path to write the JSON report (default: freshness_report.json)",
    )
    parser.add_argument(
        "--threshold-hours",
        type=int,
        default=STALE_HOURS,
        help=f"Hours without data before flagging stale (default: {STALE_HOURS})",
    )
    args = parser.parse_args()

    api_url = args.api_url.rstrip("/")
    threshold_hours = args.threshold_hours
    run_ts = iso_now()
    issue_body_path = os.path.splitext(args.output)[0] + ".md"
    

    print(f"[{run_ts}] MandiIQ Freshness Checker")
    print(f"  API: {api_url}")
    print(f"  Threshold: {threshold_hours}h")
    print(f"  Watching: {', '.join(TOP_COMMODITIES)}")
    print()

    # ── Step 1: Fetch all freshness data ──
    print("Fetching freshness data...")
    try:
        freshness = http_get_json(f"{api_url}/freshness", timeout=20)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as e:
        print(f"  ERROR: Could not reach {api_url}/freshness: {e}")
        print("  Skipping freshness check — API may be down.")
        report = {
            "status": "error",
            "checked_at": run_ts,
            "api_url": api_url,
            "error": str(e),
            "stale_commodities": [],
            "all_fresh": False,
            "total_checked": 0,
            "total_stale": 0,
        }
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"  Report written to {args.output}")
        return 1

    if isinstance(freshness, dict) and "error" in freshness:
        print(f"  API returned error: {freshness['error']}")
        report = {
            "status": "error",
            "checked_at": run_ts,
            "api_url": api_url,
            "error": freshness["error"],
            "stale_commodities": [],
            "all_fresh": False,
            "total_checked": 0,
            "total_stale": 0,
        }
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2, default=str)
        return 1

    if not isinstance(freshness, list):
        print(f"  Unexpected response type: {type(freshness).__name__}")
        return 1

    # Build a lookup by commodity name (case-insensitive)
    freshness_by_commodity: dict[str, dict] = {}
    for entry in freshness:
        if not isinstance(entry, dict):
            continue
        name = (entry.get("commodity") or "").lower().strip()
        if name:
            freshness_by_commodity[name] = entry

    print(f"  Found {len(freshness_by_commodity)} commodities with freshness data")
    print()

    # ── Step 2: Check each top commodity ──
    stale_commodities: list[dict] = []
    fresh_commodities: list[str] = []
    missing_commodities: list[str] = []

    now = datetime.datetime.utcnow()

    for commodity in TOP_COMMODITIES:
        key = commodity.lower()
        entry = freshness_by_commodity.get(key)

        if entry is None:
            missing_commodities.append(commodity)
            print(f"  ⚠  {commodity}: NOT FOUND in freshness data")
            continue

        latest_str = entry.get("latest_date") or ""
        if not latest_str:
            stale_commodities.append(entry)
            print(f"  🔴 {commodity}: no latest_date — flagged stale")
            continue

        try:
            latest_dt = datetime.datetime.strptime(str(latest_str)[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            stale_commodities.append(entry)
            print(f"  🔴 {commodity}: unparseable latest_date='{latest_str}' — flagged stale")
            continue

        hours_ago = (now - latest_dt).total_seconds() / 3600
        if hours_ago > threshold_hours:
            stale_commodities.append(entry)
            print(f"  🔴 {commodity}: last data {hours_ago:.0f}h ago (>{threshold_hours}h) — STALE")
        else:
            fresh_commodities.append(commodity)
            print(f"  🟢 {commodity}: last data {hours_ago:.0f}h ago — fresh")        # Also check 5 more from the prices table (non-top commodities)
    extra_checked = 0
    for entry in freshness:
        if not isinstance(entry, dict):
            continue
        name = (entry.get("commodity") or "").title()
        if name.lower() in [c.lower() for c in TOP_COMMODITIES]:
            continue  # already checked
        latest_str = entry.get("latest_date") or ""
        if not latest_str:
            continue
        try:
            latest_dt = datetime.datetime.strptime(str(latest_str)[:10], "%Y-%m-%d")
        except ValueError:
            continue
        hours_ago = (now - latest_dt).total_seconds() / 3600
        if hours_ago > threshold_hours:
            stale_commodities.append(dict(entry))  # copy to avoid shared refs
            print(f"  🟠 {name}: last data {hours_ago:.0f}h ago (extra, stale)")
            extra_checked += 1
            if extra_checked >= 5:
                break

    print()
    print(f"Summary: {len(fresh_commodities)} fresh, "
          f"{len(stale_commodities)} stale, "
          f"{len(missing_commodities)} missing")

    # ── Step 3: Fetch lineage trace for stale commodities ──
    lineage_map: dict[str, list[dict]] = {}
    if stale_commodities:
        print("\nFetching lineage traces for stale commodities...")
        for entry in stale_commodities:
            commodity = entry.get("commodity", "")
            if not commodity:
                continue
            try:
                lineage = http_get_json(
                    f"{api_url}/lineage?limit=10",
                    timeout=15,
                )
                if isinstance(lineage, list):
                    lineage_map[commodity] = lineage
                    print(f"  {commodity}: {len(lineage)} lineage entries")
                else:
                    print(f"  {commodity}: lineage unavailable")
            except Exception as e:
                print(f"  {commodity}: lineage fetch failed: {e}")
                lineage_map[commodity] = []

    # ── Step 4: Build the issue body ──
    issue_title = (
        f"⏰ Data Freshness Alert: {len(stale_commodities)} commodity/ies "
        f"stale ({run_ts[:10]})"
    )
    issue_body = build_issue_body(stale_commodities, api_url, lineage_map, run_ts)

    # ── Step 5: Write the JSON report ──
    report = {
        "status": "stale_found" if stale_commodities else "all_fresh",
        "checked_at": run_ts,
        "api_url": api_url,
        "threshold_hours": threshold_hours,
        "total_checked": len(TOP_COMMODITIES),
        "total_fresh": len(fresh_commodities),
        "total_stale": len(stale_commodities),
        "total_missing": len(missing_commodities),
        "fresh_commodities": fresh_commodities,
        "stale_commodities": stale_commodities,
        "missing_commodities": missing_commodities,
        "lineage_traces": lineage_map,
        "issue_title": issue_title,
        "issue_body": issue_body,
        "should_file_issue": len(stale_commodities) > 0,
    }

    with open(args.output, "w") as f:
        json.dump(report, f, indent=2, default=str)

    # Write the issue body as a separate file (safer than shell-echoing multi-line Markdown)
    with open(issue_body_path, "w", encoding="utf-8") as f:
        f.write(issue_body)

    # Also print flat status for GitHub Actions GITHUB_OUTPUT consumption
    print(f"RESULT_SHOULD_FILE_ISSUE={'true' if stale_commodities else 'false'}")
    print(f"RESULT_ISSUE_TITLE={issue_title}")
    print(f"RESULT_ISSUE_BODY_PATH={issue_body_path}")

    print(f"\nReport written to {args.output}")
    print(f"Issue body written to {issue_body_path}")
    if stale_commodities:
        print(f"Issue title: {issue_title}")
        print(f"Issue body length: {len(issue_body)} chars")

    # Exit code: 0 = all fresh, 1 = stale found
    return 1 if stale_commodities else 0


if __name__ == "__main__":
    sys.exit(main())
