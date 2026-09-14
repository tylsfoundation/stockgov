# StockGov script guide

## Quick reference

- **create_database.py** — Creates the PostgreSQL database when needed and applies the complete StockGov schema.
- **validate_new_cols.py** — Checks that the required House staging columns are present in PostgreSQL.
- **downloadsource.py** — Downloads the congress-legislators reference datasets and records a manifest.
- **load_congress_data.py** — Loads the congress-legislators YAML reference data into the normalized database tables.
- **validate_congress_data.py** — Compares the congress-legislators source files with PostgreSQL in a read-only QA run.
- **load_house_filings.py** — Loads annual House financial-disclosure XML indexes into the filing catalog.
- **validate_house_filings.py** — Compares every House XML source row with its staging and normalized database records.
- **download_house_documents.py** — Downloads House disclosure PDFs selected from the filing catalog.
- **parse_house_documents.py** — Extracts text from House PTR PDFs and inserts normalized transactions into `trades`.
- **validate_house_pdf_parsing.py** — Reports the document, extraction, and trade chain and checks PDF-ingestion integrity.
- **export_example_member.py** — Exports all stored StockGov information for Mike Crapo as a Markdown report.
- **reset_database.py** — Interactively deletes StockGov rows or drops StockGov tables for maintenance.

<div style="page-break-after: always;"></div>

## Using this guide

Run commands from the project root, `C:\Home\StockGov`:

```powershell
python scripts\script_name.py [options]
```

If `python` is not the intended interpreter, replace it with the full path to the Python 3.12 executable. Do not repeat the `scripts` directory in the path.

Most database scripts read `C:\Home\StockGov\.env`. `DATABASE_URL` is preferred. When it is absent, scripts generally use `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD`; the project defaults use PostgreSQL on host port `5433` and database `congress_trades`. A `--database-url` option is documented below when that script exposes one.

The scripts are intentionally separated into source acquisition, loading, and validation steps. A validation-only mode never writes database rows. Download, load, parse, and reset modes do write database rows or files as described in their sections.

## 1. Create and check the schema

### `create_database.py`

**Purpose.** Creates the configured database if it does not exist, then applies the complete idempotent StockGov schema, including tables, constraints, indexes, triggers, and migration-safe column additions.

**Run.**

```powershell
python scripts\create_database.py
```

**Options.**

| Option | Meaning |
|---|---|
| `--database-url URL` | Override the database URL from `.env`. |
| `--skip-create-database` | Skip the maintenance-database connection and apply the schema only to the target database. Use when the database already exists or the database user cannot create databases. |
| `--print-sql` | Print the complete schema SQL and exit without connecting to PostgreSQL. |
| `-h`, `--help` | Show command help. |

**Expected output and destination.** On success it prints `Database '...' created/already existed; StockGov schema is ready.` It writes no files; the output is the PostgreSQL schema. `--print-sql` writes SQL to the terminal only.

**Exit status.** `0` means the schema is ready; `1` means configuration or database setup failed.

### `validate_new_cols.py`

**Purpose.** Performs a small read-only compatibility check for `staging_house_filings.prefix_raw`, `suffix_raw`, and `state_district_raw`. The full schema check is performed by `create_database.py`; this utility checks these specific columns.

**Run.**

```powershell
python scripts\validate_new_cols.py
```

**Options and parameters.** There are no command-line options. It reads the database settings from `.env`.

**Expected output and destination.** The terminal shows each required table/column as `PASS` or `FAIL`, followed by `RESULT: PASS` or `RESULT: FAIL`. It writes no file and changes no database data.

**Exit status.** `0` means all required columns exist; `1` means a column or table is missing; `2` means configuration or connection failure.

## 2. Acquire and load congressional reference data

### `downloadsource.py`

**Purpose.** Downloads the current congress-legislators reference files from `https://unitedstates.github.io/congress-legislators`. It rotates one backup copy before replacing each current file.

**Run.**

```powershell
python scripts\downloadsource.py
```

**Options.**

