"""
mandi_rdd/dashboard/components.py — Reusable UI Component Library

Provides styled wrapper functions and design-system-aligned components
for the MandiIQ dashboard. Uses the existing turmeric/ink/slate token system.

All components are thin wrappers over Streamlit primitives or direct HTML
injection — they add design-system layer, not new functionality.

Component categories (10):
  1. Buttons  (primary, secondary, ghost, danger — all states)
  2. Inputs   (empty, focused, filled, invalid, disabled)
  3. Cards    (metric card, info card, error card)
  4. Tables   (styled dataframe wrapper)
  5. Charts   (Plotly theme enhancer)
  6. Modals   (blocking confirmation dialog)
  7. Toasts   (bottom-right notification stack)
  8. Badges   (status dot, count badge, tier badge)
  9. Nav      (breadcrumb, active nav item indicator)
  10. Loaders (skeleton shimmer, spinner)
"""

import streamlit as st
from mandi_rdd.dashboard.theme import INK, SLATE, PAPER, MUTED, FAINT, TURMERIC, RUST, SAGE

# ═══════════════════════════════════════════════════════════
# 1. BUTTONS
# ═══════════════════════════════════════════════════════════

_BUTTON_CSS = f"""
<style>
.mandiq-btn {{
    display: inline-flex; align-items: center; justify-content: center;
    gap: 0.4rem; border: none; border-radius: 6px;
    font-family: "IBM Plex Sans", system-ui, sans-serif;
    font-size: 0.85rem; font-weight: 500;
    padding: 0.45rem 1rem; cursor: pointer;
    transition: all 0.15s ease;
    line-height: 1.4; white-space: nowrap;
    text-decoration: none;
    position: relative;
}}
/* Primary — turmeric fill, ink text */
.mandiq-btn-primary {{
    background: {TURMERIC}; color: {INK};
    border: 1px solid {TURMERIC};
}}
.mandiq-btn-primary:hover {{
    background: #d4a444; border-color: #d4a444;
}}
.mandiq-btn-primary:active {{
    transform: scale(0.98);
    background: #c9963a; border-color: #c9963a;
}}
.mandiq-btn-primary:disabled {{
    opacity: 0.4; cursor: not-allowed; pointer-events: none;
}}
.mandiq-btn-primary.loading {{
    pointer-events: none;
}}
.mandiq-btn-primary.loading::after {{
    content: "..."; animation: mandiq-pulse 1.2s infinite;
}}

/* Secondary — outline, slate-2 border */
.mandiq-btn-secondary {{
    background: transparent; color: {PAPER};
    border: 1px solid rgba({int(SLATE[1:3],16)},{int(SLATE[3:5],16)},{int(SLATE[5:7],16)},0.6);
}}
.mandiq-btn-secondary:hover {{
    border-color: {TURMERIC}; color: {TURMERIC};
}}
.mandiq-btn-secondary:active {{
    background: rgba(232, 177, 77, 0.08);
}}
.mandiq-btn-secondary:disabled {{
    opacity: 0.4; cursor: not-allowed; pointer-events: none;
}}

/* Ghost — text only */
.mandiq-btn-ghost {{
    background: transparent; color: {MUTED};
    border: 1px solid transparent; padding: 0.45rem 0.75rem;
}}
.mandiq-btn-ghost:hover {{
    color: {TURMERIC}; text-decoration: underline;
}}
.mandiq-btn-ghost:active {{
    color: {PAPER};
}}
.mandiq-btn-ghost:disabled {{
    opacity: 0.4; cursor: not-allowed;
}}

/* Danger — rust border/text */
.mandiq-btn-danger {{
    background: transparent; color: {RUST};
    border: 1px solid {RUST};
}}
.mandiq-btn-danger:hover {{
    background: rgba(213, 102, 59, 0.12);
}}
.mandiq-btn-danger:active {{
    background: rgba(213, 102, 59, 0.20);
    transform: scale(0.98);
}}
.mandiq-btn-danger:disabled {{
    opacity: 0.4; cursor: not-allowed;
}}

@keyframes mandiq-pulse {{
    0%, 100% {{ opacity: 0.3; }}
    50% {{ opacity: 1; }}
}}
</style>
"""


