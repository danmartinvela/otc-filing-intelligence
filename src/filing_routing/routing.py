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
    normalized = (form_type or "").strip().upper()
    if normalized in EVENT_FORMS:
        return EVENT
    if normalized in CONTEXT_FORMS:
        return CONTEXT
    return IGNORED
