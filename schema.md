# Congressional Trading Analytics Platform — Database Schema

## 1. Purpose

This document is the authoritative, non-SQL description of the StockGov PostgreSQL schema. It covers congressional reference data, filing discovery, selective document processing, extracted PTR transactions, securities, prices, and source provenance.

The congressional reference loader uses the eight YAML files in `data/raw/congress` as canonical inputs. JSON and CSV editions are retained as companion source snapshots and are not loaded as duplicate records. Files ending in `V1` are backups and are not imported.

## 2. Design rules

- Database-generated `BIGINT` identity values are internal keys.
- External IDs remain unchanged and are stored in identifier tables.
- Time-dependent facts use dates or Congress numbers.
- Normalized values are stored alongside raw source values where interpretation is required.
- Every imported record retains source provenance or raw staging evidence.
- A filing may remain unmatched until its member identity is sufficiently certain.
- Reference data from `congress-legislators` is separate from House and Senate financial-disclosure data.

## 3. Relationship overview

```text
source_snapshots --< source_imports
        |
        +--< members --< member_terms --< member_term_party_affiliations
        |       |--< member_names
        |       |--< member_identifiers
        |       |--< member_family_relationships
        |       |--< leadership_roles
        |       |--< member_offices
        |       |--< member_social_accounts
        |       `--< committee_memberships >-- committees
        |
        +--< executives --< executive_terms
        |
        `--< filings --< filing_source_occurrences
                      |--< documents --< document_jobs
                      |             `--< document_extractions
                      `--< trades --< trade_evidence
                                  `--> securities --< security_identifiers

committees --< committee_identifiers
           |--< committee_congresses
           `--< committees (parent to subcommittee)
```

The central analytical path is `member -> filing -> trade -> security`.

## 4. Source and provenance

### 4.1 `source_snapshots`

One immutable description of a downloaded source file, API response, page, or document version.

Fields: `source_snapshot_id`, `source_name`, `source_type`, `source_url`, `local_path`, `coverage_start_date`, `coverage_end_date`, `retrieved_at`, `content_hash`, `file_size_bytes`, `format_version`, `notes`.

Unique identity: `source_name` plus `content_hash`.

### 4.2 `source_imports`

One execution record for an attempt to import a source snapshot.

Fields: `source_import_id`, `source_snapshot_id`, `import_type`, `importer_version`, `started_at`, `finished_at`, `status`, `records_read`, `records_inserted`, `records_updated`, `records_rejected`, `error_summary`.

Relationship: many imports may refer to one source snapshot.

## 5. Members and congressional service

### 5.1 `members`

One stable person record regardless of changes in chamber, state, district, party, or name.

Fields: `member_id`, `preferred_name`, `first_name`, `middle_name`, `last_name`, `suffix`, `nickname`, `date_of_birth`, `gender`, `is_living`, `created_at`, `updated_at`, `source_snapshot_id`.

### 5.2 `member_names`

Preferred, official, former, nickname, source, and alias forms used for display and identity resolution.

Fields: `member_name_id`, `member_id`, `name_type`, `full_name`, `first_name`, `middle_name`, `last_name`, `suffix`, `normalized_name`, `valid_from`, `valid_to`, `source_snapshot_id`.

Unique identity: member, name type, normalized name, and start date.

### 5.3 `member_identifiers`

Crosswalk identifiers such as Bioguide, FEC, GovTrack, LIS, ICPSR, Wikidata, Wikipedia, C-SPAN, OpenSecrets, VoteSmart, Ballotpedia, Pictorial, House History, and social account IDs.

Fields: `member_identifier_id`, `member_id`, `identifier_type`, `identifier_value`, `is_primary`, `valid_from`, `valid_to`, `source_snapshot_id`.

Rule: one identifier type/value pair may identify only one member. Bioguide is the preferred primary cross-source identifier.

### 5.4 `member_terms`

One elected, appointed, or special-election congressional service period.

Fields: `member_term_id`, `member_id`, `chamber`, `term_start_date`, `term_end_date`, `congress_start`, `congress_end`, `state_code`, `district_number`, `senate_class`, `senate_state_rank`, `party_code`, `party_name_raw`, `caucus_party_code`, `caucus_party_name_raw`, `term_type`, `term_end_type`, `official_website_url`, `contact_form_url`, `rss_url`, `source_snapshot_id`.

Rules: House terms may have a district but no Senate class/rank; Senate terms may have class/rank but no district. Historical House district `-1` is normalized to null.

### 5.5 `member_term_party_affiliations`

Dated party and caucus periods within a congressional term.

Fields: `member_term_party_affiliation_id`, `member_term_id`, `party_code`, `party_name_raw`, `caucus_party_code`, `caucus_party_name_raw`, `start_date`, `end_date`, `source_snapshot_id`.

