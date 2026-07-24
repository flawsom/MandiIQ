"""
MandiIQ — Shared design-system theme (Layer 2 of the design system).

Single source of truth for all injected CSS consumed by the dashboard pages.
Every page calls inject_theme() once at the top of its render() function.

Visual Aesthetic: Stark, immersive monochrome with chartreuse/lime highlights
inspired by Alche Studio (alche.studio).
"""

from pathlib import Path
import os
import streamlit as st

# ── Resilient API base resolution ──
_DEFAULT_API_BASE = "http://localhost:8000"  # override via MANDIIQ_API_URL env var

def get_api_base() -> str:
    """Resolve the FastAPI base URL."""
    candidates = ["MANDIQ_API_URL", "MANDIIQ_API_URL"]
    try:
        for key in candidates:
            val = st.secrets.get(key)
            if val:
                return str(val)
        for section in st.secrets:
            try:
                blob = st.secrets[section]
            except Exception:
                continue
            if not isinstance(blob, dict):
                continue
            for key in candidates:
                if key in blob and blob[key]:
                    return str(blob[key])
    except Exception:
        pass

    for env_key in candidates:
        val = os.environ.get(env_key)
        if val:
            return val

    return _DEFAULT_API_BASE

# ── Resolve paths relative to this file ──
_THEME_DIR = Path(__file__).resolve().parent
_DESIGN_CSS = Path(__file__).resolve().parent.parent / "styles" / "design.css"

# ── Commodity color lookup ──
COMMODITY_COLORS = {
    "Onion":  "#8B6BC4",
    "Tomato": "#D9663B",
    "Wheat":  "#D4A94E",
    "Potato": "#B98354",
}

# Palette shorthand (Alche Studio aesthetic: monochrome-lime)
INK      = "#000000"      # Alche Pure Black
SLATE    = "#111111"      # Alche Dark Charcoal Card
PAPER    = "#FFFFFF"      # Stark White Text
MUTED    = "#bababa"      # High Muted Grey
FAINT    = "#7e7e7e"      # Medium Muted Grey
TURMERIC = "#d7ff00"      # Alche Lime Accent
RUST     = "#D9663B"      # Deficit Alert
SAGE     = "#8FAE89"      # healthy NDVI