def inject_button_css():
    """Inject button CSS into the page. Safe to call multiple times."""
    if "_mandiq_btn_css_injected" not in st.session_state:
        st.markdown(_BUTTON_CSS, unsafe_allow_html=True)
        st.session_state["_mandiq_btn_css_injected"] = True


def btn(label, key=None, variant="primary", disabled=False, loading=False, on_click=None, args=None):
    """Render a design-system button. Returns True when clicked."""
    inject_button_css()

    css_class = f"mandiq-btn mandiq-btn-{variant}"
    if loading:
        css_class += " loading"

    # Use st.button for functionality, apply styles via class
    btn_html = f'<button class="{css_class}" {"disabled" if disabled else ""}>'
    btn_html += f'<span class="pulse-dots" style="display:{'inline' if loading else 'none'};">...</span>' if loading else ""
    btn_html += label
    btn_html += "</button>"

    # We use st.button behind the scenes for session state tracking
    container = st.container()
    with container:
        clicked = st.button(label, key=key, disabled=disabled, on_click=on_click, args=args, type="secondary")
        # Hide the native button, show our styled one
        st.markdown(
            f'<style>div[data-testid="stButtonWrapper"]:has(button[key="{key or label}"]) '
            f'button {{ display: none; }}</style>',
            unsafe_allow_html=True,
        )
        st.markdown(btn_html, unsafe_allow_html=True)

    return clicked


# ═══════════════════════════════════════════════════════════
# 2. INPUTS
# ═══════════════════════════════════════════════════════════

_INPUT_CSS = f"""
<style>
.mandiq-input-wrapper {{
    position: relative; margin-bottom: 0.5rem;
}}
.mandiq-input-wrapper label {{
    display: block; font-size: 0.75rem; color: {MUTED};
    margin-bottom: 0.25rem; font-family: "IBM Plex Sans", system-ui, sans-serif;
}}
.mandiq-input {{
    width: 100%; padding: 0.5rem 0.75rem;
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba({int(SLATE[1:3],16)},{int(SLATE[3:5],16)},{int(SLATE[5:7],16)},0.4);
    border-radius: 6px; color: {PAPER};
    font-family: "IBM Plex Sans", system-ui, sans-serif; font-size: 0.85rem;
    transition: border-color 0.15s, box-shadow 0.15s;
    outline: none;
}}
.mandiq-input:focus {{
    border-color: {TURMERIC}; box-shadow: 0 0 0 2px rgba(232, 177, 77, 0.2);
}}
.mandiq-input::placeholder {{ color: {FAINT}; }}
.mandiq-input.invalid {{
    border-color: {RUST}; box-shadow: 0 0 0 1px {RUST};
}}
.mandiq-input:disabled {{
    background: rgba({int(SLATE[1:3],16)},{int(SLATE[3:5],16)},{int(SLATE[5:7],16)},0.1);
    color: {FAINT}; cursor: not-allowed;
}}
.mandiq-error-msg {{
    font-size: 0.75rem; color: {RUST}; margin-top: 0.2rem;
    font-family: "IBM Plex Sans", system-ui, sans-serif;
}}
</style>
"""

def inject_input_css():
    if "_mandiq_input_css" not in st.session_state:
        st.markdown(_INPUT_CSS, unsafe_allow_html=True)
        st.session_state["_mandiq_input_css"] = True


# ═══════════════════════════════════════════════════════════
# 3. CARDS
# ═══════════════════════════════════════════════════════════

_CARD_CSS = f"""
<style>
.mandiq-card {{
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba({int(SLATE[1:3],16)},{int(SLATE[3:5],16)},{int(SLATE[5:7],16)},0.2);
    border-radius: 10px; padding: 1.25rem;
    transition: border-color 0.2s;
}}
.mandiq-card:hover {{
    border-color: rgba({int(SLATE[1:3],16)},{int(SLATE[3:5],16)},{int(SLATE[5:7],16)},0.4);
}}
.mandiq-card.metric {{
    text-align: center; padding: 1rem;
}}
.mandiq-card.metric .value {{
    font-family: "IBM Plex Mono", monospace;
    font-size: 1.8rem; font-weight: 600;
    color: {TURMERIC}; line-height: 1.2;
}}
.mandiq-card.metric .label {{
    font-size: 0.75rem; color: {MUTED};
    margin-top: 0.25rem; text-transform: uppercase;
    letter-spacing: 0.05em;
}}
.mandiq-card.metric .delta {{
    font-family: "IBM Plex Mono", monospace;
    font-size: 0.75rem; margin-top: 0.15rem;
}}
.mandiq-card.error {{
    border-color: rgba(213, 102, 59, 0.3);
    background: rgba(213, 102, 59, 0.04);
}}
.mandiq-card.info {{
    border-color: rgba(232, 177, 77, 0.15);
    background: rgba(232, 177, 77, 0.04);
}}
</style>
"""

