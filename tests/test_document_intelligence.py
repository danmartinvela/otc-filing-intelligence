import src.document_intelligence.extractor as extractor
from src.database.db import (
    get_connection,
    get_filing_snapshot,
    get_filings_needing_snapshot,
    init_db,
    insert_filing_snapshot,
    insert_filings,
)
from src.database.models import Filing
from src.document_intelligence.extractor import (
    create_document_snapshot,
    extract_agreements,
    extract_companies,
    extract_dates,
    extract_items,
    extract_keywords,
    extract_money,
    extract_people,
    extract_percentages,
)
from src.document_intelligence.models import DocumentSnapshot


# ── extract_items ────────────────────────────────────────────────────────────


def test_extract_items_finds_multiple_codes():
    text = "Item 2.01 Completion of Acquisition. Item 5.02 Departure of Directors."
    assert extract_items(text) == ["2.01", "5.02"]


def test_extract_items_case_insensitive():
    assert extract_items("ITEM 8.01 Other Events.") == ["8.01"]


def test_extract_items_deduplicates():
    text = "Item 2.01 appears here. Later Item 2.01 is referenced again."
    assert extract_items(text) == ["2.01"]


def test_extract_items_no_match_returns_empty():
    assert extract_items("No item references in this text.") == []


def test_extract_items_empty_text():
    assert extract_items("") == []


# ── extract_money ────────────────────────────────────────────────────────────


def test_extract_money_dollar_amount():
    assert extract_money("The fee was $10,000 total.") == ["$10,000"]


def test_extract_money_dollar_million():
    assert extract_money("It raised $10 million in the offering.") == ["$10 million"]


def test_extract_money_usd_million():
    assert extract_money("Valued at USD 20 million.") == ["USD 20 million"]


def test_extract_money_dollar_billion_decimal():
    assert extract_money("Total assets were $3.4 billion.") == ["$3.4 billion"]


def test_extract_money_dollars_word():
    assert extract_money("Sold for 500,000 dollars.") == ["500,000 dollars"]


def test_extract_money_normalizes_stray_space_after_dollar_sign():
    assert extract_money("Transferred $ 42,000,000 today.") == ["$42,000,000"]


def test_extract_money_deduplicates():
    text = "Price was $10,000 and later confirmed at $10,000 again."
    assert extract_money(text) == ["$10,000"]


def test_extract_money_no_match_returns_empty():
    assert extract_money("No monetary figures here.") == []


def test_extract_money_filters_bare_short_table_noise():
    """Bare 1-3 digit '$N' with no comma/decimal/scale word is table-collapse noise, not a real amount."""
    text = "$8 $26 $994 USD 000"
    assert extract_money(text) == []


def test_extract_money_keeps_two_decimal_per_share_amounts():
    assert extract_money("Diluted earnings per share of $0.31.") == ["$0.31"]


def test_extract_money_keeps_bare_amount_with_enough_digits():
    assert extract_money("The fee totaled $1500 flat.") == ["$1500"]


def test_extract_money_filters_bare_long_identifier_like_numbers():
    """A bare (uncomma'd) 10-digit run after 'USD' is far more likely a CIK/ID than an amount."""
    assert extract_money("Entity Central Index Key USD 0001000230") == []


# ── extract_percentages ──────────────────────────────────────────────────────


def test_extract_percentages_basic():
    assert extract_percentages("The stake is 51% of the company.") == ["51%"]


def test_extract_percentages_decimal():
    assert extract_percentages("A 9.99% ownership stake.") == ["9.99%"]


def test_extract_percentages_with_space_before_sign():
    assert extract_percentages("Completed 100 % of the work.") == ["100%"]


def test_extract_percentages_multiple():
    assert extract_percentages("Went from 9.99% to 51% ownership.") == ["9.99%", "51%"]


def test_extract_percentages_no_match_returns_empty():
    assert extract_percentages("No percentages mentioned.") == []


# ── extract_dates ────────────────────────────────────────────────────────────


