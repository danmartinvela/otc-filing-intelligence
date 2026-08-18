"""Explorador de Eventos — filterable, sortable, paginated table of EVENT filings.

Filtering, sorting, and pagination all happen in SQL (see services/filters.py
and database/queries.py) so this stays fast even with thousands of rows —
only the current page is ever materialized into a DataFrame.
"""
import streamlit as st

from components.data_table import render_events_table, render_pagination
from components.header import render_page_header
from components.sidebar_filters import render_sidebar_filters
from config import DEFAULT_PAGE_SIZE
from database.connection import get_connection
from database.queries import (
    get_event_date_bounds,
    get_events_count,
    get_events_page,
    get_filter_options,
    get_last_updated,
)
from services.filters import build_where_clause, resolve_order_by
from utils.formatting import format_iso_timestamp

_PAGE_KEY = "explorer_page"
_FILTERS_SIGNATURE_KEY = "explorer_filters_signature"


def render() -> None:
    conn = get_connection()

    render_page_header(
        "Explorador de Eventos",
        "Filtra, ordena y revisa los filings EVENT detectados por el pipeline",
        format_iso_timestamp(get_last_updated(conn)),
    )

    filter_options = get_filter_options(conn)
    date_bounds = get_event_date_bounds(conn)
    filters, sort_label, ascending = render_sidebar_filters(filter_options, date_bounds)

    where_extra, params = build_where_clause(filters)
    params = tuple(params)
    order_by = resolve_order_by(sort_label, ascending)

    # Any change in filters/sort restarts pagination at page 1.
    signature = (where_extra, params, order_by)
    if st.session_state.get(_FILTERS_SIGNATURE_KEY) != signature:
        st.session_state[_FILTERS_SIGNATURE_KEY] = signature
        st.session_state[_PAGE_KEY] = 1

    total_rows = get_events_count(conn, where_extra, params)
    current_page = st.session_state.get(_PAGE_KEY, 1)
    offset = (current_page - 1) * DEFAULT_PAGE_SIZE

    df = get_events_page(conn, where_extra, params, order_by, DEFAULT_PAGE_SIZE, offset)

    selected_filename = render_events_table(df)
    if selected_filename:
        st.session_state["selected_filing"] = selected_filename
        st.switch_page(st.session_state["nav_pages"]["detalle"])

    if total_rows:
        new_page = render_pagination(total_rows, DEFAULT_PAGE_SIZE, current_page)
        if new_page != current_page:
            st.session_state[_PAGE_KEY] = new_page
            st.rerun()
