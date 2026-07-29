"""
MandiIQ — Causal Explorer page.

RDD discontinuity plot, bandwidth sensitivity, placebo tests,
density check, fixed-effects cross-check — the full causal story.

Design: crosshair-panel glass cards, section labels,
glass KPI strip, interpretation boxes, consistent monochrome-lime palette.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from mandi_rdd.dashboard.theme import inject_theme, commodity_color
from mandi_rdd.dashboard.plotly_theme import make_themed_figure


def make_discontinuity_plot(plot_data: dict, commodity: str = "onion") -> go.Figure:
    color = commodity_color(commodity)
    fig = make_themed_figure()
    if "raw_x" in plot_data and "raw_y" in plot_data:
        fig.add_trace(go.Scatter(x=plot_data["raw_x"], y=plot_data["raw_y"], mode="markers", marker=dict(color="rgba(139,150,163,0.3)", size=4), name="Raw data", hovertemplate="Departure: %{x:.1f}%<br>Price: ₹%{y:.0f}<extra></extra>"))
    if "bin_centers" in plot_data and "bin_means" in plot_data:
        fig.add_trace(go.Scatter(x=plot_data["bin_centers"], y=plot_data["bin_means"], mode="markers+lines", marker=dict(color="#ffffff", size=8, line=dict(color="#000000", width=1)), line=dict(color="rgba(242,239,230,0.3)", width=1), name="Binned avg", error_y=dict(type="data", array=plot_data.get("bin_stds", []), visible=True, color="rgba(139,150,163,0.3)", thickness=1)))
    if "left_x" in plot_data and "left_y" in plot_data:
        fig.add_trace(go.Scatter(x=plot_data["left_x"], y=plot_data["left_y"], mode="lines", line=dict(color=color, width=3), name="Left fit"))
    if "right_x" in plot_data and "right_y" in plot_data:
        fig.add_trace(go.Scatter(x=plot_data["right_x"], y=plot_data["right_y"], mode="lines", line=dict(color="#d7ff00", width=3), name="Right fit"))
    cutoff = plot_data.get("cutoff", -19.0)
    fig.add_vline(x=cutoff, line=dict(color="#d7ff00", width=2, dash="dash"), annotation_text=f" Cutoff: {cutoff}%", annotation_position="top left", annotation_font=dict(color="#d7ff00", size=12))
    fig.add_hline(y=0, line=dict(color="rgba(255,255,255,0.1)", width=1))
    fig.update_layout(hovermode="closest", margin=dict(l=40, r=20, t=30, b=50), xaxis=dict(title="Rainfall Departure from Normal (%)", gridcolor="rgba(255,255,255,0.05)"), yaxis=dict(title="Avg Modal Price (₹)", gridcolor="rgba(255,255,255,0.05)"), font=dict(color="#bababa"), height=500)
    return fig


def render(**kwargs):
    inject_theme()

    selected_commodity = kwargs.get("commodity", "Onion")
    rdd_result = kwargs.get("rdd_result", {})
    plot_data = kwargs.get("plot_data", {})

    st.markdown("""
        <div class="page-hero" style="margin-bottom:2rem;">
          <div>
            <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
              Causal Inference
            </div>
            <h1 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:300;font-size:clamp(1.6rem,3vw,2.4rem);color:#ffffff;letter-spacing:0.03em;text-transform:uppercase;margin-bottom:0.5rem;">
              Causal <span style="font-weight:600;color:#d7ff00;">Explorer</span>
            </h1>
            <p style="color:#7e7e7e;max-width:680px;line-height:1.7;font-size:0.9rem;">
              Discontinuity plot, bandwidth sensitivity, placebo tests, density check,
              and fixed-effects cross-check — the full causal story for every commodity.
            </p>
          </div>
        </div>
    """, unsafe_allow_html=True)

    # ── KPI strip ──
    st.markdown('<div class="crosshair-panel glass" style="padding:1rem;">', unsafe_allow_html=True)
    col1, col2, col3, col4 = st.columns(4)
    effect = rdd_result.get("effect")
    p_val = rdd_result.get("p_value")
    with col1:
        st.metric("RDD Effect (₹)", f"₹{effect:.2f}" if effect else "N/A")
    with col2:
        st.metric("P-Value", f"{p_val:.4f}" if p_val else "N/A")
    with col3:
        st.metric("FE Cross-Check (₹)", f"₹{rdd_result.get('fe_effect', 0):.2f}" if rdd_result.get('fe_effect') else "N/A")
    with col4:
        st.metric("Observations", f"{rdd_result.get('n_total', 0):,}")
    st.markdown('</div>', unsafe_allow_html=True)

    # ── Discontinuity plot ──
    st.markdown("""
        <div style="margin-top:2rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
            01 / Discontinuity
          </div>
          <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.3rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:1rem;">
            RDD Discontinuity Plot
          </h2>
        </div>
    """, unsafe_allow_html=True)

    if "error" not in plot_data:
        fig = make_discontinuity_plot(plot_data, selected_commodity)
        st.markdown('<div class="crosshair-panel glass" style="padding:1rem;">', unsafe_allow_html=True)
        st.plotly_chart(fig, use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)
    else:
        st.markdown(
            f'<div class="interpretation-box insig-box">Plot unavailable: {plot_data.get("error", "unknown")}</div>',
            unsafe_allow_html=True,
        )

    # ── Robustness Checks ──
    st.markdown("""
        <div style="margin-top:2.5rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
            02 / Robustness
          </div>
          <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.3rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:1rem;">
            Robustness Framework
          </h2>
        </div>
    """, unsafe_allow_html=True)

    tab_bw, tab_pl, tab_de, tab_cv = st.tabs(["Bandwidth Sensitivity", "Placebo Tests", "Density Test", "Covariate Balance"])

    with tab_bw:
        bw = rdd_result.get("bandwidth_sensitivity", [])
        if bw:
            rows = []
            for r in bw:
                if not r.get("effect"):
                    continue
                e = r["effect"]
                p = r.get("p_value")
                sig = p is not None and p < 0.05
                rows.append(
                    f'<tr><td style="font-family:IBM Plex Mono;padding:0.5rem 1rem;">{r["bandwidth"]:.1f}%</td>'
                    f'<td style="font-family:IBM Plex Mono;color:#d7ff00;padding:0.5rem 1rem;">₹{e:.2f}</td>'
                    f'<td style="font-family:IBM Plex Mono;color:#bababa;padding:0.5rem 1rem;">{"{:.4f}".format(p) if p else "N/A"}</td>'
                    f'<td style="color:{"#d7ff00" if sig else "#D9663B"};padding:0.5rem 1rem;">{"✅" if sig else "⚠️"}</td></tr>'
                )
            html = (
                '<div class="crosshair-panel glass" style="padding:0;overflow:hidden;">'
                '<table style="width:100%;border-collapse:collapse;font-size:0.85rem;">'
                '<thead><tr style="color:#bababa;text-align:left;"><th style="padding:0.5rem 1rem;">BW (%)</th><th style="padding:0.5rem 1rem;">Effect (₹)</th><th style="padding:0.5rem 1rem;">P</th><th style="padding:0.5rem 1rem;">Sig</th></tr></thead>'
                '<tbody>' + "".join(rows) + '</tbody></table></div>'
            )
            st.markdown(html, unsafe_allow_html=True)
            effects = [r["effect"] for r in bw if r.get("effect")]
            if len(effects) >= 3:
                stable = all(e > 0 for e in effects) or all(e < 0 for e in effects)
                st.markdown(
                    f'<div class="interpretation-box" style="margin-top:0.5rem;">'
                    f'{"✅ Effect stable across bandwidths" if stable else "⚠️ Effect changes sign"}'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    with tab_pl:
        for p in rdd_result.get("placebo_tests", [])[:4]:
            pc = p.get("placebo_cutoff", "?")
            pe = p.get("effect")
            pp = p.get("p_value")
            sig = pp is not None and pp < 0.05
            st.markdown(
                f'<div class="crosshair-panel glass" style="padding:0.6rem 1rem;margin-bottom:0.4rem;">'
                f'<span style="font-family:IBM Plex Mono,monospace;">Placebo at {pc:.1f}%</span> → '
                f'Effect: {"₹{:.2f}".format(pe) if pe else "N/A"} | '
                f'P: {"{:.4f}".format(pp) if pp else "N/A"} '
                f'<span style="color:{"#d7ff00" if not sig else "#D9663B"};">{"✅" if not sig else "❌"}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

    with tab_de:
        d = rdd_result.get("density_test", {})
        if d.get("density_p_value") is not None:
            passed = d["density_p_value"] > 0.05
            st.markdown(
                f'<div class="interpretation-box" style="{"border-left-color:#d7ff00;" if passed else "border-left-color:#D9663B;"}">'
                f'{"✅ No evidence of manipulation" if passed else "⚠️ Possible manipulation (p={:.3f})".format(d["density_p_value"])}'
                f'</div>',
                unsafe_allow_html=True,
            )

    with tab_cv:
        for name, b in (rdd_result.get("covariate_balance", {}) or {}).items():
            bp = b.get("p_value")
            passed = bp is None or bp > 0.05
            bp_str = "{:.4f}".format(bp) if bp else "N/A"
            st.markdown(
                f'<div class="crosshair-panel glass" style="padding:0.6rem 1rem;margin-bottom:0.4rem;">'
                f'<span style="font-family:IBM Plex Mono,monospace;">{name}</span> → P: {bp_str} '
                f'<span style="color:{"#d7ff00" if passed else "#D9663B"};">{"✅" if passed else "❌"}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

    st.markdown("<hr style='border-color:rgba(255,255,255,0.07);margin:2rem 0;'>", unsafe_allow_html=True)
    st.markdown(
        '<p style="color:#7e7e7e;font-size:0.8rem;">'
        'Causal estimates use the Imbens-Kalyanaraman optimal bandwidth • '
        '<a href="https://en.wikipedia.org/wiki/Regression_discontinuity_design" style="color:#d7ff00;">Learn about RDD</a>'
        '</p>',
        unsafe_allow_html=True,
    )