def inject_theme():
    """Inject the full Layer 2 stylesheet into the Streamlit page.

    Only injects once per session — subsequent calls are no-ops.
    Call once at the top of each page render for safety; the gate
    prevents duplicate ~35KB CSS injections.
    """
    if st.session_state.get("_mandiiq_theme_injected"):
        return
    st.session_state._mandiiq_theme_injected = True

    token_css = ""
    if _DESIGN_CSS.exists():
        token_css = _DESIGN_CSS.read_text(encoding="utf-8")

    st.markdown(
        f"""<style>
/* ── Google Font imports ── */
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600&family=Space+Grotesk:wght@300;400;500;600;700&family=Barlow:wght@300;400;500;600;700&family=Inter:wght@300;400;500;600&display=swap');

/* ── Design tokens from design.css ── */
{token_css}

/* ── Typography overrides ── */
h1, h2, h3, h4, h5, h6 {{
    font-family: var(--font-display, "Space Grotesk", system-ui, sans-serif) !important;
    color: var(--color-paper, #ffffff) !important;
    font-weight: 400 !important;
    letter-spacing: 0.08em !important;
}}

.stApp p, .stApp span, .stApp li, .stApp td, .stApp th,
.stApp label, .stApp div[data-testid="stMetricLabel"],
.stApp div[data-testid="stSidebar"] p,
.stApp div[data-testid="stSidebar"] span {{
    font-family: var(--font-body, "IBM Plex Sans", system-ui, sans-serif) !important;
}}

div[data-testid="stMetricValue"] {{
    font-family: var(--font-numeric, "Barlow", "IBM Plex Mono", monospace) !important;
    color: var(--color-primary, #d7ff00) !important;
    font-weight: 500 !important;
    font-variant-numeric: tabular-nums !important;
    letter-spacing: 0.02em !important;
}}

/* Heading scale */
h1 {{ font-size: 1.6rem !important; font-weight: 500 !important; text-transform: uppercase; }}
h2 {{ font-size: 1.15rem !important; font-weight: 500 !important;
      border-bottom: 1px solid var(--hairline-strong, rgba(255,255,255,0.15));
      padding-bottom: 0.4rem; margin-bottom: 1rem; text-transform: uppercase; }}
h3 {{ font-size: 0.82rem !important; font-weight: 500 !important;
      color: var(--color-muted, #bababa) !important;
      text-transform: uppercase; letter-spacing: 0.1em; }}

/* ── Tab reskin ── */
.stTabs [data-baseweb="tab-list"] {{
    gap: 0;
    background: rgba(255,255,255,0.02);
    border-radius: 4px;
    padding: 3px;
    border: 1px solid var(--hairline);
}}
.stTabs [data-baseweb="tab"] {{
    border-radius: 2px;
    padding: 0.5rem 1.1rem;
    transition: all 0.2s ease-out;
    font-weight: 400;
    font-family: var(--font-body, "IBM Plex Sans", system-ui, sans-serif);
    color: var(--color-muted);
    font-size: 0.85rem;
}}
.stTabs [data-baseweb="tab"][aria-selected="true"] {{
    background: var(--color-primary, #d7ff00) !important;
    color: var(--color-ink, #000000) !important;
    font-weight: 600 !important;
}}
.stTabs [data-baseweb="tab"]:hover:not([aria-selected="true"]) {{
    color: var(--color-paper);
    background: rgba(255,255,255,0.04);
}}

/* ── Button reskin ── */
.stButton > button[kind="primary"],
.stButton > button {{
    background: transparent !important;
    color: var(--color-paper, #ffffff) !important;
    font-weight: 500 !important;
    border: 1px solid var(--hairline) !important;
    border-radius: 999px !important;
    padding: 0.5rem 1.5rem !important;
    transition: all 0.3s cubic-bezier(0.25, 0.46, 0.45, 0.94) !important;
}}
.stButton > button:hover {{
    background: var(--color-primary, #d7ff00) !important;
    color: var(--color-ink, #000000) !important;
    border-color: var(--color-primary, #d7ff00) !important;
}}

/* ── Sidebar adjustments ── */
div[data-testid="stSidebar"] {{
    background: var(--color-ink, #000000) !important;
    border-right: 1px solid var(--hairline) !important;
}}
div[data-testid="stSidebar"] h1,
div[data-testid="stSidebar"] h2 {{
    font-family: var(--font-display, "Space Grotesk", sans-serif) !important;
    color: var(--color-paper, #ffffff) !important;
}}

/* ── Interpretation boxes ── */
.interpretation-box {{
    background: rgba(255, 255, 255, 0.02);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-left: 3px solid var(--color-primary, #d7ff00);
    border-radius: 4px;
    padding: 1rem 1.2rem;
    margin: 1rem 0;
    font-size: 0.92rem;
    line-height: 1.6;
    color: var(--color-muted, #bababa);
}}

.insig-box {{
    border-color: rgba(255, 255, 255, 0.08);
    border-left-color: var(--color-faint, #7e7e7e);
}}

*:focus-visible {{
    outline: 2px solid var(--color-primary, #d7ff00) !important;
    outline-offset: 2px !important;
}}

@media (prefers-reduced-motion: reduce) {{
    .atmosphere-flash,
    .atmosphere-cloud {{
        animation: none !important;
    }}
}}

/* Suppress Streamlit branding */
footer {{ display: none; }}

/* Thinner Sidebar on Desktop, Responsive overrides */
@media (max-width: 1024px) {{
    div[data-testid="stSidebar"] {{
        width: 200px !important;
        min-width: 180px !important;
    }}
}}

/* ═══ RESPONSIVE OVERRIDES ═══ */

/* Touch-friendly targets */
@media (hover: none) and (pointer: coarse) {{
    .mandiq-btn, .mandiq-btn-primary, .mandiq-btn-secondary,
    .mandiq-btn-ghost, .mandiq-btn-danger,
    button, .stButton button {{
        min-height: 44px;
    }}
    select, input, textarea, .stSelectbox, .stMultiSelect {{
        font-size: 16px !important;
    }}
}}

/* Mobile (< 640px) */
@media screen and (max-width: 640px) {{
    h1, .stTitle h1 {{ font-size: 1.4rem !important; }}
    h2, .stSubHeader h2 {{ font-size: 1.15rem !important; }}
    h3 {{ font-size: 1rem !important; }}
    p, li, .stMarkdown p {{ font-size: 0.9rem !important; }}

    .stButton button {{ width: 100%; }}

    div[data-testid="metric-container"] {{ padding: 0.4rem !important; }}
    div[data-testid="metric-container"] label {{ font-size: 0.7rem !important; }}
    div[data-testid="metric-container"] div[data-testid="metric-value"] {{ font-size: 1rem !important; }}

    section[data-testid="stSidebar"] .stMarkdown {{ font-size: 0.85rem !important; }}

    .stTabs [data-baseweb="tab-list"] {{ flex-wrap: wrap !important; gap: 0.25rem !important; }}
    .stTabs [data-baseweb="tab"] {{ font-size: 0.8rem !important; padding: 0.4rem 0.6rem !important; }}

    .row-widget.stHorizontal {{ flex-direction: column !important; }}
    .row-widget.stHorizontal > div {{
        width: 100% !important;
        flex: 0 0 100% !important;
        min-width: 0 !important;
    }}
}}

/* Tablet (641px – 1024px) */
@media screen and (min-width: 641px) and (max-width: 1024px) {{
    h1 {{ font-size: 1.6rem !important; }}
    h2 {{ font-size: 1.3rem !important; }}

    .stTabs [data-baseweb="tab"] {{ font-size: 0.85rem !important; padding: 0.5rem 0.8rem !important; }}

    .row-widget.stHorizontal > div {{ min-width: 0 !important; }}
}}

/* Print */
@media print {{
    .stApp header, section[data-testid="stSidebar"],
    .stButton, button, .mandiq-toast-container,
    .mandiq-modal-overlay {{ display: none !important; }}
    .main .block-container {{ max-width: 100% !important; padding: 0 !important; }}
}}



/* ═══ ANIMATIONS ═══ */

@keyframes fadeInUp {{
    from {{ opacity: 0; transform: translateY(12px); }}
    to   {{ opacity: 1; transform: translateY(0); }}
}}

@keyframes pulseGlow {{
    0%, 100% {{ box-shadow: 0 0 4px rgba(232, 177, 77, 0.2); }}
    50%      {{ box-shadow: 0 0 12px rgba(232, 177, 77, 0.5); }}
}}

@keyframes shimmer {{
    0%   {{ background-position: -200% 0; }}
    100% {{ background-position: 200% 0; }}
}}

@keyframes floatCard {{
    0%, 100% {{ transform: translateY(0); }}
    50%      {{ transform: translateY(-3px); }}
}}

@keyframes statusPulse {{
    0%, 100% {{ opacity: 1; }}
    50%      {{ opacity: 0.4; }}
}}

.animate-fade-in  {{ animation: fadeInUp 0.4s ease-out both; }}
.animate-pulse    {{ animation: pulseGlow 2s ease-in-out infinite; }}
.animate-shimmer  {{ background: linear-gradient(90deg, rgba(255,255,255,0.04) 25%, rgba(255,255,255,0.08) 50%, rgba(255,255,255,0.04) 75%); background-size: 200% 100%; animation: shimmer 1.5s infinite; }}
.animate-float    {{ animation: floatCard 4s ease-in-out infinite; }}
.animate-status   {{ animation: statusPulse 2s ease-in-out infinite; }}

@media (prefers-reduced-motion: reduce) {{
    *, *::before, *::after {{
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.01ms !important;
    }}
}}

.mandiq-kpi.live {{
    animation: pulseGlow 2s ease-in-out infinite;
    border-color: rgba(232, 177, 77, 0.3);
}}

.status-dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 4px; }}
.status-dot.green  {{ background: #6BBF8A; }}
.status-dot.amber  {{ background: #E8B14D; }}
.status-dot.red    {{ background: #C84B4B; }}

</style>""",
        unsafe_allow_html=True,
    )

