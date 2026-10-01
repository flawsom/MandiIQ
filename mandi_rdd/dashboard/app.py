"""

MandiIQ - Route-based Navigation Dashboard with Global Shell



Entry point for Streamlit. Uses st.navigation() for proper URL routing,

deep linking, and error page handling - replaces the old 5-tab layout.



Sitemap (15 routes):

  /  /discontinuity  /forecast  /risk-map  /satellite

  /discount-simulator  /analyst-lab  /ask  /settings  /about

  /onboarding  /loading  /404  /error/model-unavailable  /error/no-data



Global Shell:

  - Sidebar (expanded/collapsed/hidden responsive)

  - Top bar (breadcrumb, Ask MandiIQ, settings, model-served)

  - Footer (pipeline timestamp, data attribution, methodology link)

  - Model-health dot (green/amber/red) in sidebar



Design: turmeric/ink/slate palette, design.css token system.

See mandi_rdd/styles/design.css and mandi_rdd/dashboard/theme.py

"""



import os

import sys

import time

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))



from functools import partial



import streamlit as st



from mandi_rdd.dashboard.theme import (

    inject_theme, inject_atmosphere,

    INK, SLATE, PAPER, MUTED, FAINT, TURMERIC, RUST, SAGE,

)



# ═══════════════════════════════════════════════════════════

# SVG Icons - from shared icon library

# ═══════════════════════════════════════════════════════════



from mandi_rdd.dashboard.icons import SVG_SUN, SVG_MOON, SVG_LEAF, SVG_CHAT, SVG_COG



# ═══════════════════════════════════════════════════════════

# Page imports

# ═══════════════════════════════════════════════════════════



from mandi_rdd.dashboard.pages.executive_overview import render as render_overview

from mandi_rdd.dashboard.pages.discontinuity import render as render_discontinuity

from mandi_rdd.dashboard.pages.forecast import render as render_forecast

from mandi_rdd.dashboard.pages.risk_map import render as render_risk_map

from mandi_rdd.dashboard.pages.satellite import render as render_satellite

from mandi_rdd.dashboard.pages.discount_simulator import render as render_discount

from mandi_rdd.dashboard.pages.ask import render as render_ask

from mandi_rdd.dashboard.pages.settings import render as render_settings

from mandi_rdd.dashboard.pages.about import render as render_about

from mandi_rdd.dashboard.pages.onboarding import render as render_onboarding

from mandi_rdd.dashboard.pages.loading import render as render_loading

from mandi_rdd.dashboard.pages.error_404 import render as render_404

from mandi_rdd.dashboard.pages.error_model_unavailable import render as render_model_unavailable

from mandi_rdd.dashboard.pages.error_no_data import render as render_no_data



# Orphan pages - previously unregistered in the nav

from mandi_rdd.dashboard.pages.deep_dive import render as render_deep_dive

from mandi_rdd.dashboard.pages.causal_explorer import render as render_causal_explorer

from mandi_rdd.dashboard.pages.risk_forecast import render as render_risk_forecast

from mandi_rdd.dashboard.pages.procurement_advisor import render as render_procurement_advisor

from mandi_rdd.dashboard.pages.analyst_lab import render as render_analyst_lab



# Components gallery (dev-only, not in prod nav by default)

try:

    from mandi_rdd.dashboard.pages.components import render as render_components

    _HAS_COMPONENTS_PAGE = True

except ImportError:

    _HAS_COMPONENTS_PAGE = False



# ═══════════════════════════════════════════════════════════

# Page config

# ═══════════════════════════════════════════════════════════



st.set_page_config(

    page_title="MandiIQ \u2014 Price Intelligence System",

    page_icon="\U0001f33e",

    layout="wide",

    initial_sidebar_state="expanded",

)



# SEO / metadata injection (claude-seo methodology).

# Route-aware canonical, Open Graph, Twitter, and JSON-LD tags. The helper is

# fully guarded and can never raise, so it cannot break page rendering.

def _inject_page_seo() -> None:
    """Route-aware canonical, Open Graph, Twitter and JSON-LD tags.

    Must run after st.navigation() creates ``pg``: referencing it earlier
    raised NameError on every page, so the function was silently swallowed by
    the guard below and the fallback canonical link was all that ever reached
    the page.
    """
    try:
        from mandi_rdd.dashboard.seo import inject_page_seo
        st.html(inject_page_seo(pg.url_path))
    except Exception:
        # Absolute fallback: a minimal canonical link only.
        st.html(
            '<link rel="canonical" href="https://mandiiq.unifies.codes/" />',
        )



# ═══════════════════════════════════════════════════════════

# Inject design system

# ═══════════════════════════════════════════════════════════



inject_theme()

inject_atmosphere()



# ── Server-side: initialize surface_mode from URL query param ──

# Reads ?surface=true/false from the URL, set by JavaScript via

# history.replaceState on the previous visit. This eliminates the

# flash-of-wrong-theme - the correct CSS is served on the very first

# render instead of relying on a client-side restore + second rerun.

_surface_param = st.query_params.get_all("surface")

if _surface_param and "surface_mode" not in st.session_state:

    st.session_state.surface_mode = _surface_param[0] == "true"



# ── Surface-mode: swap pure black for dark gray ──

_surface_on = st.session_state.get("surface_mode", False)

