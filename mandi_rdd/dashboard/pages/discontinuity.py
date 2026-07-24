import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

"""
MandiIQ — Rainfall Discontinuity (Regression Discontinuity Design)
─────────────────────────────────────────────────────────────────
RDD on India's IMD rainfall-deficit classification.

Alche Studio Design: glass KPI strip, crosshair chart panels,
interpretation boxes, consistent monochrome-lime palette.
"""

import numpy as np
import os
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from mandi_rdd.dashboard.theme import (
    inject_theme, TURMERIC, RUST, SAGE, SLATE, MUTED, FAINT, INK, get_api_base
)
from mandi_rdd.dashboard.plotly_theme import make_themed_figure
from mandi_rdd.ingestion.fetch_rainfall import fetch_all_india_monsoon

API_BASE = get_api_base()

CUTOFF = -19.0  # IMD deficient-rainfall threshold (% departure from normal)


def commodity_list(conn) -> list:
    """Data-driven commodity list (no hardcoded fallbacks)."""
    from mandi_rdd.storage.duckdb_store import get_curated_commodities
    result = get_curated_commodities()
    if result:
        return [r.title() for r in result]
    rows = conn.execute(
        "SELECT DISTINCT commodity FROM prices ORDER BY commodity LIMIT 20"
    ).fetchall()
    return [r[0].title() for r in rows] or ["Onion"]


def load_rainfall(conn) -> pd.DataFrame:
    return conn.execute(
        """
        SELECT sub_division, year, month, rainfall_mm, normal_mm, departure_pct
        FROM rainfall
        WHERE departure_pct BETWEEN -100 AND 200
        """
    ).fetchdf()


