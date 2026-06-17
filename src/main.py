import argparse
import logging
import os
import sys
from datetime import date, datetime

from dotenv import load_dotenv

from .sec_ingestion.daily_index import get_filtered_filings
from .sec_ingestion.downloader import fetch_and_clean
from .database.db import init_db, insert_filings, update_filing_content

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

_PLACEHOLDER_AGENT = "OTCFilingIntelligence contact@example.com"


def _get_user_agent() -> str:
    agent = os.getenv("SEC_USER_AGENT", "").strip()
    if not agent:
        print(
            "[WARNING] SEC_USER_AGENT is not set. Using a placeholder User-Agent.\n"
            "          Please set it in a .env file or your environment:\n"
            f"          SEC_USER_AGENT=\"YourName youremail@example.com\"\n"
            "          The SEC requires an identifiable User-Agent for all requests.\n"
        )
        return _PLACEHOLDER_AGENT
    return agent


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid date '{value}'. Expected format: YYYY-MM-DD"
        )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download SEC EDGAR filings for a given date, filter by form type, "
            "and store metadata (and optionally content) in a local SQLite database."
        )
    )
    parser.add_argument(
        "--date",
        required=True,
        type=_parse_date,
        metavar="YYYY-MM-DD",
        help="Date to fetch filings for (e.g. 2024-05-15)",
    )
    parser.add_argument(
        "--download-content",
        action="store_true",
        help="Also download and clean the text content of each filing",
    )
    return parser


def main() -> None:
    args = _build_arg_parser().parse_args()
    user_agent = _get_user_agent()

    logger.info(f"Starting pipeline for date: {args.date}")
    init_db()

    try:
        filings = get_filtered_filings(args.date, user_agent)
    except FileNotFoundError as exc:
        logger.error(str(exc))
        sys.exit(1)
    except Exception as exc:
        logger.error(f"Unexpected error while fetching the daily index: {exc}")
        sys.exit(1)

    if not filings:
        logger.info("No filings matched the form filter for this date. Nothing to store.")
        return

    logger.info(f"Storing {len(filings)} filings in the database...")
    inserted, skipped = insert_filings(filings)
    logger.info(f"  Inserted: {inserted} | Already existed: {skipped}")

    if args.download_content:
        logger.info("Downloading filing contents (this may take a while)...")
        total = len(filings)
        failed = 0
        for idx, filing in enumerate(filings, start=1):
            logger.info(
                f"  [{idx}/{total}] {filing.form_type} — {filing.company_name}"
            )
            try:
                raw, cleaned = fetch_and_clean(filing.filing_url, user_agent)
            except Exception as exc:
                logger.error(f"    Unexpected error for {filing.filing_url}: {exc}")
                failed += 1
                continue
            if raw is not None:
                update_filing_content(filing.filename, raw, cleaned or "")
            else:
                logger.warning(f"    Skipped (download failed): {filing.filing_url}")
                failed += 1
        if failed:
            logger.warning(f"Content download finished with {failed}/{total} failures.")

    logger.info("Pipeline complete.")


if __name__ == "__main__":
    main()
