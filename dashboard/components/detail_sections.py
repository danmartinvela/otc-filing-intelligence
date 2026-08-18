"""Renders the Detalle del Filing page sections. Pure presentation — the page
module fetches the record and hands this module plain dicts/strings.

All HTML is built as single-line strings: a blank or indented line inside an
unsafe_allow_html block gets parsed as a Markdown code block instead of raw
HTML (see components/kpi_card.py for the same fix, hit first there).
"""
import html
from typing import Dict, List, Optional

import streamlit as st

from components.section import section_card
from config import MAX_FULL_TEXT_CHARS
from utils.formatting import format_score, format_yyyymmdd
from utils.json_helpers import safe_json_list

_MARKET_IMPACT_BADGE_CLASS = {
    "LOW": "good",
    "MEDIUM": "warning",
    "HIGH": "serious",
    "VERY_HIGH": "critical",
}

_NEXT_STEP_BADGE_CLASS = {
    "IGNORE": "good",
    "WATCH": "warning",
    "RESEARCH": "critical",
}


def _field(label: str, value: str, escape: bool = True) -> str:
    # escape=False is only for values we built ourselves as trusted HTML
    # (the EDGAR link, badges) — everything from the filing/LLM must be escaped,
    # since filing text and free-form LLM output can contain '<', '>', '&'.
    safe_value = html.escape(str(value)) if escape else value
    return f'<div class="detail-label">{label}</div><div class="detail-value">{safe_value}</div>'


def _chip_list(items: List[str], empty_text: str = "Ninguno detectado") -> str:
    if not items:
        return f'<p class="empty-note">{empty_text}</p>'
    chips = "".join(f'<span class="chip">{html.escape(str(item))}</span>' for item in items)
    return f'<div class="chip-list">{chips}</div>'


def _badge(text: Optional[str], css_class: str) -> str:
    if not text:
        return ""
    return f'<span class="badge badge-{css_class}"><span class="badge-dot"></span>{html.escape(str(text))}</span>'


def render_general_info(detail: Dict) -> None:
    with section_card("Información general", key="general-info"):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(_field("Empresa", detail.get("company_name") or "—"), unsafe_allow_html=True)
            st.markdown(_field("Ticker", detail.get("ticker") or "—"), unsafe_allow_html=True)
            st.markdown(_field("Formulario", detail.get("form_type") or "—"), unsafe_allow_html=True)
        with col2:
            st.markdown(_field("Fecha", format_yyyymmdd(detail.get("date_filed"))), unsafe_allow_html=True)
            st.markdown(_field("OTC Tier", detail.get("otc_tier") or "—"), unsafe_allow_html=True)
            url = detail.get("filing_url")
            link = f'<a href="{html.escape(url)}" target="_blank">Ver en EDGAR ↗</a>' if url else "—"
            st.markdown(_field("Documento original", link, escape=False), unsafe_allow_html=True)


def render_ai_analysis(detail: Dict) -> None:
    with section_card("Análisis IA", key="ai-analysis"):
        if not detail.get("primary_event_type"):
            st.markdown('<p class="empty-note">Este filing todavía no ha sido analizado por el LLM.</p>', unsafe_allow_html=True)
            return

        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown(_field("Evento principal", detail["primary_event_type"]), unsafe_allow_html=True)
            secondary = safe_json_list(detail.get("secondary_event_types_json"))
            st.markdown('<div class="detail-label">Eventos secundarios</div>', unsafe_allow_html=True)
            st.markdown(_chip_list(secondary, "Sin eventos secundarios"), unsafe_allow_html=True)
        with col2:
            st.markdown(_field("Importance score", format_score(detail.get("importance_score"))), unsafe_allow_html=True)
            impact = detail.get("market_impact")
            badge = _badge(impact, _MARKET_IMPACT_BADGE_CLASS.get(impact, "neutral"))
            st.markdown(f'<div class="detail-label">Market impact</div><div class="detail-value">{badge or "—"}</div>', unsafe_allow_html=True)
        with col3:
            next_step = detail.get("next_step")
            step_badge = _badge(next_step, _NEXT_STEP_BADGE_CLASS.get(next_step, "neutral"))
            st.markdown(f'<div class="detail-label">Next step</div><div class="detail-value">{step_badge or "—"}</div>', unsafe_allow_html=True)
            deep_research = "Sí" if detail.get("deep_research") else "No"
            st.markdown(_field("Deep research", deep_research), unsafe_allow_html=True)

        st.markdown(_field("Resumen", detail.get("summary") or "—"), unsafe_allow_html=True)
        st.markdown(_field("Razón de la puntuación", detail.get("reason_for_score") or "—"), unsafe_allow_html=True)


