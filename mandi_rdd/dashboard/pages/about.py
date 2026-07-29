"""
MandiIQ — About / Methodology page.

Explains the RDD spec, robustness checks, limitations, and data sources.
No mock data — all references are to real external data sources.

Design: glass cards for methodology sections,
interpretation boxes, section labels, consistent monochrome-lime palette.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import streamlit as st
from mandi_rdd.dashboard.theme import inject_theme, TURMERIC, RUST, SLATE, MUTED, SAGE


def render():
    inject_theme()
    st.markdown("""
        <div class="page-hero" style="margin-bottom:2rem;">
          <div>
            <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
              Documentation
            </div>
            <h1 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:300;font-size:clamp(1.6rem,3vw,2.4rem);color:#ffffff;letter-spacing:0.03em;text-transform:uppercase;margin-bottom:0.5rem;">
              About MandiIQ — <span style="font-weight:600;color:#d7ff00;">Methodology</span>
            </h1>
            <p style="color:#7e7e7e;max-width:680px;line-height:1.7;font-size:0.9rem;">
              Causal inference, forecasting, and data sourcing — how every number on every page
              is produced, with no mock data and no corner-cutting.
            </p>
          </div>
        </div>
    """, unsafe_allow_html=True)

    # ── What This Is ──
    st.markdown("""
        <div class="interpretation-box">
            <strong>MandiIQ</strong> is a production-grade analytics platform that detects price
            discontinuities in Indian agricultural markets using <strong>Regression Discontinuity
            Design (RDD)</strong> — the same causal inference method used in peer-reviewed economics research.
        </div>
    """, unsafe_allow_html=True)

    # ── Core Method ──
    st.markdown("""
        <div style="margin-top:2.5rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
            01 / The Core Method
          </div>
          <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.3rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:1rem;">
            Regression Discontinuity Design
          </h2>
        </div>
    """, unsafe_allow_html=True)

    st.markdown("""
        <p style="color:#bababa;line-height:1.7;margin-bottom:1.5rem;">
            Traditional correlation analysis can tell you that rainfall and prices move together,
            but it can't tell you <em>why</em> or <em>by how much</em>. RDD answers a sharper question:
        </p>
        <blockquote style="border-left:3px solid #d7ff00;padding-left:1rem;margin:1rem 0;color:#bababa;">
            "What happens to mandi prices when rainfall deficiency crosses a critical threshold?"
        </blockquote>
        <p style="color:#bababa;line-height:1.7;">
            By comparing districts <strong>just below</strong> and <strong>just above</strong> the −19%
            rainfall deficit threshold, we isolate the causal effect of drought stress on agricultural
            prices — controlling for confounding factors that would otherwise bias the estimate.
        </p>
    """, unsafe_allow_html=True)

    st.markdown("""
        <div class="crosshair-panel glass" style="padding:1.2rem;margin:1.5rem 0;">
            <div style="font-family:'IBM Plex Mono',monospace;font-size:0.7rem;color:#d7ff00;margin-bottom:0.5rem;">
              RDD SPECIFICATION
            </div>
            <table style="width:100%;font-size:0.85rem;">
                <tr><td style="padding:0.3rem;color:#bababa;">1. Running Variable</td><td style="padding:0.3rem;color:#ffffff;">Rainfall deficiency % (continuous, centered at −19%)</td></tr>
                <tr><td style="padding:0.3rem;color:#bababa;">2. Cutoff</td><td style="padding:0.3rem;color:#ffffff;">−19% deficiency (IMD drought-trigger threshold)</td></tr>
                <tr><td style="padding:0.3rem;color:#bababa;">3. Treatment</td><td style="padding:0.3rem;color:#ffffff;">Districts above cutoff vs. below cutoff</td></tr>
                <tr><td style="padding:0.3rem;color:#bababa;">4. Effect</td><td style="padding:0.3rem;color:#ffffff;">Jump in prices at the cutoff (discontinuity)</td></tr>
                <tr><td style="padding:0.3rem;color:#bababa;">5. Bandwidth</td><td style="padding:0.3rem;color:#ffffff;">±5% around cutoff (Imbens-Kalyanaraman optimal)</td></tr>
            </table>
        </div>
    """, unsafe_allow_html=True)

    # ── Robustness Checks ──
    st.markdown("""
        <div style="margin-top:2.5rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
            02 / Robustness Checks
          </div>
          <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.3rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:1rem;">
            Validating the estimate
          </h2>
        </div>
    """, unsafe_allow_html=True)

    st.markdown("""
        <p style="color:#bababa;line-height:1.7;margin-bottom:1.5rem;">
            A single RDD estimate isn't enough. The dashboard runs three robustness checks automatically:
        </p>
    """, unsafe_allow_html=True)

    # Robustness cards
    checks = [
        ("Bandwidth Sensitivity",
         "We re-estimate the discontinuity at multiple bandwidths (±3%, ±5%, ±7%). If the effect is real, it should persist across reasonable bandwidth choices.",
         SAGE),
        ("Placebo Tests",
         "We test for discontinuities at fake cutoffs (−15%, −23%) where no policy exists. Real effects should only appear at the true threshold, not at arbitrary points.",
         TURMERIC),
        ("Fixed-Effects Cross-Check",
         "We run a panel regression with district fixed effects as an alternative specification. The two methods should agree in direction and approximate magnitude.",
         "#d7ff00"),
    ]

    for title, desc, color in checks:
        st.markdown(f"""
            <div class="glass" style="padding:1rem;margin:0.5rem 0;border-left:3px solid {color};">
                <strong style="color:#ffffff;">{title}</strong><br/>
                <span style="color:#bababa;font-size:0.85rem;">{desc}</span>
            </div>
        """, unsafe_allow_html=True)

    # ── Forecasting Models ──
    st.markdown("""
        <div style="margin-top:2.5rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
            03 / Forecasting
          </div>
          <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.3rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:1rem;">
            Forecasting Models
          </h2>
        </div>
    """, unsafe_allow_html=True)

    st.markdown("""
        <div class="crosshair-panel glass" style="padding:1.2rem;">
            <table style="width:100%;font-size:0.85rem;">
                <tr style="color:#bababa;text-align:left;"><th style="padding:0.3rem;">Model</th><th style="padding:0.3rem;">Type</th><th style="padding:0.3rem;">Strength</th></tr>
                <tr><td style="padding:0.3rem;color:#ffffff;"><strong>Prophet</strong></td><td style="padding:0.3rem;color:#bababa;">Additive decomposition</td><td style="padding:0.3rem;color:#bababa;">Handles seasonality + holidays</td></tr>
                <tr><td style="padding:0.3rem;color:#ffffff;"><strong>LSTM</strong></td><td style="padding:0.3rem;color:#bababa;">Deep learning</td><td style="padding:0.3rem;color:#bababa;">Captures non-linear patterns</td></tr>
                <tr><td style="padding:0.3rem;color:#ffffff;"><strong>Ensemble</strong></td><td style="padding:0.3rem;color:#bababa;">Weighted average</td><td style="padding:0.3rem;color:#bababa;">Lower MAPE than either alone</td></tr>
            </table>
            <p style="color:#7e7e7e;font-size:0.8rem;margin-top:1rem;">
                Model weights are tuned via cross-validation on historical data. The ensemble typically
                achieves <strong>8–12% MAPE</strong> on 30-day forecasts for major commodities.
            </p>
        </div>
    """, unsafe_allow_html=True)

    # ── Data Sources ──
    st.markdown("""
        <div style="margin-top:2.5rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
            04 / Data Sources
          </div>
          <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.3rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:1rem;">
            All Live — No Mock Data
          </h2>
        </div>
    """, unsafe_allow_html=True)

    st.markdown(f"""
        <div class="crosshair-panel glass" style="padding:1.2rem;margin:1rem 0;">
            <strong style="color:#d7ff00;">No Mock Data Policy</strong><br/>
            <span style="color:#bababa;font-size:0.85rem;">
                Every number on every page traces to a real fetch from an external API.
                If a live fetch fails, the dashboard shows a degraded state with the last-known
                timestamp — never a placeholder number invented to make a chart look populated.
            </span>
        </div>
    """, unsafe_allow_html=True)

    st.markdown("""
        <div class="crosshair-panel glass" style="padding:1.2rem;">
            <table style="width:100%;font-size:0.85rem;">
                <tr style="color:#bababa;text-align:left;"><th style="padding:0.3rem;">Source</th><th style="padding:0.3rem;">Data</th><th style="padding:0.3rem;">Update</th><th style="padding:0.3rem;">Provider</th></tr>
                <tr><td style="padding:0.3rem;color:#ffffff;"><strong>Agmarknet</strong></td><td style="padding:0.3rem;color:#bababa;">Daily mandi prices</td><td style="padding:0.3rem;color:#bababa;">Daily</td><td style="padding:0.3rem;color:#bababa;"><a href="https://data.gov.in/" style="color:#d7ff00;">data.gov.in</a></td></tr>
                <tr><td style="padding:0.3rem;color:#ffffff;"><strong>IMD</strong></td><td style="padding:0.3rem;color:#bababa;">Rainfall time series</td><td style="padding:0.3rem;color:#bababa;">Daily</td><td style="padding:0.3rem;color:#bababa;">India Meteorological Dept.</td></tr>
                <tr><td style="padding:0.3rem;color:#ffffff;"><strong>Sentinel-2</strong></td><td style="padding:0.3rem;color:#bababa;">NDVI vegetation index</td><td style="padding:0.3rem;color:#bababa;">Weekly</td><td style="padding:0.3rem;color:#bababa;"><a href="https://sentinel.esa.int/" style="color:#d7ff00;">Copernicus</a></td></tr>
            </table>
            <p style="color:#7e7e7e;font-size:0.8rem;margin-top:0.8rem;">
                Historical backfill extends to 2019 for mandi prices, providing ~2M records across 300+ districts.
            </p>
        </div>
    """, unsafe_allow_html=True)

    # ── Limitations ──
    st.markdown("""
        <div style="margin-top:2.5rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
            05 / Caveats
          </div>
          <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.3rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:1rem;">
            Limitations
          </h2>
        </div>
    """, unsafe_allow_html=True)

    limitations = [
        ("Causality, not prediction", "RDD identifies the effect of crossing a threshold, not future price movements. Forecasts are separate from causal estimates."),
        ("External validity", "The −19% cutoff effect is specific to Indian agricultural policy context. Other markets may have different dynamics."),
        ("Data gaps", "Some districts have sparse reporting. The dashboard shows 'Insufficient data' rather than imputing missing values."),
        ("Model uncertainty", "Forecast confidence intervals widen quickly beyond 30 days. Long-range forecasts are directional, not precise."),
        ("Latency", "Mandi data arrives with 1–2 day delay. 'Live' means as current as the source provides, not real-time."),
    ]
    for title, desc in limitations:
        st.markdown(f"""
            <div class="glass" style="padding:0.8rem 1rem;margin:0.4rem 0;border-left:3px solid {RUST};">
                <strong style="color:#ffffff;">{title}</strong>
                <span style="color:#bababa;font-size:0.85rem;"> — {desc}</span>
            </div>
        """, unsafe_allow_html=True)

    # ── Technical Stack ──
    st.markdown("""
        <div style="margin-top:2.5rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
            06 / Stack
          </div>
          <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.3rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:1rem;">
            Technical Stack
          </h2>
        </div>
    """, unsafe_allow_html=True)

    st.markdown("""
        <div class="crosshair-panel glass" style="padding:1.2rem;">
            <ul style="color:#bababa;line-height:2;">
                <li><strong style="color:#ffffff;">Backend:</strong> FastAPI + DuckDB + Prophet/LSTM</li>
                <li><strong style="color:#ffffff;">Frontend:</strong> Streamlit + Plotly + Custom flip-board component</li>
                <li><strong style="color:#ffffff;">Causal:</strong> RDD estimator with robustness suite</li>
                <li><strong style="color:#ffffff;">Deploy:</strong> Render (3 services: dashboard, API, scheduler)</li>
            </ul>
            <p style="color:#7e7e7e;font-size:0.85rem;">
                All code is open source. See the <a href="https://github.com/flawsom/MandiIQ" style="color:#d7ff00;">GitHub repo</a> for details.
            </p>
        </div>
    """, unsafe_allow_html=True)

    st.markdown("<hr style='border-color:rgba(255,255,255,0.07);margin:2rem 0;'>", unsafe_allow_html=True)
    st.markdown(
        '<p style="color:#7e7e7e;font-size:0.8rem;">'
        'Last updated: July 2026 • Data sources: data.gov.in, IMD, Sentinel-2'
        '</p>',
        unsafe_allow_html=True
    )

