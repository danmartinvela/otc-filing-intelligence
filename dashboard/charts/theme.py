import plotly.graph_objects as go
import streamlit as st

from config import THEME

LAYOUT_DEFAULTS = dict(
    paper_bgcolor=THEME["surface"],
    plot_bgcolor=THEME["surface"],
    font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif", color=THEME["text_secondary"], size=12),
    colorway=[
        THEME["cat_blue"], THEME["cat_orange"], THEME["cat_aqua"], THEME["cat_yellow"],
        THEME["cat_magenta"], THEME["cat_green"], THEME["cat_violet"], THEME["cat_red"],
    ],
    title=dict(font=dict(color=THEME["text_primary"], size=13)),
    legend=dict(
        bgcolor="rgba(0,0,0,0)",
        font=dict(color=THEME["text_secondary"], size=11),
        orientation="h",
        yanchor="bottom",
        y=1.02,
        xanchor="left",
        x=0,
    ),
    xaxis=dict(
        gridcolor=THEME["gridline"],
        linecolor=THEME["baseline"],
        zerolinecolor=THEME["baseline"],
        tickfont=dict(color=THEME["text_muted"]),
        title=dict(font=dict(color=THEME["text_muted"])),
        automargin=True,
    ),
    yaxis=dict(
        gridcolor=THEME["gridline"],
        linecolor=THEME["baseline"],
        zerolinecolor=THEME["baseline"],
        tickfont=dict(color=THEME["text_muted"]),
        title=dict(font=dict(color=THEME["text_muted"])),
        automargin=True,
    ),
    margin=dict(l=10, r=45, t=30, b=10),
    hoverlabel=dict(
        bgcolor=THEME["surface_alt"],
        bordercolor=THEME["border"],
        font=dict(color=THEME["text_primary"], size=12),
    ),
)


def themed_figure(fig: go.Figure) -> go.Figure:
    fig.update_layout(**LAYOUT_DEFAULTS)
    return fig


def render_chart(fig: go.Figure) -> None:
    st.plotly_chart(fig, use_container_width=True, theme=None, config={"displayModeBar": False})
