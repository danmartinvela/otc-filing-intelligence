"""Classifies SEC form types so the pipeline knows what to do with them.

EVENT filings represent corporate events that may move a company's value —
they get the full Document Intelligence treatment and are eventually sent
to an LLM. CONTEXT filings are stored but never analyzed on their own; they
only serve as historical background when an EVENT filing needs it. Anything
else is IGNORED: not stored for analysis, not classified further.
"""
from typing import Optional

EVENT = "EVENT"
CONTEXT = "CONTEXT"
IGNORED = "IGNORED"

EVENT_FORMS = {
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
}

CONTEXT_FORMS = {
    "10-K",
    "10-Q",
    "20-F",
    "6-K",
}


def get_filing_category(form_type: Optional[str]) -> str:
    """Return EVENT, CONTEXT, or IGNORED for a given SEC form type."""
    normalized = (form_type or "").strip().upper()
    if normalized in EVENT_FORMS:
        return EVENT
    if normalized in CONTEXT_FORMS:
        return CONTEXT
    return IGNORED
