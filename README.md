# Congressional Stock Trading Research and Analytics

StockGov is a data-engineering and analytics project for collecting, normalizing, and researching congressional financial-disclosure records. The immediate focus is building a reliable PostgreSQL foundation that connects legislators, terms, committees, offices, identifiers, disclosure filings, documents, and extracted periodic transaction report data.

## Questions the platform is intended to answer

With the completed data pipeline, users should be able to ask questions such as:

- Which members filed the most periodic transaction reports in a year?
- Which members from a selected state filed the most PTRs over a date range?
- Which disclosure documents have already been downloaded or processed?
- Which filings for a member and year still need to be downloaded?
- What securities were bought or sold, in what reported value range, and by whom?
- Which committees does a member currently serve on?
- Who currently serves on a selected committee?
- How did a trade perform after the transaction date and after the disclosure date?
- How did congressional portfolios compare with a benchmark such as SPY?
- How did the party composition of Congress change under each president?

Mike Crapo is the current end-to-end example member for database export and report generation. Nancy Pelosi remains a planned House test case for the full disclosure and PTR workflow.

## Current status

**Current stage: Congressional reference data loaded and validated; financial-disclosure ingestion is the next major phase.**

Completed or working:

- PostgreSQL 16 service defined in Docker Compose with persistent storage and a health check
- Normalized 38-table database schema covering provenance, members, terms, committees, executives, filings, documents, securities, trades, prices, and staging data
- Idempotent database creation and interactive reset utilities
- Downloader for the eight supported `congress-legislators` datasets, including YAML, JSON, and available CSV formats
- One-generation `V1` source-file backups and a download manifest
- Loader for congressional YAML reference data with progress reporting and source audit records
- Read-only QA program that compares raw YAML files with PostgreSQL and writes results to the screen and `logs/qaresults.log`
- House financial-disclosure index archives for 2008 through 2026 in ZIP, XML, and text formats
- House filing-type reference examples and documentation
- Senate electronic financial-disclosure research notes
- Read-only member export program, currently using Mike Crapo as its example subject
- Example Mike Crapo Word profile in `docs/Mike_Crapo_Member_Profile.docx`
- Minimal FastAPI health endpoints under `backend/app/main.py`

Not yet implemented:

- Loading House disclosure index XML files into the filing catalog
- Senate disclosure search and ingestion pipeline
- Selective PDF download queue and member/year processing tracker
- PDF text extraction, OCR fallback, and PTR transaction parsing
- Security normalization, ticker resolution, and transaction evidence storage
- Historical market prices, corporate actions, and performance calculations
- Production REST API endpoints
- Search and analytics web interface
- Scheduled nightly ingestion and monitoring

## Latest data quality result

The latest QA run completed on September 4, 2026 in approximately 28 seconds:

- 61 checks passed
- 0 warnings
- 1 check failed
- All eight canonical YAML files parsed
- All 38 required PostgreSQL tables were present
- No orphaned core relationships or duplicate key entities were found
- No congressional staging rows were rejected

The single known mismatch is `leadership_roles`: the source expectation is 157 rows and PostgreSQL contains 156. This should be resolved before the congressional reference-data import is considered fully clean.

Selected validated counts from that run:

| Dataset or entity | Rows |
| --- | ---: |
| Unique Bioguide members | 12,770 |
| Congressional terms | 45,535 |
| Legislator identifiers | 97,319 |
| Dated party affiliations | 59 |
| Family relationships | 53 |
| District offices | 1,306 |
| Social accounts | 1,731 |
| Current committee memberships | 3,895 |
| Executives | 80 |
| Executive terms | 131 |
| Executive identifiers | 307 |
| Staged member source rows | 13,903 |
| Staged committee rows | 742 |

See `logs/qaresults.log` for the complete result.

## Architecture

```text
Source downloads
      ↓
Raw immutable files and manifests
      ↓
Python loaders and staging tables
      ↓
Normalized PostgreSQL database
      ↓
Document selection and processing
      ↓
PTR transactions and market analytics
      ↓
FastAPI service and web interface
```

PostgreSQL is the system of record. Original source rows, source hashes, local paths, and extraction evidence are retained so transformed records can be traced back to their source.

## Repository layout

