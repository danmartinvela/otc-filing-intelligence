import pandas as pd
import plotly.graph_objects as go

from config import THEME
from charts.theme import themed_figure


def form_type_chart(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure(
        go.Bar(
            x=df["count"],
            y=df["form_type"],
            orientation="h",
            marker=dict(color=THEME["cat_blue"]),
            text=df["count"].map(lambda v: f"{v:,}"),
            textposition="outside",
            textfont=dict(color=THEME["text_secondary"]),
            hovertemplate="%{y}: %{x:,}<extra></extra>",
        )
    )
    height = max(240, 28 * len(df))
    fig.update_layout(
        height=height,
        showlegend=False,
        xaxis=dict(title=None, showgrid=True, range=[0, df["count"].max() * 1.2 if not df.empty else 1]),
        yaxis=dict(title=None),
        bargap=0.3,
    )
    fig = themed_figure(fig)
    fig.update_layout(margin=dict(r=55))
    return fig


def monthly_evolution_chart(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if df.empty:
        fig.update_layout(height=300)
        return themed_figure(fig)

    months = pd.to_datetime(df["year_month"], format="%Y%m")
    fig.add_trace(
        go.Scatter(
            x=months,
            y=df["count"],
            mode="lines+markers",
            line=dict(color=THEME["cat_blue"], width=2),
            marker=dict(size=6, color=THEME["cat_blue"]),
            hovertemplate="%{x|%b %Y}: %{y:,}<extra></extra>",
        )
    )
    fig.update_layout(
        height=300,
        showlegend=False,
        xaxis=dict(title=None),
        yaxis=dict(title="Filings EVENT", rangemode="tozero"),
    )
    return themed_figure(fig)


def top_companies_chart(df: pd.DataFrame) -> go.Figure:
    df = df.sort_values("count", ascending=True)
    fig = go.Figure(
        go.Bar(
            x=df["count"],
            y=df["company_name"],
            orientation="h",
            marker=dict(color=THEME["cat_blue"]),
            text=df["count"].map(lambda v: f"{v:,}"),
            textposition="outside",
            textfont=dict(color=THEME["text_secondary"]),
            hovertemplate="%{y}: %{x:,}<extra></extra>",
        )
    )
    height = max(280, 26 * len(df))
    fig.update_layout(
        height=height,
        showlegend=False,
        xaxis=dict(title=None, showgrid=True, range=[0, df["count"].max() * 1.25 if not df.empty else 1]),
        yaxis=dict(title=None, automargin=True),
        bargap=0.3,
    )
    fig = themed_figure(fig)
    fig.update_layout(margin=dict(l=10, r=55))
    return fig
