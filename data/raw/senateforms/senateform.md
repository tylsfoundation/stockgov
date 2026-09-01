# U.S. Senate Financial Disclosure Forms and Data Sources

## 1. Purpose

This document describes the official sources, report categories, retrieval process, retention rules, and recommended database treatment for U.S. Senate financial disclosure reports. It is the Senate counterpart to `houseofrepform.md`.

The authoritative online source is the U.S. Senate Electronic Financial Disclosures public search system:

- Public search: <https://efdsearch.senate.gov/search/home/>
- Senate Ethics financial-disclosure guidance: <https://www.ethics.senate.gov/public/index.cfm/financialdisclosure>

## 2. Key Difference from the House System

The House publishes annual XML index files containing a numeric `DocID` and a filing-type code. The Senate does not appear to publish an equivalent annual bulk XML index.

The Senate system instead uses:

- A session-based search website
- An acknowledgement of the statutory disclosure-use restrictions
- A CSRF security token
- A search-results JSON endpoint
- UUID report identifiers rather than numeric House `DocID` values
- Structured HTML for many electronically filed reports
- PDF or scanned attachments for some paper filings

This often makes individual electronic Senate reports easier to parse than House PDFs, but discovering the reports requires interacting with the Senate search system.

## 3. Online Historical Coverage and Retention

The Senate public search site states that it includes financial disclosure reports for Senators, former Senators, and Senate candidates filed from 2012 to the present.

Retention is not unlimited:

- Reports for a former Senator generally remain available for six years after the individual ceases to be a Member of Congress.
- Reports for a candidate generally remain available for one year after the individual is no longer a candidate.
- Additional reports may be available for inspection through the Senate Office of Public Records kiosk.

Because reports can age out of the public website, StockGov should archive the search metadata and source report while each report is available. A local historical archive should not depend on the Senate website retaining every report indefinitely.

## 4. Senate Report Categories

The Senate Ethics Committee identifies the following principal financial disclosure report categories.

### 4.1 Candidate Report

A financial disclosure submitted by a qualifying candidate for the U.S. Senate. A candidate generally becomes subject to the requirement after exceeding the applicable contribution or expenditure threshold.

### 4.2 New Filer Report

A financial disclosure submitted when an individual newly becomes subject to Senate reporting requirements. A Senator who filed a candidate report before taking office may qualify for a waiver from the new-filer report.

### 4.3 Annual Report

The annual financial disclosure may include assets and investment income, transactions, earned and non-investment income, liabilities, gifts, travel reimbursements, positions, agreements, and other required schedules.

Electronic annual-report URLs generally follow this pattern:

```text
https://efdsearch.senate.gov/search/view/annual/{REPORT_UUID}/
```

### 4.4 Periodic Transaction Report (PTR)

The primary filing for reportable purchases, sales, and exchanges. PTRs may report transactions by the filer, spouse, or dependent child. They generally include the asset, transaction date, transaction type, value range, owner, and filing date.

The Senate search system uses report type `11` for Periodic Transaction Reports.

Electronic PTR URLs generally follow this pattern:

```text
https://efdsearch.senate.gov/search/view/ptr/{REPORT_UUID}/
```

PTRs must generally be filed within 30 days after the filer receives notification of a transaction and no later than 45 days after the transaction. The Senate Ethics Committee states that extensions are not available for PTRs.

### 4.5 Termination Report

A financial disclosure submitted after a covered Senate position or employment ends. The report should retain its termination date and termination-report classification.

### 4.6 Amendment

An amendment corrects, clarifies, or supplements a previously filed report. Amendments should be stored separately and connected to the filing they amend. Original values should not be overwritten or discarded.

### 4.7 Extension Request

The Ethics Committee may grant extensions for certain annual, termination, or new-filer reports. Extensions are not available for PTRs and may be restricted for candidate reports near an election.

### 4.8 Waiver or Supporting Filing

Some records may document a waiver, reporting determination, or supporting correspondence. These documents may not contain transactions but can explain why an otherwise expected report is absent.

## 5. Search and Retrieval Process

### 5.1 Establish a Session

The downloader first requests the public landing page and obtains the session cookie and CSRF token.

