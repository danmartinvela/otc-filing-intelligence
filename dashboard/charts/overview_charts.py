import pandas as pd
import plotly.graph_objects as go

from config import THEME
from charts.theme import themed_figure

_CATEGORY_COLORS = {"EVENT": THEME["cat_blue"], "CONTEXT": THEME["cat_orange"]}
_CATEGORY_LABELS = {"EVENT": "EVENT", "CONTEXT": "CONTEXT"}


def category_breakdown_chart(df: pd.DataFrame) -> go.Figure:
    df = df.sort_values("count", ascending=True)
    colors = [_CATEGORY_COLORS.get(cat, THEME["text_muted"]) for cat in df["filing_category"]]
    fig = go.Figure(
        go.Bar(
            x=df["count"],
            y=df["filing_category"],
            orientation="h",
            marker=dict(color=colors),
            text=df["count"].map(lambda v: f"{v:,}"),
            textposition="outside",
            textfont=dict(color=THEME["text_secondary"]),
            hovertemplate="%{y}: %{x:,}<extra></extra>",
            width=0.5,
        )
    )
    fig.update_layout(
        height=180,
        showlegend=False,
        xaxis=dict(title=None, showgrid=True, range=[0, df["count"].max() * 1.25]),
        yaxis=dict(title=None),
        bargap=0.4,
    )
    fig = themed_figure(fig)
    fig.update_layout(margin=dict(r=60))
    return fig


def daily_evolution_chart(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if df.empty:
        fig.update_layout(height=280)
        return themed_figure(fig)

    dates = pd.to_datetime(df["date_filed"], format="%Y%m%d")
    df = df.assign(_date=dates)

    for category in ("EVENT", "CONTEXT"):
        series = df[df["filing_category"] == category].sort_values("_date")
        if series.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=series["_date"],
                y=series["count"],
                mode="lines",
                name=_CATEGORY_LABELS[category],
                line=dict(color=_CATEGORY_COLORS[category], width=2),
                hovertemplate=f"{_CATEGORY_LABELS[category]} · %{{x|%d/%m/%Y}}: %{{y:,}}<extra></extra>",
            )
        )
    fig.update_layout(height=280, xaxis=dict(title=None), yaxis=dict(title=None, rangemode="tozero"))
    return themed_figure(fig)


def score_distribution_chart(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if df.empty:
        fig.update_layout(height=280)
        return themed_figure(fig)

    fig.add_trace(
        go.Histogram(
            x=df["importance_score"],
            xbins=dict(start=0, end=100, size=5),
            marker=dict(color=THEME["cat_blue"]),
            hovertemplate="Score %{x}: %{y} filings<extra></extra>",
        )
    )
    fig.update_layout(
        height=280,
        showlegend=False,
        xaxis=dict(title="Importance score", range=[0, 100]),
        yaxis=dict(title="Filings", rangemode="tozero"),
        bargap=0.05,
    )
    return themed_figure(fig)
