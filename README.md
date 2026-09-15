# otc-filing-intelligence

A pipeline that detects corporate events in SEC EDGAR daily filings, runs an LLM first
pass directly over each one's text, and keeps supporting filings around as historical
context — without paying to re-read every 10-K and 10-Q that goes by.

## What it does

1. Downloads the SEC EDGAR daily index for a given date (`master.idx`)
2. Routes every filing into **EVENT**, **CONTEXT**, or **IGNORED** (see
   [Filing Routing](#filing-routing)) and drops IGNORED ones before they reach the database
3. Stores filing metadata in `data/filings.db` (SQLite), skipping duplicates
4. Downloads and cleans the primary document's text (`raw_text`/`clean_text`) — see
   [Filing Routing](#filing-routing)
5. Runs the [LLM first pass](#llm-first-pass) directly on each EVENT filing's `clean_text`

## Architecture

```
SEC EDGAR
    ↓
Download daily index
    ↓
Classify EVENT / CONTEXT / IGNORED
    ↓
Download primary document
    ↓
raw_text / clean_text
    ↓
LLM first pass on EVENT filings (event classification + importance score)
    ↓
llm_filing_analysis

CONTEXT filings (10-K, 10-Q, 20-F, 6-K)
    ↓
Simple storage (no LLM call, no CPU spent)
    ↓
Retrieved on demand, as background for an EVENT filing

IGNORED filings (everything else)
    ↓
Dropped at ingestion — never stored, never analyzed
```

The system isn't a general-purpose document analyzer — it's an event-detection pipeline.
10-Ks and 10-Qs rarely contain an immediate corporate event; their value is as historical
context *after* an event has already been detected elsewhere (an 8-K, a tender offer, a
13D). Skipping them daily is what keeps processing time and future LLM cost down.

## Installation

```bash
cd otc-filing-intelligence
python -m venv .venv
source .venv/bin/activate      # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Configuration

Copy `.env.example` to `.env` and set your User-Agent:

```bash
cp .env.example .env
# Edit .env and set:
# SEC_USER_AGENT="YourName youremail@example.com"
```

> **Important:** The SEC requires a descriptive `User-Agent` header identifying you or your organization. Failure to set one may result in your IP being blocked. See [SEC EDGAR Fair Access](https://www.sec.gov/os/accessing-edgar-data).

## Usage

### Fetch metadata only

```bash
python -m src.main --date 2024-05-15
```

### Fetch metadata + full filing text

```bash
python -m src.main --date 2024-05-15 --download-content
```

### Company enrichment

The SEC publishes a master mapping of CIK → ticker at `company_tickers.json`. Import it once
(or refresh it periodically) to populate the `companies` table, then enrich stored filings with their tickers.

```bash
# Import the SEC company/ticker mapping (~10 000 entries)
python -m src.main --import-sec-company-tickers

# Update filings.ticker for all existing filings
python -m src.main --enrich-filings

# Import + enrich in one command
python -m src.main --import-sec-company-tickers --enrich-filings

# Full workflow: import tickers, then fetch today's filings (auto-enriched on insert)
python -m src.main --import-sec-company-tickers --date 2024-05-15
```

When the `companies` table already contains data, any new filings inserted via `--date` are
**automatically enriched** with their ticker at insert time — no separate `--enrich-filings` step needed.

### OTC Markets

Download a CSV from the [OTC Markets Stock Screener](https://www.otcmarkets.com/research/stock-screener) and import it to cross-reference with SEC filings by ticker.

```bash
# Import a downloaded screener CSV
python -m src.main --import-otc-screener-csv data/Stock_Screener.csv

# Enrich filings with OTC tier, sec type, and country
python -m src.main --enrich-filings-with-otc

# Full recommended workflow
python -m src.main --import-sec-company-tickers
python -m src.main --import-otc-screener-csv data/Stock_Screener.csv
python -m src.main --date 2024-05-15
python -m src.main --enrich-filings-with-otc
```

### Filing Routing

Every filing is classified into exactly one `filing_category` the moment it's fetched
(and automatically backfilled for existing rows on `init_db`):

| Category  | Form types                                                                                          | What happens to it |
|-----------|------------------------------------------------------------------------------------------------------|---------------------|
| `EVENT`   | 8-K, 8-K/A, SC TO-I, SC TO-T, SC TO-C, SC 13D, SC 13D/A, SC 13E3, S-1, S-1/A, 424B3, 424B5, DEF 14A, DEFM14A, PREM14A | Sent to the LLM first pass |
| `CONTEXT` | 10-K, 10-Q, 20-F, 6-K                                                                                 | Stored only — retrieved on demand as background for an EVENT filing |
| `IGNORED` | Everything else                                                                                       | Dropped before it reaches the database |

The classification logic lives in one place: `src/filing_routing/routing.py`,
`get_filing_category(form_type)`. The ingestion filter (`daily_index.py`) is the only
consumer — a single source of truth, no duplicated form-type lists.

### LLM First Pass

A first classification pass over EVENT filings using an LLM — decides what each filing is
really about and whether it's worth deep research, working directly off the filing's
`clean_text`. There is no intermediate extraction/structuring stage: the pipeline is
`SEC EDGAR → daily index → EVENT/CONTEXT classification → primary document → clean_text →
LLM first pass → llm_filing_analysis`.

Works with any OpenAI-compatible chat completions API (OpenAI, Grok/x.ai, or a local
proxy). Configure it in `.env`:

```bash
LLM_API_KEY="sk-..."
LLM_BASE_URL="https://api.openai.com/v1"     # or https://api.x.ai/v1, etc.
LLM_MODEL="gpt-4o-mini"
LLM_PROVIDER="openai-compatible"             # optional, stored alongside each result
```

```bash
# Classify up to 20 pending EVENT filings
python -m src.main --llm-first-pass --limit 20

# Classify everything pending (no limit)
python -m src.main --llm-first-pass
```

For each filing, the pipeline sends a compact input (company name, ticker, form type,
filing URL, and the first 20,000 characters of `clean_text`) and
asks for strict JSON: a `primary_event_type` from a fixed list (e.g. `MERGER`,
`TENDER_OFFER`, `BANKRUPTCY_DISTRESS`, `ROUTINE`, ...), an `importance_score` (0-100), a
`deep_research` flag, a short summary, and text evidence. Results are stored in
`llm_filing_analysis`, one row per filing (deduplicated by `filing_filename`) — filings
already analyzed are skipped on the next run.

If the model's response isn't valid JSON, the raw response is still saved with
`primary_event_type = "PARSE_ERROR"` so nothing is silently lost.

### Run tests

```bash
pytest tests/ -v
```

## Project structure

```
otc-filing-intelligence/
├── src/
│   ├── sec_ingestion/
│   │   ├── daily_index.py         # URL construction, index download, form filtering
│   │   ├── downloader.py          # Filing content download and text cleaning
│   │   └── parser.py              # master.idx line parser
│   ├── company_enrichment/
│   │   ├── sec_company_tickers.py # Download/parse SEC company_tickers.json
│   │   └── enrich_filings.py      # Update filings.ticker from companies table
│   ├── otcmarkets/
│   │   └── otc_screener_importer.py  # Parse OTC Markets Stock Screener CSV
│   ├── filing_routing/
│   │   └── routing.py             # get_filing_category(form_type) -> EVENT/CONTEXT/IGNORED
│   ├── llm_analysis/
│   │   ├── client.py              # OpenAI-compatible chat completions client
│   │   ├── prompts.py             # Event types, system prompt, user message builder
│   │   └── first_pass.py          # Orchestration + robust JSON parsing
│   ├── database/
│   │   ├── db.py                  # SQLite: all table init, insert, upsert, enrich
│   │   └── models.py              # Filing dataclass
│   └── main.py                    # CLI entry point
├── data/
│   └── filings.db                 # Created on first run
├── tests/
│   ├── test_daily_index.py
│   ├── test_parser.py
│   ├── test_downloader.py
│   ├── test_company_enrichment.py
│   ├── test_otc_importer.py
│   ├── test_filing_routing.py
│   └── test_llm_analysis.py
├── .env.example
├── requirements.txt
└── README.md
```

## Database schema

Table `filings` in `data/filings.db`:

| Column        | Type    | Notes                              |
|---------------|---------|------------------------------------|
| id            | INTEGER | Primary key                        |
| cik           | TEXT    | SEC company identifier             |
| company_name  | TEXT    |                                    |
| form_type     | TEXT    | e.g. 8-K, 10-K                    |
| date_filed    | TEXT    | YYYYMMDD, as provided by SEC's master.idx |
| filename      | TEXT    | UNIQUE — used for dedup            |
| filing_url    | TEXT    | Full URL to the filing             |
| raw_text      | TEXT    | Raw content (if downloaded)        |
| clean_text    | TEXT    | Cleaned text (if downloaded)       |
| ticker        | TEXT    | Set via `--enrich-filings`         |
| exchange      | TEXT    | Reserved for future use (NULL)     |
| otc_tier      | TEXT    | Set via `--enrich-filings-with-otc`|
| sec_type      | TEXT    | Set via `--enrich-filings-with-otc`|
| country       | TEXT    | Set via `--enrich-filings-with-otc`|
| filing_category | TEXT  | `EVENT`, `CONTEXT`, or `IGNORED` — set automatically on insert, see [Filing Routing](#filing-routing) |
| created_at    | TEXT    | ISO 8601 UTC timestamp             |

Table `companies`:

| Column        | Type    | Notes                           |
|---------------|---------|---------------------------------|
| cik           | TEXT    | PRIMARY KEY (zero-padded 10 digits) |
| ticker        | TEXT    | Exchange ticker symbol          |
| company_name  | TEXT    |                                 |
| exchange      | TEXT    | Reserved for future use (NULL)  |
| source        | TEXT    | e.g. "SEC company_tickers.json" |
| updated_at    | TEXT    | ISO 8601 UTC timestamp          |

Table `otc_securities`:

| Column         | Type    | Notes                              |
|----------------|---------|------------------------------------|
| symbol         | TEXT    | PRIMARY KEY — matches filings.ticker|
| security_name  | TEXT    |                                    |
| tier           | TEXT    | Expert Market, OTCQB, OTCQX, etc. |
| price          | REAL    |                                    |
| change_percent | REAL    |                                    |
| volume         | INTEGER |                                    |
| sec_type       | TEXT    | e.g. Common Stock                  |
| country        | TEXT    |                                    |
| state          | TEXT    |                                    |
| source         | TEXT    | "OTC Markets Stock Screener"       |
| updated_at     | TEXT    | ISO 8601 UTC timestamp             |

Table `llm_filing_analysis` (one row per filing, see [LLM First Pass](#llm-first-pass)):

| Column                     | Type    | Notes                                          |
|----------------------------|---------|--------------------------------------------------|
| id                         | INTEGER | Primary key                                       |
| filing_filename            | TEXT    | UNIQUE — matches `filings.filename`               |
| provider                   | TEXT    | e.g. `openai-compatible` (from `LLM_PROVIDER`)    |
| model                      | TEXT    | Model name used (from `LLM_MODEL`)                |
| primary_event_type         | TEXT    | One of the fixed event types, or `PARSE_ERROR`    |
| secondary_event_types_json | TEXT    | JSON list of additional event types               |
| is_material                | INTEGER | 0/1                                               |
| importance_score           | INTEGER | 0-100                                             |
| market_impact              | TEXT    | `LOW`, `MEDIUM`, `HIGH`, or `VERY_HIGH`           |
| deep_research               | INTEGER | 0/1 — flags filings worth a deeper LLM pass       |
| summary                    | TEXT    | Short LLM-generated summary of the event          |
| key_entities_json           | TEXT    | JSON list of relevant names/amounts/instruments   |
| evidence_json               | TEXT    | JSON list of short evidence snippets              |
| reason_for_score           | TEXT    | Brief explanation from the model                  |
| next_step                  | TEXT    | `IGNORE`, `WATCH`, or `RESEARCH`                  |
| raw_response               | TEXT    | Raw LLM response, kept even on parse failure      |
| created_at                 | TEXT    | ISO 8601 UTC timestamp                            |

`market_impact`, `next_step`, and `key_entities_json` are backfilled automatically via
`ALTER TABLE` on `init_db()` for databases created before this schema existed, so no
manual migration step is needed — existing rows just get `NULL`/`[]` for the new fields.

## Analytical queries

```sql
-- All filings from OTC-listed companies, most recent first
SELECT company_name, ticker, form_type, otc_tier, sec_type, country
FROM filings
WHERE otc_tier IS NOT NULL
ORDER BY date_filed DESC;
```

## Filtered form types

See [Filing Routing](#filing-routing) for the full EVENT/CONTEXT breakdown. Anything not
listed there is IGNORED and never stored.
