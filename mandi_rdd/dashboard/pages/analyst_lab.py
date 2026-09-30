"""
MandiIQ - Analyst Lab page.

Quantitative research cockpit for one commodity:
- distribution-free conformal intervals around the forecast
- drift + data-quality monitoring
- EVT tail risk (VaR / CVaR / drawdown)
- cross-fitted debiased-ML rainfall sensitivity
- Kalman month-end nowcast for the incomplete reporting month

All sections come from the composite /analytics/{commodity} endpoint and
degrade independently.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from mandi_rdd.dashboard.data_access import get_analytics
from mandi_rdd.dashboard.plotly_theme import make_themed_figure
from mandi_rdd.dashboard.theme import commodity_color, inject_theme

FALLBACK_COMMODITIES = [
    "Onion", "Tomato", "Potato", "Cabbage", "Cauliflower",
    "Wheat", "Paddy(Common)", "Maize", "Soyabean",
]

TURMERIC = "#d7ff00"
RUST = "#ff5c39"


@st.cache_data(ttl=600, show_spinner=False)
def _load_report(commodity: str) -> dict:
    return get_analytics(commodity)


def _section_label(text: str) -> None:
    st.markdown(
        f'<div style="font-family:\'IBM Plex Mono\',monospace;font-size:0.72rem;'
        f'color:{TURMERIC};text-transform:uppercase;letter-spacing:0.1em;'
        f'margin:1.6rem 0 0.5rem;">{text}</div>',
        unsafe_allow_html=True,
    )


def _error_box(message: str) -> None:
    st.markdown(
        f'<div class="interpretation-box insig-box">Unavailable: {message}</div>',
        unsafe_allow_html=True,
    )


def _conformal_section(payload: dict, color: str) -> None:
    if "error" in payload or payload.get("half_width") is None:
        _error_box(payload.get("error", "no forecast available"))
        return

    cols = st.columns(4)
    cols[0].metric("Coverage target", f"{(1 - payload['alpha']) * 100:.0f}%")
    cols[1].metric("Calibration points", f"{payload['calibration_points']}")
    cols[2].metric("Interval half-width", f"₹{payload['half_width']:.0f}")
    cols[3].metric("Calibration coverage", f"{payload['calibration_coverage'] * 100:.1f}%")

    lower, upper = payload.get("lower", []), payload.get("upper", [])
    if not lower:
        return
    x = list(range(1, len(lower) + 1))
    fig = make_themed_figure(height=280)
    fig.add_trace(go.Scatter(x=x, y=upper, mode="lines", line=dict(width=0),
                             showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=x, y=lower, mode="lines", line=dict(width=0),
                             fill="tonexty", fillcolor="rgba(215,255,0,0.12)",
                             showlegend=False, hoverinfo="skip"))
    mid = [(lo + hi) / 2 for lo, hi in zip(lower, upper)]
    fig.add_trace(go.Scatter(x=x, y=mid, mode="lines+markers",
                             line=dict(color=color, width=2), name="Forecast"))
    fig.update_layout(xaxis_title="Forecast month", yaxis_title="Modal price (₹/quintal)")
    st.plotly_chart(fig, use_container_width=True)


def _drift_section(payload: dict) -> None:
    if "error" in payload:
        _error_box(payload["error"])
        return
    quality = payload.get("data_quality", {})
    psi = payload.get("psi", {})
    ks = payload.get("ks", {})
    ph = payload.get("page_hinkley", {})
    ewma = payload.get("ewma", {})

    cols = st.columns(5)
    cols[0].metric("Data quality", f"{quality.get('score', '-')}/100")
    cols[1].metric("PSI", f"{psi.get('psi', '-')} ({psi.get('verdict', '-')})")
    cols[2].metric("KS drift", "yes" if ks.get("drift_detected") else "no")
    cols[3].metric("PH change points", f"{ph.get('n_alarms', '-')}")
    cols[4].metric("EWMA flags", f"{ewma.get('n_out_of_control', '-')}")

    detail = pd.DataFrame(
        [
            {"check": "Completeness", "value": quality.get("completeness")},
            {"check": "Staleness (days)", "value": quality.get("staleness_days")},
            {"check": "Repeat price share", "value": quality.get("repeat_price_share")},
            {"check": "Outlier share", "value": quality.get("outlier_share")},
            {"check": "KS p-value", "value": ks.get("p_value")},
        ]
    )
    st.dataframe(detail, use_container_width=True, hide_index=True)


def _tail_risk_section(payload: dict) -> None:
    if "error" in payload:
        _error_box(payload["error"])
        return
    var95 = payload.get("var_95", {})
    var99 = payload.get("var_99", {})
    evt = payload.get("evt_99", {})
    dd = payload.get("max_drawdown", {})

    cols = st.columns(5)
    cols[0].metric("1-day VaR 95%", f"{var95.get('var', 0) * 100:.2f}%")
    cols[1].metric("1-day CVaR 95%", f"{var95.get('cvar', 0) * 100:.2f}%")
    cols[2].metric("1-day VaR 99%", f"{var99.get('var', 0) * 100:.2f}%")
    cols[3].metric("EVT VaR 99%", "-" if "error" in evt else f"{evt.get('var', 0) * 100:.2f}%")
    cols[4].metric("Max drawdown", f"{dd.get('max_drawdown_pct', '-')}%")
    if "error" not in evt:
        st.caption(
            f"GPD tail index ξ = {evt.get('shape_xi')} (positive means heavy tail); "
            f"EVT expected shortfall = {evt.get('expected_shortfall', 0) * 100:.2f}%."
        )


def _dml_section(payload: dict) -> None:
    if "error" in payload:
        _error_box(payload["error"])
        return
    cols = st.columns(4)
    cols[0].metric("θ per 1pp rainfall", f"{payload['theta']:.4f}")
    cols[1].metric("Effect at +10pp", f"{payload['effect_10pp_pct']:+.2f}%")
    cols[2].metric("p-value", f"{payload['p_value']:.4f}")
    cols[3].metric("First-stage R²", f"{payload['first_stage_r2']}")
    st.caption(payload.get("interpretation", ""))


def _nowcast_section(payload: dict) -> None:
    if "error" in payload:
        _error_box(payload["error"])
        return
    cols = st.columns(4)
    cols[0].metric("Month-end nowcast", f"₹{payload['nowcast_price']:.0f}")
    cols[1].metric("90% band", f"₹{payload['ci_lower']:.0f} to ₹{payload['ci_upper']:.0f}")
    cols[2].metric("Month reported", f"{payload['completion_pct']:.0f}%")
    cols[3].metric("Days remaining", f"{payload['days_remaining']}")


def render(**kwargs):
    inject_theme()

    st.markdown(
        """
        <div class="page-hero" style="margin-bottom:2rem;">
          <div>
            <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
              Quantitative Research
            </div>
            <h1 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:300;font-size:clamp(1.6rem,3vw,2.4rem);color:#ffffff;letter-spacing:0.03em;text-transform:uppercase;margin-bottom:0.5rem;">
              Analyst <span style="font-weight:600;color:#d7ff00;">Lab</span>
            </h1>
            <p style="color:#7e7e7e;max-width:720px;line-height:1.7;font-size:0.9rem;">
              Distribution-free conformal intervals, drift and data-quality monitoring,
              extreme-value tail risk, cross-fitted debiased ML, and Kalman month-end nowcasts.
            </p>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    options = FALLBACK_COMMODITIES
    try:
        from mandi_rdd.storage.duckdb_store import get_curated_commodities
        curated = get_curated_commodities()
        if curated:
            options = [c for c in curated if isinstance(c, str)]
    except Exception:
        pass

    commodity = st.selectbox("Commodity", options, index=0)
    color = commodity_color(commodity)

    with st.spinner(f"Running the analytics stack for {commodity}..."):
        report = _load_report(commodity)

    if "error" in report and "sections" not in report:
        _error_box(report["error"])
        return

    headline = report.get("headline", "")
    if headline:
        st.markdown(
            f'<div class="interpretation-box" style="margin-bottom:1rem;">{headline}</div>',
            unsafe_allow_html=True,
        )

    sections = report.get("sections", {})
    _section_label("Uncertainty - conformal prediction")
    _conformal_section(sections.get("conformal", {}), color)

    _section_label("Reliability - drift and data quality")
    _drift_section(sections.get("drift", {}))

    _section_label("Tail risk - VaR, CVaR and drawdown")
    _tail_risk_section(sections.get("tail_risk", {}))

    _section_label("Causal sensitivity - debiased ML")
    _dml_section(sections.get("dml", {}))

    _section_label("Nowcast - Kalman month-end projection")
    _nowcast_section(sections.get("nowcast", {}))