if _surface_on:

    st.html(

        f"""<style>

:root {{

    --color-bg-base: #111111 !important;

    --color-bg-radial-end: #1a1a1a !important;

    --color-surface: #1a1a1a !important;

    --color-surface-glass: rgba(255, 255, 255, 0.03) !important;

    --color-surface-glass-hover: rgba(255, 255, 255, 0.06) !important;

    --hairline: rgba(255, 255, 255, 0.06) !important;

    --hairline-strong: rgba(255, 255, 255, 0.12) !important;

}}

.atmosphere-flash {{

    background: radial-gradient(circle, rgba(215, 255, 0, 0.04) 0%, transparent 70%) !important;

}}

.atmosphere-cloud {{

    background: radial-gradient(circle, rgba(255, 255, 255, 0.02) 0%, transparent 70%) !important;

}}

.dot-grid {{

    background-image: radial-gradient(rgba(255, 255, 255, 0.035) 1.5px, transparent 2px) !important;

}}

.glass {{

    background: linear-gradient(135deg,

        rgba(255, 255, 255, 0.025) 0%,

        rgba(255, 255, 255, 0.008) 100%) !important;

}}

.crosshair-panel::before,

.crosshair-panel::after,

.crosshair-panel-inner::before,

.crosshair-panel-inner::after {{

    border-color: rgba(215, 255, 0, 0.7) !important;

}}

div[data-testid="stSidebar"] {{

    background: var(--color-surface) !important;

}}

.mandiq-topbar {{

    background: var(--color-surface) !important;

}}

</style>""",

    )

# ── Persist surface mode via localStorage + URL query param ──

# JavaScript saves the preference to TWO places on every render:

#   1. localStorage - for JavaScript-based reading

#   2. URL query param (?surface=true/false) - for server-side init on next visit

# The server reads the query param above to serve the correct CSS on first render,

# eliminating the flash-of-wrong-theme that a pure-JS restore would cause.

_js_val = "true" if _surface_on else "false"

st.html(

    f"""<script>

(function(){{

    // 1. Always sync the body class - never blocked by localStorage

    document.body.classList.toggle('theme-surface', {_js_val});

    

    // 2. Save to localStorage (best-effort, guarded for private browsing)

    try {{ localStorage.setItem('mandiiq_surface_mode', '{_js_val}'); }} catch(e) {{}}

    

    // 3. Sync URL query param for server-side init on next visit

    //    Uses history.replaceState so no page reload is triggered.

    try {{

        var url = new URL(window.location);

        if (url.searchParams.get('surface') !== '{_js_val}') {{

            url.searchParams.set('surface', '{_js_val}');

            window.history.replaceState({{}}, '', url);

        }}

    }} catch(e) {{}}



    // 4. Top-bar theme toggle: event delegation (streamlit strips inline onclick)

    //    When the user clicks the theme-toggle-topbar <a>, find the hidden

    //    Streamlit button with empty text and click it. Guarded by a flag

    //    so addEventListener only registers once across Streamlit reruns.

    if (!window.__mandiiqTopbarToggled) {{

        window.__mandiiqTopbarToggled = true;

        try {{

            document.addEventListener('click', function(e) {{

                var target = e.target;

                while (target && target !== document) {{

                    if (target.id === 'theme-toggle-topbar') {{

                        e.preventDefault();

                        var c = document.querySelectorAll('[data-testid="stButton"]');

                        for (var i = 0; i < c.length; i++) {{

                            var b = c[i].querySelector('button');

                            if (b && b.textContent.trim() === '') {{

                                b.click();

                                break;

                            }}

                        }}

                        return;

                    }}

                    target = target.parentElement;

                }}

            }});

        }} catch(e) {{}}

    }}



    // 5. Listen for storage events from other tabs (once per tab session)

    //    When another tab writes to localStorage, this tab receives

    //    a 'storage' event. If the theme key changed, sync by clicking

    //    the hidden toggle button. Guarded by a flag so addEventListener

    //    only registers once (Streamlit reruns would otherwise accumulate

    //    duplicate listeners).

    if (!window.__mandiiqStorageListened) {{

        window.__mandiiqStorageListened = true;

        try {{

            window.addEventListener('storage', function(e) {{

                // Read live state from DOM instead of captured variable

                // (closure would be stale after the first toggle)

                if (e.key === 'mandiiq_surface_mode' && e.newValue !== null) {{

                    var isSurface = document.body.classList.contains('theme-surface');

                    if ((e.newValue === 'true') !== isSurface) {{

                        var c = document.querySelectorAll('[data-testid="stButton"]');

                        for (var i = 0; i < c.length; i++) {{

                            var b = c[i].querySelector('button');

                            if (b && b.textContent.trim() === '') {{

                                b.click();

                                break;

                            }}

                        }}

                    }}

                }}

            }});

        }} catch(e) {{}}

    }}

}})();

</script>""",

)



# ═══════════════════════════════════════════════════════════

# Global CSS

# ═══════════════════════════════════════════════════════════



COMMODITY_COLORS = {

    "Onion": "#8B6BC4", "Tomato": RUST,

    "Wheat": "#D4A94E", "Potato": "#B98354",

}



