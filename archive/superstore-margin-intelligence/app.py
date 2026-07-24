"""
Superstore Margin Intelligence System — Streamlit Dashboard

An interactive decision-support dashboard with:
- Executive Overview (KPI panels + 6-pitch visual summary)
- Discount Approval Simulator (interactive)
- Forecast Explorer (Prophet vs LSTM comparison)
- Regional/Category Deep Dive (drill-down filters)

Design: Modern glass-morphism with gradient accents, smooth transitions, micro-interactions.

Run: streamlit run dashboard/app.py
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
import json
import sys
import time
import os

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.engineer import engineer_features, get_feature_columns
from src.data.init_db import get_connection

# ── Hybrid model loading: try API first, fall back to local ──
API_URL = os.getenv("API_URL", "http://localhost:8000")
USE_API = os.getenv("USE_API", "true").lower() == "true"

# Always import both — API client and local models
from dashboard.api_client import (
    predict_loss_risk as api_predict_loss,
    predict_max_discount as api_max_discount,
    get_forecast as api_get_forecast,
    get_health as api_health,
    api_available,
)
from src.models.classifier import load_classifier, predict_loss as local_predict_loss
from src.models.optimizer import compute_safe_discount as local_compute_safe_discount

# Determine which backend to use
API_AVAILABLE = False
if USE_API:
    API_AVAILABLE = api_available()
    if API_AVAILABLE:
        print(f"  Dashboard using API at {API_URL}")
    else:
        print(f"  API at {API_URL} not reachable, using local models")
else:
    print("  Dashboard using local models (USE_API=false)")

def predict_loss(features, artifacts=None):
    """Predict loss risk — tries API first, falls back to local."""
    if API_AVAILABLE:
        result = api_predict_loss(
            category=features["category"],
            sub_category=features["sub_category"],
            region=features["region"],
            segment=features["segment"],
            discount=features["discount"],
            quantity=features["quantity"],
            ship_mode=features.get("ship_mode", "Standard Class"),
            shipping_delay=features.get("shipping_delay", 4),
        )
        if "error" not in result:
            return result
    return local_predict_loss(features, artifacts)

def compute_safe_discount(category, sub_category, region, segment, quantity=3, ship_mode="Standard Class", artifacts=None):
    """Compute safe discount — tries API first, falls back to local."""
    if API_AVAILABLE:
        result = api_max_discount(
            category=category,
            sub_category=sub_category,
            region=region,
            segment=segment,
            quantity=quantity,
            ship_mode=ship_mode,
        )
        if "error" not in result:
            return result
    return local_compute_safe_discount(category, sub_category, region, segment, quantity, ship_mode, artifacts=artifacts)

# =============================================================================
# PAGE CONFIG — MUST BE FIRST
# =============================================================================

st.set_page_config(
    page_title="Margin Intelligence System",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# =============================================================================
# CUSTOM CSS — Glass-morphism + Gradient + Micro-interactions
# =============================================================================

st.markdown("""
<style>
    /* ── Global Reset & Font ── */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');
    
    * { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important; }
    
    /* ── Background Gradient ── */
    .stApp {
        background: linear-gradient(135deg, #0f0c29 0%, #1a1a3e 50%, #24243e 100%);
        background-attachment: fixed;
    }
    
    /* ── Sidebar ── */
    section[data-testid="stSidebar"] {
        background: rgba(255,255,255,0.03);
        backdrop-filter: blur(20px);
        -webkit-backdrop-filter: blur(20px);
        border-right: 1px solid rgba(255,255,255,0.05);
    }
    section[data-testid="stSidebar"] .sidebar-content {
        padding: 2rem 1rem;
    }
    section[data-testid="stSidebar"] .stSelectbox label,
    section[data-testid="stSidebar"] .stSlider label {
        color: rgba(255,255,255,0.7) !important;
        font-size: 0.8rem !important;
        font-weight: 500 !important;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    
    /* ── Main Content Area ── */
    .main > div:first-child {
        padding: 0 1.5rem;
    }
    
    /* ── Headers ── */
    h1, h2, h3 {
        color: #ffffff !important;
        font-weight: 700 !important;
        letter-spacing: -0.02em;
    }
    h1 {
        background: linear-gradient(90deg, #f7971e 0%, #ffd200 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        font-size: 2.2rem !important;
        margin-bottom: 0.5rem !important;
    }
    h2 {
        font-size: 1.5rem !important;
        border-bottom: 1px solid rgba(255,255,255,0.08);
        padding-bottom: 0.3rem;
        margin-top: 1.5rem !important;
    }
    h3 {
        font-size: 1.15rem !important;
        color: rgba(255,255,255,0.9) !important;
    }
    
    /* ── Metric Cards (Glass KPI) ── */
    div[data-testid="metric-container"] {
        background: rgba(255,255,255,0.05);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 16px;
        padding: 1.2rem 1rem;
        transition: all 0.3s cubic-bezier(0.4,0,0.2,1);
        box-shadow: 0 4px 24px rgba(0,0,0,0.2);
    }
    div[data-testid="metric-container"]:hover {
        transform: translateY(-4px);
        box-shadow: 0 8px 32px rgba(247,151,30,0.15);
        border-color: rgba(247,151,30,0.3);
        background: rgba(255,255,255,0.08);
    }
    div[data-testid="metric-container"] label {
        color: rgba(255,255,255,0.6) !important;
        font-weight: 500 !important;
        font-size: 0.8rem !important;
        text-transform: uppercase;
        letter-spacing: 0.06em;
    }
    div[data-testid="metric-container"] div[data-testid="metric-value"] {
        color: #ffffff !important;
        font-weight: 700 !important;
        font-size: 1.8rem !important;
    }
    
    /* ── Cards / Containers ── */
    .glass-card {
        background: rgba(255,255,255,0.04);
        backdrop-filter: blur(16px);
        -webkit-backdrop-filter: blur(16px);
        border: 1px solid rgba(255,255,255,0.06);
        border-radius: 20px;
        padding: 1.5rem;
        margin-bottom: 1.5rem;
        transition: all 0.3s ease;
        box-shadow: 0 4px 24px rgba(0,0,0,0.15);
    }
    .glass-card:hover {
        border-color: rgba(247,151,30,0.2);
        box-shadow: 0 6px 32px rgba(247,151,30,0.1);
    }
    
    /* ── Buttons ── */
    .stButton button {
        background: linear-gradient(135deg, #f7971e 0%, #ffd200 100%) !important;
        color: #0f0c29 !important;
        font-weight: 700 !important;
        border: none !important;
        border-radius: 12px !important;
        padding: 0.5rem 1.8rem !important;
        transition: all 0.3s cubic-bezier(0.4,0,0.2,1) !important;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        font-size: 0.85rem !important;
        box-shadow: 0 4px 16px rgba(247,151,30,0.3) !important;
    }
    .stButton button:hover {
        transform: translateY(-2px) scale(1.02);
        box-shadow: 0 8px 32px rgba(247,151,30,0.4) !important;
    }
    .stButton button:active {
        transform: translateY(0);
    }
    
    /* ── Inputs ── */
    .stSelectbox div[data-baseweb="select"] > div,
    .stSlider div[data-baseweb="slider"] > div {
        background: rgba(255,255,255,0.06) !important;
        border: 1px solid rgba(255,255,255,0.1) !important;
        border-radius: 10px !important;
        color: white !important;
    }
    .stSelectbox div[data-baseweb="select"] > div:hover {
        border-color: rgba(247,151,30,0.4) !important;
    }
    
    /* ── Warning / Info Boxes ── */
    div[data-testid="stAlert"] {
        background: rgba(255,255,255,0.04) !important;
        border: 1px solid rgba(255,255,255,0.08) !important;
        border-radius: 12px !important;
        backdrop-filter: blur(12px);
    }
    
    /* ── Tabs ── */
    .stTabs [data-baseweb="tab-list"] {
        gap: 0;
        background: rgba(255,255,255,0.03);
        border-radius: 14px;
        padding: 4px;
        border: 1px solid rgba(255,255,255,0.06);
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 12px;
        padding: 0.5rem 1.2rem;
        transition: all 0.3s ease;
        font-weight: 500;
        color: rgba(255,255,255,0.6);
    }
    .stTabs [data-baseweb="tab"][aria-selected="true"] {
        background: linear-gradient(135deg, #f7971e 0%, #ffd200 100%) !important;
        color: #0f0c29 !important;
        font-weight: 700 !important;
    }
    .stTabs [data-baseweb="tab"]:hover:not([aria-selected="true"]) {
        color: rgba(255,255,255,0.9);
        background: rgba(255,255,255,0.05);
    }
    
    /* ── Scrollbar ── */
    ::-webkit-scrollbar {
        width: 6px;
        height: 6px;
    }
    ::-webkit-scrollbar-track {
        background: transparent;
    }
    ::-webkit-scrollbar-thumb {
        background: rgba(247,151,30,0.3);
        border-radius: 3px;
    }
    ::-webkit-scrollbar-thumb:hover {
        background: rgba(247,151,30,0.5);
    }
    
    /* ── Plotly Chart Container ── */
    .js-plotly-plot {
        border-radius: 16px;
        overflow: hidden;
    }
    
    /* ── Footer ── */
    .footer {
        text-align: center;
        padding: 2rem 0 1rem;
        color: rgba(255,255,255,0.3);
        font-size: 0.8rem;
        border-top: 1px solid rgba(255,255,255,0.05);
        margin-top: 2rem;
    }
    
    /* ── Simulator Result Cards ── */
    .result-card {
        background: rgba(255,255,255,0.06);
        border: 1px solid rgba(255,255,255,0.1);
        border-radius: 16px;
        padding: 1.5rem;
        text-align: center;
        transition: all 0.4s cubic-bezier(0.4,0,0.2,1);
    }
    .result-card:hover {
        transform: translateY(-3px);
        box-shadow: 0 8px 32px rgba(0,0,0,0.2);
    }
    .result-card .label {
        color: rgba(255,255,255,0.5);
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin-bottom: 0.3rem;
    }
    .result-card .value {
        font-size: 2.2rem;
        font-weight: 800;
        line-height: 1.2;
    }
    .result-card .sub {
        color: rgba(255,255,255,0.4);
        font-size: 0.8rem;
        margin-top: 0.3rem;
    }
    
    /* ── Loading Animation ── */
    @keyframes shimmer {
        0% { background-position: -200% 0; }
        100% { background-position: 200% 0; }
    }
    .loading-shimmer {
        background: linear-gradient(90deg, rgba(255,255,255,0.02) 25%, rgba(255,255,255,0.06) 50%, rgba(255,255,255,0.02) 75%);
        background-size: 200% 100%;
        animation: shimmer 1.5s ease-in-out infinite;
        border-radius: 12px;
    }

    /* ── Animated gradient borders ── */
    @keyframes gradient-rotate {
        0% { background-position: 0% 50%; }
        50% { background-position: 100% 50%; }
        100% { background-position: 0% 50%; }
    }
    .gradient-border {
        position: relative;
        border-radius: 20px;
        padding: 2px;
        background: linear-gradient(60deg, #f7971e, #ffd200, #f7971e, #ffd200);
        background-size: 300% 300%;
        animation: gradient-rotate 4s ease infinite;
    }
    .gradient-border > div {
        background: #1a1a3e;
        border-radius: 18px;
        padding: 1.5rem;
    }
    
    /* ── Dividers ── */
    hr {
        border: none;
        height: 1px;
        background: linear-gradient(90deg, transparent, rgba(247,151,30,0.3), transparent);
        margin: 2rem 0;
    }
</style>
""", unsafe_allow_html=True)

# =============================================================================
# DATA LOADING (CACHED)
# =============================================================================

@st.cache_data(ttl=3600)
def load_data():
    """Load and engineer features from cleaned data."""
    path = Path("data/processed/superstore_clean.csv")
    if not path.exists():
        st.error("Clean data not found. Run `python run_pipeline.py` first.")
        st.stop()
    df = pd.read_csv(path, parse_dates=["order_date", "ship_date"])
    df = engineer_features(df)
    return df

@st.cache_resource(ttl=3600)
def load_model():
    """Load trained classifier model."""
    try:
        artifacts = load_classifier()
        return artifacts
    except FileNotFoundError:
        st.warning("Model not trained yet. Run `python run_pipeline.py` first.")
        return None

@st.cache_data(ttl=3600)
def load_forecast():
    """Load forecast results."""
    forecast_path = Path("models/full_forecast.csv")
    monthly_path = Path("models/monthly_sales.csv")
    results_path = Path("models/forecast_results.json")
    
    if not forecast_path.exists():
        return None, None, None
    
    forecast_df = pd.read_csv(forecast_path, parse_dates=["date"])
    monthly_df = pd.read_csv(monthly_path, parse_dates=["ds"])
    
    results = None
    if results_path.exists():
        with open(results_path) as f:
            results = json.load(f)
    
    return forecast_df, monthly_df, results

@st.cache_data(ttl=3600)
def compute_dashboard_metrics(df):
    """Compute all dashboard metrics at once."""
    metrics = {}
    metrics["total_sales"] = df["sales"].sum()
    metrics["total_profit"] = df["profit"].sum()
    metrics["total_orders"] = len(df)
    metrics["avg_margin"] = df["profit_margin"].mean()
    metrics["loss_rate"] = df["is_loss"].mean() * 100
    metrics["avg_discount"] = df["discount"].mean() * 100
    metrics["avg_order_value"] = df["sales"].mean()
    
    # Category performance
    metrics["cat_perf"] = df.groupby("category").agg(
        sales=("sales", "sum"),
        profit=("profit", "sum"),
        margin=("profit_margin", "mean"),
        orders=("order_id", "nunique"),
        loss_rate=("is_loss", "mean"),
    ).sort_values("profit", ascending=False)
    
    # Monthly trends
    metrics["monthly"] = df.groupby("order_year_month").agg(
        sales=("sales", "sum"),
        profit=("profit", "sum"),
        orders=("order_id", "nunique"),
    ).reset_index()
    metrics["monthly"]["order_year_month"] = metrics["monthly"]["order_year_month"].astype(str)
    
    # Regional performance
    metrics["region_perf"] = df.groupby("region").agg(
        sales=("sales", "sum"),
        profit=("profit", "sum"),
        margin=("profit_margin", "mean"),
        orders=("order_id", "nunique"),
        loss_rate=("is_loss", "mean"),
        avg_discount=("discount", "mean"),
    ).sort_values("profit", ascending=False)
    
    # Discount tier analysis
    metrics["discount_tier"] = df.groupby("discount_tier").agg(
        sales=("sales", "sum"),
        profit=("profit", "sum"),
        margin=("profit_margin", "mean"),
        orders=("order_id", "nunique"),
        loss_rate=("is_loss", "mean"),
    ).reset_index()
    
    # Segment analysis
    metrics["segment_perf"] = df.groupby("segment").agg(
        sales=("sales", "sum"),
        profit=("profit", "sum"),
        margin=("profit_margin", "mean"),
        orders=("order_id", "nunique"),
    ).sort_values("profit", ascending=False)
    
    # Top sub-categories by loss
    metrics["worst_subcats"] = (
        df.groupby("sub_category")
        .agg(orders=("order_id", "nunique"), loss_rate=("is_loss", "mean"), profit=("profit", "sum"))
        .query("orders >= 20")
        .sort_values("loss_rate", ascending=False)
        .head(10)
    )
    
    return metrics

# =============================================================================
# SIDEBAR
# =============================================================================

def render_sidebar():
    """Render sidebar with navigation and filters."""
    with st.sidebar:
        st.markdown("""
        <div style="text-align: center; margin-bottom: 2rem;">
            <div style="font-size: 2.5rem; margin-bottom: 0.3rem;">📊</div>
            <div style="font-weight: 800; font-size: 1.1rem; background: linear-gradient(90deg, #f7971e, #ffd200); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;">Margin Intelligence</div>
            <div style="font-size: 0.7rem; color: rgba(255,255,255,0.4); text-transform: uppercase; letter-spacing: 0.1em; margin-top: 0.2rem;">Decision Support System</div>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown("---")
        
        nav_options = {
            "📈 Executive Overview": "overview",
            "🎯 Discount Simulator": "simulator",
            "📅 Forecast Explorer": "forecast",
            "🗺️ Deep Dive": "deepdive",
        }
        
        selected = st.radio(
            "Navigation",
            options=list(nav_options.keys()),
            label_visibility="collapsed",
            index=0,
        )
        st.session_state["page"] = nav_options[selected]
        
        st.markdown("---")
        st.markdown("""
        <div style="color: rgba(255,255,255,0.3); font-size: 0.7rem; text-align: center; padding: 1rem 0;">
            Built with ❤️ using Streamlit • Plotly • SHAP • Prophet
        </div>
        """, unsafe_allow_html=True)


# =============================================================================
# PAGE: EXECUTIVE OVERVIEW
# =============================================================================

def render_overview(df, metrics):
    """Executive Overview page with KPI panels and visual summary."""
    
    # ── Hero Section ──
    st.markdown("<h1>📊 Margin Intelligence Dashboard</h1>", unsafe_allow_html=True)
    st.markdown("""
    <p style="color: rgba(255,255,255,0.6); font-size: 1rem; margin-bottom: 1.5rem;">
        Real-time visibility into profitability drivers · <strong>18.7%</strong> of orders are unprofitable — 
        catch them <em>before</em> they ship.
    </p>
    """, unsafe_allow_html=True)
    
    # ── KPI Row ──
    col1, col2, col3, col4, col5 = st.columns(5)
    
    with col1:
        st.metric(
            "Total Sales",
            f"${metrics['total_sales']:,.0f}",
            delta=None,
        )
    with col2:
        profit_color = "normal" if metrics["total_profit"] > 0 else "inverse"
        st.metric(
            "Total Profit",
            f"${metrics['total_profit']:,.0f}",
            delta=f"{metrics['avg_margin']:.1f}% margin",
            delta_color=profit_color,
        )
    with col3:
        st.metric(
            "Avg Margin",
            f"{metrics['avg_margin']:.1f}%",
            delta=None,
        )
    with col4:
        st.metric(
            "Loss Rate",
            f"{metrics['loss_rate']:.1f}%",
            delta=f"{metrics['loss_rate']:.1f}% of orders",
            delta_color="inverse",
        )
    with col5:
        st.metric(
            "Total Orders",
            f"{metrics['total_orders']:,}",
            delta=f"Avg ${metrics['avg_order_value']:.0f}/order",
        )
    
    st.markdown("<hr>", unsafe_allow_html=True)
    
    # ── Charts Row 1: Monthly Sales + Discount Impact ──
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("<h3>📈 Monthly Sales & Profit</h3>", unsafe_allow_html=True)
        fig = go.Figure()
        
        monthly = metrics["monthly"].sort_values("order_year_month")
        
        fig.add_trace(go.Bar(
            x=monthly["order_year_month"],
            y=monthly["sales"],
            name="Sales",
            marker_color="rgba(247,151,30,0.7)",
            hovertemplate="%{y:$,.0f}<extra></extra>",
        ))
        
        fig.add_trace(go.Scatter(
            x=monthly["order_year_month"],
            y=monthly["profit"],
            name="Profit",
            yaxis="y2",
            line=dict(color="#00d4aa", width=3),
            hovertemplate="%{y:$,.0f}<extra></extra>",
        ))
        
        fig.update_layout(
            template="plotly_dark",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            hovermode="x unified",
            legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"),
            margin=dict(l=0, r=0, t=20, b=0),
            yaxis=dict(title="Sales ($)", gridcolor="rgba(255,255,255,0.05)"),
            yaxis2=dict(title="Profit ($)", overlaying="y", side="right", gridcolor="rgba(255,255,255,0.05)"),
            font=dict(color="rgba(255,255,255,0.7)"),
        )
        st.plotly_chart(fig, use_container_width=True)
    
    with col2:
        st.markdown("<h3>⚠️ Discount Impact on Margin</h3>", unsafe_allow_html=True)
        
        disc = metrics["discount_tier"].copy()
        tier_order = ["0%", "1-20%", "21-40%", "41%+"]
        disc["tier_order"] = disc["discount_tier"].apply(lambda x: tier_order.index(x) if x in tier_order else 99)
        disc = disc.sort_values("tier_order")
        
        fig = go.Figure()
        
        colors = ["rgba(0,212,170,0.8)", "rgba(247,151,30,0.8)", "rgba(255,87,87,0.8)", "rgba(187,51,51,0.8)"]
        
        fig.add_trace(go.Bar(
            x=disc["discount_tier"],
            y=disc["margin"],
            marker_color=colors,
            text=disc["margin"].apply(lambda x: f"{x:.1f}%"),
            textposition="outside",
            textfont=dict(color="white", size=14, weight="bold"),
            hovertemplate="Discount: %{x}<br>Margin: %{y:.1f}%<br>Orders: %{customdata:,}<extra></extra>",
            customdata=disc["orders"],
        ))
        
        fig.add_hline(y=0, line=dict(color="rgba(255,87,87,0.5)", width=2, dash="dash"))
        
        fig.update_layout(
            template="plotly_dark",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            hovermode="x",
            margin=dict(l=0, r=0, t=20, b=0),
            yaxis=dict(title="Avg Profit Margin (%)", gridcolor="rgba(255,255,255,0.05)"),
            font=dict(color="rgba(255,255,255,0.7)"),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)
    
    # ── Charts Row 2: Category Profitability + Regional ──
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("<h3>🏷️ Category Profitability</h3>", unsafe_allow_html=True)
        
        cat = metrics["cat_perf"].reset_index()
        cat["color"] = cat["profit"].apply(lambda x: "rgba(0,212,170,0.8)" if x > 0 else "rgba(255,87,87,0.8)")
        
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=cat["profit"],
            y=cat["category"],
            orientation="h",
            marker_color=cat["color"],
            text=cat["profit"].apply(lambda x: f"${x:,.0f}"),
            textposition="outside",
            textfont=dict(color="white", size=13),
            hovertemplate="%{y}: $%{x:,.0f}<br>Margin: %{customdata[0]:.1f}%<br>Loss Rate: %{customdata[1]:.1f}%<extra></extra>",
            customdata=cat[["margin", "loss_rate"]],
        ))
        
        fig.add_vline(x=0, line=dict(color="rgba(255,255,255,0.2)", width=1))
        
        fig.update_layout(
            template="plotly_dark",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            hovermode="y",
            margin=dict(l=0, r=0, t=20, b=0),
            xaxis=dict(title="Total Profit ($)", gridcolor="rgba(255,255,255,0.05)"),
            yaxis=dict(autorange="reversed"),
            font=dict(color="rgba(255,255,255,0.7)"),
            showlegend=False,
            height=250,
        )
        st.plotly_chart(fig, use_container_width=True)
    
    with col2:
        st.markdown("<h3>🗺️ Regional Performance</h3>", unsafe_allow_html=True)
        
        region = metrics["region_perf"].reset_index()
        
        fig = go.Figure()
        
        fig.add_trace(go.Bar(
            x=region["region"],
            y=region["margin"],
            marker_color=["rgba(247,151,30,0.8)", "rgba(0,212,170,0.8)", "rgba(100,149,237,0.8)", "rgba(255,87,87,0.8)"],
            text=region["margin"].apply(lambda x: f"{x:.1f}%"),
            textposition="outside",
            textfont=dict(color="white", size=14, weight="bold"),
            hovertemplate="%{x}<br>Margin: %{y:.1f}%<br>Sales: $%{customdata[0]:,.0f}<br>Loss Rate: %{customdata[1]:.1f}%<extra></extra>",
            customdata=region[["sales", "loss_rate"]].values,
        ))
        
        fig.add_hline(y=0, line=dict(color="rgba(255,255,255,0.2)", width=1, dash="dash"))
        overall_margin = metrics["avg_margin"]
        fig.add_hline(y=overall_margin, line=dict(color="rgba(247,151,30,0.4)", width=2, dash="dot"))
        
        fig.update_layout(
            template="plotly_dark",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            hovermode="x",
            margin=dict(l=0, r=0, t=20, b=0),
            yaxis=dict(title="Avg Margin (%)", gridcolor="rgba(255,255,255,0.05)"),
            font=dict(color="rgba(255,255,255,0.7)"),
            showlegend=False,
            height=250,
        )
        st.plotly_chart(fig, use_container_width=True)
    
    # ── Bottom Section: Loss Hotspots ──
    st.markdown("<h2>🔥 Loss Hotspots</h2>", unsafe_allow_html=True)
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("<h3>Highest Loss Rate by Sub-Category</h3>", unsafe_allow_html=True)
        worst = metrics["worst_subcats"].reset_index()
        
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=worst["loss_rate"] * 100,
            y=worst["sub_category"],
            orientation="h",
            marker=dict(
                color=worst["loss_rate"],
                colorscale="Reds",
                reversescale=False,
            ),
            text=worst["loss_rate"].apply(lambda x: f"{x*100:.1f}%"),
            textposition="outside",
            textfont=dict(color="white", size=12),
            hovertemplate="%{y}<br>Loss Rate: %{x:.1f}%<br>Profit: $%{customdata:,.0f}<extra></extra>",
            customdata=worst["profit"],
        ))
        
        fig.update_layout(
            template="plotly_dark",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            hovermode="y",
            margin=dict(l=0, r=0, t=10, b=0),
            xaxis=dict(title="Loss Rate (%)", gridcolor="rgba(255,255,255,0.05)", range=[0, 100]),
            yaxis=dict(autorange="reversed"),
            font=dict(color="rgba(255,255,255,0.7)"),
            showlegend=False,
            height=300,
        )
        st.plotly_chart(fig, use_container_width=True)
    
    with col2:
        st.markdown("<h3>Segment Breakdown</h3>", unsafe_allow_html=True)
        
        seg = metrics["segment_perf"].reset_index()
        
        fig = go.Figure()
        
        fig.add_trace(go.Bar(
            x=seg["segment"],
            y=seg["margin"],
            marker_color=["rgba(100,149,237,0.8)", "rgba(247,151,30,0.8)", "rgba(0,212,170,0.8)"],
            text=seg["margin"].apply(lambda x: f"{x:.1f}%"),
            textposition="outside",
            textfont=dict(color="white", size=14, weight="bold"),
            hovertemplate="%{x}<br>Margin: %{y:.1f}%<br>Sales: $%{customdata[0]:,.0f}<br>Orders: %{customdata[1]:,}<extra></extra>",
            customdata=seg[["sales", "orders"]].values,
        ))
        
        fig.add_hline(y=0, line=dict(color="rgba(255,255,255,0.2)", width=1, dash="dash"))
        
        fig.update_layout(
            template="plotly_dark",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            hovermode="x",
            margin=dict(l=0, r=0, t=10, b=0),
            yaxis=dict(title="Avg Margin (%)", gridcolor="rgba(255,255,255,0.05)"),
            font=dict(color="rgba(255,255,255,0.7)"),
            showlegend=False,
            height=300,
        )
        st.plotly_chart(fig, use_container_width=True)
    
    # ── Key Insight Cards ──
    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown("<h2>💡 Key Insights</h2>", unsafe_allow_html=True)
    
    col1, col2, col3, col4 = st.columns(4)
    
    worst_cat = metrics["cat_perf"]["margin"].idxmin()
    best_region = metrics["region_perf"]["margin"].idxmax()
    worst_tier = disc.loc[disc["margin"].idxmin(), "discount_tier"] if len(disc) > 0 else "41%+"
    
    with col1:
        st.markdown(f"""
        <div class="glass-card" style="text-align: center;">
            <div style="font-size: 2rem; margin-bottom: 0.5rem;">📉</div>
            <div style="color: rgba(255,255,255,0.5); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em;">Worst Category</div>
            <div style="color: #ff5757; font-size: 1.4rem; font-weight: 700;">{worst_cat}</div>
            <div style="color: rgba(255,255,255,0.4); font-size: 0.8rem;">{metrics['cat_perf'].loc[worst_cat, 'margin']:.1f}% margin</div>
        </div>
        """, unsafe_allow_html=True)
    
    with col2:
        st.markdown(f"""
        <div class="glass-card" style="text-align: center;">
            <div style="font-size: 2rem; margin-bottom: 0.5rem;">🏆</div>
            <div style="color: rgba(255,255,255,0.5); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em;">Best Region</div>
            <div style="color: #00d4aa; font-size: 1.4rem; font-weight: 700;">{best_region}</div>
            <div style="color: rgba(255,255,255,0.4); font-size: 0.8rem;">{metrics['region_perf'].loc[best_region, 'margin']:.1f}% margin</div>
        </div>
        """, unsafe_allow_html=True)
    
    with col3:
        st.markdown(f"""
        <div class="glass-card" style="text-align: center;">
            <div style="font-size: 2rem; margin-bottom: 0.5rem;">⚠️</div>
            <div style="color: rgba(255,255,255,0.5); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em;">Danger Discount</div>
            <div style="color: #ff5757; font-size: 1.4rem; font-weight: 700;">{worst_tier}</div>
            <div style="color: rgba(255,255,255,0.4); font-size: 0.8rem;">Margin turns negative</div>
        </div>
        """, unsafe_allow_html=True)
    
    with col4:
        loss_count = int(metrics["total_orders"] * metrics["loss_rate"] / 100)
        st.markdown(f"""
        <div class="glass-card" style="text-align: center;">
            <div style="font-size: 2rem; margin-bottom: 0.5rem;">🔴</div>
            <div style="color: rgba(255,255,255,0.5); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em;">Unprofitable Orders</div>
            <div style="color: #ff5757; font-size: 1.4rem; font-weight: 700;">{loss_count:,}</div>
            <div style="color: rgba(255,255,255,0.4); font-size: 0.8rem;">of {metrics['total_orders']:,} total ({metrics['loss_rate']:.1f}%)</div>
        </div>
        """, unsafe_allow_html=True)


# =============================================================================
# PAGE: DISCOUNT SIMULATOR
# =============================================================================

def render_simulator(df, artifacts):
    """Discount Approval Simulator — the core interactive feature."""
    
    st.markdown("<h1>🎯 Discount Approval Simulator</h1>", unsafe_allow_html=True)
    st.markdown("""
    <p style="color: rgba(255,255,255,0.6); font-size: 1rem; margin-bottom: 1.5rem;">
        Simulate a proposed discount and see whether it will be profitable — <em>before you approve it.</em>
        The model analyzes <strong>8 features</strong> to predict loss risk and recommends a safe discount ceiling.
    </p>
    """, unsafe_allow_html=True)
    
    st.markdown("<hr>", unsafe_allow_html=True)
    
    col1, col2 = st.columns([1, 1.3])
    
    with col1:
        st.markdown("<h3>📋 Order Configuration</h3>", unsafe_allow_html=True)
        
        # Get unique values for dropdowns
        categories = sorted(df["category"].unique())
        sub_categories = sorted(df["sub_category"].unique())
        regions = sorted(df["region"].unique())
        segments = sorted(df["segment"].unique())
        ship_modes = sorted(df["ship_mode"].unique())
        
        category = st.selectbox("Product Category", categories, key="sim_cat")
        
        # Filter sub-categories by selected category
        cat_subs = sorted(df[df["category"] == category]["sub_category"].unique())
        sub_category = st.selectbox("Sub-Category", cat_subs if cat_subs else sub_categories, key="sim_sub")
        
        region = st.selectbox("Region", regions, key="sim_region")
        segment = st.selectbox("Customer Segment", segments, key="sim_seg")
        
        col_a, col_b = st.columns(2)
        with col_a:
            quantity = st.number_input("Order Quantity", min_value=1, max_value=100, value=3, key="sim_qty")
        with col_b:
            ship_mode = st.selectbox("Ship Mode", ship_modes, index=list(ship_modes).index("Standard Class") if "Standard Class" in ship_modes else 0, key="sim_ship")
        
        discount = st.slider(
            "Proposed Discount (%)",
            min_value=0,
            max_value=80,
            value=20,
            step=1,
            format="%d%%",
            key="sim_disc",
        )
        
        simulate = st.button("🔮 Predict Profitability", type="primary", use_container_width=True)
    
    with col2:
        st.markdown("<h3>📊 Simulator Results</h3>", unsafe_allow_html=True)
        
        if "simulation_result" not in st.session_state:
            st.session_state["simulation_result"] = None
        
        if simulate or st.session_state["simulation_result"]:
            
            if simulate:
                with st.spinner("Analyzing..."):
                    time.sleep(0.3)  # Micro-interaction feel
                    
                    features = {
                        "category": category,
                        "sub_category": sub_category,
                        "region": region,
                        "segment": segment,
                        "discount": discount / 100.0,
                        "quantity": quantity,
                        "ship_mode": ship_mode,
                        "shipping_delay": 4,
                    }
                    
                    try:
                        prediction = predict_loss(features, artifacts)
                        optimization = compute_safe_discount(
                            category=category,
                            sub_category=sub_category,
                            region=region,
                            segment=segment,
                            quantity=quantity,
                            ship_mode=ship_mode,
                            artifacts=artifacts,
                        )
                        
                        st.session_state["simulation_result"] = {
                            "prediction": prediction,
                            "optimization": optimization,
                            "features": features,
                        }
                    except Exception as e:
                        st.error(f"Simulation error: {e}")
                        return
            
            result = st.session_state["simulation_result"]
            prediction = result["prediction"]
            optimization = result["optimization"]
            
            risk = prediction["loss_probability"] * 100
            risk_color = "rgba(0,212,170,1)" if risk < 30 else "rgba(247,151,30,1)" if risk < 60 else "rgba(255,87,87,1)"
            
            # ── Risk Gauge ──
            fig = go.Figure(go.Indicator(
                mode="gauge+number+delta",
                value=risk,
                domain={"x": [0, 1], "y": [0, 1]},
                number={"font": {"size": 40, "color": risk_color}, "suffix": "%"},
                title={"text": "Loss Risk", "font": {"size": 18, "color": "rgba(255,255,255,0.8)"}},
                gauge={
                    "axis": {"range": [0, 100], "tickwidth": 1, "tickcolor": "rgba(255,255,255,0.3)"},
                    "bar": {"color": risk_color, "thickness": 0.8},
                    "bgcolor": "rgba(255,255,255,0.05)",
                    "borderwidth": 0,
                    "steps": [
                        {"range": [0, 30], "color": "rgba(0,212,170,0.15)"},
                        {"range": [30, 60], "color": "rgba(247,151,30,0.15)"},
                        {"range": [60, 100], "color": "rgba(255,87,87,0.15)"},
                    ],
                    "threshold": {
                        "line": {"color": "red", "width": 3},
                        "thickness": 0.75,
                        "value": 50,
                    },
                },
            ))
            fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font={"color": "rgba(255,255,255,0.7)"},
                margin=dict(l=20, r=20, t=30, b=0),
                height=220,
            )
            st.plotly_chart(fig, use_container_width=True)
            
            # ── Result Cards ──
            col_a, col_b, col_c = st.columns(3)
            
            with col_a:
                outcome = "✅ PROFITABLE" if risk < 50 else "❌ LOSS RISK"
                outcome_color = "#00d4aa" if risk < 50 else "#ff5757"
                st.markdown(f"""
                <div class="result-card">
                    <div class="label">Prediction</div>
                    <div class="value" style="color: {outcome_color}; font-size: 1.6rem;">{outcome}</div>
                    <div class="sub">at {discount}% discount</div>
                </div>
                """, unsafe_allow_html=True)
            
            with col_b:
                st.markdown(f"""
                <div class="result-card">
                    <div class="label">Safe Max Discount</div>
                    <div class="value" style="color: #00d4aa;">{optimization['safe_discount_pct']:.0f}%</div>
                    <div class="sub">to stay profitable</div>
                </div>
                """, unsafe_allow_html=True)
            
            with col_c:
                delta_str = f"↓ {optimization['current_loss_risk'] - optimization['safe_loss_risk']:.0f}pp risk"
                st.markdown(f"""
                <div class="result-card">
                    <div class="label">Risk at Safe Discount</div>
                    <div class="value" style="color: {'#00d4aa' if optimization['safe_loss_risk'] < 50 else '#f7971e'};">{optimization['safe_loss_risk']:.0f}%</div>
                    <div class="sub">{delta_str}</div>
                </div>
                """, unsafe_allow_html=True)
            
            # ── Top SHAP Factors ──
            st.markdown("<h3>🔍 Key Drivers of This Prediction</h3>", unsafe_allow_html=True)
            
            shap_factors = prediction["top_3_shap"]
            for i, (feat, val) in enumerate(shap_factors):
                direction = "↑ increases loss risk" if val > 0 else "↓ decreases loss risk"
                feat_label = feat.replace("_encoded", "").replace("_", " ").title()
                bar_color = "rgba(255,87,87,0.6)" if val > 0 else "rgba(0,212,170,0.6)"
                bar_pct = min(abs(val) * 5, 100)
                
                st.markdown(f"""
                <div class="glass-card" style="padding: 0.8rem 1rem; margin-bottom: 0.5rem;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 0.3rem;">
                        <span style="color: rgba(255,255,255,0.9); font-weight: 600;">{feat_label}</span>
                        <span style="color: {bar_color}; font-weight: 600;">{direction}</span>
                    </div>
                    <div style="background: rgba(255,255,255,0.05); border-radius: 10px; height: 8px; overflow: hidden;">
                        <div style="background: {bar_color}; width: {bar_pct:.0f}%; height: 100%; border-radius: 10px; transition: width 0.5s ease;"></div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
            
            # ── Discount Scenarios ──
            st.markdown("<h3>📈 Discount vs Risk Profile</h3>", unsafe_allow_html=True)
            
            scan = optimization.get("discount_scan", [])
            if scan:
                scan_df = pd.DataFrame(scan)
                
                fig = go.Figure()
                
                # Risk line
                fig.add_trace(go.Scatter(
                    x=scan_df["discount"] * 100,
                    y=scan_df["loss_risk"] * 100,
                    name="Loss Risk",
                    line=dict(color="#ff5757", width=3),
                    fill="tozeroy",
                    fillcolor="rgba(255,87,87,0.1)",
                    hovertemplate="Discount: %{x:.0f}%<br>Risk: %{y:.1f}%<extra></extra>",
                ))
                
                # Estimated margin line
                fig.add_trace(go.Scatter(
                    x=scan_df["discount"] * 100,
                    y=scan_df["estimated_margin"],
                    name="Est. Margin ($)",
                    yaxis="y2",
                    line=dict(color="#00d4aa", width=3),
                    hovertemplate="Discount: %{x:.0f}%<br>Est. Margin: $%{y:.2f}<extra></extra>",
                ))
                
                # Safe discount marker
                fig.add_vline(
                    x=optimization["safe_discount_pct"],
                    line=dict(color="#00d4aa", width=2, dash="dash"),
                    annotation_text=f"Safe: {optimization['safe_discount_pct']:.0f}%",
                    annotation_position="top right",
                    annotation_font=dict(color="#00d4aa"),
                )
                
                # Current discount marker
                fig.add_vline(
                    x=discount,
                    line=dict(color="#ff5757", width=2, dash="dot"),
                    annotation_text=f"Proposed: {discount}%",
                    annotation_position="bottom left",
                    annotation_font=dict(color="#ff5757"),
                )
                
                fig.update_layout(
                    template="plotly_dark",
                    plot_bgcolor="rgba(0,0,0,0)",
                    paper_bgcolor="rgba(0,0,0,0)",
                    hovermode="x unified",
                    legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"),
                    margin=dict(l=0, r=0, t=20, b=0),
                    xaxis=dict(title="Discount (%)", gridcolor="rgba(255,255,255,0.05)"),
                    yaxis=dict(title="Loss Risk (%)", gridcolor="rgba(255,255,255,0.05)", range=[0, 100]),
                    yaxis2=dict(title="Est. Margin ($)", overlaying="y", side="right", gridcolor="rgba(255,255,255,0.05)"),
                    font=dict(color="rgba(255,255,255,0.7)"),
                )
                st.plotly_chart(fig, use_container_width=True)
        
        else:
            # Placeholder
            st.markdown("""
            <div class="glass-card" style="text-align: center; padding: 3rem;">
                <div style="font-size: 3rem; margin-bottom: 1rem;">🔮</div>
                <div style="color: rgba(255,255,255,0.5); font-size: 1.1rem;">
                    Configure an order on the left and click <strong>"Predict Profitability"</strong>
                </div>
                <div style="color: rgba(255,255,255,0.3); font-size: 0.9rem; margin-top: 0.5rem;">
                    The simulator will analyze 8 features using an XGBoost model with SHAP explanations.
                </div>
            </div>
            """, unsafe_allow_html=True)


# =============================================================================
# PAGE: FORECAST EXPLORER
# =============================================================================

def render_forecast(df, forecast_df, monthly_df, forecast_results):
    """Forecast Explorer page."""
    
    st.markdown("<h1>📅 Forecast Explorer</h1>", unsafe_allow_html=True)
    st.markdown("""
    <p style="color: rgba(255,255,255,0.6); font-size: 1rem; margin-bottom: 1.5rem;">
        Monthly sales forecasts built with <strong>Prophet</strong> (classical) and <strong>LSTM</strong> (deep learning).
        The best model is selected automatically based on held-out MAPE.
    </p>
    """, unsafe_allow_html=True)
    
    if forecast_df is None or monthly_df is None:
        st.warning("Forecast data not found. Run `python run_pipeline.py` first.")
        return
    
    st.markdown("<hr>", unsafe_allow_html=True)
    
    col1, col2 = st.columns([2, 1])
    
    with col1:
        st.markdown("<h3>📈 Actual vs Predicted Sales</h3>", unsafe_allow_html=True)
        
        # Merge actual with forecast
        monthly_agg = monthly_df.rename(columns={"ds": "date", "y": "actual_sales"})
        monthly_agg["date"] = pd.to_datetime(monthly_agg["date"])
        forecast_df["date"] = pd.to_datetime(forecast_df["date"])
        
        merged = monthly_agg.merge(forecast_df, on="date", how="outer").sort_values("date")
        
        fig = go.Figure()
        
        # Actual sales
        fig.add_trace(go.Scatter(
            x=merged["date"],
            y=merged["actual_sales"],
            name="Actual Sales",
            line=dict(color="#ffd200", width=3),
            hovertemplate="%{x|%b %Y}<br>Actual: $%{y:,.0f}<extra></extra>",
        ))
        
        # Forecast
        fig.add_trace(go.Scatter(
            x=merged["date"],
            y=merged["forecast"],
            name="Forecast (Prophet)",
            line=dict(color="#00d4aa", width=3, dash="dash"),
            hovertemplate="%{x|%b %Y}<br>Forecast: $%{y:,.0f}<extra></extra>",
        ))
        
        # Confidence interval
        fig.add_trace(go.Scatter(
            x=merged["date"],
            y=merged["forecast_upper"],
            fill=None,
            mode="none",
            showlegend=False,
        ))
        fig.add_trace(go.Scatter(
            x=merged["date"],
            y=merged["forecast_lower"],
            fill="tonexty",
            fillcolor="rgba(0,212,170,0.1)",
            mode="none",
            name="Confidence Interval",
            hovertemplate="Upper: $%{y:,.0f}<extra></extra>",
        ))
        
        fig.update_layout(
            template="plotly_dark",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            hovermode="x unified",
            legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"),
            margin=dict(l=0, r=0, t=10, b=0),
            xaxis=dict(title="", gridcolor="rgba(255,255,255,0.05)"),
            yaxis=dict(title="Monthly Sales ($)", gridcolor="rgba(255,255,255,0.05)"),
            font=dict(color="rgba(255,255,255,0.7)"),
        )
        st.plotly_chart(fig, use_container_width=True)
    
    with col2:
        st.markdown("<h3>📊 Model Comparison</h3>", unsafe_allow_html=True)
        
        if forecast_results:
            prophet_m = forecast_results["prophet"]["metrics"]
            lstm_m = forecast_results["lstm"]["metrics"]
            
            better = forecast_results.get("better_model", "Prophet")
            
            st.markdown(f"""
            <div class="glass-card" style="text-align: center; margin-bottom: 1rem;">
                <div style="color: rgba(255,255,255,0.5); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em;">Best Model</div>
                <div style="color: #00d4aa; font-size: 1.8rem; font-weight: 700;">{better}</div>
                <div style="color: rgba(255,255,255,0.4); font-size: 0.8rem;">Lower MAPE wins</div>
            </div>
            """, unsafe_allow_html=True)
            
            metrics_data = pd.DataFrame({
                "Metric": ["MAE", "RMSE", "MAPE"],
                "Prophet": [f"${prophet_m['mae']:,.0f}", f"${prophet_m['rmse']:,.0f}", f"{prophet_m['mape']:.2f}%"],
                "LSTM": [f"${lstm_m['mae']:,.0f}", f"${lstm_m['rmse']:,.0f}", f"{lstm_m['mape']:.2f}%"],
            })
            
            fig = go.Figure(data=[go.Table(
                header=dict(
                    values=["<b>Metric</b>", "<b>Prophet</b>", "<b>LSTM</b>"],
                    fill_color="rgba(247,151,30,0.2)",
                    align="center",
                    font=dict(color="white", size=13),
                    height=35,
                ),
                cells=dict(
                    values=[metrics_data["Metric"], metrics_data["Prophet"], metrics_data["LSTM"]],
                    fill_color=[
                        ["rgba(255,255,255,0.03)", "rgba(255,255,255,0.06)", "rgba(255,255,255,0.03)"],
                        ["rgba(255,255,255,0.03)", "rgba(255,255,255,0.06)", "rgba(255,255,255,0.03)"],
                        ["rgba(255,255,255,0.03)", "rgba(255,255,255,0.06)", "rgba(255,255,255,0.03)"],
                    ],
                    align="center",
                    font=dict(color="rgba(255,255,255,0.8)", size=13),
                    height=35,
                ),
            )])
            fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=0, t=0, b=0),
                height=150,
            )
            st.plotly_chart(fig, use_container_width=True)
            
            # Seasonality decomposition
            st.markdown("<h3>🔄 Seasonality</h3>", unsafe_allow_html=True)
            
            try:
                from prophet import Prophet
                monthly_ts = monthly_df.rename(columns={"ds": "ds", "y": "y"})
                train = monthly_ts[:-6]
                model = Prophet(yearly_seasonality=True, weekly_seasonality=False, daily_seasonality=False)
                model.fit(train)
                
                future_dates = model.make_future_dataframe(periods=12, freq="MS")
                forecast = model.predict(future_dates)
                
                # Plot yearly seasonality
                fig2 = go.Figure()
                if "yearly" in model.seasonalities_plot:
                    pass
                
                # Extract yearly component
                yearly = forecast[["ds", "yearly"]].dropna()
                fig2.add_trace(go.Scatter(
                    x=pd.to_datetime(yearly["ds"].dt.month.astype(str) + "-01", format="%m-%d"),
                    y=yearly["yearly"],
                    mode="lines+markers",
                    line=dict(color="#f7971e", width=3),
                    marker=dict(size=6, color="#ffd200"),
                    hovertemplate="Month: %{x|%b}<br>Effect: $%{y:,.0f}<extra></extra>",
                ))
                
                fig2.update_layout(
                    template="plotly_dark",
                    plot_bgcolor="rgba(0,0,0,0)",
                    paper_bgcolor="rgba(0,0,0,0)",
                    hovermode="x",
                    margin=dict(l=0, r=0, t=10, b=0),
                    xaxis=dict(title="", dtick="M1", tickformat="%b", gridcolor="rgba(255,255,255,0.05)"),
                    yaxis=dict(title="Seasonal Effect ($)", gridcolor="rgba(255,255,255,0.05)"),
                    font=dict(color="rgba(255,255,255,0.7)"),
                )
                st.plotly_chart(fig2, use_container_width=True)
            except Exception:
                st.markdown("<div style='color: rgba(255,255,255,0.4);'>Seasonality decomposition unavailable</div>", unsafe_allow_html=True)
        else:
            st.markdown("<div style='color: rgba(255,255,255,0.4);'>No comparison data available</div>", unsafe_allow_html=True)
    
    # ── Prophet Components ──
    if forecast_results:
        st.markdown("<hr>", unsafe_allow_html=True)
        st.markdown("<h2>📉 Forecast Components</h2>", unsafe_allow_html=True)
        
        col1, col2, col3 = st.columns(3)
        
        total_forecast = forecast_df["forecast"].sum()
        avg_forecast = forecast_df["forecast"].mean()
        peak_month = forecast_df.loc[forecast_df["forecast"].idxmax(), "date"]
        
        with col1:
            st.markdown(f"""
            <div class="glass-card" style="text-align: center;">
                <div class="label">Total Forecast Sales</div>
                <div style="font-size: 1.8rem; font-weight: 700; color: #ffd200;">${total_forecast:,.0f}</div>
            </div>
            """, unsafe_allow_html=True)
        
        with col2:
            st.markdown(f"""
            <div class="glass-card" style="text-align: center;">
                <div class="label">Avg Monthly Forecast</div>
                <div style="font-size: 1.8rem; font-weight: 700; color: #00d4aa;">${avg_forecast:,.0f}</div>
            </div>
            """, unsafe_allow_html=True)
        
        with col3:
            st.markdown(f"""
            <div class="glass-card" style="text-align: center;">
                <div class="label">Peak Forecast Month</div>
                <div style="font-size: 1.4rem; font-weight: 700; color: #f7971e;">{peak_month.strftime('%b %Y') if hasattr(peak_month, 'strftime') else str(peak_month)}</div>
            </div>
            """, unsafe_allow_html=True)


# =============================================================================
# PAGE: DEEP DIVE
# =============================================================================

def render_deepdive(df, metrics):
    """Regional and Category Deep Dive page."""
    
    st.markdown("<h1>🗺️ Regional & Category Deep Dive</h1>", unsafe_allow_html=True)
    st.markdown("""
    <p style="color: rgba(255,255,255,0.6); font-size: 1rem; margin-bottom: 1.5rem;">
        Drill into performance by region, category, and segment. Identify loss hotspots and pricing gaps.
    </p>
    """, unsafe_allow_html=True)
    
    st.markdown("<hr>", unsafe_allow_html=True)
    
    # ── Filters ──
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        regions = ["All"] + sorted(df["region"].unique().tolist())
        selected_region = st.selectbox("Filter Region", regions, key="dd_region")
    with col2:
        cats = ["All"] + sorted(df["category"].unique().tolist())
        selected_cat = st.selectbox("Filter Category", cats, key="dd_cat")
    with col3:
        segs = ["All"] + sorted(df["segment"].unique().tolist())
        selected_seg = st.selectbox("Filter Segment", segs, key="dd_seg")
    with col4:
        year_range = sorted(df["order_year"].unique())
        selected_year = st.selectbox("Filter Year", ["All"] + [str(y) for y in year_range], key="dd_year")
    
    # Apply filters
    filtered = df.copy()
    if selected_region != "All":
        filtered = filtered[filtered["region"] == selected_region]
    if selected_cat != "All":
        filtered = filtered[filtered["category"] == selected_cat]
    if selected_seg != "All":
        filtered = filtered[filtered["segment"] == selected_seg]
    if selected_year != "All":
        filtered = filtered[filtered["order_year"] == int(selected_year)]
    
    # ── Filtered KPIs ──
    f_sales = filtered["sales"].sum()
    f_profit = filtered["profit"].sum()
    f_margin = filtered["profit_margin"].mean()
    f_loss = filtered["is_loss"].mean() * 100
    f_orders = len(filtered)
    
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Filtered Sales", f"${f_sales:,.0f}")
    with col2:
        st.metric("Filtered Profit", f"${f_profit:,.0f}", delta=f"{f_margin:.1f}%")
    with col3:
        st.metric("Avg Margin", f"{f_margin:.1f}%")
    with col4:
        st.metric("Loss Rate", f"{f_loss:.1f}%", delta_color="inverse")
    with col5:
        st.metric("Orders", f"{f_orders:,}")
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    # ── Sub-Category Drill Down ──
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("<h3>📊 Sub-Category Profitability</h3>", unsafe_allow_html=True)
        
        subcat = (
            filtered.groupby(["category", "sub_category"])
            .agg(
                orders=("order_id", "count"),
                sales=("sales", "sum"),
                profit=("profit", "sum"),
                margin=("profit_margin", "mean"),
                loss_rate=("is_loss", "mean"),
            )
            .reset_index()
            .sort_values("profit")
        )
        
        if len(subcat) > 0:
            subcat["color"] = subcat["profit"].apply(lambda x: "rgba(0,212,170,0.8)" if x > 0 else "rgba(255,87,87,0.8)")
            subcat["label"] = subcat["sub_category"] + " (" + subcat["category"] + ")"
            
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=subcat["profit"],
                y=subcat["label"],
                orientation="h",
                marker_color=subcat["color"],
                text=subcat["profit"].apply(lambda x: f"${x:,.0f}"),
                textposition="outside",
                textfont=dict(color="white", size=11),
                hovertemplate="%{y}<br>Profit: $%{x:,.0f}<br>Margin: %{customdata[0]:.1f}%<br>Loss Rate: %{customdata[1]:.1f}%<extra></extra>",
                customdata=subcat[["margin", "loss_rate"]].values,
            ))
            fig.add_vline(x=0, line=dict(color="rgba(255,255,255,0.2)", width=1))
            fig.update_layout(
                template="plotly_dark",
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                hovermode="y",
                margin=dict(l=0, r=0, t=10, b=0),
                xaxis=dict(title="Total Profit ($)", gridcolor="rgba(255,255,255,0.05)"),
                yaxis=dict(autorange="reversed"),
                font=dict(color="rgba(255,255,255,0.7)"),
                showlegend=False,
                height=max(50 * len(subcat), 200),
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.markdown("<div style='color: rgba(255,255,255,0.4);'>No data for selected filters</div>", unsafe_allow_html=True)
    
    with col2:
        st.markdown("<h3>📈 Monthly Trend (Filtered)</h3>", unsafe_allow_html=True)
        
        monthly_filtered = (
            filtered.groupby("order_year_month")
            .agg(sales=("sales", "sum"), profit=("profit", "sum"))
            .reset_index()
            .sort_values("order_year_month")
        )
        
        if len(monthly_filtered) > 0:
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=monthly_filtered["order_year_month"],
                y=monthly_filtered["sales"],
                name="Sales",
                marker_color="rgba(247,151,30,0.7)",
            ))
            fig.add_trace(go.Scatter(
                x=monthly_filtered["order_year_month"],
                y=monthly_filtered["profit"],
                name="Profit",
                yaxis="y2",
                line=dict(color="#00d4aa", width=3),
            ))
            fig.update_layout(
                template="plotly_dark",
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                hovermode="x unified",
                legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"),
                margin=dict(l=0, r=0, t=10, b=0),
                xaxis=dict(title="", gridcolor="rgba(255,255,255,0.05)"),
                yaxis=dict(title="Sales ($)", gridcolor="rgba(255,255,255,0.05)"),
                yaxis2=dict(title="Profit ($)", overlaying="y", side="right", gridcolor="rgba(255,255,255,0.05)"),
                font=dict(color="rgba(255,255,255,0.7)"),
                height=300,
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.markdown("<div style='color: rgba(255,255,255,0.4);'>No data for selected filters</div>", unsafe_allow_html=True)
    
    # ── Discount vs Margin Scatter ──
    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown("<h3>💸 Discount vs Margin Relationship</h3>", unsafe_allow_html=True)
    
    # Sample for performance
    scatter_sample = filtered.sample(min(500, len(filtered)), random_state=42)
    
    fig = go.Figure()
    
    for cat_name in scatter_sample["category"].unique():
        cat_data = scatter_sample[scatter_sample["category"] == cat_name]
        fig.add_trace(go.Scatter(
            x=cat_data["discount"] * 100,
            y=cat_data["profit_margin"],
            mode="markers",
            name=cat_name,
            marker=dict(
                size=cat_data["sales"] / cat_data["sales"].max() * 30 + 5,
                opacity=0.6,
                line=dict(width=1, color="rgba(255,255,255,0.1)"),
            ),
            hovertemplate="Discount: %{x:.0f}%<br>Margin: %{y:.1f}%<br>Sales: $%{customdata:,.0f}<extra>%{text}</extra>",
            text=cat_data["category"],
            customdata=cat_data["sales"],
        ))
    
    # Trend line
    slope_data = scatter_sample[["discount", "profit_margin"]].dropna()
    if len(slope_data) > 10:
        x_vals = np.linspace(0, 80, 100)
        z = np.polyfit(slope_data["discount"] * 100, slope_data["profit_margin"], 1)
        p = np.poly1d(z)
        y_vals = p(x_vals)
        fig.add_trace(go.Scatter(
            x=x_vals,
            y=y_vals,
            mode="lines",
            name="Trend",
            line=dict(color="rgba(255,255,255,0.4)", width=2, dash="dash"),
        ))
    
    fig.add_hline(y=0, line=dict(color="rgba(255,87,87,0.5)", width=1, dash="dot"))
    
    fig.update_layout(
        template="plotly_dark",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        hovermode="closest",
        legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"),
        margin=dict(l=0, r=0, t=10, b=0),
        xaxis=dict(title="Discount (%)", gridcolor="rgba(255,255,255,0.05)"),
        yaxis=dict(title="Profit Margin (%)", gridcolor="rgba(255,255,255,0.05)"),
        font=dict(color="rgba(255,255,255,0.7)"),
    )
    st.plotly_chart(fig, use_container_width=True)
    
    # ── Data Table ──
    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown("<h3>📋 Filtered Data Explorer</h3>", unsafe_allow_html=True)
    
    show_cols = ["order_id", "order_date", "category", "sub_category", "region", "segment", 
                 "sales", "discount", "profit", "profit_margin", "is_loss"]
    
    display_df = filtered[show_cols].copy()
    display_df["profit_margin"] = display_df["profit_margin"].round(1)
    display_df["discount"] = (display_df["discount"] * 100).round(1).astype(str) + "%"
    display_df["is_loss"] = display_df["is_loss"].map({0: "✅", 1: "❌"})
    display_df["order_date"] = display_df["order_date"].dt.strftime("%Y-%m-%d")
    
    st.dataframe(
        display_df.head(100),
        use_container_width=True,
        column_config={
            "sales": st.column_config.NumberColumn("Sales", format="$%.2f"),
            "profit": st.column_config.NumberColumn("Profit", format="$%.2f"),
        },
        hide_index=True,
    )


# =============================================================================
# MAIN APP
# =============================================================================

def main():
    """Main entry point for the dashboard."""
    
    # Initialization
    if "page" not in st.session_state:
        st.session_state["page"] = "overview"
    
    # Render sidebar
    render_sidebar()
    
    # Load data (with loading animation)
    with st.spinner("Loading data..."):
        df = load_data()
        metrics = compute_dashboard_metrics(df)
        artifacts = load_model()
        forecast_df, monthly_df, forecast_results = load_forecast()
    
    # Route to correct page
    if st.session_state["page"] == "overview":
        render_overview(df, metrics)
    elif st.session_state["page"] == "simulator":
        render_simulator(df, artifacts)
    elif st.session_state["page"] == "forecast":
        render_forecast(df, forecast_df, monthly_df, forecast_results)
    elif st.session_state["page"] == "deepdive":
        render_deepdive(df, metrics)
    
    # Footer
    st.markdown("""
    <div class="footer">
        Superstore Margin Intelligence System · Built with Streamlit, Plotly, SHAP, Prophet, XGBoost<br>
        <span style="color: rgba(255,255,255,0.2);">Data: Tableau Sample Superstore · 8,399 orders · 2010–2017</span>
    </div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
