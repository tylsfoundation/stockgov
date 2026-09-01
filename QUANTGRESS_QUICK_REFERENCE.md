# Quantgress Quick Reference

## What It Is
**Quantgress** = Open-source alternative to Quiver Quantitative
- Scrapes 18 public government datasets
- Stores in single DuckDB file
- Serves via FastAPI REST API
- Focused on Congressional stock trading + related political-finance data

---

## 18 Data Phases

| Phase | Dataset | Source | Module | Status |
|-------|---------|--------|--------|--------|
| 1 | Senate trades (PTR) | efdsearch.senate.gov | scrape_senate.py | ✅ |
| 2 | House trades (PTR) | disclosures-clerk.house.gov | scrape_house.py | ✅ (no OCR) |
| 3 | Ticker resolution | Embedded in fields | entities.py | ✅ 98% coverage |
| 4 | Daily incremental | — | daily.py | ✅ |
| 5 | REST API | — | api.py | ✅ |
| 6 | Corporate lobbying | lda.gov | scrape_lobbying.py | ✅ |
| 7 | Gov contracts | usaspending.gov | scrape_contracts.py | ✅ |
| 8 | Entity resolution | SEC company_tickers.json | entities.py | ✅ (refactored Phase 3) |
| 9 | Insider trades (Form 4) | SEC EDGAR | scrape_insiders.py | ✅ |
| 10 | 13F holdings | SEC 13F data | scrape_13f.py | ✅ |
| 11 | Short volume | FINRA | scrape_short_volume.py | ✅ (not in API) |
| 12 | Patents | USPTO | scrape_patents.py | ✅ |
| 13 | Donations | OpenFEC | scrape_donors.py | ✅ |
| 14 | Wikipedia pageviews | Wikimedia | (ad-hoc) | ✅ |
| 15 | Net worth (derived) | — | networth.py | ✅ (mark-to-market) |
| 16 | Exec comp (Pay vs Perf) | SEC XBRL | scrape_execcomp.py | ✅ |
| 17 | Trump trades | ProPublica/DocumentCloud | scrape_trump.py | ✅ |
| 18 | Senate annual FD | efdsearch.senate.gov | scrape_senate_annual.py | ✅ |

---

## Key Files

### Core Application
- **schema.py** — Shared DDL + parsing logic
- **entities.py** — Ticker guessing (extract + SEC name match)
- **daily.py** — Cron driver (Senate → House → Entities → Short Vol)
- **api.py** — FastAPI + auth
- **auth.py** — API key management
- **q.py** — CLI query tool (PowerShell-safe)
- **networth.py** — Politician net worth (mark-to-market)