```text
https://efdsearch.senate.gov/search/home/
```

### 5.2 Accept the Disclosure-Use Agreement

The application must submit the acknowledgement displayed on the landing page. The acknowledgement should not be bypassed, and the resulting session should be reused for the related search and report requests.

### 5.3 Query the Search Endpoint

Existing open-source Senate scrapers use the following endpoint:

```text
https://efdsearch.senate.gov/search/report/data/
```

The endpoint returns search-result data in JSON for the website's results table. Common search fields include:

- First name
- Last name
- Filer type
- State
- Report type
- Report year
- Submission start date
- Submission end date
- Result offset
- Page length

Existing implementations use a maximum page size of 100 results. This is a result-page limit, not evidence of a published 100-request-per-day quota.

### 5.4 Save Filing-Level Metadata

For each search result, save the published filer name, office or filer classification, report name, filing date, report UUID, and report URL before opening the report.

### 5.5 Retrieve the Report

Many electronically filed PTR and annual reports are structured HTML pages. Their tables can be parsed directly without OCR. Paper filings or attached documents may instead require PDF download, text extraction, and possibly OCR.

### 5.6 Process Incrementally

The scraper should:

1. Search a bounded date range.
2. Page through all results.
3. Insert or update filing metadata using the report UUID as a source identifier.
4. Skip unchanged reports already downloaded successfully.
5. Retrieve and archive new or changed reports.
6. Parse structured HTML directly.
7. Queue scanned PDFs for OCR.
8. Record all attempts, errors, and parser versions.

Use a conservative delay between requests. Quantgress recommends a self-imposed two-second delay for its historical collection process.

## 6. Recommended Database Fields

### 6.1 Senate Filing Record

Retain at least:

- Internal `filing_id`
- Stable internal `member_id`
- `senate_report_uuid`
- Filer name exactly as published
- Filer type
- State when supplied
- Report type as published
- Normalized report type
- Report year
- Filing or submission date
- Amendment indicator
- Parent or amended filing identifier when determinable
- Official report URL
- Source format: HTML, PDF, scanned PDF, or attachment
- Local source path
- Retrieval status and HTTP status
- First-seen and last-checked timestamps
- Download attempt count
- Content checksum
- Page count for PDFs
- Parser status and parser version
- Raw source metadata

The Senate UUID is a source-system identifier. StockGov should still generate its own internal `filing_id` and relate the source UUID to that internal record.

### 6.2 PTR Transaction Record

Retain at least:

- Internal `transaction_id`
- Parent `filing_id`
- Transaction row number
- Owner as published
- Asset name or description as published
- Asset type as published
- Ticker as published
- Separately inferred ticker and inference method
- Transaction type
- Transaction date
- Notification date when provided
- Amount range as published
- Normalized minimum and maximum amounts
- Filing date
- Comments or explanatory text
- Source row HTML or raw extracted text
- Parsing confidence and review status

Never replace the published asset description or ticker with an inferred value. Store normalized and inferred values in separate fields so every transformation remains auditable.

## 7. Recommended Processing Categories

| Processing category | Senate reports | Treatment |
|---|---|---|
| Transaction extraction | Periodic Transaction Report | Parse transaction rows and archive the original HTML or PDF. |
| Full financial disclosure | Candidate, New Filer, Annual, Termination | Parse the broader financial-disclosure schedules. |
| Version correction | Amendment | Preserve separately and associate with the amended filing. |
| Administrative | Extension, waiver, supporting correspondence | Preserve metadata and explain missing, delayed, or changed reports. |
| Manual/OCR queue | Paper or scanned filing | Archive immediately and process with PDF extraction or OCR. |

## 8. Existing Open-Source Implementations

### 8.1 Quantgress

Repository: <https://github.com/DMulajkar/Quantgress>

Quantgress is the recommended starting implementation for StockGov because it reads the official Senate source directly. Its relevant modules include:

- `scrape_senate.py` for Senate PTR transactions
- `scrape_senate_annual.py` for annual financial disclosures
- `daily.py` for incremental processing
- Session and CSRF handling
- Search-result enumeration
- Structured HTML parsing
- Resume-safe database processing
- Parser self-tests