Unique identity: member term and affiliation start date.

### 5.6 `member_family_relationships`

Family relationships reported in the legislator source, with an optional link when the relative is also a known member.

Fields: `member_family_relationship_id`, `member_id`, `related_member_id`, `relative_name`, `relationship_type`, `normalized_relative_name`, `valid_from`, `valid_to`, `source_snapshot_id`.

### 5.7 `leadership_roles`

Congressional and party leadership positions independent of elected term boundaries.

Fields: `leadership_role_id`, `member_id`, `chamber`, `role_title`, `party_code`, `start_date`, `end_date`, `congress_start`, `congress_end`, `source_snapshot_id`.

### 5.8 `member_offices`

Capitol and district offices, including source office identity, contact details, hours, and coordinates.

Fields: `member_office_id`, `member_id`, `office_type`, `source_office_id`, `building`, `room`, `suite`, `address_line_1`, `address_line_2`, `city`, `state_code`, `postal_code`, `phone`, `fax`, `hours_text`, `latitude`, `longitude`, `valid_from`, `valid_to`, `source_snapshot_id`.

Rule: `office_type` is a category such as `capitol` or `district`; it does not contain the source office ID.

### 5.9 `member_social_accounts`

Official social-media accounts and stable platform identifiers.

Fields: `member_social_account_id`, `member_id`, `platform`, `account_name`, `platform_account_id`, `account_url`, `is_official`, `valid_from`, `valid_to`, `source_snapshot_id`.

Platforms currently include Twitter/X, Facebook, Instagram, YouTube, and Mastodon.

## 6. Committees

### 6.1 `committees`

House, Senate, joint, standing, select, special, other, and subcommittee definitions. A subcommittee points to its parent using `parent_committee_id`.

Fields: `committee_id`, `committee_code`, `chamber`, `committee_type`, `name`, `name_raw`, `parent_committee_id`, `is_current`, `website_url`, `minority_website_url`, `jurisdiction_text`, `jurisdiction_source_url`, `address`, `phone`, `rss_url`, `minority_rss_url`, `youtube_channel_id`, `wikipedia_name`, `source_snapshot_id`.

### 6.2 `committee_identifiers`

Distinct Thomas, House, Senate, and future committee identifier systems.

Fields: `committee_identifier_id`, `committee_id`, `identifier_type`, `identifier_value`, `is_primary`, `source_snapshot_id`.

Rule: one identifier type/value pair may identify only one committee.

### 6.3 `committee_congresses`

Committee names, codes, and active status for individual Congresses.

Fields: `committee_congress_id`, `committee_id`, `congress_number`, `name`, `committee_code`, `is_active`, `source_snapshot_id`.

Unique identity: committee and Congress number.

### 6.4 `committee_memberships`

Member assignments to full committees or subcommittees. Each membership points directly to the applicable committee row, so full-committee and subcommittee ranks remain separate.

Fields: `committee_membership_id`, `committee_id`, `member_id`, `congress_number`, `member_chamber`, `start_date`, `end_date`, `party_side`, `rank`, `title`, `is_ex_officio`, `source_snapshot_id`.

Rules: `member_chamber` identifies the House or Senate side of joint committees. `is_ex_officio` is normalized from an explicit flag or an `Ex Officio` title. The current source does not establish historical membership dates.

## 7. Executive administrations

### 7.1 `executives`

Presidents and vice presidents used to associate congressional composition and trades with administrations.

Fields: `executive_id`, `full_name`, `first_name`, `middle_name`, `last_name`, `suffix`, `nickname`, `official_full_name`, `date_of_birth`, `gender`, `bioguide_id`, `source_snapshot_id`.

### 7.2 `executive_identifiers`

Executive crosswalk identifiers, including Bioguide, GovTrack, ICPSR, presidential ICPSR, LIS, FEC, C-SPAN, Wikidata, Wikipedia, and other supplied systems.

Fields: `executive_identifier_id`, `executive_id`, `identifier_type`, `identifier_value`, `is_primary`, `source_snapshot_id`.

### 7.3 `executive_terms`

Presidential and vice-presidential service periods.

Fields: `executive_term_id`, `executive_id`, `office`, `term_start_date`, `term_end_date`, `party_code`, `accession_method`, `term_number`, `source_snapshot_id`.

`accession_method` preserves source values such as election, succession, or appointment.

## 8. Filing catalog and selection

### 8.1 `filings`

One catalog record for every discovered House or Senate disclosure, whether or not its document has been downloaded.

