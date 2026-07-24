"""
MandiIQ — Discount Approval Simulator (Superstore extension).

Calculate optimal discount, safe range, and loss probability for retail promotions.
SHAP contribution mini-chart explains the model's decision.

Alche Studio Design: glass cards, glass result panels, section headers,
interpretation boxes, consistent monochrome-lime palette.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from mandi_rdd.dashboard.theme import (
    inject_theme, TURMERIC, RUST, SAGE, SLATE, MUTED, FAINT, INK
)
from mandi_rdd.dashboard.plotly_theme import make_themed_figure


def render():
    inject_theme()
    st.markdown("""
        <div class="page-hero" style="margin-bottom:2rem;">
          <div>
            <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
              Retail Analytics
            </div>
            <h1 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:300;font-size:clamp(1.6rem,3vw,2.4rem);color:#ffffff;letter-spacing:0.03em;text-transform:uppercase;margin-bottom:0.5rem;">
              Discount Approval <span style="font-weight:600;color:#d7ff00;">Simulator</span>
            </h1>
            <p style="color:#7e7e7e;max-width:680px;line-height:1.7;font-size:0.9rem;">
                Model-driven discount recommendations for retail promotions. Enter product details
                to get optimal discount, safe range, and loss probability — based on historical
                Superstore transaction patterns.
            </p>
          </div>
        </div>
    """, unsafe_allow_html=True)

    # ── Input Form ──
    st.markdown("""
        <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.5rem;">
          01 / Product Details
        </div>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns(2)

    with col1:
        category = st.selectbox(
            "Category",
            ["Technology", "Furniture", "Office Supplies"],
        )

        subcategory = st.selectbox(
            "Sub-Category",
            _get_subcategories(category),
        )

        region = st.selectbox(
            "Region",
            ["West", "East", "Central", "South"],
        )

    with col2:
        segment = st.selectbox(
            "Customer Segment",
            ["Consumer", "Corporate", "Home Office"],
        )

        ship_mode = st.selectbox(
            "Ship Mode",
            ["Standard Class", "Second Class", "First Class", "Same Day"],
        )

        base_price = st.number_input(
            "Base Price ($)",
            min_value=10.0,
            max_value=5000.0,
            value=100.0,
            step=10.0,
        )

    st.markdown("<hr style='border-color:rgba(255,255,255,0.07);margin:1.5rem 0;'>", unsafe_allow_html=True)

    # ── Run Simulation ──
    simulate = st.button("Run Simulation", type="primary")

    if not simulate:
        st.markdown("""
            <div class="glass" style="padding:2rem;text-align:center;">
                <span style="color:#7e7e7e;">
                    Enter product details and click <strong style="color:#d7ff00;">Run Simulation</strong>
                    to see discount recommendations.
                </span>
            </div>
        """, unsafe_allow_html=True)
        return

    # Simulate Model Output
    try:
        from mandi_rdd.models.discount_model import predict_discount, explain_prediction
        result = predict_discount(
            category=category,
            subcategory=subcategory,
            region=region,
            segment=segment,
            ship_mode=ship_mode,
            base_price=base_price,
        )
        explanation = explain_prediction(result)
    except ImportError:
        result = None
        explanation = None

    if result is None:
        st.markdown("""
            <div class="glass" style="padding:1.5rem;border-color:#D9663B;">
                <h3 style="color:#D9663B;margin-top:0;">⚠️ Model Not Available</h3>
                <p style="color:#bababa;font-size:0.85rem;">
                    The discount prediction model is not installed. This feature requires
                    the Superstore loss-risk classifier to be trained and deployed.
                </p>
                <p style="color:#7e7e7e;font-size:0.75rem;">
                    To enable: train the model using historical transaction data from the
                    Superstore dataset, then deploy it to <code>mandi_rdd/models/discount_model.py</code>
                </p>
            </div>
        """, unsafe_allow_html=True)

        st.markdown("""
            <div style="margin-top:1.5rem;">
              <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.5rem;">
                EXPECTED OUTPUT
              </div>
              <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.2rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:1rem;">
                Expected Output Format
              </h2>
            </div>
        """, unsafe_allow_html=True)

        st.markdown("""
            <div class="glass" style="padding:1.5rem;">
                <p style="color:#bababa;font-size:0.85rem;margin-bottom:1rem;">
                    When the model is deployed, results would appear like this:
                </p>
        """, unsafe_allow_html=True)

        _render_result_card(
            optimal_discount=15.0,
            safe_min=10.0,
            safe_max=20.0,
            loss_probability=0.12,
            expected_margin=0.18,
        )

        _render_shap_chart({
            "Category": 0.15,
            "Region": -0.08,
            "Base Price": 0.12,
            "Ship Mode": -0.05,
            "Segment": 0.03,
        })

        st.markdown("</div>", unsafe_allow_html=True)
        return

    # ── Real Model Output ──
    st.markdown("""
        <div style="margin-top:1.5rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.5rem;">
            02 / Recommendation
          </div>
        </div>
    """, unsafe_allow_html=True)
    _render_result_card(
        optimal_discount=result["optimal_discount"],
        safe_min=result["safe_min"],
        safe_max=result["safe_max"],
        loss_probability=result["loss_probability"],
        expected_margin=result["expected_margin"],
    )

    # ── SHAP Explanation ──
    if explanation:
        _render_shap_chart(explanation["shap_values"])


