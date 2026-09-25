import html
import time
from datetime import date
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

from components.header import render_page_header
from components.kpi_card import render_kpi_row
from database.connection import get_connection
from database.queries import get_filter_options
from utils.formatting import compact_number, date_to_yyyymmdd, format_yyyymmdd

from src.database.db import get_llm_selection_counts, get_llm_selection_preview
from src.llm_analysis.client import LLMConfigError
from src.pipeline import (
    DailyPipelineResult,
    DEFAULT_LLM_WORKERS,
    LLMPipelineResult,
    get_user_agent,
    run_daily_pipeline,
    run_llm_pipeline,
)

_STATUS_OPTIONS = {"Solo pendientes de análisis": "pending", "Ya analizados": "analyzed", "Todos": "all"}
_ORDER_OPTIONS = {"Más recientes primero": "recent", "Más antiguos primero": "oldest"}
_PREVIEW_DISPLAY_CAP = 200


def _format_bytes(value: float) -> str:
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.1f} MB"
    if value >= 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value:.0f} B"



def _run_ingest(target_date: date, download_index: bool, download_content: bool) -> None:
    started = time.time()
    daily_result: Optional[DailyPipelineResult] = None

    with st.status("Procesando...", expanded=True) as status:
        if download_index:
            st.write(f"**Procesando {target_date.strftime('%d/%m/%Y')}**")
            user_agent = get_user_agent()
            content_progress = None
            content_stats = None
            content_failed = 0

            def _daily_progress(data: Dict) -> None:
                nonlocal content_progress, content_stats, content_failed
                stage = data["stage"]
                if stage == "index":
                    st.write(
                        f"✓ Índice SEC descargado — {data['total_found']} documentos encontrados "
                        f"({data['event_count']} EVENT, {data['context_count']} CONTEXT)"
                    )
                elif stage == "store":
                    st.write(
                        f"✓ Guardado en SQLite — {data['inserted']} nuevos, "
                        f"{data['already_existed']} ya existían"
                    )
                elif stage == "content":
                    if content_progress is None:
                        st.write("Descargando contenido de los documentos...")
                        content_progress = st.progress(0.0)
                        content_stats = st.empty()
                    if not data["ok"]:
                        content_failed += 1
                    content_progress.progress(data["done"] / data["total"])
                    if data.get("skipped"):
                        content_stats.caption(f"[{data['done']}/{data['total']}] {data['label']} — ya tenía contenido, omitido")
                    else:
                        content_stats.caption(
                            f"[{data['done']}/{data['total']}] {data['label']} — "
                            f"{_format_bytes(data['bytes_downloaded'])} descargados · "
                            f"{_format_bytes(data['bytes_stored'])} guardados · "
                            f"{_format_bytes(data['speed_bytes_per_sec'])}/s · "
                            f"ETA {data['eta_seconds']:.0f}s"
                        )
                    if data["done"] == data["total"]:
                        st.write(f"✓ Contenido descargado ({data['total'] - content_failed} de {data['total']})")

            try:
                daily_result = run_daily_pipeline(
                    target_date, user_agent, download_content=download_content, on_progress=_daily_progress
                )
            except FileNotFoundError:
                st.warning(f"No hay índice SEC publicado para el {target_date.strftime('%d/%m/%Y')}.")
            except Exception as exc:
                st.error(f"Error al descargar el índice SEC: {exc}")

        status.update(label="Ingesta completada", state="complete")

    _render_ingest_summary(daily_result, time.time() - started)
    st.cache_data.clear()