Fields: `filing_id`, `source`, `source_filing_id`, `chamber`, `filing_type_code_raw`, `filing_type`, `reporting_year`, `filed_date`, `report_period_start`, `report_period_end`, `member_id`, `raw_first_name`, `raw_last_name`, `raw_full_name`, `raw_office`, `state_code_guess`, `district_guess`, `member_match_status`, `member_match_method`, `member_match_confidence`, `source_url`, `discovered_at`, `processing_status`, `source_snapshot_id`, `source_import_id`.

Rule: source plus source filing ID is unique. `member_id` remains null until identity resolution is sufficiently certain.

House filing codes normalize to `amendment`, `blind_trust`, `candidate_report`, `candidate_threshold_declaration`, `termination_exemption`, `gift_waiver`, `new_filer`, `annual_disclosure`, `ptr`, `ptr_waiver`, `termination`, `candidate_withdrawal`, and `extension`. The original code is always retained.

### 8.2 `member_match_candidates`

Candidate members evaluated while resolving a filing's raw filer identity.

Fields: `member_match_candidate_id`, `filing_id`, `candidate_member_id`, `candidate_rank`, `match_score`, `name_score`, `office_score`, `term_score`, `match_reasons`, `decision`, `reviewed_at`, `reviewed_by`.

### 8.3 `filing_source_occurrences`

One row for each appearance of a normalized filing in a source index. This preserves duplicate rows and documents that recur in more than one annual House index without duplicating the normalized `filings` record.

Fields: `filing_source_occurrence_id`, `filing_id`, `source_snapshot_id`, `source_import_id`, `source_row_number`, `index_year`.

Unique identity: source snapshot and source row number.

### 8.4 `selection_batches`

A user-created or scheduled request selecting filings by member, state, year, chamber, filing type, or processing status.

Fields: `selection_batch_id`, `batch_name`, `requested_by`, `filter_definition`, `created_at`, `started_at`, `finished_at`, `status`, `filings_selected`, `filings_completed`, `notes`.

Implementation phase: introduce this table when the PDF-selection interface or scheduled batch downloader is built. It may remain empty during filing-index ingestion.

### 8.5 `filing_selections`

Connects individual filings to selection batches without causing duplicate downloads.

Fields: `filing_selection_id`, `filing_id`, `selection_batch_id`, `selection_reason`, `priority`, `selected_at`, `selected_by`, `is_active`.

Purpose: the same filing can be selected by overlapping member, state, year, or filing-type requests without creating duplicate document downloads.

## 9. Documents and processing

The processing model separates the source document, work performed on the document, and versioned extraction results. This structure supports interrupted nightly processing and parser upgrades, but StockGov will implement it incrementally.

Initial processing path:

```text
filing -> document -> download/extract/parse job -> extraction -> trade
```

Initial implementation uses `documents`, `document_jobs`, and `document_extractions` only for download, text extraction, OCR when required, and PTR parsing. Advanced job types and field-level evidence remain deferred until demonstrated by real source documents.

### 9.1 `documents`

Downloaded or locally supplied filing documents and integrity metadata.

Fields: `document_id`, `filing_id`, `document_type`, `source_url`, `local_path`, `mime_type`, `file_size_bytes`, `content_hash`, `downloaded_at`, `http_status`, `is_primary`, `page_count`, `has_embedded_text`, `requires_ocr`, `verification_status`, `source_snapshot_id`.

Implementation phase: required for the first PDF-download pipeline.

Rules:

- The PDF remains on disk; this table stores its identity, path, integrity data, and verification state.
- A filing may have more than one document when amendments, replacements, or alternate copies must be retained.
- At most one document is marked primary for a filing.
- A repeated download with the same filing and content hash must not create another document row.

### 9.2 `document_jobs`

Resumable download, verification, extraction, OCR, parsing, ticker resolution, validation, and review work.

Fields: `document_job_id`, `filing_id`, `document_id`, `job_type`, `status`, `priority`, `attempt_count`, `max_attempts`, `queued_at`, `started_at`, `finished_at`, `next_attempt_at`, `worker_name`, `software_version`, `error_type`, `error_message`.

Implementation phase: use initially for `download`, `extract_text`, `ocr`, and `parse`. Defer `validate`, `resolve_ticker`, and `review` workers until those workflows exist.

Rules:

- A `download` job may have a null `document_id` because the job can exist before the PDF is downloaded.
- `verify`, `extract_text`, `ocr`, and `parse` jobs require a document before they can run.
- When both `filing_id` and `document_id` are populated, the document must belong to the same filing.
- Retrying work updates the attempt count and job status; it does not create another document record.
- A completed job is historical execution information and must not be treated as the extracted result itself.

### 9.3 `document_extractions`

Versioned outputs and quality measurements from extraction and parsing attempts.

Fields: `document_extraction_id`, `document_id`, `document_job_id`, `extraction_type`, `extractor_name`, `extractor_version`, `output_path`, `output_hash`, `started_at`, `finished_at`, `quality_score`, `characters_extracted`, `bytes_extracted`, `pages_processed`, `warnings`, `is_preferred`.

