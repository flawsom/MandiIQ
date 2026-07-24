"""
Model Health monitoring page for the Margin Intelligence Dashboard.

Shows request volume, prediction distributions, drift flags, and latency.
All traffic shown is simulated — labeled clearly as such.
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
import json


MONITORING_LOG_DIR = Path("monitoring_logs")
REQUEST_LOG = MONITORING_LOG_DIR / "predictions.csv"
DRIFT_REPORT = MONITORING_LOG_DIR / "drift_report.json"


def render_model_health():
    """Render the Model Health monitoring view."""
    
    st.markdown("<h1>🩺 Model Health</h1>", unsafe_allow_html=True)
    
    # ⚠️ Clear label that this is simulated
    st.warning("""
    **⚠️ Simulated Monitoring Data**
    
    All traffic and metrics shown below are generated from synthetic prediction requests.
    In a production deployment, this view would reflect real API usage. 
    This demonstrates the monitoring infrastructure, not actual production traffic.
    """)
    
    # Load monitoring data
    if not REQUEST_LOG.exists():
        st.info("No monitoring data yet. Run `python demo/simulate_traffic.py` to generate demo data.")
        return
    
    df = pd.read_csv(REQUEST_LOG)
    
    if len(df) == 0:
        st.info("Monitoring log is empty.")
        return
    
    # ── KPI Row ──
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("Total Requests", f"{len(df):,}")
    with col2:
        if "latency_ms" in df.columns:
            avg_lat = df["latency_ms"].mean()
            st.metric("Avg Latency", f"{avg_lat:.0f} ms", delta=None)
    with col3:
        drift_detected = False
        if DRIFT_REPORT.exists():
            with open(DRIFT_REPORT) as f:
                drift = json.load(f)
                drift_detected = drift.get("drift_detected", False)
        st.metric("Drift Status", "⚠️ Detected" if drift_detected else "✅ Normal",
                  delta_color="inverse" if drift_detected else "normal")
    with col4:
        st.metric("Data Source", "🔬 Simulated")
    
    st.markdown("<hr>", unsafe_allow_html=True)
    
    # ── Request Volume Over Time ──
    st.markdown("<h3>📈 Request Volume</h3>", unsafe_allow_html=True)
    
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["hour"] = df["timestamp"].dt.floor("h")
        
        volume = df.groupby("hour").size().reset_index(name="count")
        
        fig = px.area(volume, x="hour", y="count",
                      title="Requests per Hour",
                      template="plotly_dark")
        fig.update_layout(
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=30, b=0),
            xaxis=dict(title="", gridcolor="rgba(255,255,255,0.05)"),
            yaxis=dict(title="Request Count", gridcolor="rgba(255,255,255,0.05)"),
            font=dict(color="rgba(255,255,255,0.7)"),
        )
        st.plotly_chart(fig, use_container_width=True)
    
    # ── Latency Distribution ──
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("<h3>⏱️ Latency Distribution</h3>", unsafe_allow_html=True)
        if "latency_ms" in df.columns:
            fig = go.Figure()
            fig.add_trace(go.Histogram(
                x=df["latency_ms"],
                nbinsx=30,
                marker_color="rgba(247,151,30,0.7)",
                hovertemplate="Latency: %{x:.0f}ms<br>Count: %{y}<extra></extra>",
            ))
            fig.update_layout(
                template="plotly_dark",
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=0, t=10, b=0),
                xaxis=dict(title="Latency (ms)", gridcolor="rgba(255,255,255,0.05)"),
                yaxis=dict(title="Count", gridcolor="rgba(255,255,255,0.05)"),
                font=dict(color="rgba(255,255,255,0.7)"),
                showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)
    
    with col2:
        st.markdown("<h3>🎯 Prediction Distribution</h3>", unsafe_allow_html=True)
        output_cols = [c for c in df.columns if c.startswith("output_")]
        for col in output_cols[:1]:  # Show first output distribution
            if col in df.columns:
                values = df[col].dropna()
                if values.dtype == "object":
                    val_counts = values.value_counts().head(10)
                    fig = go.Figure(data=[go.Bar(
                        x=list(val_counts.index.astype(str)),
                        y=list(val_counts.values),
                        marker_color="rgba(0,212,170,0.7)",
                    )])
                    fig.update_layout(
                        template="plotly_dark",
                        plot_bgcolor="rgba(0,0,0,0)",
                        paper_bgcolor="rgba(0,0,0,0)",
                        margin=dict(l=0, r=0, t=10, b=0),
                        xaxis=dict(title="Prediction", gridcolor="rgba(255,255,255,0.05)"),
                        yaxis=dict(title="Count", gridcolor="rgba(255,255,255,0.05)"),
                        font=dict(color="rgba(255,255,255,0.7)"),
                        showlegend=False,
                    )
                    st.plotly_chart(fig, use_container_width=True)
    
    # ── Drift Report ──
    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown("<h3>🔬 Drift Detection</h3>", unsafe_allow_html=True)
    
    if DRIFT_REPORT.exists():
        with open(DRIFT_REPORT) as f:
            drift = json.load(f)
        
        if drift.get("drifted_features"):
            for feat, info in drift["drifted_features"].items():
                st.markdown(f"""
                <div class="glass-card" style="padding: 0.8rem 1rem; margin-bottom: 0.5rem;">
                    <div style="display: flex; justify-content: space-between;">
                        <span style="color: #ff5757; font-weight: 600;">⚠️ {feat}</span>
                        <span style="color: rgba(255,255,255,0.6); font-size: 0.9rem;">
                            {info['outlier_pct']:.1f}% outliers
                        </span>
                    </div>
                    <div style="font-size: 0.85rem; color: rgba(255,255,255,0.5); margin-top: 0.3rem;">
                        Current mean: {info['current_mean']:.4f} vs Training mean: {info['training_mean']:.4f}
                    </div>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div class="glass-card" style="text-align: center; padding: 1.5rem;">
                <div style="font-size: 2rem; margin-bottom: 0.5rem;">✅</div>
                <div style="color: rgba(255,255,255,0.6);">No significant drift detected</div>
            </div>
            """, unsafe_allow_html=True)
        
        st.markdown(f"""
        <div style="color: rgba(255,255,255,0.4); font-size: 0.8rem; text-align: center; padding: 1rem;">
            Recommendation: {drift.get('recommendation', 'N/A')}<br>
            Last checked: {drift.get('checked_at', 'N/A')}
        </div>
        """, unsafe_allow_html=True)
    else:
        st.info("No drift report yet. Run `python demo/simulate_traffic.py` first.")
    
    # ── Recent Requests Table ──
    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown("<h3>📋 Recent Requests</h3>", unsafe_allow_html=True)
    
    display_cols = [c for c in df.columns if c in ["timestamp", "latency_ms", "endpoint"] or c.startswith("input_") or c.startswith("output_")]
    if display_cols:
        st.dataframe(
            df[display_cols].tail(20),
            use_container_width=True,
            hide_index=True,
        )


if __name__ == "__main__":
    render_model_health()
