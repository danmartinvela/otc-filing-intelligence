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