def inject_atmosphere():
    """Inject the atmosphere layer (drifting blobs + dot grid)."""
    st.markdown(
        """<div class="atmosphere" aria-hidden="true">
  <div class="atmosphere-flash"></div>
  <div class="atmosphere-cloud"></div>
  <div class="atmosphere-drifter" style="--x:15%;--y:20%;--s:180px;--d:25s;--hue:100;--op:0.06"></div>
  <div class="atmosphere-drifter" style="--x:75%;--y:30%;--s:140px;--d:35s;--hue:80;--op:0.05"></div>
  <div class="atmosphere-drifter" style="--x:50%;--y:70%;--s:200px;--d:40s;--hue:60;--sat:0.9;--lit:0.6;--op:0.04"></div>
  <div class="atmosphere-drifter" style="--x:8%;--y:65%;--s:100px;--d:30s;--op:0.03"></div>
  <div class="atmosphere-drifter" style="--x:88%;--y:12%;--s:130px;--d:45s;--hue:120;--op:0.035"></div>
</div>
<div class="dot-grid" aria-hidden="true"></div>
<script>
(function(){
  if (window.__mandiiqReveal) return;
  window.__mandiiqReveal = true;
  function init(){
    var sel = '.reveal, .page-hero, .stPlotlyChart, .kpi-grid, .metric-container, .glass, .flip-board-root';
    var els = document.querySelectorAll(sel);
    if (!('IntersectionObserver' in window) || !els.length){ return; }
    els.forEach(function(el){
      if (!el.classList.contains('is-visible')) el.classList.add('reveal');
    });
    var io = new IntersectionObserver(function(entries){
      entries.forEach(function(e){
        if (e.isIntersecting){ e.target.classList.add('is-visible'); io.unobserve(e.target); }
      });
    }, { threshold: 0.08, rootMargin: '0px 0px -8% 0px' });
    els.forEach(function(el){ io.observe(el); });
  }
  if (document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', function(){ setTimeout(init, 120); });
  } else { setTimeout(init, 120); }
  document.addEventListener('streamlit:render', function(){ setTimeout(init, 200); });
})();
</script>""",
        unsafe_allow_html=True,
    )

