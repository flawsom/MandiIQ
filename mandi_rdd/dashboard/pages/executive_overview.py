"""
MandiIQ — Executive Overview page.

Headline finding, KPI panel, price trend by district/commodity.
Includes "Ask MandiIQ" AI chat panel (Phase 11 — OpenRouter multi-model routing).

Alche Studio Design: glass cards, interpretation boxes, crosshair panels,
section labels, and consistent monochrome-lime palette.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import os
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from mandi_rdd.dashboard.theme import inject_theme, commodity_color, get_api_base, INK, SLATE, PAPER, MUTED, FAINT, TURMERIC, RUST, SAGE
from mandi_rdd.dashboard.flip_board import flip_board
from mandi_rdd.dashboard.plotly_theme import make_themed_figure
from mandi_rdd.storage.duckdb_store import (
    get_connection, get_latest_rdd, get_prices, get_distinct_commodities,
    get_avg_price_and_districts, get_latest_forecast_metrics,
)
from mandi_rdd.analysis.forecast import train_forecast


# ── AI Chat API destination ──
API_BASE = get_api_base()


# ── Cached data loaders ──
@st.cache_data(ttl=300, show_spinner=False)
def _cached_prices(limit: int = 5):
    try:
        conn = get_connection(read_only=True)
        try:
            return get_prices(conn, limit=limit)
        finally:
            conn.close()
    except Exception:
        return None


@st.cache_data(ttl=300, show_spinner=False)
def _cached_rdd(commodity: str):
    try:
        conn = get_connection(read_only=True)
        try:
            return get_latest_rdd(conn, commodity)
        finally:
            conn.close()
    except Exception:
        return None


@st.cache_data(ttl=300, show_spinner=False)
def _cached_avg_price(commodity: str):
    try:
        conn = get_connection(read_only=True)
        try:
            avg, ndist = get_avg_price_and_districts(conn, commodity)
        finally:
            conn.close()
        return avg, ndist
    except Exception:
        return None, None


@st.cache_data(ttl=600, show_spinner=False)
def _cached_forecast_mape(commodity: str, h: int = 12):
    try:
        conn = get_connection(read_only=True)
        try:
            stored = get_latest_forecast_metrics(conn, commodity)
            if stored and stored.get("test_mape") is not None:
                return float(stored["test_mape"])
            fc = train_forecast(conn, commodity=commodity, periods=h)
            if fc and fc.get("metrics") and fc["metrics"].get("mape") is not None:
                return float(fc["metrics"]["mape"])
        finally:
            conn.close()
    except Exception:
        return None
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_all_india_monsoon():
    try:
        from mandi_rdd.ingestion.fetch_rainfall import fetch_all_india_monsoon
        rid = ""
        api_key = ""
        try:
            rid = st.secrets.get("ALL_INDIA_RAINFALL_RESOURCE_ID", "")
        except Exception:
            pass
        if not rid:
            rid = os.environ.get("ALL_INDIA_RAINFALL_RESOURCE_ID", "")
        try:
            api_key = st.secrets.get("ALL_INDIA_RAINFALL_API_KEY", "")
        except Exception:
            pass
        if not api_key:
            api_key = os.environ.get("ALL_INDIA_RAINFALL_API_KEY", "")
        if rid and api_key:
            return fetch_all_india_monsoon(rid, api_key)
    except Exception:
        pass
    return []


def render(**kwargs):
    # Streamlit 1.59 calls render() with no args; compute data internally.
    selected_commodity = "Onion"
    try:
        _rows = _cached_prices(limit=5)
        data_summary = {"rows": _rows, "count": len(_rows)} if _rows is not None else {"rows": [], "count": 0}
    except Exception:
        data_summary = {"rows": [], "count": 0}
    try:
        rdd_result = _cached_rdd(selected_commodity) or {}
    except Exception:
        rdd_result = {}
    inject_theme()

    # ── Hero Header ──
    st.markdown(
        """
        <div class="page-hero" style="margin-bottom: 2rem;">
          <div>
            <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
              Operational Dashboard
            </div>
            <h1 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:300;font-size:clamp(1.8rem,3.5vw,2.8rem);color:#ffffff;letter-spacing:0.03em;text-transform:uppercase;margin-bottom:0.5rem;">
              Executive <span style="font-weight:600;color:#d7ff00;">Overview</span>
            </h1>
            <p style="color:#7e7e7e;max-width:680px;line-height:1.7;font-size:0.95rem;">
              Real-time RDD causal estimate, live market KPIs, and the AI procurement chat.
            </p>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Phase 11: Ask MandiIQ AI Chat Panel ──
    _render_ask_panel(selected_commodity)

    # ── Headline finding (interpretation box) ──
    effect = rdd_result.get("effect")
    p_val = rdd_result.get("p_value")
    if effect is not None:
        sig = "ROBUST" if (p_val is not None and p_val < 0.05) else "exploratory"
        st.markdown(
            f"""
            <div class="interpretation-box">
                <strong style="color:#d7ff00;">⬡ {sig}:</strong> Crossing the −19% rainfall deficiency threshold is associated
                with a <strong>₹{effect:.2f}</strong> change in {selected_commodity} modal prices
                (p={'{:.4f}'.format(p_val) if p_val else 'N/A'}).
                Fixed-effects cross-check: <span style="font-family:'IBM Plex Mono',monospace;">₹{rdd_result.get('fe_effect', 'N/A')}</span>.
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="interpretation-box insig-box">Run the pipeline to see RDD results for Onion.</div>',
            unsafe_allow_html=True,
        )

    # ── KPI row — flip-board hero ──
    try:
        import math

        avg_price, n_districts = _cached_avg_price(selected_commodity)
        spike_n = int(n_districts) if n_districts else None
        _mape = _cached_forecast_mape(selected_commodity)

        def is_valid_num(x):
            if x is None:
                return False
            try:
                return math.isfinite(float(x))
            except (TypeError, ValueError):
                return False

        flip_board(
            effect=(f"{effect:,.0f}" if is_valid_num(effect) else "—"),
            effect_raw=(float(effect) if is_valid_num(effect) else None),
            avg_price=(f"{avg_price:,.0f}" if is_valid_num(avg_price) else "—"),
            avg_price_raw=(float(avg_price) if is_valid_num(avg_price) else None),
            districts=(f"{spike_n:,}" if is_valid_num(spike_n) else "—"),
            districts_raw=(float(spike_n) if is_valid_num(spike_n) else None),
            mape=(f"{_mape:.1f}" if is_valid_num(_mape) else "—"),
            mape_raw=(float(_mape) if is_valid_num(_mape) else None),
        )
    except Exception:
        # Fallback: flat metrics (graceful degradation)
        _mape_s = f"{_mape:.1f}%" if isinstance(_mape, (int, float)) else "—"
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Price Effect (₹)", f"₹{effect:,.0f}" if effect is not None else "—")
        with col2:
            st.metric("Avg Modal Price", f"₹{avg_price:,.0f}" if avg_price else "—")
        with col3:
            st.metric("Districts Flagged", f"{spike_n:,}" if spike_n else "—")
        with col4:
            st.metric("Forecast MAPE", _mape_s)

    # ── Data Freshness Widget ──
    _render_freshness_widget()

    # ── National Monsoon Context strip ──
    _render_national_monsoon_strip()

    # ── Price trend chart in glass card ──
    st.markdown(
        """
        <div style="margin-top:2rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
            02 / Price Trend
          </div>
          <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.4rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:1rem;">
            Daily Price Trend
          </h2>
        </div>
        """,
        unsafe_allow_html=True,
    )
    try:
        from mandi_rdd.storage.duckdb_store import get_connection
        conn = get_connection()
        df = conn.execute(
            "SELECT arrival_date, AVG(modal_price) as avg_price, MIN(modal_price) as min_price, MAX(modal_price) as max_price FROM prices WHERE commodity = ? GROUP BY arrival_date ORDER BY arrival_date",
            [selected_commodity],
        ).fetchdf()
        conn.close()
        if len(df) > 5:
            color = commodity_color(selected_commodity)
            fig = make_themed_figure()
            fig.add_trace(go.Scatter(x=df["arrival_date"], y=df["avg_price"], mode="lines", name="Avg", line=dict(color=color, width=2)))
            fig.add_trace(go.Scatter(x=df["arrival_date"], y=df["max_price"], mode="lines", name="Max", line=dict(color=color, width=1, dash="dash", opacity=0.6)))
            fig.add_trace(go.Scatter(x=df["arrival_date"], y=df["min_price"], mode="lines", name="Min", line=dict(color="#7e7e7e", width=1, dash="dash", opacity=0.5)))
            fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=350)
            st.markdown('<div class="glass" style="padding:1.2rem;">', unsafe_allow_html=True)
            st.plotly_chart(fig, use_container_width=True)
            st.markdown('</div>', unsafe_allow_html=True)
        else:
            st.markdown(
                '<div class="interpretation-box insig-box">Insufficient price data to plot a trend. '
                'Run the ingestion pipeline first.</div>',
                unsafe_allow_html=True,
            )
    except Exception:
        st.markdown(
            '<div class="interpretation-box insig-box">Price trend unavailable — run ingestion first.</div>',
            unsafe_allow_html=True,
        )


def _render_freshness_widget():
    """Render a Data Freshness widget using GET /freshness API data.

    Shows per-commodity last-updated dates, row counts, district/state coverage,
    and data source (api/csv/ashoka/rainfall). Falls back gracefully if the API
    is unreachable.
    """
    st.markdown(
        """
        <div style="margin-top:2.5rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
            01 / Data Freshness
          </div>
          <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.4rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:1rem;">
            Commodity Freshness <span style="font-size:1.2rem;">\u23f3</span>
          </h2>
          <p style="color:#7e7e7e;font-size:0.85rem;margin-bottom:1rem;">
            Per-commodity data freshness: latest record date, row count, district coverage,
            and ingestion source. Data flows from data.gov.in API, CSV backfill, Ashoka CEDA
            archive, and rainfall feeds. Rows tagged with missing commodities are grouped under
            "Other / Uncategorized".
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Fetch freshness data
    freshness_data = _cached_freshness()

    if not freshness_data or (isinstance(freshness_data, dict) and "error" in freshness_data):
        st.markdown(
            f'<div class="interpretation-box insig-box">\u26a0\ufe0f Freshness data unavailable. '
            f'Run the ingestion pipeline to populate commodity freshness.</div>',
            unsafe_allow_html=True,
        )
        return

    if isinstance(freshness_data, list) and len(freshness_data) == 0:
        st.markdown(
            '<div class="interpretation-box insig-box">No freshness data yet. '
            'The first ingestion run will populate these metrics.</div>',
            unsafe_allow_html=True,
        )
        return

    # Build a summary row at the top
    total_rows = sum(r.get("row_count", 0) for r in freshness_data if isinstance(r, dict))
    total_commodities = len([r for r in freshness_data if isinstance(r, dict) and r.get("commodity")])
    # Count how many commodities have data in the last 7 days
    import datetime
    _seven_days_ago = (datetime.datetime.utcnow() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    recent_count = sum(
        1 for r in freshness_data if isinstance(r, dict)
        and r.get("latest_date", "") >= _seven_days_ago
    )

    # KPI micro-row
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Commodities tracked", f"{total_commodities}")
    with c2:
        st.metric("Total price rows", f"{total_rows:,}")
    with c3:
        st.metric("Updated last 7d", f"{recent_count}")
    with c4:
        # Find the source types in use
        source_types = set()
        for r in freshness_data:
            if isinstance(r, dict):
                stype = r.get("source_type") or ""
                if stype:
                    source_types.add(stype)
        st.metric("Data sources", ", ".join(sorted(source_types)) if source_types else "—")

    # Render the freshness table as styled HTML
    _FRESHNESS_TABLE_CSS = f"""
    <style>
    .freshness-table {{
        width: 100%; border-collapse: collapse;
        font-family: "IBM Plex Sans", system-ui, sans-serif;
        font-size: 0.82rem;
    }}
    .freshness-table th {{
        text-align: left; padding: 0.6rem 0.75rem;
        color: #7e7e7e; font-weight: 500; font-size: 0.7rem;
        text-transform: uppercase; letter-spacing: 0.05em;
        border-bottom: 1px solid rgba(255,255,255,0.1);
        white-space: nowrap;
    }}
    .freshness-table td {{
        padding: 0.55rem 0.75rem;
        color: #ffffff;
        border-bottom: 1px solid rgba(255,255,255,0.04);
        white-space: nowrap;
    }}
    .freshness-table tr:hover td {{
        background: rgba(255,255,255,0.02);
    }}
    .freshness-table .mono {{
        font-family: "IBM Plex Mono", monospace;
        font-variant-numeric: tabular-nums;
    }}
    .freshness-table .num {{
        font-family: "IBM Plex Mono", monospace;
        text-align: right;
        font-variant-numeric: tabular-nums;
    }}
    .freshness-table .source-badge {{
        display: inline-block;
        padding: 0.1rem 0.45rem;
        border-radius: 3px;
        font-size: 0.7rem;
        font-family: "IBM Plex Mono", monospace;
        font-weight: 500;
    }}
    .source-badge-api {{
        background: rgba(215, 255, 0, 0.12);
        color: #d7ff00;
    }}
    .source-badge-csv {{
        background: rgba(139, 107, 196, 0.12);
        color: #8B6BC4;
    }}
    .source-badge-ashoka {{
        background: rgba(217, 102, 59, 0.12);
        color: #D9663B;
    }}
    .source-badge-rainfall {{
        background: rgba(143, 174, 137, 0.12);
        color: #8FAE89;
    }}
    .source-badge-varietywise {{
        background: rgba(180, 131, 84, 0.12);
        color: #B48354;
    }}
    .source-badge-other {{
        background: rgba(186, 186, 186, 0.12);
        color: #bababa;
    }}
    .freshness-table .fresh-dot {{
        display: inline-block;
        width: 7px; height: 7px;
        border-radius: 50%;
        margin-right: 4px;
    }}
    .fresh-dot-recent {{ background: #6BBF8A; }}
    .fresh-dot-stale {{ background: #E8B14D; }}
    .fresh-dot-old   {{ background: #C84B4B; }}
    </style>
    """
    st.markdown(_FRESHNESS_TABLE_CSS, unsafe_allow_html=True)

    now = datetime.datetime.utcnow()
    _cutoff_recent = (now - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    _cutoff_stale = (now - datetime.timedelta(days=30)).strftime("%Y-%m-%d")

    rows_html = ""
    for r in freshness_data:
        if not isinstance(r, dict):
            continue
        commodity = (r.get("commodity") or "Other / Uncategorized").title()
        latest = r.get("latest_date") or "—"
        earliest = r.get("earliest_date") or "—"
        row_count = r.get("row_count", 0)
        n_districts = r.get("n_districts", 0)
        n_states = r.get("n_states", 0)
        source_type = (r.get("source_type") or "other").lower()
        source_name = r.get("source_name") or ""

        # Determine freshness status dot
        if latest != "—" and latest >= _cutoff_recent:
            dot_class = "fresh-dot-recent"
            dot_title = "Updated in last 7 days"
        elif latest != "—" and latest >= _cutoff_stale:
            dot_class = "fresh-dot-stale"
            dot_title = "7–30 days old"
        else:
            dot_class = "fresh-dot-old"
            dot_title = "Over 30 days old"

        # Source badge class
        if source_type in ("api",):
            badge_class = "source-badge-api"
            badge_label = "API"
        elif source_type == "csv":
            badge_class = "source-badge-csv"
            badge_label = "CSV"
        elif source_type == "ashoka":
            badge_class = "source-badge-ashoka"
            badge_label = "Ashoka"
        elif source_type == "rainfall":
            badge_class = "source-badge-rainfall"
            badge_label = "Rainfall"
        elif source_type == "varietywise":
            badge_class = "source-badge-varietywise"
            badge_label = "Variety"
        else:
            badge_class = "source-badge-other"
            badge_label = source_type[:8].upper()

        # Tooltip for source
        title_attr = f' title="{source_name}"' if source_name else ""

        rows_html += (
            f"<tr>"
            f'<td><span class="fresh-dot {dot_class}" title="{dot_title}"></span>{commodity}</td>'
            f'<td class="mono">{latest}</td>'
            f'<td class="mono">{earliest}</td>'
            f'<td class="num">{row_count:,}</td>'
            f'<td class="num">{n_districts}</td>'
            f'<td class="num">{n_states}</td>'
            f'<td><span class="source-badge {badge_class}"{title_attr}>{badge_label}</span></td>'
            f"</tr>"
        )

    table_html = (
        '<div class="glass" style="padding:1rem;overflow-x:auto;">'
        '<table class="freshness-table">'
        "<thead><tr>"
        "<th>Commodity</th>"
        "<th>Latest Date</th>"
        "<th>Earliest Date</th>"
        "<th style=\"text-align:right;\">Rows</th>"
        "<th style=\"text-align:right;\">Districts</th>"
        "<th style=\"text-align:right;\">States</th>"
        "<th>Source</th>"
        "</tr></thead><tbody>"
        f"{rows_html}"
        "</tbody></table></div>"
    )
    st.markdown(table_html, unsafe_allow_html=True)


@st.cache_data(ttl=120, show_spinner=False)
def _cached_freshness():
    """Cached freshness data from the API. TTL=120s."""
    try:
        from mandi_rdd.dashboard.data_access import get_freshness
        return get_freshness()
    except Exception:
        return []


def _render_ask_panel(default_commodity: str):
    """
    Render the 'Ask MandiIQ' AI chat panel with Alche-styled components.
    """
    st.markdown(
        """
        <div style="margin-top:2.5rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
            03 / AI Procurement
          </div>
          <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.4rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:1rem;">
            Ask MandiIQ <span style="font-size:1.2rem;">🧠</span>
          </h2>
          <p style="color:#7e7e7e;font-size:0.85rem;margin-bottom:1rem;">
            Ask a procurement question in plain English. Answers are grounded in live
            tool-call results. Powered by <strong style="color:#bababa;">free-tier multi-model routing</strong>
            (Gemini direct or OpenRouter) with circuit-breaker fallback.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Check API availability ──
    api_available = False
    api_error = None
    try:
        import requests
        resp = requests.get(f"{API_BASE}/health", timeout=3)
        if resp.status_code == 200:
            api_available = True
        else:
            api_error = f"API server returned status {resp.status_code}"
    except ImportError:
        api_error = (
            "<code>requests</code> library not installed. "
            "Run: <code>pip install requests</code>"
        )
    except requests.exceptions.ConnectionError:
        api_error = (
            f"Cannot reach the API server at <code>{API_BASE}</code>. "
            "Start it with: <code>uvicorn mandi_rdd.api.main:app --reload</code>"
        )
    except requests.exceptions.Timeout:
        api_error = f"API server at <code>{API_BASE}</code> timed out."
    except Exception as e:
        api_error = f"Cannot connect to API: {e}"

    if not api_available:
        st.markdown(
            f'<div class="interpretation-box insig-box">⚠️ API server is not available. {api_error}</div>',
            unsafe_allow_html=True,
        )
        return

    # ── Chat UI ──
    if "ask_history" not in st.session_state:
        st.session_state.ask_history = []
    if "ask_input_key" not in st.session_state:
        st.session_state.ask_input_key = 0

    query = st.text_area(
        "Your question",
        placeholder=(
            f'e.g. "Should I lock in {default_commodity} procurement in Nashik next month?"'
        ),
        height=80,
        label_visibility="collapsed",
        key=f"ask_input_{st.session_state.ask_input_key}",
    )

    col_q1, col_q2, _ = st.columns([1, 1, 6])
    with col_q1:
        asked = st.button("🔍 Ask MandiIQ", type="primary", use_container_width=True)
    with col_q2:
        clear = st.button("Clear", use_container_width=True)

    if clear:
        st.session_state.ask_history = []
        st.session_state.ask_input_key += 1
        st.rerun()

    # ── Submit — POST /ask to the API ──
    if asked and query.strip():
        with st.spinner("Routing through OpenRouter fallback chain..."):
            try:
                resp = requests.post(
                    f"{API_BASE}/ask",
                    json={
                        "query": query.strip(),
                        "commodity": default_commodity,
                    },
                    timeout=30,
                )
                if resp.status_code == 200:
                    result = resp.json()
                else:
                    try:
                        detail = resp.json()
                    except Exception:
                        detail = {"error": f"HTTP {resp.status_code}"}
                    result = {
                        "query": query.strip(),
                        "commodity": default_commodity,
                        "district": "All",
                        "answer": f"API returned an error (HTTP {resp.status_code}).",
                        "model_used": None,
                        "endpoints_used": [],
                        "error": detail.get("detail", detail.get("error", str(resp.status_code))),
                    }

                err = result.get("error") or ""
                if ("API_KEY" in err or "provider" in err.lower()
                        or "openrouter" in err.lower() or "gemini" in err.lower()):
                    result["answer"] = (
                        "⚠️ **AI chat is not configured.** No LLM provider key is set "
                        "on the API server. Set **GEMINI_API_KEY** (free — get one at "
                        "[aistudio.google.com/apikey](https://aistudio.google.com/apikey)) "
                        "or **OPENROUTER_API_KEY** (free — "
                        "[openrouter.ai/keys](https://openrouter.ai/keys)) to enable the "
                        "Ask MandiIQ feature. No credit card required for either."
                    )

                st.session_state.ask_history.append(result)
            except requests.exceptions.Timeout:
                st.session_state.ask_history.append({
                    "query": query.strip(),
                    "commodity": default_commodity,
                    "district": "All",
                    "answer": "The request timed out. The orchestrator may be slow to respond (free-tier model latency varies). Try again or simplify your question.",
                    "model_used": None,
                    "endpoints_used": [],
                    "error": "Request timed out after 30 seconds",
                })
            except requests.exceptions.RequestException as e:
                st.session_state.ask_history.append({
                    "query": query.strip(),
                    "commodity": default_commodity,
                    "district": "All",
                    "answer": f"Could not reach the API server: {e}",
                    "model_used": None,
                    "endpoints_used": [],
                    "error": str(e),
                })
            except Exception as e:
                st.session_state.ask_history.append({
                    "query": query.strip(),
                    "commodity": default_commodity,
                    "district": "All",
                    "answer": f"Unexpected error: {e}",
                    "model_used": None,
                    "endpoints_used": [],
                    "error": str(e),
                })

    # Display chat history using interpretation boxes
    for i, entry in enumerate(reversed(st.session_state.ask_history)):
        _render_chat_entry(entry, i)


def _render_chat_entry(entry: dict, idx: int):
    """Render a single chat entry using Alche interpretation box styling."""
    answer = entry.get("answer", "No answer generated.")
    model_used = entry.get("model_used")
    endpoints_used = entry.get("endpoints_used", [])
    error = entry.get("error")
    query = entry.get("query", "")
    commodity = entry.get("commodity", "")
    district = entry.get("district", "")

    # Answer box
    st.markdown(
        f"""
        <div class="interpretation-box" style="margin:0.8rem 0;">
            <div style="font-size:0.75rem;color:#7e7e7e;margin-bottom:0.5rem;">
                📝 <strong style="color:#bababa;">{query}</strong>
            </div>
            {answer}
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Collapsible metadata
    if model_used or endpoints_used or error:
        with st.expander("⚙️ Response metadata", expanded=False):
            if model_used:
                st.markdown(
                    f'<span style="color:#7e7e7e;font-size:0.8rem;">Served by:</span> '
                    f'<span style="font-family:IBM Plex Mono;color:#d7ff00;font-size:0.8rem;">{model_used}</span>',
                    unsafe_allow_html=True,
                )
            if endpoints_used:
                eps = ", ".join(endpoints_used)
                st.markdown(
                    f'<span style="color:#7e7e7e;font-size:0.8rem;">Endpoints cited:</span> '
                    f'<span style="font-family:IBM Plex Mono;color:#d7ff00;font-size:0.8rem;">{eps}</span>',
                    unsafe_allow_html=True,
                )
            if commodity or district:
                st.markdown(
                    f'<span style="color:#7e7e7e;font-size:0.8rem;">Context:</span> '
                    f'<span style="color:#ffffff;font-size:0.8rem;">{commodity} — {district}</span>',
                    unsafe_allow_html=True,
                )
            if error:
                st.markdown(
                    f'<span style="color:#D9663B;font-size:0.8rem;">⚠️ {error}</span>',
                    unsafe_allow_html=True,
                )


def _render_national_monsoon_strip():
    """Compact glass strip: national monsoon baseline 1901-2019 + sparkline."""
    data = _cached_all_india_monsoon()
    if not data:
        return
    try:
        df = pd.DataFrame(data)
        mean_total = float(df["jun_sep"].mean())
        worst = df.loc[df["jun_sep"].idxmin()]
        best = df.loc[df["jun_sep"].idxmax()]
        last = float(df.iloc[-1]["jun_sep"])

        st.markdown(
            """
            <div style="margin-top:1.5rem;">
              <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
                CENTURY-SCALE CONTEXT
              </div>
              <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.2rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:0.8rem;">
                National Monsoon Baseline · 1901–2019
              </h2>
              <p style="color:#7e7e7e;font-size:0.85rem;max-width:700px;line-height:1.7;margin-bottom:1.2rem;">
                The long IMD series — a national reference frame for the district-level
                rainfall-deficit threshold that drives the causal analysis.
              </p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Glass card for monsoon metrics
        st.markdown('<div class="glass" style="padding:1.2rem;">', unsafe_allow_html=True)
        c1, c2, c3, c4 = st.columns([1, 1, 1, 2.2])
        with c1:
            st.metric("Avg monsoon", f"{mean_total:.0f} mm", help="Mean Jun–Sep total, 1901–2019")
        with c2:
            st.metric("Driest", f"{float(worst['jun_sep']):.0f} mm", f"{int(worst['year'])}")
        with c3:
            st.metric("Wettest", f"{float(best['jun_sep']):.0f} mm", f"{int(best['year'])}")
        with c4:
            fig = make_themed_figure()
            fig.add_trace(go.Scatter(
                x=df["year"], y=df["jun_sep"], mode="lines",
                line=dict(color="#d7ff00", width=2), fill="tozeroy",
                fillcolor="rgba(234,179,8,0.10)", showlegend=False,
            ))
            fig.add_hline(y=mean_total * 0.81, line_color="#A85A42", line_dash="dash",
                          line_width=1, annotation_text="−19%", annotation_position="top left")
            fig.update_layout(margin=dict(l=0, r=0, t=4, b=0), height=90,
                              xaxis=dict(visible=False), yaxis=dict(visible=False))
            st.plotly_chart(fig, use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)
    except Exception:
        return