st.html(f"""

<style>

/* ── Top Bar ── */

.mandiq-topbar {{

    /* In the flow, never position:fixed. Streamlit's own header is pinned at

       top:0 at the top of its own stacking order, so a bar asking for z-index

       1000 was painted underneath it (and underneath the Cloud toolbar): two

       headers in one strip of pixels. That is the "duplicate header" and the

       "alignment error" this bar kept producing. */

    position: relative;

    height: 56px;

    background: {INK};

    border-bottom: 1px solid rgba({int(SLATE[1:3],16)},{int(SLATE[3:5],16)},{int(SLATE[5:7],16)},0.5);

    display: flex; align-items: center; justify-content: space-between;

    padding: 0 1.5rem; z-index: 1000;

    font-family: "IBM Plex Sans", system-ui, sans-serif;

}}

.mandiq-topbar-left {{ display: flex; align-items: center; gap: 0.75rem; }}

.mandiq-topbar-logo {{

    font-weight: 700; font-size: 1.1rem;

    color: {TURMERIC}; font-family: "Space Grotesk", system-ui, sans-serif;

    letter-spacing: -0.02em; text-decoration: none;

}}

.mandiq-topbar-breadcrumb {{ font-size: 0.85rem; color: {MUTED}; }}

.mandiq-topbar-breadcrumb a {{ color: {MUTED}; text-decoration: none; transition: color 0.15s; }}

.mandiq-topbar-breadcrumb a:hover {{ color: {TURMERIC}; }}

.mandiq-topbar-breadcrumb .current {{ color: {PAPER}; font-weight: 500; }}

.mandiq-topbar-center {{ display: flex; align-items: center; gap: 0.5rem; }}

.mandiq-topbar-right {{ display: flex; align-items: center; gap: 0.75rem; }}

.mandiq-topbar-right a {{

    color: {MUTED}; text-decoration: none; font-size: 0.85rem; transition: color 0.15s;

}}

.mandiq-topbar-right a:hover {{ color: {TURMERIC}; }}



/* ── Top-bar icon links (Ask, Settings) ── */

.mandiq-topbar-icon-link {{

    display: inline-flex; align-items: center; gap: 4px;

}}

/* ── Model-served indicator ── */

.model-served {{

    font-size: 0.7rem; font-family: "IBM Plex Mono", monospace;

    color: {MUTED}; background: rgba(255,255,255,0.04);

    padding: 0.2rem 0.6rem; border-radius: 4px;

}}



/* ── Top-bar theme toggle ── */

.theme-toggle-btn {{

    display: inline-flex; align-items: center; justify-content: center;

    width: 28px; height: 28px;

    background: none; border: 1px solid transparent;

    border-radius: 4px; cursor: pointer;

    color: {MUTED}; text-decoration: none;

    transition: all 0.2s ease;

}}

.theme-toggle-btn:hover {{

    color: {TURMERIC};

    background: rgba(255,255,255,0.06);

    border-color: rgba(215,255,0,0.2);

}}



/* ── Hidden Streamlit button for JS theme toggle ── */

/* Hide this one widget and nothing else. Streamlit stamps a widget's key onto

   its element container as st-key-<key>, so this class is ours alone. The old

   :first-of-type rule also matched every OTHER button on every page (each

   button is the first div inside its own container), which fixed-positioned

   and zero-sized the entire cockpit's controls. */

[data-testid="stElementContainer"].st-key-_topbar_theme_btn {{

    position: fixed !important;

    opacity: 0 !important;

    pointer-events: none !important;

    width: 0 !important;

    height: 0 !important;

    overflow: hidden !important;

}}



/* ── Sidebar ── */

section[data-testid="stSidebar"] > div:nth-child(1) {{

    padding-top: 1rem !important;

}}

.sidebar-section-header {{

    font-size: 0.7rem; color: {FAINT};

    font-family: "IBM Plex Mono", monospace;

    padding: 0.5rem 1rem 0.25rem 1rem;

    text-transform: uppercase; letter-spacing: 0.05em;

}}



/* ── Footer ── */

.mandiq-footer {{

    border-top: 1px solid rgba({int(SLATE[1:3],16)},{int(SLATE[3:5],16)},{int(SLATE[5:7],16)},0.3);

    padding: 1.5rem 2rem; margin-top: 3rem;

    display: flex; justify-content: space-between;

    align-items: center; flex-wrap: wrap; gap: 0.5rem;

    font-size: 0.75rem; color: {FAINT};

}}

.mandiq-footer a {{ color: {MUTED}; text-decoration: none; transition: color 0.15s; }}

.mandiq-footer a:hover {{ color: {TURMERIC}; }}

.mandiq-footer .mono {{ font-family: "IBM Plex Mono", monospace; font-size: 0.7rem; }}



/* ── Model-health dot ── */

.health-dot {{

    display: inline-block; width: 10px; height: 10px;

    border-radius: 50%; margin-right: 6px;

    transition: background 0.3s;

}}

.health-dot.green {{ background: {SAGE}; }}

.health-dot.amber {{ background: {TURMERIC}; }}

.health-dot.red {{ background: {RUST}; }}



/* ── Legend items ── */

.legend-item {{

    display: flex; align-items: center; gap: 6px;

    font-size: 0.75rem; color: {MUTED};

    font-family: "IBM Plex Mono", monospace;

    padding: 2px 0.5rem;

}}

.legend-dot {{ width: 8px; height: 8px; border-radius: 2px; flex-shrink: 0; }}



/* ── Active nav item styling ── */

.stPageLink-active {{

    border-left: 2px solid {TURMERIC} !important;

    font-weight: 500 !important;

}}



/* Motion Catalog */

@keyframes page-enter {{

    from {{ opacity: 0; transform: translateY(4px); }}

    to   {{ opacity: 1; transform: translateY(0); }}

}}

.main > div:first-child {{

    animation: page-enter 0.35s ease both;

}}



@keyframes chart-draw {{

    from {{ clip-path: inset(0 100% 0 0); }}

    to   {{ clip-path: inset(0 0% 0 0); }}

}}

.mandiq-chart-enter {{

    animation: chart-draw 1.1s ease both;

}}



@media (prefers-reduced-motion: reduce) {{

    *, *::before, *::after {{

        animation-duration: 0.01ms !important;

        animation-iteration-count: 1 !important;

        transition-duration: 0.01ms !important;

    }}

    .main > div:first-child {{

        animation: none !important;

    }}

}}



/* ── Responsive Grid ── */

/* 12-column grid, 24px gutter, max content width 1100px centered */

.mandiq-content {{

    max-width: 1100px;

    margin: 0 auto;

    padding: 0 1rem;

}}



/* Responsive column system (12-col, 24px gutter) */

.mandiq-row {{

    display: grid;

    grid-template-columns: repeat(12, 1fr);

    gap: 24px;

    margin-bottom: 1rem;

}}

.mandiq-col-1  {{ grid-column: span 1; }}

.mandiq-col-2  {{ grid-column: span 2; }}

.mandiq-col-3  {{ grid-column: span 3; }}

.mandiq-col-4  {{ grid-column: span 4; }}

.mandiq-col-6  {{ grid-column: span 6; }}

.mandiq-col-8  {{ grid-column: span 8; }}

.mandiq-col-12 {{ grid-column: span 12; }}



/* ── KPI row: 4cols → 2cols → 2cols ── */

@media (max-width: 1024px) {{

    .kpi-grid {{

        display: grid;

        grid-template-columns: repeat(2, 1fr);

        gap: 16px;

    }}

    .kpi-grid > * {{

        padding: 0.75rem !important;

    }}

}}

@media (max-width: 760px) {{

    .kpi-grid {{

        grid-template-columns: repeat(2, 1fr);

        gap: 12px;

    }}

    .kpi-grid > * {{

        padding: 0.5rem !important;

    }}

}}

@media (min-width: 1025px) {{

    .kpi-grid {{

        display: grid;

        grid-template-columns: repeat(4, 1fr);

        gap: 20px;

    }}

}}



/* ── Ledger: full → compact on mobile ── */

@media (max-width: 760px) {{

    .ledger-table th:nth-child(3),

    .ledger-table td:nth-child(3) {{

        display: none;

    }}

    .ledger-table td:first-child {{

        font-weight: 500;

    }}

    .ledger-table td:nth-child(2) {{

        font-size: 0.75rem;

        color: {MUTED};

        display: block;

        padding-left: 0.75rem !important;

    }}

}}



/* ── Sidebar responsive states ── */

/* Desktop: expanded sidebar (>=1024px) - default */

/* Tablet: collapsed sidebar (760-1024px) */

@media (max-width: 1024px) {{

    section[data-testid="stSidebar"] > div:nth-child(1) {{

        width: 64px !important;

        min-width: 64px !important;

    }}

    section[data-testid="stSidebar"] .sidebar-section-header,

    section[data-testid="stSidebar"] .legend-item span,

    section[data-testid="stSidebar"] .theme-toggle-visual {{

        display: none;

    }}

    .stPageLink span:last-child {{

        display: none;

    }}

}}

/* Mobile: hidden sidebar, content full-width */

@media (max-width: 760px) {{

    section[data-testid="stSidebar"] {{

        display: none !important;

    }}

    .main .block-container {{

        padding-left: 1rem !important;

        padding-right: 1rem !important;

        max-width: 100% !important;

    }}

    .mandiq-topbar {{ padding: 0 0.75rem; }}

    .mandiq-topbar-breadcrumb {{ font-size: 0.75rem; }}

    .mandiq-topbar-center {{ display: none; }}

    .mandiq-footer {{

        flex-direction: column;

        text-align: center;

        gap: 0.5rem;

        padding: 1rem;

    }}

}}

/* Mobile drawer overlay */

@media (max-width: 760px) {{

    .mandiq-mobile-nav {{

        display: flex;

    }}

    .drawer-overlay {{

        position: fixed; top: 0; left: 0; right: 0; bottom: 0;

        background: rgba(11, 15, 30, 0.6);

        z-index: 9999;

    }}

    .drawer-panel {{

        position: fixed; top: 0; left: 0; bottom: 0;

        width: 280px; background: {INK};

        z-index: 10000;

        animation: drawer-slide-in 0.2s ease;

    }}

    @keyframes drawer-slide-in {{

        from {{ transform: translateX(-100%); }}

        to {{ transform: translateX(0); }}

    }}

}}

</style>

""")



