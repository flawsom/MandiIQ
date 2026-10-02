"""Dashboard boot smoke tests.

The Streamlit cockpit is the user-facing surface of MandiIQ, so it gets a real
headless run here (``streamlit.testing.v1.AppTest`` executes the actual app
script, including every page import and the sidebar/navigation shell). That is
the check which catches what actually took the dashboard down before:

* Python 3.12-only syntax on a 3.11 runtime (an f-string with nested
  same-type quotes in dashboard/components.py meant no page could render), and
* pages importing modules or helpers that no longer exist.

The integration workflow additionally starts the real Streamlit server and
waits for HTTP 200, which covers the cases an in-process run cannot see.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

pytest.importorskip("streamlit", reason="streamlit is required for dashboard tests")

REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_DIR = REPO_ROOT / "mandi_rdd" / "dashboard"
PAGES_DIR = DASHBOARD_DIR / "pages"
PAGE_FILES = sorted(
    p for p in PAGES_DIR.glob("*.py") if p.name not in {"__init__.py", "components.py"}
)


def test_dashboard_app_runs_without_exceptions(monkeypatch):
    """The app must render its shell and its live strip without blowing up.

    It runs with the production refresh interval, not with the tick switched
    off. Disabling the tick is what let the 2.4.1 outage ship: the tick
    requested a full rerun on every entry, the rerun re-entered the tick, and
    the script never reached the sidebar or the page body. AppTest follows
    rerun requests, so a loop shows up here as a run that never settles.
    """
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("MANDIIQ_UI_REFRESH_SECONDS", "60")
    app = AppTest.from_file(str(DASHBOARD_DIR / "app.py"), default_timeout=60)
    app.run()

    assert not app.exception, "Dashboard raised: " + "; ".join(
        str(exc.value) for exc in app.exception
    )


def test_one_run_paints_the_whole_cockpit_and_one_banner(monkeypatch):
    """A single settled run renders the chrome, the sidebar and the page body.

    The deployed cockpit showed two identical freshness banners above an empty
    page: aborted runs kept re-painting the only two elements they reached
    before the rerun. One settled run must therefore paint the page hero, the
    navigation, and exactly one banner.

    The warehouse behind this run is made to answer nothing at all: a dead
    loopback API for the real reads, and a stubbed data layer for the freshness
    snapshot. That second part matters - a sandbox or a CI runner with a local
    DuckDB file would answer the /data-quality fallback with a row count from
    the machine running the test, which is not the state under test.
    """
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    from mandi_rdd.dashboard import data_access

    monkeypatch.setenv("MANDIIQ_UI_REFRESH_SECONDS", "60")
    monkeypatch.setenv("MANDIQ_API_URL", "http://127.0.0.1:9")
    # What Streamlit Cloud gets from a restarting API: data_access returns {} for
    # /health and an error for the /data-quality fallback, because Cloud ships
    # no local warehouse. The app has to read that as "unreachable".
    monkeypatch.setattr(data_access, "get_health", lambda: {})
    monkeypatch.setattr(
        data_access, "get_data_quality", lambda: {"error": "Unreachable: no healthy upstream"}
    )
    st.cache_data.clear()  # the snapshot is cached for 15s across runs
    app = AppTest.from_file(str(DASHBOARD_DIR / "app.py"), default_timeout=60)
    app.run()

    assert not app.exception, "Dashboard raised: " + "; ".join(
        str(exc.value) for exc in app.exception
    )

    bodies = [str(getattr(el, "body", "")) for el in app.get("html")]
    banners = [b for b in bodies if "mandiq-live-banner" in b]
    assert len(banners) == 1, f"expected one freshness banner, got {len(banners)}"

    # The deployed cockpit reported a restarting API as a warehouse that had
    # lost every row: "Live data: empty / The warehouse has no price rows at
    # all. data through unknown". Nothing was asked of the warehouse, so the
    # strip must say so instead of making a claim it cannot support.
    assert "Live data: unreachable" in banners[0], (
        "an API that did not answer must be reported as unreachable: " + banners[0][:400]
    )
    assert "no price rows at all" not in banners[0], (
        "an unreachable API is not evidence of an empty warehouse"
    )
    assert "no answer from" in banners[0], (
        "the strip must name the address that did not answer"
    )

    sidebar_blocks = [b for b in bodies if ">Live data</div>" in b]
    assert len(sidebar_blocks) == 1, (
        f"expected one sidebar freshness block, got {len(sidebar_blocks)}"
    )

    # The revision on screen has to be the cockpit's own, not the API's build.
    assert any("cockpit " in b for b in bodies), (
        "the sidebar must print the cockpit revision it is running"
    )

    hero = [str(m.value) for m in app.markdown if "page-hero" in str(m.value)]
    assert hero, "the page body never rendered - only the shell did"

    assert len(app.get("page_link")) >= 10, "the sidebar navigation never rendered"


def test_the_refresh_error_reaches_the_screen_instead_of_being_eaten_by_html(monkeypatch):
    """The reason a refresh failed must survive the HTML surfaces.

    /health reports a transport failure as ``price source unavailable:
    <urlopen error [Errno 111] Connection refused>``. The strip and the
    sidebar are drawn with ``st.html``, so the angle brackets were parsed as a
    tag and the cause vanished: the deployed cockpit showed "error: price
    source unavailable:" - the failure named, its reason eaten - on the one
    surface built to explain a frozen warehouse. The text is escaped now, so
    the reason that was fetched is the reason on screen.
    """
    import html

    import streamlit as st
    from streamlit.testing.v1 import AppTest

    from mandi_rdd.dashboard import data_access

    reason = (
        "price source unavailable: <urlopen error [Errno 111] Connection refused>"
    )
    escaped = html.escape(reason)

    monkeypatch.setenv("MANDIIQ_UI_REFRESH_SECONDS", "60")
    monkeypatch.setenv("MANDIQ_API_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(
        data_access,
        "get_health",
        lambda: {
            "status": "stale",
            "n_prices": 1994318,
            "n_commodities": 423,
            "data_max_date": "2026-09-25",
            "days_behind": 7,
            "last_run_utc": "2026-10-02T15:27:21Z",
            "last_outcome": "degraded",
            "last_refresh_error": reason,
            "mirror_configured": True,
            "last_ceda": {"fetched": 0, "new": 0},
        },
    )
    monkeypatch.setattr(
        data_access, "get_data_quality", lambda: {"error": "Unreachable: no healthy upstream"}
    )

    st.cache_data.clear()  # the snapshot is cached for 15s across runs
    app = AppTest.from_file(str(DASHBOARD_DIR / "app.py"), default_timeout=60)
    app.run()

    assert not app.exception, "Dashboard raised: " + "; ".join(
        str(exc.value) for exc in app.exception
    )

    bodies = [str(getattr(el, "body", "")) for el in app.get("html")]
    banners = [b for b in bodies if "mandiq-live-banner" in b]
    assert len(banners) == 1, f"expected one freshness banner, got {len(banners)}"
    assert escaped in banners[0], (
        "the refresh reason must reach the banner escaped, not parsed away: "
        + banners[0][:500]
    )
    assert reason not in banners[0], (
        "raw angle brackets are parsed as a tag and the reason disappears"
    )

    sidebar_blocks = [b for b in bodies if ">Live data</div>" in b]
    assert len(sidebar_blocks) == 1, (
        f"expected one sidebar freshness block, got {len(sidebar_blocks)}"
    )
    # The sidebar body is its own st.html element, painted under the header.
    sidebar_bodies = [
        b for b in bodies if "padding:0 1rem 0.4rem;font-family:IBM Plex Mono" in b
    ]
    assert len(sidebar_bodies) == 1, (
        f"expected one sidebar freshness body, got {len(sidebar_bodies)}"
    )
    assert escaped in sidebar_bodies[0], (
        "the sidebar copy of the reason must reach the screen too: "
        + sidebar_bodies[0][:500]
    )
    assert reason not in sidebar_bodies[0], (
        "the sidebar escapes the reason for the same reason as the banner"
    )


def test_the_shell_cannot_double_its_header_or_hide_its_controls():
    """The cockpit's own chrome must not fight Streamlit's.

    Three regressions are pinned here, all of them reported from the deployed
    dashboard as "a duplicate header" and "an alignment error":

    * the top bar was position:fixed at z-index 1000 while Streamlit's own
      header is pinned at top:0 at the top of its own stacking order, so the two
      headers were painted into the same 56 pixels and a 56px spacer shifted
      the page,
    * ``.stButton:first-of-type`` matched every button in the app (each button
      is the first div inside its own element container) and fixed-positioned
      them all at zero size, and
    * the theme toggle's JS clicked "the first button in the document", which is
      a sidebar button, because Streamlit renders the sidebar before the page.
    """
    source = (DASHBOARD_DIR / "app.py").read_text(encoding="utf-8", errors="replace")

    assert "position: fixed; top: 0; left: 0; right: 0;\n" not in source, (
        "the top bar is fixed again - it will sit under Streamlit's header"
    )
    assert "mandiq-topbar-spacer" not in source, "the 56px spacer hack is back"

    assert ".stButton:first-of-type" not in source, (
        "this selector hides every button in the cockpit, not just the hidden toggle"
    )
    assert '[data-testid="stElementContainer"].st-key-_topbar_theme_btn' in source
    assert ".st-key-_topbar_theme_btn button" in source, (
        "the theme toggle must click its own keyed button, not the first button in the DOM"
    )

    assert '"{RUST}"' not in source, "an un-substituted CSS placeholder is leaking into the sidebar"


def _function_block(source: str, name: str) -> str:
    """The whole body of a top-level ``def <name>(`` block, by indentation."""
    lines = source.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"def {name}("))
    block = [lines[start]]
    for line in lines[start + 1:]:
        if line.strip() and not line.startswith((" ", "\t")):
            break
        block.append(line)
    return "\n".join(block)


def test_the_freshness_surfaces_repaint_themselves_and_never_rerun_the_app():
    """The shell must ask for no whole-app rerun at all.

    A rerun requested from inside a timer fragment hands the browser a run it
    did not ask for while the elements of the run that was interrupted are still
    on screen, and the freshness strip - what every run paints first - is the
    element that then ends up painted twice. Both freshness surfaces therefore
    repaint themselves in place, each in its own fragment, which is the one
    repaint path Streamlit clears and redraws instead of accumulating.
    """
    source = (DASHBOARD_DIR / "app.py").read_text(encoding="utf-8", errors="replace")

    assert "st.rerun(" not in source, (
        "the shell must not rerun the app - repaint through a fragment instead"
    )
    assert "_LIVE_TICK_REENTRY_S" not in source, (
        "the re-entry guard only made sense while the tick reran the app"
    )

    for tick in ("_paint_live_strip_tick", "_paint_sidebar_live_tick"):
        assert f"st.fragment(run_every=LIVE_REFRESH_SECONDS)({tick})" in source, (
            f"{tick} must be the fragment that repaints it in place"
        )
        assert "_live_snapshot()" in _function_block(source, tick), (
            f"{tick} must paint the shared snapshot"
        )

    # A repaint has to be a pure paint: reading inside the painter would leave
    # the strip able to fail (or block on the API) midway through a repaint.
    for painter in ("_paint_live_strip", "_paint_sidebar_live"):
        body = _function_block(source, painter)
        for forbidden in ("_live_snapshot()", "get_health(", "requests.", "_request_ingest("):
            assert forbidden not in body, (
                f"{painter} must paint the snapshot it is handed, not fetch one"
            )


def test_the_routed_page_is_run_once_and_never_from_a_timer_fragment():
    """The routed page object can only be run once per script run.

    Wrapping ``pg.run()`` in ``st.fragment(run_every=...)`` looked like a third
    ticking surface, but a fragment tick re-enters only the fragment - so the
    page object ``st.navigation`` returned was run a second time and Streamlit
    answered every route with "This page cannot be called directly. Only the
    page returned from st.navigation can be called once." What the user saw was
    a traceback where the Risk Map belonged. The page body is run plainly, once;
    only the strip and the sidebar carry the "Live updates" clock. If a later
    shell re-enters it anyway, the second call is swallowed as a no-op instead
    of a traceback where the page belongs.
    """
    source = (DASHBOARD_DIR / "app.py").read_text(encoding="utf-8", errors="replace")

    body = _function_block(source, "_paint_current_page")
    assert "pg.run()" in body, "the routed page must be run by the shell"
    assert source.count("pg.run()") == 1, (
        "the page body must be executed in exactly one place"
    )
    assert (
        "st.fragment(run_every=LIVE_REFRESH_SECONDS)(_paint_current_page)" not in source
    ), (
        "a ticked page fragment re-runs the st.navigation page object and crashes "
        "every route - see the exception _paint_current_page documents"
    )
    assert "_page_tick" not in source, "the page must not be wrapped in a tick"
    assert "st.rerun(" not in source, (
        "the shell must not rerun the app - repaint through a fragment instead"
    )
    assert source.count('st.session_state.get("live_auto_refresh", True)') >= 2, (
        "one toggle must gate both ticking surfaces"
    )
    # The re-entry is refused by Streamlit, not by us: the guard has to swallow
    # exactly that refusal - the page is already painted - and nothing else.
    assert '"cannot be called" in str(_exc)' in body, (
        "a second call must degrade to a no-op, not a traceback where the page belongs"
    )


def test_the_cockpit_names_the_revision_it_is_running():
    """Which revision the host serves must be readable off the app itself.

    The sidebar's ``build`` is the API's ``/health.version``: it names the
    server that answered the freshness question, not the cockpit on screen, and
    the two are separate deployments. So a fix that is on ``master`` - and green
    right here - stays invisible for as long as Streamlit Community Cloud has
    not redeployed, and until this string existed the only way to tell was to
    read line numbers out of a traceback: a live cockpit still named
    ``pg.run()`` on line 1792, the line the pre-fix file had it on, while
    ``master`` had moved it to 1795. ``COCKPIT_REV`` is painted beside the build
    so the running revision is a fact on the page.
    """
    source = (DASHBOARD_DIR / "app.py").read_text(encoding="utf-8", errors="replace")

    assert 'COCKPIT_REV = "' in source, "the cockpit must carry a revision string"
    assert '"cockpit " + COCKPIT_REV' in source, (
        "the sidebar has to paint the revision it is running"
    )
    assert "COCKPIT_REV" in _function_block(source, "_paint_sidebar_live")


def test_an_unreachable_api_is_never_reported_as_an_empty_warehouse():
    """A missing answer is not a missing row.

    ``data_access.get_health`` returns ``{}`` whenever the request fails, and
    the dashboard derived "empty" from that sentinel - so a deploy-time blip
    (Northflank answers a restarting service with 503 "no healthy upstream")
    was published to the user as a warehouse whose price table had been wiped.
    """
    source = (DASHBOARD_DIR / "app.py").read_text(encoding="utf-8", errors="replace")

    assert '"unreachable": "Nothing answered at the production API' in source

    ladder = _function_block(source, "_live_snapshot")
    assert "health_answered = bool(live)" in ladder
    assert 'status = "unreachable"' in ladder, "the failure sentinel needs its own status"
    assert 'elif n_prices == 0:' in ladder, (
        "only a count of zero - never a missing count - may be called empty"
    )
    assert 'if not live.get("n_prices"):' not in ladder, (
        "this is the line that turned a failed request into an empty warehouse"
    )


def test_a_build_that_cannot_date_its_data_is_never_called_healthy():
    """A "healthy" reading from a payload with no dates is the absence of a fact.

    An older instance reports status="healthy" while publishing no newest
    arrival date at all. Trusting it made the sidebar say "healthy" at the same
    moment the banner said "stale" - two halves of one cockpit disagreeing.
    """
    source = (DASHBOARD_DIR / "app.py").read_text(encoding="utf-8", errors="replace")
    assert (
        'if status == "healthy" and behind is None and not live.get("data_max_date"):'
        in source
    )


def test_live_freshness_strip_is_wired_into_the_shell():
    """The staleness strip, the auto-refresh fragments and the manual refresh
    control all have to be present: they are what keeps every figure's age
    honest instead of a snapshot nobody remembered to reload."""
    source = (DASHBOARD_DIR / "app.py").read_text(encoding="utf-8", errors="replace")
    for needle in (
        "def _live_snapshot",
        "def _behind_wording",
        "def _request_ingest",
        "def _paint_live_strip",
        "def _paint_sidebar_live",
        "mandiq-live-banner",
        "run_every=LIVE_REFRESH_SECONDS",
        "MANDIIQ_UI_REFRESH_SECONDS",
    ):
        assert needle in source, f"dashboard shell is missing {needle!r}"
    # The strip must be driven by the API's own status vocabulary, not a
    # hard-coded "healthy".
    assert 'if live["status"] == "healthy"' in source
    # A repaint that finds the same /health payload would otherwise look like no
    # repaint at all, so the sidebar block dates itself.
    assert '"checked "' in source and "time.gmtime()" in source


def test_every_page_module_is_importable():
    failures = []
    for path in PAGE_FILES:
        module = f"mandi_rdd.dashboard.pages.{path.stem}"
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - report, do not mask
            failures.append(f"{module}: {type(exc).__name__}: {exc}")
    assert not failures, "Page modules that fail to import:\n" + "\n".join(failures)


def test_every_page_exposes_a_render_callable():
    missing = []
    for path in PAGE_FILES:
        module = importlib.import_module(f"mandi_rdd.dashboard.pages.{path.stem}")
        if not callable(getattr(module, "render", None)):
            missing.append(path.name)
    assert not missing, "Page modules without render(): " + ", ".join(missing)


def test_cockpit_declares_the_full_route_table():
    source = (DASHBOARD_DIR / "app.py").read_text(encoding="utf-8", errors="replace")
    routes = [line.split('url_path="')[1].split('"')[0]
              for line in source.splitlines() if 'url_path="' in line]
    for route in ("", "discontinuity", "forecast", "risk-map", "satellite",
                  "discount-simulator", "analyst-lab", "ask", "settings", "about"):
        assert route in routes, f"route {route!r} is missing from the cockpit"
    assert len(routes) >= 10, f"route table shrank: {routes}"
