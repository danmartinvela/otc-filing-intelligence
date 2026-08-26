"""Procesar filings — triggers the existing backend pipeline (src/pipeline.py).

Two independent blocks, each with its own state and its own button:
  A. Ingesta SEC — fetch/store/download for one date (as before).
  B. Análisis mediante LLM — explicit filter selection (period, form types,
     status, limit, order) with a preview (counts + table) before spending
     any LLM credits. The filters go straight to src.database.db /
     src.llm_analysis.first_pass — the same functions the CLI's
     --llm-first-pass uses — so nothing here re-implements selection logic,
     and the two blocks never share state (choosing a date in A never
     affects what B sends to the model).

This is the one page in the dashboard that writes to filings.db, and only
indirectly: it calls the same pipeline functions the CLI (src/main.py) calls.
Every other page stays strictly read-only.
"""
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
    LLMPipelineResult,
    SnapshotPipelineResult,
    get_user_agent,
    run_daily_pipeline,
    run_llm_pipeline,
    run_snapshot_pipeline,
)

_STATUS_OPTIONS = {"Solo pendientes de análisis": "pending", "Ya analizados": "analyzed", "Todos": "all"}
_ORDER_OPTIONS = {"Más recientes primero": "recent", "Más antiguos primero": "oldest"}
_PREVIEW_DISPLAY_CAP = 200  # display cap only — never limits what actually gets sent


# ── A. Ingesta SEC ────────────────────────────────────────────────────────────

def _run_ingest(
    target_date: date, download_index: bool, download_content: bool, build_snapshots: bool
) -> None:
    started = time.time()
    daily_result: Optional[DailyPipelineResult] = None
    snapshot_result: Optional[SnapshotPipelineResult] = None

    with st.status("Procesando...", expanded=True) as status:
        if download_index:
            st.write(f"**Procesando {target_date.strftime('%d/%m/%Y')}**")
            user_agent = get_user_agent()
            content_progress = None
            content_failed = 0

            def _daily_progress(data: Dict) -> None:
                nonlocal content_progress, content_failed
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
                    if not data["ok"]:
                        content_failed += 1
                    content_progress.progress(data["done"] / data["total"])
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

        if build_snapshots:
            st.write("Generando Document Snapshots...")
            snap_progress = st.progress(0.0)

            def _snapshot_progress(data: Dict) -> None:
                snap_progress.progress(data["done"] / max(data["total"], 1))

            snapshot_result = run_snapshot_pipeline(on_progress=_snapshot_progress)
            st.write(f"✓ {snapshot_result.built} snapshots generados")

        status.update(label="Ingesta completada", state="complete")

    _render_ingest_summary(daily_result, snapshot_result, time.time() - started)
    st.cache_data.clear()


def _render_ingest_summary(
    daily: Optional[DailyPipelineResult], snapshot: Optional[SnapshotPipelineResult], elapsed_seconds: float
) -> None:
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
        if daily.content_downloaded or daily.content_failed:
            cards.append({
                "label": "Contenido descargado", "value": str(daily.content_downloaded),
                "caption": f"{daily.content_failed} fallos" if daily.content_failed else "sin fallos",
                "accent": daily.content_failed > 0,
            })

    if snapshot:
        cards.append({
            "label": "Snapshots generados", "value": str(snapshot.built),
            "caption": f"{len(snapshot.errors)} errores" if snapshot.errors else "sin errores",
            "accent": bool(snapshot.errors),
        })

    cards.append({"label": "Tiempo total", "value": f"{elapsed_seconds:.1f}s", "caption": "", "accent": False})
    render_kpi_row(cards)

    all_errors: List[str] = []
    if daily:
        all_errors.extend(daily.errors)
    if snapshot:
        all_errors.extend(snapshot.errors)

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
    build_snapshots = st.checkbox("Generar Document Snapshots", value=True, key="ingest_build_snapshots")
    st.caption(
        "Snapshots procesa todo lo pendiente en la base de datos, no solo los "
        "filings de la fecha elegida arriba — igual que por terminal."
    )

    if st.button("Ejecutar ingesta", type="primary", width="stretch"):
        if not any([download_index, build_snapshots]):
            st.error("Selecciona al menos una opción de ingesta.")
        else:
            _run_ingest(target_date, download_index, download_content, build_snapshots)


# ── B. Análisis mediante LLM ──────────────────────────────────────────────────

