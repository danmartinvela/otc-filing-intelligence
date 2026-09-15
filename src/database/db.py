import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

from .models import Filing
from ..filing_routing.routing import EVENT, get_filing_category

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "filings.db"

_CREATE_FILINGS_SQL = """
CREATE TABLE IF NOT EXISTS filings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    cik          TEXT NOT NULL,
    company_name TEXT NOT NULL,
    form_type    TEXT NOT NULL,
    date_filed   TEXT NOT NULL,
    filename     TEXT NOT NULL UNIQUE,
    filing_url   TEXT NOT NULL,
    raw_text     TEXT,
    clean_text   TEXT,
    ticker       TEXT,
    exchange     TEXT,
    otc_tier     TEXT,
    sec_type     TEXT,
    country      TEXT,
    filing_category TEXT,
    created_at   TEXT NOT NULL
)
"""

# Dashboard "Detalle del Filing" on-demand search (ticker/company_name
# prefix, case-insensitive) needs these to stay fast — measured ~2.4s-6.5s
# for a specific ticker on a full scan vs. ~10ms with them. NOCASE (not
# plain BINARY) is what lets SQLite use the index for a case-insensitive
# LIKE 'prefix%' range scan; a plain index couldn't be used for that.
_CREATE_TICKER_NOCASE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_filings_ticker_nocase ON filings(ticker COLLATE NOCASE)"
)
_CREATE_COMPANY_NAME_NOCASE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_filings_company_name_nocase ON filings(company_name COLLATE NOCASE)"
)

_CREATE_COMPANIES_SQL = """
CREATE TABLE IF NOT EXISTS companies (
    cik          TEXT PRIMARY KEY,
    ticker       TEXT,
    company_name TEXT,
    exchange     TEXT,
    source       TEXT,
    updated_at   TEXT NOT NULL
)
"""

_CREATE_OTC_SECURITIES_SQL = """
CREATE TABLE IF NOT EXISTS otc_securities (
    symbol         TEXT PRIMARY KEY,
    security_name  TEXT,
    tier           TEXT,
    price          REAL,
    volume         INTEGER,
    sec_type       TEXT,
    country        TEXT,
    source         TEXT,
    updated_at     TEXT NOT NULL
)
"""

_CREATE_LLM_FILING_ANALYSIS_SQL = """
CREATE TABLE IF NOT EXISTS llm_filing_analysis (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    filing_filename            TEXT NOT NULL UNIQUE,
    provider                   TEXT,
    model                      TEXT,
    primary_event_type         TEXT,
    secondary_event_types_json TEXT,
    is_material                INTEGER,
    importance_score           INTEGER,
    market_impact              TEXT,
    deep_research              INTEGER,
    summary                    TEXT,
    key_entities_json          TEXT,
    evidence_json              TEXT,
    reason_for_score           TEXT,
    next_step                  TEXT,
    raw_response               TEXT,
    created_at                 TEXT NOT NULL
)
"""

# Columns added after the initial schema — applied via ALTER TABLE for existing DBs.
_FILINGS_OPTIONAL_COLUMNS: List[tuple] = [
    ("ticker", "TEXT"),
    ("exchange", "TEXT"),
    ("otc_tier", "TEXT"),
    ("sec_type", "TEXT"),
    ("country", "TEXT"),
    ("filing_category", "TEXT"),
]

