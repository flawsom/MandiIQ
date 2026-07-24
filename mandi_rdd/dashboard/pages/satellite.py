"""
MandiIQ — Satellite View (NDVI) page.

District map colored by NDVI anomaly.
Side-by-side NDVI trend vs. rainfall trend — the cross-check from the system PRD.

Alche Studio Design: glass cards, interpretation boxes, glass KPI strip,
section labels, consistent monochrome-lime palette.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from mandi_rdd.dashboard.theme import (
    inject_theme, TURMERIC, RUST, SAGE, SLATE, MUTED, FAINT, INK
)
from mandi_rdd.dashboard.plotly_theme import make_themed_figure

import json as _json

NDVI_JSON_PATH = Path(__file__).resolve().parent.parent.parent / 'data' / 'ndvi_latest.json'


def _load_ndvi_records(district: str) -> list:
    """Load NDVI data from the git-tracked JSON export (fallback)."""
    if not NDVI_JSON_PATH.exists():
        return []
    try:
        with open(NDVI_JSON_PATH) as f:
            data = _json.load(f)
        records = data.get('records', [])
        if district:
            return [r for r in records if r.get('district') == district]
        return records
    except Exception:
        return []


def render():
    inject_theme()
    st.markdown("""
        <div class="page-hero" style="margin-bottom:2rem;">
          <div>
            <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
              Satellite Imagery
            </div>
            <h1 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:300;font-size:clamp(1.6rem,3vw,2.4rem);color:#ffffff;letter-spacing:0.03em;text-transform:uppercase;margin-bottom:0.5rem;">
              Satellite View — <span style="font-weight:600;color:#d7ff00;">NDVI Analysis</span>
            </h1>
            <p style="color:#7e7e7e;max-width:680px;line-height:1.7;font-size:0.9rem;">
                Vegetation health from Sentinel-2 satellite imagery. NDVI (Normalized Difference
                Vegetation Index) measures crop vigor — lower values indicate stress, potentially
                from drought or disease. Cross-check against rainfall to distinguish causes.
            </p>
          </div>
        </div>
    """, unsafe_allow_html=True)

    # ── District selector ──
    districts = None
    db_error = False

    try:
        from mandi_rdd.storage.duckdb_store import get_connection
        conn = get_connection()
        result = conn.execute("SELECT DISTINCT district FROM prices ORDER BY district").fetchall()
        if result:
            districts = [r[0] for r in result]
        conn.close()
    except Exception:
        db_error = True

    if not districts or db_error:
        all_records = _load_ndvi_records("")
        ndvi_districts = list(set(r['district'] for r in all_records if r.get('district')))
        if ndvi_districts:
            districts = sorted(ndvi_districts)
            db_error = False

    if db_error or not districts:
        st.markdown("""
            <div class="glass" style="padding:1.5rem;text-align:center;border-color:#D9663B;">
                <p style="color:#bababa;margin:0;font-size:0.9rem;">
                    ⚠ Data source unavailable — unable to load district list.
                    Check the pipeline status on the Settings page.
                </p>
            </div>
        """, unsafe_allow_html=True)
        return

    selected_district = st.selectbox("Select District", districts)

    # ── Try to load NDVI data ──
    ndvi_df = None
    rainfall_df = None

    try:
        from mandi_rdd.storage.duckdb_store import get_connection
        conn = get_connection()

        try:
            ndvi_df = conn.execute("""
                SELECT date, ndvi, anomaly
                FROM ndvi
                WHERE district = ?
                ORDER BY date
            """, [selected_district]).fetchdf()
        except Exception:
            ndvi_df = None

        try:
            rainfall_df = conn.execute("""
                SELECT date, rainfall_mm, deficit_pct
                FROM rainfall
                WHERE district = ?
                ORDER BY date
            """, [selected_district]).fetchdf()
        except Exception:
            rainfall_df = None

        conn.close()
    except Exception:
        pass

    # ── Empty state (glass card) ──
    if ndvi_df is None or len(ndvi_df) == 0:
        st.markdown("""
            <div class="glass" style="padding:2rem;text-align:center;">
                <h3 style="color:#bababa;margin-top:0;font-size:1.1rem;">No NDVI data available</h3>
                <p style="color:#7e7e7e;font-size:0.85rem;">
                    Satellite imagery requires Sentinel Hub credentials.
                </p>
                <p style="color:#7e7e7e;font-size:0.75rem;">
                    Set <strong>SENTINEL_CLIENT_ID</strong> and <strong>SENTINEL_CLIENT_SECRET</strong>
                    to enable satellite data ingestion.<br/>
                    Get free tier at <a href="https://www.sentinel-hub.com/pricing/" style="color:#d7ff00;">sentinel-hub.com</a>
                </p>
            </div>
        """, unsafe_allow_html=True)

        st.markdown("""
            <div style="margin-top:1.5rem;">
              <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
                MAP
              </div>
              <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.3rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:1rem;">
                NDVI Anomaly Map
              </h2>
            </div>
        """, unsafe_allow_html=True)

        st.markdown("""
            <div class="glass" style="padding:2rem;text-align:center;">
                <span style="color:#7e7e7e;">Satellite imagery would appear here when data is available.<br/>
                NDVI anomaly highlights areas of vegetation stress.</span>
            </div>
        """, unsafe_allow_html=True)
        return

    # ── NDVI Summary (glass KPI strip) ──
    st.markdown("""
        <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.8rem;">
          VEGETATION HEALTH
        </div>
    """, unsafe_allow_html=True)
    st.markdown('<div class="crosshair-panel glass" style="padding:1rem;">', unsafe_allow_html=True)
    col1, col2, col3 = st.columns(3)
    with col1:
        latest_ndvi = ndvi_df.iloc[-1]["ndvi"] if len(ndvi_df) > 0 else None
        st.metric("Current NDVI", f"{latest_ndvi:.2f}" if latest_ndvi else "—")
    with col2:
        latest_anomaly = ndvi_df.iloc[-1]["anomaly"] if len(ndvi_df) > 0 else None
        anomaly_color = "inverse" if latest_anomaly and latest_anomaly < 0 else "normal"
        st.metric("NDVI Anomaly", f"{latest_anomaly:+.2f}" if latest_anomaly else "—",
                  delta_color=anomaly_color)
    with col3:
        avg_ndvi = ndvi_df["ndvi"].mean()
        st.metric("Avg NDVI (Historical)", f"{avg_ndvi:.2f}" if avg_ndvi else "—")
    st.markdown('</div>', unsafe_allow_html=True)

    # ── NDVI Trend Chart ──
    st.markdown("""
        <div style="margin-top:1.5rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
            01 / Trend
          </div>
          <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.3rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:1rem;">
            NDVI Trend
          </h2>
        </div>
    """, unsafe_allow_html=True)

    fig = make_themed_figure()
    fig.add_trace(go.Scatter(
        x=ndvi_df["date"],
        y=ndvi_df["ndvi"],
        mode="lines",
        name="NDVI",
        line=dict(color=SAGE, width=2),
    ))

    if "anomaly" in ndvi_df.columns:
        fig.add_hline(
            y=ndvi_df["ndvi"].mean(),
            line_dash="dash",
            line_color=MUTED,
            annotation_text="Historical mean",
            annotation_position="right",
        )

    fig.update_layout(
        yaxis_title="NDVI",
        margin=dict(l=0, r=0, t=10, b=0),
        height=300,
    )
    st.markdown('<div class="crosshair-panel glass" style="padding:1rem;">', unsafe_allow_html=True)
    st.plotly_chart(fig, use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

    # ── Side-by-side: NDVI vs Rainfall ──
    if rainfall_df is not None and len(rainfall_df) > 0:
        st.markdown("""
            <div style="margin-top:1.5rem;">
              <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
                02 / Cross-Check
              </div>
              <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.3rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:1rem;">
                NDVI vs Rainfall
              </h2>
            </div>
        """, unsafe_allow_html=True)

        col1, col2 = st.columns(2)

        with col1:
            st.markdown('<div class="glass" style="padding:0.8rem;">', unsafe_allow_html=True)
            fig1 = make_themed_figure()
            fig1.add_trace(go.Scatter(
                x=ndvi_df["date"],
                y=ndvi_df["ndvi"],
                mode="lines",
                name="NDVI",
                line=dict(color=SAGE, width=2),
            ))
            fig1.update_layout(
                margin=dict(l=0, r=0, t=10, b=0),
                height=250,
                showlegend=False,
            )
            st.plotly_chart(fig1, use_container_width=True)
            st.markdown('</div>', unsafe_allow_html=True)

        with col2:
            st.markdown('<div class="glass" style="padding:0.8rem;">', unsafe_allow_html=True)
            fig2 = make_themed_figure()
            fig2.add_trace(go.Scatter(
                x=rainfall_df["date"],
                y=rainfall_df["rainfall_mm"],
                mode="lines",
                name="Rainfall",
                line=dict(color="#8FAE89", width=2),
            ))
            fig2.update_layout(
                margin=dict(l=0, r=0, t=10, b=0),
                height=250,
                showlegend=False,
            )
            st.plotly_chart(fig2, use_container_width=True)
            st.markdown('</div>', unsafe_allow_html=True)

        # Interpretation
        st.markdown("""
            <div class="interpretation-box">
                <strong>Cross-check interpretation:</strong> When NDVI declines coincide with
                rainfall deficit, the cause is likely drought stress. If NDVI drops while
                rainfall is normal, investigate other factors (pest, disease, soil).
            </div>
        """, unsafe_allow_html=True)

    # ── NDVI Anomaly Legend ──
    st.markdown("<hr style='border-color:rgba(255,255,255,0.07);margin:1.5rem 0;'>", unsafe_allow_html=True)
    st.markdown(f"""
    <div style="display:flex;gap:1.5rem;color:{MUTED};font-size:0.8rem;">
        <div>NDVI: <span style="color:{SAGE};">0.6–0.9</span> = healthy vegetation</div>
        <div>NDVI: <span style="color:{TURMERIC};">0.3–0.6</span> = moderate stress</div>
        <div>NDVI: <span style="color:{RUST};">0.0–0.3</span> = severe stress / bare soil</div>
    </div>
    <p style="color:{FAINT};font-size:0.75rem;margin-top:0.5rem;">
        Data source: Sentinel-2 / Copernicus Programme
    </p>
    """, unsafe_allow_html=True)