def _render_ingest_summary(daily: Optional[DailyPipelineResult], elapsed_seconds: float) -> None:
    cards: List[Dict] = []

    if daily:
        cards.append({
            "label": "Documentos encontrados", "value": str(daily.total_found),
            "caption": f"{daily.event_count} EVENT · {daily.context_count} CONTEXT", "accent": False,
        })
        cards.append({
            "label": "Insertados", "value": str(daily.inserted),
            "caption": f"{daily.already_existed} ya existían", "accent": False,
        })
        if daily.content_downloaded or daily.content_failed or daily.content_skipped_existing:
            reduction_pct = (
                (1 - daily.content_bytes_stored / daily.content_bytes_downloaded) * 100
                if daily.content_bytes_downloaded else 0.0
            )
            cards.append({
                "label": "Contenido descargado", "value": str(daily.content_downloaded),
                "caption": f"{_format_bytes(daily.content_bytes_downloaded)} transferidos", "accent": False,
            })
            cards.append({
                "label": "Almacenado (solo doc. principal)", "value": _format_bytes(daily.content_bytes_stored),
                "caption": f"{reduction_pct:.0f}% menos que lo transferido" if daily.content_bytes_downloaded else "",
                "accent": False,
            })
            cards.append({
                "label": "Omitidos", "value": str(daily.content_skipped_existing),
                "caption": "ya tenían contenido", "accent": False,
            })
            if daily.content_failed:
                cards.append({
                    "label": "Fallos de descarga", "value": str(daily.content_failed),
                    "caption": "", "accent": True,
                })

    cards.append({"label": "Tiempo total", "value": f"{elapsed_seconds:.1f}s", "caption": "", "accent": False})
    render_kpi_row(cards)

    all_errors: List[str] = []
    if daily:
        all_errors.extend(daily.errors)

    if all_errors:
        st.warning(f"{len(all_errors)} documento(s) no pudieron procesarse correctamente.")
        with st.expander("Ver errores"):
            for error in all_errors:
                st.code(error, language=None)


def _render_ingest_section() -> None:
    st.markdown("### A. Ingesta SEC")

    target_date = st.date_input("Fecha a procesar", value=date.today(), key="ingest_date")

    st.markdown('<div class="detail-label">OPCIONES DE PROCESAMIENTO</div>', unsafe_allow_html=True)
    download_index = st.checkbox("Descargar índice SEC", value=True, key="ingest_download_index")
    download_content = st.checkbox(
        "Descargar contenido de los documentos", value=True,
        disabled=not download_index, key="ingest_download_content",
    )

    if st.button("Ejecutar ingesta", type="primary", width="stretch"):
        if not download_index:
            st.error("Selecciona al menos una opción de ingesta.")
        else:
            _run_ingest(target_date, download_index, download_content)



def _resolve_period() -> Tuple[Optional[str], Optional[str], str]:
    period = st.radio(
        "Periodo", ["Día concreto", "Rango de fechas"],
        key="llm_period", horizontal=True,
    )
    if period == "Rango de fechas":
        col_from, col_to = st.columns(2)
        with col_from:
            d_from = st.date_input("Desde", value=date.today(), key="llm_range_from")
        with col_to:
            d_to = st.date_input("Hasta", value=date.today(), key="llm_range_to")
        label = f"{d_from.strftime('%d/%m/%Y')} – {d_to.strftime('%d/%m/%Y')}"
        return date_to_yyyymmdd(d_from), date_to_yyyymmdd(d_to), label

    d = st.date_input("Fecha", value=date.today(), key="llm_single_date")
    return date_to_yyyymmdd(d), date_to_yyyymmdd(d), d.strftime("%d/%m/%Y")


def _matching_count_for_status(counts: Dict[str, int], status: str) -> int:
    if status == "pending":
        return counts["pending"]
    if status == "analyzed":
        return counts["total_matching"] - counts["pending"]
    return counts["total_matching"]


