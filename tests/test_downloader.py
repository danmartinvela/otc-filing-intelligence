import logging
from unittest.mock import Mock, patch

import pytest
import requests
from bs4.exceptions import ParserRejectedMarkup

from src.sec_ingestion.downloader import (
    _is_html,
    _strip_tags_regex,
    clean_filing_text,
    extract_primary_document,
    fetch_and_clean,
    fetch_raw,
)


def test_is_html_detects_html_tags():
    assert _is_html("<html><body>Hello</body></html>")
    assert _is_html("<div class='foo'>content</div>")
    assert _is_html("<p>A paragraph</p>")
    assert _is_html("<table><tr><td>cell</td></tr></table>")


def test_is_html_plain_text():
    assert not _is_html("Plain text with no tags")
    assert not _is_html("CIK|Name|Form|Date|File")
    assert not _is_html("")


def test_clean_filing_text_strips_html():
    raw = "<html><body><p>Hello world</p><p>Second paragraph</p></body></html>"
    result = clean_filing_text(raw)
    assert "Hello world" in result
    assert "Second paragraph" in result
    assert "<html>" not in result
    assert "<p>" not in result


def test_clean_filing_text_normalizes_spaces():
    raw = "Word1    Word2\t\tWord3"
    result = clean_filing_text(raw)
    assert "Word1 Word2 Word3" in result


def test_clean_filing_text_collapses_blank_lines():
    raw = "Line 1\n\n\n\n\nLine 2\n\n\n\nLine 3"
    result = clean_filing_text(raw)
    assert "\n\n\n" not in result
    assert "Line 1" in result
    assert "Line 2" in result
    assert "Line 3" in result


def test_clean_filing_text_plain_passthrough():
    raw = "Annual report\nRevenue: $1M\nExpenses: $500K"
    result = clean_filing_text(raw)
    assert "Annual report" in result
    assert "Revenue: $1M" in result



def test_clean_filing_text_bs4_parser_rejected_falls_back():
    """When BS4 raises ParserRejectedMarkup, regex fallback is used and no exception propagates."""
    html = "<html><body><p>Important content</p></body></html>"
    with patch(
        "src.sec_ingestion.downloader.BeautifulSoup",
        side_effect=ParserRejectedMarkup("rejected"),
    ):
        result = clean_filing_text(html)
    assert result is not None
    assert "Important content" in result
    assert "<html>" not in result
    assert "<p>" not in result


def test_clean_filing_text_bs4_generic_exception_falls_back():
    """Any unexpected BS4 exception is also caught and falls back gracefully."""
    html = "<div>Some filing text</div>"
    with patch(
        "src.sec_ingestion.downloader.BeautifulSoup",
        side_effect=RuntimeError("internal bs4 error"),
    ):
        result = clean_filing_text(html)
    assert result is not None
    assert "Some filing text" in result


def test_clean_filing_text_null_bytes_do_not_raise():
    """Null bytes in HTML content must not crash the pipeline."""
    raw = "<html><body>Valid text\x00with null bytes</body></html>"
    result = clean_filing_text(raw)
    assert result is not None
    assert "Valid text" in result


def test_clean_filing_text_empty_string():
    result = clean_filing_text("")
    assert result == ""


def test_clean_filing_text_only_tags():
    """HTML with no visible text returns empty (or near-empty) string without raising."""
    result = clean_filing_text("<html><head><style>.foo{}</style></head><body></body></html>")
    assert result is not None


def test_strip_tags_regex_removes_tags():
    result = _strip_tags_regex("<b>hello</b> <i>world</i>")
    assert "hello" in result
    assert "world" in result
    assert "<b>" not in result
    assert "<i>" not in result


def test_strip_tags_regex_unclosed_tag():
    """Regex fallback should not raise on unclosed tags."""
    result = _strip_tags_regex("<div>text < unclosed")
    assert "text" in result



def _make_http_error(status_code: int) -> requests.HTTPError:
    response = Mock()
    response.status_code = status_code
    error = requests.HTTPError(f"{status_code} error")
    error.response = response
    return error


def _make_success_response(text: str) -> Mock:
    response = Mock()
    response.raise_for_status = Mock()
    response.text = text
    return response