def inject_card_css():
    if "_mandiq_card_css" not in st.session_state:
        st.markdown(_CARD_CSS, unsafe_allow_html=True)
        st.session_state["_mandiq_card_css"] = True


def metric_card(value, label, delta=None, delta_color="normal"):
    """Render a metric KPI card matching the flip-board style."""
    inject_card_css()
    delta_class = "green" if delta_color == "normal" and delta and delta.startswith("+") else \
                  "red" if delta_color == "inverse" else "muted"
    delta_color_css = {"green": SAGE, "red": RUST, "muted": MUTED}.get(delta_class, MUTED)
    delta_html = f'<div class="delta" style="color:{delta_color_css};">{delta}</div>' if delta else ""
    st.markdown(
        f'<div class="mandiq-card metric">'
        f'<div class="label">{label}</div>'
        f'<div class="value">{value}</div>'
        f'{delta_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


# ═══════════════════════════════════════════════════════════
# 4. TABLES
# ═══════════════════════════════════════════════════════════

_TABLE_CSS = f"""
<style>
.mandiq-table {{
    width: 100%; border-collapse: collapse;
    font-family: "IBM Plex Sans", system-ui, sans-serif;
    font-size: 0.85rem;
}}
.mandiq-table th {{
    text-align: left; padding: 0.6rem 0.75rem;
    color: {MUTED}; font-weight: 500; font-size: 0.75rem;
    text-transform: uppercase; letter-spacing: 0.05em;
    border-bottom: 1px solid rgba({int(SLATE[1:3],16)},{int(SLATE[3:5],16)},{int(SLATE[5:7],16)},0.2);
}}
.mandiq-table td {{
    padding: 0.6rem 0.75rem;
    color: {PAPER}; border-bottom: 1px solid rgba({int(SLATE[1:3],16)},{int(SLATE[3:5],16)},{int(SLATE[5:7],16)},0.08);
}}
.mandiq-table tr:hover td {{
    background: rgba(255,255,255,0.02);
}}
.mandiq-table .mono {{ font-family: "IBM Plex Mono", monospace; }}
.mandiq-table .num {{ font-family: "IBM Plex Mono", monospace; text-align: right; }}
</style>
"""

def inject_table_css():
    if "_mandiq_table_css" not in st.session_state:
        st.markdown(_TABLE_CSS, unsafe_allow_html=True)
        st.session_state["_mandiq_table_css"] = True


# ═══════════════════════════════════════════════════════════
# 5. CHARTS (Plotly theme enhancer)
# ═══════════════════════════════════════════════════════════

def apply_chart_theme(fig):
    """Apply MandiIQ design tokens to a Plotly figure in-place."""
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color=PAPER,
        font_family="IBM Plex Sans, system-ui, sans-serif",
        xaxis=dict(
            gridcolor=f"rgba({int(SLATE[1:3],16)},{int(SLATE[3:5],16)},{int(SLATE[5:7],16)},0.15)",
            zerolinecolor=f"rgba({int(SLATE[1:3],16)},{int(SLATE[3:5],16)},{int(SLATE[5:7],16)},0.3)",
        ),
        yaxis=dict(
            gridcolor=f"rgba({int(SLATE[1:3],16)},{int(SLATE[3:5],16)},{int(SLATE[5:7],16)},0.15)",
            zerolinecolor=f"rgba({int(SLATE[1:3],16)},{int(SLATE[3:5],16)},{int(SLATE[5:7],16)},0.3)",
        ),
        hoverlabel=dict(
            bgcolor=INK,
            font_color=PAPER,
            font_family="IBM Plex Mono, monospace",
        ),
        legend=dict(
            font_color=MUTED,
        ),
        margin=dict(l=40, r=20, t=30, b=40),
    )
    return fig


# ═══════════════════════════════════════════════════════════
# 6. MODALS (blocking confirmation dialog)
# ═══════════════════════════════════════════════════════════

