"""
MandiIQ — Procurement Advisor page.

Interactive prescriptive recommendation combining causal effect,
risk score, and forecast.

Design: glass KPI strip, interpretation boxes,
section headers, consistent monochrome-lime palette.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import streamlit as st
import pandas as pd
from mandi_rdd.dashboard.theme import inject_theme


def render(**kwargs):
    inject_theme()

    selected_commodity = "Onion"
    try:
        from mandi_rdd.storage.duckdb_store import get_curated_commodities
        result = get_curated_commodities()
        if result:
            selected_commodity = result[0].title()
    except Exception:
        pass

    st.markdown("""
        <div class="page-hero" style="margin-bottom:2rem;">
          <div>
            <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
              Decision Support
            </div>
            <h1 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:300;font-size:clamp(1.6rem,3vw,2.4rem);color:#ffffff;letter-spacing:0.03em;text-transform:uppercase;margin-bottom:0.5rem;">
              Procurement Risk <span style="font-weight:600;color:#d7ff00;">Advisor</span>
            </h1>
            <p style="color:#7e7e7e;max-width:680px;line-height:1.7;font-size:0.9rem;">
                Combines the causal effect size (how much prices jump at the −19% cutoff),
                the risk score (how likely a jump is next month), and the forecast
                (expected price path) into one actionable recommendation.
            </p>
          </div>
        </div>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns([1, 2])

    with col1:
        district = st.text_input("District (optional)", value="", placeholder="e.g., Nashik")

    try:
        from mandi_rdd.storage.duckdb_store import get_connection
        from mandi_rdd.analysis.prescriptive import compute_recommendation

        conn = get_connection()
        rec = compute_recommendation(
            conn,
            commodity=selected_commodity,
            district=district if district else None,
        )
        conn.close()

        with col2:
            if rec.get("confidence") == "high":
                st.markdown(f'<div class="interpretation-box" style="border-left-color:#d7ff00;">{rec.get("recommendation", "")}</div>', unsafe_allow_html=True)
            elif rec.get("confidence") == "moderate":
                st.markdown(f'<div class="interpretation-box" style="border-left-color:#D9663B;">{rec.get("recommendation", "")}</div>', unsafe_allow_html=True)
            else:
                st.markdown(f'<div class="interpretation-box insig-box">{rec.get("recommendation", "")}</div>', unsafe_allow_html=True)

        st.markdown("<hr style='border-color:rgba(255,255,255,0.07);margin:1.5rem 0;'>", unsafe_allow_html=True)

        st.markdown("""
            <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.8rem;">
              SUPPORTING DATA
            </div>
            <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.2rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:1rem;">
              Supporting Data
            </h2>
        """, unsafe_allow_html=True)

        st.markdown('<div class="crosshair-panel glass" style="padding:1rem;">', unsafe_allow_html=True)
        col_a, col_b, col_c, col_d = st.columns(4)
        with col_a:
            st.metric("RDD Effect", f"₹{rec.get('rdd_effect', 0):.2f}" if rec.get('rdd_effect') else "N/A")
        with col_b:
            st.metric("FE Effect", f"₹{rec.get('fe_effect', 0):.2f}" if rec.get('fe_effect') else "N/A")
        with col_c:
            st.metric("Risk Score", f"{rec.get('risk_score', 0):.1f}%" if rec.get('risk_score') else "N/A")
        with col_d:
            st.metric("Forecast Trend", f"{rec.get('forecast_trend_pct', 0):.1f}%" if rec.get('forecast_trend_pct') else "N/A")
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown(
            f"<div style='color:#7e7e7e;font-size:0.85rem;margin-top:0.5rem;'>"
            f"<strong>Confidence:</strong> {rec.get('confidence', 'low').upper()} "
            f"| <strong>Action:</strong> {rec.get('action', 'N/A')}"
            f"<br/><em>Recommendation generated from {len([k for k in ['rdd_effect', 'fe_effect', 'risk_score', 'forecast_trend_pct'] if rec.get(k) is not None])} of 4 data sources.</em>"
            f"</div>",
            unsafe_allow_html=True,
        )

    except Exception as e:
        st.markdown(
            f'<div class="interpretation-box insig-box">Recommendation unavailable: {e}</div>',
            unsafe_allow_html=True,
        )
        st.markdown("""
            <div class="glass" style="padding:2rem;text-align:center;border:none;">
                <div style="font-size:2rem;">🛒</div>
                <div style="font-size:1rem;margin-top:0.5rem;color:#7e7e7e;">Run the nightly pipeline to generate recommendations.</div>
            </div>
        """, unsafe_allow_html=True)