| Option | Meaning |
|---|---|
| `--output-directory PATH` | Destination for source files. Default: `data/raw/congress`. |
| `--timeout SECONDS` | HTTP timeout for each file. Default: `120`. |
| `-h`, `--help` | Show command help. |

**Expected output and destination.** The script downloads the supported YAML, JSON, and CSV files into the output directory, moves an existing current file to a `V1` backup such as `legislators-currentV1.yaml`, and writes `download_manifest.json` containing URLs, byte counts, and SHA-256 hashes. It prints one progress line per file. It does not connect to PostgreSQL.

**Exit status.** `0` means all source files downloaded; `1` means a download or file operation failed. A failed run leaves completed downloads and the rotated backups in place.

### `load_congress_data.py`

**Purpose.** Validates and loads the eight supported congress-legislators YAML files into normalized member, term, committee, office, social-account, and executive tables. It also records source snapshots/import audit rows and preserves source records in staging tables where applicable. The loader is idempotent at the normalized-table level.

**Run.**

```powershell
python scripts\load_congress_data.py
```

**Options.**

| Option | Meaning |
|---|---|
| `--source-dir PATH` | Directory containing the YAML files. Default: `data/raw/congress`. |
| `--database-url URL` | Override `DATABASE_URL` for this run. |
| `--current-congress N` | Congress number assigned to current committee memberships. Default: calculated from today’s date. |
| `--validate-only` | Parse and count selected YAML files without connecting to PostgreSQL. |
| `--progress-every N` | Print database-load progress every N source rows. Use `0` to disable progress lines. Default: `250`. |
| `--files FILE ...` | Load only selected supported files. Without this option, all supported files are loaded. |
| `-h`, `--help` | Show command help. |

Supported names for `--files` are `legislators-historical.yaml`, `legislators-current.yaml`, `committees-historical.yaml`, `committees-current.yaml`, `committee-membership-current.yaml`, `legislators-district-offices.yaml`, `legislators-social-media.yaml`, and `executive.yaml`.

**Expected output and destination.** `--validate-only` prints a valid-record count for each file and ends with `Validation complete: ...; no database connection made`. A load prints per-file `read`, `inserted`, `updated`, and `rejected` counts, then a total. Database output is written to the reference tables and source audit tables; no new output file is created by this script.

**Exit status.** `0` means loading completed with no rejected records; `2` means loading completed but one or more records were rejected; `1` means the load failed before completion.

### `validate_congress_data.py`

**Purpose.** Performs a read-only comparison between the canonical congress-legislators YAML files and PostgreSQL. It checks source hashes, members, identifiers, names, terms, affiliations, committees, memberships, offices, executives, relationships, counts, and referential integrity.

**Run.**

```powershell
python scripts\validate_congress_data.py
```

**Options.**

| Option | Meaning |
|---|---|
| `--source-dir PATH` | Canonical YAML directory. Default: `data/raw/congress`. |
| `--database-url URL` | Override `DATABASE_URL` from `.env`. |
| `--progress-every N` | Print comparison progress every N members. Default: `1000`. |
| `-h`, `--help` | Show command help. |

**Expected output and destination.** Progress, `PASS`/`WARN`/`FAIL` checks, and a QA summary are printed to the terminal and mirrored to `logs/qaresults.log`. The log is overwritten on each run. The database is opened read-only and no data is changed.

**Exit status.** `0` means pass, including pass with warnings; `1` means one or more failed checks; `2` means an unexpected configuration or execution error.

## 3. Load and validate the House filing catalog

### `load_house_filings.py`

**Purpose.** Loads annual House financial-disclosure XML indexes. Each source row is preserved in `staging_house_filings` and `filing_source_occurrences`; `filings` holds one normalized row per House DocID. This script does not download PDFs or insert transaction rows.

**Validate source files without database changes.**

```powershell
python scripts\load_house_filings.py --validate-only
```

**Import validated source files.**

```powershell
python scripts\load_house_filings.py --import
```

**Options.**

