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
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    ticker: str = ""
    form_types: List[str] = field(default_factory=list)


def build_where_clause(filters: EventFilters) -> Tuple[str, List]:
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
    if filters.form_types:
        placeholders = ",".join("?" for _ in filters.form_types)
        clauses.append(f"f.form_type IN ({placeholders})")
        params.extend(filters.form_types)

    return (" AND " + " AND ".join(clauses)) if clauses else "", params


def resolve_order_by(sort_label: str, ascending: bool) -> str:
    column = SORT_OPTIONS.get(sort_label, SORT_OPTIONS["Fecha"])
    direction = "ASC" if ascending else "DESC"
    return f"{column} {direction} NULLS LAST" if column.startswith("l.") else f"{column} {direction}"
