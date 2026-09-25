import streamlit as st

from charts.overview_charts import (
    category_breakdown_chart,
    daily_evolution_chart,
    score_distribution_chart,
)
from charts.theme import render_chart
from components.header import render_page_header
from components.kpi_card import render_kpi_row
from components.section import section_card
from database.connection import get_connection
from database.queries import (
    get_category_counts,
    get_daily_filing_counts,
    get_importance_score_distribution,
    get_last_updated,
    get_summary_kpis,
)
from services.metrics import build_kpi_cards
from utils.formatting import format_iso_timestamp


def render() -> None:
    conn = get_connection()

    render_page_header(
        "Resumen Ejecutivo",
        "Estado general del pipeline de filings",
        format_iso_timestamp(get_last_updated(conn)),
    )

    kpis = get_summary_kpis(conn)
    render_kpi_row(build_kpi_cards(kpis))

    col_left, col_right = st.columns([1, 2])

    with col_left:
        with section_card("Eventos por categoría", key="category"):
            category_df = get_category_counts(conn)
            if category_df.empty:
                st.markdown('<p class="empty-note">Sin datos todavía.</p>', unsafe_allow_html=True)
            else:
                render_chart(category_breakdown_chart(category_df))

    with col_right:
        with section_card("Evolución diaria", key="evolution"):
            daily_df = get_daily_filing_counts(conn)
            if daily_df.empty:
                st.markdown('<p class="empty-note">Sin datos todavía.</p>', unsafe_allow_html=True)
            else:
                render_chart(daily_evolution_chart(daily_df))

    with section_card("Distribución de importance score", key="score-distribution"):
        score_df = get_importance_score_distribution(conn)
        if score_df.empty:
            st.markdown('<p class="empty-note">Todavía no hay filings analizados por el LLM.</p>', unsafe_allow_html=True)
        else:
            render_chart(score_distribution_chart(score_df))
