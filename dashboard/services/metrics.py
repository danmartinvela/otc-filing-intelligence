"""Turns raw query results into ready-to-render view models.

Keeps pages/*.py free of arithmetic — a page fetches data and calls a service,
it never computes a percentage or picks a caption itself.
"""
from typing import Dict, List

from config import IMPORTANCE_THRESHOLD
from utils.formatting import compact_number, format_score


def build_kpi_cards(kpis: Dict) -> List[Dict]:
    """Build the ordered list of KPI card view models for the executive summary."""
    total = kpis["total_filings"]
    deep_research_pct = (
        (kpis["deep_research_count"] / kpis["analyzed_count"] * 100)
        if kpis["analyzed_count"]
        else None
    )
    avg_score = kpis["avg_importance_score"]

    return [
        {
            "label": "Filings procesados",
            "value": compact_number(total),
            "caption": f"{compact_number(kpis['event_count'])} EVENT · {compact_number(kpis['context_count'])} CONTEXT",
            "accent": False,
        },
        {
            "label": "Filings EVENT",
            "value": compact_number(kpis["event_count"]),
            "caption": f"{kpis['analyzed_count']} con análisis IA",
            "accent": False,
        },
        {
            "label": "Filings CONTEXT",
            "value": compact_number(kpis["context_count"]),
            "caption": "almacenados como contexto histórico",
            "accent": False,
        },
        {
            "label": "Deep research",
            "value": compact_number(kpis["deep_research_count"]),
            "caption": f"{deep_research_pct:.0f}% de los analizados" if deep_research_pct is not None else "sin datos",
            "accent": True,
        },
        {
            "label": "Importance score medio",
            "value": format_score(avg_score) if avg_score is not None else "—",
            "caption": "sobre 100",
            "accent": False,
        },
        {
            "label": f"Eventos importantes (≥{IMPORTANCE_THRESHOLD})",
            "value": compact_number(kpis["important_count"]),
            "caption": "candidatos a revisión prioritaria",
            "accent": True,
        },
    ]
