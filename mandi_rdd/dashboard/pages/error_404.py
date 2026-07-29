"""
MandiIQ — 404 Not Found page.

On-brand 404 page that doesn't look like a framework default.
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

    # Custom 404 styling
    st.markdown("""
    <style>
        section[data-testid="stSidebar"] { display: none; }
        header { display: none; }
    </style>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div style="display: flex; flex-direction: column; align-items: center; 
                justify-content: center; min-height: 70vh; text-align: center;">
        
        <div style="font-size: 8rem; font-weight: 700; color: #d7ff00; 
                    line-height: 1; margin-bottom: 1rem; font-family: IBM Plex Mono, monospace;">
            404
        </div>
        
        <h1 style="color: #ffffff; margin-top: 0; margin-bottom: 0.5rem;">
            Page Not Found
        </h1>
        
        <p style="color: #bababa; font-size: 1rem; max-width: 400px; margin-bottom: 2rem;">
            This page doesn't exist — but the district data does. 
            The page you're looking for may have been moved or the URL is incorrect.
        </p>
        
        <div style="display: flex; gap: 1rem; flex-wrap: wrap; justify-content: center;">
            <a href="/" style="display: inline-block; background: #d7ff00; color: #000000; 
                              padding: 0.75rem 1.5rem; border-radius: 6px; text-decoration: none; 
                              font-weight: 600;">
                ← Back to Overview
            </a>
            <a href="/risk_map" style="display: inline-block; border: 1px solid #d7ff00; 
                                      color: #d7ff00; padding: 0.75rem 1.5rem; border-radius: 6px; 
                                      text-decoration: none;">
                View Districts →
            </a>
            <a href="/about" style="display: inline-block; border: 1px solid #111111; 
                                   color: #bababa; padding: 0.75rem 1.5rem; border-radius: 6px; 
                                   text-decoration: none;">
                About MandiIQ
            </a>
        </div>
        
        <div style="margin-top: 3rem; padding: 1rem; background: rgba(11, 15, 30, 0.4); 
                    border-radius: 8px; border: 1px solid #111111;">
            <p style="color: #7e7e7e; font-size: 0.8rem; margin: 0;">
                <strong>Looking for something specific?</strong><br/>
                Try the pages above, or use the navigation menu on the left.
            </p>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown(f"""
    <p style="color: {FAINT}; font-size: 0.75rem; text-align: center; 
               position: fixed; bottom: 1rem; left: 0; right: 0;">
        MandiIQ Dashboard • Real data, no mock fallbacks
    </p>
    """, unsafe_allow_html=True)

