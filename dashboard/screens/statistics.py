"""Estadísticas — aggregate analytical charts over all EVENT filings."""
import streamlit as st

from charts.overview_charts import category_breakdown_chart, score_distribution_chart
from charts.stats_charts import form_type_chart, monthly_evolution_chart, top_companies_chart
from charts.theme import render_chart
from components.header import render_page_header
from components.meter import render_meter
from components.section import section_card
from database.connection import get_connection
from database.queries import (
    get_category_counts,
    get_deep_research_ratio,
    get_form_type_counts,
    get_importance_score_distribution,
    get_last_updated,
    get_monthly_event_counts,
    get_top_companies,
)
from utils.formatting import format_iso_timestamp


def render() -> None:
    conn = get_connection()

    render_page_header(
        "Estadísticas",
        "Análisis agregado de los eventos detectados",
        format_iso_timestamp(get_last_updated(conn)),
    )

    col_category, col_meter = st.columns([2, 1])

    with col_category:
        with section_card("Eventos por categoría", key="stats-category"):
            category_df = get_category_counts(conn)
            if category_df.empty:
                st.markdown('<p class="empty-note">Sin datos todavía.</p>', unsafe_allow_html=True)
            else:
                render_chart(category_breakdown_chart(category_df))

    with col_meter:
        with section_card("Deep Research", key="stats-deep-research"):
            ratio = get_deep_research_ratio(conn)
            pct = (ratio["deep_research"] / ratio["total"] * 100) if ratio["total"] else 0.0
            render_meter(
                "% de filings analizados marcados como Deep Research",
                pct,
                f"{ratio['deep_research']:,} de {ratio['total']:,} filings analizados",
            )

    with section_card("Eventos por formulario", key="stats-form-type"):
        form_df = get_form_type_counts(conn)
        if form_df.empty:
            st.markdown('<p class="empty-note">Sin datos todavía.</p>', unsafe_allow_html=True)
        else:
            render_chart(form_type_chart(form_df))

    with section_card("Evolución temporal", key="stats-evolution"):
        monthly_df = get_monthly_event_counts(conn)
        if monthly_df.empty:
            st.markdown('<p class="empty-note">Sin datos todavía.</p>', unsafe_allow_html=True)
        else:
            render_chart(monthly_evolution_chart(monthly_df))

    col_companies, col_scores = st.columns(2)

    with col_companies:
        with section_card("Empresas con más eventos", key="stats-top-companies"):
            companies_df = get_top_companies(conn)
            if companies_df.empty:
                st.markdown('<p class="empty-note">Sin datos todavía.</p>', unsafe_allow_html=True)
            else:
                render_chart(top_companies_chart(companies_df))

    with col_scores:
        with section_card("Distribución de Importance Score", key="stats-score-distribution"):
            score_df = get_importance_score_distribution(conn)
            if score_df.empty:
                st.markdown('<p class="empty-note">Todavía no hay filings analizados por el LLM.</p>', unsafe_allow_html=True)
            else:
                render_chart(score_distribution_chart(score_df))