_LLM_FILING_ANALYSIS_OPTIONAL_COLUMNS: List[tuple] = [
    ("market_impact", "TEXT"),
    ("next_step", "TEXT"),
    ("key_entities_json", "TEXT"),
]


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _add_missing_columns(
    conn: sqlite3.Connection, table: str, columns: List[tuple]
) -> None:
    """Safely add columns to an existing table if they are not already present."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for col_name, col_type in columns:
        if col_name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
            logger.debug(f"Migration: added '{col_name}' to '{table}'")


def _backfill_filing_categories(conn: sqlite3.Connection) -> None:
    """Assign filing_category to rows left NULL by a fresh ALTER TABLE.

    Batched by distinct form_type (a handful of values) rather than per row.
    """
    rows = conn.execute(
        "SELECT DISTINCT form_type FROM filings WHERE filing_category IS NULL"
    ).fetchall()
    for row in rows:
        form_type = row["form_type"]
        conn.execute(
            "UPDATE filings SET filing_category = ? "
            "WHERE form_type = ? AND filing_category IS NULL",
            (get_filing_category(form_type), form_type),
        )


def _auto_enrich_tickers(conn: sqlite3.Connection) -> None:
    """Batch-set ticker on filings that have a matching CIK in companies."""
    conn.execute(
        """
        UPDATE filings
        SET ticker = (
            SELECT c.ticker FROM companies c
            WHERE c.cik = printf('%010d', CAST(filings.cik AS INTEGER))
        )
        WHERE ticker IS NULL
          AND EXISTS (
            SELECT 1 FROM companies c
            WHERE c.cik = printf('%010d', CAST(filings.cik AS INTEGER))
          )
        """
    )


# ── Initialisation ────────────────────────────────────────────────────────────

def init_companies_table(db_path: Path = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute(_CREATE_COMPANIES_SQL)


def init_otc_securities_table(db_path: Path = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute(_CREATE_OTC_SECURITIES_SQL)


def init_llm_filing_analysis_table(db_path: Path = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute(_CREATE_LLM_FILING_ANALYSIS_SQL)
        _add_missing_columns(conn, "llm_filing_analysis", _LLM_FILING_ANALYSIS_OPTIONAL_COLUMNS)


def init_db(db_path: Path = DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(db_path) as conn:
        conn.execute(_CREATE_FILINGS_SQL)
        _add_missing_columns(conn, "filings", _FILINGS_OPTIONAL_COLUMNS)
        _backfill_filing_categories(conn)
        conn.execute(_CREATE_TICKER_NOCASE_INDEX_SQL)
        conn.execute(_CREATE_COMPANY_NAME_NOCASE_INDEX_SQL)
        conn.execute(_CREATE_COMPANIES_SQL)
        conn.execute(_CREATE_OTC_SECURITIES_SQL)
        conn.execute(_CREATE_LLM_FILING_ANALYSIS_SQL)
        _add_missing_columns(conn, "llm_filing_analysis", _LLM_FILING_ANALYSIS_OPTIONAL_COLUMNS)
    logger.debug(f"Database ready at {db_path}")


# ── Filings ───────────────────────────────────────────────────────────────────

def insert_filings(
    filings: List[Filing], db_path: Path = DB_PATH
) -> tuple[int, int]:
    """Insert filings, ignoring duplicates by filename. Returns (inserted, skipped).

    If the companies table has data, newly inserted filings are auto-enriched
    with their ticker before this function returns.
    """
    inserted = skipped = 0
    with get_connection(db_path) as conn:
        for filing in filings:
            try:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO filings
                        (cik, company_name, form_type, date_filed, filename,
                         filing_url, raw_text, clean_text, filing_category, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        filing.cik,
                        filing.company_name,
                        filing.form_type,
                        filing.date_filed,
                        filing.filename,
                        filing.filing_url,
                        filing.raw_text,
                        filing.clean_text,
                        get_filing_category(filing.form_type),
                        filing.created_at,
                    ),
                )
                if cursor.rowcount == 1:
                    inserted += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.error(f"Failed to insert filing '{filing.filename}': {e}")

        has_companies = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0] > 0
        if has_companies:
            _auto_enrich_tickers(conn)

    return inserted, skipped


def update_filing_content(
    filename: str, raw_text: str, clean_text: str, db_path: Path = DB_PATH
) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE filings SET raw_text = ?, clean_text = ? WHERE filename = ?",
            (raw_text, clean_text, filename),
        )


def get_filenames_with_content(filenames: List[str], db_path: Path = DB_PATH) -> Set[str]:
    """Return the subset of `filenames` that already have non-empty clean_text.

    Used to skip re-downloading filing content that was already fetched in a
    previous run of the same date — `filename` already has a unique index, so
    this is a lookup, not a table scan.
    """
    if not filenames:
        return set()
    placeholders = ",".join("?" for _ in filenames)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT filename FROM filings WHERE filename IN ({placeholders}) "
            "AND clean_text IS NOT NULL AND clean_text != ''",
            filenames,
        ).fetchall()
    return {row["filename"] for row in rows}


def enrich_filings_with_tickers(db_path: Path = DB_PATH) -> tuple[int, int]:
    """Batch-update filings.ticker from companies. Returns (enriched, still_missing)."""
    with get_connection(db_path) as conn:
        before = conn.execute(
            "SELECT COUNT(*) FROM filings WHERE ticker IS NULL"
        ).fetchone()[0]
        _auto_enrich_tickers(conn)
        after = conn.execute(
            "SELECT COUNT(*) FROM filings WHERE ticker IS NULL"
        ).fetchone()[0]
    enriched = before - after
    logger.info(f"Ticker enrichment: {enriched} updated, {after} still without ticker.")
    return enriched, after


# ── Companies ─────────────────────────────────────────────────────────────────

def upsert_companies(companies: List[Dict], db_path: Path = DB_PATH) -> int:
    """Insert or replace company records. Returns count processed."""
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        (c["cik"], c["ticker"], c["company_name"], c.get("source", ""), now)
        for c in companies
    ]
    with get_connection(db_path) as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO companies
                (cik, ticker, company_name, exchange, source, updated_at)
            VALUES (?, ?, ?, NULL, ?, ?)
            """,
            rows,
        )
    return len(rows)


