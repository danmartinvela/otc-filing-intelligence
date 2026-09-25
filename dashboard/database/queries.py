import sqlite3
from typing import Dict, List, Optional, Sequence

import pandas as pd
import streamlit as st

from config import CACHE_TTL_SECONDS, IMPORTANCE_THRESHOLD, TOP_COMPANIES_LIMIT

_CACHE = st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)

_EVENT_LIST_COLUMNS = """
    f.filename, f.date_filed, f.company_name, f.ticker, f.form_type,
    l.primary_event_type, l.importance_score, l.deep_research
"""
_EVENT_LIST_FROM = """
    FROM filings f
    LEFT JOIN llm_filing_analysis l ON l.filing_filename = f.filename
    WHERE f.filing_category = 'EVENT'
"""



@_CACHE
def get_last_updated(_conn: sqlite3.Connection) -> Optional[str]:
    row = _conn.execute("SELECT MAX(created_at) AS ts FROM filings").fetchone()
    return row["ts"] if row else None


@_CACHE
def get_summary_kpis(_conn: sqlite3.Connection) -> Dict:
    totals = _conn.execute(
        """
        SELECT
            COUNT(*) AS total_filings,
            SUM(CASE WHEN filing_category = 'EVENT' THEN 1 ELSE 0 END) AS event_count,
            SUM(CASE WHEN filing_category = 'CONTEXT' THEN 1 ELSE 0 END) AS context_count
        FROM filings
        """
    ).fetchone()
    analysis = _conn.execute(
        """
        SELECT
            COUNT(*) AS analyzed_count,
            SUM(CASE WHEN deep_research = 1 THEN 1 ELSE 0 END) AS deep_research_count,
            AVG(importance_score) AS avg_importance_score,
            SUM(CASE WHEN importance_score >= ? THEN 1 ELSE 0 END) AS important_count
        FROM llm_filing_analysis
        """,
        (IMPORTANCE_THRESHOLD,),
    ).fetchone()
    return {
        "total_filings": totals["total_filings"] or 0,
        "event_count": totals["event_count"] or 0,
        "context_count": totals["context_count"] or 0,
        "analyzed_count": analysis["analyzed_count"] or 0,
        "deep_research_count": analysis["deep_research_count"] or 0,
        "avg_importance_score": analysis["avg_importance_score"],
        "important_count": analysis["important_count"] or 0,
    }


@_CACHE
def get_category_counts(_conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT filing_category, COUNT(*) AS count
        FROM filings
        WHERE filing_category IS NOT NULL
        GROUP BY filing_category
        ORDER BY count DESC
        """,
        _conn,
    )


@_CACHE
def get_daily_filing_counts(_conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT date_filed, filing_category, COUNT(*) AS count
        FROM filings
        WHERE filing_category IN ('EVENT', 'CONTEXT')
        GROUP BY date_filed, filing_category
        ORDER BY date_filed ASC
        """,
        _conn,
    )


@_CACHE
def get_importance_score_distribution(_conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT importance_score FROM llm_filing_analysis WHERE importance_score IS NOT NULL",
        _conn,
    )



@_CACHE
def get_form_type_counts(_conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT form_type, COUNT(*) AS count
        FROM filings
        WHERE filing_category = 'EVENT'
        GROUP BY form_type
        ORDER BY count ASC
        """,
        _conn,
    )


@_CACHE
def get_monthly_event_counts(_conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT substr(date_filed, 1, 6) AS year_month, COUNT(*) AS count
        FROM filings
        WHERE filing_category = 'EVENT'
        GROUP BY year_month
        ORDER BY year_month ASC
        """,
        _conn,
    )


@_CACHE
def get_top_companies(_conn: sqlite3.Connection, limit: int = TOP_COMPANIES_LIMIT) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT company_name, COUNT(*) AS count
        FROM filings
        WHERE filing_category = 'EVENT' AND company_name IS NOT NULL AND company_name != ''
        GROUP BY company_name
        ORDER BY count DESC
        LIMIT ?
        """,
        _conn,
        params=(limit,),
    )


@_CACHE
def get_deep_research_ratio(_conn: sqlite3.Connection) -> Dict[str, int]:
    row = _conn.execute(
        "SELECT COUNT(*) AS total, SUM(CASE WHEN deep_research = 1 THEN 1 ELSE 0 END) AS deep_research "
        "FROM llm_filing_analysis"
    ).fetchone()
    return {"total": row["total"] or 0, "deep_research": row["deep_research"] or 0}



@_CACHE
def get_event_date_bounds(_conn: sqlite3.Connection) -> Dict[str, Optional[str]]:
    row = _conn.execute(
        "SELECT MIN(date_filed) AS min_date, MAX(date_filed) AS max_date "
        "FROM filings WHERE filing_category = 'EVENT'"
    ).fetchone()
    return {"min_date": row["min_date"], "max_date": row["max_date"]}


@_CACHE
def get_filter_options(_conn: sqlite3.Connection) -> Dict[str, List[str]]:
    form_types = _conn.execute(
        "SELECT DISTINCT form_type FROM filings WHERE filing_category = 'EVENT' "
        "AND form_type IS NOT NULL ORDER BY form_type"
    ).fetchall()
    return {
        "form_types": [r["form_type"] for r in form_types],
    }


@_CACHE
def get_events_count(_conn: sqlite3.Connection, where_extra: str, params: Sequence) -> int:
    sql = f"SELECT COUNT(*) AS n {_EVENT_LIST_FROM}{where_extra}"
    return _conn.execute(sql, tuple(params)).fetchone()["n"]


@_CACHE
def search_event_filings(_conn: sqlite3.Connection, term: str, limit: int) -> pd.DataFrame:
    pattern = f"{term.strip()}%"
    return pd.read_sql_query(
        """
        SELECT filename, date_filed, ticker, company_name, form_type, filing_category
        FROM filings
        WHERE filing_category = 'EVENT'
          AND (ticker LIKE ? OR company_name LIKE ?)
        ORDER BY date_filed DESC
        LIMIT ?
        """,
        _conn,
        params=(pattern, pattern, limit),
    )


@_CACHE
def get_filing_detail(_conn: sqlite3.Connection, filename: str) -> Optional[Dict]:
    row = _conn.execute(
        """
        SELECT
            f.filename, f.company_name, f.ticker, f.form_type, f.date_filed,
            f.filing_url, f.clean_text, f.sec_type, f.country,
            l.primary_event_type, l.secondary_event_types_json, l.is_material,
            l.importance_score, l.market_impact, l.deep_research, l.summary,
            l.key_entities_json, l.evidence_json, l.reason_for_score, l.next_step
        FROM filings f
        LEFT JOIN llm_filing_analysis l ON l.filing_filename = f.filename
        WHERE f.filename = ?
        """,
        (filename,),
    ).fetchone()
    return dict(row) if row else None


@_CACHE
def get_events_page(
    _conn: sqlite3.Connection,
    where_extra: str,
    params: Sequence,
    order_by: str,
    limit: int,
    offset: int,
) -> pd.DataFrame:
    sql = f"SELECT {_EVENT_LIST_COLUMNS} {_EVENT_LIST_FROM}{where_extra} ORDER BY {order_by} LIMIT ? OFFSET ?"
    return pd.read_sql_query(sql, _conn, params=(*params, limit, offset))