# ═══════════════════════════════════════════════════════════

# Model-health & pipeline state (cached)

# ═══════════════════════════════════════════════════════════



@st.cache_data(ttl=60)

def _model_health_status():

    try:

        from mandi_rdd.ai.router import check_health

        health = check_health()

        if health.get("status") == "ok":

            return "green"

        elif health.get("status") == "degraded":

            return "amber"

        return "red"

    except Exception:

        return "amber"



@st.cache_data(ttl=300)

def _latest_pipeline_run():

    # The production API is the source of truth: Streamlit Cloud serves this
    # repository from an immutable layer, so the committed status file can be
    # months old even while the pipeline keeps running.

    try:

        from mandi_rdd.dashboard import data_access as _da

        _health = _da.get_health()

        if _health.get("last_run_utc"):

            return _health["last_run_utc"]

    except Exception:

        pass

    # Fallback: last_ingest_status.json, written by run_nightly / the pipeline.

    try:

        from pathlib import Path as _P

        candidates = [

            _P(__file__).resolve().parent / "data" / "last_ingest_status.json",

            _P(__file__).resolve().parent.parent / "data" / "last_ingest_status.json",

            _P("mandi_rdd/data/last_ingest_status.json"),

        ]

        for cand in candidates:

            if cand.exists():

                import json as _json

                rec = _json.loads(cand.read_text(encoding="utf-8"))

                return rec.get("last_run_utc") or rec.get("last_run")

    except Exception:

        pass

    return None