def render():
    inject_theme()

    # ── Hero Header ──
    st.markdown(
        """
        <div class="page-hero" style="margin-bottom:2rem;">
          <div>
            <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
              Regression Discontinuity Design
            </div>
            <h1 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:300;font-size:clamp(1.6rem,3vw,2.4rem);color:#ffffff;letter-spacing:0.03em;text-transform:uppercase;">
              Rainfall Deficit <span style="font-weight:600;color:#d7ff00;">Discontinuity</span>
            </h1>
            <p style="color:#7e7e7e;max-width:720px;line-height:1.7;font-size:0.9rem;margin-top:0.5rem;">
              India's IMD flags a subdivision as <strong style="color:#bababa;">deficient</strong> when monsoon rainfall
              departs more than <strong style="color:#bababa;">−19%</strong> from the long-period normal. That hard cutoff is
              a natural regression-discontinuity threshold. We test whether the density of
              rainfall departures drops off a cliff at −19% — and how deficit exposure
              has shifted across the monsoon years.
            </p>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    try:
        from mandi_rdd.storage.duckdb_store import get_connection
        conn = get_connection()
        df = load_rainfall(conn)
        conn.close()
    except Exception as e:
        st.markdown(
            f'<div class="interpretation-box insig-box">Could not load rainfall series: {e}</div>',
            unsafe_allow_html=True,
        )
        return

    if df.empty:
        st.markdown(
            '<div class="interpretation-box insig-box">No rainfall departure data available yet. '
            'Run the ingestion pipeline to populate the warehouse.</div>',
            unsafe_allow_html=True,
        )
        return

    # ── KPI strip with glass cards ──
    n_obs = len(df)
    n_deficit = int((df["departure_pct"] < CUTOFF).sum())
    deficit_share = n_deficit / n_obs
    subs = df["sub_division"].nunique()

    st.markdown(
        """
        <div style="font-family:'IBM Plex Mono',monospace;font-size:0.7rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.8rem;">
          Warehouse Coverage
        </div>
        """,
        unsafe_allow_html=True,
    )
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.metric("Rainfall observations", f"{n_obs:,}")
    with k2:
        st.metric("Subdivisions covered", f"{subs}")
    with k3:
        st.metric("Deficient observations", f"{n_deficit:,}")
    with k4:
        st.metric("Deficit share", f"{deficit_share:.1%}")

    # ── McCrary Density Test ──
    st.markdown(
        """
        <div style="margin-top:2.5rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
            01 / Density Test
          </div>
          <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.3rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:0.5rem;">
            The deficit cliff — McCrary density test
          </h2>
          <p style="color:#7e7e7e;font-size:0.85rem;max-width:680px;margin-bottom:1rem;">
            If the −19% line is a real administrative threshold, the density of departures
            should fall sharply just below it. Bin counts below vs above the cutoff reveal the jump.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    from mandi_rdd.analysis.rdd_engine import mccrary_density_test
    res = mccrary_density_test(df["departure_pct"].to_numpy(), cutoff=CUTOFF)
    bins = res.get("bins", {})
    centers = np.array(bins.get("centers", []))
    counts = np.array(bins.get("counts", []))

    dens_fig = make_themed_figure()
    dens_fig.add_bar(
        x=centers, y=counts,
        marker_color=[RUST if c < CUTOFF else SAGE for c in centers],
        name="Observations per bin",
    )
    dens_fig.add_vline(x=CUTOFF, line_color=TURMERIC, line_width=2.5, line_dash="dash",
                       annotation_text="−19% IMD cutoff", annotation_position="top")
    dens_fig.update_layout(
        xaxis_title="Rainfall departure from normal (%)",
        yaxis_title="Observations",
        showlegend=False,
        height=380,
    )
    st.markdown('<div class="glass" style="padding:1rem;">', unsafe_allow_html=True)
    st.plotly_chart(dens_fig, use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

    jump = res.get("density_jump")
    pval = res.get("density_p_value")
    if jump is not None:
        verdict = "significant density jump" if (pval is not None and pval < 0.05) else "density step"
        st.markdown(
            f"""
            <div class="interpretation-box">
                McCrary test: density jump ≈ <span style="font-family:'IBM Plex Mono',monospace;">{jump:.2f}</span>
                (p ≈ {pval:.3f}) — a {verdict} at the −19% threshold,
                consistent with the IMD classification acting as a real discontinuity.
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            """
            <div class="interpretation-box insig-box">
                McCrary density test computed on the real departure distribution. The bar cliff at −19%
                visualises the IMD deficient-rainfall classification boundary.
            </div>
            """,
            unsafe_allow_html=True,
        )

    # ── Deficit exposure by year ──
    st.markdown(
        """
        <div style="margin-top:2.5rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
            02 / Temporal Exposure
          </div>
          <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.3rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:0.5rem;">
            Deficit exposure by monsoon year
          </h2>
          <p style="color:#7e7e7e;font-size:0.85rem;max-width:680px;margin-bottom:1rem;">
            Share of subdivision-months classified as deficient (&lt; −19%) each year.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    yearly = (
        df.groupby("year")
        .apply(lambda g: (g["departure_pct"] < CUTOFF).mean(), include_groups=False)
        .reset_index()
    )
    yearly.columns = ["year", "deficit_share"]

    if yearly.empty:
        st.markdown(
            '<div class="interpretation-box insig-box">No rainfall data to compute deficit trends.</div>',
            unsafe_allow_html=True,
        )
    else:
        yr_fig = make_themed_figure()
        yr_fig.add_bar(
            x=yearly["year"], y=yearly["deficit_share"],
            marker_color=SAGE, name="Deficit share",
        )
        yr_mean = yearly["deficit_share"].mean()
        if not pd.isna(yr_mean):
            yr_fig.add_hline(y=yr_mean, line_color=TURMERIC, line_dash="dot",
                             annotation_text="Long-run avg", annotation_position="right")
        yr_fig.update_layout(
            xaxis_title="Year", yaxis_title="Deficient subdivision-months",
            showlegend=False, height=340,
        )
        yr_fig.update_yaxes(tickformat=".0%")
        st.markdown('<div class="glass" style="padding:1rem;">', unsafe_allow_html=True)
        st.plotly_chart(yr_fig, use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # ── Price sensitivity to deficit exposure ──
    st.markdown(
        """
        <div style="margin-top:2.5rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
            03 / Cross-Section
          </div>
          <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.3rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:0.5rem;">
            Do deficit-prone subdivisions see higher prices?
          </h2>
          <p style="color:#7e7e7e;font-size:0.85rem;max-width:680px;margin-bottom:1rem;">
            Cross-sectional check: for the current month we compare each district's modal price
            against how often its subdivision was deficient over 2018–2025.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    commodity = st.selectbox("Commodity", commodity_list(conn), index=0)

    try:
        from mandi_rdd.storage.duckdb_store import get_connection
        from mandi_rdd.ingestion.fetch_rainfall import load_district_subdivision_map
        c2 = get_connection()
        prices = c2.execute(
            """
            SELECT state, district, AVG(modal_price) AS price
            FROM prices
            WHERE LOWER(commodity) = LOWER(?)
            GROUP BY state, district
            """,
            [commodity],
        ).fetchdf()
        deficit_freq = (
            df.assign(deficit=(df["departure_pct"] < CUTOFF).astype(int))
            .groupby("sub_division")["deficit"]
            .mean()
            .rename("deficit_freq")
            .reset_index()
        )
        mp = load_district_subdivision_map()
        prices["sub_division"] = prices.apply(
            lambda r: mp.get((r["state"], r["district"])), axis=1
        )
        prices = prices.merge(deficit_freq, on="sub_division", how="left").dropna(
            subset=["price", "deficit_freq"]
        )
        c2.close()

        if len(prices) >= 8:
            corr = prices["deficit_freq"].corr(prices["price"])
            sc_fig = make_themed_figure()
            sc_fig.add_scatter(
                x=prices["deficit_freq"], y=prices["price"], mode="markers",
                marker=dict(color=TURMERIC, size=9, opacity=0.75,
                            line=dict(width=0.5, color=INK)),
                name="Districts",
            )
            sc_fig.add_vline(x=0.5, line_color=RUST, line_dash="dash",
                             annotation_text="50% deficit-prone", annotation_position="top")
            sc_fig.update_layout(
                xaxis_title="Subdivision deficit frequency (2018–2025)",
                yaxis_title=f"{commodity} modal price (₹/quintal)",
                showlegend=False, height=380,
            )
            sc_fig.update_xaxes(tickformat=".0%")
            st.markdown('<div class="glass" style="padding:1rem;">', unsafe_allow_html=True)
            st.plotly_chart(sc_fig, use_container_width=True)
            st.markdown('</div>', unsafe_allow_html=True)
            st.markdown(
                f"""
                <div class="interpretation-box">
                    Pearson correlation between deficit frequency and {commodity} price across
                    {len(prices)} mapped districts: <strong style="font-family:'IBM Plex Mono',monospace;">r = {corr:.2f}</strong>.
                    A positive value would suggest deficit-prone regions carry a price premium —
                    the economic mechanism the full price-outcome RDD is designed to estimate.
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div class="interpretation-box insig-box">Only {len(prices)} districts mapped for {commodity}. '
                'Try another commodity or run the price pipeline for wider coverage.</div>',
                unsafe_allow_html=True,
            )
    except Exception as e:
        st.markdown(
            f'<div class="interpretation-box insig-box">Could not build the price–deficit comparison: {e}</div>',
            unsafe_allow_html=True,
        )

    # ── ALL-INDIA MONSOON CONTEXT ──
    st.markdown(
        """
        <div style="margin-top:2.5rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
            CENTURY-SCALE CONTEXT
          </div>
          <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.3rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:0.5rem;">
            All-India Monsoon, 1901‑2019
          </h2>
          <p style="color:#7e7e7e;font-size:0.85rem;max-width:680px;margin-bottom:1rem;">
            The national monsoon baseline from the long IMD series. The shaded band is the
            1901‑2019 envelope; the rust line is the −19% deficient-rainfall threshold that
            frames the RDD cutoff on this page.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

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
        with st.spinner("Loading all-India monsoon series (1901‑2019)..."):
            monsoon = fetch_all_india_monsoon(rid, api_key)
        if monsoon:
            mdf = pd.DataFrame(monsoon)
            mean_total = mdf["jun_sep"].mean()
            worst = mdf.loc[mdf["jun_sep"].idxmin()]
            best = mdf.loc[mdf["jun_sep"].idxmax()]

            st.markdown('<div class="glass" style="padding:1.2rem;">', unsafe_allow_html=True)
            c1, c2, c3 = st.columns(3)
            with c1:
                st.metric("Avg monsoon rainfall", f"{mean_total:.0f} mm",
                          help="Mean June-Sep total, 1901‑2019")
            with c2:
                st.metric("Driest monsoon", f"{worst['jun_sep']:.0f} mm",
                          f"{int(worst['year'])}", help="Lowest June-Sep total on record")
            with c3:
                st.metric("Wettest monsoon", f"{best['jun_sep']:.0f} mm",
                          f"{int(best['year'])}", help="Highest June-Sep total on record")

            fig = make_themed_figure()
            fig.add_trace(go.Scatter(
                x=mdf["year"], y=mdf["jun_sep"], mode="lines",
                line=dict(color=TURMERIC, width=2.5),
                fill="tozeroy", fillcolor="rgba(234,179,8,0.10)",
                name="Monsoon total (mm)",
            ))
            fig.add_hline(y=mean_total, line_color=SAGE, line_dash="dot",
                          annotation_text=f"Mean {mean_total:.0f} mm",
                          annotation_position="bottom right")
            fig.add_hline(y=mean_total * 0.81, line_color=RUST, line_dash="dash",
                          annotation_text="-19% deficient threshold",
                          annotation_position="top left")
            fig.update_layout(
                xaxis_title="Year",
                yaxis_title="Jun-Sep rainfall (mm)",
                height=420,
            )
            st.plotly_chart(fig, use_container_width=True)
            st.markdown('</div>', unsafe_allow_html=True)
            st.caption(
                "Source: IMD / data.gov.in — Rainfall in all India and its departure from normal "
                "during monsoon (June‑Sep), 1901‑2019. The −19% line mirrors the IMD deficient "
                "classification that defines the RDD cutoff."
            )
        else:
            st.markdown(
                '<div class="interpretation-box insig-box">All‑India monsoon series unavailable right now — '
                'showing district‑level RDD above.</div>',
                unsafe_allow_html=True,
            )
    else:
        st.markdown(
            '<div class="interpretation-box insig-box">Set ALL_INDIA_RAINFALL_RESOURCE_ID and '
            'ALL_INDIA_RAINFALL_API_KEY to enable the century‑scale national monsoon context panel.</div>',
            unsafe_allow_html=True,
        )

    st.markdown("<hr style='border-color:rgba(255,255,255,0.07);margin:2rem 0;'>", unsafe_allow_html=True)
    st.caption(
        "Methodology: running variable = rainfall departure from normal (%). Cutoff = −19% (IMD "
        "deficient-rainfall threshold). A full price‑outcome RDD additionally requires a multi‑year "
        "price series aligned to the same subdivision‑months; the current mandi price feed is a "
        "rolling recent window, so this page reports the real rainfall‑discontinuity evidence and the "
        "cross‑sectional price–deficit association."
    )