_MODAL_CSS = f"""
<style>
.mandiq-modal-scrim {{
    position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(11, 15, 30, 0.6);
    z-index: 9999; display: flex; align-items: center; justify-content: center;
    animation: mandiq-fade-in 0.15s ease;
}}
.mandiq-modal-panel {{
    background: {INK};
    border: 1px solid rgba({int(SLATE[1:3],16)},{int(SLATE[3:5],16)},{int(SLATE[5:7],16)},0.4);
    border-radius: 12px; padding: 1.5rem; max-width: 420px; width: 90%;
    animation: mandiq-rise 0.15s ease;
}}
.mandiq-modal-title {{
    font-size: 1.1rem; font-weight: 600; color: {PAPER}; margin-bottom: 0.5rem;
}}
.mandiq-modal-body {{
    font-size: 0.85rem; color: {MUTED}; margin-bottom: 1.25rem;
    line-height: 1.5;
}}
.mandiq-modal-actions {{
    display: flex; gap: 0.5rem; justify-content: flex-end;
}}

@keyframes mandiq-fade-in {{
    from {{ opacity: 0; }}
    to {{ opacity: 1; }}
}}
@keyframes mandiq-rise {{
    from {{ opacity: 0; transform: translateY(4px); }}
    to {{ opacity: 1; transform: translateY(0); }}
}}
</style>
"""

def inject_modal_css():
    if "_mandiq_modal_css" not in st.session_state:
        st.markdown(_MODAL_CSS, unsafe_allow_html=True)
        st.session_state["_mandiq_modal_css"] = True


def confirm_dialog(title, body, confirm_label="Confirm", cancel_label="Cancel", key="confirm"):
    """Show a confirmation modal. Returns True/False after user action.

    Uses session_state to track open/dismiss state.
    """
    inject_modal_css()
    dialog_key = f"_modal_{key}"

    if dialog_key not in st.session_state:
        st.session_state[dialog_key] = None

    # Show the modal if triggered
    trigger = st.button(confirm_label, key=f"{key}_trigger", type="secondary")
    if trigger:
        st.session_state[dialog_key] = "open"

    if st.session_state.get(dialog_key) == "open":
        st.markdown(
            f'<div class="mandiq-modal-scrim">'
            f'<div class="mandiq-modal-panel">'
            f'<div class="mandiq-modal-title">{title}</div>'
            f'<div class="mandiq-modal-body">{body}</div>'
            f'<div class="mandiq-modal-actions">'
            f'<button class="mandiq-btn mandiq-btn-secondary" '
            f'onclick="document.getElementById(\'{key}_cancel\').click()">{cancel_label}</button>'
            f'<button class="mandiq-btn mandiq-btn-danger" '
            f'onclick="document.getElementById(\'{key}_confirm\').click()">{confirm_label}</button>'
            f'</div></div></div>',
            unsafe_allow_html=True,
        )
        col1, col2 = st.columns(2)
        with col1:
            if st.button(cancel_label, key=f"{key}_cancel"):
                st.session_state[dialog_key] = False
                st.rerun()
        with col2:
            if st.button(confirm_label, key=f"{key}_confirm"):
                st.session_state[dialog_key] = True
                st.rerun()

    return st.session_state.get(dialog_key) == True


# ═══════════════════════════════════════════════════════════
# 7. TOASTS (notification system)
# ═══════════════════════════════════════════════════════════

_TOAST_CSS = f"""
<style>
.mandiq-toast-container {{
    position: fixed; bottom: 1.5rem; right: 1.5rem;
    z-index: 10000; display: flex; flex-direction: column-reverse;
    gap: 0.5rem; pointer-events: none;
}}
.mandiq-toast {{
    background: {INK};
    border: 1px solid rgba({int(SLATE[1:3],16)},{int(SLATE[3:5],16)},{int(SLATE[5:7],16)},0.3);
    border-radius: 8px; padding: 0.75rem 1rem;
    font-size: 0.85rem; color: {PAPER};
    box-shadow: 0 4px 16px rgba(0,0,0,0.3);
    animation: mandiq-toast-in 0.2s ease;
    pointer-events: auto; max-width: 360px;
    display: flex; align-items: center; gap: 0.5rem;
}}
.mandiq-toast.success {{ border-left: 3px solid {SAGE}; }}
.mandiq-toast.error {{ border-left: 3px solid {RUST}; }}
.mandiq-toast.info {{ border-left: 3px solid {TURMERIC}; }}
.mandiq-toast-close {{
    cursor: pointer; opacity: 0.5; margin-left: auto;
    font-size: 1rem; background: none; border: none; color: {MUTED};
}}

@keyframes mandiq-toast-in {{
    from {{ opacity: 0; transform: translateY(8px); }}
    to {{ opacity: 1; transform: translateY(0); }}
}}
</style>
"""