def test_extract_dates_month_name_format():
    assert extract_dates("Effective June 5, 2026 the merger closed.") == ["June 5, 2026"]


def test_extract_dates_iso_format():
    assert extract_dates("Filed on 2024-05-15.") == ["2024-05-15"]


def test_extract_dates_us_slash_format():
    assert extract_dates("Date: 03/15/2024.") == ["03/15/2024"]


def test_extract_dates_multiple_formats_in_one_text():
    text = "Signed May 3, 2024, filed 2024-05-15, recorded 03/16/2024."
    assert extract_dates(text) == ["May 3, 2024", "2024-05-15", "03/16/2024"]


def test_extract_dates_no_match_returns_empty():
    assert extract_dates("No dates in this sentence.") == []


def test_extract_dates_ignores_accession_number_substring():
    """An accession number like 0001437749-24-019781 must not yield a fake ISO date."""
    text = "See filing 0001437749-24-019781 for details."
    assert extract_dates(text) == []


def test_extract_dates_ignores_slash_date_embedded_in_longer_digits():
    text = "Reference number 1203/15/20241999 is not a date."
    assert extract_dates(text) == []


# ── extract_agreements ───────────────────────────────────────────────────────


def test_extract_agreements_basic():
    text = "The parties executed an Asset Purchase Agreement dated June 1."
    assert extract_agreements(text) == ["Asset Purchase Agreement"]


def test_extract_agreements_case_insensitive_canonicalized():
    text = "pursuant to the credit agreement entered into by the company"
    assert extract_agreements(text) == ["Credit Agreement"]


def test_extract_agreements_prefers_longest_match():
    text = "This MATERIAL DEFINITIVE AGREEMENT supersedes prior arrangements."
    assert extract_agreements(text) == ["Material Definitive Agreement"]


def test_extract_agreements_multiple_types():
    text = "They signed a Merger Agreement and later a Loan Agreement."
    assert extract_agreements(text) == ["Merger Agreement", "Loan Agreement"]


def test_extract_agreements_no_match_returns_empty():
    assert extract_agreements("No contracts mentioned here.") == []


# ── extract_keywords ──────────────────────────────────────────────────────────


def test_extract_keywords_detects_known_terms():
    text = "The company filed for Chapter 11 bankruptcy amid a going concern warning."
    keywords = extract_keywords(text)
    assert "Chapter 11" in keywords
    assert "Bankruptcy" in keywords
    assert "Going Concern" in keywords


def test_extract_keywords_exchange_terms():
    text = "The stock was delisted from Nasdaq and now trades on OTCQB."
    keywords = extract_keywords(text)
    assert "Delisted" in keywords
    assert "Nasdaq" in keywords
    assert "OTCQB" in keywords


def test_extract_keywords_no_match_returns_empty():
    assert extract_keywords("Nothing notable in this sentence.") == []


def test_extract_keywords_deduplicates():
    text = "Dividend announced. Later, another dividend was confirmed."
    assert extract_keywords(text) == ["Dividend"]


# ── extract_companies ────────────────────────────────────────────────────────


def test_extract_companies_regex_fallback_detects_suffixed_names(monkeypatch):
    monkeypatch.setattr(extractor, "_load_spacy_model", lambda: None)
    text = "ABC Holdings Inc. announced a deal with XYZ Corporation today."
    assert extract_companies(text) == ["ABC Holdings Inc.", "XYZ Corporation"]


def test_extract_companies_regex_ignores_generic_the_company(monkeypatch):
    monkeypatch.setattr(extractor, "_load_spacy_model", lambda: None)
    text = "The Company entered into an agreement with Acme Ltd."
    assert extract_companies(text) == ["Acme Ltd."]


def test_extract_companies_regex_no_match_returns_empty(monkeypatch):
    monkeypatch.setattr(extractor, "_load_spacy_model", lambda: None)
    assert extract_companies("No company names in this sentence.") == []