| Option | Meaning |
|---|---|
| `--source-dir PATH` | Directory containing annual House XML files. Default: `data/raw/houseofreptrans`. |
| `--files FILE ...` | Load only named files such as `2025FD.xml`. Without it, every `*FD.xml` file in the source directory is discovered. |
| `--import` | Validate files and import them into PostgreSQL. Mutually exclusive with `--validate-only`. |
| `--validate-only` | Validate XML and count rows without connecting to PostgreSQL. Mutually exclusive with `--import`. |
| `--progress-every N` | Print import progress every N rows. Default: `250`; must be positive for an import. |
| `--current-year YEAR` | Maximum accepted annual file year. Default: current year. |
| `--skip-member-matching` | Do not match filing names/offices to the `members` table during import. |
| `--database-url URL` | Override database settings for the import. |
| `-h`, `--help` | Show command help. |

**Expected output and destination.** The script prints discovered files, XML row counts, per-file progress, and totals for `read`, `inserted`, `updated`, `rejected`, and `warnings`. `--validate-only` ends with `no database connection made`. Import output is written to PostgreSQL source snapshots/imports, staging tables, filings, occurrences, and match candidates; no new file is produced.

**Exit status.** `0` means success with no rejected records; `1` means the import completed with at least one rejected source row; `2` means source, configuration, or database execution failure.

### `validate_house_filings.py`

**Purpose.** Independently validates the House XML catalog against PostgreSQL. It compares every source row and hash, normalized filing values, filing types, PTR totals, member matches, relationships, and deterministic samples.

**Run.**

```powershell
python scripts\validate_house_filings.py
```

**Options.**

| Option | Meaning |
|---|---|
| `--source-dir PATH` | Canonical House XML directory. Default: `data/raw/houseofreptrans`. |
| `--log-file PATH` | QA log destination. Default: `logs/house_filings_qa.log`. |
| `--current-year YEAR` | Highest expected annual XML year. Default: current year. |
| `--database-url URL` | Override database settings. |
| `--progress-every N` | Retained compatibility option for progress cadence. Default: `250`. |
| `-h`, `--help` | Show command help. |

**Expected output and destination.** The same numbered checks and summary are printed to the terminal and written to the selected log file. The log is overwritten on each run. SQL is read-only and no database rows are changed.

**Exit status.** `0` means no failed checks; `1` means one or more failed checks; `2` means configuration or execution error.

## 4. Download, parse, and validate House documents

These three scripts form the document-to-trade workflow:

```powershell
python scripts\download_house_documents.py --filing-type P --delay 1
python scripts\parse_house_documents.py --all
python scripts\validate_house_pdf_parsing.py
```

### `download_house_documents.py`

**Purpose.** Selects House filings from the catalog and downloads their official PDFs. Verified primary documents are skipped, so an interrupted run can be resumed.

**Options.**

| Option | Meaning |
|---|---|
| `--all` | Select every eligible House filing. Do not combine with another selection filter. |
| `--member NAME` | Filter by matched member preferred/stored name. |
| `--bioguide ID` | Filter by exact member Bioguide ID. |
| `--state XX` | Filter by two-letter state code. |
| `--year YEAR` | Select one reporting year. |
| `--from-year YEAR` | Inclusive starting reporting year. |
| `--to-year YEAR` | Inclusive ending reporting year. |
| `--filing-type CODE` | Raw House filing code such as `P` (PTR), `O`, `A`, or `X`. Use `--filing-type P` without `--all` to select all eligible PTRs. |
| `--limit N` | Maximum number of eligible filings. |
| `--delay SECONDS` | Pause between HTTP requests. Default: `1.0`. |
| `--timeout SECONDS` | HTTP timeout per request. Default: `60`. |
| `--max-attempts N` | Maximum attempts recorded per download job. Default: `3`. |
| `--retry-failed` | Requeue existing retryable download jobs. |
| `--dry-run` | Preview selection without changing PostgreSQL or the filesystem. |
| `--output-dir PATH` | Root PDF destination. Default: `data/raw/house_documents`. |
| `--database-url URL` | Override database settings; accepted as an advanced option. |
| `-h`, `--help` | Show command help. |

