import json
import sqlite3
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.database.db import (
    get_connection,
    get_event_filings_needing_llm_analysis,
    get_llm_filing_analysis,
    init_db,
    init_llm_filing_analysis_table,
    insert_filings,
    insert_llm_filing_analysis,
)
from src.database.models import Filing
from src.llm_analysis.client import LLMClient, LLMConfigError, LLMResponse
from src.llm_analysis.first_pass import (
    MAX_CLEAN_TEXT_CHARS,
    PARSE_ERROR,
    build_filing_input,
    parse_llm_response,
    run_first_pass,
)
from src.llm_analysis.prompts import EVENT_TYPES, SYSTEM_PROMPT, build_user_message


# ── prompts ──────────────────────────────────────────────────────────────────


def test_system_prompt_contains_all_event_types():
    for event_type in EVENT_TYPES:
        assert event_type in SYSTEM_PROMPT


def test_system_prompt_requests_strict_json():
    assert "JSON" in SYSTEM_PROMPT


def test_build_user_message_includes_core_fields():
    filing_input = {
        "company_name": "ABC Holdings Inc.",
        "ticker": "ABCH",
        "form_type": "8-K",
        "filing_url": "https://www.sec.gov/Archives/edgar/data/1/a.txt",
        "items": ["2.01", "5.02"],
        "keywords": ["Nasdaq"],
        "clean_text": "Item 2.01 Completion of Acquisition.",
    }
    message = build_user_message(filing_input)
    assert "ABC Holdings Inc." in message
    assert "ABCH" in message
    assert "8-K" in message
    assert "2.01, 5.02" in message
    assert "Item 2.01 Completion of Acquisition." in message


def test_build_user_message_handles_missing_optional_fields():
    filing_input = {"company_name": None, "form_type": "8-K", "clean_text": ""}
    message = build_user_message(filing_input)
    assert "Unknown" not in message or "N/A" in message  # no crash either way
    assert "8-K" in message


# ── parse_llm_response ───────────────────────────────────────────────────────


def test_parse_llm_response_valid_json():
    raw = json.dumps({
        "primary_event_type": "MERGER",
        "secondary_event_types": ["CHANGE_OF_CONTROL"],
        "is_material": True,
        "importance_score": 90,
        "market_impact": "VERY_HIGH",
        "deep_research": True,
        "summary": "Company X merges with Company Y.",
        "key_entities": ["Company Y", "$500 million"],
        "evidence": ["Merger Agreement dated June 1"],
        "reason_for_score": "Control change with material valuation impact.",
        "next_step": "RESEARCH",
    })
    parsed = parse_llm_response(raw)
    assert parsed["primary_event_type"] == "MERGER"
    assert parsed["is_material"] is True
    assert parsed["importance_score"] == 90
    assert parsed["market_impact"] == "VERY_HIGH"
    assert parsed["deep_research"] is True
    assert parsed["key_entities"] == ["Company Y", "$500 million"]
    assert parsed["evidence"] == ["Merger Agreement dated June 1"]
    assert parsed["next_step"] == "RESEARCH"


def test_parse_llm_response_invalid_json_returns_parse_error():
    parsed = parse_llm_response("not valid json {{{")
    assert parsed["primary_event_type"] == PARSE_ERROR
    assert parsed["is_material"] is False
    assert parsed["importance_score"] == 0
    assert parsed["market_impact"] is None
    assert parsed["deep_research"] is False
    assert parsed["key_entities"] == []
    assert parsed["next_step"] is None
    assert "JSON parse error" in parsed["reason_for_score"]


def test_parse_llm_response_json_array_is_not_a_valid_object():
    parsed = parse_llm_response("[1, 2, 3]")
    assert parsed["primary_event_type"] == PARSE_ERROR


def test_parse_llm_response_empty_string():
    parsed = parse_llm_response("")
    assert parsed["primary_event_type"] == PARSE_ERROR


def test_parse_llm_response_none_is_a_parse_error_not_a_crash():
    parsed = parse_llm_response(None)
    assert parsed["primary_event_type"] == PARSE_ERROR


# ── parse_llm_response: markdown-fenced JSON ─────────────────────────────────
#
# Observed for real from google/gemini-2.5-flash-lite via OpenRouter: it
# wraps its JSON reply in a ```json ... ``` code fence even though the
# prompt asks for raw JSON. A string starting with a backtick fails
# json.loads with the exact same "Expecting value: line 1 column 1 (char 0)"
# error as an empty string — without stripping the fence, every response
# from a model that does this gets misreported as PARSE_ERROR.