### Scrapers (All Resumable)
- **scrape_senate.py** — Senate PTRs
- **scrape_house.py** — House PTRs (plus --ocr-queue for scanned)
- **scrape_lobbying.py** — LDA filings
- **scrape_contracts.py** — USAspending awards
- **scrape_insiders.py** — SEC Form 4 (bulk + live)
- **scrape_13f.py** — SEC Form 13F (quarterly)
- **scrape_short_volume.py** — FINRA daily files
- **scrape_patents.py** — USPTO granted patents
- **scrape_donors.py** — FEC corporate donations
- **scrape_execcomp.py** — SEC Pay vs. Performance
- **scrape_trump.py** — Trump 278-T filings (OCR'd)
- **scrape_senate_annual.py** — Senate annual disclosures

---

## Database

**File:** `congress_trades.duckdb` (DuckDB OLAP)

### Key Tables/Views
```
Core:
  - trades (view: senate_trades ∪ house_trades with ticker coalescing)

Datasets:
  - lobbying_filings
  - gov_contracts
  - insider_trades
  - f13_holdings, f13_changes (view), f13_top_holders (view)
  - short_volume (not in public API)
  - patents
  - corporate_donations_raw → corporate_donations_agg (view, legal limit)
  - pageviews
  - exec_comp
  - trump_trades_clean
  - fd_assets, fd_liabilities (Senate annual)

Auth:
  - api_keys (SHA256-hashed keys)
```

### Design Principle
All guesses in `*_guess` + `*_guess_how` columns (never overwrite real values)

---

## API

**Framework:** FastAPI + SlowAPI rate limiting (500 req/day)

### Public Endpoint
```
POST /signup (5 req/day per IP, one active key per email)
  Body: {"email": "..."}
  Response: {"api_key": "qg_live_..."}
```

### Protected Routes (Require X-API-Key Header)
```
GET /                        # Dataset counts + usage
GET /trades                  # Congress trades
GET /politician/{name}       # One politician's trades + summary
GET /ticker/{symbol}         # One ticker's trades
GET /{dataset}              # Generic: lobbying, contracts, insiders, 13f-*, 
                            #          patents, donors, pageviews, exec-comp, 
                            #          trump-trades, senate-assets, senate-liabilities
```

### Filter Modes
- **eq** — Exact (integer columns)
- **eq_ci** — Case-insensitive exact (tickers)
- **ilike** — Substring (names)

### Query Parameters
- `limit` (default 100, max 1000)
- `offset` (default 0)
- Dataset-specific filters (chamber, tkr, last_name, tx_type, asset_type, etc.)

---

## Critical Design Decisions

### Why DuckDB (not Postgres)?
- Single-file, zero-server embedded OLAP engine
- Right fit for personal research data queried far more than written
- Easier deployment + no operational overhead

### Why Fresh Connection Per Request?
- Read-only connection opened at startup would see stale snapshot
- daily.py writes new rows continuously
- Fresh connection per request ensures each response reflects latest writes

### Why Reversible Guesses?
- `ticker_guess` never overwrites real `ticker`
- Every inferred value has `*_guess_how` provenance
- Can audit/reverse any guess with one UPDATE

### Why No Fuzzy Matching?
- Phase 3 lesson: Fuzzy mapped ABB Ltd. → ABLZF (wrong) instead of ABBNY
- Current approach: Normalization + exact match only
- Ambiguous normalized names dropped, not guessed

### Why No OCR Path?
- ~87% of House PTRs are born-digital (pdfplumber text layer sufficient)
- ~13% scanned (worse in older years); queued via --ocr-queue
- OCR results require manual QA; not worth dependency cost

---

## Major Gaps & Limitations

### Data Coverage
- ❌ House OCR path not built (~13% scanned PDFs unprocessed)
- ❌ Ticker resolution only 98% (2% may be legitimate: bonds, munis, private LLCs)
- ❌ Insider trading: Only Table I (non-derivatives) included
- ❌ Trump trades: OCR noise in descriptions
- ❌ Wikipedia pageviews: No systematic company→article mapping
- ⚠️ Form 13F: Fragile join via CUSIP + free-text issuer_name

### API Design
- ❌ No pagination metadata (total_count, has_more)
- ❌ No bulk export (CSV/Parquet download)
- ❌ No full-text search (exact/ILIKE only)
- ❌ No cross-dataset joins exposed
- ❌ Short volume not exposed (FINRA TOS restriction)
- ❌ Donor data aggregated only (FEC legal restriction)

### Operational
- ❌ Windows Task Scheduler only (no cron/systemd template)
- ❌ No monitoring/alerting on scraper failures
- ❌ No adaptive backoff (fixed delays; raises on 429/503)
- ❌ Minimal logging (print-based, not structured)
- ❌ Async scrapers not implemented (sequential HTTP)

### Performance/Scale
- ❌ Single DuckDB file (no partitioning)
- ❌ No connection pooling (fresh open/close per API request)
- ❌ No caching layer (Redis/Memcached)
- ⚠️ Phase 7 contracts: 2.6M rows; full backfill takes hours

### Data Quality
- ❌ Form 4 amendments stored as duplicates (different accession_numbers)
- ❌ No deduplication across amendments
- ❌ No freshness metadata (no last_updated on rows)
- ❌ No conflict detection across sources

---

## Common Operations

### Run All Scrapers
```bash
py daily.py                    # Senate + House + Entities + Short Vol
```

### Start API
```bash
py -m uvicorn api:app --host 0.0.0.0 --port 8000
```

### Query Database (CLI)
```bash
py q.py "SELECT COUNT(*) FROM trades WHERE tkr='AAPL'"
py q.py --types                # Asset types + counts
py q.py --tickered --limit 100 # Only rows with ticker
```

### Issue API Key (CLI)
```bash
py auth.py issue someone@example.com    # Create + print key (once)
py auth.py list                          # Every issued key + status
py auth.py revoke <raw_key>              # Disable a key
```

### Check Parser Logic Offline
```bash
py scrape_senate.py --selftest           # No network call
py entities.py --selftest                # Offline validation
py api.py --selftest                     # Route check
```

### Bounded Test Run
```bash
py scrape_house.py --year 2026 --limit 20   # 20 filings only
py scrape_lobbying.py --year 2026 --limit 20
py scrape_contracts.py --limit 20
```

---

## Dependencies

```
requests         # HTTP client
beautifulsoup4   # HTML parsing
pandas           # DataFrames
lxml             # XML parsing
duckdb           # Database
pdfplumber       # PDF extraction
pyyaml           # YAML parsing
fastapi          # REST framework
uvicorn          # ASGI server
httpx            # HTTP (async unused)
slowapi          # Rate limiting
```

---

## Summary: Capabilities vs. Quiver Quantitative

| Aspect | Quantgress | Quiver | Notes |
|--------|------------|--------|-------|
| Congressional trades | ✅ | ✅ | Full coverage, open-source |
| Insider trading | ✅ | ✅ | Quantgress: Table I only |
| 13F holdings | ✅ | ✅ | Quantgress: Free |
| Lobbying | ✅ | ✅ | Both sources from LDA |
| Gov contracts | ✅ | ✅ | Quantgress: 2.6M rows |
| Patents | ✅ | ✅ | Quantgress: Free USPTO |
| Donations | ⚠️ Agg | ✅ | Quantgress: Legal restriction |
| Pageviews | ⚠️ Ad-hoc | ✅ | Quantgress: No systematic |
| Net worth | ✅ Floor | ✅ | Quantgress: Mark-to-market only |
| Trump trades | ✅ | ✅ | Quantgress: OCR'd |
| API | ✅ Read-only | ✅ | Quantgress: 500 req/day free |
| **Deployment** | **Self-hosted** | **SaaS** | |
| **Cost** | **$0** | **$$$ 200+/mo** | |
| **Control** | **Full** | **Limited** | |
| **Downtime** | **User responsible** | **Quiver responsible** | |

---

## For Further Investigation

1. **Volume of data** — Full database size on disk?
2. **Query performance** — Benchmarks on trades view (1000+ rows)?
3. **Entity resolution accuracy** — How many false positives in SEC name matching?
4. **Deployment guide** — Step-by-step for Windows/Linux?
5. **Integration examples** — Dashboard/notebook examples?