def render_structured_extraction(detail: Dict) -> None:
    with section_card("Información estructurada extraída", key="structured-extraction"):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown('<div class="detail-label">Items SEC</div>', unsafe_allow_html=True)
            st.markdown(_chip_list(safe_json_list(detail.get("items_json"))), unsafe_allow_html=True)

            st.markdown('<div class="detail-label">Importes</div>', unsafe_allow_html=True)
            st.markdown(_chip_list(safe_json_list(detail.get("money_json"))), unsafe_allow_html=True)

            st.markdown('<div class="detail-label">Fechas mencionadas</div>', unsafe_allow_html=True)
            st.markdown(_chip_list(safe_json_list(detail.get("dates_json"))), unsafe_allow_html=True)

            st.markdown('<div class="detail-label">Agreements</div>', unsafe_allow_html=True)
            st.markdown(_chip_list(safe_json_list(detail.get("agreements_json"))), unsafe_allow_html=True)
        with col2:
            st.markdown('<div class="detail-label">Empresas detectadas</div>', unsafe_allow_html=True)
            st.markdown(_chip_list(safe_json_list(detail.get("companies_json"))), unsafe_allow_html=True)

            st.markdown('<div class="detail-label">Personas detectadas</div>', unsafe_allow_html=True)
            st.markdown(_chip_list(safe_json_list(detail.get("people_json"))), unsafe_allow_html=True)

            st.markdown('<div class="detail-label">Palabras clave</div>', unsafe_allow_html=True)
            key_entities = safe_json_list(detail.get("key_entities_json"))
            st.markdown(_chip_list(key_entities, "Sin entidades clave"), unsafe_allow_html=True)


def render_evidence(detail: Dict) -> None:
    with section_card("Evidencias utilizadas por el modelo", key="evidence"):
        evidence = safe_json_list(detail.get("evidence_json"))
        if not evidence:
            st.markdown('<p class="empty-note">Sin evidencias registradas.</p>', unsafe_allow_html=True)
            return
        items_html = "".join(f"<li>{html.escape(str(item))}</li>" for item in evidence)
        st.markdown(f'<ul class="evidence-list">{items_html}</ul>', unsafe_allow_html=True)


def render_full_text(detail: Dict) -> None:
    with section_card("Texto completo del documento", key="full-text"):
        text = detail.get("clean_text")
        if not text:
            st.markdown('<p class="empty-note">No se descargó el texto completo de este filing.</p>', unsafe_allow_html=True)
            return

        total_chars = len(text)
        if total_chars > MAX_FULL_TEXT_CHARS:
            url = detail.get("filing_url")
            link = f'<a href="{html.escape(url)}" target="_blank">documento completo en EDGAR ↗</a>' if url else "EDGAR"
            st.markdown(
                f'<p class="empty-note">Mostrando los primeros {MAX_FULL_TEXT_CHARS:,} caracteres '
                f'de un documento de {total_chars:,} — consulta el {link} para el resto.</p>',
                unsafe_allow_html=True,
            )
            text = text[:MAX_FULL_TEXT_CHARS]

        with st.container(height=420):
            st.markdown(f'<div class="filing-text-box">{html.escape(text)}</div>', unsafe_allow_html=True)
