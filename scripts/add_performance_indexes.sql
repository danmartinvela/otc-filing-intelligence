-- Performance indexes for data/filings.db.
--
-- Standalone, one-off schema change — does NOT touch src/ or dashboard/
-- code, and changes no application behavior. Every dashboard query that
-- filters/sorts on filing_category + date_filed, or reads MAX(created_at),
-- was doing a full table scan (~7.3s each, measured) because `filings` has
-- grown to 16GB (mostly oversized raw_text/clean_text blobs on a minority
-- of rows) with no index beyond the implicit one on `filename`.
--
-- Run once with:
--   sqlite3 data/filings.db < scripts/add_performance_indexes.sql
--
-- Safe to re-run (IF NOT EXISTS) and safe to re-run after the backend
-- recreates filings.db from scratch, since init_db() doesn't create these.

-- Covers: get_category_counts, get_summary_kpis, get_daily_filing_counts,
-- get_form_type_counts, get_monthly_event_counts, get_top_companies,
-- get_event_date_bounds, get_filter_options, get_events_count,
-- get_events_page — every one of these filters on filing_category and/or
-- sorts on date_filed.
CREATE INDEX IF NOT EXISTS idx_filings_category_date
    ON filings(filing_category, date_filed);

-- Covers get_last_updated (SELECT MAX(created_at) FROM filings).
CREATE INDEX IF NOT EXISTS idx_filings_created_at
    ON filings(created_at);

-- Detalle del Filing's on-demand search (search_event_filings): ticker/
-- company_name LIKE 'prefix%', case-insensitive. NOCASE is what lets
-- SQLite use these as an index range scan instead of a full scan for a
-- case-insensitive prefix match — measured ~2.4s-6.5s for one specific
-- ticker without them, ~10ms with them. Unlike the two indexes above,
-- these ARE also created by init_db() (src/database/db.py) — this script
-- just applies them immediately instead of waiting for the next init_db()
-- call.
CREATE INDEX IF NOT EXISTS idx_filings_ticker_nocase
    ON filings(ticker COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_filings_company_name_nocase
    ON filings(company_name COLLATE NOCASE);

ANALYZE filings;