Quantgress currently uses DuckDB, so its source-specific retrieval and parsing logic should be adapted to the StockGov PostgreSQL schema rather than copying its storage design unchanged.

### 8.2 Senate EFD Python Example

Repository example: <https://gist.github.com/fraserlove/2fbe462bbebd11bb4c2774ec967e4f67>

This small Python implementation demonstrates:

- Establishing a Senate EFD session
- Reading the CSRF token
- Accepting the disclosure-use agreement
- Querying report type `11`
- Paging through results in batches of 100
- Opening electronic PTR pages
- Extracting transaction rows

### 8.3 Congress Stock Tracker

Repository: <https://github.com/SirMist/congress-stock-tracker>

This implementation combines House and Senate results and demonstrates the different discovery workflows for the two chambers. It is useful as a reference, although its published interface focuses on recent filing-level results rather than being the permanent historical archive for StockGov.

## 9. Third-Party Reporting Websites

Third-party websites are useful for comparing StockGov results and identifying potential gaps. They should not replace the official report as the evidence source.

| Website | Coverage and role |
|---|---|
| Capitol Trades — <https://www.capitoltrades.com/trades> | Free House and Senate browser with filters for chamber, state, committee, politician, issuer, and transaction type. The public trade page currently states that displayed historical data is limited to the past three years. |
| Unusual Whales Politics — <https://unusualwhales.com/politics> | House and Senate trade browsing and visualization. Useful for result comparison. |
| Bargo Congress API — <https://www.bargo.ai/free-apis/congress> | A newer JSON API claiming House and Senate coverage. Validate completeness, provenance, limits, and durability before relying on it. |
| Kapitol.ai — <https://kapitol.ai/developers> | Commercial API claiming House and Senate history back to 2012. Useful as a comparison source, not the primary archive. |

## 10. Legal and Use Restrictions

The Senate landing page requires users to acknowledge restrictions derived from the Ethics in Government Act. The notice states that a disclosure report may not be obtained or used:

1. For an unlawful purpose
2. For a commercial purpose, other than use by news and communications media for dissemination to the general public
3. To determine or establish an individual's credit rating
4. Directly or indirectly in the solicitation of money for a political, charitable, or other purpose

The notice also describes the possibility of a civil action and monetary penalty for prohibited use.

This restriction should be considered before exposing Senate disclosure documents or derived personal information through a commercial public product. Personal research and internal analysis are different from selling access, but commercial deployment should receive appropriate legal review.

## 11. Recommended StockGov Collection Plan

1. Use `efdsearch.senate.gov` as the authoritative evidence source.
2. Adapt the Quantgress Senate scraper to the StockGov PostgreSQL database.
3. Run a small bounded live test before attempting a backfill.
4. Backfill all currently available Senate PTR results from 2012 forward.
5. Archive the raw HTML or PDF for every discovered filing.
6. Store each Senate report UUID and its official URL.
7. Parse electronic HTML reports without converting them to PDF.
8. Route scanned or paper filings to a separate OCR queue.
9. Retain amendments and connect them to their original reports.
10. Run an incremental daily search for new or changed PTR filings.
11. Run annual-report collection as a separate workflow.
12. Compare counts and selected transactions with third-party reporting sites to locate potential gaps.

## 12. Practical Conclusion

The Senate side does not require brute-force PDF downloading for every filing. The search results provide the report inventory, and electronically filed reports frequently expose structured transaction tables in HTML. The central engineering requirement is therefore a reliable, respectful session-based collector with durable report tracking.

The StockGov Senate pipeline should be:

```text
Senate EFD search
    -> report UUID and filing metadata
    -> structured HTML or source PDF
    -> archived raw evidence
    -> normalized PostgreSQL filing record
    -> PTR transaction rows or full disclosure schedules
    -> incremental daily update
```

## 13. Official References

- U.S. Senate Electronic Financial Disclosures: <https://efdsearch.senate.gov/search/home/>
- U.S. Senate Select Committee on Ethics, Financial Disclosure: <https://www.ethics.senate.gov/public/index.cfm/financialdisclosure>
- U.S. Senate Office of Public Records: <https://www.senate.gov/lobby/>