@patch("src.sec_ingestion.downloader.time.sleep")
def test_fetch_raw_retries_on_5xx_then_succeeds(mock_sleep):
    session = Mock()
    session.get = Mock(
        side_effect=[_make_http_error(503), _make_http_error(502), _make_success_response("content after retries")]
    )

    result = fetch_raw("https://www.sec.gov/Archives/edgar/data/1/filing.txt", "test-agent test@example.com", session=session)

    assert result == "content after retries"
    assert session.get.call_count == 3
    assert mock_sleep.call_count == 2


@patch("src.sec_ingestion.downloader.time.sleep")
def test_fetch_raw_gives_up_after_max_retries(mock_sleep):
    session = Mock()
    session.get = Mock(side_effect=[_make_http_error(503), _make_http_error(503), _make_http_error(503)])

    result = fetch_raw("https://www.sec.gov/Archives/edgar/data/1/filing.txt", "test-agent test@example.com", session=session)

    assert result is None
    assert session.get.call_count == 3


@patch("src.sec_ingestion.downloader.time.sleep")
def test_fetch_raw_does_not_retry_4xx(mock_sleep):
    session = Mock()
    session.get = Mock(side_effect=_make_http_error(404))

    result = fetch_raw("https://www.sec.gov/Archives/edgar/data/1/missing.txt", "test-agent test@example.com", session=session)

    assert result is None
    assert session.get.call_count == 1
    mock_sleep.assert_not_called()



def _sgml_document(doc_type: str, sequence: str, filename: str, text: str, description: str = "EXHIBIT") -> str:
    return (
        "<DOCUMENT>\n"
        f"<TYPE>{doc_type}\n"
        f"<SEQUENCE>{sequence}\n"
        f"<FILENAME>{filename}\n"
        f"<DESCRIPTION>{description}\n"
        "<TEXT>\n"
        f"{text}\n"
        "</TEXT>\n"
        "</DOCUMENT>\n"
    )


def _sgml_submission(*documents: str, form_type: str = "8-K") -> str:
    header = (
        "<SEC-DOCUMENT>0001234567-26-000001.txt : 20260115\n"
        "<SEC-HEADER>0001234567-26-000001.hdr.sgml : 20260115\n"
        "<ACCEPTANCE-DATETIME>20260115120000\n"
        "ACCESSION NUMBER:\t\t0001234567-26-000001\n"
        f"CONFORMED SUBMISSION TYPE:\t{form_type}\n"
        f"PUBLIC DOCUMENT COUNT:\t\t{len(documents)}\n"
        "</SEC-HEADER>\n"
    )
    return header + "".join(documents) + "</SEC-DOCUMENT>\n"


def test_extract_primary_document_single_document():
    submission = _sgml_submission(
        _sgml_document("8-K", "1", "form8k.htm", "<html><body>Material event text</body></html>"),
        form_type="8-K",
    )
    result = extract_primary_document(submission)
    assert result.found is True
    assert result.sequence == "1"
    assert result.doc_type == "8-K"
    assert "Material event text" in result.text


def test_extract_primary_document_multiple_documents_returns_only_sequence_one():
    submission = _sgml_submission(
        _sgml_document("8-K", "1", "form8k.htm", "PRIMARY DOCUMENT CONTENT"),
        _sgml_document("EX-99.1", "2", "ex99-1.htm", "EXHIBIT ONE CONTENT press release"),
        _sgml_document("EX-99.2", "3", "ex99-2.htm", "EXHIBIT TWO CONTENT financial statements"),
        form_type="8-K",
    )
    result = extract_primary_document(submission)
    assert result.found is True
    assert result.sequence == "1"
    assert result.doc_type == "8-K"
    assert "PRIMARY DOCUMENT CONTENT" in result.text
    assert "EXHIBIT ONE CONTENT" not in result.text
    assert "EXHIBIT TWO CONTENT" not in result.text


def test_extract_primary_document_type_matches_form_type():
    submission = _sgml_submission(
        _sgml_document("DEF 14A", "1", "defproxy.htm", "Proxy statement body"),
        form_type="DEF 14A",
    )
    result = extract_primary_document(submission)
    assert result.doc_type == "DEF 14A"


