import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from src.database.db import (
    get_connection,
    get_event_filings_needing_llm_analysis,
    get_llm_filing_analysis,
    init_db,
    init_llm_filing_analysis_table,
    insert_filing_snapshot,
    insert_filings,
    insert_llm_filing_analysis,
)
from src.database.models import Filing
from src.document_intelligence.models import DocumentSnapshot
from src.llm_analysis.client import LLMClient, LLMConfigError, LLMResponse
from src.llm_analysis.first_pass import (
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
    assert len(filing_input["clean_text"]) == 25000
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


def test_get_event_filings_needing_llm_analysis_includes_snapshot_items(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    insert_filings([_make_filing("event.txt")], db_path)
    insert_filing_snapshot(
        DocumentSnapshot(filename="event.txt", form_type="8-K", items=["2.01"]),
        db_path,
    )
    pending = get_event_filings_needing_llm_analysis(db_path=db_path)
    assert pending[0]["items_json"] == '["2.01"]'


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
