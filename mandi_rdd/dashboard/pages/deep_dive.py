"""
MandiIQ — Deep Dive page.

Raw data explorer, analytical SQL query results.

Design: glass cards for data and SQL tabs,
section headers, consistent monochrome-lime palette.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import streamlit as st
import pandas as pd
from mandi_rdd.dashboard.theme import inject_theme, commodity_color


def render(**kwargs):
    inject_theme()

    # Determine selected_commodity
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
              Data Analysis
            </div>
            <h1 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:300;font-size:clamp(1.6rem,3vw,2.4rem);color:#ffffff;letter-spacing:0.03em;text-transform:uppercase;margin-bottom:0.5rem;">
              Deep <span style="font-weight:600;color:#d7ff00;">Dive</span>
            </h1>
            <p style="color:#7e7e7e;max-width:680px;line-height:1.7;font-size:0.9rem;">
              Raw price records and analytical SQL queries running against the live mandi warehouse.
            </p>
          </div>
        </div>
    """, unsafe_allow_html=True)

    tab_raw, tab_sql = st.tabs(["Raw Data Explorer", "Analytical SQL Queries"])

    with tab_raw:
        st.markdown("""
            <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.8rem;">
              Price Records
            </div>
            <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.2rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:1rem;">
              Recent Price Records
            </h2>
        """, unsafe_allow_html=True)

        try:
            from mandi_rdd.storage.duckdb_store import get_connection
            conn = get_connection()
            df = conn.execute(
                "SELECT state, district, market, arrival_date, commodity, variety, modal_price, min_price, max_price FROM prices WHERE commodity = ? ORDER BY arrival_date DESC LIMIT 100",
                [selected_commodity],
            ).fetchdf()
            conn.close()
            if len(df) > 0:
                rows = []
                for _, r in df.iterrows():
                    chip = f'<span class="commodity-badge {selected_commodity}" style="font-size:0.7rem;padding:0.1rem 0.5rem;border-radius:4px;color:#000000;font-weight:600;">{selected_commodity.upper()}</span>'
                    rows.append(
                        f'<tr>'
                        f'<td style="padding:0.4rem 0.7rem;">{r["state"]}</td>'
                        f'<td style="padding:0.4rem 0.7rem;">{r["district"]}</td>'
                        f'<td style="padding:0.4rem 0.7rem;">{r["market"]}</td>'
                        f'<td style="padding:0.4rem 0.7rem;font-family:\'IBM Plex Mono\',monospace;">{r["arrival_date"]}</td>'
                        f'<td style="padding:0.4rem 0.7rem;">{chip}</td>'
                        f'<td style="padding:0.4rem 0.7rem;font-family:\'IBM Plex Mono\',monospace;">₹{r["modal_price"]:,}</td>'
                        f'<td style="padding:0.4rem 0.7rem;font-family:\'IBM Plex Mono\',monospace;color:#bababa;">₹{r["min_price"]:,}</td>'
                        f'<td style="padding:0.4rem 0.7rem;font-family:\'IBM Plex Mono\',monospace;color:#bababa;">₹{r["max_price"]:,}</td>'
                        f'</tr>'
                    )
                html = (
                    '<div class="crosshair-panel glass" style="padding:0;max-height:500px;overflow-y:auto;">'
                    '<table style="width:100%;border-collapse:collapse;font-size:0.8rem;">'
                    '<thead style="position:sticky;top:0;background:#0a0a0a;color:#bababa;">'
                    '<tr style="text-align:left;"><th style="padding:0.4rem 0.7rem;">State</th><th style="padding:0.4rem 0.7rem;">District</th><th style="padding:0.4rem 0.7rem;">Market</th><th style="padding:0.4rem 0.7rem;">Date</th><th style="padding:0.4rem 0.7rem;">Commodity</th><th style="padding:0.4rem 0.7rem;">Modal</th><th style="padding:0.4rem 0.7rem;">Min</th><th style="padding:0.4rem 0.7rem;">Max</th></tr>'
                    '</thead><tbody>' + "".join(rows) + '</tbody></table></div>'
                )
                st.markdown(html, unsafe_allow_html=True)
                st.markdown(f"*Showing {len(df)} records*")
            else:
                st.markdown(
                    '<div class="interpretation-box insig-box">No data — run the ingestion pipeline first.</div>',
                    unsafe_allow_html=True,
                )
        except Exception as e:
            st.markdown(
                f'<div class="interpretation-box insig-box">Data unavailable: {e}</div>',
                unsafe_allow_html=True,
            )

    with tab_sql:
        st.markdown("""
            <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.5rem;">
              SQL
            </div>
            <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.2rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:0.5rem;">
              Analytical SQL Queries
            </h2>
            <p style="color:#7e7e7e;font-size:0.85rem;max-width:680px;margin-bottom:1rem;">
                Five analytical SQL queries mirroring the Superstore pattern —
                window functions, CTEs, joins — applied to live government API data.
            </p>
        """, unsafe_allow_html=True)

        sql_queries = [
            ("01_rolling_price_trend.sql", "Rolling 30-day price trend by district"),
            ("02_monthly_price_volatility.sql", "Month-over-month price volatility by commodity"),
            ("03_district_rainfall_deficiency_ranking.sql", "District ranking by rainfall-deficiency frequency"),
            ("04_price_dispersion_by_market.sql", "Price dispersion (max-min spread) by market"),
            ("05_yoy_rainfall_season_comparison.sql", "Year-over-year rainfall season comparison"),
        ]

        for filename, description in sql_queries:
            with st.expander(f"{description}"):
                sql_path = Path(__file__).resolve().parent.parent.parent / "sql" / filename
                if sql_path.exists():
                    st.code(sql_path.read_text(), language="sql")
                    st.markdown(
                        '<div class="interpretation-box insig-box" style="margin-top:0.5rem;">'
                        '*These queries use DuckDB-specific syntax (EXTRACT, DATE_TRUNC, STDDEV) and require '
                        'the DuckDB migration. Run with DuckDB enabled to see live results.*'
                        '</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f'<div class="interpretation-box insig-box">SQL file not found: {filename}</div>',
                        unsafe_allow_html=True,
                    )

