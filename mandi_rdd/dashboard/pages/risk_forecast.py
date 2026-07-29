"""
MandiIQ — Risk & Forecast page.

Classifier risk scores by district and Prophet forecast chart.

Design: glass KPI strip, glass chart panels, glass comparison cards,
section headers, consistent monochrome-lime palette.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import numpy as np
from mandi_rdd.dashboard.theme import inject_theme, commodity_color
from mandi_rdd.dashboard.plotly_theme import make_themed_figure


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
              Model Analysis
            </div>
            <h1 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:300;font-size:clamp(1.6rem,3vw,2.4rem);color:#ffffff;letter-spacing:0.03em;text-transform:uppercase;margin-bottom:0.5rem;">
              Risk &amp; <span style="font-weight:600;color:#d7ff00;">Forecast</span>
            </h1>
            <p style="color:#7e7e7e;max-width:680px;line-height:1.7;font-size:0.9rem;">
              Classifier risk scores by district and Prophet/LSTM forecast comparison.
            </p>
          </div>
        </div>
    """, unsafe_allow_html=True)

    # ── Model Comparison Toggle ──
    enable_comparison = st.checkbox(
        "Show Prophet vs LSTM comparison",
        value=False,
        help="Runs both Prophet and LSTM on the same data and picks the honest winner.",
    )

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("""
            <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.5rem;">
              RISK SCORE
            </div>
            <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.2rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:1rem;">
              Price-Spike Risk Score
            </h2>
        """, unsafe_allow_html=True)

        try:
            from mandi_rdd.storage.duckdb_store import get_connection
            from mandi_rdd.analysis.classifier import predict_spike_risk
            conn = get_connection()
            risk = predict_spike_risk(conn, commodity=selected_commodity)
            conn.close()

            if "error" not in risk:
                st.markdown('<div class="crosshair-panel glass" style="padding:1rem;">', unsafe_allow_html=True)
                st.metric("Overall Risk", f"{risk.get('overall_risk', 0):.1f}%")
                st.metric("Max District Risk", f"{risk.get('max_risk', 0):.1f}%")
                st.metric("Districts Analyzed", f"{risk.get('n_districts_analyzed', 0)}")
                st.markdown('</div>', unsafe_allow_html=True)

                top = risk.get("top_5_risk_districts", [])
                if top:
                    st.markdown("""
                        <div style="font-family:'IBM Plex Mono',monospace;font-size:0.7rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.08em;margin:1rem 0 0.5rem;">
                          TOP DISTRICTS
                        </div>
                    """, unsafe_allow_html=True)
                    df = pd.DataFrame(top)
                    st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.markdown(
                    f'<div class="interpretation-box insig-box">Risk score unavailable: {risk["error"]}</div>',
                    unsafe_allow_html=True,
                )
        except Exception as e:
            st.markdown(
                f'<div class="interpretation-box insig-box">Risk score unavailable: {e}</div>',
                unsafe_allow_html=True,
            )

    with col2:
        if enable_comparison:
            st.markdown("""
                <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.5rem;">
                  MODEL COMPARISON
                </div>
                <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.2rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:1rem;">
                  Prophet vs LSTM
                </h2>
            """, unsafe_allow_html=True)

            try:
                from mandi_rdd.storage.duckdb_store import get_connection, init_schema
                from mandi_rdd.analysis.forecast import compare_forecast_models
                conn = get_connection()
                init_schema(conn)
                comp = compare_forecast_models(conn, commodity=selected_commodity)
                conn.close()

                if "error" not in comp:
                    winner = comp.get("better_model", "—")
                    if winner == "Prophet":
                        st.markdown(f'<div class="interpretation-box" style="border-left-color:#d7ff00;">🏆 <strong>Winner: Prophet</strong> — {comp.get("explanation", "")}</div>', unsafe_allow_html=True)
                    elif winner == "LSTM":
                        st.markdown(f'<div class="interpretation-box" style="border-left-color:#D9663B;">🏆 <strong>Winner: LSTM</strong> — {comp.get("explanation", "")}</div>', unsafe_allow_html=True)
                    elif winner == "Tie":
                        st.markdown(f'<div class="interpretation-box">⚖️ <strong>Tie</strong> — {comp.get("explanation", "")}</div>', unsafe_allow_html=True)
                    else:
                        st.markdown(
                            f'<div class="interpretation-box insig-box">Comparison unavailable: {comp.get("explanation", "")}</div>',
                            unsafe_allow_html=True,
                        )

                    # Side-by-side metrics in glass card
                    p = comp.get("prophet", {})
                    l = comp.get("lstm", {})

                    st.markdown("""
                        <div style="font-family:'IBM Plex Mono',monospace;font-size:0.7rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.08em;margin:1rem 0 0.5rem;">
                          TEST METRICS
                        </div>
                    """, unsafe_allow_html=True)
                    col_a, col_b, col_c = st.columns(3)

                    with col_a:
                        st.markdown('<div class="crosshair-panel glass" style="padding:0.8rem;">', unsafe_allow_html=True)
                        st.markdown("**Prophet**")
                        mape_p = p.get("test_mape")
                        mae_p = p.get("test_mae")
                        st.metric("MAPE", f"{mape_p:.1f}%" if mape_p else "N/A")
                        st.metric("MAE", f"₹{mae_p:.0f}" if mae_p else "N/A")
                        st.markdown('</div>', unsafe_allow_html=True)
                    with col_b:
                        st.markdown('<div class="crosshair-panel glass" style="padding:0.8rem;">', unsafe_allow_html=True)
                        st.markdown("**LSTM**")
                        mape_l = l.get("test_mape")
                        mae_l = l.get("test_mae")
                        st.metric("MAPE", f"{mape_l:.1f}%" if mape_l else "N/A")
                        st.metric("MAE", f"₹{mae_l:.0f}" if mae_l else "N/A")
                        st.markdown('</div>', unsafe_allow_html=True)
                    with col_c:
                        st.markdown('<div class="crosshair-panel glass" style="padding:0.8rem;">', unsafe_allow_html=True)
                        st.markdown("**Training Data**")
                        st.metric("Months", f"{comp.get('n_training_months', 0)}")
                        st.markdown('</div>', unsafe_allow_html=True)

                    # Dual forecast chart
                    st.markdown("""
                        <div style="font-family:'IBM Plex Mono',monospace;font-size:0.7rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.08em;margin:1rem 0 0.5rem;">
                          FORECAST COMPARISON
                        </div>
                    """, unsafe_allow_html=True)
                    fig = make_themed_figure()

                    hist = comp.get("monthly_history", {})
                    if hist.get("dates"):
                        fig.add_trace(go.Scatter(
                            x=pd.to_datetime(hist["dates"]),
                            y=hist["prices"],
                            mode="lines",
                            name="Historical",
                            line=dict(color="#7e7e7e", width=1.5),
                        ))

                    fc = pd.DataFrame(comp.get("forecast", []))
                    if len(fc) > 0:
                        fc["date"] = pd.to_datetime(fc["date"])
                        fig.add_trace(go.Scatter(
                            x=fc["date"], y=fc["forecast"], mode="lines",
                            name=f"Prophet (MAPE: {p.get('test_mape', 0):.1f}%)" if p.get('test_mape') else "Prophet",
                            line=dict(color="#d7ff00", width=2.5),
                        ))
                        if "forecast_lower" in fc.columns:
                            fig.add_trace(go.Scatter(
                                x=fc["date"], y=fc["forecast_upper"],
                                fill=None, mode="none", showlegend=False,
                            ))
                            fig.add_trace(go.Scatter(
                                x=fc["date"], y=fc["forecast_lower"],
                                fill="tonexty", fillcolor="rgba(215,255,0,0.08)",
                                mode="none", name="Prophet 95% CI",
                            ))

                    lstm_future = l.get("future_forecast", [])
                    if lstm_future and len(lstm_future) > 0:
                        last_date = pd.to_datetime(hist["dates"][-1]) if hist.get("dates") else pd.Timestamp.now()
                        lstm_dates = pd.date_range(start=last_date + pd.DateOffset(months=1), periods=len(lstm_future), freq="MS")
                        fig.add_trace(go.Scatter(
                            x=lstm_dates, y=lstm_future, mode="lines+markers",
                            name=f"LSTM (MAPE: {l.get('test_mape', 0):.1f}%)" if l.get('test_mape') else "LSTM",
                            line=dict(color="#D9663B", width=2, dash="dot"),
                            marker=dict(size=4),
                        ))
                    fig.update_layout(hovermode="x unified", margin=dict(l=0, r=0, t=10, b=0), height=350,
                        legend=dict(orientation="h", y=1.12, x=0, font=dict(size=10)),
                    )
                    st.markdown('<div class="crosshair-panel glass" style="padding:1rem;">', unsafe_allow_html=True)
                    st.plotly_chart(fig, use_container_width=True)
                    st.markdown('</div>', unsafe_allow_html=True)

                    st.caption(
                        f"Data: {comp.get('n_training_months', 0)} months of {selected_commodity} modal prices. "
                        f"Winner chosen by lowest test MAPE on held-out months."
                    )
                else:
                    st.markdown(
                        f'<div class="interpretation-box insig-box">Comparison unavailable: {comp.get("error", "unknown")}</div>',
                        unsafe_allow_html=True,
                    )
            except Exception as e:
                st.markdown(
                    f'<div class="interpretation-box insig-box">Model comparison unavailable: {e}</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.markdown("""
                <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.5rem;">
                  PROPHET FORECAST
                </div>
                <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.2rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:1rem;">
                  Prophet Forecast
                </h2>
            """, unsafe_allow_html=True)

            try:
                from mandi_rdd.storage.duckdb_store import get_connection, init_schema
                from mandi_rdd.analysis.forecast import train_forecast
                conn = get_connection()
                init_schema(conn)
                fc = train_forecast(conn, commodity=selected_commodity, periods=12)
                conn.close()

                if "error" not in fc and fc.get("forecast"):
                    df = pd.DataFrame(fc["forecast"])
                    df["date"] = pd.to_datetime(df["date"])

                    fig = make_themed_figure()
                    fig.add_trace(go.Scatter(x=df["date"], y=df["forecast"], mode="lines", name="Forecast", line=dict(color="#d7ff00", width=3)))
                    if "forecast_lower" in df.columns:
                        fig.add_trace(go.Scatter(x=df["date"], y=df["forecast_upper"], fill=None, mode="none", showlegend=False))
                        fig.add_trace(go.Scatter(x=df["date"], y=df["forecast_lower"], fill="tonexty", fillcolor="rgba(215,255,0,0.1)", mode="none", name="95% CI"))
                    fig.update_layout(hovermode="x unified", margin=dict(l=0, r=0, t=10, b=0), height=300)

                    st.markdown('<div class="crosshair-panel glass" style="padding:1rem;">', unsafe_allow_html=True)
                    st.plotly_chart(fig, use_container_width=True)
                    st.markdown('</div>', unsafe_allow_html=True)

                    if fc.get("metrics"):
                        st.markdown('<div class="crosshair-panel glass" style="padding:0.8rem;">', unsafe_allow_html=True)
                        col_a, col_b, col_c = st.columns(3)
                        m = fc["metrics"]
                        col_a.metric("MAE", f"₹{m['mae']:.0f}")
                        col_b.metric("RMSE", f"₹{m['rmse']:.0f}")
                        col_c.metric("MAPE", f"{m['mape']:.1f}%")
                        st.markdown('</div>', unsafe_allow_html=True)
                else:
                    st.markdown(
                        f'<div class="interpretation-box insig-box">Forecast unavailable: {fc.get("error", "unknown")}</div>',
                        unsafe_allow_html=True,
                    )
            except Exception as e:
                st.markdown(
                    f'<div class="interpretation-box insig-box">Forecast unavailable: {e}</div>',
                    unsafe_allow_html=True,
                )

