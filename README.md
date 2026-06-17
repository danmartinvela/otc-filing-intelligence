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

### Run tests

```bash
pytest tests/ -v
```

## Project structure

```
otc-filing-intelligence/
├── src/
│   ├── sec_ingestion/
│   │   ├── daily_index.py   # URL construction, index download, form filtering
│   │   ├── downloader.py    # Filing content download and text cleaning
│   │   └── parser.py        # master.idx line parser
│   ├── database/
│   │   ├── db.py            # SQLite connection, init, insert, update
│   │   └── models.py        # Filing dataclass
│   └── main.py              # CLI entry point
├── data/
│   └── filings.db           # Created on first run
├── tests/
│   ├── test_daily_index.py
│   ├── test_parser.py
│   └── test_downloader.py
├── .env.example
├── requirements.txt
└── README.md
```

## Database schema

Table `filings` in `data/filings.db`:

| Column        | Type    | Notes                       |
|---------------|---------|-----------------------------|
| id            | INTEGER | Primary key                 |
| cik           | TEXT    | SEC company identifier      |
| company_name  | TEXT    |                             |
| form_type     | TEXT    | e.g. 8-K, 10-K             |
| date_filed    | TEXT    | YYYY-MM-DD                  |
| filename      | TEXT    | UNIQUE — used for dedup     |
| filing_url    | TEXT    | Full URL to the filing      |
| raw_text      | TEXT    | Raw content (if downloaded) |
| clean_text    | TEXT    | Cleaned text (if downloaded)|
| created_at    | TEXT    | ISO 8601 UTC timestamp      |

## Filtered form types

```
8-K, 10-K, 10-Q, SC TO-I, SC TO-T, SC 13D, SC 13G, DEF 14A, S-1
```