def commodity_color(commodity: str) -> str:
    """Return the hex color for a commodity name (case-insensitive)."""
    return COMMODITY_COLORS.get(commodity.title(), MUTED)

def render_ledger_table(df, columns=None, commodity_col=None, highlight_col=None):
    """Render a pandas DataFrame as an HTML ledger table with commodity chips."""
    if df is None or len(df) == 0:
        return

    cols = columns or list(df.columns)
    ccol = commodity_col or highlight_col

    # Build header
    ths = "".join(f"<th>{c.replace('_', ' ').upper()}</th>" for c in cols)
    html = f'<table class="ledger-table"><thead><tr>{ths}</tr></thead><tbody>'

    for _, row in df[cols].iterrows():
        html += "<tr>"
        for c in cols:
            val = row[c]
            if c == ccol and ccol in COMMODITY_COLORS:
                color = COMMODITY_COLORS.get(str(val).title(), "#bababa")
                html += (
                    f'<td><span style="display:inline-flex;align-items:center;gap:6px;">'
                    f'<span style="width:8px;height:8px;border-radius:2px;'
                    f'background:{color};flex-shrink:0;"></span>'
                    f'{val}</span></td>'
                )
            else:
                if isinstance(val, float):
                    if abs(val) < 0.01:
                        html += f"<td>{val:.4f}</td>"
                    elif abs(val) < 100:
                        html += f"<td>{val:.2f}</td>"
                    else:
                        html += f"<td>{val:,.0f}</td>"
                else:
                    html += f"<td>{val}</td>"
        html += "</tr>"

    html += "</tbody></table>"
    st.markdown(html, unsafe_allow_html=True)
