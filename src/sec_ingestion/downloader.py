import importlib.util
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_HTML_TAG_RE = re.compile(r"<\s*(html|body|div|p|table|head|span)\b", re.IGNORECASE)
_ALL_TAGS_RE = re.compile(r"<[^>]*>")

DEFAULT_DELAY = 0.2
MIN_SAFE_DELAY = 0.11

_MAX_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 2.0

_LXML_AVAILABLE = importlib.util.find_spec("lxml") is not None


def build_session(user_agent: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    return session


def fetch_raw(url: str, user_agent: str, session: Optional[requests.Session] = None) -> Optional[str]:
    client = session if session is not None else requests
    headers = None if session is not None else {"User-Agent": user_agent}

    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = client.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            return response.text
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status is not None and 500 <= status < 600 and attempt < _MAX_RETRIES:
                wait = _RETRY_BACKOFF_SECONDS * (attempt + 1)
                logger.warning(
                    f"SEC returned {status} for {url} "
                    f"(attempt {attempt + 1}/{_MAX_RETRIES + 1}); retrying in {wait:.0f}s..."
                )
                time.sleep(wait)
                continue
            logger.error(f"Failed to download {url}: {exc}")
            return None
        except requests.RequestException as exc:
            logger.error(f"Failed to download {url}: {exc}")
            return None
    return None


def _is_html(text: str) -> bool:
    return bool(_HTML_TAG_RE.search(text))


def _strip_tags_regex(text: str) -> str:
    return _ALL_TAGS_RE.sub(" ", text)


def _bs4_get_text(raw: str) -> Optional[str]:
    for parser in (["html.parser"] + (["lxml"] if _LXML_AVAILABLE else [])):
        try:
            return BeautifulSoup(raw, parser).get_text(separator=" ")
        except Exception:
            continue
    return None


def _normalize(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    lines = [line.strip() for line in text.splitlines()]
    result: list[str] = []
    prev_blank = False
    for line in lines:
        if not line:
            if not prev_blank:
                result.append("")
            prev_blank = True
        else:
            result.append(line)
            prev_blank = False
    return "\n".join(result).strip()


def clean_filing_text(raw: str) -> str:
    if not _is_html(raw):
        return _normalize(raw)

    text = _bs4_get_text(raw)
    if text is None:
        logger.warning(
            "All BS4 parsers rejected the markup; falling back to regex tag stripping."
        )
        text = _strip_tags_regex(raw)

    return _normalize(text)



_DOCUMENT_TAG = "<DOCUMENT>"
_DOCUMENT_CLOSE_TAG = "</DOCUMENT>"
_TEXT_OPEN_TAG = "<TEXT>"
_TEXT_CLOSE_TAG = "</TEXT>"
_TYPE_RE = re.compile(r"<TYPE>\s*([^\r\n<]*)", re.IGNORECASE)
_SEQUENCE_RE = re.compile(r"<SEQUENCE>\s*([^\r\n<]*)", re.IGNORECASE)


@dataclass
class PrimaryDocumentResult:
    text: Optional[str]
    doc_type: Optional[str]
    sequence: Optional[str]
    found: bool


def extract_primary_document(raw_submission: str) -> PrimaryDocumentResult:
    doc_start = raw_submission.find(_DOCUMENT_TAG)
    if doc_start == -1:
        return PrimaryDocumentResult(text=None, doc_type=None, sequence=None, found=False)

    text_start = raw_submission.find(_TEXT_OPEN_TAG, doc_start)
    doc_close_before_text = raw_submission.find(_DOCUMENT_CLOSE_TAG, doc_start)
    if text_start == -1 or (0 <= doc_close_before_text < text_start):
        return PrimaryDocumentResult(text=None, doc_type=None, sequence=None, found=False)

    header = raw_submission[doc_start:text_start]
    type_match = _TYPE_RE.search(header)
    sequence_match = _SEQUENCE_RE.search(header)
    doc_type = type_match.group(1).strip() if type_match else None
    sequence = sequence_match.group(1).strip() if sequence_match else None

    text_end = raw_submission.find(_TEXT_CLOSE_TAG, text_start)
    if text_end == -1:
        return PrimaryDocumentResult(text=None, doc_type=doc_type, sequence=sequence, found=False)

    content_start = text_start + len(_TEXT_OPEN_TAG)
    document_text = raw_submission[content_start:text_end].strip("\r\n")

    return PrimaryDocumentResult(text=document_text, doc_type=doc_type, sequence=sequence, found=True)


def fetch_and_clean(
    url: str,
    user_agent: str,
    delay: float = DEFAULT_DELAY,
    session: Optional[requests.Session] = None,
    expected_type: Optional[str] = None,
) -> tuple[Optional[str], Optional[str], int, int]:
    raw_submission = fetch_raw(url, user_agent, session=session)
    if raw_submission is None:
        return None, None, 0, 0

    downloaded_bytes = len(raw_submission.encode("utf-8", errors="ignore"))

    primary = extract_primary_document(raw_submission)
    if primary.found and primary.text is not None:
        raw_text = primary.text
        if expected_type and primary.doc_type and primary.doc_type.strip().upper() != expected_type.strip().upper():
            logger.warning(
                f"Primary document TYPE mismatch for {url}: expected '{expected_type}', "
                f"found '{primary.doc_type}' (SEQUENCE={primary.sequence}). Processing it anyway."
            )
    else:
        logger.warning(
            f"Could not locate a <DOCUMENT>/<TEXT> block in the complete submission for {url}; "
            "falling back to the full submission as raw_text. This filing should be audited."
        )
        raw_text = raw_submission

    stored_bytes = len(raw_text.encode("utf-8", errors="ignore"))
    time.sleep(max(delay, MIN_SAFE_DELAY))
    clean_text = clean_filing_text(raw_text)
    return raw_text, clean_text, downloaded_bytes, stored_bytes