def test_extract_primary_document_no_document_structure_falls_back():
    """Very old / malformed filings with no <DOCUMENT> tag at all."""
    plain = "CIK|COMPANY NAME|FORM TYPE|DATE FILED|FILENAME\nNo SGML structure here.\n"
    result = extract_primary_document(plain)
    assert result.found is False
    assert result.text is None


def test_extract_primary_document_malformed_missing_text_tag():
    """A <DOCUMENT> block that closes before any <TEXT> is found."""
    submission = "<DOCUMENT>\n<TYPE>8-K\n<SEQUENCE>1\n</DOCUMENT>\n"
    result = extract_primary_document(submission)
    assert result.found is False



def test_extract_primary_document_sc_to_t_style_tender_offer():
    submission = _sgml_submission(
        _sgml_document("SC TO-T", "1", "sctot.htm", "TENDER OFFER STATEMENT body text"),
        _sgml_document("EX-99.(A)(1)(I)", "2", "exhibit_offer.htm", "OFFER TO PURCHASE full text"),
        _sgml_document("EX-99.(D)(1)", "3", "exhibit_agreement.htm", "TENDER AGREEMENT full text"),
        form_type="SC TO-T",
    )
    result = extract_primary_document(submission)
    assert result.found is True
    assert result.doc_type == "SC TO-T"
    assert "TENDER OFFER STATEMENT body text" in result.text
    assert "OFFER TO PURCHASE full text" not in result.text
    assert "TENDER AGREEMENT full text" not in result.text


def test_extract_primary_document_s1_style_registration():
    submission = _sgml_submission(
        _sgml_document("S-1", "1", "forms1.htm", "REGISTRATION STATEMENT prospectus body"),
        _sgml_document("EX-1.1", "2", "ex1-1.htm", "UNDERWRITING AGREEMENT full text"),
        _sgml_document("EX-23.1", "3", "ex23-1.htm", "CONSENT OF INDEPENDENT AUDITORS"),
        form_type="S-1",
    )
    result = extract_primary_document(submission)
    assert result.found is True
    assert result.doc_type == "S-1"
    assert "REGISTRATION STATEMENT prospectus body" in result.text
    assert "UNDERWRITING AGREEMENT" not in result.text
    assert "CONSENT OF INDEPENDENT AUDITORS" not in result.text


def test_extract_primary_document_sc_13e3_style_going_private():
    submission = _sgml_submission(
        _sgml_document("SC 13E3", "1", "sc13e3.htm", "GOING PRIVATE TRANSACTION STATEMENT body"),
        _sgml_document("EX-99.(C)(1)", "2", "fairness_opinion.htm", "FAIRNESS OPINION full text"),
        form_type="SC 13E3",
    )
    result = extract_primary_document(submission)
    assert result.found is True
    assert result.doc_type == "SC 13E3"
    assert "GOING PRIVATE TRANSACTION STATEMENT body" in result.text
    assert "FAIRNESS OPINION" not in result.text


def test_extract_primary_document_sc_13d_a_dual_type_case():
    """Modeled on a real observed case (a GENCO SHIPPING accession):
    master.idx listed the filing as SC 13D/A, but the primary document's own
    <TYPE> was SC TO-T/A, because EDGAR let the filer cross-reference the
    same accession under both purposes. SEQUENCE=1 is still the correct
    primary document — a TYPE mismatch must never block extraction, only
    warn (checked separately in the fetch_and_clean tests below)."""
    submission = _sgml_submission(
        _sgml_document("SC TO-T/A", "1", "sctota.htm", "AMENDED TENDER OFFER STATEMENT body"),
        _sgml_document("EX-99.(A)(5)(I)", "2", "press_release.htm", "PRESS RELEASE full text"),
        form_type="SC 13D/A",
    )
    result = extract_primary_document(submission)
    assert result.found is True
    assert result.doc_type == "SC TO-T/A"
    assert result.sequence == "1"
    assert "AMENDED TENDER OFFER STATEMENT body" in result.text
    assert "PRESS RELEASE full text" not in result.text



