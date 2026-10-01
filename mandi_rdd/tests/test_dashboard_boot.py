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

    A dead loopback API keeps this deterministic (and offline): /health fails at
    once, the snapshot reports "empty", and the banner is guaranteed to render.
    """
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("MANDIIQ_UI_REFRESH_SECONDS", "60")
    monkeypatch.setenv("MANDIQ_API_URL", "http://127.0.0.1:9")
    app = AppTest.from_file(str(DASHBOARD_DIR / "app.py"), default_timeout=60)
    app.run()

    assert not app.exception, "Dashboard raised: " + "; ".join(
        str(exc.value) for exc in app.exception
    )

    bodies = [str(getattr(el, "body", "")) for el in app.get("html")]
    banners = [b for b in bodies if "mandiq-live-banner" in b]
    assert len(banners) == 1, f"expected one freshness banner, got {len(banners)}"

    sidebar_blocks = [b for b in bodies if ">Live data</div>" in b]
    assert len(sidebar_blocks) == 1, (
        f"expected one sidebar freshness block, got {len(sidebar_blocks)}"
    )

    hero = [str(m.value) for m in app.markdown if "page-hero" in str(m.value)]
    assert hero, "the page body never rendered - only the shell did"

    assert len(app.get("page_link")) >= 10, "the sidebar navigation never rendered"


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
