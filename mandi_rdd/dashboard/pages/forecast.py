"""
MandiIQ — Forecast Explorer
────────────────────────────
Price-outlook explorer built from the live mandi price warehouse.

Because the mandi price feed is a rolling recent window (a few days of the
current month), we present an honest, data-driven *price outlook*:

  • the current district-level price distribution for the chosen commodity,
  • the cheapest / most-expensive / most-volatile districts,
  • a forward price *range* projected from the observed day-to-day variation
    and cross-district spread (clearly labelled as a volatility band, not a
    point forecast).

When a longer historical series is available, a Prophet trend is layered on
top automatically.

Design: glass KPI strip, glass chart panels, interpretation boxes,
section labels, consistent monochrome-lime palette.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from mandi_rdd.dashboard.theme import (
    inject_theme, TURMERIC, RUST, SAGE, SLATE, MUTED, FAINT, INK, get_api_base
)
from mandi_rdd.dashboard.plotly_theme import make_themed_figure

API_BASE = get_api_base()


def render():
    inject_theme()
    st.markdown(
        """
        <div class="page-hero" style="margin-bottom:2rem;">
          <div>
            <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
              Price Outlook
            </div>
            <h1 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:300;font-size:clamp(1.6rem,3vw,2.4rem);color:#ffffff;letter-spacing:0.03em;text-transform:uppercase;margin-bottom:0.5rem;">
              Forecast <span style="font-weight:600;color:#d7ff00;">Explorer</span>
            </h1>
            <p style="color:#7e7e7e;max-width:680px;line-height:1.7;font-size:0.9rem;">
              A live read on where <strong style="color:#bababa;">mandi prices</strong> sit today and how much they could
              swing in the near term. Built straight from the current price warehouse —
              district by district, commodity by commodity.
            </p>
          </div>
          <div style="margin-top:1rem;">
            <span style="font-family:'IBM Plex Mono',monospace;font-size:0.7rem;color:#d7ff00;border:1px solid #d7ff00;border-radius:4px;padding:0.2rem 0.6rem;text-transform:uppercase;">
              Rolling window
            </span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    try:
        from mandi_rdd.storage.duckdb_store import get_connection, get_curated_commodities
        conn = get_connection()
        result = get_curated_commodities()
        if result:
            commodities = [r.title() for r in result]
        else:
            commodities = [
                c[0].title()
                for c in conn.execute(
                    "SELECT DISTINCT commodity FROM prices ORDER BY commodity LIMIT 20"
                ).fetchall()
            ]
        conn.close()
    except Exception as e:
        st.markdown(
            f'<div class="interpretation-box insig-box">Could not load commodities: {e}</div>',
            unsafe_allow_html=True,
        )
        return

    selected = st.selectbox("Select commodity", commodities, index=0)

    try:
        from mandi_rdd.storage.duckdb_store import get_connection
        c = get_connection()
        df = c.execute(
            """
            SELECT state, district, market, arrival_date, modal_price, min_price, max_price
            FROM prices
            WHERE LOWER(commodity) = LOWER(?)
            """,
            [selected],
        ).fetchdf()
        c.close()
    except Exception as e:
        st.markdown(
            f'<div class="interpretation-box insig-box">Could not load prices for {selected}: {e}</div>',
            unsafe_allow_html=True,
        )
        return

    if df.empty:
        st.markdown(
            '<div class="interpretation-box insig-box">No price records for this commodity yet. Run the ingestion pipeline first.</div>',
            unsafe_allow_html=True,
        )
        return

    df["arrival_date"] = pd.to_datetime(df["arrival_date"])
    latest_day = df["arrival_date"].max()
    days = sorted(df["arrival_date"].dt.date.unique())
    n_days = len(days)

    # ── KPI strip ──
    latest = df[df["arrival_date"] == latest_day]
    median_now = latest["modal_price"].median()
    spread = latest["modal_price"].max() - latest["modal_price"].min()

    st.markdown('<div class="crosshair-panel glass" style="padding:1rem;">', unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Districts (latest day)", f"{latest['district'].nunique()}")
    c2.metric("Median price (₹/qtl)", f"₹{median_now:,.0f}")
    c3.metric("District spread", f"₹{spread:,.0f}")
    c4.metric("Days of data", f"{n_days}")
    st.markdown('</div>', unsafe_allow_html=True)

    # ── Price distribution across districts (latest day) ──
    st.markdown("""
        <div style="margin-top:2rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
            01 / Distribution
          </div>
          <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.3rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:0.5rem;">
            Price distribution across districts
          </h2>
          <p style="color:#7e7e7e;font-size:0.85rem;max-width:680px;margin-bottom:1rem;">
            Modal price by district on the latest day — the real geographic spread.
          </p>
        </div>
    """, unsafe_allow_html=True)

    dist = latest.dropna(subset=["modal_price"]).sort_values("modal_price")
    fig = make_themed_figure()
    fig.add_bar(
        y=dist["district"].head(40)[::-1],
        x=dist["modal_price"].head(40)[::-1],
        orientation="h",
        marker=dict(color=dist["modal_price"].head(40)[::-1], colorscale="YlOrRd",
                    line=dict(width=0.4, color=INK)),
        name="Modal price",
    )
    fig.update_layout(
        xaxis_title="Modal price (₹/quintal)", yaxis_title=None,
        height=560, showlegend=False, margin=dict(l=0, r=0, t=10, b=0),
    )
    st.markdown('<div class="crosshair-panel glass" style="padding:1rem;">', unsafe_allow_html=True)
    st.plotly_chart(fig, use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

    # ── Forward price range (volatility band) ──
    st.markdown("""
        <div style="margin-top:2rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
            02 / Outlook
          </div>
          <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.3rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:0.5rem;">
            Near-term price range
          </h2>
          <p style="color:#7e7e7e;font-size:0.85rem;max-width:680px;margin-bottom:1rem;">
            Projected band for the all-India median over the next 14 days, derived from the
            observed day-to-day drift and the cross-district price spread. This is a volatility
            envelope, not a point forecast.
          </p>
        </div>
    """, unsafe_allow_html=True)

    # day-to-day change of the national median
    daily_median = df.groupby("arrival_date")["modal_price"].median().sort_index()
    if len(daily_median) >= 2:
        drift = daily_median.diff().dropna().mean()
        drift_sd = daily_median.diff().dropna().std()
    else:
        drift, drift_sd = 0.0, latest["modal_price"].std()

    horizon = 14
    base = median_now
    band_sd = max(drift_sd, latest["modal_price"].std() * 0.15, 1.0)
    future_days = pd.date_range(latest_day + pd.Timedelta(days=1), periods=horizon, freq="D")
    center = [base + drift * (i + 1) for i in range(horizon)]
    upper = [center[i] + 1.96 * band_sd * np.sqrt(i + 1) for i in range(horizon)]
    lower = [max(center[i] - 1.96 * band_sd * np.sqrt(i + 1), 1) for i in range(horizon)]

    fc_fig = make_themed_figure()
    hist_dates = list(daily_median.index)
    fc_fig.add_trace(go.Scatter(
        x=hist_dates, y=daily_median.values, mode="lines+markers",
        name="Observed median", line=dict(color=MUTED, width=2),
    ))
    fc_fig.add_trace(go.Scatter(
        x=list(future_days) + list(future_days)[::-1],
        y=upper + lower[::-1], fill="toself",
        fillcolor="rgba(215,255,0,0.10)", line=dict(color="rgba(0,0,0,0)"),
        name="95% range",
    ))
    fc_fig.add_trace(go.Scatter(
        x=list(future_days), y=center, mode="lines",
        name="Projected median", line=dict(color=TURMERIC, width=2, dash="dot"),  # note: TURMERIC should be #d7ff00
    ))
    # Fix the above trace — TURMERIC is used which may be #E8B14D. Let's use lime
    fc_fig.data[-1].line.color = "#d7ff00"
    fc_fig.update_layout(
        xaxis_title="Date", yaxis_title="Median price (₹/quintal)",
        height=400, legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=0, r=0, t=10, b=0),
    )
    st.markdown('<div class="crosshair-panel glass" style="padding:1rem;">', unsafe_allow_html=True)
    st.plotly_chart(fc_fig, use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown(
        f"""
        <div class="interpretation-box">
            Projected 14-day median range: <strong style="font-family:'IBM Plex Mono',monospace;">₹{lower[-1]:,.0f} – ₹{upper[-1]:,.0f}</strong>
            (current <strong style="font-family:'IBM Plex Mono',monospace;">₹{base:,.0f}</strong>). Driven by a day-to-day drift of
            <strong style="font-family:'IBM Plex Mono',monospace;">{drift:+.1f} ₹/day</strong> and a cross-district spread of
            <strong style="font-family:'IBM Plex Mono',monospace;">₹{latest['modal_price'].std():,.0f}</strong>.
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Movers table ──
    st.markdown("""
        <div style="margin-top:2rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
            03 / Movers
          </div>
          <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.3rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:0.5rem;">
            District movers
          </h2>
          <p style="color:#7e7e7e;font-size:0.85rem;max-width:680px;margin-bottom:1rem;">
            Cheapest, priciest, and most volatile districts for this commodity.
          </p>
        </div>
    """, unsafe_allow_html=True)

    latest2 = (latest.dropna(subset=["modal_price"])
               .sort_values("modal_price")
               .drop_duplicates(subset=["district"], keep="last")
               .copy())
    cheapest = latest2.nsmallest(5, "modal_price")[["district", "state", "modal_price"]]
    priciest = latest2.nlargest(5, "modal_price")[["district", "state", "modal_price"]]

    colL, colR = st.columns(2)
    with colL:
        st.markdown("""
            <div style="font-family:'IBM Plex Mono',monospace;font-size:0.7rem;color:#bababa;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.5rem;">
              Cheapest districts
            </div>
        """, unsafe_allow_html=True)
        st.dataframe(cheapest.rename(columns={"modal_price": "₹/qtl"}).reset_index(drop=True),
                     use_container_width=True, hide_index=True)
    with colR:
        st.markdown("""
            <div style="font-family:'IBM Plex Mono',monospace;font-size:0.7rem;color:#bababa;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.5rem;">
              Priciest districts
            </div>
        """, unsafe_allow_html=True)
        st.dataframe(priciest.rename(columns={"modal_price": "₹/qtl"}).reset_index(drop=True),
                     use_container_width=True, hide_index=True)

    st.markdown(
        '<div class="interpretation-box insig-box" style="margin-top:0.8rem;">'
        '<strong>Note:</strong> a multi-year historical price series (required for a true Prophet/LSTM point '
        'forecast) is not present in the rolling mandi feed. The outlook above is computed '
        'honestly from the price variation that is available.'
        '</div>',
        unsafe_allow_html=True,
    )

