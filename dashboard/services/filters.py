"""Translates the Explorador de Eventos filter/sort UI state into a
parameterized SQL WHERE clause + ORDER BY — the only place that builds SQL
fragments from user input, so injection-safety lives in one spot.

Every value the user can type goes through `?` parameter binding. The only
thing ever string-interpolated is the ORDER BY column, and only after it's
been checked against SORT_OPTIONS (a fixed whitelist) — never the raw filter
values themselves.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

SORT_OPTIONS = {
    "Fecha": "f.date_filed",
    "Empresa": "f.company_name",
    "Ticker": "f.ticker",
    "Importance score": "l.importance_score",
}


@dataclass
class EventFilters:
    date_from: Optional[str] = None  # YYYYMMDD
    date_to: Optional[str] = None  # YYYYMMDD
    ticker: str = ""
    company_name: str = ""
    form_types: List[str] = field(default_factory=list)
    event_types: List[str] = field(default_factory=list)
    min_importance_score: int = 0
    deep_research_only: bool = False
    otc_tiers: List[str] = field(default_factory=list)


def build_where_clause(filters: EventFilters) -> Tuple[str, List]:
    """Returns (sql_fragment, params) for everything after 'WHERE f.filing_category = ?'."""
    clauses: List[str] = []
    params: List = []

    if filters.date_from:
        clauses.append("f.date_filed >= ?")
        params.append(filters.date_from)
    if filters.date_to:
        clauses.append("f.date_filed <= ?")
        params.append(filters.date_to)
    if filters.ticker:
        clauses.append("f.ticker LIKE ?")
        params.append(f"%{filters.ticker.strip().upper()}%")
    if filters.company_name:
        clauses.append("f.company_name LIKE ?")
        params.append(f"%{filters.company_name.strip()}%")
    if filters.form_types:
        placeholders = ",".join("?" for _ in filters.form_types)
        clauses.append(f"f.form_type IN ({placeholders})")
        params.extend(filters.form_types)
    if filters.event_types:
        placeholders = ",".join("?" for _ in filters.event_types)
        clauses.append(f"l.primary_event_type IN ({placeholders})")
        params.extend(filters.event_types)
    if filters.min_importance_score > 0:
        clauses.append("l.importance_score >= ?")
        params.append(filters.min_importance_score)
    if filters.deep_research_only:
        clauses.append("l.deep_research = 1")
    if filters.otc_tiers:
        placeholders = ",".join("?" for _ in filters.otc_tiers)
        clauses.append(f"f.otc_tier IN ({placeholders})")
        params.extend(filters.otc_tiers)

    return (" AND " + " AND ".join(clauses)) if clauses else "", params


def resolve_order_by(sort_label: str, ascending: bool) -> str:
    column = SORT_OPTIONS.get(sort_label, SORT_OPTIONS["Fecha"])
    direction = "ASC" if ascending else "DESC"
    # NULLS LAST keeps un-analyzed filings from dominating a descending score sort.
    return f"{column} {direction} NULLS LAST" if column.startswith("l.") else f"{column} {direction}"
