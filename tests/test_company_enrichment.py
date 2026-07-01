import sqlite3

import pytest

from src.company_enrichment.sec_company_tickers import (
    normalize_cik,
    parse_sec_company_tickers,
)
from src.database.db import (
    enrich_filings_with_tickers,
    get_all_companies,
    get_company_by_cik,
    get_connection,
    init_db,
    insert_filings,
    upsert_companies,
)
from src.database.models import Filing


# ── normalize_cik ─────────────────────────────────────────────────────────────


def test_normalize_cik_from_int():
    assert normalize_cik(320193) == "0000320193"


def test_normalize_cik_from_string():
    assert normalize_cik("320193") == "0000320193"


def test_normalize_cik_already_padded():
    assert normalize_cik("0000320193") == "0000320193"


def test_normalize_cik_single_digit():
    assert normalize_cik("1") == "0000000001"


def test_normalize_cik_max_digits():
    assert normalize_cik("9999999999") == "9999999999"


# ── parse_sec_company_tickers ─────────────────────────────────────────────────

_SAMPLE_DATA = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
}


def test_parse_returns_correct_count():
    assert len(parse_sec_company_tickers(_SAMPLE_DATA)) == 2


def test_parse_normalizes_cik():
    result = parse_sec_company_tickers(_SAMPLE_DATA)
    ciks = {r["cik"] for r in result}
    assert "0000320193" in ciks
    assert "0000789019" in ciks


def test_parse_fields_are_correct():
    result = parse_sec_company_tickers(_SAMPLE_DATA)
    aapl = next(r for r in result if r["ticker"] == "AAPL")
    assert aapl["company_name"] == "Apple Inc."
    assert aapl["source"] == "SEC company_tickers.json"


def test_parse_skips_malformed_entries():
    data = {
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"broken": "entry"},
    }
    assert len(parse_sec_company_tickers(data)) == 1


def test_parse_empty_data():
    assert parse_sec_company_tickers({}) == []


# ── companies table ───────────────────────────────────────────────────────────


def test_init_db_creates_companies_table(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    with get_connection(db_path) as conn:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        ]
    assert "companies" in tables


def test_upsert_and_get_company(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    upsert_companies(
        [{"cik": "0000320193", "ticker": "AAPL", "company_name": "Apple Inc.", "source": "test"}],
        db_path,
    )
    result = get_company_by_cik("0000320193", db_path)
    assert result is not None
    assert result["ticker"] == "AAPL"
    assert result["exchange"] is None


def test_upsert_replaces_existing(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    base = [{"cik": "0000320193", "ticker": "AAPL", "company_name": "Apple Inc.", "source": "test"}]
    upsert_companies(base, db_path)
    updated = [{"cik": "0000320193", "ticker": "AAPL2", "company_name": "Apple Inc. Updated", "source": "test"}]
    upsert_companies(updated, db_path)
    result = get_company_by_cik("0000320193", db_path)
    assert result["ticker"] == "AAPL2"


def test_get_company_by_cik_not_found(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    assert get_company_by_cik("0000000001", db_path) is None


def test_get_all_companies(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    upsert_companies(
        [
            {"cik": "0000320193", "ticker": "AAPL", "company_name": "Apple", "source": "test"},
            {"cik": "0000789019", "ticker": "MSFT", "company_name": "Microsoft", "source": "test"},
        ],
        db_path,
    )
    assert len(get_all_companies(db_path)) == 2


# ── ticker enrichment ─────────────────────────────────────────────────────────


def _make_filing(cik: str, filename: str) -> Filing:
    return Filing(
        cik=cik,
        company_name="Test Corp",
        form_type="8-K",
        date_filed="2024-05-15",
        filename=filename,
        filing_url=f"https://www.sec.gov/Archives/{filename}",
    )


def test_enrich_sets_ticker_when_match(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    upsert_companies(
        [{"cik": "0000320193", "ticker": "AAPL", "company_name": "Apple Inc.", "source": "test"}],
        db_path,
    )
    insert_filings([_make_filing("320193", "edgar/data/320193/file1.txt")], db_path)

    enriched, missing = enrich_filings_with_tickers(db_path)

    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT ticker FROM filings WHERE cik = '320193'"
        ).fetchone()
    assert row["ticker"] == "AAPL"
    # enrich_filings_with_tickers was called after insert auto-enriched; so enriched may be 0
    # but the ticker must be set regardless of which path set it
    assert row["ticker"] is not None


def test_enrich_handles_unpadded_cik(tmp_path):
    """CIK '320193' in filings must match company with CIK '0000320193'."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    upsert_companies(
        [{"cik": "0000320193", "ticker": "AAPL", "company_name": "Apple Inc.", "source": "test"}],
        db_path,
    )
    # Insert without companies present first to avoid auto-enrich, then test manual enrich
    with get_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO filings (cik, company_name, form_type, date_filed, filename,
               filing_url, created_at) VALUES ('320193','X','8-K','2024-01-01',
               'edgar/data/320193/f.txt','https://sec.gov/f.txt','2024-01-01')"""
        )
    enriched, _ = enrich_filings_with_tickers(db_path)
    assert enriched == 1
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT ticker FROM filings").fetchone()
    assert row["ticker"] == "AAPL"


def test_enrich_no_match_does_not_raise(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    insert_filings([_make_filing("9999999999", "edgar/data/9999/file1.txt")], db_path)
    enriched, missing = enrich_filings_with_tickers(db_path)
    assert enriched == 0
    assert missing == 1


def test_auto_enrich_on_insert(tmp_path):
    """Ticker is set automatically at insert time when companies table is populated."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    upsert_companies(
        [{"cik": "0000789019", "ticker": "MSFT", "company_name": "Microsoft", "source": "test"}],
        db_path,
    )
    insert_filings([_make_filing("789019", "edgar/data/789019/file1.txt")], db_path)

    with get_connection(db_path) as conn:
        row = conn.execute("SELECT ticker FROM filings WHERE cik = '789019'").fetchone()
    assert row["ticker"] == "MSFT"


# ── schema migration ──────────────────────────────────────────────────────────


def test_init_db_adds_ticker_exchange_to_existing_filings(tmp_path):
    """Upgrading an existing DB without ticker/exchange adds those columns."""
    db_path = tmp_path / "test.db"
    # Simulate a pre-enrichment DB (no ticker/exchange columns)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE filings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cik TEXT NOT NULL,
                company_name TEXT NOT NULL,
                form_type TEXT NOT NULL,
                date_filed TEXT NOT NULL,
                filename TEXT NOT NULL UNIQUE,
                filing_url TEXT NOT NULL,
                raw_text TEXT,
                clean_text TEXT,
                created_at TEXT NOT NULL
            )
            """
        )

    init_db(db_path)

    with get_connection(db_path) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(filings)")}
    assert "ticker" in cols
    assert "exchange" in cols
