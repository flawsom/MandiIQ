#!/usr/bin/env python3
"""
MandiIQ — End-to-End Live Data Verification.

Checks all 4 production URLs are reachable and validates the API serves
fresh (non-stale) data. Run daily via GitHub Actions cron.

Usage:
    python -m mandi_rdd.scripts.verify_live_data --output verify_report.json

Exit codes:
    0 - all sites healthy, data fresh
    1 - one or more sites stale/unreachable
"""

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request

SITES = [
    {"name": "API (mandiiq-api.onrender.com)", "url": "https://mandiiq-api.onrender.com/health", "expected_status": 200, "is_api": True, "stale_threshold_hours": 48},
    {"name": "Landing Page (mandiiq.unifies.codes)", "url": "https://mandiiq.unifies.codes", "expected_status": 200, "is_api": False},
    {"name": "Streamlit Dashboard (mandiiq.streamlit.app)", "url": "https://mandiiq.streamlit.app", "expected_status": [200, 302, 303], "is_api": False},
    {"name": "GitHub Repo (github.com/flawsom/MandiIQ)", "url": "https://github.com/flawsom/MandiIQ", "expected_status": [200, 301, 302], "is_api": False},
]

USER_AGENT = "MandiIQ-Live-Verifier/1.0"


def iso_now():
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_url(site, timeout=20):
    result = {"name": site["name"], "url": site["url"], "status": None, "ok": False, "response_time_s": None, "error": None, "api_data": None}
    start = datetime.datetime.now(datetime.UTC)
    req = urllib.request.Request(site["url"], headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result["status"] = resp.status
            result["response_time_s"] = round((datetime.datetime.now(datetime.UTC) - start).total_seconds(), 2)
            expected = site["expected_status"]
            result["ok"] = resp.status in expected if isinstance(expected, list) else resp.status == expected
            if site.get("is_api"):
                try:
                    body = json.loads(resp.read().decode("utf-8"))
                    result["api_data"] = body
                    threshold = site.get("stale_threshold_hours")
                    if threshold and body.get("last_run_utc"):
                        last_run = body["last_run_utc"]
                        try:
                            last_dt = datetime.datetime.strptime(str(last_run)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.UTC)
                            hours_ago = (datetime.datetime.now(datetime.UTC) - last_dt).total_seconds() / 3600
                            result["hours_since_last_run"] = round(hours_ago, 1)
                            if hours_ago > threshold:
                                result["ok"] = False
                                result["error"] = "Data stale: last run %.0fh ago (>%dh threshold)" % (hours_ago, threshold)
                        except ValueError:
                            pass
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    result["error"] = "API response not valid JSON: %s" % e
    except urllib.error.HTTPError as e:
        result["status"] = e.code
        result["error"] = "HTTP %d: %s" % (e.code, e.reason)
        expected = site["expected_status"]
        result["ok"] = e.code in expected if isinstance(expected, list) else e.code == expected
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        result["error"] = "Connection failed: %s" % e
    except Exception as e:
        result["error"] = "Unexpected error: %s" % e
    if result["response_time_s"] is None:
        result["response_time_s"] = round((datetime.datetime.now(datetime.UTC) - start).total_seconds(), 2)
    return result


def main():
    parser = argparse.ArgumentParser(description="End-to-end live data verification for MandiIQ.")
    parser.add_argument("--output", default="verify_report.json", help="Path to write the JSON report")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP request timeout (default: 20)")
    args = parser.parse_args()

    run_ts = iso_now()
    print("[%s] MandiIQ Live Data Verification" % run_ts)
    print("  Checking %d sites..." % len(SITES))
    print()

    results = []
    all_ok = True

    for site in SITES:
        print("  Checking %s..." % site["name"], end=" ", flush=True)
        result = check_url(site, timeout=args.timeout)
        results.append(result)

        if result["ok"]:
            sd = str(result["status"])
            ad = result.get("api_data") or {}
            if ad.get("n_prices") is not None:
                sd = "%s (%sprices/%scommodities/%sstates)" % (result["status"], ad["n_prices"], ad["n_commodities"], ad["n_states"])
                if result.get("hours_since_last_run") is not None:
                    sd += ", last_run=%.0fh ago" % result["hours_since_last_run"]
            print("OK [%s] in %.1fs" % (sd, result["response_time_s"]))
        else:
            all_ok = False
            print("FAIL [%s] - %s" % (result["status"], result.get("error", "unknown")))

    print()
    n_ok = sum(1 for r in results if r["ok"])
    n_total = len(results)
    print("Summary: %d/%d sites healthy" % (n_ok, n_total))

    if not all_ok:
        print("\nFailed sites:")
        for r in results:
            if not r["ok"]:
                print("  - %s: %s" % (r["name"], r.get("error", "unknown")))

    report = {"checked_at": run_ts, "n_sites_checked": n_total, "n_sites_healthy": n_ok, "all_healthy": all_ok, "sites": results}
    with open(args.output, "w") as f:
        json.dump(report, f, indent=2, default=str)

    print("\nReport written to %s" % args.output)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
