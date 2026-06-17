import logging
from datetime import date
from typing import List

import requests

from .parser import parse_master_idx
from ..database.models import Filing

logger = logging.getLogger(__name__)

SEC_DAILY_INDEX_BASE = "https://www.sec.gov/Archives/edgar/daily-index"

IMPORTANT_FORMS = [
    "8-K",
    "10-K",
    "10-Q",
    "SC TO-I",
    "SC TO-T",
    "SC 13D",
    "SC 13G",
    "DEF 14A",
    "S-1",
]


def get_quarter(month: int) -> int:
    return (month - 1) // 3 + 1


def build_index_url(target_date: date) -> str:
    quarter = get_quarter(target_date.month)
    date_str = target_date.strftime("%Y%m%d")
    return (
        f"{SEC_DAILY_INDEX_BASE}/{target_date.year}"
        f"/QTR{quarter}/master.{date_str}.idx"
    )


def fetch_daily_index(target_date: date, user_agent: str) -> str:
    url = build_index_url(target_date)
    logger.info(f"Fetching daily index: {url}")
    response = requests.get(
        url, headers={"User-Agent": user_agent}, timeout=30
    )
    if response.status_code == 404:
        raise FileNotFoundError(
            f"Daily index not found for {target_date}. URL: {url}\n"
            "The SEC may not have published filings for this date "
            "(weekends, holidays, or a date in the future)."
        )
    response.raise_for_status()
    return response.text


def get_filtered_filings(target_date: date, user_agent: str) -> List[Filing]:
    content = fetch_daily_index(target_date, user_agent)
    all_filings = parse_master_idx(content)
    filtered = [f for f in all_filings if f.form_type in IMPORTANT_FORMS]
    logger.info(
        f"Parsed {len(all_filings)} total filings, "
        f"{len(filtered)} match the form filter."
    )
    return filtered
