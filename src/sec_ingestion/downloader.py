import importlib.util
import logging
import re
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_HTML_TAG_RE = re.compile(r"<\s*(html|body|div|p|table|head|span)\b", re.IGNORECASE)
_ALL_TAGS_RE = re.compile(r"<[^>]*>")
DEFAULT_DELAY = 0.5  # seconds between requests, per SEC EDGAR fair-use guidelines

_LXML_AVAILABLE = importlib.util.find_spec("lxml") is not None


def fetch_raw(url: str, user_agent: str) -> Optional[str]:
    try:
        response = requests.get(
            url, headers={"User-Agent": user_agent}, timeout=30
        )
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        logger.error(f"Failed to download {url}: {e}")
        return None


def _is_html(text: str) -> bool:
    return bool(_HTML_TAG_RE.search(text))


def _strip_tags_regex(text: str) -> str:
    """Last-resort tag removal when all BS4 parsers fail."""
    return _ALL_TAGS_RE.sub(" ", text)


def _bs4_get_text(raw: str) -> Optional[str]:
    """
    Try to extract text via BeautifulSoup.
    Attempts html.parser first, then lxml (if installed).
    Returns None when every available parser fails.
    """
    for parser in (["html.parser"] + (["lxml"] if _LXML_AVAILABLE else [])):
        try:
            return BeautifulSoup(raw, parser).get_text(separator=" ")
        except Exception:
            continue
    return None


def _normalize(text: str) -> str:
    """Collapse whitespace and limit consecutive blank lines to one."""
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
    """
    Extract and normalize text from a filing.
    Never raises — falls back to regex tag stripping if BS4 rejects the markup.
    """
    if not _is_html(raw):
        return _normalize(raw)

    text = _bs4_get_text(raw)
    if text is None:
        logger.warning(
            "All BS4 parsers rejected the markup; falling back to regex tag stripping."
        )
        text = _strip_tags_regex(raw)

    return _normalize(text)


def fetch_and_clean(
    url: str, user_agent: str, delay: float = DEFAULT_DELAY
) -> tuple[Optional[str], Optional[str]]:
    """Download a filing and return (raw_text, clean_text), or (None, None) on error."""
    raw = fetch_raw(url, user_agent)
    if raw is None:
        return None, None
    time.sleep(delay)
    return raw, clean_filing_text(raw)
