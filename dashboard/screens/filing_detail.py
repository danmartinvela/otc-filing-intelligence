"""Detalle del Filing — full record for one EVENT filing.

Reached either by selecting a row in the Explorador (which sets
st.session_state["selected_filing"] and switches here — opens directly,
no re-search needed) or by searching on this page directly.

The search is strictly on-demand: nothing is queried and nothing is loaded
into memory until the user types something (see _render_search). There is
no upfront list of filings, no dropdown of every filing, and no Python-side
filtering over a pre-fetched list — every keystroke that changes the term
runs one indexed SQLite query for up to _RESULTS_LIMIT + 1 rows (the lean
columns only; clean_text/raw_text/raw_response are never touched by search,
only by get_filing_detail once a specific filename is picked).
"""
import streamlit as st

from components.data_table import render_search_results_table
from components.detail_sections import (
    render_ai_analysis,
    render_evidence,
    render_full_text,
    render_general_info,
)
from components.header import render_page_header
from database.connection import get_connection
from database.queries import get_filing_detail, get_last_updated, search_event_filings
from utils.formatting import format_iso_timestamp

_RESULTS_LIMIT = 50

# Durable session key for the search term — deliberately never passed as the
# text_input's own `key=`. Streamlit prunes a widget's own session_state
# entry once that widget isn't instantiated in a render (confirmed earlier
# for the Explorador de Eventos filters, see sidebar_filters.py), and this
# page's search box goes uninstantiated the moment the user navigates to any
# other page — exactly the trip the term must survive. _SEARCH_WIDGET_KEY is
# the widget's own disposable key; the durable one is read to seed the
# widget's `value=` and written back with its result every render.
_SEARCH_TERM_KEY = "filing_detail_search_term"
_SEARCH_WIDGET_KEY = "_widget__filing_detail_search_term"


def _render_search(conn) -> None:
    if _SEARCH_TERM_KEY not in st.session_state:
        st.session_state[_SEARCH_TERM_KEY] = ""

    st.markdown('<div class="detail-label">BUSCAR UN FILING</div>', unsafe_allow_html=True)
    term = st.text_input(
        "Buscar por empresa o ticker",
        value=st.session_state[_SEARCH_TERM_KEY],
        placeholder="Buscar por ticker o empresa...",
        label_visibility="collapsed",
        key=_SEARCH_WIDGET_KEY,
    )
    st.session_state[_SEARCH_TERM_KEY] = term
    term = term.strip()

    if not term:
        # Empty field: no query, no results, nothing loaded — the whole point.
        return

    results = search_event_filings(conn, term, limit=_RESULTS_LIMIT + 1)
    if results.empty:
        st.markdown('<p class="empty-note">Ningún filing coincide con esta búsqueda.</p>', unsafe_allow_html=True)
        return

    truncated = len(results) > _RESULTS_LIMIT
    display_results = results.head(_RESULTS_LIMIT)

    if truncated:
        st.caption(f"Mostrando los {_RESULTS_LIMIT} filings más recientes")
    else:
        count = len(display_results)
        st.caption(f"{count} resultado{'s' if count != 1 else ''}")

    selected_filename = render_search_results_table(display_results)
    if selected_filename and selected_filename != st.session_state.get("selected_filing"):
        st.session_state["selected_filing"] = selected_filename
        st.rerun()


def render() -> None:
    conn = get_connection()

    render_page_header(
        "Detalle del Filing",
        "Información completa de un filing seleccionado",
        format_iso_timestamp(get_last_updated(conn)),
    )

    filename = st.session_state.get("selected_filing")
    if not filename:
        _render_search(conn)
        return

    detail = get_filing_detail(conn, filename)
    if detail is None:
        st.warning("No se encontró el filing seleccionado. Puede que ya no exista en la base de datos.")
        st.session_state["selected_filing"] = None
        _render_search(conn)
        return

    with st.expander("Buscar otro filing"):
        _render_search(conn)

    render_general_info(detail)
    render_ai_analysis(detail)
    render_evidence(detail)
    render_full_text(detail)
