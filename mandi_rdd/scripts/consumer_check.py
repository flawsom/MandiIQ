"""End-to-end check of everything a consumer of MandiIQ can actually reach.

This walks the product the way a visitor does rather than the way the
repository is organised:

  1. every public page (landing, GitHub Pages hero, the Live Data Console,
     the Streamlit cockpit) is fetched and must answer with something a
     browser can render,
  2. every API route a consumer surface depends on is called and must answer
     with the shape that surface expects,
  3. every link found on those pages is followed, so a CTA that points at a
     dead page fails here instead of in front of a visitor,
  4. the visible copy is scanned for placeholders and for claims that the
     measurement contradicts (an "always-on" badge over an unreachable API).

Staleness is split into two different things, because they need different
responses:

  * **upstream publication lag** - data.gov.in simply has not published newer
    arrivals, so every commodity stops on the same date. This is a warning:
    nothing in the pipeline can close the gap.
  * **pipeline ingest failure** - our own runs are failing, or commodities
    stop on *different* dates, so published rows are missing. This is a
    blocker, and it remains one even while upstream is also quiet.

Exit status is 0 only when no *blocker* was found. Warnings - a private
Streamlit app, an endpoint that only exists after the next deploy - are
reported and do not fail the run, because they are states a consumer can still
use the product in.

Usage:
    python -m mandi_rdd.scripts.consumer_check
    python -m mandi_rdd.scripts.consumer_check --json
    python -m mandi_rdd.scripts.consumer_check --api-base http://localhost:8000
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

DEFAULT_API = "https://p01--mandiiq--x4n8x4gkmzht.code.run"
DEFAULT_MIRROR = "https://p01--mandiiq--zbvjrztgjqgw.code.run"
LANDING = "https://mandiiq.unifies.codes"
PAGES_HOME = "https://flawsom.github.io/MandiIQ/"
LIVE_CONSOLE = "https://flawsom.github.io/MandiIQ/live.html"
STREAMLIT = "https://mandiiq.streamlit.app"
REPO = "https://github.com/flawsom/MandiIQ"

USER_AGENT = "MandiIQ-consumer-check/1.0 (+https://github.com/flawsom/MandiIQ)"

# The API surface a consumer surface actually calls. Each entry is
# (path, required_keys_in_the_response). A missing key is a blocker: it means
# a page would render a blank where a number belongs.
API_SURFACE = [
    ("/health", ("status", "n_prices", "n_commodities", "data_max_date", "days_behind")),
    ("/freshness", None),
    ("/prices?limit=2", None),
    ("/data-quality", ("max_date", "days_behind", "n_future_dates", "n_rows")),
    ("/fdr", ("n_hypotheses", "n_significant_fdr", "entries")),
    ("/spec-curve/Onion", ("summary", "specifications")),
    ("/rdd-result/Onion", None),
    ("/rdd-plot/Onion", ("raw_x", "raw_y")),
    ("/forecast/Onion", None),
    ("/robustness/Onion", None),
    ("/risk-score/Onion", None),
    ("/analytics/Onion", ("version", "sections")),
    ("/conformal/Onion", None),
    ("/drift/Onion", None),
    ("/tail-risk/Onion", None),
    ("/dml/Onion", None),
    ("/nowcast/Onion", None),
    ("/metrics", None),
    ("/openapi.json", ("paths", "info")),
    ("/docs", None),
]

# Optional-until-deployed routes: a 404 is a warning, not a blocker.
OPTIONAL_ROUTES = ("/fdr", "/spec-curve/Onion", "/data-quality", "/analytics/Onion")

PLACEHOLDER_PATTERNS = (
    "lorem ipsum", "coming soon", "todo:", "tbd", "placeholder", "xxx",
    "your api key here", "undefined",
)

# Copy that promises liveness. If the API behind the page is unreachable or the
# warehouse is stale, the promise is false and the page is a blocker.
LIVENESS_PROMISES = ("real-time", "live data", "always-on", "updated every")


class _LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "a" and attributes.get("href"):
            self.links.append(attributes["href"])
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def _get(url: str, timeout: float = 30.0, method: str = "GET"):
    """Return (status, body_text, elapsed_seconds). Never raises."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method=method)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, body, time.monotonic() - started
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return exc.code, body, time.monotonic() - started
    except Exception as exc:  # DNS, TLS, timeout, connection reset
        return None, str(exc), time.monotonic() - started


def _own_copy(body: str) -> str:
    """The text a visitor reads, with inline scripts and styles removed.

    Placeholder scanning is worthless if it trips on a bundler's JavaScript, so
    the scan runs over visible copy only.
    """
    without_scripts = re.sub(r"<(script|style)\b.*?</\1>", " ", body, flags=re.S | re.I)
    return re.sub(r"<[^>]+>", " ", without_scripts)