def _render_llm_preview(
    period_label: str, status_label: str, limit: Optional[int],
    date_from: Optional[str], date_to: Optional[str], form_types: Optional[List[str]],
    status: str, order: str,
) -> int:
    counts = get_llm_selection_counts(date_from=date_from, date_to=date_to, form_types=form_types)
    matching = _matching_count_for_status(counts, status)
    to_send = matching if limit is None else min(matching, limit)

    st.markdown('<div class="section-title">Documentos seleccionados</div>', unsafe_allow_html=True)
    render_kpi_row([
        {"label": "Periodo", "value": period_label, "caption": "", "accent": False},
        {"label": "Filings encontrados", "value": compact_number(counts["total_matching"]), "caption": "", "accent": False},
        {"label": "Pendientes de IA", "value": compact_number(counts["pending"]), "caption": "", "accent": False},
        {"label": "Límite seleccionado", "value": compact_number(limit) if limit is not None else "Sin límite", "caption": "", "accent": False},
        {"label": "Se enviarán al LLM", "value": compact_number(to_send), "caption": "", "accent": True},
    ])

    if to_send > 0:
        preview_rows = get_llm_selection_preview(
            date_from=date_from, date_to=date_to, form_types=form_types,
            status=status, order=order,
            limit=min(to_send, _PREVIEW_DISPLAY_CAP),
        )
        df = pd.DataFrame(preview_rows)
        df["date_filed"] = df["date_filed"].map(format_yyyymmdd)
        df["text_length"] = df["text_length"].map(compact_number)
        df["is_analyzed"] = df["is_analyzed"].map(lambda v: "Analizado" if v else "Pendiente")
        df = df.rename(columns={
            "date_filed": "Fecha", "company_name": "Empresa", "ticker": "Ticker",
            "form_type": "Formulario", "text_length": "Tamaño texto", "is_analyzed": "Estado LLM",
        })
        st.dataframe(
            df[["Fecha", "Empresa", "Ticker", "Formulario", "Tamaño texto", "Estado LLM"]],
            width="stretch", hide_index=True, height=min(300, 40 + 35 * len(df)),
        )
        if to_send > _PREVIEW_DISPLAY_CAP:
            st.caption(f"Mostrando los primeros {_PREVIEW_DISPLAY_CAP} de {to_send} que se enviarían.")
    else:
        st.markdown('<p class="empty-note">Ningún filing coincide con estos filtros.</p>', unsafe_allow_html=True)

    return to_send


_LLM_PROGRESS_HEADER = (
    '<div class="llm-progress-header">'
    '<div class="llm-progress-cell">Progreso</div>'
    '<div class="llm-progress-cell">Empresa</div>'
    '<div class="llm-progress-cell">Evento</div>'
    '<div class="llm-progress-cell llm-progress-cell--score">Score</div>'
    '</div>'
)


def _llm_progress_row_html(data: Dict) -> str:
    done, total = data["done"], data["total"]
    progress_txt = f"{'✓' if data['ok'] else '✗'} {done}/{total}"

    if data["ok"]:
        company = html.escape(str(data.get("company_name") or "—"))
        event = html.escape(str(data.get("primary_event_type") or "—"))
        score = data.get("importance_score")
        score_txt = html.escape(str(score)) if score is not None else "—"
        row_class = "llm-progress-row"
    else:
        company = "—"
        event = html.escape(f"Error: {data['error']}")
        score_txt = "—"
        row_class = "llm-progress-row is-error"

    return (
        f'<div class="{row_class}">'
        f'<div class="llm-progress-cell">{html.escape(progress_txt)}</div>'
        f'<div class="llm-progress-cell" title="{company}">{company}</div>'
        f'<div class="llm-progress-cell" title="{event}">{event}</div>'
        f'<div class="llm-progress-cell llm-progress-cell--score">{score_txt}</div>'
        f'</div>'
    )


