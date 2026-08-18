import pytest

from src.filing_routing.routing import CONTEXT, EVENT, IGNORED, get_filing_category


# ── EVENT ────────────────────────────────────────────────────────────────────

_EVENT_FORMS = [
    "8-K",
    "8-K/A",
    "SC TO-I",
    "SC TO-T",
    "SC TO-C",
    "SC 13D",
    "SC 13D/A",
    "SC 13E3",
    "S-1",
    "S-1/A",
    "424B3",
    "424B5",
    "DEF 14A",
    "DEFM14A",
    "PREM14A",
]


@pytest.mark.parametrize("form_type", _EVENT_FORMS)
def test_event_forms_classified_as_event(form_type):
    assert get_filing_category(form_type) == EVENT


# ── CONTEXT ──────────────────────────────────────────────────────────────────

_CONTEXT_FORMS = ["10-K", "10-Q", "20-F", "6-K"]


@pytest.mark.parametrize("form_type", _CONTEXT_FORMS)
def test_context_forms_classified_as_context(form_type):
    assert get_filing_category(form_type) == CONTEXT


# ── IGNORED ──────────────────────────────────────────────────────────────────

_IGNORED_FORMS = ["SC 13G", "SC 13G/A", "4", "144", "NT 10-K", "8-A12B", "UNKNOWN-FORM"]


@pytest.mark.parametrize("form_type", _IGNORED_FORMS)
def test_other_forms_classified_as_ignored(form_type):
    assert get_filing_category(form_type) == IGNORED


def test_empty_form_type_is_ignored():
    assert get_filing_category("") == IGNORED


def test_none_form_type_is_ignored():
    assert get_filing_category(None) == IGNORED


def test_form_type_is_case_insensitive():
    assert get_filing_category("8-k") == EVENT
    assert get_filing_category("10-q") == CONTEXT


def test_form_type_strips_whitespace():
    assert get_filing_category("  8-K  ") == EVENT


def test_event_and_context_forms_do_not_overlap():
    assert set(_EVENT_FORMS).isdisjoint(_CONTEXT_FORMS)