def _get_subcategories(category: str) -> list:
    """Return subcategories for a given category."""
    subcats = {
        "Technology": ["Phones", "Machines", "Copiers", "Accessories", "Cameras"],
        "Furniture": ["Chairs", "Tables", "Bookcases", "Furnishings"],
        "Office Supplies": ["Binders", "Paper", "Storage", "Art", "Supplies", "Appliances", "Labels", "Envelopes"],
    }
    return subcats.get(category, ["Other"])


def _render_result_card(
    optimal_discount: float,
    safe_min: float,
    safe_max: float,
    loss_probability: float,
    expected_margin: float,
):
    """Render the flip-board style result card."""
    st.markdown("""
        <div style="font-family:'IBM Plex Mono',monospace;font-size:0.7rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.08em;margin:1rem 0 0.5rem;">
          RECOMMENDATION
        </div>
    """, unsafe_allow_html=True)

    st.markdown('<div class="crosshair-panel glass" style="padding:1rem;">', unsafe_allow_html=True)
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Optimal Discount", f"{optimal_discount:.0f}%",
                  help="The discount that maximizes profit while minimizing loss risk")
    with col2:
        st.metric("Safe Range", f"{safe_min:.0f}% – {safe_max:.0f}%",
                  help="Acceptable discount range within risk tolerance")
    with col3:
        prob_color = RUST if loss_probability > 0.3 else (TURMERIC if loss_probability > 0.15 else SAGE)
        st.metric("Loss Probability", f"{loss_probability:.0%}",
                  help="Probability of this promotion resulting in a loss")
    with col4:
        st.metric("Expected Margin", f"{expected_margin:.0%}",
                  help="Expected profit margin after discount")
    st.markdown('</div>', unsafe_allow_html=True)

    # Status indicator
    if loss_probability < 0.15:
        status = "✅ Approve"
        status_color = SAGE
    elif loss_probability < 0.30:
        status = "⚠️ Review"
        status_color = TURMERIC
    else:
        status = "❌ Reject"
        status_color = RUST

    st.markdown(f"""
        <div class="glass" style="padding:1rem;margin:1rem 0;text-align:center;border-color:{status_color};">
            <span style="color:{status_color};font-size:1.1rem;font-weight:600;">
                {status}
            </span>
        </div>
    """, unsafe_allow_html=True)


def _render_shap_chart(shap_values: dict):
    """Render a horizontal bar chart of SHAP contribution values."""
    st.markdown("""
        <div style="margin-top:1.5rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.5rem;">
            03 / Model Explanation
          </div>
          <h2 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:400;font-size:1.2rem;color:#ffffff;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:1rem;">
            Model Explanation
          </h2>
        </div>
    """, unsafe_allow_html=True)

    st.markdown("""
        <p style="color:#bababa;font-size:0.8rem;margin-bottom:1rem;">
            SHAP values show how each feature contributed to the prediction.
            Positive values increase the recommended discount; negative values decrease it.
        </p>
    """, unsafe_allow_html=True)

    sorted_items = sorted(shap_values.items(), key=lambda x: abs(x[1]), reverse=True)
    features = [item[0] for item in sorted_items]
    values = [item[1] for item in sorted_items]
    colors = [SAGE if v > 0 else RUST for v in values]

    fig = make_themed_figure()
    fig.add_trace(go.Bar(
        y=features,
        x=values,
        orientation="h",
        marker_color=colors,
        text=[f"{v:+.2f}" for v in values],
        textposition="outside",
        textfont=dict(color=MUTED, size=11),
    ))

    fig.update_layout(
        xaxis_title="SHAP Value",
        margin=dict(l=0, r=40, t=10, b=0),
        height=200 + len(features) * 25,
        showlegend=False,
        xaxis=dict(zeroline=True, zerolinecolor=MUTED, zerolinewidth=1),
    )

    st.markdown('<div class="crosshair-panel glass" style="padding:1rem;">', unsafe_allow_html=True)
    st.plotly_chart(fig, use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)
