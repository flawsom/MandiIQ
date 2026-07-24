"""
MandiIQ — Settings page.

Data source status, model routing status, theme controls.
Shows live health of all connected APIs.

Alche Studio Design: glass status cards, section headers,
database table with Alche tokens, consistent monochrome-lime palette.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import os
import streamlit as st
from mandi_rdd.dashboard.theme import inject_theme, TURMERIC, RUST, SAGE, SLATE, MUTED
from mandi_rdd.dashboard.icons import SVG_SUN, SVG_MOON
from mandi_rdd.storage.duckdb_store import get_connection
from mandi_rdd.ingestion.scheduler import run_ingestion


def render():
    inject_theme()
    st.markdown("""
        <div class="page-hero" style="margin-bottom:2rem;">
          <div>
            <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
              Configuration
            </div>
            <h1 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:300;font-size:clamp(1.6rem,3vw,2.4rem);color:#ffffff;letter-spacing:0.03em;text-transform:uppercase;margin-bottom:0.5rem;">
              Settings — <span style="font-weight:600;color:#d7ff00;">System Status</span>
            </h1>
            <p style="color:#7e7e7e;max-width:680px;line-height:1.7;font-size:0.9rem;">
              Monitor data source health, pipeline status, and environment configuration.
            </p>
          </div>
        </div>
    """, unsafe_allow_html=True)

    # ── Data Source Status ──
    st.markdown("""
        <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.8rem;">
          01 / Data Sources
        </div>
    """, unsafe_allow_html=True)

    # Check each data source
    conn = None
    try:
        conn = get_connection()
        price_count = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
        rain_count = conn.execute("SELECT COUNT(*) FROM rainfall").fetchone()[0]
        ndvi_count = conn.execute("SELECT COUNT(*) FROM ndvi").fetchone()[0]
    except Exception:
        price_count = 0
        rain_count = 0
        ndvi_count = 0
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    sources = [
        ("Agmarknet (Mandi Prices)", price_count, "price records", "https://data.gov.in/"),
        ("IMD (Rainfall)", rain_count, "rainfall obs", "https://mausam.imd.gov.in/"),
        ("Sentinel-2 (NDVI)", ndvi_count, "NDVI records", "https://sentinel.esa.int/"),
    ]

    for name, count, unit, url in sources:
        configured = count > 0
        status_color = SAGE if configured else RUST
        status_text = f"Configured — {count:,} {unit}" if configured else "Not configured"

        st.markdown(f"""
            <div class="crosshair-panel glass" style="padding:1rem;margin:0.5rem 0;display:flex;justify-content:space-between;align-items:center;">
                <div>
                    <strong style="color:#ffffff;">{name}</strong><br/>
                    <span style="color:{MUTED};font-size:0.8rem;">{url}</span>
                </div>
                <div style="display:flex;align-items:center;gap:8px;">
                    <span style="width:10px;height:10px;border-radius:50%;background:{status_color};"></span>
                    <span style="color:{status_color};font-size:0.85rem;">{status_text}</span>
                </div>
            </div>
        """, unsafe_allow_html=True)

    # ── AI Provider Status ──
    st.markdown("""
        <div style="margin-top:2rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.8rem;">
            02 / AI Provider
          </div>
          <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.2rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:0.8rem;">
            Ask MandiIQ
          </h2>
        </div>
    """, unsafe_allow_html=True)

    gemini_key = bool(os.environ.get("GEMINI_API_KEY"))
    openrouter_key = bool(os.environ.get("OPENROUTER_API_KEY"))

    ai_configured = gemini_key or openrouter_key

    if ai_configured:
        provider = "Gemini (direct)" if gemini_key else "OpenRouter"
        st.markdown(f"""
            <div class="crosshair-panel glass" style="padding:1rem;border-color:{SAGE};">
                <span style="color:{SAGE};">✓</span>
                <strong style="color:#ffffff;">{provider}</strong> configured<br/>
                <span style="color:{MUTED};font-size:0.8rem;">
                    Ask MandiIQ chat is enabled. Free-tier limits apply.
                </span>
            </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown(f"""
            <div class="crosshair-panel glass" style="padding:1rem;border-color:{RUST};">
                <span style="color:{RUST};">⚠</span>
                <strong style="color:#ffffff;">No AI provider configured</strong><br/>
                <span style="color:{MUTED};font-size:0.8rem;">
                    Set <code>GEMINI_API_KEY</code> or <code>OPENROUTER_API_KEY</code> to enable chat.
                    Both have free tiers — no credit card required.
                </span>
            </div>
        """, unsafe_allow_html=True)
        st.markdown("""
            <div style="margin-top:0.8rem;">
                <strong style="color:#d7ff00;">Get a free API key:</strong><br/>
                • <a href="https://aistudio.google.com/apikey" style="color:#d7ff00;">Google AI Studio (Gemini)</a> — 15 req/min free<br/>
                • <a href="https://openrouter.ai/keys" style="color:#d7ff00;">OpenRouter</a> — multi-model routing
            </div>
        """, unsafe_allow_html=True)

    # ── System Health ──
    st.markdown("""
        <div style="margin-top:2rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.8rem;">
            03 / System Health
          </div>
        </div>
    """, unsafe_allow_html=True)

    # Check DuckDB connection
    db_ok = False
    data_path = Path(__file__).resolve().parent.parent.parent.parent / "mandi_rdd" / "data"
    conn = None
    try:
        conn = get_connection()
        db_ok = True
    except Exception:
        db_ok = False
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    data_dir_ok = data_path.exists()

    ingest_ok = False
    ingest_time = None
    ingest_file = data_path / "last_ingest_status.json"
    try:
        import json
        if ingest_file.exists():
            with open(ingest_file) as f:
                status = json.load(f)
            ingest_ok = status.get("status") == "success"
            ingest_time = status.get("last_run")
    except Exception:
        pass

    checks = [
        ("DuckDB Database", db_ok, "Connected" if db_ok else "Not reachable"),
        ("Data Directory", data_dir_ok, "Exists" if data_dir_ok else "Missing"),
        ("Pipeline Status", ingest_ok, f"Last run: {ingest_time}" if ingest_time else ("No runs yet" if data_dir_ok else "N/A")),
    ]

    for name, ok, detail in checks:
        color = SAGE if ok else (RUST if not ok else MUTED)
        icon = "✓" if ok else "⚠"
        st.markdown(f"""
            <div class="crosshair-panel glass" style="padding:1rem;margin:0.5rem 0;display:flex;justify-content:space-between;align-items:center;">
                <div>
                    <strong style="color:#ffffff;">{name}</strong><br/>
                    <span style="color:{MUTED};font-size:0.8rem;">{detail}</span>
                </div>
                <span style="color:{color};font-size:1.2rem;font-weight:bold;">{icon}</span>
            </div>
        """, unsafe_allow_html=True)

    # ── Database Status ──
    st.markdown("""
        <div style="margin-top:2rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.8rem;">
            04 / Database Tables
          </div>
        </div>
    """, unsafe_allow_html=True)

    try:
        conn = get_connection()
        tables = ["prices", "rainfall", "ndvi", "forecasts", "discontinuities"]
        counts = {}
        for table in tables:
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                counts[table] = count
            except Exception:
                counts[table] = 0
        conn.close()

        st.markdown(f"""
            <div class="crosshair-panel glass" style="padding:1rem;margin:0.5rem 0;">
                <strong style="color:#ffffff;">DuckDB Storage</strong><br/>
                <span style="color:#bababa;font-size:0.8rem;">mandi_rdd/data/mandi_iq.duckdb</span>
            </div>
            <table style="width:100%;margin-top:0.5rem;font-size:0.85rem;">
                <tr style="color:#bababa;">
                    <th style="text-align:left;padding:0.3rem;">Table</th>
                    <th style="text-align:right;padding:0.3rem;">Rows</th>
                </tr>
        """, unsafe_allow_html=True)

        for table, count in counts.items():
            color = SAGE if count > 0 else MUTED
            st.markdown(f"""
                <tr>
                    <td style="padding:0.3rem;color:#ffffff;">{table}</td>
                    <td style="padding:0.3rem;text-align:right;color:{color};font-family:'IBM Plex Mono',monospace;">{count:,}</td>
                </tr>
            """, unsafe_allow_html=True)

        st.markdown("</table>", unsafe_allow_html=True)

    except Exception as e:
        st.markdown(f"""
            <div class="crosshair-panel glass" style="padding:1rem;text-align:center;border-color:{RUST};">
                <span style="color:{RUST};">⚠</span>
                <strong style="color:#ffffff;">Database not initialized</strong><br/>
                <span style="color:{MUTED};font-size:0.8rem;">
                    Run ingestion: <code>python -m mandi_rdd.ingestion.ingest</code>
                </span>
            </div>
        """, unsafe_allow_html=True)

    # ── Pipeline Status ──
    st.markdown("""
        <div style="margin-top:2rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.8rem;">
            05 / Pipeline
          </div>
        </div>
    """, unsafe_allow_html=True)

    col_info, col_btn = st.columns([3, 1])
    with col_info:
        st.markdown("""
            <div class="crosshair-panel glass" style="padding:1rem;margin:0.5rem 0;">
                <strong style="color:#ffffff;">Nightly Ingestion</strong><br/>
                <span style="color:#bababa;font-size:0.8rem;">
                    Runs daily at 2:00 AM IST • Fetches latest mandi prices, rainfall, NDVI
                </span>
            </div>
        """, unsafe_allow_html=True)

    with col_btn:
        if "pipeline_running" not in st.session_state:
            st.session_state.pipeline_running = False
            st.session_state.pipeline_result = None

        run_clicked = st.button("▶ Run Pipeline Now", type="primary", use_container_width=True)

        if run_clicked or st.session_state.pipeline_running:
            if not st.session_state.pipeline_running:
                st.session_state.pipeline_running = True
                st.session_state.pipeline_result = None
                st.rerun()

            with st.spinner("Running pipeline — fetching data, running RDD analysis..."):
                result = run_ingestion(skip_rainfall=False)
                st.session_state.pipeline_result = result
                st.session_state.pipeline_running = False

            # Rerun outside the spinner context so Streamlit cleans up properly
            st.rerun()

        if st.session_state.pipeline_result:
            result = st.session_state.pipeline_result
            if result.get("status") == "ok":
                steps = result.get("steps", {})
                prices = steps.get("prices", {})
                rainfall = steps.get("rainfall", {})
                st.success(
                    f"Pipeline complete in {result.get('duration_seconds', '?')}s. "
                    f"Prices: {prices.get('fetched', 0)} records, "
                    f"Rainfall: {rainfall.get('fetched', 0)} records, "
                    f"RDD: {steps.get('rdd', {}).get('commodities_run', 0)} commodities. "
                    f"Refresh the app to see updated data."
                )
            else:
                st.error(f"Pipeline failed: {result.get('error', 'unknown error')}")

    # ── Theme ──
    st.markdown("""
        <div style="margin-top:2rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.8rem;">
            06 / Theme
          </div>
        </div>
    """, unsafe_allow_html=True)

    st.markdown("""
        <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.2rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:1rem;">
            Display Theme
        </h2>
    """, unsafe_allow_html=True)

    st.markdown("""
        <div class="crosshair-panel glass" style="padding:1.2rem;">
            <p style="color:#bababa;font-size:0.85rem;margin-bottom:0.8rem;">
                Swap the pure-black canvas (<code style="color:#d7ff00;">#000000</code>) for a dark-gray surface
                (<code style="color:#d7ff00;">#111111</code>) — easier on the eyes during daytime.
            </p>
    """, unsafe_allow_html=True)

    _surface_on = st.session_state.get("surface_mode", False)
    _icon = SVG_MOON if _surface_on else SVG_SUN
    _label = "Surface mode on" if _surface_on else "Lighter surface"

    st.markdown(
        '<div style="display:flex;align-items:center;gap:6px;margin-top:0.5rem;margin-bottom:0.75rem;">'
        '<span style="display:flex;color:#bababa;">' + _icon + '</span>'
        '<span style="font-size:0.85rem;color:#bababa;">' + _label + '</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.button(
        "Toggle",
        key="surface_mode_settings_btn",
        on_click=lambda: st.session_state.update(
            surface_mode=not st.session_state.get("surface_mode", False)
        ),
        use_container_width=True,
    )

    st.markdown("""
        </div>
    """, unsafe_allow_html=True)

    # ── Environment Info ──
    st.markdown("""
        <div style="margin-top:2rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.8rem;">
            07 / Environment
          </div>
        </div>
    """, unsafe_allow_html=True)

    st.markdown("""
        <div class="crosshair-panel glass" style="padding:1rem;margin:0.5rem 0;font-size:0.85rem;">
            <div style="margin-bottom:0.5rem;">
                <span style="color:#bababa;">Internal Service:</span>
                <code style="color:#d7ff00;">FastAPI (localhost:8000)</code>
            </div>
            <div style="margin-bottom:0.5rem;">
                <span style="color:#bababa;">Dashboard Port:</span>
                <code style="color:#d7ff00;">8501</code>
            </div>
            <div>
                <span style="color:#bababa;">Data Directory:</span>
                <code style="color:#d7ff00;">mandi_rdd/data/</code>
            </div>
        </div>
    """, unsafe_allow_html=True)

    # ── Footer ──
    st.markdown("<hr style='border-color:rgba(255,255,255,0.07);margin:1.5rem 0;'>", unsafe_allow_html=True)
    st.markdown(
        '<p style="color:#7e7e7e;font-size:0.8rem;">'
        'Configuration via environment variables • See <code>.env.example</code> for options'
        '</p>',
        unsafe_allow_html=True
    )