def check_pages() -> list[dict]:
    """Fetch every consumer-facing page and describe what came back.

    ``authored`` marks the pages whose copy this project controls. Placeholder
    scanning is limited to those: GitHub's own repository HTML will always
    contain words like "undefined" somewhere in its scripts, and a warning that
    cannot be acted on trains the reader to ignore the real ones.
    """
    pages = [
        ("landing page", LANDING, True),
        ("GitHub Pages hero", PAGES_HOME, True),
        ("Live Data Console", LIVE_CONSOLE, True),
        ("Streamlit cockpit", STREAMLIT, True),
        ("repository", REPO, False),
    ]
    results = []
    for name, url, authored in pages:
        status, body, elapsed = _get(url)
        entry = {
            "name": name,
            "url": url,
            "status": status,
            "seconds": round(elapsed, 2),
            "bytes": len(body) if isinstance(body, str) else 0,
            "title": "",
            "problems": [],
            "warnings": [],
        }
        if status is None:
            entry["problems"].append(f"unreachable: {body[:160]}")
        elif status == 303 and "streamlit.io/-/auth" in body:
            entry["warnings"].append(
                "Streamlit app is private - flip Settings > 'Who can view this "
                "app' to public in the Streamlit Cloud dashboard"
            )
        elif status >= 400:
            entry["problems"].append(f"HTTP {status}")
        else:
            parser = _LinkExtractor()
            try:
                parser.feed(body)
            except Exception:
                pass
            entry["title"] = parser.title.strip()[:120]
            entry["links"] = parser.links
            if not parser.title:
                entry["warnings"].append("page has no <title>")
            if authored:
                lowered = _own_copy(body).lower()
                for pattern in PLACEHOLDER_PATTERNS:
                    if pattern in lowered:
                        entry["warnings"].append(f"copy contains {pattern!r}")
        results.append(entry)
    return results


def check_links(pages: list[dict], limit: int = 40) -> list[dict]:
    """Follow the links on the public pages; report the ones that are broken."""
    seen: dict[str, dict] = {}
    checked = 0
    for page in pages:
        for href in page.get("links") or []:
            if checked >= limit:
                break
            absolute = urllib.parse.urljoin(page["url"], href)
            if not absolute.startswith("http"):
                continue
            if absolute in seen:
                continue
            checked += 1
            status, _body, _elapsed = _get(absolute, timeout=20, method="GET")
            seen[absolute] = {
                "from": page["name"],
                "url": absolute,
                "status": status,
                "ok": status is not None and status < 400,
            }
    return list(seen.values())


def check_api(base: str) -> dict:
    """Call the routes the consumer surfaces depend on."""
    routes = []
    for path, required in API_SURFACE:
        status, body, elapsed = _get(base + path, timeout=60)
        entry = {
            "path": path,
            "status": status,
            "seconds": round(elapsed, 2),
            "problems": [],
            "payload": None,
        }
        if status is None:
            entry["problems"].append(f"unreachable: {body[:120]}")
        elif status == 404 and path in OPTIONAL_ROUTES:
            entry["problems"].append("not deployed yet (404) - a consumer surface "
                                     "falls back, but the build is behind")
        elif status >= 400:
            entry["problems"].append(f"HTTP {status}")
        else:
            try:
                payload = json.loads(body)
            except Exception:
                payload = None
            entry["payload"] = payload
            if required:
                if not isinstance(payload, dict):
                    entry["problems"].append("not a JSON object")
                else:
                    missing = [key for key in required if key not in payload]
                    if missing:
                        entry["problems"].append(f"missing keys: {missing}")
        routes.append(entry)
    return {"base": base, "routes": routes}


def upstream_lag_signature(freshness, data_max_date) -> dict | None:
    """Recognise the shape of *upstream* lag: one arrival date for everything.

    When data.gov.in has not published a new day yet, every commodity's newest
    row is the same date, and that date equals /health's ``data_max_date``.
    That is a source-publication fact, not a defect in this pipeline. When the
    dates differ, some commodities are ahead of others - rows exist upstream
    that we failed to ingest - and the caller treats it as a pipeline problem.
    """
    if not isinstance(freshness, list) or not freshness:
        return None
    dates = [row.get("latest_date") for row in freshness
             if isinstance(row, dict) and row.get("latest_date")]
    if not dates:
        return None
    unique = set(dates)
    if len(unique) != 1:
        return None
    (only,) = unique
    if data_max_date and only != data_max_date:
        return None
    return {"n_commodities": len(dates), "latest_date": only}


