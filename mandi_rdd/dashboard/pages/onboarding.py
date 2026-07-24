"""
MandiIQ — Onboarding page.

First-run walkthrough: 3 steps, skippable, session-only.
Explains what the app does, how to use Ask MandiIQ, and how to follow districts.

Alche Studio Design: crosshair-panel glass cards, interpretation boxes,
consistent monochrome-lime palette.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import streamlit as st
from mandi_rdd.dashboard.theme import (
    inject_theme, TURMERIC, RUST, SAGE, SLATE, MUTED, FAINT, INK
)

def render():
    inject_theme()

    # Initialize onboarding state
    if "onboarding_step" not in st.session_state:
        st.session_state.onboarding_step = 0

    if "onboarding_complete" not in st.session_state:
        st.session_state.onboarding_complete = False

    # If onboarding is complete, redirect
    if st.session_state.onboarding_complete:
        st.markdown("""
        <script>
            window.location.href = '/';
        </script>
        <meta http-equiv="refresh" content="0; url=/">
        <p style="color: #bababa;">Redirecting to dashboard...</p>
        """, unsafe_allow_html=True)
        return

    step = st.session_state.onboarding_step

    st.markdown("""
    <style>
        section[data-testid="stSidebar"] { display: none; }
    </style>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div style="text-align: center; margin-bottom: 2rem;">
        <div style="font-size: 2.5rem; font-weight: 700; color: #d7ff00; margin-bottom: 0.5rem;">
            MandiIQ
        </div>
        <div style="color: #bababa; font-size: 0.9rem;">
            Agricultural Market Intelligence
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Progress dots
    steps = 3
    dots_html = '<div style="display: flex; justify-content: center; gap: 0.5rem; margin-bottom: 2rem;">'
    for i in range(steps):
        dot_color = TURMERIC if i <= step else SLATE
        dots_html += f'<span style="width: 10px; height: 10px; border-radius: 50%; background: {dot_color};"></span>'
    dots_html += '</div>'
    st.markdown(dots_html, unsafe_allow_html=True)

    # Step content
    if step == 0:
        render_step_1()
    elif step == 1:
        render_step_2()
    elif step == 2:
        render_step_3()

    st.markdown("---")

    # Navigation buttons
    col1, col2, col3 = st.columns([2, 1, 2])

    with col1:
        if st.button("← Previous", disabled=(step == 0), use_container_width=True):
            st.session_state.onboarding_step = max(0, step - 1)
            st.rerun()

    with col2:
        if st.button("Skip", use_container_width=True):
            st.session_state.onboarding_complete = True
            st.rerun()

    with col3:
        button_text = "Next →" if step < 2 else "Get Started →"
        if st.button(button_text, type="primary", use_container_width=True):
            if step < 2:
                st.session_state.onboarding_step = step + 1
                st.rerun()
            else:
                st.session_state.onboarding_complete = True
                st.rerun()


def render_step_1():
    """Step 1: What this app does."""
    st.markdown("""
    <div style="text-align: center; margin: 1rem 0;">
        <h2 style="color: #ffffff; margin-bottom: 1rem;">
            What MandiIQ Does
        </h2>
        <p style="color: #bababa; font-size: 1rem; line-height: 1.6; max-width: 500px; margin: 0 auto;">
            MandiIQ detects <strong style="color: #d7ff00;">causal price effects</strong> 
            in Indian agricultural markets using real mandi data — no correlations, 
            no guesswork, no mock numbers.
        </p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div class="crosshair-panel glass" style="padding:1.5rem;margin:2rem auto;max-width:500px;text-align:center;">
        <div style="color: #bababa; font-size: 0.85rem; margin-bottom: 0.5rem;">
            Live Example (if data is available):
        </div>
        <div style="color: #d7ff00; font-size: 1.2rem; font-family: IBM Plex Mono, monospace;">
            Nashik onions: +₹340/quintal at −19% rainfall cutoff
        </div>
        <div style="color: #7e7e7e; font-size: 0.75rem; margin-top: 0.5rem;">
            Regression Discontinuity Design • p &lt; 0.05
        </div>
    </div>
    """, unsafe_allow_html=True)


def render_step_2():
    """Step 2: How to use Ask MandiIQ."""
    st.markdown("""
    <div style="text-align: center; margin: 1rem 0;">
        <h2 style="color: #ffffff; margin-bottom: 1rem;">
            Ask MandiIQ
        </h2>
        <p style="color: #bababa; font-size: 1rem; line-height: 1.6; max-width: 500px; margin: 0 auto;">
            Ask questions in plain English. Get answers grounded in live data — 
            not speculation, not AI hallucinations.
        </p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div class="crosshair-panel glass" style="padding:1.5rem;margin:2rem auto;max-width:500px;">
        <div style="color: #bababa; font-size: 0.85rem; margin-bottom: 1rem;">
            Example questions:
        </div>
        <ul style="color: #ffffff; font-size: 0.9rem; margin: 0; padding-left: 1.5rem;">
            <li style="margin-bottom: 0.5rem;">"Should I lock in onion procurement in Nashik next month?"</li>
            <li style="margin-bottom: 0.5rem;">"What's the price trend for tomatoes in Pune?"</li>
            <li style="margin-bottom: 0.5rem;">"Which districts are high risk for wheat?"</li>
        </ul>
    </div>
    """, unsafe_allow_html=True)


def render_step_3():
    """Step 3: How to follow districts."""
    st.markdown("""
    <div style="text-align: center; margin: 1rem 0;">
        <h2 style="color: #ffffff; margin-bottom: 1rem;">
            Track Your Districts
        </h2>
        <p style="color: #bababa; font-size: 1rem; line-height: 1.6; max-width: 500px; margin: 0 auto;">
            Follow districts you care about. Get notified when prices cross thresholds 
            or rainfall deficiency spikes.
        </p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div class="crosshair-panel glass" style="padding:1.5rem;margin:2rem auto;max-width:500px;">
        <div style="display: flex; gap: 1rem; margin-bottom: 1rem;">
            <div class="crosshair-panel" style="flex:1;padding:1rem;text-align:center;">
                <div style="font-size: 1.5rem;">📍</div>
                <div style="color: #ffffff; font-size: 0.85rem; margin-top: 0.5rem;">
                    Select districts from the Risk Map
                </div>
            </div>
            <div class="crosshair-panel" style="flex:1;padding:1rem;text-align:center;">
                <div style="font-size: 1.5rem;">🔔</div>
                <div style="color: #ffffff; font-size: 0.85rem; margin-top: 0.5rem;">
                    Get notified on threshold breaches
                </div>
            </div>
        </div>
        <div style="color: #7e7e7e; font-size: 0.8rem; text-align: center;">
            You can unfollow districts anytime from the sidebar.
        </div>
    </div>
    """, unsafe_allow_html=True)