def test_fetch_and_clean_stores_only_primary_document_in_raw_and_clean_text():
    """Fundamental acceptance criterion: a multi-document submission must
    leave no trace of its exhibits in either raw_text or clean_text — only
    SEQUENCE=1's own content may be stored."""
    submission = _sgml_submission(
        _sgml_document("8-K", "1", "form8k.htm", "<html><body>Material event text</body></html>"),
        _sgml_document("EX-99.1", "2", "ex99-1.htm", "<html><body>Press release exhibit content</body></html>"),
        _sgml_document("EX-99.2", "3", "ex99-2.htm", "<html><body>Financial statement exhibit content</body></html>"),
        form_type="8-K",
    )
    with patch("src.sec_ingestion.downloader.fetch_raw", return_value=submission), \
         patch("src.sec_ingestion.downloader.time.sleep"):
        raw_text, clean_text, downloaded_bytes, stored_bytes = fetch_and_clean(
            "https://www.sec.gov/Archives/edgar/data/1/0001234567-26-000001.txt",
            "test-agent test@example.com",
            expected_type="8-K",
        )

    assert "Material event text" in raw_text
    assert "Press release exhibit content" not in raw_text
    assert "Financial statement exhibit content" not in raw_text

    assert "Material event text" in clean_text
    assert "Press release exhibit content" not in clean_text
    assert "Financial statement exhibit content" not in clean_text

    assert downloaded_bytes == len(submission.encode("utf-8", errors="ignore"))
    assert stored_bytes == len(raw_text.encode("utf-8", errors="ignore"))
    assert stored_bytes < downloaded_bytes


def test_fetch_and_clean_type_mismatch_logs_warning_but_still_processes(caplog):
    submission = _sgml_submission(
        _sgml_document("SC TO-T/A", "1", "sctota.htm", "AMENDED TENDER OFFER STATEMENT body"),
        form_type="SC 13D/A",
    )
    with patch("src.sec_ingestion.downloader.fetch_raw", return_value=submission), \
         patch("src.sec_ingestion.downloader.time.sleep"), \
         caplog.at_level(logging.WARNING, logger="src.sec_ingestion.downloader"):
        raw_text, clean_text, downloaded_bytes, stored_bytes = fetch_and_clean(
            "https://www.sec.gov/Archives/edgar/data/1/0001234567-26-000002.txt",
            "test-agent test@example.com",
            expected_type="SC 13D/A",
        )

    assert "AMENDED TENDER OFFER STATEMENT body" in raw_text
    assert any("TYPE mismatch" in message for message in caplog.messages)


def test_fetch_and_clean_matching_type_does_not_warn(caplog):
    submission = _sgml_submission(
        _sgml_document("8-K", "1", "form8k.htm", "Material event text"),
        form_type="8-K",
    )
    with patch("src.sec_ingestion.downloader.fetch_raw", return_value=submission), \
         patch("src.sec_ingestion.downloader.time.sleep"), \
         caplog.at_level(logging.WARNING, logger="src.sec_ingestion.downloader"):
        fetch_and_clean(
            "https://www.sec.gov/Archives/edgar/data/1/0001234567-26-000003.txt",
            "test-agent test@example.com",
            expected_type="8-K",
        )

    assert not any("TYPE mismatch" in message for message in caplog.messages)


def test_fetch_and_clean_no_document_structure_falls_back_with_warning(caplog):
    plain_submission = "CIK|COMPANY NAME|FORM TYPE|DATE FILED|FILENAME\nLegacy plain-text filing body.\n"
    with patch("src.sec_ingestion.downloader.fetch_raw", return_value=plain_submission), \
         patch("src.sec_ingestion.downloader.time.sleep"), \
         caplog.at_level(logging.WARNING, logger="src.sec_ingestion.downloader"):
        raw_text, clean_text, downloaded_bytes, stored_bytes = fetch_and_clean(
            "https://www.sec.gov/Archives/edgar/data/1/0001234567-26-000004.txt",
            "test-agent test@example.com",
            expected_type="8-K",
        )

    assert raw_text == plain_submission
    assert downloaded_bytes == stored_bytes
    assert any("Could not locate a <DOCUMENT>" in message for message in caplog.messages)


def test_fetch_and_clean_returns_zeros_on_download_failure():
    with patch("src.sec_ingestion.downloader.fetch_raw", return_value=None):
        raw_text, clean_text, downloaded_bytes, stored_bytes = fetch_and_clean(
            "https://www.sec.gov/Archives/edgar/data/1/missing.txt",
            "test-agent test@example.com",
        )

    assert raw_text is None
    assert clean_text is None
    assert downloaded_bytes == 0
    assert stored_bytes == 0