def pipeline_failure_signals(health: dict) -> list[str]:
    """Evidence that *our* ingest is failing, independent of upstream."""
    signals = []
    if health.get("last_outcome") == "failure":
        signals.append("the last ingest run failed")
    error = health.get("last_refresh_error")
    if error:
        signals.append("last refresh error: " + str(error).replace("\n", " ")[:160])
    runs = health.get("refresh_runs")
    if runs is not None:
        runs, failures = int(runs or 0), int(health.get("refresh_failures") or 0)
        if runs > 0 and failures >= runs:
            signals.append(f"every self-refresh attempt has failed ({failures}/{runs})")
    return signals


def check_provenance(health: dict, data_quality: dict, freshness=None) -> list[dict]:
    """Does the product's own freshness reporting agree with itself?

    Staleness is attributed before it is graded. ``days_behind`` alone cannot
    say whether the source has published nothing (upstream publication lag,
    a warning) or whether rows exist upstream that our pipeline missed
    (an ingest failure, a blocker). The /freshness catalogue and the refresh
    counters are what separate the two.
    """
    findings = []

    def add(level, message):
        findings.append({"level": level, "message": message})

    if not health:
        add("blocker", "/health returned no payload, so nothing can be trusted")
        return findings

    behind = health.get("days_behind")
    status = health.get("status")
    if behind is None:
        add("blocker", "/health does not report days_behind")
    elif behind < 0:
        add("blocker",
            f"newest arrival date {health.get('data_max_date')} is {-behind} days in "
            f"the future - the month/day mis-parse is present")
    elif behind > 4:
        failures = pipeline_failure_signals(health)
        lag = upstream_lag_signature(freshness, health.get("data_max_date"))
        if failures:
            add("blocker",
                f"newest arrival date is {behind} days old and our ingest is itself "
                f"failing - {failures[0]}. This is a pipeline stall, not upstream "
                f"lag, and it will not close without a fix")
        elif lag:
            add("warning",
                f"newest arrival date is {behind} days old, but all "
                f"{lag['n_commodities']} commodities in /freshness stop on the same "
                f"date ({lag['latest_date']}): upstream publication lag, not a "
                f"pipeline failure - data.gov.in has not published newer arrivals")
        else:
            add("blocker",
                f"newest arrival date is {behind} days old and commodities do not "
                f"share one latest date - our ingest is behind upstream")
    else:
        add("ok", f"data is {behind} day(s) behind today")

    if status and status != "healthy":
        add("warning", f"/health reports status={status!r}")
    if health.get("n_future_dates"):
        add("blocker", f"{health['n_future_dates']} rows carry an impossible date")

    if health.get("index_fault_pending"):
        # DuckDB leaves the prices UNIQUE index inconsistent after a bulk load
        # that runs out of memory. Writes keep failing until the table is
        # rebuilt, so a recorded-and-unrepaired fault is a stall, not a detail.
        add("blocker",
            "an inconsistent prices index is recorded and has not been repaired "
            "yet - writes to the warehouse fail until the next run rebuilds the "
            "table")

    outcome = health.get("last_outcome")
    if outcome == "failure":
        add("blocker", "the last ingest run failed")
    elif outcome == "degraded":
        add("warning", "the last ingest run was degraded (a source was unavailable)")

    if health.get("refresh_runs") is not None:
        failures = int(health.get("refresh_failures") or 0)
        if failures and failures >= int(health.get("refresh_runs") or 1):
            add("blocker", "every self-refresh attempt has failed: "
                           + str(health.get("last_refresh_error"))[:200])
        else:
            add("ok", f"self-refresh {int(health['refresh_runs']) - failures} ok of "
                      f"{health['refresh_runs']} attempt(s)")

    if data_quality and data_quality.get("n_future_dates"):
        add("blocker", f"/data-quality confirms {data_quality['n_future_dates']} "
                       f"impossible dates")
    return findings


def check_cross_surface(api_health: dict, mirror_health: dict) -> list[dict]:
    """Two instances serving different worlds is a consumer-visible bug.

    The mirror is the failover, not a second opinion: when the primary's edge
    answers `503 no healthy upstream`, the docs site, the landing page and
    refresh-live-data.yml all fall through to this host. So a mirror that
    cannot be identified or dated is the *failover path* serving something
    else. On 2026-10-02 the mirror answered ``status: "healthy"`` while
    publishing no ``data_max_date`` and no ``version`` at all - and every check
    here stayed silent, because the disagreement loop skips a key either side
    omits and an omitted date is not a date that conflicts. Both halves of that
    are findings now.
    """
    findings = []
    if not api_health or not mirror_health:
        return findings
    for key in ("n_prices", "data_max_date"):
        primary = api_health.get(key)
        mirror = mirror_health.get(key)
        if primary is not None and mirror is not None and primary != mirror:
            findings.append({
                "level": "warning",
                "message": f"primary and mirror disagree on {key}: {primary} vs {mirror}",
            })
    if not mirror_health.get("version"):
        findings.append({
            "level": "warning",
            "message": (
                "the mirror publishes no version - the failover is running a build "
                "that cannot be identified; redeploy it before trusting it"
            ),
        })
    if mirror_health.get("status") == "healthy" and not mirror_health.get("data_max_date"):
        findings.append({
            "level": "warning",
            "message": (
                "the mirror reports status=healthy while publishing no newest arrival "
                "date - a build that cannot date its data is not a healthy one"
            ),
        })
    return findings


