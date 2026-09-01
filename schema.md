# Congressional Trading Analytics Platform — Database Schema

## 1. Purpose

This schema supports the collection, processing, and analysis of congressional financial disclosure filings and Periodic Transaction Reports (PTRs) for both the House and Senate.

It is designed to:

- Maintain a stable record for each member of Congress across multiple terms, offices, names, and source systems.
- Import current and historical member, committee, and executive-branch reference data.
- Catalog every discovered financial disclosure filing before deciding whether to download it.
- Selectively download and process PTR documents by member, state, chamber, year, filing type, or other criteria.
- Resume interrupted work without downloading or parsing the same document unnecessarily.
- Preserve raw source values and evidence alongside normalized data.
- Track uncertain member matches, ticker guesses, OCR results, parser versions, and manual review decisions.
- Support historical analysis without using information that was unavailable on the date being analyzed.

## 2. Design Conventions

### 2.1 Internal identifiers

Every major table has an internal numeric identifier ending in `_id`. The database generates these identifiers when a record is inserted. They are permanent database keys and do not encode a name, state, chamber, or source-system identifier.

For example, `member_id` is the internal identity of a member. Bioguide, FEC, GovTrack, LIS, Wikidata, and other identifiers are stored separately in `member_identifiers`.

### 2.2 External identifiers

Identifiers assigned by source systems are retained in their original form. Examples include Bioguide IDs, House document IDs, Senate filing URLs, committee codes, CUSIPs, and ticker symbols.

### 2.3 Source provenance

Imported records retain the source snapshot or source document from which they were derived. Raw names, labels, amounts, dates, and other source values are preserved even when normalized equivalents are also stored.

### 2.4 Historical validity

Time-dependent records use effective dates, term dates, or Congress numbers. Committee membership, party affiliation, district, ticker symbols, and other values that change over time must not be treated as permanently current.

### 2.5 Nullable member matches

A filing may be stored before its filer has been confidently connected to a member. In that case, `filings.member_id` remains empty and the candidate match is sent for review. An uncertain match must not automatically create a duplicate member.

### 2.6 Controlled values

Fields such as chamber, processing status, filing type, transaction type, owner type, and match method use controlled values. The application should retain the original source value in the appropriate raw field when it differs from the normalized value.

## 3. Relationship Overview

```text
source_snapshots
    |--< source_imports
    |--< members, terms, committees, memberships, executives
    `--< filings and documents

members
    |--< member_names
    |--< member_identifiers
    |--< member_terms
    |--< leadership_roles
    |--< member_offices
    |--< member_social_accounts
    |--< committee_memberships >-- committees
    `--< filings --< documents --< document_jobs
                      |             `--< document_extractions
                      `--< trades --< trade_evidence
                                  `--> securities --< security_identifiers

committees --< committees
             parent      child/subcommittee

