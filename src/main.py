import argparse
import logging
import sys
from datetime import date, datetime

from dotenv import load_dotenv

from .database.db import (
    init_db,
    upsert_companies,
    enrich_filings_with_tickers,
    enrich_filings_with_otc,
)
from .company_enrichment.sec_company_tickers import (
    download_sec_company_tickers,
    parse_sec_company_tickers,
)
from .otcmarkets.otc_screener_importer import import_otc_screener_csv
from .llm_analysis.client import LLMConfigError
from .pipeline import get_user_agent, run_daily_pipeline, run_snapshot_pipeline, run_llm_pipeline

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


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
    parser.add_argument(
        "--build-snapshots",
        action="store_true",
        help="Build Document Intelligence snapshots for filings with clean_text",
    )
    parser.add_argument(
        "--llm-first-pass",
        action="store_true",
        help="Run the LLM first pass over pending EVENT filings (requires LLM_API_KEY, "
        "LLM_BASE_URL, LLM_MODEL)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of filings to process with --llm-first-pass (default: no limit)",
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


def _daily_pipeline_cli_progress(data: dict) -> None:
    """Reproduces the log lines _run_daily_pipeline used to print inline,
    now driven by pipeline.run_daily_pipeline's on_progress checkpoints so
    the CLI output stays in the same order as before (fetch -> store ->
    per-item download), even though the loop itself now lives in pipeline.py.
    """
    stage = data["stage"]
    if stage == "index" and data["total_found"] > 0:
        logger.info(f"Storing {data['total_found']} filings in the database...")
    elif stage == "store":
        logger.info(f"  Inserted: {data['inserted']} | Already existed: {data['already_existed']}")
    elif stage == "content":
        if data["done"] == 1:
            logger.info("Downloading filing contents (this may take a while)...")
        logger.info(f"  [{data['done']}/{data['total']}] {data['label']}")
        if not data["ok"]:
            logger.error(f"    {data['error']}")


def _run_daily_pipeline(args, user_agent: str) -> None:
    logger.info(f"Starting pipeline for date: {args.date}")
    try:
        result = run_daily_pipeline(
            args.date,
            user_agent,
            download_content=args.download_content,
            on_progress=_daily_pipeline_cli_progress,
        )
    except FileNotFoundError as exc:
        logger.error(str(exc))
        sys.exit(1)
    except Exception as exc:
        logger.error(f"Unexpected error while fetching the daily index: {exc}")
        sys.exit(1)

    if result.total_found == 0:
        logger.info("No filings matched the form filter for this date. Nothing to store.")
        return

    if args.download_content and result.content_failed:
        logger.warning(
            f"Content download finished with {result.content_failed}/{result.total_found} failures."
        )


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


def _run_build_snapshots() -> None:
    logger.info("Building Document Intelligence snapshots...")
    result = run_snapshot_pipeline()
    if result.pending == 0:
        logger.info("  No filings pending a snapshot.")
        return
    skipped = result.pending - result.built - len(result.errors)
    logger.info(f"  Snapshots built: {result.built} | Skipped (already existed): {skipped}")
    if result.errors:
        logger.warning(f"  {len(result.errors)} filing(s) failed during snapshot generation.")


def _run_llm_first_pass(limit) -> None:
    logger.info("Running LLM first pass over pending EVENT filings...")
    try:
        result = run_llm_pipeline(limit=limit)
    except LLMConfigError as exc:
        logger.error(f"LLM first pass not run: {exc}")
        sys.exit(1)
    logger.info(f"  Processed: {result.processed} | Errors: {result.errors}")


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()

    if not any([
        args.date,
        args.import_sec_company_tickers,
        args.enrich_filings,
        args.import_otc_screener_csv,
        args.enrich_filings_with_otc,
        args.build_snapshots,
        args.llm_first_pass,
    ]):
        parser.error(
            "Specify at least one action: --date, --import-sec-company-tickers, "
            "--enrich-filings, --import-otc-screener-csv, --enrich-filings-with-otc, "
            "--build-snapshots, or --llm-first-pass."
        )

    user_agent = get_user_agent()
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

    if args.build_snapshots:
        _run_build_snapshots()

    if args.llm_first_pass:
        _run_llm_first_pass(args.limit)

    logger.info("Done.")


if __name__ == "__main__":
    main()
