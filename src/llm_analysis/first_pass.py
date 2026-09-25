import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional, Tuple

from .client import LLMClient, LLMResponse
from .prompts import SYSTEM_PROMPT, build_user_message
from ..database.db import (
    get_event_filings_needing_llm_analysis,
    insert_llm_filing_analysis,
)

logger = logging.getLogger(__name__)

MAX_CLEAN_TEXT_CHARS = 20000
PARSE_ERROR = "PARSE_ERROR"
DEFAULT_WORKERS = 5


def _load_json_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return []


def build_filing_input(filing: Dict) -> Dict:
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


_MARKDOWN_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL)


def _strip_markdown_fence(raw: str) -> str:
    stripped = raw.strip()
    match = _MARKDOWN_FENCE_RE.match(stripped)
    return match.group(1).strip() if match else stripped


def parse_llm_response(raw_content: str) -> Dict:
    try:
        parsed = json.loads(_strip_markdown_fence(raw_content or ""))
    except (TypeError, ValueError) as exc:
        logger.error(f"Failed to parse LLM response as JSON: {exc}")
        return _parse_error_result(f"JSON parse error: {exc}")
    if not isinstance(parsed, dict):
        logger.error(f"LLM response was valid JSON but not an object: {raw_content!r}")
        return _parse_error_result("JSON parse error: response was not a JSON object")
    return parsed


def _call_llm(client: LLMClient, filing: Dict) -> Tuple[Dict, Optional[LLMResponse], Optional[str]]:
    filing_input = build_filing_input(filing)
    user_message = build_user_message(filing_input)
    try:
        response = client.chat_completion(SYSTEM_PROMPT, user_message)
        return filing, response, None
    except Exception as exc:
        return filing, None, str(exc)


def run_first_pass(
    limit: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    form_types: Optional[List[str]] = None,
    status: str = "pending",
    order: str = "recent",
    category: Optional[str] = None,
    workers: int = DEFAULT_WORKERS,
    on_progress: Optional[Callable[[int, int, bool, Optional[str], Optional[Dict]], None]] = None,
) -> tuple[int, int]:
    if workers < 1:
        raise ValueError(f"workers must be >= 1 (got {workers})")

    client = LLMClient()
    kwargs = dict(limit=limit, date_from=date_from, date_to=date_to, form_types=form_types, status=status, order=order)
    if category is not None:
        kwargs["category"] = category
    pending = get_event_filings_needing_llm_analysis(**kwargs)
    total = len(pending)
    logger.info(f"LLM first pass: {total} filing(s) pending ({workers} worker(s)).")

    processed = errors = 0
    if total == 0:
        return processed, errors

    started = time.monotonic()

    def _timing_fields(done: int) -> Dict:
        elapsed = time.monotonic() - started
        speed_per_min = (done / elapsed) * 60 if elapsed > 0 else 0.0
        avg_seconds_per_item = elapsed / done if done else 0.0
        return {
            "workers": workers,
            "elapsed_seconds": elapsed,
            "speed_per_min": speed_per_min,
            "eta_seconds": avg_seconds_per_item * (total - done),
        }

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_call_llm, client, filing) for filing in pending]
        for done, future in enumerate(as_completed(futures), start=1):
            filing, response, error = future.result()
            info = _timing_fields(done)

            if error is not None:
                logger.error(f"LLM call failed for '{filing['filename']}': {error}")
                errors += 1
                if on_progress:
                    on_progress(done, total, False, error, info)
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
                info.update({
                    "company_name": filing.get("company_name"),
                    "primary_event_type": parsed.get("primary_event_type"),
                    "importance_score": parsed.get("importance_score"),
                    "deep_research": bool(parsed.get("deep_research")),
                })
                on_progress(done, total, True, None, info)

    return processed, errors
