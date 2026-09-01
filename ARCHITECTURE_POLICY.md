# Architecture & Reference Projects Policy

## Overview

StockGov is built on reference implementations from two upstream projects. These projects are **READ-ONLY** and serve as architecture templates and data sources. They must never be modified within this repository.

---

## Reference Projects (Read-Only)

### 1. congress-legislators-main/
**Upstream:** https://github.com/unitedstates/congress-legislators  
**Purpose:** Authoritative U.S. Congressional member directory and committee data  
**Location:** `congress-legislators-main/` (root level)  
**Status:** ⛔ READ-ONLY  
**License:** CC0 (Public Domain)  

**Use Cases:**
- ✅ Reference for member data schema and field definitions
- ✅ Extract YAML parsing patterns
- ✅ Understand committee membership structure
- ✅ Cross-walk ID systems (bioguide, govtrack, opensecrets, etc.)

**When You Need It:**
- Designing StockGov's `members` table schema
- Building member entity resolution logic
- Joining trade data with member metadata

**Related Documentation:**
- See `CONGRESS_LEGISLATORS_REVIEW.md` for detailed analysis

---

### 2. Quantgress-main/
**Upstream:** Reference implementation for Congressional data scraping  
**Purpose:** Complete framework for Congressional trade, SEC, lobbying, contracts data ingestion  
**Location:** `Quantgress-main/` (root level)  
**Status:** ⛔ READ-ONLY  

**Use Cases:**
- ✅ Reference for trade scraper patterns (Senate PTRs, House PDFs)
- ✅ Reference for SEC Form 4/13F scraping logic
- ✅ Entity resolution techniques (ticker guessing, name matching)
- ✅ Daily incremental update driver pattern
- ✅ Database schema design (adapted for PostgreSQL)
- ✅ API design patterns and rate limiting approaches

**When You Need It:**
- Building `ingestion/congress/` trade scrapers
- Implementing `ingestion/sec/` Form 4 data ingest
- Designing entity resolution/ticker matching
- Understanding multi-source data reconciliation

**Key Files to Reference:**
- `scrape_senate.py` — Senate PTR scraping
- `scrape_house.py` — House PTR scraping with PDF handling
- `scrape_insiders.py` — SEC Form 4 scraping pattern
- `entities.py` — Ticker and entity resolution logic
- `schema.py` — Database schema (DuckDB; adapt to PostgreSQL)
- `daily.py` — Incremental update orchestration
- `api.py` — REST API design and rate limiting

**Related Documentation:**
- See `QUANTGRESS_ANALYSIS.md` for 18-phase capabilities breakdown
- See `QUANTGRESS_QUICK_REFERENCE.md` for quick lookup of modules and endpoints

---

## Copy-Not-Modify Pattern

### ✅ DO

1. **Copy scraper logic from Quantgress** into appropriate `ingestion/` modules
   ```
   Quantgress-main/scrape_senate.py 
     → Adapt and copy to ingestion/congress/senate_scraper.py
   ```

2. **Adapt YAML parsing** from congress-legislators-main for StockGov
   ```
   congress-legislators-main/legislators-current.yaml
     → Study structure, create loader in ingestion/members/loader.py
   ```

3. **Reference schema patterns** from both projects
   ```
   Quantgress-main/schema.py 
     → Inform database/sql/001_initial_schema.sql (PostgreSQL version)
   ```

4. **Reuse tested logic** by implementing equivalent versions for PostgreSQL/FastAPI
   ```
   Quantgress-main/entities.py (DuckDB entity resolution)
     → Create backend/app/services/entity_resolution.py (PostgreSQL version)
   ```

### ❌ DON'T

- 🚫 Modify any files in `congress-legislators-main/`
- 🚫 Modify any files in `Quantgress-main/`
- 🚫 Commit changes to either reference project
- 🚫 Create issues/PRs in either reference repo from this StockGov instance
- 🚫 Directly import from reference projects in production code (copy + adapt instead)

---

## Data Flow & Adaptation Pattern