**Expected output and destination.** PDFs are saved as `data/raw/house_documents/{year}/ptr/{DocID}.pdf` for PTR filings and `{year}/financial/{DocID}.pdf` for other filing types. The script records selection batches, filing selections, download jobs, document metadata, HTTP status, file size, and SHA-256 hashes in PostgreSQL. The terminal shows eligible, runnable, skipped, downloaded, retryable, and permanent totals. `--dry-run` makes no changes.

**Exit status.** `0` means no retryable or permanent failures; `1` means at least one download failed; `2` means configuration, connection, or execution failure. `Ctrl+C` leaves completed jobs available for a later run.

### `parse_house_documents.py`

**Purpose.** Reads downloaded, verified House PTR (`P`) PDFs with `pypdf`. When the fast extraction loses table rows or produces invalid records, the parser retries the same embedded text with `pdfplumber` layout extraction before assigning the document to OCR. It writes the selected extraction beside each PDF and inserts one normalized transaction row per parsed trade into `trades`. It also records extraction metadata and updates document, filing, and parse-job status.

**Options.**

| Option | Meaning |
|---|---|
| `--all` | Process every eligible House PTR document. |
| `--docid DOCID` | Process one exact House DocID. |
| `--filing-id ID` | Process one database `filing_id`. |
| `--member NAME` | Filter by matched member or stored filer name. |
| `--year YEAR` | Process one reporting year. |
| `--from-year YEAR` | Inclusive first reporting year. |
| `--to-year YEAR` | Inclusive last reporting year. |
| `--limit N` | Maximum number of selected documents. |
| `--retry-failed` | Retry failed retryable parse jobs. |
| `--reprocess` | Reprocess completed or review jobs. Normally completed jobs are skipped. |
| `--dry-run` | List eligible documents without changing files or PostgreSQL. |
| `--max-attempts N` | Maximum attempts per parse job. Default: `3`. |
| `--database-url URL` | Override database settings; accepted as an advanced option. |
| `-h`, `--help` | Show command help. |

**Expected output and destination.** For a source PDF such as `...\2025\ptr\20016861.pdf`, extracted text is written to a versioned file such as `...\20016861.pypdf-1.2.0.txt` or `...\20016861.pdfplumber_layout-1.0.0.txt`. PostgreSQL receives `document_extractions`, staged rows, `trades`, and parse-job status updates; `filings.processing_status` and document completeness fields are updated. The terminal reports the selected extractor, pages, trade count, parse status, and totals for `selected`, `parsed`, `trades`, `needs_review`, `skipped`, and `failed`. A document is not marked parsed if the number of recognizable transaction signatures differs from the emitted row count. Image-only PDFs are flagged for OCR review; this script does not perform OCR.

**Exit status.** `0` means the run finished without failed documents; `1` means one or more documents failed; `2` means configuration, connection, or execution failure; `130` means the run was interrupted with `Ctrl+C`.

### `validate_house_pdf_parsing.py`

**Purpose.** Reads the filing/document/parse-job/extraction/staging/trade chain in read-only mode and writes a human-readable QA log for parser version `1.2.1`. It checks required tables and columns, document identity, preferred extractions, completeness status, staged invalid rows, trade links, orphaned trades, duplicate parser rows, and optionally local artifact hashes and transaction-signature coverage.

**Options.**

| Option | Meaning |
|---|---|
| `--docid DOCID` | Restrict the report to one DocID; repeat the option for multiple IDs. |
| `--member NAME` | Case-insensitive filer-name fragment. |
| `--year YEAR` | Restrict to one reporting year. |
| `--from-year YEAR` | Inclusive first reporting year. |
| `--to-year YEAR` | Inclusive last reporting year. |
| `--limit N` | Maximum number of documents to log. |
| `--include-non-primary` | Include non-primary document rows. The default is primary documents only. |
| `--check-files` | Verify local PDF/text existence, file sizes, SHA-256 hashes, extracted character counts, and that recognizable text transactions match current parser trade rows. |
| `--log-file PATH` | Log destination. Default: `logs/house_pdf_qa.log`. |
| `--database-url URL` | Override database settings. |
| `-h`, `--help` | Show command help. |