Implementation phase: required when text extraction or PTR parsing begins.

Rules:

- Each materially different extractor or parser version produces a separate extraction row.
- Earlier extraction rows are retained so parser output can be audited and compared.
- `is_preferred` identifies the extraction currently used to produce normalized results.
- When `document_job_id` is present, its `document_id` must agree with the extraction's `document_id`.
- Output text and structured parser artifacts may remain on disk; the database stores their paths, hashes, versions, and quality measurements.

## 10. Securities and trades

### 10.1 `securities`

Normalized financial instruments and issuers.

Fields: `security_id`, `security_type`, `issuer_name`, `security_name`, `primary_exchange`, `currency_code`, `is_publicly_traded`, `active_from`, `active_to`, `source_snapshot_id`.

### 10.2 `security_identifiers`

Time-valid tickers, CUSIPs, FIGIs, ISINs, CIKs, and other security identifiers.

Fields: `security_identifier_id`, `security_id`, `identifier_type`, `identifier_value`, `exchange_code`, `valid_from`, `valid_to`, `is_primary`, `source_snapshot_id`.

### 10.3 `trades`

One normalized PTR transaction line, retaining the reported values, inferred security information, parser provenance, and amendment relationship.

Fields: `trade_id`, `filing_id`, `document_id`, `document_extraction_id`, `source_row_number`, `source_page_number`, `source_transaction_id_raw`, `transaction_date`, `notification_date`, `filed_date`, `owner_type`, `owner_raw`, `transaction_type`, `transaction_type_raw`, `asset_name_raw`, `asset_type_code_raw`, `asset_type`, `security_id`, `ticker_reported`, `ticker_inferred`, `ticker_inference_method`, `ticker_confidence`, `amount_range_raw`, `amount_min`, `amount_max`, `amount_exact`, `capital_gains_over_200`, `description_raw`, `is_partial_sale`, `is_annual_report_transaction`, `transaction_sequence`, `is_amended`, `supersedes_trade_id`, `parser_name`, `parser_version`, `is_current_parser_result`, `parse_confidence`, `review_status`, `created_at`, `updated_at`.

Relationship: member identity is obtained through `trades.filing_id -> filings.member_id`; it is not duplicated on the trade.

Consistency rules:

- When `document_id` is present, the document must belong to `filing_id`.
- When `document_extraction_id` is present, the extraction must belong to `document_id` and ultimately to the same filing.
- The repeated filing, document, and extraction references are retained for practical querying and provenance, but the validator must reject inconsistent combinations.
- `is_current_parser_result` distinguishes the latest active result from retained historical parser output after a reparse.

### 10.4 `trade_evidence`

Page text, locations, images, and confidence supporting individual extracted fields.

Fields: `trade_evidence_id`, `trade_id`, `document_id`, `document_extraction_id`, `field_name`, `page_number`, `source_text`, `bounding_box`, `image_path`, `confidence`.

Implementation phase: deferred. The initial PTR pipeline relies on the trade's raw fields, source row number, parser name and version, parse confidence, and document extraction link. Populate `trade_evidence` only when field-level review, page highlighting, OCR diagnosis, or audit requirements justify the additional storage and processing.

When implemented, the evidence document and extraction must agree with the corresponding trade's document chain.

### 10.5 `market_prices`

Daily security prices used to measure performance from transaction and disclosure dates.

Fields: `market_price_id`, `security_id`, `price_date`, `open_price`, `high_price`, `low_price`, `close_price`, `adjusted_close_price`, `volume`, `currency_code`, `price_source`, `retrieved_at`, `source_snapshot_id`.

### 10.6 `corporate_actions`

Splits, mergers, acquisitions, symbol changes, spinoffs, and other events affecting historical comparisons.

Fields: `corporate_action_id`, `security_id`, `action_type`, `effective_date`, `ratio_or_terms`, `related_security_id`, `description`, `source_snapshot_id`.

### 10.7 Processing implementation priorities

The presence of a table in the schema does not require its application workflow to be built immediately.

| Priority | Tables | Planned use |
|---|---|---|
| Use now | `filings`, `documents`, `document_jobs`, `document_extractions`, `trades` | Filing catalog, PDF download, retry tracking, extraction, and parsed PTR rows |
| Add with selection interface | `selection_batches`, `filing_selections` | Member, state, year, and filing-type download requests with overlap tracking |
| Defer | `trade_evidence` | Field-level source text, page locations, images, and review evidence |
| Defer | `corporate_actions` and advanced security resolution | Performance adjustments after the basic trade pipeline is reliable |

Deferred tables may remain empty. No application code should be added solely because a future-facing table exists.

## 11. Staging tables

