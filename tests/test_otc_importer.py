import sqlite3
from pathlib import Path

import pytest

from src.database.db import (
    enrich_filings_with_otc,
    get_connection,
    init_db,
    insert_filings,
    upsert_companies,
    upsert_otc_securities,
)
from src.database.models import Filing
from src.otcmarkets.otc_screener_importer import (
    _parse_row,
    _to_float,
    _to_int,
    import_otc_screener_csv,
)

_SAMPLE_CSV_CONTENT = """\
Symbol,Security Name,Tier,Price,Change %,Vol,Sec Type,Country,State
AAPL,Apple Inc.,Expert Market,150.25,1.23,1234567,Common Stock,United States,CA
TSLA,Tesla Inc.,OTCQB,200.50,-2.45,987654,Common Stock,United States,TX
MSFT,Microsoft Corp.,Expert Market,300.00,0.00,,Common Stock,United States,
"""


def _write_csv(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "screener.csv"
    p.write_text(content, encoding="utf-8")
    return p


# ── _to_float ─────────────────────────────────────────────────────────────────


def test_to_float_plain():
    assert _to_float("1.23") == pytest.approx(1.23)


def test_to_float_with_dollar():
    assert _to_float("$150.25") == pytest.approx(150.25)


def test_to_float_with_percent():
    assert _to_float("1.23%") == pytest.approx(1.23)


def test_to_float_negative():
    assert _to_float("-2.45%") == pytest.approx(-2.45)


def test_to_float_empty():
    assert _to_float("") is None


def test_to_float_non_numeric():
    assert _to_float("N/A") is None


# ── _to_int ───────────────────────────────────────────────────────────────────


def test_to_int_plain():
    assert _to_int("1234567") == 1234567


def test_to_int_with_commas():
    assert _to_int("1,234,567") == 1234567


def test_to_int_empty():
    assert _to_int("") is None


def test_to_int_non_numeric():
    assert _to_int("N/A") is None


# ── _parse_row ────────────────────────────────────────────────────────────────


def test_parse_row_normalizes_symbol():
    row = {"Symbol": "  aapl  ", "Security Name": "", "Tier": "", "Price": "",
           "Change %": "", "Vol": "", "Sec Type": "", "Country": "", "State": ""}
    result = _parse_row(row)
    assert result["symbol"] == "AAPL"


def test_parse_row_converts_numbers():
    row = {"Symbol": "X", "Security Name": "X Corp", "Tier": "OTCQB",
           "Price": "$10.50", "Change %": "-1.5%", "Vol": "100,000",
           "Sec Type": "Common Stock", "Country": "US", "State": ""}
    result = _parse_row(row)
    assert result["price"] == pytest.approx(10.50)
    assert result["volume"] == 100000
    assert "change_percent" not in result
    assert "state" not in result


def test_parse_row_empty_strings_become_none():
    row = {"Symbol": "X", "Security Name": "", "Tier": "", "Price": "",
           "Change %": "", "Vol": "", "Sec Type": "", "Country": "", "State": ""}
    result = _parse_row(row)
    assert result["security_name"] is None
    assert result["tier"] is None
    assert result["price"] is None
    assert result["volume"] is None
    assert result["sec_type"] is None
    assert result["country"] is None


# ── import_otc_screener_csv ───────────────────────────────────────────────────


def test_import_csv_inserts_rows(tmp_path):
    csv_path = _write_csv(tmp_path, _SAMPLE_CSV_CONTENT)
    db_path = tmp_path / "test.db"
    init_db(db_path)
    inserted, updated = import_otc_screener_csv(str(csv_path), db_path)
    assert inserted == 3
    assert updated == 0


def test_import_csv_updates_on_reimport(tmp_path):
    csv_path = _write_csv(tmp_path, _SAMPLE_CSV_CONTENT)
    db_path = tmp_path / "test.db"
    init_db(db_path)
    import_otc_screener_csv(str(csv_path), db_path)
    inserted, updated = import_otc_screener_csv(str(csv_path), db_path)
    assert inserted == 0
    assert updated == 3


def test_import_csv_file_not_found(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    with pytest.raises(FileNotFoundError):
        import_otc_screener_csv(str(tmp_path / "nonexistent.csv"), db_path)


def test_import_csv_data_persisted(tmp_path):
    csv_path = _write_csv(tmp_path, _SAMPLE_CSV_CONTENT)
    db_path = tmp_path / "test.db"
    init_db(db_path)
    import_otc_screener_csv(str(csv_path), db_path)
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM otc_securities WHERE symbol = 'AAPL'"
        ).fetchone()
    assert row is not None
    assert row["tier"] == "Expert Market"
    assert row["price"] == pytest.approx(150.25)
    assert row["country"] == "United States"


def test_import_csv_null_volume_for_empty(tmp_path):
    csv_path = _write_csv(tmp_path, _SAMPLE_CSV_CONTENT)
    db_path = tmp_path / "test.db"
    init_db(db_path)
    import_otc_screener_csv(str(csv_path), db_path)
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT volume FROM otc_securities WHERE symbol = 'MSFT'"
        ).fetchone()
    assert row["volume"] is None