def run(api_base: str = DEFAULT_API, mirror_base: str = DEFAULT_MIRROR) -> dict:
    pages = check_pages()
    links = check_links(pages)

    primary = check_api(api_base)
    health_entry = next((r for r in primary["routes"] if r["path"] == "/health"), {})
    quality_entry = next((r for r in primary["routes"] if r["path"] == "/data-quality"), {})
    freshness_entry = next((r for r in primary["routes"] if r["path"] == "/freshness"), {})

    _status, mirror_body, _elapsed = _get(mirror_base + "/health", timeout=30)
    mirror_health = {}
    try:
        mirror_health = json.loads(mirror_body)
    except Exception:
        pass

    provenance = check_provenance(
        health_entry.get("payload") or {},
        quality_entry.get("payload") or {},
        freshness_entry.get("payload"),
    )

    broken_links = [link for link in links if not link["ok"]]
    page_problems = [f"{p['name']}: {'; '.join(p['problems'])}" for p in pages if p["problems"]]
    api_problems = [f"{r['path']}: {'; '.join(r['problems'])}" for r in primary["routes"] if r["problems"]]
    blockers = (
        page_problems
        + api_problems
        + [f"broken link {l['url']} ({l['status']})" for l in broken_links]
        + [f["message"] for f in provenance + check_cross_surface(health_entry.get("payload") or {}, mirror_health)
           if f["level"] == "blocker"]
    )

    warnings = [f"{p['name']}: {'; '.join(p['warnings'])}" for p in pages if p["warnings"]]
    warnings += [f"{f['level']}: {f['message']}" for f in provenance if f["level"] == "warning"]
    warnings += [f"{f['level']}: {f['message']}" for f in check_cross_surface(health_entry.get("payload") or {}, mirror_health)
                 if f["level"] == "warning"]

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pages": pages,
        "links": links,
        "api": primary,
        "mirror_health": {k: mirror_health.get(k) for k in ("status", "n_prices", "n_commodities", "data_max_date")},
        "provenance": provenance,
        "blockers": blockers,
        "warnings": warnings,
        "ok": not blockers,
    }


def render(report: dict) -> str:
    lines = []
    lines.append("MandiIQ consumer check - " + report["generated_at"])
    lines.append("")
    lines.append("Pages")
    for page in report["pages"]:
        mark = "OK " if not page["problems"] else "FAIL"
        detail = page["title"] or ""
        lines.append(f"  [{mark}] {page['name']:<20} {page['status']} "
                     f"{page['bytes']}B {page['seconds']}s  {detail}")
        for problem in page["problems"]:
            lines.append(f"         ! {problem}")
        for warning in page["warnings"]:
            lines.append(f"         ~ {warning}")

    lines.append("")
    lines.append(f"Links followed: {len(report['links'])}")
    for link in report["links"]:
        if not link["ok"]:
            lines.append(f"  [FAIL] {link['url']} ({link['status']}) from {link['from']}")

    lines.append("")
    lines.append("API")
    for route in report["api"]["routes"]:
        mark = "OK " if not route["problems"] else "FAIL"
        lines.append(f"  [{mark}] {route['status']} {route['seconds']:>6}s  {route['path']}")
        for problem in route["problems"]:
            lines.append(f"         ! {problem}")

    lines.append("")
    lines.append("Data provenance")
    for finding in report["provenance"]:
        symbol = {"ok": "+", "warning": "~", "blocker": "!"}.get(finding["level"], "?")
        lines.append(f"  [{symbol}] {finding['message']}")

    lines.append("")
    lines.append(f"Result: {'PASS' if report['ok'] else 'FAIL'} "
                 f"({len(report['blockers'])} blocker(s), {len(report['warnings'])} warning(s))")
    for blocker in report["blockers"]:
        lines.append(f"  BLOCKER {blocker}")
    for warning in report["warnings"]:
        lines.append(f"  warning {warning}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="End-to-end consumer check for MandiIQ.")
    parser.add_argument("--api-base", default=DEFAULT_API)
    parser.add_argument("--mirror-base", default=DEFAULT_MIRROR)
    parser.add_argument("--json", action="store_true", help="Print the raw report")
    args = parser.parse_args(argv)

    report = run(args.api_base, args.mirror_base)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
