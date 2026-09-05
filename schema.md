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
        `--< filings --< documents --< document_jobs
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

### 8.2 `member_match_candidates`

Candidate members evaluated while resolving a filing's raw filer identity.

Fields: `member_match_candidate_id`, `filing_id`, `candidate_member_id`, `candidate_rank`, `match_score`, `name_score`, `office_score`, `term_score`, `match_reasons`, `decision`, `reviewed_at`, `reviewed_by`.

### 8.3 `selection_batches`

A user-created or scheduled request selecting filings by member, state, year, chamber, filing type, or processing status.

Fields: `selection_batch_id`, `batch_name`, `requested_by`, `filter_definition`, `created_at`, `started_at`, `finished_at`, `status`, `filings_selected`, `filings_completed`, `notes`.

### 8.4 `filing_selections`

Connects individual filings to selection batches without causing duplicate downloads.

Fields: `filing_selection_id`, `filing_id`, `selection_batch_id`, `selection_reason`, `priority`, `selected_at`, `selected_by`, `is_active`.

## 9. Documents and processing

### 9.1 `documents`

Downloaded or locally supplied filing documents and integrity metadata.

Fields: `document_id`, `filing_id`, `document_type`, `source_url`, `local_path`, `mime_type`, `file_size_bytes`, `content_hash`, `downloaded_at`, `http_status`, `is_primary`, `page_count`, `has_embedded_text`, `requires_ocr`, `verification_status`, `source_snapshot_id`.

### 9.2 `document_jobs`

Resumable download, verification, extraction, OCR, parsing, ticker resolution, validation, and review work.

Fields: `document_job_id`, `filing_id`, `document_id`, `job_type`, `status`, `priority`, `attempt_count`, `max_attempts`, `queued_at`, `started_at`, `finished_at`, `next_attempt_at`, `worker_name`, `software_version`, `error_type`, `error_message`.

### 9.3 `document_extractions`

Versioned outputs and quality measurements from extraction and parsing attempts.

Fields: `document_extraction_id`, `document_id`, `document_job_id`, `extraction_type`, `extractor_name`, `extractor_version`, `output_path`, `output_hash`, `started_at`, `finished_at`, `quality_score`, `characters_extracted`, `pages_processed`, `warnings`, `is_preferred`.

## 10. Securities and trades

### 10.1 `securities`

Normalized financial instruments and issuers.

Fields: `security_id`, `security_type`, `issuer_name`, `security_name`, `primary_exchange`, `currency_code`, `is_publicly_traded`, `active_from`, `active_to`, `source_snapshot_id`.

### 10.2 `security_identifiers`

Time-valid tickers, CUSIPs, FIGIs, ISINs, CIKs, and other security identifiers.

Fields: `security_identifier_id`, `security_id`, `identifier_type`, `identifier_value`, `exchange_code`, `valid_from`, `valid_to`, `is_primary`, `source_snapshot_id`.

### 10.3 `trades`

One normalized PTR transaction line, retaining the reported values, inferred security information, parser provenance, and amendment relationship.

Fields: `trade_id`, `filing_id`, `document_id`, `document_extraction_id`, `source_row_number`, `transaction_date`, `notification_date`, `filed_date`, `owner_type`, `owner_raw`, `transaction_type`, `transaction_type_raw`, `asset_name_raw`, `asset_type_code_raw`, `asset_type`, `security_id`, `ticker_reported`, `ticker_inferred`, `ticker_inference_method`, `ticker_confidence`, `amount_range_raw`, `amount_min`, `amount_max`, `amount_exact`, `capital_gains_over_200`, `description_raw`, `is_amended`, `supersedes_trade_id`, `parser_name`, `parser_version`, `parse_confidence`, `review_status`, `created_at`, `updated_at`.

Relationship: member identity is obtained through `trades.filing_id -> filings.member_id`; it is not duplicated on the trade.

### 10.4 `trade_evidence`

Page text, locations, images, and confidence supporting individual extracted fields.

Fields: `trade_evidence_id`, `trade_id`, `document_id`, `document_extraction_id`, `field_name`, `page_number`, `source_text`, `bounding_box`, `image_path`, `confidence`.

### 10.5 `market_prices`

Daily security prices used to measure performance from transaction and disclosure dates.

Fields: `market_price_id`, `security_id`, `price_date`, `open_price`, `high_price`, `low_price`, `close_price`, `adjusted_close_price`, `volume`, `currency_code`, `price_source`, `retrieved_at`, `source_snapshot_id`.

### 10.6 `corporate_actions`

Splits, mergers, acquisitions, symbol changes, spinoffs, and other events affecting historical comparisons.

Fields: `corporate_action_id`, `security_id`, `action_type`, `effective_date`, `ratio_or_terms`, `related_security_id`, `description`, `source_snapshot_id`.

## 11. Staging tables

Staging tables preserve source rows, validation outcomes, and links to accepted normalized records.

### 11.1 `staging_members`

Fields: `staging_member_id`, `source_import_id`, `source_row_number`, `raw_record`, `raw_full_name`, `normalized_name`, `raw_identifiers`, `chamber`, `state_code`, `district_number`, `party_raw`, `term_start_date`, `term_end_date`, `validation_status`, `error_details`, `member_id`.

### 11.2 `staging_committees`

Fields: `staging_committee_id`, `source_import_id`, `source_row_number`, `raw_record`, `committee_code_raw`, `name_raw`, `chamber_raw`, `committee_type_raw`, `parent_code_raw`, `congress_start`, `congress_end`, `validation_status`, `error_details`, `committee_id`.

### 11.3 `staging_committee_memberships`

Fields: `staging_committee_membership_id`, `source_import_id`, `source_row_number`, `raw_record`, `bioguide_id_raw`, `member_name_raw`, `committee_code_raw`, `congress_number`, `party_side_raw`, `rank_raw`, `title_raw`, `start_date`, `end_date`, `validation_status`, `error_details`, `committee_membership_id`.

### 11.4 `staging_house_filings`

Fields: `staging_house_filing_id`, `source_import_id`, `source_row_number`, `doc_id_raw`, `reporting_year_raw`, `filing_type_code_raw`, `first_name_raw`, `last_name_raw`, `office_raw`, `filed_date_raw`, `document_url_raw`, `raw_xml`, `validation_status`, `error_details`, `filing_id`.

### 11.5 `staging_senate_filings`

Fields: `staging_senate_filing_id`, `source_import_id`, `source_row_number`, `source_filing_id_raw`, `filing_url_raw`, `filer_name_raw`, `office_raw`, `filing_type_raw`, `filed_date_raw`, `report_period_start_raw`, `report_period_end_raw`, `raw_record`, `validation_status`, `error_details`, `filing_id`.

### 11.6 `staging_house_trades`

Fields: `staging_house_trade_id`, `source_import_id`, `filing_id`, `document_extraction_id`, `source_row_number`, `transaction_date_raw`, `notification_date_raw`, `owner_raw`, `asset_name_raw`, `asset_type_code_raw`, `transaction_type_raw`, `amount_raw`, `ticker_raw`, `description_raw`, `raw_record`, `parse_warnings`, `validation_status`, `error_details`, `trade_id`.

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

House and Senate financial-disclosure sources populate filings, documents, trades, and their staging tables in later ingestion stages.

## 13. Initial validation subjects

- Nancy Pelosi is the primary House end-to-end test member.
- Mike Crapo is a Senate test member with committee, campaign-finance, lobbying, and PTR cross-source use cases.
- Small-state tests such as New Hampshire, North Dakota, and South Dakota validate state filtering and sparse historical selections.