# ═══════════════════════════════════════════════════════════

# Top Bar - rendered below after st.navigation()



# ═══════════════════════════════════════════════════════════

# Navigation & Sidebar

# ═══════════════════════════════════════════════════════════



# NOTE: Streamlit 1.59+ calls page render() with no args, and pages read theme

# constants from their own modules. Passing theme_kwargs via partial() caused

# 'render() got an unexpected keyword argument RUST'. Removed.



# components.py requires the theme colors as kwargs; pass them only there.

theme_kwargs = dict(RUST=RUST, TURMERIC=TURMERIC, INK=INK, MUTED=MUTED, PAPER=PAPER)



_all_pages = [

    st.Page(render_overview,

            title="Executive Overview", icon="\U0001f4ca", url_path="", default=True),

    st.Page(render_discontinuity,

            title="Discontinuity Explorer", icon="\U0001f4c8", url_path="discontinuity"),

    st.Page(render_forecast,

            title="Forecast Explorer", icon="\U0001f52e", url_path="forecast"),

    st.Page(render_risk_map,

            title="Risk Map", icon="\U0001f5fa", url_path="risk-map"),

    st.Page(render_satellite,

            title="Satellite View", icon="\U0001f4f0", url_path="satellite"),

    st.Page(render_discount,

            title="Discount Simulator", icon="\U0001f4b0", url_path="discount-simulator"),

    st.Page(render_analyst_lab,

            title="Analyst Lab", icon="\U0001f52c", url_path="analyst-lab"),

    st.Page(render_ask,

            title="Ask MandiIQ", icon="\U0001f4ac", url_path="ask"),

    st.Page(render_settings,

            title="Settings", icon="\u2699", url_path="settings"),

    st.Page(render_about,

            title="About", icon="\u2139", url_path="about"),

]



# Dev-only component gallery

if _HAS_COMPONENTS_PAGE:

    _all_pages.append(

        st.Page(partial(render_components, **theme_kwargs),

                title="Components", icon="\u2699", url_path="components")

    )



pg = st.navigation(_all_pages, position="hidden")

# `pg` now exists, so the route-aware SEO tags can be emitted with the real
# url_path of the page being served.

_inject_page_seo()





# ═══════════════════════════════════════════════════════════

# Top Bar - uses pg.title from st.navigation() for breadcrumb

# ═══════════════════════════════════════════════════════════



_page_label = getattr(pg, 'title', 'Executive Overview')



# ── Hidden Streamlit button for top-bar theme toggle ──

# The top-bar icon's JS clicks this button; its on_click flips surface_mode.

# It is found by its key - Streamlit puts the key on the element container as

# st-key-_topbar_theme_btn, which is also what the CSS hides. Do not go back to

# "the first button in the document": the sidebar renders before the main

# content, so that selector hit the sidebar's "Refresh data now" button and the

# theme icon kicked off an ingest instead of switching the theme.

st.button(

    "",

    key="_topbar_theme_btn",

    on_click=lambda: st.session_state.update(

        surface_mode=not st.session_state.get("surface_mode", False)

    ),

)

# Removed: the inline onclick on the top-bar button handles the toggle directly.

# CSP blocks inline <script>, but inline event handlers (onclick) work fine.



_TOPBAR_HTML = (

    '<div class="mandiq-topbar" role="banner">'

    '<div class="mandiq-topbar-left">'

    '<a href="/" class="mandiq-topbar-logo">' + SVG_LEAF + ' MandiIQ</a>'

    '<span style="color:%(FAINT)s;">/</span>'

    '<span class="mandiq-topbar-breadcrumb"><span class="current">'+str(_page_label)+'</span></span>'

    '</div>'

    '<div class="mandiq-topbar-center">'

    '<span class="model-served" title="Model serving this page\'s data">deepseek/deepseek-chat:free</span>'

    '<a class="theme-toggle-btn" id="theme-toggle-topbar" href="#" title="Toggle surface mode"'

    'onclick="var btn=document.querySelector(\'.st-key-_topbar_theme_btn button\');if(btn)btn.click();return false">'

    '<span id="theme-toggle-icon">' + (SVG_SUN if not _surface_on else SVG_MOON) + '</span>'

    '</a>'

    '</div>'

    '<div class="mandiq-topbar-right">'

    '<a href="/ask" title="Ask MandiIQ" class="mandiq-topbar-icon-link">' + SVG_CHAT + ' Ask</a>'

    '<span style="color:%(FAINT)s;">|</span>'

    '<a href="/settings" title="Settings" class="mandiq-topbar-icon-link">' + SVG_COG + '</a>'

    '</div>'

    '</div>'

) % dict(FAINT=FAINT)



st.html(_TOPBAR_HTML)



# ═══════════════════════════════════════════════════════════
# Live freshness strip
# ═══════════════════════════════════════════════════════════
# Every figure on every page is served by the production API. This strip says
# how old that warehouse actually is - in the same words the API uses - and
# the two fragments below repaint it, and the sidebar's copy of it, on a timer
# so neither is a snapshot somebody had to remember to refresh.

try:
    LIVE_REFRESH_SECONDS = max(0, int(os.environ.get("MANDIIQ_UI_REFRESH_SECONDS", "60")))
except ValueError:
    LIVE_REFRESH_SECONDS = 60