def _resolve_period() -> Tuple[Optional[str], Optional[str], str]:
    """Returns (date_from, date_to, display_label) — dates in YYYYMMDD or None."""
    period = st.radio(
        "Periodo", ["Día concreto", "Rango de fechas", "Todos los pendientes"],
        key="llm_period", horizontal=True,
    )
    if period == "Día concreto":
        d = st.date_input("Fecha", value=date.today(), key="llm_single_date")
        return date_to_yyyymmdd(d), date_to_yyyymmdd(d), d.strftime("%d/%m/%Y")
    if period == "Rango de fechas":
        col_from, col_to = st.columns(2)
        with col_from:
            d_from = st.date_input("Desde", value=date.today(), key="llm_range_from")
        with col_to:
            d_to = st.date_input("Hasta", value=date.today(), key="llm_range_to")
        label = f"{d_from.strftime('%d/%m/%Y')} – {d_to.strftime('%d/%m/%Y')}"
        return date_to_yyyymmdd(d_from), date_to_yyyymmdd(d_to), label
    return None, None, "Todo el histórico (sin filtro de fecha)"


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
    """Renders the 'before you spend money' preview. Returns how many filings
    would actually be sent to the LLM with the current filters."""
    counts = get_llm_selection_counts(date_from=date_from, date_to=date_to, form_types=form_types)
    matching = _matching_count_for_status(counts, status)
    to_send = matching if limit is None else min(matching, limit)

    st.markdown('<div class="section-title">Documentos seleccionados</div>', unsafe_allow_html=True)
    render_kpi_row([
        {"label": "Periodo", "value": period_label, "caption": status_label, "accent": False},
        {"label": "Filings encontrados", "value": compact_number(counts["total_matching"]), "caption": "categoría EVENT, este periodo/formulario", "accent": False},
        {"label": "Pendientes de IA", "value": compact_number(counts["pending"]), "caption": "sin análisis todavía", "accent": False},
        {"label": "Límite seleccionado", "value": compact_number(limit) if limit is not None else "Sin límite", "caption": "", "accent": False},
        {"label": "Se enviarán al LLM", "value": compact_number(to_send), "caption": "con los filtros actuales", "accent": True},
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


def _run_llm_analysis(
    date_from: Optional[str], date_to: Optional[str], form_types: Optional[List[str]],
    status: str, order: str, limit: Optional[int],
) -> None:
    started = time.time()
    deep_research_count = 0

    with st.status("Analizando con IA...", expanded=True) as status_box:
        progress = st.progress(0.0)
        log_lines: List[str] = []
        log_placeholder = st.empty()

        def _on_progress(data: Dict) -> None:
            nonlocal deep_research_count
            done, total = data["done"], data["total"]
            progress.progress(done / max(total, 1))
            if data["ok"]:
                if data.get("deep_research"):
                    deep_research_count += 1
                score = data.get("importance_score")
                score_txt = f"{score}" if score is not None else "—"
                log_lines.append(
                    f"✓ {done}/{total}  {data.get('company_name') or '—':<30} "
                    f"{data.get('primary_event_type') or '—':<25} {score_txt}"
                )
            else:
                log_lines.append(f"✗ {done}/{total}  Error: {data['error']}")
            log_placeholder.code("\n".join(log_lines[-20:]), language=None)

        try:
            llm_result = run_llm_pipeline(
                limit=limit, date_from=date_from, date_to=date_to, form_types=form_types,
                status=status, order=order, on_progress=_on_progress,
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
    st.caption(
        "Solo se envían al modelo los documentos que cumplan estos filtros — "
        "nada se procesa automáticamente. Categoría: **EVENT** (fijo por ahora)."
    )

    date_from, date_to, period_label = _resolve_period()

    filter_options = get_filter_options(conn)
    form_types = st.multiselect(
        "Tipo de formulario", options=filter_options.get("form_types", []),
        placeholder="Todos los formularios EVENT", key="llm_form_types",
    ) or None

    col_limit, col_status, col_order = st.columns(3)
    with col_limit:
        max_filings = st.number_input(
            "Máximo de filings a analizar", min_value=0, value=25, step=1,
            help="0 = sin límite", key="llm_max_filings",
        )
        limit = None if max_filings == 0 else int(max_filings)
    with col_status:
        status_label = st.selectbox("Estado de análisis", list(_STATUS_OPTIONS.keys()), key="llm_status_label")
        status = _STATUS_OPTIONS[status_label]
    with col_order:
        order_label = st.selectbox("Priorizar por", list(_ORDER_OPTIONS.keys()), key="llm_order_label")
        order = _ORDER_OPTIONS[order_label]

    to_send = _render_llm_preview(
        period_label, status_label, limit, date_from, date_to, form_types, status, order,
    )

    if st.button(
        f"Analizar {to_send} filing{'s' if to_send != 1 else ''} con IA" if to_send else "Analizar con IA",
        type="primary", width="stretch", disabled=to_send == 0,
    ):
        _run_llm_analysis(date_from, date_to, form_types, status, order, limit)


def render() -> None:
    render_page_header(
        "Procesar filings",
        "Ejecuta el pipeline de ingesta y análisis existente sin salir del navegador",
    )

    st.warning(
        "Esta página escribe en `filings.db`: descarga filings reales de SEC EDGAR y "
        "ejecuta el pipeline ya existente. El resto del dashboard es de solo lectura."
    )

    conn = get_connection()

    _render_ingest_section()
    st.divider()
    _render_llm_section(conn)