def test_parse_llm_response_strips_json_language_tagged_fence():
    raw = '```json\n{"primary_event_type": "ROUTINE", "importance_score": 10}\n```'
    parsed = parse_llm_response(raw)
    assert parsed["primary_event_type"] == "ROUTINE"
    assert parsed["importance_score"] == 10


def test_parse_llm_response_strips_untagged_fence():
    raw = '```\n{"primary_event_type": "MERGER"}\n```'
    parsed = parse_llm_response(raw)
    assert parsed["primary_event_type"] == "MERGER"


def test_parse_llm_response_still_handles_unfenced_json():
    """Most providers return raw JSON — the fix must not regress that path."""
    raw = '{"primary_event_type": "BANKRUPTCY_DISTRESS"}'
    parsed = parse_llm_response(raw)
    assert parsed["primary_event_type"] == "BANKRUPTCY_DISTRESS"


def test_parse_llm_response_fenced_but_invalid_json_is_still_a_parse_error():
    raw = '```json\nnot actually json\n```'
    parsed = parse_llm_response(raw)
    assert parsed["primary_event_type"] == PARSE_ERROR


# ── build_filing_input ───────────────────────────────────────────────────────


def test_build_filing_input_truncates_clean_text():
    filing = {
        "filename": "a.txt",
        "company_name": "ABC",
        "ticker": "ABC",
        "form_type": "8-K",
        "filing_url": "https://sec.gov/a.txt",
        "clean_text": "x" * 30000,
        "items_json": '["2.01"]',
        "keywords_json": '["Nasdaq"]',
    }
    filing_input = build_filing_input(filing)
    assert len(filing_input["clean_text"]) == MAX_CLEAN_TEXT_CHARS
    assert filing_input["items"] == ["2.01"]
    assert filing_input["keywords"] == ["Nasdaq"]


def test_build_filing_input_handles_missing_json_fields():
    filing = {
        "filename": "a.txt",
        "company_name": "ABC",
        "ticker": None,
        "form_type": "8-K",
        "filing_url": "https://sec.gov/a.txt",
        "clean_text": "some text",
        "items_json": None,
        "keywords_json": None,
    }
    filing_input = build_filing_input(filing)
    assert filing_input["items"] == []
    assert filing_input["keywords"] == []


# ── LLMClient config ─────────────────────────────────────────────────────────


def test_llm_client_raises_when_env_vars_missing(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    with pytest.raises(LLMConfigError):
        LLMClient()


def test_llm_client_accepts_explicit_args():
    client = LLMClient(api_key="key", base_url="https://api.example.com/v1", model="test-model")
    assert client.api_key == "key"
    assert client.base_url == "https://api.example.com/v1"
    assert client.model == "test-model"
    assert client.provider == "openai-compatible"


def test_llm_client_strips_trailing_slash_from_base_url():
    client = LLMClient(api_key="key", base_url="https://api.example.com/v1/", model="m")
    assert client.base_url == "https://api.example.com/v1"


@patch("src.llm_analysis.client.requests.post")
def test_llm_client_chat_completion_parses_response(mock_post):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": '{"primary_event_type": "ROUTINE"}'}}]
    }
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    client = LLMClient(api_key="key", base_url="https://api.example.com/v1", model="m")
    result = client.chat_completion("system prompt", "user message")

    assert isinstance(result, LLMResponse)
    assert result.content == '{"primary_event_type": "ROUTINE"}'
    called_url = mock_post.call_args.args[0]
    assert called_url == "https://api.example.com/v1/chat/completions"
    called_payload = mock_post.call_args.kwargs["json"]
    assert called_payload["model"] == "m"
    assert called_payload["messages"][0] == {"role": "system", "content": "system prompt"}


# ── SQLite persistence ────────────────────────────────────────────────────────


def _make_filing(filename: str, clean_text: str = "text", form_type: str = "8-K") -> Filing:
    return Filing(
        cik="320193",
        company_name="Test Corp",
        form_type=form_type,
        date_filed="2024-05-15",
        filename=filename,
        filing_url=f"https://www.sec.gov/Archives/{filename}",
        clean_text=clean_text,
    )