def _behind_wording(behind):
    """Human wording for the signed days-behind value /health reports."""
    if behind is None:
        return "age unknown"
    behind = int(behind)
    if behind < 0:
        return "%d days in the future (impossible)" % -behind
    if behind == 0:
        return "current as of today"
    return "%d day(s) behind" % behind


@st.cache_data(ttl=15, show_spinner=False)
def _live_snapshot() -> dict:
    """Freshness of the warehouse behind every figure on this page."""
    from mandi_rdd.dashboard import data_access as _da
    live = _da.get_health() or {}
    # /health already carries the whole provenance block, so the second request
    # is only made for builds old enough not to have it - one round trip is
    # what stands between a visitor and the first paint.
    has_provenance = live.get("days_behind") is not None or live.get("data_max_date")
    quality = {} if has_provenance else (_da.get_data_quality() or {})
    behind = quality.get("days_behind")
    if behind is None:
        behind = live.get("days_behind")
    # An empty dict is ``get_health``'s failure sentinel: it means nobody
    # answered, not that the warehouse lost its rows. Deriving "empty" from it
    # is what made a restarting API - Northflank answers a restarting service
    # with "no healthy upstream" - read as a warehouse with no price rows at
    # all, dated "unknown". The cockpit has to report the outage it actually
    # saw, and name the address that did not answer.
    health_answered = bool(live)
    quality_answered = bool(quality) and not quality.get("error")
    n_prices = live.get("n_prices")
    if n_prices is None:
        n_prices = quality.get("n_prices")
    status = live.get("status")
    if not status:
        # An older build does not report a status, so derive it the same way the
        # API does instead of assuming everything is fine.
        if not (health_answered or quality_answered):
            status = "unreachable"
        elif n_prices == 0:
            # Only a source that answered and counted zero rows can say this.
            status = "empty"
        elif not n_prices or behind is None:
            # Answered, but published neither a row count nor a date: that is a
            # missing fact, not an empty warehouse.
            status = "unknown"
        elif int(behind) < 0:
            status = "degraded"
        else:
            status = "stale" if int(behind) > 3 else "healthy"
    # A build that cannot date its own data cannot know that it is fresh, so
    # "healthy" from such a payload is not a fact - it is the absence of one.
    # The banner and the sidebar both read this snapshot, so without this the
    # cockpit could say "stale" in the strip and "healthy" in the sidebar at
    # the same moment, depending on which instance happened to answer.
    if status == "healthy" and behind is None and not live.get("data_max_date"):
        status = "unknown"
    return {
        "status": status,
        "max_date": quality.get("max_date") or live.get("data_max_date"),
        "min_date": quality.get("min_date") or live.get("data_min_date"),
        "behind": behind,
        "future": quality.get("n_future_dates", live.get("n_future_dates")) or 0,
        "n_prices": n_prices,
        "n_commodities": live.get("n_commodities"),
        "last_run": live.get("last_run_utc"),
        "last_outcome": live.get("last_outcome"),
        "refresh_runs": live.get("refresh_runs"),
        "refresh_failures": live.get("refresh_failures"),
        "last_refresh_error": live.get("last_refresh_error"),
        "interval_s": live.get("refresh_interval_s"),
        "version": live.get("version"),
        # Which source last served a price fetch, and whether the one mirror
        # that is reachable from cloud networks is armed. Without these two, a
        # stale banner can only say "the data is old" - with them it can say
        # why, which is the difference between a blocker and a known outage.
        "last_price_source": live.get("last_price_source"),
        "mirror_configured": bool(live.get("mirror_configured")),
        # The address behind those figures, so the strip can name it when
        # nothing answers. A cockpit pointed at a retired service is a
        # one-line fix - once the address is on screen.
        "api_base": _da._get_api_base(),
    }


def _request_ingest() -> bool:
    """Ask production to ingest now. The run lock makes this safe to repeat."""
    import requests as _rq
    from mandi_rdd.dashboard import data_access as _da
    try:
        return _rq.post(f"{_da._get_api_base()}/refresh", timeout=25).status_code == 200
    except Exception:
        return False