def get_company_by_cik(cik: str, db_path: Path = DB_PATH) -> Optional[Dict]:
    """Return a company dict by its zero-padded CIK, or None if not found."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM companies WHERE cik = ?", (cik,)
        ).fetchone()
        return dict(row) if row else None


def get_all_companies(db_path: Path = DB_PATH) -> List[Dict]:
    """Return all rows from the companies table."""
    with get_connection(db_path) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM companies").fetchall()]


# ── OTC Securities ────────────────────────────────────────────────────────────

def upsert_otc_securities(
    securities: List[Dict], db_path: Path = DB_PATH
) -> tuple[int, int]:
    """Insert or replace OTC securities. Returns (inserted, updated)."""
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        existing = {
            r[0] for r in conn.execute("SELECT symbol FROM otc_securities")
        }
        inserted = updated = 0
        for s in securities:
            symbol = s.get("symbol", "")
            if not symbol:
                continue
            conn.execute(
                """
                INSERT OR REPLACE INTO otc_securities
                    (symbol, security_name, tier, price, volume,
                     sec_type, country, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    s.get("security_name"),
                    s.get("tier"),
                    s.get("price"),
                    s.get("volume"),
                    s.get("sec_type"),
                    s.get("country"),
                    s.get("source", ""),
                    now,
                ),
            )
            if symbol in existing:
                updated += 1
            else:
                inserted += 1
    return inserted, updated