def test_extract_companies_regex_blocks_sec_boilerplate_phrases(monkeypatch):
    monkeypatch.setattr(extractor, "_load_spacy_model", lambda: None)
    text = "Emerging Growth Company [ ]  Smaller Reporting Company [ ]"
    assert extract_companies(text) == []


def test_extract_companies_regex_does_not_bridge_paragraph_breaks(monkeypatch):
    """A match must never glue words from separate lines/paragraphs together."""
    monkeypatch.setattr(extractor, "_load_spacy_model", lambda: None)
    text = "Stock Compensation\n\nThe Company adopted a new plan."
    assert extract_companies(text) == []


def test_extract_companies_uses_spacy_when_available(monkeypatch):
    fake_nlp = _make_fake_nlp([("ABC Holdings", "ORG"), ("John Smith", "PERSON")])
    monkeypatch.setattr(extractor, "_load_spacy_model", lambda: fake_nlp)
    assert extract_companies("irrelevant text, spaCy is mocked") == ["ABC Holdings"]


# ── extract_people ───────────────────────────────────────────────────────────


def test_extract_people_returns_empty_when_spacy_unavailable(monkeypatch):
    monkeypatch.setattr(extractor, "_load_spacy_model", lambda: None)
    assert extract_people("John Smith resigned as CEO.") == []


def test_extract_people_uses_spacy_when_available(monkeypatch):
    fake_nlp = _make_fake_nlp([("John Smith", "PERSON"), ("Mary Johnson", "PERSON")])
    monkeypatch.setattr(extractor, "_load_spacy_model", lambda: fake_nlp)
    assert extract_people("irrelevant text, spaCy is mocked") == ["John Smith", "Mary Johnson"]


def test_extract_people_empty_text_never_calls_spacy(monkeypatch):
    monkeypatch.setattr(extractor, "_load_spacy_model", lambda: (_ for _ in ()).throw(AssertionError("should not load spaCy for empty text")))
    assert extract_people("") == []


# ── spaCy test helpers ───────────────────────────────────────────────────────


class _FakeEnt:
    def __init__(self, text, label):
        self.text = text
        self.label_ = label


class _FakeDoc:
    def __init__(self, ents):
        self.ents = ents


class _FakeNLP:
    def __init__(self, ents):
        self._ents = ents

    def __call__(self, text):
        return _FakeDoc(self._ents)


def _make_fake_nlp(entities):
    return _FakeNLP([_FakeEnt(text, label) for text, label in entities])


# ── create_document_snapshot ─────────────────────────────────────────────────

_SAMPLE_FILING_TEXT = """
Item 2.01 Completion of Acquisition or Disposition of Assets.

On June 5, 2026, ABC Holdings Inc. completed its acquisition of XYZ Corp
pursuant to the Asset Purchase Agreement. The purchase price was $42,000,000,
representing approximately 51% of outstanding shares.

Item 5.02 Departure of Directors or Certain Officers.

The Company also entered into a new Credit Agreement.
"""


def test_create_document_snapshot_from_dict(monkeypatch):
    monkeypatch.setattr(extractor, "_load_spacy_model", lambda: None)
    filing = {
        "filename": "0001234567-24-000012.htm",
        "form_type": "8-K",
        "clean_text": _SAMPLE_FILING_TEXT,
    }
    snapshot = create_document_snapshot(filing)

    assert isinstance(snapshot, DocumentSnapshot)
    assert snapshot.filename == "0001234567-24-000012.htm"
    assert snapshot.form_type == "8-K"
    assert snapshot.items == ["2.01", "5.02"]
    assert snapshot.sections == ["Item 2.01", "Item 5.02"]
    assert "$42,000,000" in snapshot.money
    assert "51%" in snapshot.percentages
    assert "June 5, 2026" in snapshot.dates
    assert "Asset Purchase Agreement" in snapshot.agreements
    assert "Credit Agreement" in snapshot.agreements
    assert "ABC Holdings Inc." in snapshot.companies