def _paint_live_strip(live: dict) -> None:
    """The strip above the page body: what the warehouse holds, and how old it is.

    Reading and painting are separate so the strip can repaint itself on a
    timer (see ``_paint_live_strip_tick``) without repainting anything else.
    """
    if live["status"] == "healthy":
        return
    _blurb = {
        "stale": "The newest arrival date is more than three days old, so every figure here is behind reality.",
        "degraded": "The warehouse holds arrival dates that cannot be true, so affected commodities are unreliable.",
        "empty": "The warehouse has no price rows at all.",
        "unknown": "The API did not report how old its data is.",
        "unreachable": "Nothing answered at the production API, so the cockpit cannot see the warehouse at all.",
    }.get(live["status"], "Data freshness is not what it should be.")
    if live["status"] == "unreachable":
        # "data through unknown" is still a claim about the warehouse. The only
        # fact here is that the request went nowhere, so report that, and where.
        _facts = ["no answer from <b>%s</b>" % (live["api_base"] or "the production API")]
    else:
        _facts = ["data through <b>%s</b>" % (live["max_date"] or "unknown"),
                  _behind_wording(live["behind"])]
    if live["n_prices"]:
        _facts.append("{:,} rows".format(int(live["n_prices"])))
    if live["future"]:
        _facts.append("<b>%s impossible date(s)</b>" % live["future"])
    if live["last_refresh_error"]:
        _facts.append("last refresh error: %s" % str(live["last_refresh_error"])[:180])
    if live["last_price_source"]:
        _facts.append("prices last served by %s" % str(live["last_price_source"])[:120])
    elif live["status"] in ("stale", "degraded"):
        # "No rows arrived" and "no source answered" are different problems.
        _facts.append("no price source has answered yet")
    if not live["mirror_configured"] and live["status"] in ("stale", "degraded"):
        # The documented feed is unreachable from cloud networks, so this is
        # the one action that restores the daily update - say it in the app
        # rather than in a deploy note nobody opens.
        _facts.append(
            "api.data.gov.in is unreachable from cloud networks - set "
            "MANDIIQ_CEDA_API_KEY (Agmarknet via CEDA) to restore the daily feed"
        )
    st.html(
        '<style>'
        '.mandiq-live-banner{display:flex;gap:14px;align-items:flex-start;margin:0 0 18px;'
        'padding:13px 18px;border:1px solid rgba(%(RUST_RGB)s,0.45);border-left:3px solid %(RUST)s;'
        'border-radius:10px;background:rgba(%(RUST_RGB)s,0.07);}'
        '.mandiq-live-banner .dot{width:9px;height:9px;border-radius:50%%;background:%(RUST)s;'
        'margin-top:5px;flex:0 0 auto;box-shadow:0 0 0 3px rgba(%(RUST_RGB)s,0.18);}'
        '.mandiq-live-banner .title{font-family:IBM Plex Mono,monospace;font-size:0.72rem;'
        'letter-spacing:0.12em;text-transform:uppercase;color:%(RUST)s;}'
        '.mandiq-live-banner .body{font-family:IBM Plex Mono,monospace;font-size:0.72rem;'
        'line-height:1.65;color:%(PAPER)s;margin-top:5px;}'
        '</style>'
        '<div class="mandiq-live-banner"><div class="dot"></div>'
        '<div><div class="title">Live data: %(STATUS)s</div>'
        '<div class="body">%(BLURB)s %(FACTS)s</div></div></div>'
        % dict(
            RUST=RUST,
            RUST_RGB="%d,%d,%d" % (int(RUST[1:3], 16), int(RUST[3:5], 16), int(RUST[5:7], 16)),
            PAPER=PAPER,
            STATUS=live["status"],
            BLURB=_blurb,
            FACTS=" &middot; ".join(_facts),
        )
    )


def _paint_live_strip_tick() -> None:
    """Repaint the strip in place. This is the whole auto-refresh of the shell.

    It must never call ``st.rerun``. A rerun asked for from a fragment runs the
    shell while the browser still holds the elements of the run it just
    interrupted, and the strip - the first element every run paints - is what
    then ends up on screen twice. Streamlit clears and redraws a fragment's own
    elements on every fragment rerun, which is the one repaint path that cannot
    accumulate: a fragment repaint of the strip can only ever be one strip.

    2.4.1 stacked this same banner by looping the tick; the guard that stopped
    the loop left the timer, so a settled page could still collect a second
    banner - and a first one behind it.
    """
    _paint_live_strip(_live_snapshot())


def _paint_sidebar_live(live: dict) -> None:
    """The sidebar's copy of the strip: the same snapshot, in the same words.

    Both surfaces read ``_live_snapshot``, so the two halves of the cockpit
    cannot disagree about the warehouse behind them.
    """
    st.html('<div class="sidebar-section-header">Live data</div>')
    _accent = SAGE if live["status"] == "healthy" else RUST
    _sidebar_rows = ("%s rows" % format(int(live["n_prices"]), ",")) \
        if live["n_prices"] else "row count unknown"
    _lines = [
        '<span style="color:%s;">&#9679;</span> %s' % (_accent, live["status"]),
        'through <span style="color:%s;">%s</span>' % (PAPER, live["max_date"] or "unknown"),
        _behind_wording(live["behind"]) + " &middot; " + _sidebar_rows,
    ]
    if live["future"]:
        _lines.append("%s impossible date(s)" % live["future"])
    if live["n_commodities"]:
        _lines.append("%s commodities" % format(int(live["n_commodities"]), ","))
    if live["last_run"]:
        _lines.append("ingest %s &middot; %s" % (
            str(live["last_run"])[:16].replace("T", " "),
            live["last_outcome"] or "unknown",
        ))
    if live["refresh_runs"] is not None:
        _lines.append("self-refresh %d/%d ok" % (
            int(live["refresh_runs"]) - int(live["refresh_failures"] or 0),
            int(live["refresh_runs"]),
        ))
    if live["last_refresh_error"]:
        _lines.append("error: " + str(live["last_refresh_error"])[:140])
    if live["version"]:
        _lines.append("build " + str(live["version"]))
    if live["status"] == "unreachable" and live["api_base"]:
        _lines.append("no answer from " + str(live["api_base"]))
    # A repaint that finds the same /health payload looks like no repaint at
    # all, so the block dates itself. That is the difference between "the
    # cockpit stopped refreshing" and "the warehouse has nothing new to say".
    _lines.append("checked " + time.strftime("%H:%M", time.gmtime()) + " UTC")
    st.html(
        '<div style="padding:0 1rem 0.4rem;font-family:IBM Plex Mono,monospace;'
        'font-size:0.68rem;line-height:1.7;color:' + MUTED + ';">'
        + "<br>".join(_lines) + '</div>'
    )


def _paint_sidebar_live_tick() -> None:
    """Repaint the sidebar block in place, off the strip's own snapshot."""
    _paint_sidebar_live(_live_snapshot())


