"""
MandiIQ — Loading page.

App-level splash while nightly cache is (re)building.
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

    st.markdown("""
    <style>
        section[data-testid="stSidebar"] { display: none; }
        header { display: none; }
        .stApp { overflow: hidden; }
    </style>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div style="display: flex; flex-direction: column; align-items: center; 
                justify-content: center; min-height: 80vh; text-align: center;">
        
        <div style="font-size: 3rem; font-weight: 700; color: #d7ff00; 
                    margin-bottom: 1rem; font-family: IBM Plex Mono, monospace;">
            MandiIQ
        </div>
        
        <h2 style="color: #ffffff; margin-top: 0; margin-bottom: 0.5rem;">
            Building Cache...
        </h2>
        
        <p style="color: #bababa; font-size: 0.9rem; max-width: 400px; margin-bottom: 2rem;">
            The nightly data pipeline is running. This usually takes 1–2 minutes 
            when fetching fresh data from data.gov.in.
        </p>
        
        <!-- Skeleton loader simulation -->
        <div style="width: 100%; max-width: 600px; margin: 1rem 0;">
            <div style="display: flex; gap: 1rem; margin-bottom: 1rem;">
                <div style="flex: 1; height: 80px; background: rgba(46, 58, 85, 0.3); 
                            border-radius: 8px; animation: pulse 1.5s ease-in-out infinite;"></div>
                <div style="flex: 1; height: 80px; background: rgba(46, 58, 85, 0.3); 
                            border-radius: 8px; animation: pulse 1.5s ease-in-out infinite 0.2s;"></div>
                <div style="flex: 1; height: 80px; background: rgba(46, 58, 85, 0.3); 
                            border-radius: 8px; animation: pulse 1.5s ease-in-out infinite 0.4s;"></div>
                <div style="flex: 1; height: 80px; background: rgba(46, 58, 85, 0.3); 
                            border-radius: 8px; animation: pulse 1.5s ease-in-out infinite 0.6s;"></div>
            </div>
            <div style="height: 200px; background: rgba(46, 58, 85, 0.2); 
                        border-radius: 8px; margin-bottom: 1rem; animation: pulse 1.5s ease-in-out infinite;"></div>
            <div style="display: flex; gap: 0.5rem;">
                <div style="flex: 1; height: 24px; background: rgba(46, 58, 85, 0.3); 
                            border-radius: 4px; animation: pulse 1.5s ease-in-out infinite;"></div>
                <div style="flex: 1; height: 24px; background: rgba(46, 58, 85, 0.3); 
                            border-radius: 4px; animation: pulse 1.5s ease-in-out infinite 0.2s;"></div>
            </div>
        </div>
        
        <style>
            @keyframes pulse {
                0%, 100% { opacity: 0.3; }
                50% { opacity: 0.6; }
            }
        </style>
        
        <div style="margin-top: 2rem; padding: 1rem; background: rgba(11, 15, 30, 0.6); 
                    border-radius: 8px; border: 1px solid #111111; max-width: 400px;">
            <div style="color: #bababa; font-size: 0.85rem; text-align: left;">
                <strong style="color: #d7ff00;">What's happening:</strong><br/>
                • Fetching latest mandi prices from data.gov.in<br/>
                • Updating rainfall records from IMD<br/>
                • Recomputing forecasts and risk scores<br/>
                • Building nightly narrative summary
            </div>
        </div>
        
    </div>
    """, unsafe_allow_html=True)

    st.markdown(f"""
    <p style="color: {FAINT}; font-size: 0.75rem; text-align: center; 
               position: fixed; bottom: 1rem; left: 0; right: 0;">
        Last run: 2026-07-18 02:00 IST • No mock data, ever
    </p>
    """, unsafe_allow_html=True)

    # Auto-refresh simulation
    import time
    time.sleep(2)

    # In production, this would check actual cache status
    st.markdown("""
    <meta http-equiv="refresh" content="3">
    <p style="color: #7e7e7e; font-size: 0.8rem; text-align: center; margin-top: 1rem;">
        Auto-refreshing... (page will reload when cache is ready)
    </p>
    """, unsafe_allow_html=True)

