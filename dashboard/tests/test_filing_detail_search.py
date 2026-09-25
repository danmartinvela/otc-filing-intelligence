import sqlite3
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from database.queries import search_event_filings

_HARNESS = str(Path(__file__).resolve().parent / "apps" / "filing_detail_search_harness.py")

_FILINGS_SCHEMA = """
CREATE TABLE filings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL UNIQUE,
    date_filed TEXT NOT NULL,
    ticker TEXT,
    company_name TEXT,
    form_type TEXT,
    filing_category TEXT,
    raw_text TEXT,
    clean_text TEXT
)
"""


def _build_db(tmp_path) -> sqlite3.Connection:
    db_path = tmp_path / "search_test.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(_FILINGS_SCHEMA)
    rows = [
        ("edgar/aapl_1.txt", "20260831", "AAPL", "APPLE INC.", "8-K", "EVENT"),
        ("edgar/aapl_2.txt", "20260825", "AAPL", "APPLE INC.", "424B5", "EVENT"),
        ("edgar/aapl_3.txt", "20260812", "AAPL", "APPLE INC.", "8-K", "EVENT"),
        ("edgar/aapl_4.txt", "20260805", "AAPL", "APPLE INC.", "10-Q", "EVENT"),
        ("edgar/msft_1.txt", "20260830", "MSFT", "MICROSOFT CORP", "8-K", "EVENT"),
        ("edgar/aapl_ctx.txt", "20260829", "AAPL", "APPLE INC.", "10-K", "CONTEXT"),
    ]
    conn.executemany(
        "INSERT INTO filings (filename, date_filed, ticker, company_name, form_type, filing_category) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return conn




def test_search_matches_ticker_case_insensitively(tmp_path):
    conn = _build_db(tmp_path)
    for term in ("AAPL", "aapl", "Aapl"):
        df = search_event_filings(conn, term, limit=50)
        assert set(df["filename"]) == {"edgar/aapl_1.txt", "edgar/aapl_2.txt", "edgar/aapl_3.txt", "edgar/aapl_4.txt"}


def test_search_matches_company_name_prefix_case_insensitively(tmp_path):
    conn = _build_db(tmp_path)
    for term in ("APPLE", "apple", "appl"):
        df = search_event_filings(conn, term, limit=50)
        assert set(df["filename"]) == {"edgar/aapl_1.txt", "edgar/aapl_2.txt", "edgar/aapl_3.txt", "edgar/aapl_4.txt"}


def test_search_excludes_context_filings(tmp_path):
    conn = _build_db(tmp_path)
    df = search_event_filings(conn, "AAPL", limit=50)
    assert "edgar/aapl_ctx.txt" not in set(df["filename"])


def test_search_orders_by_date_filed_desc(tmp_path):
    conn = _build_db(tmp_path)
    df = search_event_filings(conn, "AAPL", limit=50)
    assert list(df["date_filed"]) == ["20260831", "20260825", "20260812", "20260805"]


def test_search_respects_limit(tmp_path):
    conn = _build_db(tmp_path)
    df = search_event_filings(conn, "AAPL", limit=2)
    assert len(df) == 2
    assert list(df["date_filed"]) == ["20260831", "20260825"]


def test_search_returns_only_lean_columns(tmp_path):
    conn = _build_db(tmp_path)
    df = search_event_filings(conn, "AAPL", limit=50)
    assert set(df.columns) == {"filename", "date_filed", "ticker", "company_name", "form_type", "filing_category"}
    assert "raw_text" not in df.columns
    assert "clean_text" not in df.columns


def test_search_no_match_returns_empty_dataframe(tmp_path):
    conn = _build_db(tmp_path)
    df = search_event_filings(conn, "ZZZZ", limit=50)
    assert df.empty




def _run() -> AppTest:
    at = AppTest.from_file(_HARNESS)
    at.run()
    assert not at.exception
    return at


def test_empty_search_field_runs_no_query_and_shows_no_results():
    with patch("screens.filing_detail.search_event_filings") as mock_search:
        at = _run()
        mock_search.assert_not_called()
        assert len(at.dataframe) == 0


def test_typing_a_term_runs_the_query_and_shows_results():
    at = _run()
    at.text_input[0].set_value("AAPL")
    at.run()
    assert len(at.dataframe) == 1


def test_search_term_persists_across_a_simulated_page_revisit():
    at = _run()
    at.text_input[0].set_value("AAPL")
    at.run()
    assert at.session_state["filing_detail_search_term"] == "AAPL"

    at.run()
    assert at.session_state["filing_detail_search_term"] == "AAPL"
    assert at.text_input[0].value == "AAPL"