```
congress-legislators-main/ (READ)
    ├── legislators-current.yaml
    ├── committees-current.yaml
    └── committee-membership-current.yaml
         ↓ STUDY SCHEMA
         ↓ ADAPT & COPY
    StockGov: ingestion/members/
         ├── loader.py (YAML parser)
         ├── normalizer.py (adapt congress-legislators schema)
         └── test_data/
             └── sample_legislators.yaml (copy from reference for testing)

Quantgress-main/ (READ)
    ├── scrape_senate.py
    ├── scrape_house.py
    ├── scrape_insiders.py
    ├── entities.py
    └── schema.py
         ↓ STUDY PATTERNS
         ↓ ADAPT & COPY
    StockGov: ingestion/
         ├── congress/
         │   ├── senate_scraper.py (adapted from Quantgress)
         │   └── house_scraper.py (adapted from Quantgress)
         ├── sec/
         │   └── form4_scraper.py (adapted from Quantgress insiders)
         └── common/
             └── entity_resolution.py (adapted from Quantgress entities.py)

    StockGov: database/sql/
         └── 001_initial_schema.sql
             (reference both, but PostgreSQL not DuckDB)

    StockGov: backend/app/
         ├── api/routes.py (reference Quantgress api.py patterns)
         └── services/ (reference Quantgress business logic)
```

---

## Documentation References

Three analysis documents have been created to guide adaptation:

1. **[QUANTGRESS_ANALYSIS.md](../QUANTGRESS_ANALYSIS.md)**
   - Deep 8-section breakdown of Quantgress architecture
   - 18 data sources with modules, formats, update cadences
   - 5 categories of gaps and limitations
   - Detailed process/transformation documentation

2. **[QUANTGRESS_QUICK_REFERENCE.md](../QUANTGRESS_QUICK_REFERENCE.md)**
   - Executive summary with 18-phase table
   - API endpoints cheat sheet
   - Design decision rationale
   - Common operations examples

3. **[CONGRESS_LEGISLATORS_REVIEW.md](../CONGRESS_LEGISLATORS_REVIEW.md)**
   - Member data schema and field definitions
   - Committee structure documentation
   - Data quality assessment
   - PostgreSQL schema design recommendations
   - Integration patterns for StockGov

---

## When Reference Projects Get Updated

### Upstream Updates to Reference Projects

If the upstream repos (congress-legislators or Quantgress) release major updates:

1. **For congress-legislators:** 
   - Pull the latest version (via git submodule or manual update)
   - Review changes for new member data, committee assignments
   - Update StockGov's member ingestion patterns as needed
   - Test against new field definitions

2. **For Quantgress:**
   - Review scraper improvements or bug fixes
   - Update adapted code in StockGov's ingestion/ modules accordingly
   - Do NOT directly use Quantgress code; keep a copy-and-adapt barrier

### Preventing Accidental Modifications

**Best Practices:**
- Treat `congress-legislators-main/` and `Quantgress-main/` as "libraries" that you read from
- Create `.gitignore` entries to prevent accidental commits (already included)
- Document in PR reviews: "Adapted from Quantgress" or "Based on congress-legislators pattern"
- Link to reference repos in code comments when copying logic

---

## StockGov Actual Development Directories

Use these directories for **actual implementation code** (not read-only):

| Directory | Purpose | Patterns From |
|-----------|---------|---|
| `ingestion/members/` | Member data loading & normalization | congress-legislators-main |
| `ingestion/congress/` | Senate/House trade disclosure scraping | Quantgress-main |
| `ingestion/sec/` | SEC Form 4/13F/8-K scraping | Quantgress-main |
| `ingestion/prices/` | Stock price data ingestion | (Quantgress lacks this) |
| `ingestion/contracts/` | Government contracts ingestion | Quantgress-main pattern |
| `ingestion/common/` | Entity resolution, utilities | Quantgress-main (entities.py) |
| `database/sql/` | PostgreSQL schema & migrations | Both (DuckDB → PostgreSQL) |
| `backend/app/models/` | SQLAlchemy ORM models | congress-legislators + Quantgress schemas |
| `backend/app/services/` | Business logic & processing | Adapted from Quantgress |
| `backend/app/api/` | REST API endpoints | Quantgress api.py patterns |

---

## Summary

✅ **congress-legislators-main/** = Read-only member data reference  
✅ **Quantgress-main/** = Read-only scraper/ingestion reference  
✅ **StockGov/** = Development repo (copy & adapt from reference projects)  

Never modify the reference projects. Always copy logic/data into StockGov and adapt for PostgreSQL + FastAPI architecture.