Staging tables preserve source rows, validation outcomes, and links to accepted normalized records.

### 11.1 `staging_members`

Fields: `staging_member_id`, `source_import_id`, `source_row_number`, `raw_record`, `raw_full_name`, `normalized_name`, `raw_identifiers`, `chamber`, `state_code`, `district_number`, `party_raw`, `term_start_date`, `term_end_date`, `validation_status`, `error_details`, `member_id`.

### 11.2 `staging_committees`

Fields: `staging_committee_id`, `source_import_id`, `source_row_number`, `raw_record`, `committee_code_raw`, `name_raw`, `chamber_raw`, `committee_type_raw`, `parent_code_raw`, `congress_start`, `congress_end`, `validation_status`, `error_details`, `committee_id`.

### 11.3 `staging_committee_memberships`

Fields: `staging_committee_membership_id`, `source_import_id`, `source_row_number`, `raw_record`, `bioguide_id_raw`, `member_name_raw`, `committee_code_raw`, `congress_number`, `party_side_raw`, `rank_raw`, `title_raw`, `start_date`, `end_date`, `validation_status`, `error_details`, `committee_membership_id`.

### 11.4 `staging_house_filings`

Fields: `staging_house_filing_id`, `source_import_id`, `source_row_number`, `doc_id_raw`, `reporting_year_raw`, `filing_type_code_raw`, `prefix_raw`, `first_name_raw`, `last_name_raw`, `suffix_raw`, `state_district_raw`, `filed_date_raw`, `document_url_raw`, `raw_xml`, `validation_status`, `error_details`, `filing_id`.

`state_district_raw` preserves the exact House `StateDst` value. Valid values are parsed into `filings.state_code_guess` and `filings.district_guess`; malformed and blank values remain available for review.

### 11.5 `staging_senate_filings`

Fields: `staging_senate_filing_id`, `source_import_id`, `source_row_number`, `source_filing_id_raw`, `filing_url_raw`, `filer_name_raw`, `office_raw`, `filing_type_raw`, `filed_date_raw`, `report_period_start_raw`, `report_period_end_raw`, `raw_record`, `validation_status`, `error_details`, `filing_id`.

### 11.6 `staging_house_trades`

Fields: `staging_house_trade_id`, `source_import_id`, `filing_id`, `document_extraction_id`, `source_row_number`, `source_page_number`, `source_transaction_id_raw`, `transaction_date_raw`, `notification_date_raw`, `owner_raw`, `asset_name_raw`, `asset_type_code_raw`, `transaction_type_raw`, `amount_raw`, `ticker_raw`, `description_raw`, `raw_record`, `parse_warnings`, `validation_status`, `error_details`, `trade_id`, `parser_name`, `parser_version`, `parse_confidence`.

The staging row preserves every candidate parsed from a House PTR before loading. Invalid candidates remain available for review; valid candidates link to the resulting `trades` row through `trade_id`.

### 11.7 `staging_senate_trades`

Fields: `staging_senate_trade_id`, `source_import_id`, `filing_id`, `document_extraction_id`, `source_row_number`, `transaction_date_raw`, `notification_date_raw`, `owner_raw`, `asset_name_raw`, `asset_type_code_raw`, `transaction_type_raw`, `amount_raw`, `ticker_raw`, `description_raw`, `raw_record`, `parse_warnings`, `validation_status`, `error_details`, `trade_id`.

## 12. Current source mappings

| Source file | Primary normalized destinations |
|---|---|
| `legislators-current.yaml` | members, names, identifiers, terms, affiliations, family, leadership, Capitol offices |
| `legislators-historical.yaml` | same member tables for former members |
| `legislators-social-media.yaml` | member identifiers and social accounts |
| `legislators-district-offices.yaml` | member offices |
| `committees-current.yaml` | committees, identifiers, Congress records |
| `committees-historical.yaml` | committees, identifiers, historical names and Congress records |
| `committee-membership-current.yaml` | committee memberships |
| `executive.yaml` | executives, executive identifiers, executive terms |
| `2008FD.xml` through `2026FD.xml` | source snapshots, source imports, staged House filings, normalized filings, filing source occurrences, member match candidates |

House filing indexes populate the filing catalog and matching tables. Downloaded House documents and extracted trades, along with Senate disclosure sources, populate documents, trades, and their staging tables in later ingestion stages.

## 13. Initial validation subjects

- Nancy Pelosi is the primary House end-to-end test member.
- Mike Crapo is a Senate test member with committee, campaign-finance, lobbying, and PTR cross-source use cases.
- Small-state tests such as New Hampshire, North Dakota, and South Dakota validate state filtering and sparse historical selections.

## 14. Full financial-disclosure expansion

