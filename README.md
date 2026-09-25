# otc-filing-intelligence

A pipeline that detects corporate events in SEC EDGAR daily filings, runs an LLM first
pass directly over each one's text.
## What it does

1. Downloads the SEC EDGAR daily index for a given date (`master.idx`)
2. Routes every filing into **EVENT**, **CONTEXT**, or **IGNORED** 
3. Stores filing metadata in `data/filings.db` (SQLite)
4. Downloads and cleans the primary document's text
5. Runs the LLM first pass

## Installation

```bash
cd otc-filing-intelligence
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Copy `.env.example` to `.env` and set your User-Agent:

```bash
cp .env.example .env
# Edit .env and set:
# SEC_USER_AGENT="YourName youremail@example.com"
```

## Usage

### Fetch metadata only

```bash
python -m src.main --date 2024-05-15
```

### Fetch metadata + full filing text

```bash
python -m src.main --date 2024-05-15 --download-content
```