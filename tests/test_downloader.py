from unittest.mock import patch

import pytest
from bs4.exceptions import ParserRejectedMarkup

from src.sec_ingestion.downloader import _is_html, _strip_tags_regex, clean_filing_text


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
    # Should not have more than one consecutive blank line
    assert "\n\n\n" not in result
    assert "Line 1" in result
    assert "Line 2" in result
    assert "Line 3" in result


def test_clean_filing_text_plain_passthrough():
    raw = "Annual report\nRevenue: $1M\nExpenses: $500K"
    result = clean_filing_text(raw)
    assert "Annual report" in result
    assert "Revenue: $1M" in result


# --- Robustness / fallback tests ---

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