**Expected output and destination.** The terminal and log contain each selected DocID, representative, document path/status, parse job, extraction, trade count, and up to five trade details, followed by status counts and integrity results. The default log is `logs/house_pdf_qa.log`; it is overwritten on each run. The database transaction is read-only.

**Exit status.** `0` means no failed checks (warnings are allowed); `1` means failed checks; `2` means configuration or execution error.

## 5. Export a member report

### `export_example_member.py`

**Purpose.** Reads the database in read-only mode and exports all currently stored StockGov information for Mike Crapo (Bioguide ID `C000880`), including names, identifiers, terms, party affiliations, family relationships, leadership, offices, social accounts, committees, filings, documents, jobs, extractions, trades, evidence, and sources.

**Run.**

```powershell
python scripts\export_example_member.py
```

**Options.**

| Option | Meaning |
|---|---|
| `--database-url URL` | Override `DATABASE_URL` from `.env`. |
| `-h`, `--help` | Show command help. |

The member is fixed in the script; there is no member-name option.

**Expected output and destination.** The terminal shows progress, output path, report size, and elapsed time. The Markdown report is written to `logs/example_member.log`. Empty sections mean that no corresponding rows are currently stored; they do not establish that no real-world records exist. The database is not changed.

**Exit status.** `0` means the report was written; `1` means member lookup, connection, or file output failed.

## 6. Reset or remove database data

### `reset_database.py`

**Purpose.** Performs an interactive maintenance operation across the allowlisted StockGov tables. `delete` removes all rows and resets identities while preserving the tables. `drop` removes the StockGov tables and their data.

**Run.**

```powershell
python scripts\reset_database.py delete
```

To drop the schema completely:

```powershell
python scripts\reset_database.py drop
```

**Options and parameters.**

| Parameter/option | Meaning |
|---|---|
| `action` | Required positional value: `delete` or `drop`. |
| `--database-url URL` | PostgreSQL connection URL; otherwise read from `.env`. |
| `-h`, `--help` | Show command help. |

The script asks for an explicit `y`/`yes` confirmation before changing anything. Any other answer cancels the operation.

**Expected output and destination.** No files are created. A successful `delete` prints that all rows were deleted while table structure was preserved. A successful `drop` prints that all StockGov tables were dropped. This is the only script in this guide intended to remove stored data.

**Exit status.** `0` means the requested action completed or was cancelled; `2` means the action was missing/invalid. Connection or execution errors are reported by the script.

## 7. Other files in `scripts`

### `.gitkeep`

An empty placeholder that keeps the `scripts` directory in version control when no other tracked files are present. It has no command-line parameters, expected runtime output, or output directory.

### `howto.md`

This guide documents the scripts, their workflow order, parameters, expected output, and output destinations. It is a documentation file and is not executable.

## Recommended end-to-end order

For a new database and a complete House PTR-to-trade workflow, use this sequence:

```powershell
# 1. Create the schema
python scripts\create_database.py
python scripts\validate_new_cols.py

# 2. Download and load reference data
python scripts\downloadsource.py
python scripts\load_congress_data.py
python scripts\validate_congress_data.py

# 3. Load and validate the House filing catalog
python scripts\load_house_filings.py --import
python scripts\validate_house_filings.py

# 4. Download and parse House PTR documents
python scripts\download_house_documents.py --filing-type P --delay 1
python scripts\parse_house_documents.py --all
python scripts\validate_house_pdf_parsing.py --check-files

# 5. Optional example report
python scripts\export_example_member.py
```

For a large download, use `--year`, `--from-year`, and `--to-year` to work in batches. The downloader’s `--dry-run` previews a selection; the parser’s `--dry-run` previews downloaded documents. Re-running the downloader skips verified documents, and re-running the parser skips completed parse jobs unless `--reprocess` is supplied.
