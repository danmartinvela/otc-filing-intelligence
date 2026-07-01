import logging
from pathlib import Path

from ..database.db import DB_PATH, enrich_filings_with_tickers

logger = logging.getLogger(__name__)


def enrich_existing_filings_with_tickers(db_path: Path = DB_PATH) -> tuple[int, int]:
    """Update filings.ticker from the companies table.

    Returns (enriched_count, still_missing_count).
    """
    return enrich_filings_with_tickers(db_path)
