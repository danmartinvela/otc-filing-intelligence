import json
import logging
from typing import Callable, Dict, List, Optional

from .client import LLMClient
from .prompts import SYSTEM_PROMPT, build_user_message
from ..database.db import (
    get_event_filings_needing_llm_analysis,
    insert_llm_filing_analysis,
)

logger = logging.getLogger(__name__)

MAX_CLEAN_TEXT_CHARS = 25000
PARSE_ERROR = "PARSE_ERROR"


def _load_json_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return []


def build_filing_input(filing: Dict) -> Dict:
    """Build the compact, LLM-ready input for a filing row from the database.

    keywords_json is included only as optional context — it must never be the
    primary signal for classification (that's the whole point of this module).
    """
    clean_text = (filing.get("clean_text") or "")[:MAX_CLEAN_TEXT_CHARS]
    return {
        "company_name": filing.get("company_name"),
        "ticker": filing.get("ticker"),
        "form_type": filing.get("form_type"),
        "filing_url": filing.get("filing_url"),
        "items": _load_json_list(filing.get("items_json")),
        "keywords": _load_json_list(filing.get("keywords_json")),
        "clean_text": clean_text,
    }


def _parse_error_result(reason: str) -> Dict:
    return {
        "primary_event_type": PARSE_ERROR,
        "secondary_event_types": [],
        "is_material": False,
        "importance_score": 0,
        "market_impact": None,
        "deep_research": False,
        "summary": "",
        "key_entities": [],
        "evidence": [],
        "reason_for_score": reason,
        "next_step": None,
    }


def parse_llm_response(raw_content: str) -> Dict:
    """Parse the LLM's JSON response. Never raises — falls back to PARSE_ERROR.

    On success, passes through all fields returned by the model, including
    market_impact, next_step, and key_entities.
    """
    try:
        parsed = json.loads(raw_content)
    except (TypeError, ValueError) as exc:
        logger.error(f"Failed to parse LLM response as JSON: {exc}")
        return _parse_error_result(f"JSON parse error: {exc}")
    if not isinstance(parsed, dict):
        logger.error(f"LLM response was valid JSON but not an object: {raw_content!r}")
        return _parse_error_result("JSON parse error: response was not a JSON object")
    return parsed


def run_first_pass(
    limit: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    form_types: Optional[List[str]] = None,
    status: str = "pending",
    order: str = "recent",
    category: Optional[str] = None,
    on_progress: Optional[Callable[[int, int, bool, Optional[str], Optional[Dict]], None]] = None,
) -> tuple[int, int]:
    """Run the LLM first pass over a selection of EVENT filings. Returns (processed, errors).

    date_from/date_to/form_types/status/order/category select which filings
    are candidates (see database.db.get_event_filings_needing_llm_analysis —
    the single source of truth for that selection, shared with the preview
    counts/table so the CLI, the dashboard preview, and this run never
    disagree). None of the new parameters change behavior when omitted:
    defaults match the original "all pending EVENT filings" selection.

    on_progress, if given, is called once per filing as
    on_progress(done, total, ok, error, info) — after the attempt. info is
    {"company_name", "primary_event_type", "importance_score", "deep_research"}
    on success, None on failure. Optional and unused by the CLI, so passing
    nothing keeps this function's behavior exactly as before.
    """
    client = LLMClient()
    kwargs = dict(limit=limit, date_from=date_from, date_to=date_to, form_types=form_types, status=status, order=order)
    if category is not None:
        kwargs["category"] = category
    pending = get_event_filings_needing_llm_analysis(**kwargs)
    total = len(pending)
    logger.info(f"LLM first pass: {total} filing(s) pending.")

    processed = errors = 0
    for idx, filing in enumerate(pending, start=1):
        filing_input = build_filing_input(filing)
        user_message = build_user_message(filing_input)
        error: Optional[str] = None
        try:
            response = client.chat_completion(SYSTEM_PROMPT, user_message)
        except Exception as exc:
            logger.error(f"LLM call failed for '{filing['filename']}': {exc}")
            errors += 1
            error = str(exc)
            if on_progress:
                on_progress(idx, total, False, error, None)
            continue

        parsed = parse_llm_response(response.content)
        insert_llm_filing_analysis(
            filing_filename=filing["filename"],
            provider=client.provider,
            model=client.model,
            parsed=parsed,
            raw_response=response.content,
        )
        processed += 1
        if on_progress:
            info = {
                "company_name": filing.get("company_name"),
                "primary_event_type": parsed.get("primary_event_type"),
                "importance_score": parsed.get("importance_score"),
                "deep_research": bool(parsed.get("deep_research")),
            }
            on_progress(idx, total, True, None, info)

    return processed, errors