def enrich_filings_with_otc(db_path: Path = DB_PATH) -> tuple[int, int]:
    """Set otc_tier, sec_type, country on filings matched by ticker → symbol.

    Returns (enriched, no_match) where no_match is filings with a ticker but
    no corresponding row in otc_securities.
    """
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE filings
            SET
                otc_tier = (
                    SELECT o.tier     FROM otc_securities o WHERE o.symbol = filings.ticker
                ),
                sec_type = (
                    SELECT o.sec_type FROM otc_securities o WHERE o.symbol = filings.ticker
                ),
                country  = (
                    SELECT o.country  FROM otc_securities o WHERE o.symbol = filings.ticker
                )
            WHERE ticker IS NOT NULL
              AND EXISTS (
                SELECT 1 FROM otc_securities o WHERE o.symbol = filings.ticker
              )
            """
        )
        enriched = cursor.rowcount
        no_match = conn.execute(
            "SELECT COUNT(*) FROM filings WHERE ticker IS NOT NULL AND otc_tier IS NULL"
        ).fetchone()[0]
    logger.info(f"OTC enrichment: {enriched} filings updated, {no_match} with no OTC match.")
    return enriched, no_match


# ── LLM first-pass analysis ──────────────────────────────────────────────────

def _llm_selection_where(
    category: str,
    date_from: Optional[str],
    date_to: Optional[str],
    form_types: Optional[List[str]],
    status: str,
) -> tuple:
    """Shared WHERE fragment for 'which filings are candidates for the LLM'.

    The one place that decides filing selection for LLM analysis — the
    pending-fetch, the counts, and the preview table all build on this, so
    they can never disagree about which rows match a given set of filters.

    status: "pending" (default, no llm_filing_analysis row yet), "analyzed"
    (already has one), or "all" (no status restriction).
    """
    clauses = ["f.filing_category = ?", "f.clean_text IS NOT NULL", "f.clean_text != ''"]
    params: List = [category]
    if date_from:
        clauses.append("f.date_filed >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("f.date_filed <= ?")
        params.append(date_to)
    if form_types:
        placeholders = ",".join("?" for _ in form_types)
        clauses.append(f"f.form_type IN ({placeholders})")
        params.extend(form_types)
    if status == "pending":
        clauses.append("f.filename NOT IN (SELECT filing_filename FROM llm_filing_analysis)")
    elif status == "analyzed":
        clauses.append("f.filename IN (SELECT filing_filename FROM llm_filing_analysis)")
    # status == "all": no extra clause
    return " AND ".join(clauses), params


def get_event_filings_needing_llm_analysis(
    limit: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    form_types: Optional[List[str]] = None,
    status: str = "pending",
    order: str = "recent",
    category: str = EVENT,
    db_path: Path = DB_PATH,
) -> List[Dict]:
    """Return EVENT filings with clean_text matching the given selection —
    defaults (no date/form_types, status="pending", order="recent") match
    the original behavior exactly, so `--llm-first-pass --limit N` alone is
    unchanged.

    The LLM works directly off f.clean_text — no other table feeds its input.
    """
    where, params = _llm_selection_where(category, date_from, date_to, form_types, status)
    direction = "ASC" if order == "oldest" else "DESC"
    query = f"""
        SELECT f.filename, f.company_name, f.ticker, f.form_type, f.filing_url, f.clean_text
        FROM filings f
        WHERE {where}
        ORDER BY f.date_filed {direction}
    """
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    with get_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_llm_selection_counts(
    category: str = EVENT,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    form_types: Optional[List[str]] = None,
    db_path: Path = DB_PATH,
) -> Dict[str, int]:
    """Counts for the 'before you spend money' preview: how many filings match
    period+category+form (any status), and how many of those are pending —
    independent of whatever status filter is currently selected in the UI.
    """
    with get_connection(db_path) as conn:
        where_all, params_all = _llm_selection_where(category, date_from, date_to, form_types, "all")
        total = conn.execute(f"SELECT COUNT(*) AS n FROM filings f WHERE {where_all}", params_all).fetchone()["n"]
        where_pending, params_pending = _llm_selection_where(category, date_from, date_to, form_types, "pending")
        pending = conn.execute(f"SELECT COUNT(*) AS n FROM filings f WHERE {where_pending}", params_pending).fetchone()["n"]
    return {"total_matching": total, "pending": pending}


def get_llm_selection_preview(
    category: str = EVENT,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    form_types: Optional[List[str]] = None,
    status: str = "pending",
    order: str = "recent",
    limit: Optional[int] = None,
    db_path: Path = DB_PATH,
) -> List[Dict]:
    """Lightweight rows for the preview table — same selection as
    get_event_filings_needing_llm_analysis but WITHOUT clean_text, so
    previewing never loads full document text just to display a row count.
    """
    where, params = _llm_selection_where(category, date_from, date_to, form_types, status)
    direction = "ASC" if order == "oldest" else "DESC"
    query = f"""
        SELECT f.filename, f.date_filed, f.company_name, f.ticker, f.form_type,
               LENGTH(f.clean_text) AS text_length,
               CASE WHEN l.filing_filename IS NULL THEN 0 ELSE 1 END AS is_analyzed
        FROM filings f
        LEFT JOIN llm_filing_analysis l ON l.filing_filename = f.filename
        WHERE {where}
        ORDER BY f.date_filed {direction}
    """
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    with get_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def insert_llm_filing_analysis(
    filing_filename: str,
    provider: str,
    model: str,
    parsed: Dict,
    raw_response: str,
    db_path: Path = DB_PATH,
) -> bool:
    """Persist an LLM first-pass result. Returns True if inserted, False if it already existed."""
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO llm_filing_analysis
                (filing_filename, provider, model, primary_event_type,
                 secondary_event_types_json, is_material, importance_score,
                 market_impact, deep_research, summary, key_entities_json,
                 evidence_json, reason_for_score, next_step, raw_response, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                filing_filename,
                provider,
                model,
                parsed.get("primary_event_type"),
                json.dumps(parsed.get("secondary_event_types") or []),
                int(bool(parsed.get("is_material", False))),
                parsed.get("importance_score"),
                parsed.get("market_impact"),
                int(bool(parsed.get("deep_research", False))),
                parsed.get("summary"),
                json.dumps(parsed.get("key_entities") or []),
                json.dumps(parsed.get("evidence") or []),
                parsed.get("reason_for_score"),
                parsed.get("next_step"),
                raw_response,
                now,
            ),
        )
        return cursor.rowcount == 1


def get_llm_filing_analysis(filename: str, db_path: Path = DB_PATH) -> Optional[Dict]:
    """Return a stored LLM analysis by filing filename, with JSON fields decoded, or None."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM llm_filing_analysis WHERE filing_filename = ?", (filename,)
        ).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["secondary_event_types"] = json.loads(data.pop("secondary_event_types_json"))
    data["evidence"] = json.loads(data.pop("evidence_json"))
    data["key_entities"] = json.loads(data.pop("key_entities_json") or "[]")
    return data
