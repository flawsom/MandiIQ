"""

MandiIQ — Route-based Navigation Dashboard with Global Shell



Entry point for Streamlit. Uses st.navigation() for proper URL routing,

deep linking, and error page handling — replaces the old 5-tab layout.



Sitemap (14 routes):

  /  /discontinuity  /forecast  /risk-map  /satellite

  /discount-simulator  /ask  /settings  /about

  /onboarding  /loading  /404  /error/model-unavailable  /error/no-data



Global Shell:

  - Sidebar (expanded/collapsed/hidden responsive)

  - Top bar (breadcrumb, Ask MandiIQ, settings, model-served)

  - Footer (pipeline timestamp, data attribution, methodology link)

  - Model-health dot (green/amber/red) in sidebar



Design: turmeric/ink/slate palette, design.css token system.

See mandi_rdd/styles/design.css and mandi_rdd/dashboard/theme.py

"""



import sys

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))



from functools import partial



import streamlit as st



from mandi_rdd.dashboard.theme import (

    inject_theme, inject_atmosphere,

    INK, SLATE, PAPER, MUTED, FAINT, TURMERIC, RUST, SAGE,

)



# ═══════════════════════════════════════════════════════════

# SVG Icons — from shared icon library

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



# Orphan pages — previously unregistered in the nav

from mandi_rdd.dashboard.pages.deep_dive import render as render_deep_dive

from mandi_rdd.dashboard.pages.causal_explorer import render as render_causal_explorer

from mandi_rdd.dashboard.pages.risk_forecast import render as render_risk_forecast

from mandi_rdd.dashboard.pages.procurement_advisor import render as render_procurement_advisor



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

try:

    from mandi_rdd.dashboard.seo import inject_page_seo

    st.markdown(inject_page_seo(pg.url_path), unsafe_allow_html=True)

except Exception:

    # Absolute fallback: a minimal canonical link only.

    st.markdown(

        '<link rel="canonical" href="https://mandiiq.unifies.codes/" />',

        unsafe_allow_html=True,

    )



# ═══════════════════════════════════════════════════════════

# Inject design system

# ═══════════════════════════════════════════════════════════



inject_theme()

inject_atmosphere()



# ── Server-side: initialize surface_mode from URL query param ──

# Reads ?surface=true/false from the URL, set by JavaScript via

# history.replaceState on the previous visit. This eliminates the

# flash-of-wrong-theme — the correct CSS is served on the very first

# render instead of relying on a client-side restore + second rerun.

_surface_param = st.query_params.get_all("surface")

if _surface_param and "surface_mode" not in st.session_state:

    st.session_state.surface_mode = _surface_param[0] == "true"



# ── Surface-mode: swap pure black for dark gray ──

_surface_on = st.session_state.get("surface_mode", False)

if _surface_on:

    st.markdown(

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

        unsafe_allow_html=True,

    )

# ── Persist surface mode via localStorage + URL query param ──

# JavaScript saves the preference to TWO places on every render:

#   1. localStorage — for JavaScript-based reading

#   2. URL query param (?surface=true/false) — for server-side init on next visit

# The server reads the query param above to serve the correct CSS on first render,

# eliminating the flash-of-wrong-theme that a pure-JS restore would cause.

_js_val = "true" if _surface_on else "false"

