import argparse
import logging
import os
import sys
from datetime import date, datetime

from dotenv import load_dotenv

from .sec_ingestion.daily_index import get_filtered_filings
from .sec_ingestion.downloader import fetch_and_clean
from .database.db import (
    init_db,
    insert_filings,
    update_filing_content,
    upsert_companies,
    enrich_filings_with_tickers,
    enrich_filings_with_otc,
)
from .company_enrichment.sec_company_tickers import (
    download_sec_company_tickers,
    parse_sec_company_tickers,
)
from .otcmarkets.otc_screener_importer import import_otc_screener_csv

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
            "SEC EDGAR filing pipeline. Download and store daily filings, import "
            "the company/ticker mapping, and enrich stored filings with tickers."
        )
    )
    parser.add_argument(
        "--date",
        type=_parse_date,
        metavar="YYYY-MM-DD",
        default=None,
        help="Fetch and store filings for this date (e.g. 2024-05-15)",
    )
    parser.add_argument(
        "--download-content",
        action="store_true",
        help="Also download and clean the text content of each filing (requires --date)",
    )
    parser.add_argument(
        "--import-sec-company-tickers",
        action="store_true",
        help="Download the SEC company/ticker mapping and store it in the companies table",
    )
    parser.add_argument(
        "--enrich-filings",
        action="store_true",
        help="Update filings.ticker from the companies table",
    )
    parser.add_argument(
        "--import-otc-screener-csv",
        metavar="PATH",
        default=None,
        help="Import an OTC Markets Stock Screener CSV into the otc_securities table",
    )
    parser.add_argument(
        "--enrich-filings-with-otc",
        action="store_true",
        help="Update filings.otc_tier, sec_type, country from otc_securities via ticker match",
    )
    return parser


def _run_import_tickers(user_agent: str) -> None:
    logger.info("Importing SEC company/ticker mapping...")
    try:
        data = download_sec_company_tickers(user_agent)
    except Exception as exc:
        logger.error(f"Failed to download company tickers: {exc}")
        sys.exit(1)
    companies = parse_sec_company_tickers(data)
    count = upsert_companies(companies)
    logger.info(f"  {count} company records stored in 'companies' table.")


def _run_daily_pipeline(args, user_agent: str) -> None:
    logger.info(f"Starting pipeline for date: {args.date}")
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


def _run_enrich_filings() -> None:
    logger.info("Enriching filings with tickers from companies table...")
    enriched, missing = enrich_filings_with_tickers()
    logger.info(f"  Enriched: {enriched} | No ticker match: {missing}")


def _run_import_otc_csv(csv_path: str) -> None:
    try:
        inserted, updated = import_otc_screener_csv(csv_path)
        logger.info(f"  OTC securities: {inserted} inserted, {updated} updated.")
    except FileNotFoundError as exc:
        logger.error(str(exc))


def _run_enrich_filings_with_otc() -> None:
    logger.info("Enriching filings with OTC data (tier, sec_type, country)...")
    enriched, no_match = enrich_filings_with_otc()
    logger.info(f"  Enriched: {enriched} | No OTC match: {no_match}")


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()

    if not any([
        args.date,
        args.import_sec_company_tickers,
        args.enrich_filings,
        args.import_otc_screener_csv,
        args.enrich_filings_with_otc,
    ]):
        parser.error(
            "Specify at least one action: --date, --import-sec-company-tickers, "
            "--enrich-filings, --import-otc-screener-csv, or --enrich-filings-with-otc."
        )

    user_agent = _get_user_agent()
    init_db()

    if args.import_sec_company_tickers:
        _run_import_tickers(user_agent)

    if args.date:
        _run_daily_pipeline(args, user_agent)

    if args.enrich_filings:
        _run_enrich_filings()

    if args.import_otc_screener_csv:
        _run_import_otc_csv(args.import_otc_screener_csv)

    if args.enrich_filings_with_otc:
        _run_enrich_filings_with_otc()

    logger.info("Done.")


if __name__ == "__main__":
    main()
