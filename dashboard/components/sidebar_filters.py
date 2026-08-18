"""Renders the Explorador de Eventos filter panel in the sidebar.

Pure UI: takes the distinct option lists and date bounds the page already
fetched from the database, returns an EventFilters + the chosen sort — this
module never touches SQL.
"""
from datetime import date
from typing import Dict, List, Tuple

import streamlit as st

from services.filters import EventFilters, SORT_OPTIONS
from utils.formatting import date_to_yyyymmdd, yyyymmdd_to_date


def render_sidebar_filters(
    filter_options: Dict[str, List[str]], date_bounds: Dict[str, str]
) -> Tuple[EventFilters, str, bool]:
    min_date = yyyymmdd_to_date(date_bounds.get("min_date"), fallback=date(2020, 1, 1))
    max_date = yyyymmdd_to_date(date_bounds.get("max_date"), fallback=date.today())

    st.sidebar.markdown('<div class="filter-panel-title">Filtros</div>', unsafe_allow_html=True)

    date_range = st.sidebar.date_input(
        "Rango de fechas",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )
    date_from, date_to = (date_range if len(date_range) == 2 else (min_date, max_date))

    ticker = st.sidebar.text_input("Ticker", placeholder="p. ej. AAPL")
    company_name = st.sidebar.text_input("Empresa", placeholder="p. ej. Acme Corp")

    form_types = st.sidebar.multiselect("Formulario", options=filter_options.get("form_types", []))
    event_types = st.sidebar.multiselect("Categoría del evento", options=filter_options.get("event_types", []))
    otc_tiers = st.sidebar.multiselect("OTC Tier", options=filter_options.get("otc_tiers", []))

    min_score = st.sidebar.slider("Importance score mínimo", min_value=0, max_value=100, value=0, step=5)
    deep_research_only = st.sidebar.checkbox("Solo Deep Research")

    st.sidebar.markdown('<div class="filter-panel-title">Orden</div>', unsafe_allow_html=True)
    sort_label = st.sidebar.selectbox("Ordenar por", options=list(SORT_OPTIONS.keys()), index=0)
    ascending = st.sidebar.toggle("Ascendente", value=False)

    filters = EventFilters(
        date_from=date_to_yyyymmdd(date_from),
        date_to=date_to_yyyymmdd(date_to),
        ticker=ticker,
        company_name=company_name,
        form_types=form_types,
        event_types=event_types,
        min_importance_score=min_score,
        deep_research_only=deep_research_only,
        otc_tiers=otc_tiers,
    )
    return filters, sort_label, ascending