def _run_llm_analysis(
    date_from: Optional[str], date_to: Optional[str], form_types: Optional[List[str]],
    status: str, order: str, limit: Optional[int], workers: int,
) -> None:
    started = time.time()
    deep_research_count = 0

    with st.status("Analizando con IA...", expanded=True) as status_box:
        progress = st.progress(0.0)
        stats_placeholder = st.empty()
        log_lines: List[str] = []
        log_placeholder = st.empty()

        def _on_progress(data: Dict) -> None:
            nonlocal deep_research_count
            done, total = data["done"], data["total"]
            progress.progress(done / max(total, 1))
            eta = data.get("eta_seconds", 0.0)
            stats_placeholder.caption(
                f"Workers: {data.get('workers', workers)} · "
                f"Velocidad: {data.get('speed_per_min', 0.0):.1f} filings/min · "
                f"ETA: {int(eta // 60)}m {int(eta % 60):02d}s"
            )
            if data["ok"] and data.get("deep_research"):
                deep_research_count += 1
            log_lines.append(_llm_progress_row_html(data))
            log_placeholder.markdown(
                f'<div class="llm-progress-table">{_LLM_PROGRESS_HEADER}{"".join(log_lines[-20:])}</div>',
                unsafe_allow_html=True,
            )

        try:
            llm_result = run_llm_pipeline(
                limit=limit, date_from=date_from, date_to=date_to, form_types=form_types,
                status=status, order=order, workers=workers, on_progress=_on_progress,
            )
        except LLMConfigError as exc:
            status_box.update(label="Análisis LLM no ejecutado", state="error")
            st.warning(f"Análisis LLM no ejecutado: {exc}")
            return

        status_box.update(label="Análisis completado", state="complete")

    elapsed = time.time() - started
    total_attempted = llm_result.processed + llm_result.errors
    render_kpi_row([
        {"label": "Procesados", "value": str(total_attempted), "caption": "", "accent": False},
        {"label": "Correctos", "value": str(llm_result.processed), "caption": "", "accent": False},
        {"label": "Errores", "value": str(llm_result.errors), "caption": "", "accent": llm_result.errors > 0},
        {"label": "Deep Research", "value": str(deep_research_count), "caption": "", "accent": deep_research_count > 0},
        {"label": "Tiempo total", "value": f"{elapsed:.1f}s", "caption": "", "accent": False},
    ])
    st.cache_data.clear()


def _render_llm_section(conn) -> None:
    st.markdown("### B. Análisis mediante LLM")
    st.caption("Solo se envían al modelo los documentos que cumplan estos filtros")

    date_from, date_to, period_label = _resolve_period()

    filter_options = get_filter_options(conn)
    form_types = st.multiselect(
        "Tipo de formulario", options=filter_options.get("form_types", []),
        placeholder="Todos los formularios EVENT", key="llm_form_types",
    ) or None

    col_limit, col_status, col_order, col_workers = st.columns(4)
    with col_limit:
        max_filings = st.number_input(
            "Máximo de filings a analizar", min_value=0, value=25, step=1,
            key="llm_max_filings",
        )
        limit = None if max_filings == 0 else int(max_filings)
    with col_status:
        status_label = st.selectbox("Estado de análisis", list(_STATUS_OPTIONS.keys()), key="llm_status_label")
        status = _STATUS_OPTIONS[status_label]
    with col_order:
        order_label = st.selectbox("Priorizar por", list(_ORDER_OPTIONS.keys()), key="llm_order_label")
        order = _ORDER_OPTIONS[order_label]
    with col_workers:
        workers = int(st.number_input(
            "Workers", min_value=1, max_value=16,
            value=DEFAULT_LLM_WORKERS, step=1, key="llm_workers",
        ))

    to_send = _render_llm_preview(
        period_label, status_label, limit, date_from, date_to, form_types, status, order,
    )

    if st.button(
        f"Analizar {to_send} filing{'s' if to_send != 1 else ''} con IA" if to_send else "Analizar con IA",
        type="primary", width="stretch", disabled=to_send == 0,
    ):
        _run_llm_analysis(date_from, date_to, form_types, status, order, limit, workers)


def render() -> None:
    render_page_header(
        "Procesar filings",
        "Ejecuta el pipeline de ingesta y análisis existente sin salir del navegador",
    )

    conn = get_connection()

    _render_ingest_section()
    st.divider()
    _render_llm_section(conn)