House filing types `C`, `H`, `O`, and `T` contain substantially more information than a Periodic Transaction Report. They may report assets, asset income, earned income, liabilities, gifts, travel, outside positions, agreements, compensation sources, charitable payments, transactions, exclusions, and certification details. Administrative filings also explain amendments, extensions, waivers, exemptions, candidate status, and otherwise missing reports.

The following tables extend the existing `filings`, `documents`, and `trades` model. They are organized by the information being reported rather than by House filing-type code. This avoids creating a separate table for every form while preserving normalized, queryable financial facts.

The initial expansion adds the first fifteen tables in this section. The two trust tables are optional until blind-trust parsing is implemented.

### 14.1 `disclosure_reports`

One report-level record for each candidate, new-filer, annual, or termination disclosure. Stores the form version, reporting period, filer status, termination date, certification details, and exclusion flags.

Fields: `disclosure_report_id`, `filing_id`, `form_version`, `report_status`, `report_period_start`, `report_period_end`, `termination_date`, `candidate_election_date`, `certification_date`, `signature_method`, `trust_information_excluded`, `spouse_dependent_information_excluded`, `created_at`, `updated_at`.

Relationship: each qualifying filing has at most one disclosure report, and each disclosure report belongs to exactly one filing.

Rules:

- `filing_id` is unique.
- The normalized report retains the raw House filing classification in `filings.filing_type_code_raw`.
- `form_version` must distinguish older paper schedules from newer electronically generated reports.
- Report period values supplement the catalog fields on `filings`; they do not replace the raw filing-index evidence.

### 14.2 `disclosure_questions`

Stores the preliminary yes/no questions appearing on disclosure forms. This supports different questions and form versions without repeatedly adding columns to `disclosure_reports`.

Fields: `disclosure_question_id`, `disclosure_report_id`, `question_code`, `question_text_raw`, `question_type`, `answer`, `related_schedule_raw`, `source_page_number`, `source_row_number`.

Relationship: one disclosure report may contain many questions.

Rules:

- Preserve the original question text and raw schedule reference.
- Normalize answers without discarding ambiguous, blank, or illegible source values.

### 14.3 `disclosure_assets`

Stores securities, real estate, businesses, partnerships, retirement accounts, trusts, notes receivable, bank accounts, and other reported assets. A self-reference supports assets held inside accounts such as IRAs.

Fields: `disclosure_asset_id`, `disclosure_report_id`, `parent_asset_id`, `security_id`, `owner_type`, `owner_raw`, `asset_name_raw`, `asset_type_code_raw`, `asset_type`, `description`, `location_city`, `location_state`, `location_country`, `value_range_raw`, `value_min`, `value_max`, `is_excepted_investment_fund`, `is_blind_trust`, `schedule_raw`, `source_page_number`, `source_row_number`, `parse_confidence`, `review_status`.

Relationships:

- One disclosure report may contain many assets.
- `parent_asset_id` points to another asset in the same report and represents nested holdings such as a security held inside an IRA or brokerage account.
- `security_id` is optional because real estate, private companies, partnerships, and unresolved assets may not have a record in `securities`.

### 14.4 `disclosure_asset_income`

Stores income associated with an asset. One asset can produce multiple income types, such as dividends, interest, rent, capital gains, royalties, or partnership income.

Fields: `disclosure_asset_income_id`, `disclosure_asset_id`, `income_type`, `income_type_raw`, `income_period`, `amount_range_raw`, `amount_min`, `amount_max`, `amount_exact`, `is_tax_deferred`, `source_page_number`, `source_row_number`.

Relationship: one disclosure asset may have zero or many income records.

### 14.5 `disclosure_earned_income`

Stores salaries, management fees, employment income, self-employment, and spouse-earned income. It also preserves current-year and preceding-year amounts.

Fields: `disclosure_earned_income_id`, `disclosure_report_id`, `owner_type`, `owner_raw`, `source_name`, `source_location`, `income_type`, `income_type_raw`, `amount_current_year`, `amount_preceding_year`, `amount_raw`, `schedule_raw`, `source_page_number`, `source_row_number`.

Relationship: one disclosure report may contain many earned-income records.

### 14.6 `disclosure_liabilities`

Stores mortgages, lines of credit, margin accounts, unsecured loans, creditors, dates incurred, related properties, and reported balance ranges.

Fields: `disclosure_liability_id`, `disclosure_report_id`, `owner_type`, `owner_raw`, `creditor_name`, `date_incurred`, `date_incurred_raw`, `liability_type`, `liability_type_raw`, `associated_asset_id`, `associated_asset_raw`, `amount_range_raw`, `amount_min`, `amount_max`, `interest_rate_raw`, `term_raw`, `schedule_raw`, `source_page_number`, `source_row_number`.

Relationship: `associated_asset_id` may point to the asset or property securing the liability when the relationship can be established.