# ── upsert_otc_securities ─────────────────────────────────────────────────────


def test_upsert_otc_securities_returns_counts(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    rows = [
        {"symbol": "AAA", "security_name": "A Corp", "tier": "OTCQB",
         "price": 1.0, "volume": 100,
         "sec_type": "Common Stock", "country": "US", "source": "test"},
    ]
    inserted, updated = upsert_otc_securities(rows, db_path)
    assert inserted == 1 and updated == 0
    inserted2, updated2 = upsert_otc_securities(rows, db_path)
    assert inserted2 == 0 and updated2 == 1


# ── enrich_filings_with_otc ───────────────────────────────────────────────────


def _make_filing(cik: str, ticker: str, filename: str) -> Filing:
    f = Filing(
        cik=cik,
        company_name="Test Corp",
        form_type="8-K",
        date_filed="2024-05-15",
        filename=filename,
        filing_url=f"https://www.sec.gov/Archives/{filename}",
    )
    return f


def test_enrich_with_otc_sets_fields(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)

    # Insert OTC security
    upsert_otc_securities(
        [{"symbol": "AAPL", "security_name": "Apple", "tier": "Expert Market",
          "price": 150.0, "volume": 1000,
          "sec_type": "Common Stock", "country": "United States",
          "source": "test"}],
        db_path,
    )
    # Insert filing then manually set its ticker (simulating prior SEC enrichment)
    insert_filings([_make_filing("320193", "AAPL", "edgar/data/320193/f1.txt")], db_path)
    with get_connection(db_path) as conn:
        conn.execute("UPDATE filings SET ticker = 'AAPL'")

    enriched, no_match = enrich_filings_with_otc(db_path)

    assert enriched == 1
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT otc_tier, sec_type, country FROM filings").fetchone()
    assert row["otc_tier"] == "Expert Market"
    assert row["sec_type"] == "Common Stock"
    assert row["country"] == "United States"


def test_enrich_with_otc_no_match_does_not_raise(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    insert_filings([_make_filing("999", "ZZZZ", "edgar/data/999/f1.txt")], db_path)
    with get_connection(db_path) as conn:
        conn.execute("UPDATE filings SET ticker = 'ZZZZ'")
    enriched, no_match = enrich_filings_with_otc(db_path)
    assert enriched == 0
    assert no_match == 1


def test_init_db_creates_otc_securities_table(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    with get_connection(db_path) as conn:
        tables = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        ]
    assert "otc_securities" in tables


def test_init_db_adds_otc_columns_to_existing_filings(tmp_path):
    """Upgrading an old DB without OTC columns adds them."""
    db_path = tmp_path / "test.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE filings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cik TEXT NOT NULL, company_name TEXT NOT NULL,
                form_type TEXT NOT NULL, date_filed TEXT NOT NULL,
                filename TEXT NOT NULL UNIQUE, filing_url TEXT NOT NULL,
                raw_text TEXT, clean_text TEXT, created_at TEXT NOT NULL
            )"""
        )
    init_db(db_path)
    with get_connection(db_path) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(filings)")}
    assert "otc_tier" in cols
    assert "sec_type" in cols
    assert "country" in cols
