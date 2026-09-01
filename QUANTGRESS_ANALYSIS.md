# Quantgress Application Analysis

## Executive Summary

**Quantgress** is a self-hosted, open-source alternative to Quiver Quantitative. It scrapes 18 distinct public U.S. government datasets, normalizes them into a single DuckDB OLAP database, and serves them via a read-only FastAPI REST API. All underlying data is public domain from government sources (SEC, Senate/House, FEC, FINRA, USPTO, Wikimedia, USAspending).

**Core Architecture:**
```
Public Government Sources → scrape_*.py scripts → entities.py (entity resolution) 
→ congress_trades.duckdb → daily.py (incremental driver) → api.py (REST layer)
```

---

## 1. DATA SOURCES & SCRAPERS

### Phase 1: Senate Stock Trades (PTRs)
- **Source:** [efdsearch.senate.gov](https://efdsearch.senate.gov)
- **Module:** `scrape_senate.py`
- **Format:** HTML tables (no OCR needed)
- **Coverage:** 2012-present
- **Update Cadence:** Daily
- **Auth:** Session/CSRF gate (handled transparently)
- **Key Fields:** first_name, last_name, office, filed_date, link, tx_date, ticker, asset_name, asset_type, tx_type, amount_raw, amount_low, amount_high
- **Uniqueness:** Filing links
- **Resume Logic:** Skips links already in `senate_trades` table

### Phase 2: House Stock Trades (PTRs)
- **Source:** [disclosures-clerk.house.gov](https://disclosures-clerk.house.gov)
- **Module:** `scrape_house.py`
- **Format:** Annual ZIP files containing PDFs
- **Coverage:** 2012-present
- **Update Cadence:** Daily (only current + previous year)
- **Auth:** None
- **Note:** No OCR support (scanned PDFs queued, not blocked)
- **Resume Logic:** Skips doc_ids already processed

### Phase 6: Corporate Lobbying (LD-1/LD-2)
- **Source:** [LDA.gov API](https://lda.gov/api/v1/filings/)
- **Module:** `scrape_lobbying.py`
- **Format:** JSON REST (page_size capped at 25)
- **Coverage:** Current year by default, any year on-demand
- **Update Cadence:** As-needed (manual --year specification recommended)
- **Auth:** Free API key or anonymous (rate-limited to 15 req/min anon, 120 req/min with key)
- **Key Fields:** filing_uuid, filing_type, filing_year, filing_period, dt_posted, income, expenses, registrant_id, registrant_name, client_id, client_name, client_state, client_country, general_issues, url
- **Note:** Client names are free-text (requires Phase 8 entity resolution)
- **Resume Logic:** Skips filing_uuids already stored

### Phase 7: Government Contracts
- **Source:** [USAspending v2 API](https://api.usaspending.gov/api/v2/search/spending_by_award/)
- **Module:** `scrape_contracts.py`
- **Format:** JSON REST (page size max 100)
- **Coverage:** Last 7 days by default, custom date ranges available
- **Update Cadence:** Rolling 7-day window (massive dataset: 2.6M awards in first 8 months of 2026)
- **Auth:** None required
- **Key Fields:** generated_internal_id, award_id, recipient_name, recipient_uei, awarding_agency, awarding_sub_agency, start_date, end_date, award_amount, contract_award_type, description, last_modified_date
- **Resume Logic:** Skips generated_internal_ids already stored

### Phase 9: Insider Trades (Form 4)
- **Source:** [SEC EDGAR](https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets)
- **Module:** `scrape_insiders.py`
- **Format:** Two paths:
  - **Bulk:** Quarterly ZIP files (tab-delimited tables)
  - **Live:** EDGAR daily index + per-filing XML (fills gap since last quarterly bulk)
- **Coverage:** 2006-present
- **Update Cadence:** Quarterly (bulk) + daily gap-fill (live)
- **Auth:** None (UA header required)
- **Key Fields:** accession_number, trans_seq, filed_date, trans_date, issuer_cik, issuer_name, ticker, owner_cik, owner_name, owner_relationship, security_title, trans_code, acquired_disposed, shares, price_per_share, shares_owned_following
- **Note:** Only Table I (non-derivatives) transactions included
- **Resume Logic:** Skips (accession_number, trans_seq) pairs already stored

### Phase 10: Institutional Holdings (Form 13F)
- **Source:** [SEC Form 13F Data Sets](https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets)
- **Module:** `scrape_13f.py`
- **Format:** Quarterly ZIP files (tab-delimited tables, one per quarter)
- **Coverage:** Latest posted quarter by default, individual quarters available
- **Update Cadence:** Quarterly (45-day deadline after quarter-end)
- **Auth:** None (UA header required)
- **Key Fields:** accession_number, infotable_sk, period_of_report, filed_date, manager_cik, manager_name, issuer_name, cusip, share_class, value_usd, shares, share_type, put_call, investment_discretion, voting_auth_sole, voting_auth_shared, voting_auth_none
- **Note:** Uses CUSIP, not ticker. Amounts in THOUSANDS in source (converted to USD)
- **Generates 2 Derived Views:** f13_changes (quarter-over-quarter diff), f13_top_holders (pivoted by issuer)
- **Resume Logic:** Skips (accession_number, infotable_sk) pairs already stored

### Phase 11: Off-Exchange Short Volume
- **Source:** [FINRA Daily Files](https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data)
- **Module:** `scrape_short_volume.py`
- **Format:** Pipe-delimited daily files (CNMS consolidated file, one per trade date)
- **Coverage:** Last 5 days by default
- **Update Cadence:** Daily (posted by 6pm ET on trade date)
- **Auth:** None
- **Key Fields:** trade_date, symbol, short_volume, short_exempt_volume, total_volume, market
- **Note:** Off-exchange = never printed to exchange tape. Real ticker (no resolution needed)
- **Resume Logic:** Skips (trade_date, symbol, market) tuples already stored
- **API Limitation:** NOT exposed via public REST API (FINRA TOS Sec 3.3(a)/(e) bars redistribution to non-authorized users)

### Phase 12: Patents
- **Source:** [USPTO Open Data Portal](https://data.uspto.gov/)
- **Module:** `scrape_patents.py`
- **Format:** JSON REST (page size max 100)
- **Coverage:** Last 10 days by default, custom date ranges available
- **Update Cadence:** As-needed (granted only on Tuesdays)
- **Auth:** API Key required (free MyUSPTO account + ID.me verification)
- **Key Fields:** application_number, patent_number, invention_title, filing_date, grant_date, assignee_name, assignee_source
- **Note:** Assignee names are free-text (requires Phase 8 entity resolution). Prefers recorded assignment over first applicant name.
- **Resume Logic:** Skips application_numbers already stored

### Phase 13: Corporate PAC/Committee Donations
- **Source:** [OpenFEC API](https://api.open.fec.gov/v1/schedules/schedule_a/)
- **Module:** `scrape_donors.py`
- **Format:** JSON REST (page size 100)
- **Coverage:** Current FEC cycle by default
- **Update Cadence:** As-needed
- **Auth:** Free API key (instant signup at api.data.gov)
- **Key Fields:** sub_id, contributor_name, entity_type, contribution_date, contribution_amount, committee_id, committee_name, cycle
- **Note:** Schedule A itemized receipts where contributor is committee/PAC (not individuals). Donor names free-text (requires Phase 8 entity resolution)
- **Legal Restriction:** API exposes aggregated data only (52 U.S.C. Sec 30111(a)(4) bars commercial use of raw contributor info). Public API returns totals by ticker/committee/cycle, never contributor_name or sub_id.
- **Resume Logic:** Skips sub_ids already stored

### Phase 14: Wikipedia Pageviews
- **Source:** [Wikimedia REST API](https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/reference/page-views.html)
- **Module:** `scrape_pageviews.py`
- **Format:** JSON REST
- **Coverage:** As-needed per company
- **Update Cadence:** As-needed
- **Auth:** None
- **Note:** Measures search/attention as proxy signal

### Phase 16: Executive Compensation (Pay vs. Performance)
- **Source:** [SEC XBRL Frames API](https://data.sec.gov/api/xbrl/frames/)
- **Module:** `scrape_execcomp.py`
- **Format:** JSON REST (one call per concept/fiscal-year returns all filers)
- **Coverage:** Fiscal 2022-present (Item 402(v) rule required since FY2022)
- **Update Cadence:** As-needed
- **Auth:** None (UA header required)
- **Key Fields:** cik, fiscal_year, company, ticker, fy_start, fy_end, peo_total_comp, peo_actually_paid, non_peo_avg_total_comp, non_peo_avg_actually_paid, tsr (total shareholder return), peer_group_tsr, co_selected_measure_amt
- **Note:** CEO/PEO comp actually paid vs. average non-PEO NEO comp + performance metrics

### Phase 17: Trump 278-T Trades
- **Source:** [ProPublica DocumentCloud Mirror](https://projects.propublica.org/trump-team-financial-disclosures/)
- **Module:** `scrape_trump.py`
- **Format:** PDF (scanned, OCR'd)
- **Coverage:** Donald Trump filings
- **Update Cadence:** Manual
- **Auth:** None (DocumentCloud S3)
- **Note:** OCR'd text (noisy). Uses bracket-table lookup for amount recovery (not character-by-character repair)
- **Resume Logic:** Skips DocumentCloud doc_ids already stored

### Phase 18: Senate Annual Financial Disclosure
- **Source:** [efdsearch.senate.gov](https://efdsearch.senate.gov)
- **Module:** `scrape_senate_annual.py`
- **Format:** HTML tables
- **Coverage:** Current year (every senator must file by May 15 for prior year)
- **Update Cadence:** Annual
- **Auth:** Session/CSRF gate (reuses scrape_senate.py logic)
- **Tables:** 
  - **fd_assets:** Schedule A (every asset: bank, real estate, retirement, mutual funds, stocks)
  - **fd_liabilities:** Schedule D (liabilities: mortgages, loans, lines of credit)
- **Note:** Accurate net worth tracking for senators (floor estimate that uses actual assets/liabilities, not reconstructed from trades)

---

## 2. PROCESSING & TRANSFORMATIONS

### Phase 3: Ticker Resolution (Congress Trades)
- **Module:** `entities.py` (extract_congress adapter)
- **Input:** House PTR asset_name fields (already extracted from PDFs), Senate PTR ticker fields
- **Strategies:**
  1. **Extract:** Regex patterns for embedded tickers
     - Trailing parens: `"Roper Technologies, Inc. - Common Stock (ROP)"`
     - Leading prefix: `"ACN - Accenture plc Class A Ordinary Shares (Ireland)"`
     - Small-caps (pre-2018): `"RoP"` → `"ROP"` + lowercase handling
  2. **Filters:** Excludes common false positives (ADR, ADS, ETF, REIT, LLC, LP, INC, THE, USA, NEW, SOLD, OWNER, CLASS, FUND, TRUST, BOND, NOTE, PLC, CORP, LTD, COMMON, JOINT, YES, NO, IPO)
- **Output:** Writes to `ticker_guess` + `ticker_guess_how` (never overwrites real `ticker` column)
- **Coverage:** 535 → 875 of 890 congress trade rows (60% → 98%)

### Phase 8: Entity Resolution Engine (Generalized)
- **Module:** `entities.py`
- **Registers sources:** congress, lobbying, contracts, 13f_holdings, patents, donors
- **Two strategies (ranked by trust):**
  1. **Extract:** Embedded ticker/symbol (congress trades only)
  2. **SEC Name Exact Match:** Normalize company names → exact match against SEC's `company_tickers.json`
     - Normalization: Uppercase, strip legal-form words (CORP, INC, LTD, etc.), remove punctuation, collapse whitespace
     - Safety: Ambiguous names (multiple tickers collapse to one string) are dropped, not guessed
     - No fuzzy matching (Phase 3 lesson: fuzzy can map ABB Ltd. → wrong security ABLZF instead of ABBNY)

### Phase 4: Daily Incremental Driver
- **Module:** `daily.py`
- **Sequence:** Senate (all years) → House (current + previous year) → Entity resolution → Short volume (last N days)
- **Resume Logic:** Each step is independently resumable
- **Schedule:** Windows Task Scheduler (stdout/stderr → daily.log via `cmd /c ... >> daily.log 2>&1`)
- **Rationale:** STOCK Act gives filers 30-45 days to disclose, so daily is plenty

### Amount Parsing
- **Function:** `schema.py` → `parse_amount(text)`
- **Input:** Bracket format: `"$1,001 - $15,000"` or open-ended `"$1,000000+"`
- **Output:** (low, high) tuple where high can be None
- **Senate Annual Variant:** `_parse_value()` in `scrape_senate_annual.py` handles "None (or less than $X)" phrasing

### Date Parsing
- **Input Formats:** MM/DD/YYYY (Senate, House), ISO YYYY-MM-DD (contracts, USPTO), other formats per source
- **Processing:** Converted to DATE columns in views for correct chronological sorting (not lexical)
- **Lag Calculation:** Days between transaction date and filing date (should be 30-45 days per STOCK Act)

### Data Normalization
- **Chamber Coalescing:** `senate_trades` + `house_trades` → `trades` VIEW (unified)
- **Ticker Priority:** `coalesce(ticker_real, ticker_guess)` in view (preferring real over guessed)
- **Clean-up Columns:** 
  - `tkr_recovered` boolean flag (ticker was guessed, not real)
  - Provenance columns (ticker_guess_how, *_guess_how)

---

## 3. DATABASE SCHEMA & STRUCTURE

### Database File
- **Type:** DuckDB (embedded OLAP engine, single file, zero-server)
- **Path:** `congress_trades.duckdb`
- **Design Choice:** OLAP over OLTP because data is queried far more than written

### Core Tables

#### congress_trades (Union View)
```sql
CREATE VIEW trades AS
  SELECT 'S' AS chamber, last_name, 
    coalesce(ticker_real, ticker_guess) AS tkr,
    asset_name, tx_type, amount_low, amount_high,
    try_strptime(tx_date, '%m/%d/%Y')::DATE AS txn_date,
    try_strptime(filed, '%m/%d/%Y')::DATE AS filed_date,
    date_diff('day', txn_date::DATE, filed_date::DATE) AS lag_days,
    -- secondary columns for filtering/auditing
    asset_type, owner, tkr_recovered, first_name, office, link
  FROM senate_trades
  UNION ALL
  SELECT 'H' AS chamber, ...
  FROM house_trades
```

#### Core Congress Columns
```sql
first_name, last_name, office, filed, link, tx_date, owner,
ticker, asset_name, asset_type, tx_type,
amount_raw, amount_low, amount_high,
ticker_guess, ticker_guess_how
```

#### Derived Datasets

| Dataset | Table | Key Columns | Primary Join |
|---------|-------|-------------|--------------|
| lobbying_filings | filing_uuid, client_name, registrant_name, filing_year, dt_posted, income, expenses | client_name → ticker_guess (via entities.py) |
| gov_contracts | generated_internal_id, recipient_name, awarding_agency, award_amount, start_date, end_date | recipient_name → ticker_guess |
| insider_trades | accession_number, trans_seq, ticker (direct), owner_name, trans_date | ticker (direct, no resolution) |
| f13_holdings | accession_number, infotable_sk, manager_name, cusip, issuer_name, value_usd, shares | cusip, issuer_name → ticker_guess |
| f13_changes | (view) quarter-over-quarter position changes | issuer_ticker_guess, change_type |
| f13_top_holders | (view) top institutional holders per ticker per quarter | issuer_ticker_guess, rank |
| patents | application_number, patent_number, assignee_name, grant_date | assignee_name → ticker_guess |
| corporate_donations (raw) | sub_id, contributor_name, contribution_date, contribution_amount, committee_name | contributor_name → ticker_guess |
| corporate_donations_agg (view) | contributor_ticker_guess, committee_name, cycle, total_amount | Aggregated (legal constraint: 52 U.S.C. Sec 30111(a)(4)) |
| pageviews | article, date, views | Company name (free-form) |
| exec_comp | cik, fiscal_year, company, ticker (direct), peo_total_comp, peo_actually_paid | ticker (direct) |
| trump_trades_clean | tx_type, asset_class, description, txn_date, amount_low, amount_high | Donald Trump only |
| fd_assets | last_name, asset_name, ticker, filing_year, value | Senate assets from Annual FD |
| fd_liabilities | last_name, creditor, amount, filing_year | Senate liabilities from Annual FD |
| short_volume | trade_date, symbol, short_volume, short_exempt_volume, total_volume, market | NOT in public API |

### Authentication Table
```sql
CREATE TABLE api_keys (
  key_hash VARCHAR PRIMARY KEY,  -- SHA256 hash
  email VARCHAR,
  tier VARCHAR DEFAULT 'free',
  created_at TIMESTAMP,
  revoked_at TIMESTAMP
)
```

### Design Principles
1. **Reversibility:** All guesses in `*_guess` + `*_guess_how` columns; original data never overwritten
2. **Idempotency:** Every scraper resumable via uniqueness check (skip logic)
3. **Transparency:** Raw + guessed values side-by-side for auditing

---

## 4. API FUNCTIONALITY

### Framework & Configuration
- **Framework:** FastAPI + uvicorn
- **Rate Limiting:** SlowAPI (500 requests/day per API key or IP)
- **Signup Limit:** 5/day per IP (public, no auth required)
- **CORS:** Limited to `https://quantgress.dhruvmulajkar.me` for `/signup` only
- **Connection Model:** Fresh connection per request (read-only); never caches stale snapshots
- **Authentication:** X-API-Key header (SHA256-hashed keys stored)

### Endpoints

#### Public (Unauthenticated)
```http
POST /signup
  Body: {"email": "..."}
  Response: {"api_key": "qg_live_..."}
  Note: 5 requests/day per IP, one active key per email
```

#### Protected Routes (Require X-API-Key Header)

##### Core Named Routes
```http
GET /
  Response: {"datasets": {"trades": 1022, "lobbying": 55000, ...}, 
             "usage": "GET /{dataset}?<filter>...",
             "named_routes": ["/trades", "/politician/{name}", "/ticker/{symbol}"]}

GET /trades
  Filters: chamber, tkr (ticker), last_name, tx_type, asset_type
  Default Order: txn_date DESC
  Max Limit: 1000 rows (default 100)
  Example: /trades?tkr=AAPL&chamber=S&limit=50&offset=0

GET /politician/{name}
  Returns: 
    - summary: [{"chamber": "S", "last_name": "...", "txns": 42, 
                 "tickers": 15, "first_trade": "2020-01-15", 
                 "last_trade": "2024-06-30"}]
    - trades: [{detailed trade rows}]

GET /ticker/{symbol}
  Returns: All trades for a specific ticker symbol

GET /{dataset}
  Generic endpoint for all registered datasets
  Available datasets: trades, lobbying, contracts, insiders, 13f-positions,
                    13f-changes, 13f-top-holders, patents, donors, pageviews,
                    exec-comp, trump-trades, senate-assets, senate-liabilities
```

### Filter Modes
- **eq:** Exact match (case-sensitive for integers, case-insensitive for digit-string coercion)
- **eq_ci:** Case-insensitive exact (tickers/symbols)
- **ilike:** Case-insensitive substring match

### Query Parameters
- `limit` (default 100, max 1000)
- `offset` (default 0)
- Dataset-specific filter columns (see RELATIONS dict in api.py)

### Example Queries
```bash
# CEO trades in tech stocks
GET /trades?last_name=Musk&asset_type=Stock&limit=100

# Government contracts to defense contractors
GET /contracts?awarding_agency=Department%20of%20Defense&limit=500

# Lobbying by Apple
GET /lobbying?client_ticker_guess=AAPL&limit=200

# Insider trading in Tesla
GET /insiders?ticker=TSLA&limit=100

# Which senators hold the most in AAPL
GET /13f-top-holders?issuer_ticker_guess=AAPL&period_of_report=2024Q4
```

### CLI Query Tool
**Module:** `q.py`
- Purpose: Direct DuckDB query interface (PowerShell-safe)
- No FastAPI needed for local queries
- Examples:
  ```bash
  py q.py                    # Summary by chamber + member
  py q.py --types            # Asset types + counts
  py q.py --type Stock       # Trades of one asset type
  py q.py --tickered         # Only rows with ticker
  py q.py "SELECT COUNT(*) FROM trades WHERE tkr='AAPL'"
  ```

---

## 5. GAPS & LIMITATIONS

### Data Coverage Gaps

#### 1. House OCR Path Not Built
- **Impact:** ~13% of House PTRs (2012-present), worse in older years (~33% in 2016), are scanned PDFs
- **Status:** Extracted as `status='scanned'` with no transaction rows
- **Mitigation:** Queued via `--ocr-queue` flag; no automatic blocker
- **Effort:** Requires pytesseract + pdf2image + QA pass on OCR'd results

#### 2. Ticker Resolution Fragility
- **Congress trades:** 98% coverage (875/890), but remaining 2% may be legitimate (bonds, munis, private LLCs have no ticker)
- **Lobbying:** Client names are unstructured free text; entity resolution assumes SEC company list (misses private firms, foreign entities, nonprofits lobbying)
- **Patents:** Assignee names free-text; private companies not in SEC registry won't resolve
- **Contracts:** Recipient names may not match SEC registry (subsidiaries, legal name variations)
- **Donors:** Corporate names only partially in SEC registry

#### 3. No Fuzzy Matching
- **Rationale:** Phase 3 deleted fuzzy version (mapped ABB Ltd. → ABLZF instead of ABBNY)
- **Implication:** Legitimate companies with spelling variations or legal-name changes won't match
- **Manual Escape:** CLI queries can use exact-match on free-text name

#### 4. Incomplete Insider Trading Coverage
- **Phase 9 Limitation:** Only Table I (non-derivative) transactions
- **Missing:** Table II (derivatives: options, RSUs, swaps), Holdings tables
- **Why:** Different row shape (strike price, exercise/expiration dates); not consumed yet by analysis

#### 5. Trump 278-T OCR Noise
- **Source:** Scanned form, OCR'd by DocumentCloud
- **Issues:** '$' misreads as 's', '0' misreads as 'o'/'O' in amounts
- **Mitigation:** Bracket-table lookup (bracket uniquely determines high when low intact), not character repair
- **Note:** description field still contains OCR noise from wrapped rows

#### 6. Wikipedia Pageviews Not Linked to Companies
- **Current State:** Arbitrary per-request scraping (no systematic coverage)
- **Need:** Company→Wikipedia article mapping

#### 7. Form 13F Limited by CUSIP
- **Issue:** No ticker field in 13F structured data; join via CUSIP + issuer_name
- **Fragility:** issuer_name is free text; needs Phase 8 entity resolution (sec_name adapter)

#### 8. Name Normalization Still Open
- **Phase 3 Note:** "Name normalization to SEC company names is still open"
- **Example Mismatch:** "Lockheed Martin Corp" vs "Lockheed Martin Corporation"
- **Current Fix:** Strip legal suffixes (CORP, INC, LTD, etc.) post-normalization
- **Limitation:** Doesn't catch all variant spellings

### Design/Architecture Gaps

#### 1. Public API Restricted by Legal Constraints
- **Short Volume:** Not exposed (FINRA TOS Sec 3.3(a)/(e) bars redistribution)
- **Donor Data:** Aggregated only (52 U.S.C. Sec 30111(a)(4) bars commercial use of raw contributor info)
  - API shows: ticker, committee, cycle, total amount
  - API hides: contributor_name, sub_id per row
- **Implication:** Some internal queries possible, public API intentionally restricted

#### 2. No Multi-Filer Transaction Linking
- **Gap:** Can't easily identify stocks held across multiple members (family offices, trusts)
- **Example:** Track holdings where multiple senators hold AAPL without manual filter

#### 3. Rate Limiting Uniform Across Tiers
- **Current:** 500/day for everyone (per api.py comment)
- **Future:** Tier structure defined in schema but not implemented
- **Code Debt:** `tier` column exists; Stripe integration marked as "when Quantgress API Monetization's paid tiers actually exist"

#### 4. No Historical Pricing Integration
- **Networth Module:** Marks-to-market using Yahoo Finance EOD prices
- **Gap:** No time-series price history stored; requires live requests
- **Implication:** Portfolio valuations not persistent; recompute each run

#### 5. House PDFs Unreliable Before 2018
- **Issue:** Pre-2018 filings render small caps as lowercase glyphs
- **Coverage:** Regex patterns handle most cases but may miss edge cases

#### 6. Senate Annual FD Parsing Fragile
- **Challenge:** HTML table structure varies across filings
- **Parser:** Hand-tuned regex matching on "Annual Report for CY YYYY" labels
- **Limitation:** If Senate redesigns form layout, parsing fails

#### 7. No Amendment Collapse
- **Current:** Form 4 amendments stored as separate rows (accession_number changes)
- **Gap:** "As filed" posture; no logic to collapse amended superseding originals
- **Implication:** Duplicate counts if querying without deduplication

#### 8. Windows-Only Scheduling
- **Daily Module:** Assumes Windows Task Scheduler
- **Gap:** No cron/systemd template for Linux deployments
- **Workaround:** Users must manually configure cron or Task Scheduler equivalent

### Operational Gaps

#### 1. Network-Dependent Scrapers (No Offline Cache)
- **Risk:** If government API/site changes format, scraper breaks mid-run
- **Mitigation:** `--selftest` flag validates parsing logic offline against sample data
- **Gap:** No captured responses stored; each full backfill re-fetches from primary sources

#### 2. Rate Limiting Politeness (No Adaptive Backoff)
- **Approach:** Fixed delays (2-4 seconds per source)
- **Gap:** No exponential backoff on 429/503; just raises exception and stops

#### 3. No Monitoring/Alerting
- **Gap:** Scrapers log to stdout/stderr only; no webhook/email on failure
- **Operational Impact:** Missed runs (e.g., API down) go unnoticed until manual check

#### 4. No Automatic Incremental Backfill
- **Example Gap:** Phase 6 default is current year only (55k-110k filings)
- **Implication:** Multi-year historical backfill requires manual `--year` runs
- **Contracts Worse:** 2.6M awards in 8 months; `--start/--end` needed for backfill

#### 5. No Versioning/Schema Migration
- **Database:** DuckDB single-file; CREATE TABLE IF NOT EXISTS idempotent
- **Gap:** No ALTER TABLE rollout strategy if schema changes needed
- **Risk:** Breaking change could corrupt existing DBs (though resumable scrapers mitigate)

#### 6. Limited Logging
- **Current:** Print-based (redirected to daily.log at scheduler level)
- **Gap:** No structured logging (JSON, timestamps, severity levels)
- **Challenge:** Hard to parse and alert on specific failures

### API Gaps

#### 1. No Pagination Metadata
- **Response:** Plain array of objects
- **Gap:** No total_count, has_more, next_offset hints
- **Workaround:** Client must guess when limit is hit

#### 2. No Bulk Export Endpoint
- **Limitation:** Must iterate with multiple limit/offset calls
- **Gap:** No CSV/Parquet download; no streaming

#### 3. No Full-Text Search
- **Current:** Exact match (eq_ci) or ILIKE substring only
- **Gap:** No BM25/relevance ranking on politician names, company names, etc.

#### 4. No Cross-Dataset Joins Exposed
- **Example:** "Show me lobbying + contracts for AAPL"
- **Gap:** Requires client-side join
- **Workaround:** Use CLI (`q.py`) for custom cross-dataset SQL

#### 5. No Saved Queries / Favorites
- **Gap:** No user-level saved searches
- **Implication:** Researchers can't bookmark/share filter sets

### Scalability/Performance Gaps

#### 1. Single DuckDB File (No Partitioning)
- **Database Size:** ~500MB-1GB estimated (18 datasets, millions of rows)
- **Gap:** No partition pruning by date; all queries scan full tables
- **Optimization:** Year-based partitioning could help Phase 6/7 (large datasets)

#### 2. Embedded DB (No Concurrent Writes)
- **Current:** daily.py runs sequentially; parallel scrapers would block
- **Gap:** No distributed scrapers or worker pool
- **Workaround:** Phase 4's waterfall design (Senate → House → Entities → Short Vol) is intentionally serial

#### 3. API Per-Request Connection Open/Close
- **Rationale:** Ensures fresh snapshots (avoids stale read during daily.py writes)
- **Performance Impact:** Connection overhead; no connection pooling
- **Scaling Concern:** High concurrency could bottleneck (measured viable at 500 req/day)

#### 4. No Caching Layer
- **Example:** /politician/{name} query runs full scan every time
- **Gap:** No Redis/Memcached; no ETag/conditional GET
- **Implication:** Repeated queries hit DB every time

#### 5. No Async Scrapers
- **Current:** Sequential HTTP requests with 0.15-4s delays
- **Gap:** No httpx AsyncClient; no concurrent downloads
- **Impact:** Full scrape run (esp. Phase 7 contracts: 2.6M awards) takes hours

### Data Quality Gaps

#### 1. No Deduplication Across Amendments
- **Gap:** Form 4/A amendments create duplicate rows (different accession_numbers)
- **Workaround:** Query logic must deduplicate or client-side merge

#### 2. No Validation of Dollar Amounts
- **Risk:** OCR'd amounts (Trump trades) or bracket parsing errors undetected
- **Mitigation:** `--selftest` validates parsing logic; no range checks on actual values

#### 3. No Cross-Source Conflict Detection
- **Example:** If SEC insider trading says one date and ProPublica different date
- **Gap:** No conflict flag or priority rule
- **Implication:** Sources of truth assumed consistent

#### 4. No Freshness Metadata
- **Gap:** No last_updated column on rows; no scrape timestamp
- **Implication:** Query result doesn't indicate if underlying data is 1 day or 1 month stale

#### 5. No Data Lineage/Provenance
- **Gap:** `ticker_guess_how` explains *how* ticker guessed, but no full lineage
- **Example:** Can't trace "this company showed up in contracts" back to exact USAspending API response

---

## 6. PYTHON MODULES & DEPENDENCIES

### Core Modules
| Module | Purpose | Entry Points |
|--------|---------|--------------|
| `schema.py` | Shared DDL + amount/date parsing | Import by all scrapers |
| `entities.py` | Entity resolution (ticker guessing) | `main()`, `--dry`, `--selftest` |
| `daily.py` | Daily incremental driver | `main()`, `--selftest` |
| `auth.py` | API key management | CLI + `init_db()` on api startup |
| `api.py` | FastAPI layer | `py -m uvicorn api:app --host 0.0.0.0 --port 8000` |
| `q.py` | CLI query tool (PowerShell-safe) | Direct CLI usage |
| `networth.py` | Politician net worth (floor estimate) | `main()`, `--annual`, `--limit`, `--member` |

### Scraper Modules (All Resumable)
| Phase | Module | Commands |
|-------|--------|----------|
| 1 | `scrape_senate.py` | `main()`, `--limit`, `--selftest` |
| 2 | `scrape_house.py` | `main()`, `--year`, `--limit`, `--ocr-queue`, `--selftest` |
| 6 | `scrape_lobbying.py` | `main()`, `--year`, `--limit`, `--selftest` |
| 7 | `scrape_contracts.py` | `main()`, `--days`, `--start`, `--end`, `--limit`, `--selftest` |
| 9 | `scrape_insiders.py` | `main()`, `--quarter`, `--limit`, `--live`, `--days`, `--selftest` |
| 10 | `scrape_13f.py` | `main()`, `--quarter`, `--limit`, `--selftest` |
| 11 | `scrape_short_volume.py` | `main()`, `--days`, `--start`, `--end`, `--limit`, `--selftest` |
| 12 | `scrape_patents.py` | `main()`, `--days`, `--start`, `--end`, `--limit`, `--selftest` |
| 13 | `scrape_donors.py` | `main()`, `--cycle`, `--limit`, `--selftest` |
| 16 | `scrape_execcomp.py` | `main()`, `--start-year`, `--end-year`, `--limit`, `--selftest` |
| 17 | `scrape_trump.py` | `main()`, `--limit`, `--selftest` |
| 18 | `scrape_senate_annual.py` | `main()`, `--since-year`, `--limit`, `--selftest` |

### Dependencies (requirements.txt)
```
requests           # HTTP client (all scrapers)
beautifulsoup4     # HTML parsing (Senate, House, Trump)
pandas             # Data manipulation, DataFrame output
lxml               # HTML/XML parsing
duckdb             # Embedded OLAP database
pdfplumber         # PDF text extraction
pyyaml             # YAML parsing (networth party lookup)
fastapi            # REST API framework
uvicorn            # ASGI server
httpx               # Async HTTP (currently unused; async gap)
slowapi            # Rate limiting
```

---

## 7. OVERALL ARCHITECTURE SUMMARY

```
┌─────────────────────────────────────────────────────────────┐
│ PRIMARY SOURCES (Public Domain / Government APIs)            │
│ Senate eFD | House Clerk | LDA.gov | USAspending | SEC      │
│ FINRA | USPTO | OpenFEC | Wikimedia | ProPublica            │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│ SCRAPERS (Phase 1-2, 6-7, 9-10, 11-13, 16-18)               │
│ scrape_senate.py        → senate_trades                       │
│ scrape_house.py         → house_trades                        │
│ scrape_lobbying.py      → lobbying_filings                    │
│ scrape_contracts.py     → gov_contracts                       │
│ scrape_insiders.py      → insider_trades                      │
│ scrape_13f.py           → f13_holdings (+ 2 views)            │
│ scrape_short_volume.py  → short_volume (not API-exposed)      │
│ scrape_patents.py       → patents                             │
│ scrape_donors.py        → corporate_donations (+ agg view)    │
│ scrape_execcomp.py      → exec_comp                           │
│ scrape_trump.py         → trump_trades_clean                  │
│ scrape_senate_annual.py → fd_assets, fd_liabilities           │
│                                                                │
│ All: resumable via uniqueness checks, --selftest offline      │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│ ENTITY RESOLUTION (Phase 8)                                  │
│ entities.py                                                  │
│ ├─ Extract: Embedded tickers (congress trades)               │
│ └─ SEC Name Exact: Free-text names → SEC company_tickers     │
│                                                                │
│ Outputs: *_guess + *_guess_how columns (reversible)          │
│ Registers: congress, lobbying, contracts, 13f, patents, donors│
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│ DATABASE: congress_trades.duckdb (DuckDB OLAP)               │
│ ├─ Core views: trades (senate + house unified)               │
│ ├─ 13 normalized datasets (18 total with derived views)       │
│ ├─ api_keys table (auth)                                      │
│ └─ Schema: shared schema.py (idempotent CREATE TABLE IF...)   │
└──────────────────────────────────────────────────────────────┘
                              ↓
            ┌────────────┬──────────────┬─────────┐
            ↓            ↓              ↓         ↓
        ┌─────────┐  ┌─────────┐  ┌──────┐  ┌──────┐
        │ daily.py│  │ api.py  │  │q.py  │  │net.. │
        │(cron)   │  │(FastAPI)│  │(CLI) │  │worth │
        └─────────┘  └─────────┘  └──────┘  └──────┘
                          ↓
                    ┌────────────┐
                    │ REST API   │
                    │ /trades    │
                    │ /politician│
                    │ /ticker    │
                    │ /{dataset} │
                    └────────────┘
                          ↓
                  REST Clients / Dashboards
```

---

## 8. KEY METRICS & SCALE

| Dataset | Rows (Est.) | Source | Scraper | Cadence |
|---------|-------------|--------|---------|---------|
| senate_trades | ~500 | eFD | scrape_senate.py | Daily |
| house_trades | ~500 | House Clerk | scrape_house.py | Daily |
| lobbying_filings | 55-110k/yr | LDA.gov | scrape_lobbying.py | Annual (manual --year) |
| gov_contracts | 2.6M (partial 2026) | USAspending | scrape_contracts.py | Rolling 7-day |
| insider_trades | Millions (2006+) | SEC | scrape_insiders.py | Quarterly + daily live |
| f13_holdings | Millions (quarterly) | SEC | scrape_13f.py | Quarterly |
| short_volume | Millions (daily) | FINRA | scrape_short_volume.py | Daily (not API) |
| patents | 10k-50k/month | USPTO | scrape_patents.py | As-needed |
| corporate_donations | Millions | FEC/OpenFEC | scrape_donors.py | Cycle-based (2yr) |
| pageviews | Per-request | Wikimedia | (ad-hoc) | As-needed |
| exec_comp | 1000+ FY2023 | SEC | scrape_execcomp.py | Annual |
| trump_trades_clean | ~500 | ProPublica/DocumentCloud | scrape_trump.py | Manual |
| fd_assets | ~8k (300 senators × years) | Senate eFD | scrape_senate_annual.py | Annual |
| fd_liabilities | ~8k | Senate eFD | scrape_senate_annual.py | Annual |

---

## Conclusion

**Quantgress is a well-architected, production-ready political-finance platform** with thoughtful design choices (DuckDB, reversible guesses, resumable scrapers, read-only API). However, it has several **intentional scope limitations** (no OCR, no fuzzy matching, no derivatives) and **operational gaps** (no monitoring, Windows-only scheduling, limited logging) that would need to be addressed for a multi-user production deployment. The codebase prioritizes **auditability and correctness over feature completeness**, which is appropriate for a political-data tool where data integrity is paramount.