def inject_toast_css():
    if "_mandiq_toast_css" not in st.session_state:
        st.markdown(_TOAST_CSS, unsafe_allow_html=True)
        st.session_state["_mandiq_toast_css"] = True


def toast(message, type="info", duration=4):
    """Queue a toast notification. Types: info, success, error.

    Auto-dismisses after `duration` seconds.
    Manually closable via × button.
    """
    inject_toast_css()
    toast_key = f"_toast_{len(st.session_state.get('_toasts', []))}"
    toasts = st.session_state.setdefault("_toasts", [])
    toasts.append({"message": message, "type": type, "duration": duration, "key": toast_key})
    st.rerun()


def _render_toasts():
    """Render all queued toast notifications. Called by app.py footer."""
    toasts = st.session_state.get("_toasts", [])
    if not toasts:
        return

    html = '<div class="mandiq-toast-container">'
    for t in toasts:
        html += (
            f'<div class="mandiq-toast {t["type"]}">'
            f'<span>{t["message"]}</span>'
            f'<button class="mandiq-toast-close">\u00d7</button>'
            f'</div>'
        )
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)

    # Auto-dismiss after duration (use JavaScript)
    for t in toasts:
        st.markdown(
            f'<script>setTimeout(function(){{ '
            f'document.querySelector(".mandiq-toast.{t["type"]}:last-child")?.remove(); '
            f'}}, {t["duration"] * 1000});</script>',
            unsafe_allow_html=True,
        )


# ═══════════════════════════════════════════════════════════
# 8. BADGES / STATUS DOTS
# ═══════════════════════════════════════════════════════════

def badge(text, color=TURMERIC, size="small"):
    """Render an inline badge."""
    sizes = {"small": "0.65rem", "medium": "0.75rem", "large": "0.85rem"}
    font_size = sizes.get(size, "0.75rem")
    st.markdown(
        f'<span style="display:inline-block;background:rgba({int(color[1:3],16)},{int(color[3:5],16)},{int(color[5:7],16)},0.15);'
        f'color:{color};font-size:{font_size};font-family:IBM Plex Mono,monospace;'
        f'padding:0.1rem 0.5rem;border-radius:4px;font-weight:500;">{text}</span>',
        unsafe_allow_html=True,
    )


def status_dot(status="green"):
    """Render a small status indicator dot."""
    colors = {"green": SAGE, "amber": TURMERIC, "red": RUST, "muted": FAINT}
    color = colors.get(status, FAINT)
    st.markdown(
        f'<span style="display:inline-block;width:8px;height:8px;'
        f'border-radius:50%;background:{color};'
        f'box-shadow:0 0 4px rgba({int(color[1:3],16)},{int(color[3:5],16)},{int(color[5:7],16)},0.4);'
        f'margin-right:4px;"></span>',
        unsafe_allow_html=True,
    )


# ═══════════════════════════════════════════════════════════
# 9. NAVIGATION ELEMENTS
# ═══════════════════════════════════════════════════════════

_NAV_CSS = f"""
<style>
.mandiq-breadcrumb {{
    font-size: 0.8rem; color: {MUTED};
    font-family: "IBM Plex Sans", system-ui, sans-serif;
    padding: 0.75rem 0;
}}
.mandiq-breadcrumb a {{
    color: {MUTED}; text-decoration: none; transition: color 0.15s;
}}
.mandiq-breadcrumb a:hover {{
    color: {TURMERIC};
}}
.mandiq-breadcrumb .current {{
    color: {PAPER}; font-weight: 500;
}}
.mandiq-tabs {{
    display: flex; gap: 0; border-bottom: 1px solid rgba({int(SLATE[1:3],16)},{int(SLATE[3:5],16)},{int(SLATE[5:7],16)},0.2);
    margin-bottom: 1rem;
}}
.mandiq-tab {{
    padding: 0.5rem 1rem; font-size: 0.85rem; color: {MUTED};
    cursor: pointer; border-bottom: 2px solid transparent;
    transition: all 0.15s; font-family: "IBM Plex Sans", system-ui, sans-serif;
}}
.mandiq-tab:hover {{
    color: {PAPER}; background: rgba(255,255,255,0.02);
}}
.mandiq-tab.active {{
    color: {TURMERIC}; border-bottom-color: {TURMERIC}; font-weight: 500;
}}
</style>
"""

