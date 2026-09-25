"""Standalone script for streamlit.testing.v1.AppTest: exercises
screens.filing_detail._render_search in isolation, against a small
in-memory sqlite3 connection instead of the real production database.

Not a page of the real app — only used by
dashboard/tests/test_filing_detail_search.py. Rebuilds the fixture DB fresh
on every AppTest.run() (module-level code re-executes on each simulated
rerun, same as a real Streamlit script) — deterministic since the fixture
data never changes.
"""
import sqlite3
import sys
from pathlib import Path

_DASHBOARD_ROOT = Path(__file__).resolve().parents[2]
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

from screens import filing_detail

_FILINGS_SCHEMA = """
CREATE TABLE filings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL UNIQUE,
    date_filed TEXT NOT NULL,
    ticker TEXT,
    company_name TEXT,
    form_type TEXT,
    filing_category TEXT
)
"""

_conn = sqlite3.connect(":memory:")
_conn.row_factory = sqlite3.Row
_conn.execute(_FILINGS_SCHEMA)
_conn.execute(
    "INSERT INTO filings (filename, date_filed, ticker, company_name, form_type, filing_category) "
    "VALUES ('edgar/aapl_1.txt', '20260831', 'AAPL', 'APPLE INC.', '8-K', 'EVENT')"
)
_conn.commit()

filing_detail._render_search(_conn)
