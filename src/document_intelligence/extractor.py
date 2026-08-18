import logging
import re
from typing import List, Optional

from .models import DocumentSnapshot
from .patterns import (
    AGREEMENT_PATTERN,
    AGREEMENT_TYPES,
    COMPANY_ABBREVIATED_SUFFIXES,
    COMPANY_NAME_BLOCKLIST,
    COMPANY_NAME_STOPWORDS,
    COMPANY_SUFFIX_PATTERN,
    DATE_PATTERN,
    ITEM_PATTERN,
    KEYWORD_PATTERN,
    KEYWORDS,
    MONEY_PATTERN,
    PERCENTAGE_PATTERN,
)

logger = logging.getLogger(__name__)

_NLP = None
_SPACY_LOAD_ATTEMPTED = False


def _load_spacy_model():
    """Lazily load the spaCy NER model. Returns None if spaCy or the model
    isn't available — callers must fall back gracefully, never raise."""
    global _NLP, _SPACY_LOAD_ATTEMPTED
    if _SPACY_LOAD_ATTEMPTED:
        return _NLP
    _SPACY_LOAD_ATTEMPTED = True
    try:
        import spacy
    except ImportError:
        logger.info(
            "spaCy not installed; extract_companies falls back to regex, "
            "extract_people will return []."
        )
        return None
    try:
        _NLP = spacy.load("en_core_web_sm")
    except OSError:
        logger.info(
            "spaCy model 'en_core_web_sm' not found "
            "(run: python -m spacy download en_core_web_sm)."
        )
        _NLP = None
    return _NLP


def _dedupe_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


# ── Items ────────────────────────────────────────────────────────────────────

def extract_items(text: str) -> List[str]:
    """Return the SEC 8-K item codes referenced in the text, e.g. ['2.01', '5.02']."""
    if not text:
        return []
    return _dedupe_preserve_order(ITEM_PATTERN.findall(text))


# ── Money ────────────────────────────────────────────────────────────────────

def _normalize_money(raw: str) -> str:
    """Collapse whitespace and remove the gap between '$' and the amount."""
    return " ".join(raw.split()).replace("$ ", "$")


def _looks_like_real_amount(text: str) -> bool:
    """Filter out bare digit-only '$N' matches with no comma grouping.

    Short ones (1-3 digits) are almost always artifacts of HTML financial
    tables collapsing into plain text. Long ones (7+ digits) are almost
    always identifiers (CIK, accession number fragments) rather than real
    amounts, since actual large amounts in filings are comma-formatted.
    """
    lowered = text.lower()
    if "," in text:
        return True
    if any(word in lowered for word in ("thousand", "million", "billion", "dollars")):
        return True
    decimal_match = re.search(r"\.(\d+)", text)
    if decimal_match and len(decimal_match.group(1)) == 2:
        return True
    digits = re.sub(r"\D", "", text)
    return 4 <= len(digits) <= 6


def extract_money(text: str) -> List[str]:
    """Return monetary amounts found in the text, e.g. ['$42,000,000', '$3.5 million']."""
    if not text:
        return []
    matches = [_normalize_money(m) for m in MONEY_PATTERN.findall(text)]
    matches = [m for m in matches if _looks_like_real_amount(m)]
    return _dedupe_preserve_order(matches)


# ── Percentages ──────────────────────────────────────────────────────────────

def extract_percentages(text: str) -> List[str]:
    """Return percentage values found in the text, e.g. ['51%', '9.99%']."""
    if not text:
        return []
    matches = ["".join(m.split()) for m in PERCENTAGE_PATTERN.findall(text)]
    return _dedupe_preserve_order(matches)


# ── Dates ────────────────────────────────────────────────────────────────────

def extract_dates(text: str) -> List[str]:
    """Return dates found in the text in whatever format they appear."""
    if not text:
        return []
    return _dedupe_preserve_order(DATE_PATTERN.findall(text))


# ── Agreements ───────────────────────────────────────────────────────────────

def extract_agreements(text: str) -> List[str]:
    """Return named agreement types found in the text, canonicalized to title case."""
    if not text:
        return []
    lookup = {a.lower(): a for a in AGREEMENT_TYPES}
    matches = [lookup[m.lower()] for m in AGREEMENT_PATTERN.findall(text)]
    return _dedupe_preserve_order(matches)


# ── Keywords ─────────────────────────────────────────────────────────────────

def extract_keywords(text: str) -> List[str]:
    """Return relevant keywords/phrases present in the text (detection only, no classification)."""
    if not text:
        return []
    lookup = {k.lower(): k for k in KEYWORDS}
    matches = [lookup[m.lower()] for m in KEYWORD_PATTERN.findall(text) if m.lower() in lookup]
    return _dedupe_preserve_order(matches)


# ── Companies ────────────────────────────────────────────────────────────────

def _clean_company_candidate(raw: str) -> Optional[str]:
    text = raw.strip()
    words = text.split()
    if not words:
        return None
    first_word = words[0].lower().strip(".,")
    if first_word in COMPANY_NAME_STOPWORDS:
        return None
    if text.endswith("."):
        suffix_word = text[:-1].split()[-1].lower()
        if suffix_word not in COMPANY_ABBREVIATED_SUFFIXES:
            text = text[:-1]
    if text.lower().rstrip(".") in COMPANY_NAME_BLOCKLIST:
        return None
    return text


def _extract_companies_regex(text: str) -> List[str]:
    candidates = (_clean_company_candidate(c) for c in COMPANY_SUFFIX_PATTERN.findall(text))
    return _dedupe_preserve_order([c for c in candidates if c])


def extract_companies(text: str) -> List[str]:
    """Return company names found in the text.

    Uses spaCy NER (ORG entities) when available, otherwise falls back to a
    suffix-based regex (e.g. "ABC Holdings Inc.").
    """
    if not text:
        return []
    nlp = _load_spacy_model()
    if nlp is not None:
        doc = nlp(text)
        names = [ent.text.strip() for ent in doc.ents if ent.label_ == "ORG"]
        return _dedupe_preserve_order(names)
    return _extract_companies_regex(text)


# ── People ───────────────────────────────────────────────────────────────────

def extract_people(text: str) -> List[str]:
    """Return person names found in the text via spaCy NER.

    Returns [] (never raises) when spaCy or its model isn't installed.
    """
    if not text:
        return []
    nlp = _load_spacy_model()
    if nlp is None:
        return []
    doc = nlp(text)
    names = [ent.text.strip() for ent in doc.ents if ent.label_ == "PERSON"]
    return _dedupe_preserve_order(names)


# ── Snapshot ─────────────────────────────────────────────────────────────────

def create_document_snapshot(filing) -> DocumentSnapshot:
    """Build a DocumentSnapshot from a filing (Filing dataclass or dict-like row).

    Requires `filename`, `form_type`, and `clean_text` (attributes or keys).
    """
    if isinstance(filing, dict):
        filename = filing["filename"]
        form_type = filing.get("form_type", "")
        text = filing.get("clean_text") or ""
    else:
        filename = filing.filename
        form_type = getattr(filing, "form_type", "")
        text = getattr(filing, "clean_text", None) or ""

    items = extract_items(text)

    return DocumentSnapshot(
        filename=filename,
        form_type=form_type,
        items=items,
        keywords=extract_keywords(text),
        money=extract_money(text),
        percentages=extract_percentages(text),
        dates=extract_dates(text),
        companies=extract_companies(text),
        people=extract_people(text),
        agreements=extract_agreements(text),
        sections=[f"Item {code}" for code in items],
    )
