"""Detalle del Filing — full record for one EVENT filing.

Reached either by selecting a row in the Explorador (which sets
st.session_state["selected_filing"] and switches here) or, if opened
directly, via the manual picker fallback below.
"""
import streamlit as st

from components.detail_sections import (
    render_ai_analysis,
    render_evidence,
    render_full_text,
    render_general_info,
    render_structured_extraction,
)
from components.header import render_page_header
from database.connection import get_connection
from database.queries import get_all_event_filings_lite, get_filing_detail, get_last_updated
from utils.formatting import format_iso_timestamp, format_yyyymmdd


def _render_manual_picker(conn) -> None:
    filings = get_all_event_filings_lite(conn)
    if filings.empty:
        st.markdown('<p class="empty-note">Todavía no hay filings EVENT en la base de datos.</p>', unsafe_allow_html=True)
        return

    options = filings["filename"].tolist()
    labels = {
        row.filename: f"{format_yyyymmdd(row.date_filed)} · {row.company_name} ({row.ticker or 's/t'}) · {row.form_type}"
        for row in filings.itertuples()
    }
    selected = st.selectbox(
        "Buscar un filing",
        options=options,
        format_func=lambda fn: labels.get(fn, fn),
        index=None,
        placeholder="Escribe el nombre de una empresa o ticker...",
    )
    # st.selectbox returns its current value on every rerun, not just when the
    # user changes it — only act (and rerun) on an actual change, or this loops forever.
    if selected and selected != st.session_state.get("selected_filing"):
        st.session_state["selected_filing"] = selected
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
        st.markdown('<p class="empty-note">Selecciona un filing desde el Explorador de Eventos, o búscalo aquí directamente.</p>', unsafe_allow_html=True)
        _render_manual_picker(conn)
        return

    detail = get_filing_detail(conn, filename)
    if detail is None:
        st.warning("No se encontró el filing seleccionado. Puede que ya no exista en la base de datos.")
        st.session_state["selected_filing"] = None
        _render_manual_picker(conn)
        return

    with st.expander("Buscar otro filing"):
        _render_manual_picker(conn)

    render_general_info(detail)
    render_ai_analysis(detail)
    render_structured_extraction(detail)
    render_evidence(detail)
    render_full_text(detail)