def test_create_document_snapshot_from_filing_dataclass(monkeypatch):
    monkeypatch.setattr(extractor, "_load_spacy_model", lambda: None)
    filing = Filing(
        cik="320193",
        company_name="Test Corp",
        form_type="8-K",
        date_filed="2024-05-15",
        filename="edgar/data/320193/file1.txt",
        filing_url="https://www.sec.gov/Archives/edgar/data/320193/file1.txt",
        clean_text=_SAMPLE_FILING_TEXT,
    )
    snapshot = create_document_snapshot(filing)
    assert snapshot.items == ["2.01", "5.02"]


def test_create_document_snapshot_no_clean_text_returns_empty_lists():
    filing = {"filename": "empty.htm", "form_type": "8-K", "clean_text": None}
    snapshot = create_document_snapshot(filing)
    assert snapshot.items == []
    assert snapshot.money == []
    assert snapshot.companies == []


# ── SQLite persistence ────────────────────────────────────────────────────────


def _make_filing(filename: str, clean_text: str = "", form_type: str = "8-K") -> Filing:
    return Filing(
        cik="320193",
        company_name="Test Corp",
        form_type=form_type,
        date_filed="2024-05-15",
        filename=filename,
        filing_url=f"https://www.sec.gov/Archives/{filename}",
        clean_text=clean_text,
    )


def test_init_db_creates_filing_snapshots_table(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    with get_connection(db_path) as conn:
        tables = [
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
    assert "filing_snapshots" in tables


def test_get_filings_needing_snapshot_excludes_blank_clean_text(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    insert_filings(
        [
            _make_filing("edgar/data/1/a.txt", clean_text="Item 2.01 details here."),
            _make_filing("edgar/data/1/b.txt", clean_text=""),
        ],
        db_path,
    )
    pending = get_filings_needing_snapshot(db_path)
    assert len(pending) == 1
    assert pending[0]["filename"] == "edgar/data/1/a.txt"


def test_get_filings_needing_snapshot_excludes_context_and_ignored(tmp_path):
    """Only EVENT filings are eligible for a snapshot — CONTEXT and IGNORED never are."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    insert_filings(
        [
            _make_filing("edgar/data/1/event.txt", clean_text="Item 2.01 details.", form_type="8-K"),
            _make_filing("edgar/data/1/context.txt", clean_text="Annual report body.", form_type="10-K"),
            _make_filing("edgar/data/1/ignored.txt", clean_text="Insider trade.", form_type="4"),
        ],
        db_path,
    )
    pending = get_filings_needing_snapshot(db_path)
    assert [p["filename"] for p in pending] == ["edgar/data/1/event.txt"]


def test_insert_filing_snapshot_and_read_back(tmp_path, monkeypatch):
    monkeypatch.setattr(extractor, "_load_spacy_model", lambda: None)
    db_path = tmp_path / "test.db"
    init_db(db_path)
    filing = {
        "filename": "edgar/data/1/a.txt",
        "form_type": "8-K",
        "clean_text": _SAMPLE_FILING_TEXT,
    }
    snapshot = create_document_snapshot(filing)

    assert insert_filing_snapshot(snapshot, db_path) is True

    stored = get_filing_snapshot("edgar/data/1/a.txt", db_path)
    assert stored is not None
    assert stored["items"] == ["2.01", "5.02"]
    assert stored["sections"] == ["Item 2.01", "Item 5.02"]
    assert "$42,000,000" in stored["money"]


def test_insert_filing_snapshot_does_not_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(extractor, "_load_spacy_model", lambda: None)
    db_path = tmp_path / "test.db"
    init_db(db_path)
    snapshot = DocumentSnapshot(filename="edgar/data/1/a.txt", form_type="8-K", items=["2.01"])

    assert insert_filing_snapshot(snapshot, db_path) is True
    assert insert_filing_snapshot(snapshot, db_path) is False

    with get_connection(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM filing_snapshots").fetchone()[0]
    assert count == 1


def test_get_filing_snapshot_not_found_returns_none(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    assert get_filing_snapshot("does/not/exist.txt", db_path) is None
