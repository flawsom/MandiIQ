"""
MandiIQ — Error: No Data page.

Shown when a district/commodity combination has no matching records.
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
    st.markdown("<h1>📊 No Data Available</h1>", unsafe_allow_html=True)

    st.markdown("""
    <div style="background: rgba(232, 177, 77, 0.1); border: 1px solid rgba(232, 177, 77, 0.3); 
                border-radius: 10px; padding: 1.5rem; margin: 1.5rem 0;">
        <p style="color: #ffffff; margin: 0;">
            The selected district and commodity combination has no matching records in our database.
            This could mean the commodity isn't traded in that district, or reporting is sparse.
        </p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("### What You Can Do")

    st.markdown("""
    <div style="display: grid; gap: 1rem; margin: 1rem 0;">
        <div style="background: rgba(11, 15, 30, 0.6); border: 1px solid #111111; 
                    border-radius: 8px; padding: 1rem;">
            <strong style="color: #d7ff00;">1. Try a Different Commodity</strong><br/>
            <span style="color: #bababa; font-size: 0.85rem;">
                Major commodities (Onion, Tomato, Wheat, Potato) have better coverage across districts.
            </span>
        </div>
        <div style="background: rgba(11, 15, 30, 0.6); border: 1px solid #111111; 
                    border-radius: 8px; padding: 1rem;">
            <strong style="color: #d7ff00;">2. Try a Different District</strong><br/>
            <span style="color: #bababa; font-size: 0.85rem;">
                Nashik, Pune, and Ahmednagar have the most complete reporting for most commodities.
            </span>
        </div>
        <div style="background: rgba(11, 15, 30, 0.6); border: 1px solid #111111; 
                    border-radius: 8px; padding: 1rem;">
            <strong style="color: #d7ff00;">3. Check Data Coverage</strong><br/>
            <span style="color: #bababa; font-size: 0.85rem;">
                Visit <a href="/settings" style="color: #d7ff00;">Settings</a> to see database 
                statistics and table row counts.
            </span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("---")

    st.markdown("### Why This Happens")

    st.markdown("""
    <div style="color: #bababa; font-size: 0.9rem; line-height: 1.6;">
        <p>Mandi price reporting is voluntary and varies by region. Some districts report 
        regularly for certain commodities but not others. We show real data only — 
        no interpolation or estimation.</p>
        <p>If you expected data for this combination, it may indicate:</p>
        <ul style="margin-left: 1rem;">
            <li>The commodity isn't primarily traded in this district</li>
            <li>Reporting gaps in the source data (data.gov.in)</li>
            <li>The district name may have changed or been reorganized</li>
        </ul>
    </div>
    """, unsafe_allow_html=True)

    st.markdown(f"""
    <div style="text-align: center; margin: 2rem 0;">
        <a href="/" style="display: inline-block; background: #d7ff00; color: #000000; 
                          padding: 0.75rem 1.5rem; border-radius: 6px; text-decoration: none; 
                          font-weight: 600; margin-right: 1rem;">
            ← Back to Overview
        </a>
        <a href="/risk_map" style="display: inline-block; border: 1px solid #d7ff00; 
                                  color: #d7ff00; padding: 0.75rem 1.5rem; border-radius: 6px; 
                                  text-decoration: none;">
            View All Districts →
        </a>
    </div>
    """, unsafe_allow_html=True)

    st.markdown(f"""
    <p style="color: {FAINT}; font-size: 0.75rem; text-align: center; margin-top: 2rem;">
        Data source: data.gov.in Agmarknet • No synthetic fallbacks ever
    </p>
    """, unsafe_allow_html=True)