# The auto-refresh: one fragment per freshness surface, each repainting its own
# elements in place on the timer. Fragments are the only repaint path Streamlit
# guarantees cannot accumulate, and this shell asks for no whole-app rerun at
# all - see _paint_live_strip_tick for what a rerun asked from a fragment costs.
# With "Live updates" switched off, both surfaces are painted once, statically.
if LIVE_REFRESH_SECONDS and st.session_state.get("live_auto_refresh", True):
    _strip_tick = st.fragment(run_every=LIVE_REFRESH_SECONDS)(_paint_live_strip_tick)
    _strip_tick()
else:
    _paint_live_strip_tick()



# Build custom sidebar

health = _model_health_status()

health_labels = {"green": "All healthy", "amber": "Degraded", "red": "Unavailable"}



with st.sidebar:

    # Navigation links

    for p in _all_pages:

        st.page_link(p, label=p.title, icon=p.icon)



    st.markdown("---")



    # Commodity legend

    st.html(

        f'<div class="sidebar-section-header">Commodities</div>',

    )

    for name, color in COMMODITY_COLORS.items():

        st.html(

            f'<div class="legend-item"><span class="legend-dot" style="background:{color};"></span>'

            f'<span>{name}</span></div>',

        )



    # ── Surface mode toggle ──

    st.html(

        f'<div style="padding:0.5rem 1rem 0.25rem;font-size:0.7rem;color:{FAINT};'

        f'font-family:IBM Plex Mono,monospace;text-transform:uppercase;letter-spacing:0.05em;">'

        f'Theme</div>',

    )

    st.html(

        '<div class="theme-toggle-visual" style="display:flex;align-items:center;gap:6px;margin-bottom:2px;">'

        '<span style="display:flex;color:' + MUTED + ';">' + (SVG_SUN if not _surface_on else SVG_MOON) + '</span>'

        '<span style="font-size:0.85rem;color:' + MUTED + ';">Lighter surface</span></div>',

    )

    st.toggle(

        "Toggle surface mode",

        key="surface_mode",

        help="Swap pure-black background (#000000) for a dark-gray surface (#111111) for daytime readability.",

        label_visibility="collapsed",

    )



    # ── Live data provenance ──
    # Every figure in this cockpit is only as good as the warehouse behind it,
    # so its age is stated in the same words the API uses - and, like the
    # strip, it repaints itself on a timer rather than waiting for someone to
    # press R. The button and the toggle below stay outside the fragment: a
    # widget owned by a timer fragment is a widget that reruns by itself.
    if LIVE_REFRESH_SECONDS and st.session_state.get("live_auto_refresh", True):
        _sidebar_tick = st.fragment(run_every=LIVE_REFRESH_SECONDS)(_paint_sidebar_live_tick)
        _sidebar_tick()
    else:
        _paint_sidebar_live_tick()

    if st.button("Refresh data now", key="_sidebar_ingest", width="stretch"):
        if _request_ingest():
            _live_snapshot.clear()
            st.toast("Ingestion started on the server")
        else:
            st.toast("Could not reach the API")

    if LIVE_REFRESH_SECONDS:
        st.toggle(
            "Live updates",
            value=True,
            key="live_auto_refresh",
            help=("Repaint the freshness strip and the sidebar every %d seconds."
                  % LIVE_REFRESH_SECONDS),
        )



    # Spacer

    st.html("<div style='min-height: 30px;'></div>")



    # Model-health dot at bottom

    st.html(

        f'<div style="display:flex;align-items:center;padding:0.5rem 1rem;'

        f'font-size:0.7rem;color:{MUTED};font-family:IBM Plex Mono,monospace;'

        f'border-top:1px solid rgba({int(SLATE[1:3],16)},{int(SLATE[3:5],16)},{int(SLATE[5:7],16)},0.3);">'

        f'<span class="health-dot {health}"></span>'

        f'{health_labels.get(health, "Unknown")}'

        f'</div>',

    )



# Run the current page

try:

    pg.run()

except Exception as _exc:  # surface real error instead of redacted box

    import traceback as _tb

    _msg = "".join(_tb.format_exception(type(_exc), _exc, _exc.__traceback__))

    try:

        with open("/mount/src/mandiiq/app_error.log", "w", encoding="utf-8") as _f:

            _f.write(_msg)

    except Exception:

        pass

    st.exception(_exc)



# Footer

# ═══════════════════════════════════════════════════════════



pipeline_ts = _latest_pipeline_run()

ts_str = (

    f'<span class="mono">{pipeline_ts}</span>'

    if pipeline_ts

    else f'<span class="mono" style="color:{RUST};">No pipeline run recorded</span>'

)



st.html(f"""

<div style="font-size:0.75rem;color:#9e9e9e;padding:1rem 0;text-align:center;border-top:1px solid #333;">

    <div>

        <a href="/methodology" target="_blank" style="color:#9e9e9e;text-decoration:none;">Methodology</a>

        <span style="margin:0 8px;color:{FAINT};">·</span>

        <a href="https://data.gov.in/" target="_blank" style="color:#9e9e9e;text-decoration:none;">data.gov.in/Agmarknet</a>

        <span style="margin:0 8px;color:{FAINT};">·</span>

        <a href="https://mausam.imd.gov.in/" target="_blank" style="color:#9e9e9e;text-decoration:none;">IMD</a>

        <span style="margin:0 8px;color:{FAINT};">·</span>

        <a href="https://sentinel.esa.int/" target="_blank" style="color:#9e9e9e;text-decoration:none;">Sentinel-2</a>

    </div>

    <div>

        Last pipeline run: {ts_str}

    </div>

</div>

""")

