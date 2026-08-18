"""Procesar filings — triggers the existing backend pipeline (src/pipeline.py)
for a chosen date, so the ingestion commands normally run from a terminal
can be run from the browser instead.

This is the one page in the dashboard that writes to filings.db, and only
indirectly: it calls the same pipeline functions the CLI (src/main.py) calls,
nothing here re-implements any download/parsing/storage logic. Every other
page stays strictly read-only.
"""
import time
from datetime import date
from typing import Dict, List, Optional

import streamlit as st

from components.header import render_page_header
from components.kpi_card import render_kpi_row

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


def _run_pipeline(
    target_date: date,
    download_index: bool,
    download_content: bool,
    build_snapshots: bool,
    run_llm: bool,
) -> None:
    started = time.time()
    daily_result: Optional[DailyPipelineResult] = None
    snapshot_result: Optional[SnapshotPipelineResult] = None
    llm_result: Optional[LLMPipelineResult] = None
    llm_error: Optional[str] = None

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

        if run_llm:
            st.write("Ejecutando análisis LLM...")
            llm_progress = st.progress(0.0)
            llm_counter = st.empty()

            def _llm_progress(data: Dict) -> None:
                llm_progress.progress(data["done"] / max(data["total"], 1))
                llm_counter.write(f"⏳ Ejecutando análisis LLM: {data['done']} / {data['total']}")

            try:
                llm_result = run_llm_pipeline(on_progress=_llm_progress)
                llm_counter.write(f"✓ Análisis LLM completado ({llm_result.processed} filings)")
            except LLMConfigError as exc:
                llm_error = str(exc)
                st.warning(f"Análisis LLM no ejecutado: {exc}")

        status.update(label="Procesamiento completado", state="complete")

    _render_summary(daily_result, snapshot_result, llm_result, llm_error, time.time() - started)
    st.cache_data.clear()


def _render_summary(
    daily: Optional[DailyPipelineResult],
    snapshot: Optional[SnapshotPipelineResult],
    llm: Optional[LLMPipelineResult],
    llm_error: Optional[str],
    elapsed_seconds: float,
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

    if llm:
        cards.append({
            "label": "Análisis LLM", "value": str(llm.processed),
            "caption": f"{llm.errors} errores" if llm.errors else "sin errores",
            "accent": llm.errors > 0,
        })

    cards.append({"label": "Tiempo total", "value": f"{elapsed_seconds:.1f}s", "caption": "", "accent": False})
    render_kpi_row(cards)

    all_errors: List[str] = []
    if daily:
        all_errors.extend(daily.errors)
    if snapshot:
        all_errors.extend(snapshot.errors)
    if llm_error:
        all_errors.append(f"Análisis LLM: {llm_error}")

    if all_errors:
        st.warning(f"{len(all_errors)} documento(s) no pudieron procesarse correctamente.")
        with st.expander("Ver errores"):
            for error in all_errors:
                st.code(error, language=None)


def render() -> None:
    render_page_header(
        "Procesar filings",
        "Ejecuta el pipeline de ingesta y análisis existente sin salir del navegador",
    )

    st.warning(
        "Esta página escribe en `filings.db`: descarga filings reales de SEC EDGAR y "
        "ejecuta el pipeline ya existente. El resto del dashboard es de solo lectura."
    )

    target_date = st.date_input("Fecha a procesar", value=date.today())

    st.markdown('<div class="detail-label">OPCIONES DE PROCESAMIENTO</div>', unsafe_allow_html=True)
    download_index = st.checkbox("Descargar índice SEC", value=True)
    download_content = st.checkbox(
        "Descargar contenido de los documentos", value=True, disabled=not download_index,
    )
    build_snapshots = st.checkbox("Generar Document Snapshots", value=True)
    run_llm = st.checkbox("Ejecutar análisis LLM", value=True)
    st.caption(
        "Snapshots y análisis LLM procesan todo lo pendiente en la base de datos, "
        "no solo los filings de la fecha elegida arriba — igual que por terminal."
    )

    if st.button("Procesar filings", type="primary", width="stretch"):
        if not any([download_index, build_snapshots, run_llm]):
            st.error("Selecciona al menos una opción de procesamiento.")
        else:
            _run_pipeline(target_date, download_index, download_content, build_snapshots, run_llm)