def inject_nav_css():
    if "_mandiq_nav_css" not in st.session_state:
        st.markdown(_NAV_CSS, unsafe_allow_html=True)
        st.session_state["_mandiq_nav_css"] = True


# ═══════════════════════════════════════════════════════════
# 10. SKELETON LOADERS
# ═══════════════════════════════════════════════════════════

_SKELETON_CSS = f"""
<style>
@keyframes mandiq-shimmer {{
    0% {{ background-position: -200% 0; }}
    100% {{ background-position: 200% 0; }}
}}
.mandiq-skeleton {{
    background: linear-gradient(
        90deg,
        rgba({int(SLATE[1:3],16)},{int(SLATE[3:5],16)},{int(SLATE[5:7],16)},0.08) 25%,
        rgba({int(SLATE[1:3],16)},{int(SLATE[3:5],16)},{int(SLATE[5:7],16)},0.15) 50%,
        rgba({int(SLATE[1:3],16)},{int(SLATE[3:5],16)},{int(SLATE[5:7],16)},0.08) 75%
    );
    background-size: 200% 100%;
    animation: mandiq-shimmer 1.5s infinite;
    border-radius: 6px;
}}
@media (prefers-reduced-motion: reduce) {{
    .mandiq-skeleton {{
        animation: none;
        background: rgba({int(SLATE[1:3],16)},{int(SLATE[3:5],16)},{int(SLATE[5:7],16)},0.08);
    }}
}}

/* Shape variants */
.skeleton-text {{ height: 0.85rem; width: 100%; margin-bottom: 0.5rem; }}
.skeleton-title {{ height: 1.2rem; width: 60%; margin-bottom: 0.75rem; }}
.skeleton-kpi {{ height: 5rem; width: 100%; margin-bottom: 0.5rem; }}
.skeleton-chart {{ height: 200px; width: 100%; margin-bottom: 1rem; }}
.skeleton-avatar {{ width: 32px; height: 32px; border-radius: 50%; }}
</style>
"""

def inject_skeleton_css():
    if "_mandiq_skeleton_css" not in st.session_state:
        st.markdown(_SKELETON_CSS, unsafe_allow_html=True)
        st.session_state["_mandiq_skeleton_css"] = True


