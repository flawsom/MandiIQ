import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

"""
mandi_rdd/dashboard/pages/components.py — Dev-only Component Visual QA

This page renders every component in every state for visual review.
Only accessible in dev mode (session flag). Removed from navigation in production.

This is a design-system sanity check — not a functional page.
"""

import streamlit as st
import time
from mandi_rdd.dashboard.components import (
    metric_card, badge, status_dot, skeleton,
    apply_chart_theme, confirm_dialog, inject_all_component_css,
)
from mandi_rdd.dashboard.theme import TURMERIC, RUST, SAGE, SLATE, MUTED

# --- Live data helpers (for real dropdown options) ---
@st.cache_data(ttl=3600)
def _get_district_opts():
    from mandi_rdd.storage.duckdb_store import get_distinct_options
    return [""] + get_distinct_options("district", limit=30)

@st.cache_data(ttl=3600)
def _get_commodity_opts():
    from mandi_rdd.storage.duckdb_store import get_curated_commodities
    curated = get_curated_commodities(limit=12)
    return [""] + curated

@st.cache_data(ttl=3600)
def _get_state_opts():
    from mandi_rdd.storage.duckdb_store import get_distinct_options
    return [""] + get_distinct_options("state", limit=20)

@st.cache_data(ttl=3600)
def _get_market_opts():
    from mandi_rdd.storage.duckdb_store import get_distinct_options
    return [""] + get_distinct_options("market", limit=30)

@st.cache_data(ttl=3600)
def _get_grade_opts():
    from mandi_rdd.storage.duckdb_store import get_distinct_options
    return [""] + get_distinct_options("grade", limit=15)


