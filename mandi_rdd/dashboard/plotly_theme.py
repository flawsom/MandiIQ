"""
MandiIQ — Shared Plotly theming helper (PRD §6).

All 5 pages that render Plotly charts should call make_themed_figure()
instead of hand-declaring layout properties inline. This is the single
source of truth for: transparent backgrounds, on-palette fonts,
grid colors, and margin defaults.
"""

import plotly.graph_objects as go

# Palette shorthand (duplicated from theme.py to avoid circular imports
# if theme.py ever grows heavy — these are tiny constants).
INK   = "#000000"
PAPER = "#ffffff"
MUTED = "#bababa"


def make_themed_figure(
    height: int | None = None,
    show_legend: bool = True,
    margin: dict | None = None,
) -> go.Figure:
    """Return a plotly.graph_objects.Figure with the MandiIQ theme applied.

    Use as the base figure, then add_traces() on top. Or call
    fig.update_layout(make_themed_layout(...)) on an existing fig.

    Args:
        height: chart height in px. None = Plotly auto.
        show_legend: whether to show the legend.
        margin: override default margins. Defaults to compact.
    """
    fig = go.Figure()
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(
            family="IBM Plex Mono, monospace",
            color=PAPER,
            size=12,
        ),
        showlegend=show_legend,
        height=height,
        margin=margin or dict(l=40, r=20, t=30, b=40),
        hovermode="x unified",
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            font=dict(color=MUTED, size=11),
        ),
        xaxis=dict(
            gridcolor="rgba(255,255,255,0.05)",
            zerolinecolor="rgba(255,255,255,0.1)",
        ),
        yaxis=dict(
            gridcolor="rgba(255,255,255,0.05)",
            zerolinecolor="rgba(255,255,255,0.1)",
        ),
    )
    return fig


def make_themed_layout(
    height: int | None = None,
    show_legend: bool = True,
    margin: dict | None = None,
) -> dict:
    """Return a layout dict for fig.update_layout() on an existing figure.

    Same args as make_themed_figure(). Use when you already have a fig
    (e.g. from px.line) and want to apply the theme.
    """
    return dict(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(
            family="IBM Plex Mono, monospace",
            color=PAPER,
            size=12,
        ),
        showlegend=show_legend,
        height=height,
        margin=margin or dict(l=40, r=20, t=30, b=40),
        hovermode="x unified",
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            font=dict(color=MUTED, size=11),
        ),
        xaxis=dict(
            gridcolor="rgba(255,255,255,0.05)",
            zerolinecolor="rgba(255,255,255,0.1)",
        ),
        yaxis=dict(
            gridcolor="rgba(255,255,255,0.05)",
            zerolinecolor="rgba(255,255,255,0.1)",
        ),
    )
