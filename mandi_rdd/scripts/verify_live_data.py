#!/usr/bin/env python3
"""
MandiIQ - End-to-End Live Data Verification.

Checks every production URL is reachable and that the API serves fresh
(non-stale) data. Run daily via GitHub Actions cron.

What counts as healthy:
  * every site answers with the status we expect
  * the API reports a pipeline run within ``stale_threshold_hours``
  * the newest arrival date in the warehouse is within
    ``stale_days_threshold`` days (mandi feeds publish with a 1-2 day lag)
  * the dashboard is publicly viewable (a 303 to the Streamlit auth
    service means the app is private, so nobody can actually see the data)

Usage:
    python -m mandi_rdd.scripts.verify_live_data --output verify_report.json

Exit codes:
    0 - all sites healthy, data fresh
    1 - one or more sites stale/unreachable/private
"""

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_API = os.environ.get(
    "MANDIIQ_API_URL", "https://p01--mandiiq--x4n8x4gkmzht.code.run"
)

SITES = [
    {
        "name": "API (Northflank)",
        "url": f"{DEFAULT_API}/health",
        "expected_status": 200,
        "is_api": True,
        # The pipeline refreshes hourly; a day of silence means it stalled.
        "stale_threshold_hours": 26,
        "stale_days_threshold": 4,
    },
    {
        "name": "Landing Page (mandiiq.unifies.codes)",
        "url": "https://mandiiq.unifies.codes",
        "expected_status": 200,
    },
    {
        "name": "Live Data Console (GitHub Pages)",
        "url": "https://flawsom.github.io/MandiIQ/live.html",
        "expected_status": 200,
    },
    {
        "name": "Streamlit Dashboard (mandiiq.streamlit.app)",
        "url": "https://mandiiq.streamlit.app",
        "expected_status": 200,
        "must_be_public": True,
    },
    {
        "name": "GitHub Repo (github.com/flawsom/MandiIQ)",
        "url": "https://github.com/flawsom/MandiIQ",
        "expected_status": [200, 301, 302],
    },
]

USER_AGENT = "MandiIQ-Live-Verifier/1.1"

AUTH_REDIRECT_MARKER = "share.streamlit.io/-/auth"


def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hours_since(timestamp):
    try:
        last_dt = datetime.datetime.strptime(
            str(timestamp)[:19], "%Y-%m-%dT%H:%M:%S"
        ).replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None
    return (
        datetime.datetime.now(datetime.timezone.utc) - last_dt
    ).total_seconds() / 3600


def _check_api_freshness(result, body, site):
    """Validate pipeline recency and warehouse recency from a /health body."""
    threshold = site.get("stale_threshold_hours")
    if threshold:
        hours_ago = _hours_since(body.get("last_run_utc"))
        if hours_ago is not None:
            result["hours_since_last_run"] = round(hours_ago, 1)
            if hours_ago > threshold:
                result["ok"] = False
                result["error"] = (
                    "Ingestion stalled: last pipeline run %.1fh ago (>%dh)"
                    % (hours_ago, threshold)
                )

    days_behind = body.get("days_behind")
    if days_behind is not None:
        result["days_behind"] = days_behind
        limit = site.get("stale_days_threshold")
        if limit is not None and days_behind > limit:
            result["ok"] = False
            result["error"] = (
                "Data stale: newest arrival date %s is %d days old (>%dd)"
                % (body.get("data_max_date"), days_behind, limit)
            )

    n_future = body.get("n_future_dates")
    if n_future:
        result["ok"] = False
        result["error"] = (
            "Date integrity: %d price rows carry an impossible (future) "
            "arrival date - run POST /admin/repair-dates?dry_run=false" % n_future
        )