### 14.7 `disclosure_gifts`

Stores the gift source, recipient, description, date, value, and relationship to a gift-waiver filing.

Fields: `disclosure_gift_id`, `disclosure_report_id`, `owner_type`, `owner_raw`, `source_name`, `description`, `gift_date`, `gift_date_raw`, `value_raw`, `value_amount`, `value_min`, `value_max`, `filing_action_id`, `schedule_raw`, `source_page_number`, `source_row_number`.

Relationship: `filing_action_id` optionally connects a disclosed gift to its gift-waiver request or decision.

### 14.8 `disclosure_travel`

Stores reimbursed travel: sponsor, traveler, dates, destination, lodging, food, family participation, and days paid personally.

Fields: `disclosure_travel_id`, `disclosure_report_id`, `traveler_type`, `traveler_name_raw`, `sponsor_name`, `departure_date`, `return_date`, `departure_location`, `destination`, `lodging_provided`, `food_provided`, `family_member_included`, `family_member_name_raw`, `days_not_at_sponsor_expense`, `description`, `schedule_raw`, `source_page_number`, `source_row_number`.

### 14.9 `disclosure_positions`

Stores positions held outside Congress, including organization, title, dates, compensation status, and position category.

Fields: `disclosure_position_id`, `disclosure_report_id`, `position_title`, `organization_name`, `position_category`, `start_date`, `end_date`, `is_compensated`, `description`, `schedule_raw`, `source_page_number`, `source_row_number`.

### 14.10 `disclosure_agreements`

Stores employment, pension, leave, compensation, and other continuing arrangements reported by the filer.

Fields: `disclosure_agreement_id`, `disclosure_report_id`, `other_party_name`, `agreement_type`, `agreement_type_raw`, `agreement_date`, `effective_date`, `end_date`, `terms_text`, `status`, `schedule_raw`, `source_page_number`, `source_row_number`.

### 14.11 `disclosure_compensation_sources`

Stores sources that paid compensation exceeding the applicable reporting threshold, including source name, description, amount when supplied, and threshold.

Fields: `disclosure_compensation_source_id`, `disclosure_report_id`, `source_name`, `source_location`, `services_description`, `amount_raw`, `amount_exact`, `reporting_threshold`, `schedule_raw`, `source_page_number`, `source_row_number`.

### 14.12 `disclosure_charitable_payments`

Stores payments made to charity instead of honoraria, including the payer, activity, recipient charity, date, and amount.

Fields: `disclosure_charitable_payment_id`, `disclosure_report_id`, `payer_name`, `activity_description`, `charity_name`, `payment_date`, `amount_raw`, `amount_exact`, `schedule_raw`, `source_page_number`, `source_row_number`.

### 14.13 `filing_actions`

Stores administrative events affecting a filing, including extension requests and decisions, candidate threshold declarations, candidate withdrawals, termination exemptions, gift waivers, PTR waivers, and blind-trust approvals.

Fields: `filing_action_id`, `filing_id`, `action_type`, `request_date`, `decision_date`, `effective_date`, `original_deadline`, `requested_deadline`, `granted_deadline`, `status`, `reason`, `authority`, `fee_amount`, `raw_details`, `source_page_number`, `created_at`, `updated_at`.

Representative `action_type` values: `extension_request`, `extension_granted`, `termination_exemption`, `candidate_below_threshold`, `candidate_withdrawal`, `gift_waiver`, `ptr_waiver`, and `blind_trust_approval`.

Relationship: one filing may describe one or more administrative actions.

### 14.14 `filing_action_subjects`

Identifies what a filing action affects. For example, it can connect a PTR waiver to a particular partnership or connect an extension to the report whose deadline was extended.

Fields: `filing_action_subject_id`, `filing_action_id`, `subject_type`, `subject_name_raw`, `disclosure_asset_id`, `related_filing_id`, `description`.

Rules:

- A subject may reference a normalized disclosure asset, a related filing, or only the raw subject name when resolution is incomplete.
- Do not create a placeholder asset solely to satisfy a waiver or administrative-document relationship.

### 14.15 `filing_relationships`

Connects related filings, including amendment to original filing, replacement to previous filing, waiver to affected report, extension to the report whose deadline changed, and supporting document to primary disclosure.

Fields: `filing_relationship_id`, `from_filing_id`, `to_filing_id`, `relationship_type`, `match_method`, `match_confidence`, `is_confirmed`, `reviewed_at`, `reviewed_by`, `notes`.

Representative `relationship_type` values: `amends`, `supersedes`, `corrects`, `waives_requirement_for`, `extends_deadline_for`, `supports`, and `replaces`.

Rules:

