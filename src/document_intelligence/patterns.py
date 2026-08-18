"""Regex patterns and keyword lists used by the extractors in extractor.py.

This module holds no logic — only compiled patterns and reference lists,
so extractors stay readable and patterns stay easy to extend.
"""
import re

# ── Item numbers (Form 8-K "Item X.XX" references) ─────────────────────────

ITEM_PATTERN = re.compile(r"\bItem\s+(\d{1,2}\.\d{2})\b", re.IGNORECASE)

# ── Money ────────────────────────────────────────────────────────────────────

# Either a comma-grouped number (1,234) or a plain digit run (1500) — the
# comma-only version alone would silently truncate bare numbers like "$1500"
# to "$150" since \d{1,3} stops at 3 digits without a following comma group.
_MONEY_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)"

MONEY_PATTERN = re.compile(
    rf"\$\s?{_MONEY_NUMBER}(?:\.\d+)?(?:\s?(?:thousand|million|billion))?"
    rf"|USD\s?{_MONEY_NUMBER}(?:\.\d+)?(?:\s?(?:thousand|million|billion))?"
    rf"|\b{_MONEY_NUMBER}(?:\.\d+)?(?:\s?(?:thousand|million|billion))?\s+dollars\b",
    re.IGNORECASE,
)

# ── Percentages ──────────────────────────────────────────────────────────────

PERCENTAGE_PATTERN = re.compile(r"\d{1,3}(?:\.\d+)?\s?%")

# ── Dates ────────────────────────────────────────────────────────────────────

_MONTHS = (
    "January|February|March|April|May|June|July|"
    "August|September|October|November|December"
)

DATE_PATTERN = re.compile(
    rf"(?:{_MONTHS})\s+\d{{1,2}},\s?\d{{4}}"
    rf"|(?<!\d)\d{{4}}-\d{{2}}-\d{{2}}(?!\d)"
    rf"|(?<!\d)\d{{2}}/\d{{2}}/\d{{4}}(?!\d)"
)

# ── Agreements ───────────────────────────────────────────────────────────────

AGREEMENT_TYPES = [
    "Asset Purchase Agreement",
    "Merger Agreement",
    "Stock Purchase Agreement",
    "Securities Purchase Agreement",
    "Membership Interest Purchase Agreement",
    "Purchase Agreement",
    "Credit Agreement",
    "Loan Agreement",
    "Employment Agreement",
    "Separation Agreement",
    "Consulting Agreement",
    "Registration Rights Agreement",
    "Underwriting Agreement",
    "Settlement Agreement",
    "License Agreement",
    "Voting Agreement",
    "Subscription Agreement",
    "Indemnification Agreement",
    "Non-Disclosure Agreement",
    "Material Definitive Agreement",
    "Definitive Agreement",
]

# Longest-first so "Material Definitive Agreement" wins over "Definitive Agreement".
_AGREEMENT_TYPES_SORTED = sorted(AGREEMENT_TYPES, key=len, reverse=True)

AGREEMENT_PATTERN = re.compile(
    "|".join(re.escape(a) for a in _AGREEMENT_TYPES_SORTED), re.IGNORECASE
)

# ── Keywords ─────────────────────────────────────────────────────────────────
# Broad, non-classifying list of terms relevant to OTC/SEC filing analysis.
# Detection only — no interpretation of what they mean for the filing.

KEYWORDS = [
    "Tender Offer",
    "Going Concern",
    "Reverse Merger",
    "Reverse Stock Split",
    "Asset Purchase Agreement",
    "Convertible Note",
    "Convertible Debenture",
    "Preferred Stock",
    "Private Placement",
    "Chapter 11",
    "Chapter 7",
    "Bankruptcy",
    "Insolvency",
    "Receivership",
    "Credit Agreement",
    "Change of Control",
    "Material Definitive Agreement",
    "Material Impairment",
    "Shelf Registration",
    "Share Repurchase",
    "Stock Buyback",
    "Dividend",
    "Special Dividend",
    "Proxy",
    "Proxy Statement",
    "Delisting",
    "Delisted",
    "Nasdaq",
    "NYSE",
    "OTCQX",
    "OTCQB",
    "Pink Sheets",
    "Expert Market",
    "Attorney Letter",
    "Going-Private",
    "Going Private Transaction",
    "Bankruptcy Petition",
    "Debt Restructuring",
    "Default",
    "Covenant Breach",
    "Impairment Charge",
    "Restatement",
    "Internal Control",
    "Material Weakness",
    "Auditor Resignation",
    "Auditor Dismissal",
    "Stock Split",
    "Spin-Off",
    "Divestiture",
    "Acquisition",
    "Joint Venture",
    "Standstill Agreement",
    "Poison Pill",
    "Shareholder Rights Plan",
    "Related Party Transaction",
    "Golden Parachute",
    "Severance",
    "Resignation",
    "Termination of Employment",
    "Executive Compensation",
    "Special Meeting",
    "Annual Meeting",
    "Cease Trade Order",
    "Trading Halt",
    "SEC Investigation",
    "Subpoena",
    "Class Action",
    "Securities Fraud",
]

# Longest-first so multi-word keywords aren't shadowed by shorter substrings.
_KEYWORDS_SORTED = sorted(set(KEYWORDS), key=len, reverse=True)

KEYWORD_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in _KEYWORDS_SORTED) + r")\b",
    re.IGNORECASE,
)

# ── Companies (regex fallback when spaCy is unavailable) ───────────────────

_COMPANY_SUFFIXES = (
    r"(?:Inc|Incorporated|Corp|Corporation|Ltd|Limited|LLC|LLP|"
    r"Holdings|Group|Company|Co|PLC)\.?"
)

# Candidate words are joined by plain spaces only (not \s) so a match can never
# bridge a newline/paragraph break and glue unrelated sentence fragments together.
COMPANY_SUFFIX_PATTERN = re.compile(
    rf"\b([A-Z][\w&'-]*(?:[ ]+[A-Z][\w&'.-]*){{0,4}}[ ]+{_COMPANY_SUFFIXES})"
    rf"(?=[\s,.;:]|$)"
)

COMPANY_NAME_STOPWORDS = {
    "the", "this", "that", "any", "such", "said", "each", "every",
    "a", "an", "its", "their", "our", "your",
}

COMPANY_ABBREVIATED_SUFFIXES = {"inc", "corp", "ltd", "co"}

# Standard SEC cover-page/boilerplate phrases that structurally look like a
# company name (Capitalized Words + "Company") but never are one.
COMPANY_NAME_BLOCKLIST = {
    "emerging growth company",
    "smaller reporting company",
    "shell company",
    "public company",
    "reporting company",
    "well-known seasoned issuer",
    "voluntary filer",
    "acceleration filer",
}