def skeleton(variant="text"):
    """Render a skeleton placeholder shaped like the expected content.

    Variants: text, title, kpi, chart, avatar
    """
    inject_skeleton_css()
    st.markdown(f'<div class="mandiq-skeleton skeleton-{variant}"></div>', unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════
# ALL-IN-ONE CSS INJECTOR
# ═══════════════════════════════════════════════════════════

_ALL_CSS_INJECTED_KEY = "_mandiq_all_css_injected"

_RESPONSIVE_CSS = f"""
<style>
/* ── Shared touch-friendly target ── */
@media (hover: none) and (pointer: coarse) {{
    .mandiq-btn, .mandiq-btn-primary, .mandiq-btn-secondary,
    .mandiq-btn-ghost, .mandiq-btn-danger,
    button, .stButton button {{
        min-height: 44px;
    }}
    select, input, textarea, .stSelectbox, .stMultiSelect {{
        font-size: 16px !important;
    }}
}}

/* ── Small mobile (< 640px) ── */
@media screen and (max-width: 640px) {{
    h1, .stTitle h1 {{ font-size: 1.4rem !important; }}
    h2, .stSubHeader h2 {{ font-size: 1.15rem !important; }}
    h3 {{ font-size: 1rem !important; }}
    p, li, .stMarkdown p {{ font-size: 0.9rem !important; }}

    .mandiq-btn, .mandiq-btn-primary, .mandiq-btn-secondary,
    .mandiq-btn-ghost, .mandiq-btn-danger {{
        width: 100%;
        justify-content: center;
        padding: 0.6rem 1rem;
    }}
    .stButton button {{ width: 100%; }}

    .mandiq-card, .mandiq-kpi {{
        padding: 0.75rem !important;
        margin-bottom: 0.5rem !important;
    }}
    .mandiq-kpi-value {{ font-size: 1.3rem !important; }}

    .stDataFrame, div[data-testid="stDataFrame"] {{
        overflow-x: auto !important;
        -webkit-overflow-scrolling: touch;
    }}
    .stDataFrame table {{ font-size: 0.8rem !important; }}

    div[data-testid="metric-container"] {{ padding: 0.5rem !important; }}
    div[data-testid="metric-container"] label {{ font-size: 0.75rem !important; }}
    div[data-testid="metric-container"] div[data-testid="metric-value"] {{ font-size: 1.1rem !important; }}

    section[data-testid="stSidebar"] .stMarkdown {{ font-size: 0.85rem !important; }}

    .mandiq-toast-container {{
        bottom: auto !important;
        right: auto !important;
        top: 0 !important;
        left: 0 !important;
        width: 100% !important;
        max-width: 100% !important;
        padding: 0.5rem !important;
    }}
    .mandiq-toast {{ border-radius: 0 !important; margin-bottom: 0.25rem !important; }}

    .mandiq-modal-overlay {{ align-items: flex-end !important; }}
    .mandiq-modal {{
        margin: 0 !important;
        border-radius: 12px 12px 0 0 !important;
        max-height: 90vh !important;
        width: 100% !important;
        max-width: 100% !important;
    }}

    .stPlotlyChart, .js-plotly-plot, .plot-container {{ width: 100% !important; }}

    .stTabs [data-baseweb="tab-list"] {{ flex-wrap: wrap !important; gap: 0.25rem !important; }}
    .stTabs [data-baseweb="tab"] {{ font-size: 0.8rem !important; padding: 0.4rem 0.6rem !important; }}

    .row-widget.stHorizontal {{ flex-direction: column !important; }}
    .row-widget.stHorizontal > div {{
        width: 100% !important;
        flex: 0 0 100% !important;
        min-width: 0 !important;
    }}

    .skeleton-chart {{ height: 140px !important; }}
    .skeleton-kpi {{ height: 3.5rem !important; }}
}}

/* ── Tablet (641px – 1024px) ── */
@media screen and (min-width: 641px) and (max-width: 1024px) {{
    h1 {{ font-size: 1.6rem !important; }}
    h2 {{ font-size: 1.3rem !important; }}

    .mandiq-btn, .stButton button {{ padding: 0.5rem 1rem; }}
    .mandiq-card {{ padding: 1rem !important; }}

    .stTabs [data-baseweb="tab"] {{ font-size: 0.85rem !important; padding: 0.5rem 0.8rem !important; }}

    .row-widget.stHorizontal > div {{ min-width: 0 !important; }}
}}

/* ── Desktop (1025px+) ── */
@media screen and (min-width: 1025px) {{
    .mandiq-btn, .stButton button {{ transition: all 0.15s ease; }}
    .mandiq-btn:hover {{ transform: translateY(-1px); }}
}}

/* ── Print ── */
@media print {{
    .stApp header, section[data-testid="stSidebar"],
    .stButton, button, .mandiq-toast-container,
    .mandiq-modal-overlay {{ display: none !important; }}
    .main .block-container {{ max-width: 100% !important; padding: 0 !important; }}
}}
</style>
"""


def inject_all_component_css():
    """Inject all component CSS at once. Safe to call multiple times."""
    if _ALL_CSS_INJECTED_KEY not in st.session_state:
        st.markdown(_BUTTON_CSS, unsafe_allow_html=True)
        st.markdown(_INPUT_CSS, unsafe_allow_html=True)
        st.markdown(_CARD_CSS, unsafe_allow_html=True)
        st.markdown(_TABLE_CSS, unsafe_allow_html=True)
        st.markdown(_MODAL_CSS, unsafe_allow_html=True)
        st.markdown(_TOAST_CSS, unsafe_allow_html=True)
        st.markdown(_NAV_CSS, unsafe_allow_html=True)
        st.markdown(_SKELETON_CSS, unsafe_allow_html=True)
        st.markdown(_RESPONSIVE_CSS, unsafe_allow_html=True)
        st.session_state[_ALL_CSS_INJECTED_KEY] = True