def check_url(site, timeout=20):
    result = {
        "name": site["name"],
        "url": site["url"],
        "status": None,
        "ok": False,
        "response_time_s": None,
        "error": None,
        "api_data": None,
    }
    start = datetime.datetime.now(datetime.timezone.utc)
    req = urllib.request.Request(site["url"], headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result["status"] = resp.status
            result["response_time_s"] = round(
                (datetime.datetime.now(datetime.timezone.utc) - start).total_seconds(), 2
            )
            expected = site["expected_status"]
            result["ok"] = (
                resp.status in expected
                if isinstance(expected, list)
                else resp.status == expected
            )
            if site.get("is_api"):
                try:
                    body = json.loads(resp.read().decode("utf-8"))
                    result["api_data"] = body
                    _check_api_freshness(result, body, site)
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    result["error"] = "API response not valid JSON: %s" % e
            else:
                resp.read(1024)  # drain so the connection closes cleanly
    except urllib.error.HTTPError as e:
        result["status"] = e.code
        location = e.headers.get("Location", "") if e.headers else ""
        if site.get("must_be_public") and AUTH_REDIRECT_MARKER in location:
            result["error"] = (
                "Dashboard is private: it redirects to %s. Open the Streamlit "
                "Cloud dashboard and set 'Who can view this app' to the public "
                "option so visitors reach the app instead of a sign-in page."
                % location
            )
        else:
            result["error"] = "HTTP %d: %s" % (e.code, e.reason)
            expected = site["expected_status"]
            result["ok"] = (
                e.code in expected if isinstance(expected, list) else e.code == expected
            )
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        result["error"] = "Connection failed: %s" % e
    except Exception as e:  # pragma: no cover - defensive
        result["error"] = "Unexpected error: %s" % e

    if result["ok"] and result["error"]:
        result["error"] = None
    if result["response_time_s"] is None:
        result["response_time_s"] = round(
            (datetime.datetime.now(datetime.timezone.utc) - start).total_seconds(), 2
        )
    return result


def _site_detail(site):
    """One-line human summary of a site result."""
    api = site.get("api_data") or {}
    if site.get("error"):
        return site["error"]
    if api.get("n_prices") is not None:
        detail = "%s prices, %s commodities" % (api["n_prices"], api["n_commodities"])
        if api.get("data_max_date"):
            detail += ", data through %s" % api["data_max_date"]
        return detail
    return "-"


def render_markdown(report) -> str:
    """Step-summary table for the workflow run page."""
    lines = [
        "## Live data verification",
        "",
        "**Result:** %d/%d sites healthy"
        % (report["n_sites_healthy"], report["n_sites_checked"]),
        "",
        "| Site | Status | HTTP | Time | Detail |",
        "|------|--------|------|------|--------|",
    ]
    for site in report.get("sites", []):
        lines.append(
            "| %s | %s | %s | %.1fs | %s |"
            % (
                site["name"],
                "OK" if site.get("ok") else "FAIL",
                site.get("status", "?"),
                site.get("response_time_s", 0),
                _site_detail(site).replace("|", "\\|"),
            )
        )
    return "\n".join(lines) + "\n"


def render_issue_body(report) -> str:
    """Issue body for an unhealthy production."""
    lines = [
        "## Live Data Verification Report",
        "",
        "**Checked at:** `%s`" % report.get("checked_at"),
        "**Result:** %d/%d sites healthy"
        % (report["n_sites_healthy"], report["n_sites_checked"]),
        "",
        "| Site | Status | HTTP | Time | Details |",
        "|------|--------|------|------|---------|",
    ]
    for site in report.get("sites", []):
        lines.append(
            "| %s | %s | %s | %.1fs | %s |"
            % (
                site["name"],
                "OK" if site.get("ok") else "FAIL",
                site.get("status", "?"),
                site.get("response_time_s", 0),
                _site_detail(site).replace("|", "\\|"),
            )
        )
    failed = [s for s in report.get("sites", []) if not s.get("ok")]
    if failed:
        lines += ["", "### Failed sites", ""]
        lines += ["- **%s**: %s" % (s["name"], s.get("error", "unknown")) for s in failed]
        lines += [
            "",
            "### Suggested actions",
            "1. Re-run the **Refresh Live Data** workflow to force a fresh ingest",
            "2. Check [GitHub Actions](https://github.com/flawsom/MandiIQ/actions) for pipeline failures",
            "3. Check the hosting dashboard if the API is unreachable",
            "4. If the dashboard is private, set `Who can view this app` to public in Streamlit Cloud",
        ]
    lines += ["", "---", "_Auto-generated by verify-live-data.yml_"]
    return "\n".join(lines) + "\n"


def render_issue_title(report) -> str:
    return "[Verify] %d/%d sites healthy - %s" % (
        report["n_sites_healthy"],
        report["n_sites_checked"],
        report.get("checked_at", "?"),
    )


def main():
    parser = argparse.ArgumentParser(
        description="End-to-end live data verification for MandiIQ."
    )
    parser.add_argument(
        "--output", default="verify_report.json", help="Path to write the JSON report"
    )
    parser.add_argument(
        "--markdown", help="Write a markdown summary table to this path"
    )
    parser.add_argument(
        "--issue-body", help="Write a GitHub issue body to this path"
    )
    parser.add_argument(
        "--issue-title", help="Write a GitHub issue title to this path"
    )
    parser.add_argument(
        "--timeout", type=int, default=20, help="HTTP request timeout (default: 20)"
    )
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
            status = str(result["status"])
            api = result.get("api_data") or {}
            if api.get("n_prices") is not None:
                status = "%s (%s prices/%s commodities/%s states)" % (
                    status, api["n_prices"], api["n_commodities"], api["n_states"],
                )
                if result.get("hours_since_last_run") is not None:
                    status += ", last_run=%.1fh ago" % result["hours_since_last_run"]
                if api.get("data_max_date"):
                    status += ", data through %s" % api["data_max_date"]
            print("OK [%s] in %.1fs" % (status, result["response_time_s"]))
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

    report = {
        "checked_at": run_ts,
        "n_sites_checked": n_total,
        "n_sites_healthy": n_ok,
        "all_healthy": all_ok,
        "sites": results,
    }
    with open(args.output, "w") as f:
        json.dump(report, f, indent=2, default=str)

    for path, renderer in (
        (args.markdown, render_markdown),
        (args.issue_body, render_issue_body),
        (args.issue_title, render_issue_title),
    ):
        if path:
            with open(path, "w") as f:
                f.write(renderer(report))

    print("\nReport written to %s" % args.output)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