def test_init_db_creates_llm_filing_analysis_table(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    with get_connection(db_path) as conn:
        tables = [
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
    assert "llm_filing_analysis" in tables


def test_insert_llm_filing_analysis_and_read_back(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    parsed = {
        "primary_event_type": "MERGER",
        "secondary_event_types": ["CHANGE_OF_CONTROL"],
        "is_material": True,
        "importance_score": 85,
        "market_impact": "HIGH",
        "deep_research": True,
        "summary": "Merger disclosed.",
        "key_entities": ["BlackRock", "$500 million"],
        "evidence": ["Item 2.01"],
        "reason_for_score": "Material control change.",
        "next_step": "RESEARCH",
    }
    inserted = insert_llm_filing_analysis(
        filing_filename="a.txt",
        provider="openai-compatible",
        model="gpt-test",
        parsed=parsed,
        raw_response=json.dumps(parsed),
        db_path=db_path,
    )
    assert inserted is True

    stored = get_llm_filing_analysis("a.txt", db_path)
    assert stored["primary_event_type"] == "MERGER"
    assert stored["secondary_event_types"] == ["CHANGE_OF_CONTROL"]
    assert stored["is_material"] == 1
    assert stored["importance_score"] == 85
    assert stored["market_impact"] == "HIGH"
    assert stored["deep_research"] == 1
    assert stored["key_entities"] == ["BlackRock", "$500 million"]
    assert stored["evidence"] == ["Item 2.01"]
    assert stored["next_step"] == "RESEARCH"


def test_llm_filing_analysis_queryable_by_next_step_and_importance(tmp_path):
    """New columns must be plain SQL-queryable, not just accessible via get_llm_filing_analysis."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    insert_llm_filing_analysis(
        "high.txt", "p", "m",
        {"primary_event_type": "MERGER", "importance_score": 95,
         "market_impact": "VERY_HIGH", "next_step": "RESEARCH"},
        "{}", db_path,
    )
    insert_llm_filing_analysis(
        "low.txt", "p", "m",
        {"primary_event_type": "ROUTINE", "importance_score": 10,
         "market_impact": "LOW", "next_step": "IGNORE"},
        "{}", db_path,
    )
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT filing_filename, importance_score, market_impact
            FROM llm_filing_analysis
            WHERE next_step = 'RESEARCH'
            ORDER BY importance_score DESC
            """
        ).fetchall()
    assert [r["filing_filename"] for r in rows] == ["high.txt"]
    assert rows[0]["market_impact"] == "VERY_HIGH"


def test_insert_llm_filing_analysis_prevents_duplicates(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    parsed = {"primary_event_type": "ROUTINE"}

    first = insert_llm_filing_analysis("a.txt", "p", "m", parsed, "{}", db_path)
    second = insert_llm_filing_analysis("a.txt", "p", "m", parsed, "{}", db_path)

    assert first is True
    assert second is False
    with get_connection(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM llm_filing_analysis").fetchone()[0]
    assert count == 1


def test_get_llm_filing_analysis_not_found_returns_none(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    assert get_llm_filing_analysis("does/not/exist.txt", db_path) is None


# ── schema migration ──────────────────────────────────────────────────────────


def test_init_db_migrates_old_llm_filing_analysis_table(tmp_path):
    """Upgrading a pre-market_impact/next_step/key_entities DB adds those columns
    without losing existing rows."""
    db_path = tmp_path / "test.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE llm_filing_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filing_filename TEXT NOT NULL UNIQUE,
                provider TEXT,
                model TEXT,
                primary_event_type TEXT,
                secondary_event_types_json TEXT,
                is_material INTEGER,
                importance_score INTEGER,
                deep_research INTEGER,
                summary TEXT,
                evidence_json TEXT,
                reason_for_score TEXT,
                raw_response TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """INSERT INTO llm_filing_analysis
               (filing_filename, provider, model, primary_event_type,
                secondary_event_types_json, is_material, importance_score,
                deep_research, summary, evidence_json, reason_for_score,
                raw_response, created_at)
               VALUES ('old.txt', 'p', 'm', 'ROUTINE', '[]', 0, 10, 0, 'old row',
                       '[]', 'legacy', '{}', '2024-01-01')"""
        )

    init_db(db_path)

    with get_connection(db_path) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(llm_filing_analysis)")}
    assert "market_impact" in cols
    assert "next_step" in cols
    assert "key_entities_json" in cols

    # Existing row must survive the migration untouched, with NULL new columns.
    stored = get_llm_filing_analysis("old.txt", db_path)
    assert stored["primary_event_type"] == "ROUTINE"
    assert stored["market_impact"] is None
    assert stored["next_step"] is None
    assert stored["key_entities"] == []


def test_init_llm_filing_analysis_table_migrates_directly(tmp_path):
    """init_llm_filing_analysis_table alone (not just init_db) must also migrate."""
    db_path = tmp_path / "test.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE llm_filing_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filing_filename TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
            """
        )
    init_llm_filing_analysis_table(db_path)
    with get_connection(db_path) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(llm_filing_analysis)")}
    assert {"market_impact", "next_step", "key_entities_json"} <= cols


def test_get_event_filings_needing_llm_analysis_excludes_context_and_ignored(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    insert_filings(
        [
            _make_filing("event.txt", form_type="8-K"),
            _make_filing("context.txt", form_type="10-K"),
            _make_filing("ignored.txt", form_type="4"),
        ],
        db_path,
    )
    pending = get_event_filings_needing_llm_analysis(db_path=db_path)
    assert [p["filename"] for p in pending] == ["event.txt"]


def test_get_event_filings_needing_llm_analysis_excludes_already_analyzed(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    insert_filings([_make_filing("event.txt")], db_path)
    insert_llm_filing_analysis("event.txt", "p", "m", {"primary_event_type": "ROUTINE"}, "{}", db_path)

    pending = get_event_filings_needing_llm_analysis(db_path=db_path)
    assert pending == []


def test_get_event_filings_needing_llm_analysis_respects_limit(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    insert_filings(
        [_make_filing(f"event{i}.txt") for i in range(5)],
        db_path,
    )
    pending = get_event_filings_needing_llm_analysis(limit=2, db_path=db_path)
    assert len(pending) == 2


def test_get_event_filings_needing_llm_analysis_has_no_document_snapshots_dependency(tmp_path):
    """Document Snapshots was removed entirely — the LLM selection query
    must feed off filings.clean_text alone, with no join or column tying it
    to a filing_snapshots table (which no longer even exists for a fresh DB)."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    insert_filings([_make_filing("event.txt", clean_text="Item 2.01 details.")], db_path)

    with get_connection(db_path) as conn:
        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "filing_snapshots" not in tables

    pending = get_event_filings_needing_llm_analysis(db_path=db_path)
    assert pending[0]["filename"] == "event.txt"
    assert pending[0]["clean_text"] == "Item 2.01 details."
    assert "items_json" not in pending[0]
    assert "keywords_json" not in pending[0]


# ── run_first_pass orchestration ─────────────────────────────────────────────


@patch("src.llm_analysis.first_pass.LLMClient")
def test_run_first_pass_processes_pending_filings_and_saves_results(mock_client_cls, tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    insert_filings([_make_filing("event.txt", clean_text="Item 2.01 details.")], db_path)

    monkeypatch.setattr("src.llm_analysis.first_pass.get_event_filings_needing_llm_analysis",
                         lambda **kwargs: get_event_filings_needing_llm_analysis(db_path=db_path, **kwargs))
    monkeypatch.setattr("src.llm_analysis.first_pass.insert_llm_filing_analysis",
                         lambda **kwargs: insert_llm_filing_analysis(db_path=db_path, **kwargs))

    mock_client = MagicMock()
    mock_client.provider = "openai-compatible"
    mock_client.model = "test-model"
    mock_client.chat_completion.return_value = LLMResponse(
        content=json.dumps({"primary_event_type": "ROUTINE", "importance_score": 5}),
        raw_response={},
    )
    mock_client_cls.return_value = mock_client

    processed, errors = run_first_pass(limit=10)

    assert processed == 1
    assert errors == 0
    stored = get_llm_filing_analysis("event.txt", db_path)
    assert stored["primary_event_type"] == "ROUTINE"


@patch("src.llm_analysis.first_pass.LLMClient")
def test_run_first_pass_counts_errors_without_raising(mock_client_cls, tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    insert_filings([_make_filing("event.txt", clean_text="Item 2.01 details.")], db_path)

    monkeypatch.setattr("src.llm_analysis.first_pass.get_event_filings_needing_llm_analysis",
                         lambda **kwargs: get_event_filings_needing_llm_analysis(db_path=db_path, **kwargs))
    monkeypatch.setattr("src.llm_analysis.first_pass.insert_llm_filing_analysis",
                         lambda **kwargs: insert_llm_filing_analysis(db_path=db_path, **kwargs))

    mock_client = MagicMock()
    mock_client.chat_completion.side_effect = RuntimeError("API down")
    mock_client_cls.return_value = mock_client

    processed, errors = run_first_pass(limit=10)

    assert processed == 0
    assert errors == 1
    assert get_llm_filing_analysis("event.txt", db_path) is None


# ── LLMClient retry/backoff ──────────────────────────────────────────────────


def _http_error_response(status_code, retry_after=None):
    """A mock requests.Response whose raise_for_status() raises an HTTPError
    carrying that status code — like the real requests library does."""
    response = MagicMock()
    response.status_code = status_code
    response.headers = {"Retry-After": retry_after} if retry_after else {}
    error = requests.HTTPError(f"{status_code} error")
    error.response = response
    response.raise_for_status.side_effect = error
    return response


def _success_response(content="ok"):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": {"content": content}}]}
    return response


@patch("src.llm_analysis.client.time.sleep")
@patch("src.llm_analysis.client.requests.post")
def test_chat_completion_retries_on_429_then_succeeds(mock_post, mock_sleep):
    mock_post.side_effect = [_http_error_response(429), _success_response()]
    client = LLMClient(api_key="k", base_url="https://api.example.com/v1", model="m")

    result = client.chat_completion("sp", "um")

    assert result.content == "ok"
    assert mock_post.call_count == 2
    mock_sleep.assert_called_once()


@patch("src.llm_analysis.client.time.sleep")
@patch("src.llm_analysis.client.requests.post")
def test_chat_completion_honors_retry_after_header(mock_post, mock_sleep):
    mock_post.side_effect = [_http_error_response(429, retry_after="5"), _success_response()]
    client = LLMClient(api_key="k", base_url="https://api.example.com/v1", model="m")

    client.chat_completion("sp", "um")

    mock_sleep.assert_called_once_with(5.0)


@patch("src.llm_analysis.client.time.sleep")
@patch("src.llm_analysis.client.requests.post")
def test_chat_completion_retry_after_header_is_capped(mock_post, mock_sleep):
    """A provider asking for an absurd Retry-After must not stall the batch."""
    mock_post.side_effect = [_http_error_response(429, retry_after="9999"), _success_response()]
    client = LLMClient(api_key="k", base_url="https://api.example.com/v1", model="m")

    client.chat_completion("sp", "um")

    mock_sleep.assert_called_once_with(30.0)


@patch("src.llm_analysis.client.time.sleep")
@patch("src.llm_analysis.client.requests.post")
def test_chat_completion_retries_on_5xx_then_succeeds(mock_post, mock_sleep):
    mock_post.side_effect = [_http_error_response(503), _http_error_response(502), _success_response()]
    client = LLMClient(api_key="k", base_url="https://api.example.com/v1", model="m")

    result = client.chat_completion("sp", "um")

    assert result.content == "ok"
    assert mock_post.call_count == 3
    assert mock_sleep.call_count == 2


@patch("src.llm_analysis.client.time.sleep")
@patch("src.llm_analysis.client.requests.post")
def test_chat_completion_retries_on_timeout_then_succeeds(mock_post, mock_sleep):
    mock_post.side_effect = [requests.exceptions.Timeout("timed out"), _success_response()]
    client = LLMClient(api_key="k", base_url="https://api.example.com/v1", model="m")

    result = client.chat_completion("sp", "um")

    assert result.content == "ok"
    assert mock_post.call_count == 2


@patch("src.llm_analysis.client.time.sleep")
@patch("src.llm_analysis.client.requests.post")
def test_chat_completion_retries_on_connection_error_then_succeeds(mock_post, mock_sleep):
    mock_post.side_effect = [requests.exceptions.ConnectionError("connection reset by peer"), _success_response()]
    client = LLMClient(api_key="k", base_url="https://api.example.com/v1", model="m")

    result = client.chat_completion("sp", "um")

    assert result.content == "ok"
    assert mock_post.call_count == 2


@patch("src.llm_analysis.client.time.sleep")
@patch("src.llm_analysis.client.requests.post")
def test_chat_completion_does_not_retry_4xx_other_than_429(mock_post, mock_sleep):
    mock_post.return_value = _http_error_response(400)
    client = LLMClient(api_key="k", base_url="https://api.example.com/v1", model="m")

    with pytest.raises(requests.HTTPError):
        client.chat_completion("sp", "um")

    assert mock_post.call_count == 1
    mock_sleep.assert_not_called()


@patch("src.llm_analysis.client.time.sleep")
@patch("src.llm_analysis.client.requests.post")
def test_chat_completion_gives_up_after_max_retries(mock_post, mock_sleep):
    mock_post.return_value = _http_error_response(503)
    client = LLMClient(api_key="k", base_url="https://api.example.com/v1", model="m")

    with pytest.raises(requests.HTTPError):
        client.chat_completion("sp", "um")

    assert mock_post.call_count == 4  # 1 initial attempt + 3 retries


# ── run_first_pass concurrency ───────────────────────────────────────────────


def _install_real_db(monkeypatch, db_path):
    """Point first_pass's module-level DB calls at a throwaway tmp_path DB
    instead of the real data/filings.db, while keeping the real SQL (no
    behavior is mocked away, only which file it reads/writes)."""
    monkeypatch.setattr(
        "src.llm_analysis.first_pass.get_event_filings_needing_llm_analysis",
        lambda **kwargs: get_event_filings_needing_llm_analysis(db_path=db_path, **kwargs),
    )
    monkeypatch.setattr(
        "src.llm_analysis.first_pass.insert_llm_filing_analysis",
        lambda **kwargs: insert_llm_filing_analysis(db_path=db_path, **kwargs),
    )


def test_run_first_pass_rejects_invalid_worker_count():
    with pytest.raises(ValueError):
        run_first_pass(workers=0)


@patch("src.llm_analysis.first_pass.LLMClient")
def test_run_first_pass_runs_calls_concurrently(mock_client_cls, tmp_path, monkeypatch):
    """4 filings whose LLM call each sleeps 0.2s, run with workers=4, must
    finish in well under 4 * 0.2s — proving the calls actually overlap
    instead of the old one-at-a-time loop."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    insert_filings([_make_filing(f"event{i}.txt") for i in range(4)], db_path)
    _install_real_db(monkeypatch, db_path)

    call_duration = 0.2

    def _slow_chat_completion(system_prompt, user_message):
        time.sleep(call_duration)
        return LLMResponse(content=json.dumps({"primary_event_type": "ROUTINE"}), raw_response={})

    mock_client = MagicMock()
    mock_client.provider = "openai-compatible"
    mock_client.model = "test-model"
    mock_client.chat_completion.side_effect = _slow_chat_completion
    mock_client_cls.return_value = mock_client

    started = time.monotonic()
    processed, errors = run_first_pass(limit=10, workers=4)
    elapsed = time.monotonic() - started

    assert processed == 4
    assert errors == 0
    assert elapsed < call_duration * 2.5


@patch("src.llm_analysis.first_pass.LLMClient")
def test_run_first_pass_never_exceeds_configured_workers(mock_client_cls, tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    insert_filings([_make_filing(f"event{i}.txt") for i in range(10)], db_path)
    _install_real_db(monkeypatch, db_path)

    lock = threading.Lock()
    state = {"current": 0, "max_seen": 0}

    def _tracking_chat_completion(system_prompt, user_message):
        with lock:
            state["current"] += 1
            state["max_seen"] = max(state["max_seen"], state["current"])
        time.sleep(0.05)
        with lock:
            state["current"] -= 1
        return LLMResponse(content=json.dumps({"primary_event_type": "ROUTINE"}), raw_response={})

    mock_client = MagicMock()
    mock_client.provider = "openai-compatible"
    mock_client.model = "test-model"
    mock_client.chat_completion.side_effect = _tracking_chat_completion
    mock_client_cls.return_value = mock_client

    processed, errors = run_first_pass(limit=10, workers=3)

    assert processed == 10
    assert errors == 0
    assert state["max_seen"] == 3


@patch("src.llm_analysis.first_pass.LLMClient")
def test_run_first_pass_writes_db_only_from_calling_thread(mock_client_cls, tmp_path, monkeypatch):
    """SQLite writes must be serialized on the thread that called
    run_first_pass, never on a worker thread — this is what avoids
    'database is locked' without needing an explicit lock."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    insert_filings([_make_filing(f"event{i}.txt") for i in range(6)], db_path)
    monkeypatch.setattr(
        "src.llm_analysis.first_pass.get_event_filings_needing_llm_analysis",
        lambda **kwargs: get_event_filings_needing_llm_analysis(db_path=db_path, **kwargs),
    )

    write_thread_ids = []

    def _tracking_insert(**kwargs):
        write_thread_ids.append(threading.current_thread().ident)
        return insert_llm_filing_analysis(db_path=db_path, **kwargs)

    monkeypatch.setattr("src.llm_analysis.first_pass.insert_llm_filing_analysis", _tracking_insert)

    mock_client = MagicMock()
    mock_client.provider = "openai-compatible"
    mock_client.model = "test-model"
    mock_client.chat_completion.return_value = LLMResponse(
        content=json.dumps({"primary_event_type": "ROUTINE"}), raw_response={},
    )
    mock_client_cls.return_value = mock_client

    main_thread_id = threading.current_thread().ident
    processed, errors = run_first_pass(limit=10, workers=4)

    assert processed == 6
    assert len(write_thread_ids) == 6
    assert all(tid == main_thread_id for tid in write_thread_ids)


@patch("src.llm_analysis.first_pass.LLMClient")
def test_run_first_pass_one_failure_does_not_stop_the_batch(mock_client_cls, tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    insert_filings([_make_filing(f"event{i}.txt") for i in range(5)], db_path)
    _install_real_db(monkeypatch, db_path)

    lock = threading.Lock()
    counter = {"n": 0}

    def _flaky_chat_completion(system_prompt, user_message):
        with lock:
            counter["n"] += 1
            n = counter["n"]
        if n == 3:
            raise RuntimeError("simulated failure")
        return LLMResponse(content=json.dumps({"primary_event_type": "ROUTINE"}), raw_response={})

    mock_client = MagicMock()
    mock_client.provider = "openai-compatible"
    mock_client.model = "test-model"
    mock_client.chat_completion.side_effect = _flaky_chat_completion
    mock_client_cls.return_value = mock_client

    processed, errors = run_first_pass(limit=10, workers=3)

    assert processed == 4
    assert errors == 1


@patch("src.llm_analysis.first_pass.LLMClient")
def test_run_first_pass_progress_info_includes_timing_and_workers(mock_client_cls, tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    insert_filings([_make_filing("event.txt")], db_path)
    _install_real_db(monkeypatch, db_path)

    mock_client = MagicMock()
    mock_client.provider = "openai-compatible"
    mock_client.model = "test-model"
    mock_client.chat_completion.return_value = LLMResponse(
        content=json.dumps({"primary_event_type": "ROUTINE", "importance_score": 5}), raw_response={},
    )
    mock_client_cls.return_value = mock_client

    progress_calls = []
    run_first_pass(limit=10, workers=2, on_progress=lambda *args: progress_calls.append(args))

    assert len(progress_calls) == 1
    done, total, ok, error, info = progress_calls[0]
    assert (done, total, ok, error) == (1, 1, True, None)
    assert info["workers"] == 2
    assert "elapsed_seconds" in info
    assert "speed_per_min" in info
    assert "eta_seconds" in info
    assert info["company_name"] == "Test Corp"


@patch("src.llm_analysis.first_pass.LLMClient")
def test_run_first_pass_progress_info_present_on_failure_too(mock_client_cls, tmp_path, monkeypatch):
    """Timing/workers info must be available even for a failed call, since
    the CLI/dashboard progress line reports ETA and error count together."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    insert_filings([_make_filing("event.txt")], db_path)
    _install_real_db(monkeypatch, db_path)

    mock_client = MagicMock()
    mock_client.chat_completion.side_effect = RuntimeError("boom")
    mock_client_cls.return_value = mock_client

    progress_calls = []
    run_first_pass(limit=10, workers=2, on_progress=lambda *args: progress_calls.append(args))

    assert len(progress_calls) == 1
    done, total, ok, error, info = progress_calls[0]
    assert ok is False
    assert error == "boom"
    assert info["workers"] == 2
    assert "eta_seconds" in info
