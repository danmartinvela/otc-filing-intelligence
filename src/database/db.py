import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .models import Filing

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
    created_at   TEXT NOT NULL
)
"""

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

# Columns added after the initial schema — applied via ALTER TABLE for existing DBs.
_FILINGS_OPTIONAL_COLUMNS: List[tuple] = [
    ("ticker", "TEXT"),
    ("exchange", "TEXT"),
    ("otc_tier", "TEXT"),
    ("sec_type", "TEXT"),
    ("country", "TEXT"),
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


def init_db(db_path: Path = DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(db_path) as conn:
        conn.execute(_CREATE_FILINGS_SQL)
        _add_missing_columns(conn, "filings", _FILINGS_OPTIONAL_COLUMNS)
        conn.execute(_CREATE_COMPANIES_SQL)
        conn.execute(_CREATE_OTC_SECURITIES_SQL)
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
                         filing_url, raw_text, clean_text, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