def render(RUST="#D9663B", TURMERIC="#d7ff00", INK="#000000", MUTED="#bababa", PAPER="#ffffff"):
    st.title("Component Library — Visual QA")

    st.caption("Every component in every defined state. Use this to verify design consistency.")

    inject_all_component_css()

    # ── 1. Buttons ──
    st.markdown("## 1. Buttons")
    st.caption("Variants: Primary, Secondary, Ghost, Danger")
    st.markdown("States: Default, Hover, Active, Disabled, Loading")

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.markdown("**Default**")
        st.markdown(
            f'<button class="mandiq-btn mandiq-btn-primary">Primary</button><br><br>'
            f'<button class="mandiq-btn mandiq-btn-secondary">Secondary</button><br><br>'
            f'<button class="mandiq-btn mandiq-btn-ghost">Ghost</button><br><br>'
            f'<button class="mandiq-btn mandiq-btn-danger">Danger</button>',
            unsafe_allow_html=True
        )
    with col2:
        st.markdown("**Loading**")
        st.caption("Interactive \u2014 click to show the loading state for ~1.2s, then it auto-resets (never stuck).")

        def _start(key):
            st.session_state[key] = True

        _lp = st.session_state.get("load_primary_run", False)
        _ld = st.session_state.get("load_danger_run", False)

        if _lp:
            st.markdown('<button class="mandiq-btn mandiq-btn-primary loading">Loading</button>', unsafe_allow_html=True)
            time.sleep(1.2)
            st.session_state.load_primary_run = False
            st.rerun()
        else:
            st.button("Run Primary", key="load_primary", on_click=_start, args=("load_primary_run",))
            st.markdown('<button class="mandiq-btn mandiq-btn-primary">Primary</button>', unsafe_allow_html=True)

        if _ld:
            st.markdown('<button class="mandiq-btn mandiq-btn-danger loading">Remove</button>', unsafe_allow_html=True)
            time.sleep(1.2)
            st.session_state.load_danger_run = False
            st.rerun()
        else:
            st.button("Run Danger", key="load_danger", on_click=_start, args=("load_danger_run",))
            st.markdown('<button class="mandiq-btn mandiq-btn-danger">Remove</button>', unsafe_allow_html=True)

        st.markdown(
            f'<button class="mandiq-btn mandiq-btn-secondary" disabled>Loading (disabled preview)</button><br><br>'
            f'<span style="color:{MUTED};font-size:0.8rem;">Ghost \u2014 N/A</span>',
            unsafe_allow_html=True,
        )
    with col3:
        st.markdown("**Disabled**")
        st.markdown(
            f'<button class="mandiq-btn mandiq-btn-primary" disabled>Primary</button><br><br>'
            f'<button class="mandiq-btn mandiq-btn-secondary" disabled>Secondary</button><br><br>'
            f'<button class="mandiq-btn mandiq-btn-ghost" disabled>Ghost</button><br><br>'
            f'<button class="mandiq-btn mandiq-btn-danger" disabled>Danger</button>',
            unsafe_allow_html=True
        )
    with col4:
        st.markdown("**Hover (simulated)**")
        st.markdown(
            f'<span style="color:{MUTED};font-size:0.8rem;">'
            f'Hover states are interactive — mouse over the "Default" column buttons.</span>',
            unsafe_allow_html=True
        )
    with col5:
        st.markdown("**Sizes**")
        sizes = {"Small": "0.75rem", "Default": "0.85rem", "Large": "1rem"}
        for label, size in sizes.items():
            st.markdown(
                f'<button class="mandiq-btn mandiq-btn-primary" '
                f'style="font-size:{size};">{label}</button><br><br>',
                unsafe_allow_html=True
            )

    # ── 2. Inputs / Select Fields ──
    st.markdown("## 2. Inputs / Select Fields")
    st.caption("Real dropdowns (no blank boxes) with field-appropriate options. Invalid/Disabled states are shown as style previews.")
    st.markdown(
        f'<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:1rem;">'
        f'<div><label style="font-size:0.75rem;color:{MUTED};">Empty (preview)</label>'
        f'<input class="mandiq-input" placeholder="e.g. Nashik"></div>'
        f'<div><label style="font-size:0.75rem;color:{MUTED};">Focused (preview)</label>'
        f'<input class="mandiq-input" placeholder="e.g. Nashik" value="Nashik"></div>'
        f'<div><label style="font-size:0.75rem;color:{MUTED};">Invalid (preview)</label>'
        f'<input class="mandiq-input invalid" placeholder="District name" value="Invalid!">'
        f'<div class="mandiq-error-msg">Select a valid option below to clear</div></div>'
        f'<div><label style="font-size:0.75rem;color:{MUTED};">Disabled</label>'
        f'<input class="mandiq-input" disabled placeholder="Unavailable" value=""></div>'
        f'<div><label style="font-size:0.75rem;color:{MUTED};">Filled (read)</label>'
        f'<input class="mandiq-input" value="Nashik" readonly></div>'
        f'</div>',
        unsafe_allow_html=True
    )

    st.markdown("### Live dropdowns (pull from dataset)")
    st.caption("Options are queried from the live database. "
               "Empty field = no data; select a non-empty option to clear the validation below.")

    with st.spinner("Loading field options from dataset..."):
        district_opts = _get_district_opts()
        commodity_opts = _get_commodity_opts()
        state_opts = _get_state_opts()
        market_opts = _get_market_opts()
        grade_opts = _get_grade_opts()
        unit_opts = ["", "Quintal (100 kg)", "Kg", "Tonne", "Metric Ton"]

    st.caption(f"{len(district_opts)-1} districts, {len(commodity_opts)-1} commodities, "
               f"{len(state_opts)-1} states, {len(market_opts)-1} markets, {len(grade_opts)-1} grades available.")

    col_a, col_b = st.columns(2)
    with col_a:
        district = st.selectbox("District", district_opts, key="qa_district")
        commodity = st.selectbox("Commodity", commodity_opts, key="qa_commodity")
        state = st.selectbox("State", state_opts, key="qa_state")
    with col_b:
        market = st.selectbox("Market (APMC)", market_opts, key="qa_market")
        grade = st.selectbox("Grade", grade_opts, key="qa_grade")
        unit = st.selectbox("Unit", unit_opts, key="qa_unit")

    selections = {
        "District": district, "Commodity": commodity, "State": state,
        "Market": market, "Grade": grade, "Unit": unit,
    }
    missing = [name for name, val in selections.items() if not val]
    if missing:
        st.markdown(
            f'<div class="mandiq-error-msg">Please select: {", ".join(missing)}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.success(f"All fields selected - {district}, {commodity}, {market} ready to query MandiIQ.")

    # ── 3. Cards ──
    st.markdown("## 3. Cards")
    st.caption("Variants: Metric KPI, Info, Error")
    col1, col2, col3 = st.columns(3)
    with col1:
        metric_card("\u20b91,842", "Avg. Price Onion", "\u2191 +12.3%")
    with col2:
        st.markdown(
            f'<div class="mandiq-card info" style="padding:1.25rem;">'
            f'<div style="font-size:0.85rem;color:{TURMERIC};">\u2139\ufe0f Info</div>'
            f'<div style="font-size:0.8rem;color:{MUTED};margin-top:0.5rem;">'
            f'Nightly pipeline completed at 02:14. All 12 districts updated.</div></div>',
            unsafe_allow_html=True
        )
    with col3:
        st.markdown(
            f'<div class="mandiq-card error" style="padding:1.25rem;">'
            f'<div style="font-size:0.85rem;color:{RUST};">\u26a0 Error</div>'
            f'<div style="font-size:0.8rem;color:{MUTED};margin-top:0.5rem;">'
            f'Data source unavailable — using last known values</div></div>',
            unsafe_allow_html=True
        )

    # ── 4. Tables ──
    st.markdown("## 4. Table Styling")
    st.caption("Applied to st.dataframe via CSS classes")
    st.markdown(
        f'<table class="mandiq-table"><thead><tr>'
        f'<th>District</th><th>Commodity</th><th class="num">Price</th><th class="num">\u0394</th><th>Tier</th>'
        f'</tr></thead><tbody>'
        f'<tr><td>Nashik</td><td>Onion</td><td class="num">\u20b91,842</td><td class="num" style="color:{RUST};">-3.2%</td>'
        f'<td><span style="color:{RUST};font-weight:500;">High</span></td></tr>'
        f'<tr><td>Pune</td><td>Tomato</td><td class="num">\u20b92,150</td><td class="num" style="color:{SAGE};">+5.1%</td>'
        f'<td><span style="color:{TURMERIC};font-weight:500;">Moderate</span></td></tr>'
        f'<tr><td>Ahmednagar</td><td>Wheat</td><td class="num">\u20b91,975</td><td class="num" style="color:{MUTED};">0.0%</td>'
        f'<td><span style="color:{MUTED};">Low</span></td></tr>'
        f'</tbody></table>',
        unsafe_allow_html=True
    )

    # ── 5. Charts (theme placeholder) ──
    st.markdown("## 5. Chart Theme")
    st.caption("Plotly theme applied via `apply_chart_theme()` — see plotly_theme.py")
    st.markdown(
        f'<div class="mandiq-card" style="padding:2rem;text-align:center;color:{MUTED};font-size:0.85rem;">'
        f'Chart theme is applied dynamically to Plotly figures. '
        f'<br>Check <code>plotly_theme.py</code> or any page with a chart for live example.</div>',
        unsafe_allow_html=True
    )

    # ── 6. Modals ──
    st.markdown("## 6. Modal / Dialog")
    st.caption("Confirmation dialog with scrim and focus trapping")
    modal_result = confirm_dialog(
        "Clear all followed districts?",
        "This will remove all followed district alerts and threshold notifications. "
        "You can follow districts again at any time.",
        confirm_label="Clear All",
        cancel_label="Cancel",
        key="qa_modal"
    )
    if modal_result:
        st.success("Confirmed! (modal result = True)")
    elif modal_result is False:
        st.info("Cancelled (modal result = False)")

    # ── 7. Toasts ──
    st.markdown("## 7. Toast / Notification")
    st.caption("Position: bottom-right, stack upward, auto-dismiss")
    if st.button("Show Info Toast", key="toast_info"):
        from mandi_rdd.dashboard.components import toast
        toast("Nightly refresh completed", type="info", duration=4)
    if st.button("Show Success Toast", key="toast_success"):
        from mandi_rdd.dashboard.components import toast
        toast("Followed Nashik successfully", type="success", duration=4)
    if st.button("Show Error Toast", key="toast_error"):
        from mandi_rdd.dashboard.components import toast
        toast("Data source unavailable", type="error", duration=4)

    # ── 8. Badges ──
    st.markdown("## 8. Badges / Status Dots")
    st.caption("Variants: Count badge, Status dot, Tier badge")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("**Count Badges**")
        badge("3", TURMERIC)
        badge("12", SAGE)
        badge("7", RUST)
    with col2:
        st.markdown("**Status Dots**")
        status_dot("green")
        st.caption("Green — healthy")
        status_dot("amber")
        st.caption("Amber — degraded")
        status_dot("red")
        st.caption("Red — unavailable")
    with col3:
        st.markdown("**Tier Badges**")
        badge("High Risk", RUST, "medium")
        badge("Moderate", TURMERIC, "medium")
        badge("Low Risk", SAGE, "medium")

    # ── 9. Navigation Elements ──
    st.markdown("## 9. Navigation Elements")
    st.caption("Breadcrumb, Tabs, Active Nav Indicator")
    st.markdown(
        f'<div class="mandiq-breadcrumb">'
        f'<a href="/">Overview</a> <span style="color:{MUTED};">/</span> '
        f'<a href="/risk-map">Risk Map</a> <span style="color:{MUTED};">/</span> '
        f'<span class="current">Nashik</span></div>',
        unsafe_allow_html=True
    )
    st.markdown(
        f'<div class="mandiq-tabs">'
        f'<div class="mandiq-tab active">Prophet</div>'
        f'<div class="mandiq-tab">LSTM</div>'
        f'<div class="mandiq-tab">Ensemble</div>'
        f'</div>',
        unsafe_allow_html=True
    )

    # ── 10. Skeleton Loaders ──
    st.markdown("## 10. Skeleton Loaders")
    st.caption("Shimmer effect, shaped-like-content. Respekts prefers-reduced-motion.")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown("**Text**")
        skeleton("text")
        skeleton("text")
        skeleton("text",)
    with col2:
        st.markdown("**KPI**")
        skeleton("kpi")
    with col3:
        st.markdown("**Chart**")
        skeleton("chart")
    with col4:
        st.markdown("**Avatar**")
        skeleton("avatar")