- Both filing references must point to different filings.
- Inferred relationships retain their method and confidence until confirmed.
- A filing-level amendment relationship does not replace `trades.supersedes_trade_id`, which records a confirmed transaction-level correction.

## 15. Optional blind-trust expansion

The following tables should be introduced when blind-trust extraction begins. Until then, blind-trust documents remain available through `filings`, `documents`, `filing_actions`, and their extraction records.

### 15.1 `disclosure_trusts`

Stores the trust name, type, establishment date, approval date, termination date, status, and whether it is a qualified blind trust.

Fields: `disclosure_trust_id`, `filing_id`, `member_id`, `trust_name`, `trust_type`, `established_date`, `approved_date`, `terminated_date`, `is_qualified_blind_trust`, `status`, `description`, `source_page_number`.

Relationships:

- A member may have multiple trusts over time.
- A trust record retains the filing that establishes or reports it.
- Later amendments and approvals are connected through `filing_relationships` and `filing_actions` rather than overwriting the original evidence.

### 15.2 `disclosure_trust_parties`

Stores trustors, trustees, successor trustees, beneficiaries, service dates, and approval dates.

Fields: `disclosure_trust_party_id`, `disclosure_trust_id`, `party_name`, `party_role`, `start_date`, `end_date`, `approval_date`, `description`.

Representative roles: `trustor`, `trustee`, `successor_trustee`, and `beneficiary`.

## 16. Changes to existing tables for full disclosures

### 16.1 `trades`

The existing table remains the single normalized transaction table. It must support transactions extracted from annual, candidate, new-filer, and termination reports as well as PTRs.

Additional fields: `disclosure_report_id`, `disclosure_asset_id`, `schedule_raw`, `is_partial_sale`, `is_annual_report_transaction`, `transaction_sequence`, `source_transaction_id_raw`.

Rules:

- Do not create a separate annual-transactions table.
- `disclosure_report_id` is null for PTRs that do not have a corresponding full disclosure report.
- `disclosure_asset_id` is optional because an asset may not yet have been resolved or may appear only in a transaction schedule.
- `source_transaction_id_raw` preserves the House transaction identifier printed on amended PTR rows.
- Source filing type determines the reporting context; `is_annual_report_transaction` is a practical query flag and must agree with that context.

### 16.2 Generalized extracted-field evidence

The current `trade_evidence` table is specific to transactions. When extraction expands beyond PTRs, introduce `extracted_field_evidence` and migrate or supplement transaction evidence so assets, liabilities, gifts, waivers, positions, and other values can retain page-level support.

Fields: `extracted_field_evidence_id`, `document_id`, `document_extraction_id`, `entity_type`, `entity_id`, `field_name`, `page_number`, `source_text`, `bounding_box`, `image_path`, `confidence`.

Rules:

- `entity_type` and `entity_id` identify the normalized record supported by the evidence.
- The document and extraction must agree with the supported record's filing chain.
- This remains deferred until field-level review, highlighting, or OCR validation is required.

### 16.3 `documents`

Add `detected_form_version` and `document_completeness_status` when form-version detection begins. These fields distinguish older scanned paper forms from electronically generated reports and identify documents that appear truncated, incomplete, or composed only of selected schedules.

### 16.4 Filing-type processing priorities

| Priority | Filing codes | Treatment |
|---|---|---|
| Highest | P | Extract timely transaction activity into `trades`. |
| High | C, H, O, T | Parse complete financial profiles, including assets, income, liabilities, positions, and any transactions. |
| High when related filing is processed | A | Preserve corrections and link them to the original filing and corrected records. |
| Important explanatory filing | R | Record assets exempted from PTR reporting so missing transaction reports are not treated as ingestion failures. |
| Important trust filing | B | Preserve trust identity, parties, approvals, and amendments. |
| Administrative completeness | D, E, G, W, X | Record candidate status, exemptions, gift waivers, withdrawals, extensions, decisions, and deadlines. |

## 17. Expanded relationship overview

```text
member
  `--< filing
       |--0..1 disclosure_report
       |       |--< disclosure_questions
       |       |--< disclosure_assets --< disclosure_asset_income
       |       |--< disclosure_earned_income
       |       |--< disclosure_liabilities
       |       |--< disclosure_gifts
       |       |--< disclosure_travel
       |       |--< disclosure_positions
       |       |--< disclosure_agreements
       |       |--< disclosure_compensation_sources
       |       `--< disclosure_charitable_payments
       |--< trades >-- securities
       |--< filing_actions --< filing_action_subjects
       |--< filing_relationships >-- filings
       |--< documents --< document_extractions
       `--< disclosure_trusts --< disclosure_trust_parties
```

Initial implementation expands the schema from 39 to 54 tables. Adding the two optional trust tables increases it to 56. The additional tables represent distinct financial and administrative facts rather than additional job-processing infrastructure.
