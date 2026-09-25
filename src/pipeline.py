"""Orchestration layer shared by the CLI (main.py) and the Streamlit
dashboard's "Procesar filings" page.

Each function here is the extracted body of what used to live inline in
main.py's private _run_* functions — same logic, just parameterized (no
argparse.Namespace) and with an optional on_progress callback for live UI
updates. Importing this module has no side effects (no load_dotenv(), no
logging.basicConfig()) — that stays the caller's responsibility.

on_progress, when given, is called with a single dict per unit of work:
    {"stage": "content"|"llm", "done": int, "total": int,
     "ok": bool, "error": str | None}
Both the CLI and the dashboard read this same shape; each decides what to
do with it (the CLI logs it, the dashboard updates st.status/st.progress).
"""
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Dict, List, Optional

from .sec_ingestion.daily_index import get_filtered_filings
from .sec_ingestion.downloader import build_session, fetch_and_clean
from .filing_routing.routing import EVENT, CONTEXT, get_filing_category
from .database.db import (
    insert_filings,
    update_filing_content,
    get_filenames_with_content,
)
from .llm_analysis.first_pass import run_first_pass, DEFAULT_WORKERS as DEFAULT_LLM_WORKERS
from .llm_analysis.client import LLMConfigError

logger = logging.getLogger(__name__)

ProgressCallback = Optional[Callable[[Dict], None]]

_PLACEHOLDER_AGENT = "OTCFilingIntelligence contact@example.com"


def _emit(on_progress: ProgressCallback, **data) -> None:
    if on_progress:
        on_progress(data)


def get_user_agent() -> str:
    """Read SEC_USER_AGENT from the environment, falling back to a labeled
    placeholder (and logging a warning) if it isn't set."""
    agent = os.getenv("SEC_USER_AGENT", "").strip()
    if not agent:
        logger.warning(
            "SEC_USER_AGENT is not set. Using a placeholder User-Agent. "
            'Set it in .env: SEC_USER_AGENT="YourName youremail@example.com"'
        )
        return _PLACEHOLDER_AGENT
    return agent


@dataclass
class DailyPipelineResult:
    target_date: date
    total_found: int = 0
    event_count: int = 0
    context_count: int = 0
    inserted: int = 0
    already_existed: int = 0
    content_downloaded: int = 0
    content_skipped_existing: int = 0
    content_failed: int = 0
    content_bytes_downloaded: int = 0
    content_bytes_stored: int = 0
    errors: List[str] = field(default_factory=list)


def run_daily_pipeline(
    target_date: date,
    user_agent: str,
    download_content: bool = False,
    on_progress: ProgressCallback = None,
) -> DailyPipelineResult:
    """Fetch the SEC daily index for target_date, store EVENT/CONTEXT
    filings, and optionally download+clean each filing's full text.

    Raises FileNotFoundError if no index exists for that date (e.g. a
    weekend/holiday) and any other exception the index fetch/parse raises —
    same as get_filtered_filings always has; callers handle it the same way
    _run_daily_pipeline's caller (main()) already does.
    """
    result = DailyPipelineResult(target_date=target_date)

    filings = get_filtered_filings(target_date, user_agent)
    result.total_found = len(filings)
    result.event_count = sum(1 for f in filings if get_filing_category(f.form_type) == EVENT)
    result.context_count = sum(1 for f in filings if get_filing_category(f.form_type) == CONTEXT)
    _emit(
        on_progress, stage="index", done=1, total=1, ok=True, error=None,
        total_found=result.total_found, event_count=result.event_count, context_count=result.context_count,
    )

    if not filings:
        return result

    inserted, skipped = insert_filings(filings)
    result.inserted = inserted
    result.already_existed = skipped
    _emit(
        on_progress, stage="store", done=1, total=1, ok=True, error=None,
        inserted=inserted, already_existed=skipped,
    )

    if download_content:
        total = len(filings)
        already_have_content = get_filenames_with_content([f.filename for f in filings])
        session = build_session(user_agent)
        started = time.monotonic()
        attempted = 0

        for idx, filing in enumerate(filings, start=1):
            if filing.filename in already_have_content:
                result.content_skipped_existing += 1
                _emit(
                    on_progress, stage="content", done=idx, total=total, ok=True, error=None,
                    label=f"{filing.form_type} — {filing.company_name}", skipped=True,
                    bytes_downloaded=result.content_bytes_downloaded,
                    bytes_stored=result.content_bytes_stored,
                    elapsed_seconds=time.monotonic() - started,
                    speed_bytes_per_sec=0.0, eta_seconds=0.0,
                )
                continue

            error: Optional[str] = None
            downloaded_size = 0
            stored_size = 0
            try:
                raw, cleaned, downloaded_size, stored_size = fetch_and_clean(
                    filing.filing_url, user_agent, session=session, expected_type=filing.form_type,
                )
            except Exception as exc:
                raw = None
                error = f"{filing.filename}: {exc}"

            if raw is not None:
                update_filing_content(filing.filename, raw, cleaned or "")
                result.content_downloaded += 1
                result.content_bytes_downloaded += downloaded_size
                result.content_bytes_stored += stored_size
            else:
                result.content_failed += 1
                error = error or f"{filing.filename}: download failed"
                result.errors.append(error)

            attempted += 1
            elapsed = time.monotonic() - started
            speed = result.content_bytes_downloaded / elapsed if elapsed > 0 else 0.0
            avg_seconds_per_item = elapsed / attempted if attempted else 0.0
            eta = avg_seconds_per_item * (total - idx)

            _emit(
                on_progress,
                stage="content",
                done=idx,
                total=total,
                ok=error is None,
                error=error,
                label=f"{filing.form_type} — {filing.company_name}",
                skipped=False,
                bytes_downloaded=result.content_bytes_downloaded,
                bytes_stored=result.content_bytes_stored,
                elapsed_seconds=elapsed,
                speed_bytes_per_sec=speed,
                eta_seconds=eta,
            )

    return result


@dataclass
class LLMPipelineResult:
    pending: int = 0
    processed: int = 0
    errors: int = 0


def run_llm_pipeline(
    limit: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    form_types: Optional[List[str]] = None,
    status: str = "pending",
    order: str = "recent",
    workers: int = DEFAULT_LLM_WORKERS,
    on_progress: ProgressCallback = None,
) -> LLMPipelineResult:
    """Run the LLM first pass over a selection of EVENT filings.

    date_from/date_to/form_types/status/order select the candidates — see
    llm_analysis.first_pass.run_first_pass, which this delegates to
    entirely (no part of that loop is reimplemented here). Defaults match
    the original "all pending EVENT filings" behavior. Raises
    LLMConfigError unchanged if LLM_API_KEY/LLM_BASE_URL/LLM_MODEL aren't
    configured; callers decide how to surface that (main() exits, the
    dashboard shows an inline warning).

    workers: number of concurrent LLM HTTP calls (see run_first_pass) —
    forwarded as-is, same default.
    """
    result = LLMPipelineResult()

    def _forward(done: int, total: int, ok: bool, error: Optional[str], info: Optional[Dict]) -> None:
        result.pending = total
        extra = info or {}
        _emit(on_progress, stage="llm", done=done, total=total, ok=ok, error=error, **extra)

    processed, errors = run_first_pass(
        limit=limit, date_from=date_from, date_to=date_to, form_types=form_types,
        status=status, order=order, workers=workers, on_progress=_forward,
    )
    result.processed = processed
    result.errors = errors
    return result
