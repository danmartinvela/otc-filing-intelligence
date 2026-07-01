# otc-filing-intelligence

A pipeline to download, filter, and store SEC EDGAR daily filings into a local SQLite database.

## What it does

1. Downloads the SEC EDGAR daily index for a given date (`master.idx`)
2. Parses and filters filings by relevant form types (8-K, 10-K, 10-Q, SC TO-I/T, SC 13D/G, DEF 14A, S-1)
3. Stores filing metadata in `data/filings.db` (SQLite), skipping duplicates
4. Optionally downloads and cleans the full text of each filing

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
│   └── test_otc_importer.py
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
| date_filed    | TEXT    | YYYY-MM-DD                         |
| filename      | TEXT    | UNIQUE — used for dedup            |
| filing_url    | TEXT    | Full URL to the filing             |
| raw_text      | TEXT    | Raw content (if downloaded)        |
| clean_text    | TEXT    | Cleaned text (if downloaded)       |
| ticker        | TEXT    | Set via `--enrich-filings`         |
| exchange      | TEXT    | Reserved for future use (NULL)     |
| otc_tier      | TEXT    | Set via `--enrich-filings-with-otc`|
| sec_type      | TEXT    | Set via `--enrich-filings-with-otc`|
| country       | TEXT    | Set via `--enrich-filings-with-otc`|
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

## Analytical queries

```sql
-- All filings from OTC-listed companies, most recent first
SELECT company_name, ticker, form_type, otc_tier, sec_type, country
FROM filings
WHERE otc_tier IS NOT NULL
ORDER BY date_filed DESC;
```

## Filtered form types

```
8-K, 10-K, 10-Q, SC TO-I, SC TO-T, SC 13D, SC 13G, DEF 14A, S-1
```
