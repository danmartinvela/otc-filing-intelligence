import logging
import sqlite3
from pathlib import Path
from typing import List

from .models import Filing

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "filings.db"

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS filings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    cik         TEXT NOT NULL,
    company_name TEXT NOT NULL,
    form_type   TEXT NOT NULL,
    date_filed  TEXT NOT NULL,
    filename    TEXT NOT NULL UNIQUE,
    filing_url  TEXT NOT NULL,
    raw_text    TEXT,
    clean_text  TEXT,
    created_at  TEXT NOT NULL
)
"""


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(db_path) as conn:
        conn.execute(_CREATE_TABLE_SQL)
    logger.debug(f"Database ready at {db_path}")


def insert_filings(
    filings: List[Filing], db_path: Path = DB_PATH
) -> tuple[int, int]:
    """Insert filings, ignoring duplicates by filename. Returns (inserted, skipped)."""
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
    return inserted, skipped


def update_filing_content(
    filename: str, raw_text: str, clean_text: str, db_path: Path = DB_PATH
) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE filings SET raw_text = ?, clean_text = ? WHERE filename = ?",
            (raw_text, clean_text, filename),
        )