st.markdown(

    f"""<script>

(function(){{

    // 1. Always sync the body class — never blocked by localStorage

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

    unsafe_allow_html=True,

)



# ═══════════════════════════════════════════════════════════

# Global CSS

# ═══════════════════════════════════════════════════════════



COMMODITY_COLORS = {

    "Onion": "#8B6BC4", "Tomato": "{RUST}",

    "Wheat": "#D4A94E", "Potato": "#B98354",

}



st.markdown(f"""

<style>

/* ── Top Bar ── */

.mandiq-topbar {{

    position: fixed; top: 0; left: 0; right: 0;

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

.mandiq-topbar-spacer {{ height: 56px; }}



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

/* Target the first Streamlit button container (our hidden toggle).

   Streamlit renders it before all other buttons, so :first-of-type

   reliably selects it. */

.stButton:first-of-type {{

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

/* Desktop: expanded sidebar (>=1024px) — default */

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

""", unsafe_allow_html=True)



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

    # Phase 10: last_ingest_status.json is the single source of truth,

    # written by run_nightly. The pipeline_log table is not populated,

    # so read the JSON file from the package data dir.

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

# Top Bar — rendered below after st.navigation()



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





# ═══════════════════════════════════════════════════════════

# Top Bar — uses pg.title from st.navigation() for breadcrumb

# ═══════════════════════════════════════════════════════════



_page_label = getattr(pg, 'title', 'Executive Overview')



# ── Hidden Streamlit button for top-bar theme toggle ──

# The JS in the top-bar icon finds this hidden button and clicks it.

# We identify it by its empty label text (no other Streamlit button

# uses empty text). The on_click lambda flips the surface_mode state.

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

    'onclick="var btn=document.querySelector(\'[data-testid=stButton] button\');if(btn)btn.click();return false">'

    '<span id="theme-toggle-icon">' + (SVG_SUN if not _surface_on else SVG_MOON) + '</span>'

    '</a>'

    '</div>'

    '<div class="mandiq-topbar-right">'

    '<a href="/ask" title="Ask MandiIQ" class="mandiq-topbar-icon-link">' + SVG_CHAT + ' Ask</a>'

    '<span style="color:%(FAINT)s;">|</span>'

    '<a href="/settings" title="Settings" class="mandiq-topbar-icon-link">' + SVG_COG + '</a>'

    '</div>'

    '</div>'

    '<div class="mandiq-topbar-spacer"></div>'

) % dict(FAINT=FAINT)



st.markdown(_TOPBAR_HTML, unsafe_allow_html=True)



# Build custom sidebar

health = _model_health_status()

health_labels = {"green": "All healthy", "amber": "Degraded", "red": "Unavailable"}



with st.sidebar:

    # Navigation links

    for p in _all_pages:

        st.page_link(p, label=p.title, icon=p.icon)



    st.markdown("---")



    # Commodity legend

    st.markdown(

        f'<div class="sidebar-section-header">Commodities</div>',

        unsafe_allow_html=True,

    )

    for name, color in COMMODITY_COLORS.items():

        st.markdown(

            f'<div class="legend-item"><span class="legend-dot" style="background:{color};"></span>'

            f'<span>{name}</span></div>',

            unsafe_allow_html=True,

        )



    # ── Surface mode toggle ──

    st.markdown(

        f'<div style="padding:0.5rem 1rem 0.25rem;font-size:0.7rem;color:{FAINT};'

        f'font-family:IBM Plex Mono,monospace;text-transform:uppercase;letter-spacing:0.05em;">'

        f'Theme</div>',

        unsafe_allow_html=True,

    )

    st.markdown(

        '<div class="theme-toggle-visual" style="display:flex;align-items:center;gap:6px;margin-bottom:2px;">'

        '<span style="display:flex;color:' + MUTED + ';">' + (SVG_SUN if not _surface_on else SVG_MOON) + '</span>'

        '<span style="font-size:0.85rem;color:' + MUTED + ';">Lighter surface</span></div>',

        unsafe_allow_html=True,

    )

    st.toggle(

        "Toggle surface mode",

        key="surface_mode",

        help="Swap pure-black background (#000000) for a dark-gray surface (#111111) for daytime readability.",

        label_visibility="collapsed",

    )



    # Spacer

    st.markdown("<div style='min-height: 30px;'></div>", unsafe_allow_html=True)



    # Model-health dot at bottom

    st.markdown(

        f'<div style="display:flex;align-items:center;padding:0.5rem 1rem;'

        f'font-size:0.7rem;color:{MUTED};font-family:IBM Plex Mono,monospace;'

        f'border-top:1px solid rgba({int(SLATE[1:3],16)},{int(SLATE[3:5],16)},{int(SLATE[5:7],16)},0.3);">'

        f'<span class="health-dot {health}"></span>'

        f'{health_labels.get(health, "Unknown")}'

        f'</div>',

        unsafe_allow_html=True,

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

