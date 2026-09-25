"""The Explorador de Eventos results table + pagination controls.

Column config (progress bar for score, checkbox icon for deep research) uses
Streamlit's built-in st.column_config — no external grid component.
"""
from typing import Optional

import pandas as pd
import streamlit as st

from utils.formatting import format_yyyymmdd

_COLUMN_RENAME = {
    "date_filed": "Fecha",
    "company_name": "Empresa",
    "ticker": "Ticker",
    "form_type": "Formulario",
    "primary_event_type": "Evento principal",
    "importance_score": "Importance Score",
    "deep_research": "Deep Research",
}

_DISPLAY_ORDER = [
    "Fecha", "Empresa", "Ticker", "Formulario", "Evento principal",
    "Importance Score", "Deep Research",
]


def render_events_table(df: pd.DataFrame) -> Optional[str]:
    """Renders the table; returns the filename of the selected row, if any."""
    if df.empty:
        st.markdown('<p class="empty-note">Ningún filing coincide con estos filtros.</p>', unsafe_allow_html=True)
        return None

    display_df = df.copy()
    display_df["date_filed"] = display_df["date_filed"].map(format_yyyymmdd)
    display_df["deep_research"] = display_df["deep_research"].fillna(0).astype(bool)
    display_df = display_df.rename(columns=_COLUMN_RENAME)

    event = st.dataframe(
        display_df[_DISPLAY_ORDER],
        width="stretch",
        hide_index=True,
        height=560,
        column_config={
            "Importance Score": st.column_config.ProgressColumn(
                "Importance Score", min_value=0, max_value=100, format="%d"
            ),
            "Deep Research": st.column_config.CheckboxColumn("Deep Research"),
        },
        selection_mode="single-row",
        on_select="rerun",
    )

    selected_rows = event.selection.rows if event and event.selection else []
    if not selected_rows:
        return None
    return df.iloc[selected_rows[0]]["filename"]


_SEARCH_RESULTS_COLUMN_RENAME = {
    "date_filed": "Fecha",
    "company_name": "Empresa",
    "ticker": "Ticker",
    "form_type": "Formulario",
}
_SEARCH_RESULTS_DISPLAY_ORDER = ["Fecha", "Empresa", "Ticker", "Formulario"]


def render_search_results_table(df: pd.DataFrame) -> Optional[str]:
    """Renders the Detalle del Filing search-results table; returns the
    filename of the selected row, if any. Same click-to-select idiom as
    render_events_table, with the lean column set search_event_filings
    returns (no LLM-analysis join — the search itself never touches
    clean_text/raw_text/raw_response). Assumes df is non-empty; the caller
    handles the "no results" / empty-search states."""
    display_df = df.copy()
    display_df["date_filed"] = display_df["date_filed"].map(format_yyyymmdd)
    display_df = display_df.rename(columns=_SEARCH_RESULTS_COLUMN_RENAME)

    event = st.dataframe(
        display_df[_SEARCH_RESULTS_DISPLAY_ORDER],
        width="stretch",
        hide_index=True,
        selection_mode="single-row",
        on_select="rerun",
    )

    selected_rows = event.selection.rows if event and event.selection else []
    if not selected_rows:
        return None
    return df.iloc[selected_rows[0]]["filename"]


def render_pagination(total_rows: int, page_size: int, current_page: int) -> int:
    """Renders 'showing X-Y of N' + prev/next controls. Returns the (possibly updated) page."""
    total_pages = max(1, -(-total_rows // page_size))
    current_page = min(current_page, total_pages)

    start = (current_page - 1) * page_size + 1
    end = min(current_page * page_size, total_rows)

    col_info, col_prev, col_indicator, col_next = st.columns([6, 1, 1, 1])
    with col_info:
        st.markdown(
            f'<div class="pagination-info">Mostrando {start}–{end} de {total_rows}</div>',
            unsafe_allow_html=True,
        )
    with col_prev:
        if st.button("←", disabled=current_page <= 1, width="stretch"):
            current_page -= 1
    with col_indicator:
        st.markdown(
            f'<div class="pagination-info" style="text-align:center">{current_page} / {total_pages}</div>',
            unsafe_allow_html=True,
        )
    with col_next:
        if st.button("→", disabled=current_page >= total_pages, width="stretch"):
            current_page += 1

    return current_page
