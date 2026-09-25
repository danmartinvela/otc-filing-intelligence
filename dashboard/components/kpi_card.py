"""Renders the KPI row on the executive summary as stat tiles (label + value +
caption) — see dataviz skill: a handful of headline numbers is a KPI row of
stat tiles, not a chart.
"""
from typing import Dict, List

import streamlit as st


def _card_html(card: Dict) -> str:
    value_class = "kpi-value accent" if card.get("accent") else "kpi-value"
    return (
        '<div class="kpi-card">'
        f'<div class="kpi-label">{card["label"]}</div>'
        f'<div class="{value_class}">{card["value"]}</div>'
        f'<div class="kpi-caption">{card["caption"]}</div>'
        "</div>"
    )


def render_kpi_row(cards: List[Dict]) -> None:
    tiles = "".join(_card_html(card) for card in cards)
    st.markdown(f'<div class="kpi-row">{tiles}</div>', unsafe_allow_html=True)
