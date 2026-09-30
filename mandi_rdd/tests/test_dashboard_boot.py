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


def test_dashboard_app_runs_without_exceptions():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(DASHBOARD_DIR / "app.py"), default_timeout=120)
    app.run()

    assert not app.exception, "Dashboard raised: " + "; ".join(
        str(exc.value) for exc in app.exception
    )


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