| Path | Purpose | Status |
| --- | --- | --- |
| `schema.md` | Authoritative table, field, and relationship design | Current |
| `scripts/` | Database creation, reset, downloads, loading, validation, and member export | Active |
| `data/raw/congress/` | Canonical congressional reference datasets and backups | Populated |
| `data/raw/houseofreptrans/` | House annual filing indexes for 2008–2026 | Populated |
| `data/raw/houseofrepforms/` | House filing-type notes and sample forms | Populated |
| `data/raw/senateforms/` | Senate EFD research notes | Started |
| `docs/` | Design comparisons and generated reports | Active |
| `logs/` | QA and example-member exports | Active |
| `backend/` | FastAPI application scaffold | Early scaffold |
| `frontend/` | Planned web interface | Not implemented |
| `ingestion/` | Planned reusable ingestion package | Early scaffold |
| `tests/` | Planned automated tests | Early scaffold |
| `congress-legislators-main/` | Read-only upstream reference repository | Reference only |
| `Quantgress-main/` | Read-only reference implementation and DuckDB dataset | Reference only |

The two reference repositories are not StockGov ingestion inputs and should be ignored by StockGov download and load automation unless they are being inspected deliberately.

## Data sources currently represented

### Congressional reference data

The downloader retrieves these datasets from the `unitedstates/congress-legislators` published data endpoint:

- `legislators-current`
- `legislators-historical`
- `legislators-social-media`
- `legislators-district-offices`
- `committees-current`
- `committees-historical`
- `committee-membership-current`
- `executive`

YAML is the canonical database-loading format because it preserves the nested source structure. JSON and CSV copies are retained where available for inspection and interoperability.

### Financial disclosures

- House annual disclosure indexes: `data/raw/houseofreptrans/2008FD` through `2026FD`
- House form definitions and examples: `data/raw/houseofrepforms/`
- Senate EFD research: `data/raw/senateforms/senateform.md`

The House annual indexes identify filings and document IDs; individual transaction details generally require downloading and parsing the associated PTR document. The intended strategy is to load all lightweight index metadata first, then selectively download documents by member, state, year, filing type, and prior processing status instead of brute-force downloading every PDF.

## Configuration

Copy `.env.example` to `.env` and set local credentials. Do not commit `.env`.

The scripts use `DATABASE_URL` when present. Otherwise they read:

- `POSTGRES_HOST`
- `POSTGRES_PORT`
- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`

Docker Compose exposes PostgreSQL on host port `5433` by default to avoid conflicts with another local PostgreSQL service using `5432`.

## Setup and common commands

Install the current Python dependencies:

```powershell
py -m pip install psycopg2-binary PyYAML python-dotenv
```

Start PostgreSQL:

```powershell
docker compose up -d postgres
docker compose ps
```

Create missing database infrastructure without removing existing data:

```powershell
py scripts/create_database.py
```

Download or refresh congressional reference files:

```powershell
py scripts/downloadsource.py
```

Validate source files without loading them:

```powershell
py scripts/load_congress_data.py --validate-only
```

Load the canonical YAML reference data:

```powershell
py scripts/load_congress_data.py
```

Run database integrity and source-comparison QA:

```powershell
py scripts/validate_congress_data.py
```

Export all currently stored information for the example member:

```powershell
py scripts/export_example_member.py
```

The member export is written to `logs/example_member.log` in structured Markdown suitable for review or report generation.

Reset operations are destructive and interactive. Review the choices before confirming:

```powershell
py scripts/reset_database.py
```

## Docker and application services

Only the PostgreSQL service is currently enabled in `docker-compose.yml`. The backend and frontend definitions are commented out until their Dockerfiles and application implementations are ready.

The minimal FastAPI application exposes `/` and `/health`, but it is not yet connected to production API routes.

## Important data rules

- Preserve raw downloaded files and provenance metadata.
- Use stable external identifiers, especially Bioguide ID, to connect members across sources.
- Do not infer that a zero database count means no real-world record exists; it may mean that ingestion has not run for that domain.
- Keep current and historical information distinct.
- Make loaders restartable and idempotent.
- Track document download, processing, extraction, and evidence status so overlapping member/year selections do not repeat completed work.
- Keep credentials in `.env`, never in committed source files.

## Key documentation

- `schema.md` — database tables, fields, and relationships
- `ARCHITECTURE_POLICY.md` — project architecture rules
- `CONGRESS_LEGISLATORS_REVIEW.md` — congressional source analysis
- `docs/SOURCE_SCHEMA_COMPARISON.md` — source-to-schema comparison
- `data/raw/houseofrepforms/houseofrepform.md` — House filing-type definitions
- `data/raw/senateforms/senateform.md` — Senate disclosure research
- `QUANTGRESS_ANALYSIS.md` and `QUANTGRESS_QUICK_REFERENCE.md` — notes from the Quantgress reference application

## Longer-term direction

After disclosure ingestion and PTR parsing are stable, the project can expand into market-price analysis, legislation and committee activity, lobbying and donor datasets, government contracts, corporate events, official social posts, scheduled refreshes, and natural-language querying through an API-backed interface.

## License

See the applicable license files for upstream reference projects and datasets. A StockGov project license has not yet been documented in this repository.
