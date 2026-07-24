"""
MandiIQ — Flip-board KPI hero (Layer 3 of the design system).

Streamlit custom component wrapping a React flip-board that receives
4 analytical KPIs from Python. The component holds its own client-side
state so it only animates when a value actually changes — immune to
unrelated Streamlit reruns (PRD §1/§4).

Usage:
    from dashboard.flip_board import flip_board
    flip_board(
        effect="₹350.20",
        effect_raw=350.20,
        avg_price="₹1,842",
        avg_price_raw=1842.0,
        districts="23",
        districts_raw=23,
        mape="12.8%",
        mape_raw=12.8,
    )

The built bundle lives in mandi_rdd/dashboard/frontend/dist/ and is
committed to the repo so deploy (Render / Docker) doesn't need node.
To rebuild: cd mandi_rdd/dashboard/frontend && npm install && npm run build
"""

import streamlit.components.v1 as components
from pathlib import Path

# Resolve the built bundle path relative to this file.
_FRONTEND_DIST = Path(__file__).resolve().parent / "frontend" / "dist"

# declare_component — loads from the built static bundle.
# Streamlit serves the files from this directory.
_component_func = components.declare_component(
    "flip_board",
    path=str(_FRONTEND_DIST),
)


def flip_board(
    effect: str = "—",
    effect_raw: float = float("nan"),
    avg_price: str = "—",
    avg_price_raw: float = float("nan"),
    districts: str = "—",
    districts_raw: float = float("nan"),
    mape: str = "—",
    mape_raw: float = float("nan"),
) -> None:
    """Render the flip-board KPI hero.

    Each KPI takes a display string and a raw numeric value.
    The raw value is used for change detection (only flip when it
    actually changes). Use float('nan') for the initial/unknown state.

    Falls back gracefully if the frontend bundle is missing (e.g.
    during development before npm build) — renders plain st.metric.
    """
    # If the bundle doesn't exist, fall back to st.metric
    if not _FRONTEND_DIST.exists():
        import streamlit as st
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("RDD Effect", effect)
        with col2:
            st.metric("Avg Price", avg_price)
        with col3:
            st.metric("Districts Flagged", districts)
        with col4:
            st.metric("Forecast MAPE", mape)
        return

    kpis = {
        "effect": {
            "label": "RDD Effect",
            "value": effect,
            "raw": effect_raw,
            "prefix": "+₹",
        },
        "avg_price": {
            "label": "Avg Price",
            "value": avg_price,
            "raw": avg_price_raw,
            "prefix": "₹",
        },
        "districts": {
            "label": "Districts",
            "value": districts,
            "raw": districts_raw,
        },
        "mape": {
            "label": "Forecast MAPE",
            "value": mape,
            "raw": mape_raw,
            "suffix": "%",
        },
    }

    # NaN / Inf are not valid JSON tokens — they serialize to the literal
    # `NaN`/`Infinity` and break the React client's JSON.parse (manifests as
    # "Unexpected token 'N'... is not valid JSON"). Coerce non-finite raw
    # values to None (→ JSON null) so the component treats them as the
    # "no value / initial" state. This also makes the wrapper safe for the
    # float("nan") defaults above and for callers that pass nan on purpose.
    import math as _math
    for _k in kpis:
        _r = kpis[_k]["raw"]
        if isinstance(_r, float) and not _math.isfinite(_r):
            kpis[_k]["raw"] = None

    # Render the component. On Streamlit Cloud the custom-component frontend
    # may fail to register (missing manifest / static-asset path issues), so
    # fall back to plain st.metric instead of breaking the whole page.
    try:
        _component_func(kpis=kpis, default=None)
    except Exception:
        import streamlit as st
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("RDD Effect", effect)
        with col2:
            st.metric("Avg Price", avg_price)
        with col3:
            st.metric("Districts Flagged", districts)
        with col4:
            st.metric("Forecast MAPE", mape)