executives --< executive_terms
```

The central analytical path is:

```text
member -> filing -> trade -> security
```

A trade belongs to a filing, and the filing belongs to a member after identity resolution. The trade does not need a second copy of `member_id`.

## 4. Source and Provenance Tables

### 4.1 `source_snapshots`

Records each downloaded source file, API response, index page, repository snapshot, or other source artifact used by the system.

| Field | Description |
|---|---|
| `source_snapshot_id` | Database-generated primary identifier. |
| `source_name` | Controlled source name, such as House Clerk, Senate eFD, Congress.gov, or congress-legislators. |
| `source_type` | Kind of source, such as XML index, YAML file, API response, HTML page, PDF, or CSV. |
| `source_url` | Original URL when the artifact came from the web. |
| `local_path` | Storage location of the preserved source artifact. |
| `coverage_start_date` | Earliest date represented by the snapshot, when known. |
| `coverage_end_date` | Latest date represented by the snapshot, when known. |
| `retrieved_at` | Date and time the artifact was obtained. |
| `content_hash` | Hash used to detect changes and verify file identity. |
| `file_size_bytes` | Size of the saved artifact. |
| `format_version` | Source format or release version, when available. |
| `notes` | Human-readable source or quality notes. |

Relationships:

- One source snapshot may support many imported records.
- A new snapshot is created when a source file changes; previous snapshots remain available for audit purposes.

### 4.2 `source_imports`

Tracks each attempt to load a source snapshot into staging and production tables.

| Field | Description |
|---|---|
| `source_import_id` | Database-generated primary identifier. |
| `source_snapshot_id` | Snapshot being imported. |
| `import_type` | Importer or dataset type, such as House filing index or current legislators. |
| `importer_version` | Version of the import logic. |
| `started_at` | Import start time. |
| `finished_at` | Import finish time. |
| `status` | Pending, running, complete, partially complete, or failed. |
| `records_read` | Number of source records examined. |
| `records_inserted` | Number of new records created. |
| `records_updated` | Number of existing records updated. |
| `records_rejected` | Number of records that could not be accepted. |
| `error_summary` | Summary of import errors. |

Relationships:

- Each import belongs to one `source_snapshot`.
- Production and staging records may reference the import that created or last updated them.

## 5. Member and Congressional Service Tables

### 5.1 `members`

Stores one stable record for each person who serves or has served in Congress.

| Field | Description |
|---|---|
| `member_id` | Database-generated primary identifier. |
| `preferred_name` | Preferred display name. |
| `first_name` | Normalized given name. |
| `middle_name` | Normalized middle name or initial. |
| `last_name` | Normalized family name. |
| `suffix` | Name suffix, when present. |
| `nickname` | Common nickname, when present. |
| `date_of_birth` | Date of birth, when supplied by a trusted source. |
| `gender` | Source-provided gender value, when available. |
| `is_living` | Whether the person is believed to be living. |
| `created_at` | Record creation time. |
| `updated_at` | Most recent update time. |
| `source_snapshot_id` | Snapshot that supports the current core values. |

Relationships:

- One member may have many names, identifiers, terms, offices, leadership roles, social accounts, committee memberships, and filings.
- A person has only one `members` record even if the person changes chamber, state, district, party, or name.

### 5.2 `member_names`

Stores alternate, historical, display, and source-specific forms of a member's name for identity matching.

| Field | Description |
|---|---|
| `member_name_id` | Database-generated primary identifier. |
| `member_id` | Member to whom the name belongs. |
| `name_type` | Preferred, official, former, nickname, source, or matching alias. |
| `full_name` | Complete name as displayed or received. |
| `first_name` | Parsed given name. |
| `middle_name` | Parsed middle name or initial. |
| `last_name` | Parsed family name. |
| `suffix` | Parsed suffix. |
| `normalized_name` | Standardized value used for matching. |
| `valid_from` | Beginning of known usage, when available. |
| `valid_to` | End of known usage, when available. |
| `source_snapshot_id` | Source supporting the name. |

Relationships:

- Many name records belong to one member.
- Filing identity resolution compares raw filer names with these records but also considers chamber, state, district, and term dates.

### 5.3 `member_identifiers`

Stores identifiers assigned to a member by external systems.

| Field | Description |
|---|---|
| `member_identifier_id` | Database-generated primary identifier. |
| `member_id` | Member identified by the external value. |
| `identifier_type` | Bioguide, FEC, GovTrack, LIS, ICPSR, Wikidata, Ballotpedia, or another defined system. |
| `identifier_value` | Identifier exactly as assigned by that system. |
| `is_primary` | Whether this is the preferred identifier of its type. |
| `valid_from` | Beginning of validity, when applicable. |
| `valid_to` | End of validity, when applicable. |
| `source_snapshot_id` | Source supporting the identifier. |

Relationships and rules:

- Many identifiers may belong to one member.
- The combination of identifier type and identifier value must identify only one member.
- Bioguide is the preferred cross-source identifier when it is available.

### 5.4 `member_terms`

Stores each period of congressional service. Party, chamber, state, and district belong here because they may change over a member's career.

| Field | Description |
|---|---|
| `member_term_id` | Database-generated primary identifier. |
| `member_id` | Member serving the term. |
| `chamber` | House or Senate. |
| `term_start_date` | First date of the service period. |
| `term_end_date` | Last date of the service period. |
| `congress_start` | First Congress covered by the term. |
| `congress_end` | Last Congress covered by the term. |
| `state_code` | Two-letter state or territory code. |
| `district_number` | House district; empty for senators and at-large values represented consistently. |
| `senate_class` | Senate class, when applicable. |
| `senate_state_rank` | Junior or senior senator, when available. |
| `party_code` | Normalized party code during the term. |
| `party_name_raw` | Party value exactly as provided by the source. |
| `term_type` | Regular, special election, appointed, or other defined service type. |
| `source_snapshot_id` | Source supporting the term. |

Relationships:

- Many terms belong to one member.
- A filing date can be compared with term dates to validate a filer match.
- State and party analyses use the term active on the relevant analysis date, not the member's current term.

### 5.5 `leadership_roles`

Stores congressional and party leadership positions held by members.

| Field | Description |
|---|---|
| `leadership_role_id` | Database-generated primary identifier. |
| `member_id` | Member holding the role. |
| `chamber` | House, Senate, joint, or party organization. |
| `role_title` | Speaker, Majority Leader, Conference Vice Chair, or another title. |
| `party_code` | Associated party, when relevant. |
| `start_date` | Date the role began, when known. |
| `end_date` | Date the role ended, when known. |
| `congress_start` | First Congress associated with the role. |
| `congress_end` | Last Congress associated with the role. |
| `source_snapshot_id` | Source supporting the role. |

Relationships:

- Many leadership roles may belong to one member.

### 5.6 `member_offices`

Stores official office addresses and contact information with historical validity when available.

| Field | Description |
|---|---|
| `member_office_id` | Database-generated primary identifier. |
| `member_id` | Member associated with the office. |
| `office_type` | Capitol, district, state, campaign, or another defined type. |
| `building` | Building name. |
| `room` | Room or suite. |
| `address_line_1` | First address line. |
| `address_line_2` | Second address line. |
| `city` | City. |
| `state_code` | State or territory code. |
| `postal_code` | Postal code. |
| `phone` | Published phone number. |
| `fax` | Published fax number. |
| `valid_from` | Beginning of validity. |
| `valid_to` | End of validity. |
| `source_snapshot_id` | Source supporting the office information. |

Relationships:

- Many office records may belong to one member.

### 5.7 `member_social_accounts`

Stores official or verified public web and social-media accounts.

| Field | Description |
|---|---|
| `member_social_account_id` | Database-generated primary identifier. |
| `member_id` | Member associated with the account. |
| `platform` | Website, X, Facebook, YouTube, Instagram, or another platform. |
| `account_name` | Platform username or account label. |
| `account_url` | Full account URL. |
| `is_official` | Whether the account is an official congressional account. |
| `valid_from` | Beginning of known validity. |
| `valid_to` | End of known validity. |
| `source_snapshot_id` | Source supporting the account. |

Relationships:

- Many social accounts may belong to one member.

## 6. Committee Tables

### 6.1 `committees`

Stores House, Senate, joint, select, standing, special, and subcommittee definitions.

| Field | Description |
|---|---|
| `committee_id` | Database-generated primary identifier. |
| `committee_code` | Official or source-specific committee code, such as `SSFI`. |
| `chamber` | House, Senate, or joint. |
| `committee_type` | Standing, select, special, joint, or subcommittee. |
| `name` | Current normalized committee name. |
| `name_raw` | Committee name exactly as supplied by the source. |
| `parent_committee_id` | Parent committee for a subcommittee; empty for top-level committees. |
| `is_current` | Whether the committee currently exists. |
| `website_url` | Official committee website, when available. |
| `source_snapshot_id` | Source supporting the definition. |

Relationships:

- A committee may have many child committees through `parent_committee_id`.
- A committee may have many Congress-specific records and many member assignments.
- Main-committee-only queries select rows without a parent committee.

### 6.2 `committee_congresses`

Stores a committee's name and status for each Congress, preserving reorganizations and historical names.

| Field | Description |
|---|---|
| `committee_congress_id` | Database-generated primary identifier. |
| `committee_id` | Committee represented by the record. |
| `congress_number` | Number of the Congress. |
| `name` | Committee name during that Congress. |
| `committee_code` | Code used during that Congress. |
| `is_active` | Whether the committee was active in that Congress. |
| `source_snapshot_id` | Source supporting the historical definition. |

Relationships:

- Many Congress-specific records may belong to one committee.

### 6.3 `committee_memberships`

Connects members to committees and records rank, role, and historical validity.

| Field | Description |
|---|---|
| `committee_membership_id` | Database-generated primary identifier. |
| `committee_id` | Committee on which the member served. |
| `member_id` | Member serving on the committee. |
| `congress_number` | Congress during which the assignment applied. |
| `start_date` | Assignment start date, when known. |
| `end_date` | Assignment end date, when known. |
| `party_side` | Majority, minority, independent, or other source value. |
| `rank` | Source-provided ordering or seniority rank. |
| `title` | Chair, Ranking Member, Vice Chair, ex officio, or another title. |
| `is_ex_officio` | Whether the assignment is ex officio. |
| `source_snapshot_id` | Source supporting the assignment. |

Relationships and cautions:

- Many members may serve on one committee, and one member may serve on many committees.
- Current membership files provide current assignments but may not provide start and end dates.
- Historical committee definitions alone do not prove historical membership. Historical membership must come from a source that explicitly connects a member to a committee for a particular Congress or date.

## 7. Executive Administration Tables

### 7.1 `executives`

Stores presidents and other executives used to define an administration for historical analysis.

| Field | Description |
|---|---|
| `executive_id` | Database-generated primary identifier. |
| `full_name` | Preferred display name. |
| `first_name` | Given name. |
| `middle_name` | Middle name. |
| `last_name` | Family name. |
| `date_of_birth` | Date of birth, when available. |
| `bioguide_id` | Bioguide identifier, when available and applicable. |
| `source_snapshot_id` | Source supporting the person record. |

Relationships:

- One executive may have multiple terms.

### 7.2 `executive_terms`

Stores each presidential or executive term used to group congressional composition and trading activity by administration.

| Field | Description |
|---|---|
| `executive_term_id` | Database-generated primary identifier. |
| `executive_id` | Executive serving the term. |
| `office` | President, Vice President, or another defined office. |
| `term_start_date` | Start of the term. |
| `term_end_date` | End of the term. |
| `party_code` | Party during the term. |
| `term_number` | Sequence number for that executive, when useful. |
| `source_snapshot_id` | Source supporting the term. |

Relationships:

- Many terms may belong to one executive.
- Member terms and trades can be grouped by the executive term active on the relevant date.

## 8. Filing Catalog and Identity Resolution Tables

### 8.1 `filings`

Stores one catalog record for every discovered House or Senate financial disclosure filing, whether or not its document has been downloaded.

| Field | Description |
|---|---|
| `filing_id` | Database-generated primary identifier. |
| `source` | House Clerk, Senate eFD, or another disclosure source. |
| `source_filing_id` | Source's stable filing identifier, such as a House DocID or canonical Senate filing identifier. |
| `chamber` | House or Senate. |
| `filing_type_code_raw` | Filing code exactly as received, such as `P`, `O`, or `X`. |
| `filing_type` | Normalized type, such as PTR, annual disclosure, amendment, extension, termination, or candidate report. |
| `reporting_year` | Filing/index year represented by the source. |
| `filed_date` | Date submitted or published. |
| `report_period_start` | Beginning of the reporting period, when available. |
| `report_period_end` | End of the reporting period, when available. |
| `member_id` | Resolved member; empty until confidently matched. |
| `raw_first_name` | Filer's first name exactly as indexed. |
| `raw_last_name` | Filer's last name exactly as indexed. |
| `raw_full_name` | Complete source name when provided. |
| `raw_office` | Office, state, or district value exactly as indexed. |
| `state_code_guess` | State inferred from the filing index or office text. |
| `district_guess` | District inferred from the filing index or office text. |
| `member_match_status` | Unmatched, automatically matched, manually matched, ambiguous, or rejected. |
| `member_match_method` | Bioguide, exact name and term, normalized name and office, manual review, or another method. |
| `member_match_confidence` | Numeric or categorized confidence in the match. |
| `source_url` | Filing landing page or original document URL. |
| `discovered_at` | Date and time the filing was first cataloged. |
| `processing_status` | Current overall processing state. |
| `source_snapshot_id` | Index or source snapshot from which the filing was discovered. |
| `source_import_id` | Import run that created or updated the catalog record. |

Relationships and rules:

- A member may have many filings.
- A filing may have multiple documents, processing jobs, and extracted trades.
- The combination of source and source filing identifier must be unique.
- `member_id` may remain empty while identity resolution is pending.
- The raw filer name must remain unchanged even after a member match is established.

### 8.2 `member_match_candidates`

Records possible members considered during filing identity resolution.

| Field | Description |
|---|---|
| `member_match_candidate_id` | Database-generated primary identifier. |
| `filing_id` | Filing being resolved. |
| `candidate_member_id` | Possible member match. |
| `candidate_rank` | Relative ordering among candidates. |
| `match_score` | Overall matching score. |
| `name_score` | Name similarity component. |
| `office_score` | State, district, or office similarity component. |
| `term_score` | Whether the member served in the chamber on the filing date. |
| `match_reasons` | Human-readable explanation of supporting or conflicting evidence. |
| `decision` | Pending, accepted, or rejected. |
| `reviewed_at` | Review date and time. |
| `reviewed_by` | User or process that made the decision. |

Relationships:

- One filing may have several candidate members.
- One accepted candidate establishes `filings.member_id` and records a manual or automatic match method.

### 8.3 `filing_selections`

Tracks explicit user or scheduled selections of filings for download and processing. This enables requests that overlap years or previously processed ranges without duplicating work.

| Field | Description |
|---|---|
| `filing_selection_id` | Database-generated primary identifier. |
| `filing_id` | Filing selected for work. |
| `selection_batch_id` | Batch or request that selected the filing. |
| `selection_reason` | Member, state, year, chamber, filing type, nightly state run, test case, or manual selection. |
| `priority` | Processing priority. |
| `selected_at` | Date and time selected. |
| `selected_by` | User, scheduler, or process making the selection. |
| `is_active` | Whether the selection remains active. |

Relationships:

- A filing may be selected in multiple batches.
- Selection does not imply another document download if a verified document already exists.

### 8.4 `selection_batches`

Stores the filters and status of each user-created or scheduled processing request.

| Field | Description |
|---|---|
| `selection_batch_id` | Database-generated primary identifier. |
| `batch_name` | Human-readable request name. |
| `requested_by` | User or scheduler. |
| `filter_definition` | Structured record of selected members, states, dates, years, chambers, filing types, or statuses. |
| `created_at` | Request creation time. |
| `started_at` | Processing start time. |
| `finished_at` | Processing finish time. |
| `status` | Draft, queued, running, complete, partially complete, canceled, or failed. |
| `filings_selected` | Number of filings matched by the request. |
| `filings_completed` | Number fully processed. |
| `notes` | User or processing notes. |

Relationships:

- One batch may create many filing selections.

## 9. Document Storage and Processing Tables

### 9.1 `documents`

Stores each downloaded or locally supplied filing document and its integrity information.

| Field | Description |
|---|---|
| `document_id` | Database-generated primary identifier. |
| `filing_id` | Filing represented by the document. |
| `document_type` | Original filing, amendment, attachment, HTML page, PDF, XML, or another type. |
| `source_url` | Exact URL from which the document was retrieved. |
| `local_path` | Location of the preserved file. |
| `mime_type` | Detected media type. |
| `file_size_bytes` | File size. |
| `content_hash` | Hash used for integrity and duplicate detection. |
| `downloaded_at` | Download completion time. |
| `http_status` | Retrieval response status, when applicable. |
| `is_primary` | Whether this is the primary document for the filing. |
| `page_count` | Number of pages, when applicable. |
| `has_embedded_text` | Whether usable text is embedded in the file. |
| `requires_ocr` | Whether OCR is required. |
| `verification_status` | Unverified, verified, corrupt, wrong document, or unavailable. |
| `source_snapshot_id` | Provenance snapshot representing the downloaded artifact, when used. |

Relationships:

- One filing may have multiple documents.
- One document may have many processing jobs and extraction attempts.
- Identical content hashes can reveal duplicate documents without discarding filing relationships.

### 9.2 `document_jobs`

Tracks download, text extraction, OCR, parsing, validation, and review work as resumable jobs.

| Field | Description |
|---|---|
| `document_job_id` | Database-generated primary identifier. |
| `filing_id` | Filing being processed. |
| `document_id` | Document being processed; may be empty for a download job that has not produced a document. |
| `job_type` | Download, verify, extract text, OCR, parse, validate, resolve ticker, or review. |
| `status` | Queued, running, retryable failure, permanent failure, needs review, or complete. |
| `priority` | Queue priority. |
| `attempt_count` | Number of attempts made. |
| `max_attempts` | Maximum automatic attempts. |
| `queued_at` | Queue time. |
| `started_at` | Most recent start time. |
| `finished_at` | Completion or failure time. |
| `next_attempt_at` | Earliest retry time. |
| `worker_name` | Worker or host handling the job. |
| `software_version` | Code version used for the job. |
| `error_type` | Classified error. |
| `error_message` | Human-readable error details. |

Relationships:

- A filing or document may have multiple jobs over time.
- Completed jobs prevent unnecessary repetition unless the user requests reprocessing with a newer parser or OCR version.

### 9.3 `document_extractions`

Stores the output and quality measurements from each text extraction, OCR, or parsing attempt.

| Field | Description |
|---|---|
| `document_extraction_id` | Database-generated primary identifier. |
| `document_id` | Source document. |
| `document_job_id` | Job that produced the extraction. |
| `extraction_type` | Embedded text, OCR, table extraction, or structured parser. |
| `extractor_name` | Tool or parser used. |
| `extractor_version` | Version of the tool or parser. |
| `output_path` | Location of extracted text or structured output. |
| `output_hash` | Hash of the extraction output. |
| `started_at` | Extraction start time. |
| `finished_at` | Extraction finish time. |
| `quality_score` | Overall extraction quality estimate. |
| `characters_extracted` | Number of extracted characters. |
| `pages_processed` | Number of pages processed. |
| `warnings` | Extraction warnings or anomalies. |
| `is_preferred` | Whether this is the currently preferred extraction for the document. |

Relationships:

- One document may have several extractions created by different tools or versions.
- Trades reference the parser/extraction from which they were produced.

## 10. PTR Transaction Tables

### 10.1 `trades`

Stores one reported transaction row extracted from a PTR or other disclosure filing.

| Field | Description |
|---|---|
| `trade_id` | Database-generated primary identifier. |
| `filing_id` | Filing containing the transaction. |
| `document_id` | Document from which the row was extracted. |
| `document_extraction_id` | Extraction or parser output that produced the row. |
| `source_row_number` | Stable row number or parser row key within the filing. |
| `transaction_date` | Date on which the reported transaction occurred. |
| `notification_date` | Date the filer was notified, when present. |
| `filed_date` | Disclosure date copied from or validated against the filing for convenient analysis. |
| `owner_type` | Self, spouse, dependent child, jointly held, or another normalized owner category. |
| `owner_raw` | Owner value exactly as printed. |
| `transaction_type` | Purchase, sale, exchange, or another normalized transaction category. |
| `transaction_type_raw` | Transaction code or label exactly as printed. |
| `asset_name_raw` | Asset description exactly as printed. |
| `asset_type_code_raw` | House, Senate, or source asset-type code exactly as printed. |
| `asset_type` | Normalized asset category. |
| `security_id` | Resolved security or asset; empty while unresolved. |
| `ticker_reported` | Ticker printed in the filing, if any. |
| `ticker_inferred` | Ticker inferred by the application. |
| `ticker_inference_method` | Exact lookup, name match, manual review, or another method. |
| `ticker_confidence` | Confidence in the inferred ticker. |
| `amount_range_raw` | Amount or range exactly as printed. |
| `amount_min` | Normalized lower bound. |
| `amount_max` | Normalized upper bound. |
| `amount_exact` | Exact value when the disclosure provides one. |
| `capital_gains_over_200` | Source indication that capital gains exceeded the reporting threshold, when present. |
| `description_raw` | Additional description or comments exactly as printed. |
| `is_amended` | Whether the row originates from an amendment or superseding filing. |
| `supersedes_trade_id` | Earlier trade row replaced or corrected by this row, when established. |
| `parser_name` | Parser that produced the record. |
| `parser_version` | Parser version. |
| `parse_confidence` | Overall confidence in the parsed row. |
| `review_status` | Unreviewed, accepted, corrected, rejected, or needs review. |
| `created_at` | Record creation time. |
| `updated_at` | Most recent update time. |

Relationships and rules:

- Many trades may belong to one filing.
- A trade may resolve to one security.
- A trade may have many evidence records.
- A transaction date and a filing date are separate facts and must never be substituted for one another.
- The filing supplies the member relationship; `member_id` is not duplicated here.
- A row is uniquely identified within a filing by its source row key and parser version. New parser versions may create a new extraction while preserving the earlier result for comparison.

### 10.2 `trade_evidence`

Connects parsed trade fields to the exact page, text, coordinates, or source fragment supporting them.

| Field | Description |
|---|---|
| `trade_evidence_id` | Database-generated primary identifier. |
| `trade_id` | Trade supported by the evidence. |
| `document_id` | Source document. |
| `document_extraction_id` | Extraction containing the evidence. |
| `field_name` | Trade field supported, or `row` for the complete transaction. |
| `page_number` | Source page number. |
| `source_text` | Relevant extracted text fragment. |
| `bounding_box` | Page coordinates of the evidence, when available. |
| `image_path` | Cropped source image used for review, when retained. |
| `confidence` | Confidence that the evidence supports the parsed value. |

Relationships:

- Many evidence records may support one trade.
- Evidence allows a reviewer to trace normalized data back to the filing without reparsing the entire document.

## 11. Security and Market Reference Tables

### 11.1 `securities`

Stores one normalized record for each resolved stock, fund, option underlying, bond, cryptocurrency, private asset, or other reportable asset.

| Field | Description |
|---|---|
| `security_id` | Database-generated primary identifier. |
| `security_type` | Common stock, ETF, mutual fund, bond, option, cryptocurrency, private company, real estate, or another category. |
| `issuer_name` | Normalized issuer or asset name. |
| `security_name` | Normalized security name. |
| `primary_exchange` | Primary exchange, when applicable. |
| `currency_code` | Trading or valuation currency. |
| `is_publicly_traded` | Whether public market data is expected. |
| `active_from` | Beginning of known activity. |
| `active_to` | End of known activity. |
| `source_snapshot_id` | Source supporting the security record. |

Relationships:

- One security may have many identifiers and many trades.
- An unresolved or non-public asset may still have a security record without a ticker.

### 11.2 `security_identifiers`

Stores ticker symbols and other identifiers with validity dates so ticker changes and reused symbols are handled correctly.

| Field | Description |
|---|---|
| `security_identifier_id` | Database-generated primary identifier. |
| `security_id` | Security identified by the value. |
| `identifier_type` | Ticker, CUSIP, ISIN, FIGI, LEI, SEC CIK, or another identifier system. |
| `identifier_value` | Identifier value. |
| `exchange_code` | Exchange or market qualifier when needed. |
| `valid_from` | First date the identifier applied. |
| `valid_to` | Last date the identifier applied. |
| `is_primary` | Whether it is the preferred current identifier of that type. |
| `source_snapshot_id` | Source supporting the identifier. |

Relationships and rules:

- Many identifiers may belong to one security.
- Ticker alone is not a permanent security key because symbols can change or be reused.

### 11.3 `market_prices`

Stores historical market observations used to analyze price changes before and after transactions or disclosures.

| Field | Description |
|---|---|
| `market_price_id` | Database-generated primary identifier. |
| `security_id` | Security being priced. |
| `price_date` | Market date of the observation. |
| `open_price` | Opening price. |
| `high_price` | Highest price. |
| `low_price` | Lowest price. |
| `close_price` | Closing price. |
| `adjusted_close_price` | Split- and distribution-adjusted close, when supplied. |
| `volume` | Trading volume. |
| `currency_code` | Currency of the values. |
| `price_source` | Market-data provider. |
| `retrieved_at` | Retrieval time. |
| `source_snapshot_id` | Preserved source artifact, when applicable. |

Relationships and rules:

- Many price observations belong to one security.
- A security has at most one preferred observation per date and price source.
- Backtests must use the transaction date and disclosure date separately to prevent look-ahead bias.

### 11.4 `corporate_actions`

Stores splits, mergers, symbol changes, spin-offs, and other events needed to interpret historical prices and identifiers.

| Field | Description |
|---|---|
| `corporate_action_id` | Database-generated primary identifier. |
| `security_id` | Security affected by the action. |
| `action_type` | Split, merger, acquisition, spin-off, symbol change, delisting, or another event. |
| `effective_date` | Date the action became effective. |
| `ratio_or_terms` | Split ratio or other structured terms. |
| `related_security_id` | Successor, parent, child, or other related security, when applicable. |
| `description` | Human-readable description. |
| `source_snapshot_id` | Source supporting the action. |

Relationships:

- Many corporate actions may affect one security.
- An action may link an old security record to a successor or related security.

## 12. Staging Tables

Staging tables hold source-shaped records before normalization. They allow imports to be inspected, corrected, and repeated without polluting the production tables.

### 12.1 `staging_members`

Temporary representation of member records imported from current and historical legislator files or APIs.

Key fields include the import identifier, source row number, raw source record, parsed name, source identifiers, chamber, state, district, party, term dates, validation status, and error details.

### 12.2 `staging_committees`

Temporary representation of current and historical committee definitions.

Key fields include the import identifier, source row number, raw committee code, name, chamber, committee type, parent code, Congress coverage, validation status, and error details.

### 12.3 `staging_committee_memberships`

Temporary representation of current or historical member-to-committee assignments.

Key fields include the import identifier, source row number, raw Bioguide value, raw member name, committee code, Congress number, party side, rank, title, dates, validation status, and error details.

### 12.4 `staging_house_filings`

Temporary representation of House annual XML filing-index rows.

Key fields include the import identifier, source row number, DocID, year, filing type code, filer name, state/district or office text, filing date, document URL, raw XML fragment, validation status, and error details.

### 12.5 `staging_senate_filings`

Temporary representation of Senate eFD search results and filing metadata.

Key fields include the import identifier, source row number, canonical filing identifier or URL, filer name, office, filing type, filing date, report period, raw source fragment, validation status, and error details.

### 12.6 `staging_house_trades`

Temporary source-shaped House transaction rows before they are validated and loaded into `trades`.

Key fields include the filing and extraction identifiers, raw row number, raw dates, owner, asset name, asset code, transaction code, amount, ticker, description, parse warnings, and raw row content.

### 12.7 `staging_senate_trades`

Temporary source-shaped Senate transaction rows before they are validated and loaded into `trades`.

Key fields parallel the House staging table while retaining Senate-specific labels and source structure.

Staging relationships and rules:

- Every staging row belongs to a `source_import`.
- Staging rows may point to the production record created from them.
- Rejected records remain available with validation messages.
- Staging tables are not used directly for user-facing analytics.

## 13. Important Status Values

The exact implementation may use reference tables or application-level controlled values. At minimum, the filing and document workflow needs these states:

| Status | Meaning |
|---|---|
| `discovered` | Filing metadata is cataloged but not selected. |
| `selected` | Filing matches a user or scheduled request. |
| `queued` | Work has been placed in the processing queue. |
| `downloaded` | Source document has been saved. |
| `verified` | Downloaded content has passed integrity and identity checks. |
| `text_extracted` | Usable text has been extracted. |
| `needs_ocr` | Embedded text is missing or inadequate. |
| `parsed` | Structured rows have been produced. |
| `needs_review` | Identity, extraction, ticker, or validation is uncertain. |
| `failed_retryable` | Work failed but may be attempted again. |
| `failed_permanent` | Automatic retries are not expected to succeed. |
| `complete` | Required processing and validation are finished. |

## 14. Core Integrity Rules

1. A member is represented by one stable `member_id`; changing terms or names do not create a new member.
2. A source identifier cannot silently point to two different members.
3. A filing is unique by source and source filing identifier.
4. A House DocID identifies a filing, not a member or filing type.
5. Filing type comes from the filing index or document metadata and is stored independently of DocID.
6. A filing may exist without a downloaded document and without a resolved member.
7. An ambiguous filer match is reviewed instead of automatically creating a new member.
8. Raw source values are never overwritten by normalized or inferred values.
9. Inferred tickers and identities include their method and confidence.
10. Transaction date, notification date, and filing date remain separate.
11. Committee membership must be valid for the Congress or date being analyzed.
12. A ticker is a time-dependent identifier, not the primary key of a security.
13. Reprocessing with a new parser or OCR version preserves prior extraction provenance.
14. Overlapping download selections reuse verified documents and completed work.
15. Every published trade can be traced to a filing, document, extraction version, and supporting evidence.

## 15. Example Analytical Paths

### Top members by number of PTRs in a year

Count PTR-type `filings`, group them by their resolved member, and restrict `filed_date` or `reporting_year` to the requested year.

### State with the most PTRs

Connect each filing to the member term active on the filing date, then group the filings by the term's state.

### All PTRs for a member across selected years

Find the member using Bioguide or another identifier, then follow the member-to-filing relationship and filter to PTR filings and the requested years.

### Committees held by a member during a term

Connect the member to committee memberships and restrict assignments by Congress or membership dates. Follow `parent_committee_id` when main committees must be separated from subcommittees.

### Members serving on a committee

Locate the committee by code and Congress, then return memberships valid during that period and connect them to member and member-term records.

### Party makeup of Congress under each president

Connect presidential term dates to overlapping member terms, group active members by chamber and party, and calculate the composition for each administration or Congress.

### Price movement after a transaction or disclosure

Resolve the trade to a security, connect it to historical market prices, and calculate returns from the transaction date and filing date as separate analyses.

### Selective document download

Filter cataloged filings by member, state, chamber, filing type, year, and processing status. Create a selection batch and filing selections only for matching records. Existing verified documents and completed extractions are reused automatically.

## 16. Initial Test Records

Nancy Pelosi is the primary House end-to-end test member. Her preferred external identifier is Bioguide `P000197`. Tests should cover member import, term history, filing identity resolution, selective PTR download, extraction, transaction storage, ticker resolution, evidence, and price analysis.

Elizabeth Warren, Bioguide `W000817`, is an appropriate Senate test member for the Senate eFD path and committee-membership analysis.

Test data is illustrative. Source field names and values may differ between House, Senate, Congress.gov, congress-legislators, and Quantgress inputs. Importers must map source-specific structures into this normalized schema while preserving the raw values and provenance.
