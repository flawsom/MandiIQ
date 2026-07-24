"""
MandiIQ — Error: Model Unavailable page.

Shown when all orchestrator models are exhausted.
Degraded view with explanation and last-known data.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import streamlit as st
from mandi_rdd.dashboard.theme import (
    inject_theme, TURMERIC, RUST, SAGE, SLATE, MUTED, FAINT
)

def render():
    inject_theme()
    st.markdown("<h1>⚠️ AI Services Temporarily Unavailable</h1>", unsafe_allow_html=True)

    st.markdown("""
    <div style="background: rgba(217, 102, 59, 0.1); border: 1px solid rgba(217, 102, 59, 0.3); 
                border-radius: 10px; padding: 1.5rem; margin: 1.5rem 0;">
        <p style="color: #ffffff; margin: 0;">
            The AI model chain is currently busy. This affects <strong>Ask MandiIQ</strong> 
            and automated summaries — but all numbers below are live and unaffected.
        </p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("### What's Working")

    st.markdown("""
    <div style="display: grid; gap: 1rem; margin: 1rem 0;">
        <div style="background: rgba(11, 15, 30, 0.6); border: 1px solid #111111; 
                    border-radius: 8px; padding: 1rem; display: flex; align-items: center; gap: 1rem;">
            <span style="color: #8FAE89; font-size: 1.2rem;">✓</span>
            <div>
                <strong style="color: #ffffff;">Live Price Data</strong><br/>
                <span style="color: #bababa; font-size: 0.85rem;">
                    Mandi prices from data.gov.in are updating normally.
                </span>
            </div>
        </div>
        <div style="background: rgba(11, 15, 30, 0.6); border: 1px solid #111111; 
                    border-radius: 8px; padding: 1rem; display: flex; align-items: center; gap: 1rem;">
            <span style="color: #8FAE89; font-size: 1.2rem;">✓</span>
            <div>
                <strong style="color: #ffffff;">Forecast Models</strong><br/>
                <span style="color: #bababa; font-size: 0.85rem;">
                    Prophet/LSTM predictions are available.
                </span>
            </div>
        </div>
        <div style="background: rgba(11, 15, 30, 0.6); border: 1px solid #111111; 
                    border-radius: 8px; padding: 1rem; display: flex; align-items: center; gap: 1rem;">
            <span style="color: #8FAE89; font-size: 1.2rem;">✓</span>
            <div>
                <strong style="color: #ffffff;">Risk Forecasts</strong><br/>
                <span style="color: #bababa; font-size: 0.85rem;">
                    Price probability and rainfall correlation charts work.
                </span>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("### What's Temporarily Down")

    st.markdown("""
    <div style="display: grid; gap: 1rem; margin: 1rem 0;">
        <div style="background: rgba(11, 15, 30, 0.6); border: 1px solid rgba(217, 102, 59, 0.5); 
                    border-radius: 8px; padding: 1rem; display: flex; align-items: center; gap: 1rem;">
            <span style="color: #D9663B; font-size: 1.2rem;">⚠</span>
            <div>
                <strong style="color: #ffffff;">Ask MandiIQ Chat</strong><br/>
                <span style="color: #bababa; font-size: 0.85rem;">
                    LLM providers are rate-limited. Try again in a few minutes.
                </span>
            </div>
        </div>
        <div style="background: rgba(11, 15, 30, 0.6); border: 1px solid rgba(217, 102, 59, 0.5); 
                    border-radius: 8px; padding: 1rem; display: flex; align-items: center; gap: 1rem;">
            <span style="color: #D9663B; font-size: 1.2rem;">⚠</span>
            <div>
                <strong style="color: #ffffff;">Automated Narratives</strong><br/>
                <span style="color: #bababa; font-size: 0.85rem;">
                    Nightly summary generation is queued. Charts still show current data.
                </span>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("---")

    st.markdown("""
    <div style="text-align: center; margin: 2rem 0;">
        <p style="color: #bababa; font-size: 0.9rem;">
            This usually resolves within 5–10 minutes. 
            The free-tier LLM quota resets hourly.
        </p>
        <a href="/" style="display: inline-block; background: #d7ff00; color: #000000; 
                          padding: 0.75rem 1.5rem; border-radius: 6px; text-decoration: none; 
                          font-weight: 600; margin-top: 1rem;">
            Return to Dashboard →
        </a>
    </div>
    """, unsafe_allow_html=True)

    st.markdown(f"""
    <p style="color: {FAINT}; font-size: 0.75rem; text-align: center; margin-top: 2rem;">
        Error code: MODEL_CHAIN_EXHAUSTED<br/>
        Timestamp: 2026-07-18 • Last pipeline run: see /settings for details
    </p>
    """, unsafe_allow_html=True)
