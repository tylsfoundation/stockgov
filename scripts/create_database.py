"""Create the StockGov PostgreSQL database and current infrastructure.

This script is intentionally self-contained: the database bootstrap logic and the
complete PostgreSQL schema live in this file. It is safe to run more than
once because database objects are created only when they do not already exist.

Configuration is read from DATABASE_URL when available.  Otherwise the script
uses POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, and
POSTGRES_PASSWORD.  Values match the project's .env.example defaults.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg2
from psycopg2 import sql


SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS source_snapshots (
    source_snapshot_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_url TEXT,
    local_path TEXT,
    coverage_start_date DATE,
    coverage_end_date DATE,
    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    content_hash TEXT,
    file_size_bytes BIGINT CHECK (file_size_bytes IS NULL OR file_size_bytes >= 0),
    format_version TEXT,
    notes TEXT,
    CONSTRAINT source_snapshots_hash_unique UNIQUE (source_name, content_hash),
    CONSTRAINT source_snapshots_coverage_valid CHECK (
        coverage_end_date IS NULL OR coverage_start_date IS NULL
        OR coverage_end_date >= coverage_start_date
    )
);

CREATE TABLE IF NOT EXISTS source_imports (
    source_import_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_snapshot_id BIGINT NOT NULL REFERENCES source_snapshots(source_snapshot_id),
    import_type TEXT NOT NULL,
    importer_version TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'running', 'complete', 'partially_complete', 'failed')
    ),
    records_read BIGINT NOT NULL DEFAULT 0 CHECK (records_read >= 0),
    records_inserted BIGINT NOT NULL DEFAULT 0 CHECK (records_inserted >= 0),
    records_updated BIGINT NOT NULL DEFAULT 0 CHECK (records_updated >= 0),
    records_rejected BIGINT NOT NULL DEFAULT 0 CHECK (records_rejected >= 0),
    error_summary TEXT,
    CONSTRAINT source_imports_times_valid CHECK (
        finished_at IS NULL OR finished_at >= started_at
    )
);

CREATE TABLE IF NOT EXISTS members (
    member_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    preferred_name TEXT NOT NULL,
    first_name TEXT NOT NULL,
    middle_name TEXT,
    last_name TEXT NOT NULL,
    suffix TEXT,
    nickname TEXT,
    date_of_birth DATE,
    gender TEXT,
    is_living BOOLEAN,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source_snapshot_id BIGINT REFERENCES source_snapshots(source_snapshot_id)
);

CREATE TABLE IF NOT EXISTS member_names (
    member_name_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    member_id BIGINT NOT NULL REFERENCES members(member_id) ON DELETE CASCADE,
    name_type TEXT NOT NULL CHECK (
        name_type IN ('preferred', 'official', 'former', 'nickname', 'source', 'alias')
    ),
    full_name TEXT NOT NULL,
    first_name TEXT,
    middle_name TEXT,
    last_name TEXT,
    suffix TEXT,
    normalized_name TEXT NOT NULL,
    valid_from DATE,
    valid_to DATE,
    source_snapshot_id BIGINT REFERENCES source_snapshots(source_snapshot_id),
    CONSTRAINT member_names_valid_dates CHECK (
        valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from
    ),
    CONSTRAINT member_names_identity_unique UNIQUE (
        member_id, name_type, normalized_name, valid_from
    )
);

CREATE TABLE IF NOT EXISTS member_identifiers (
    member_identifier_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    member_id BIGINT NOT NULL REFERENCES members(member_id) ON DELETE CASCADE,
    identifier_type TEXT NOT NULL,
    identifier_value TEXT NOT NULL,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    valid_from DATE,
    valid_to DATE,
    source_snapshot_id BIGINT REFERENCES source_snapshots(source_snapshot_id),
    CONSTRAINT member_identifiers_external_unique UNIQUE (
        identifier_type, identifier_value
    ),
    CONSTRAINT member_identifiers_valid_dates CHECK (
        valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from
    )
);

CREATE TABLE IF NOT EXISTS member_terms (
    member_term_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    member_id BIGINT NOT NULL REFERENCES members(member_id) ON DELETE CASCADE,
    chamber TEXT NOT NULL CHECK (chamber IN ('house', 'senate')),
    term_start_date DATE NOT NULL,
    term_end_date DATE NOT NULL,
    congress_start SMALLINT,
    congress_end SMALLINT,
    state_code CHAR(2) NOT NULL,
    district_number SMALLINT CHECK (district_number IS NULL OR district_number >= 0),
    senate_class SMALLINT CHECK (senate_class IS NULL OR senate_class BETWEEN 1 AND 3),
    senate_state_rank TEXT CHECK (
        senate_state_rank IS NULL OR senate_state_rank IN ('junior', 'senior')
    ),
    party_code TEXT,
    party_name_raw TEXT,
    caucus_party_code TEXT,
    caucus_party_name_raw TEXT,
    term_type TEXT NOT NULL DEFAULT 'regular',
    term_end_type TEXT,
    official_website_url TEXT,
    contact_form_url TEXT,
    rss_url TEXT,
    source_snapshot_id BIGINT REFERENCES source_snapshots(source_snapshot_id),
    CONSTRAINT member_terms_dates_valid CHECK (term_end_date >= term_start_date),
    CONSTRAINT member_terms_congress_valid CHECK (
        congress_end IS NULL OR congress_start IS NULL OR congress_end >= congress_start
    ),
    CONSTRAINT member_terms_chamber_fields_valid CHECK (
        (chamber = 'house' AND senate_class IS NULL AND senate_state_rank IS NULL)
        OR (chamber = 'senate' AND district_number IS NULL)
    ),
    CONSTRAINT member_terms_natural_unique UNIQUE (
        member_id, chamber, term_start_date, state_code
    )
);

CREATE TABLE IF NOT EXISTS member_term_party_affiliations (
    member_term_party_affiliation_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    member_term_id BIGINT NOT NULL REFERENCES member_terms(member_term_id) ON DELETE CASCADE,
    party_code TEXT,
    party_name_raw TEXT,
    caucus_party_code TEXT,
    caucus_party_name_raw TEXT,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    source_snapshot_id BIGINT REFERENCES source_snapshots(source_snapshot_id),
    CONSTRAINT member_term_party_affiliations_dates_valid CHECK (end_date >= start_date),
    CONSTRAINT member_term_party_affiliations_unique UNIQUE (member_term_id, start_date)
);

CREATE TABLE IF NOT EXISTS member_family_relationships (
    member_family_relationship_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    member_id BIGINT NOT NULL REFERENCES members(member_id) ON DELETE CASCADE,
    related_member_id BIGINT REFERENCES members(member_id) ON DELETE SET NULL,
    relative_name TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    normalized_relative_name TEXT NOT NULL,
    valid_from DATE,
    valid_to DATE,
    source_snapshot_id BIGINT REFERENCES source_snapshots(source_snapshot_id),
    CONSTRAINT member_family_relationships_dates_valid CHECK (
        valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from
    ),
    CONSTRAINT member_family_relationships_unique UNIQUE (
        member_id, normalized_relative_name, relationship_type
    )
);

CREATE TABLE IF NOT EXISTS leadership_roles (
    leadership_role_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    member_id BIGINT NOT NULL REFERENCES members(member_id) ON DELETE CASCADE,
    chamber TEXT CHECK (chamber IS NULL OR chamber IN ('house', 'senate', 'joint', 'party')),
    role_title TEXT NOT NULL,
    party_code TEXT,
    start_date DATE,
    end_date DATE,
    congress_start SMALLINT,
    congress_end SMALLINT,
    source_snapshot_id BIGINT REFERENCES source_snapshots(source_snapshot_id),
    CONSTRAINT leadership_roles_dates_valid CHECK (
        end_date IS NULL OR start_date IS NULL OR end_date >= start_date
    ),
    CONSTRAINT leadership_roles_congress_valid CHECK (
        congress_end IS NULL OR congress_start IS NULL OR congress_end >= congress_start
    )
);

CREATE TABLE IF NOT EXISTS member_offices (
    member_office_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    member_id BIGINT NOT NULL REFERENCES members(member_id) ON DELETE CASCADE,
    office_type TEXT NOT NULL,
    source_office_id TEXT,
    building TEXT,
    room TEXT,
    suite TEXT,
    address_line_1 TEXT,
    address_line_2 TEXT,
    city TEXT,
    state_code CHAR(2),
    postal_code TEXT,
    phone TEXT,
    fax TEXT,
    hours_text TEXT,
    latitude NUMERIC(9, 6),
    longitude NUMERIC(9, 6),
    valid_from DATE,
    valid_to DATE,
    source_snapshot_id BIGINT REFERENCES source_snapshots(source_snapshot_id),
    CONSTRAINT member_offices_dates_valid CHECK (
        valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from
    )
);

CREATE TABLE IF NOT EXISTS member_social_accounts (
    member_social_account_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    member_id BIGINT NOT NULL REFERENCES members(member_id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    account_name TEXT,
    platform_account_id TEXT,
    account_url TEXT NOT NULL,
    is_official BOOLEAN NOT NULL DEFAULT FALSE,
    valid_from DATE,
    valid_to DATE,
    source_snapshot_id BIGINT REFERENCES source_snapshots(source_snapshot_id),
    CONSTRAINT member_social_accounts_dates_valid CHECK (
        valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from
    ),
    CONSTRAINT member_social_accounts_unique UNIQUE (platform, account_url)
);

CREATE TABLE IF NOT EXISTS committees (
    committee_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    committee_code TEXT NOT NULL,
    chamber TEXT NOT NULL CHECK (chamber IN ('house', 'senate', 'joint')),
    committee_type TEXT NOT NULL CHECK (
        committee_type IN ('standing', 'select', 'special', 'joint', 'subcommittee', 'other')
    ),
    name TEXT NOT NULL,
    name_raw TEXT,
    parent_committee_id BIGINT REFERENCES committees(committee_id),
    is_current BOOLEAN NOT NULL DEFAULT TRUE,
    website_url TEXT,
    minority_website_url TEXT,
    jurisdiction_text TEXT,
    jurisdiction_source_url TEXT,
    address TEXT,
    phone TEXT,
    rss_url TEXT,
    minority_rss_url TEXT,
    youtube_channel_id TEXT,
    wikipedia_name TEXT,
    source_snapshot_id BIGINT REFERENCES source_snapshots(source_snapshot_id),
    CONSTRAINT committees_code_unique UNIQUE (committee_code),
    CONSTRAINT committees_not_own_parent CHECK (
        parent_committee_id IS NULL OR parent_committee_id <> committee_id
    )
);

CREATE TABLE IF NOT EXISTS committee_identifiers (
    committee_identifier_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    committee_id BIGINT NOT NULL REFERENCES committees(committee_id) ON DELETE CASCADE,
    identifier_type TEXT NOT NULL,
    identifier_value TEXT NOT NULL,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    source_snapshot_id BIGINT REFERENCES source_snapshots(source_snapshot_id),
    CONSTRAINT committee_identifiers_external_unique UNIQUE (
        identifier_type, identifier_value
    ),
    CONSTRAINT committee_identifiers_member_unique UNIQUE (
        committee_id, identifier_type, identifier_value
    )
);

CREATE TABLE IF NOT EXISTS committee_congresses (
    committee_congress_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    committee_id BIGINT NOT NULL REFERENCES committees(committee_id) ON DELETE CASCADE,
    congress_number SMALLINT NOT NULL CHECK (congress_number > 0),
    name TEXT NOT NULL,
    committee_code TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    source_snapshot_id BIGINT REFERENCES source_snapshots(source_snapshot_id),
    CONSTRAINT committee_congresses_unique UNIQUE (committee_id, congress_number)
);

CREATE TABLE IF NOT EXISTS committee_memberships (
    committee_membership_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    committee_id BIGINT NOT NULL REFERENCES committees(committee_id),
    member_id BIGINT NOT NULL REFERENCES members(member_id),
    congress_number SMALLINT NOT NULL CHECK (congress_number > 0),
    member_chamber TEXT CHECK (
        member_chamber IS NULL OR member_chamber IN ('house', 'senate')
    ),
    start_date DATE,
    end_date DATE,
    party_side TEXT CHECK (
        party_side IS NULL OR party_side IN ('majority', 'minority', 'independent', 'other')
    ),
    rank SMALLINT CHECK (rank IS NULL OR rank > 0),
    title TEXT,
    is_ex_officio BOOLEAN NOT NULL DEFAULT FALSE,
    source_snapshot_id BIGINT REFERENCES source_snapshots(source_snapshot_id),
    CONSTRAINT committee_memberships_dates_valid CHECK (
        end_date IS NULL OR start_date IS NULL OR end_date >= start_date
    ),
    CONSTRAINT committee_memberships_unique UNIQUE (
        committee_id, member_id, congress_number, start_date
    )
);

CREATE TABLE IF NOT EXISTS executives (
    executive_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    full_name TEXT NOT NULL,
    first_name TEXT NOT NULL,
    middle_name TEXT,
    last_name TEXT NOT NULL,
    suffix TEXT,
    nickname TEXT,
    official_full_name TEXT,
    date_of_birth DATE,
    gender TEXT,
    bioguide_id TEXT,
    source_snapshot_id BIGINT REFERENCES source_snapshots(source_snapshot_id),
    CONSTRAINT executives_bioguide_unique UNIQUE (bioguide_id)
);

CREATE TABLE IF NOT EXISTS executive_identifiers (
    executive_identifier_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    executive_id BIGINT NOT NULL REFERENCES executives(executive_id) ON DELETE CASCADE,
    identifier_type TEXT NOT NULL,
    identifier_value TEXT NOT NULL,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    source_snapshot_id BIGINT REFERENCES source_snapshots(source_snapshot_id),
    CONSTRAINT executive_identifiers_external_unique UNIQUE (
        identifier_type, identifier_value
    )
);

CREATE TABLE IF NOT EXISTS executive_terms (
    executive_term_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    executive_id BIGINT NOT NULL REFERENCES executives(executive_id) ON DELETE CASCADE,
    office TEXT NOT NULL,
    term_start_date DATE NOT NULL,
    term_end_date DATE NOT NULL,
    party_code TEXT,
    accession_method TEXT,
    term_number SMALLINT CHECK (term_number IS NULL OR term_number > 0),
    source_snapshot_id BIGINT REFERENCES source_snapshots(source_snapshot_id),
    CONSTRAINT executive_terms_dates_valid CHECK (term_end_date >= term_start_date),
    CONSTRAINT executive_terms_unique UNIQUE (executive_id, office, term_start_date)
);

CREATE TABLE IF NOT EXISTS filings (
    filing_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    source_filing_id TEXT NOT NULL,
    chamber TEXT NOT NULL CHECK (chamber IN ('house', 'senate')),
    filing_type_code_raw TEXT,
    filing_type TEXT NOT NULL CHECK (
        filing_type IN (
            'ptr', 'annual_disclosure', 'amendment', 'extension',
            'termination', 'candidate_report', 'new_filer', 'other', 'unknown'
        )
    ),
    reporting_year SMALLINT CHECK (reporting_year IS NULL OR reporting_year >= 1900),
    filed_date DATE,
    report_period_start DATE,
    report_period_end DATE,
    member_id BIGINT REFERENCES members(member_id),
    raw_first_name TEXT,
    raw_last_name TEXT,
    raw_full_name TEXT,
    raw_office TEXT,
    state_code_guess CHAR(2),
    district_guess SMALLINT CHECK (district_guess IS NULL OR district_guess >= 0),
    member_match_status TEXT NOT NULL DEFAULT 'unmatched' CHECK (
        member_match_status IN (
            'unmatched', 'automatically_matched', 'manually_matched',
            'ambiguous', 'rejected'
        )
    ),
    member_match_method TEXT,
    member_match_confidence NUMERIC(5,4) CHECK (
        member_match_confidence IS NULL
        OR member_match_confidence BETWEEN 0 AND 1
    ),
    source_url TEXT,
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processing_status TEXT NOT NULL DEFAULT 'discovered' CHECK (
        processing_status IN (
            'discovered', 'selected', 'queued', 'downloaded', 'verified',
            'text_extracted', 'needs_ocr', 'parsed', 'needs_review',
            'failed_retryable', 'failed_permanent', 'complete'
        )
    ),
    source_snapshot_id BIGINT REFERENCES source_snapshots(source_snapshot_id),
    source_import_id BIGINT REFERENCES source_imports(source_import_id),
    CONSTRAINT filings_source_identity_unique UNIQUE (source, source_filing_id),
    CONSTRAINT filings_report_period_valid CHECK (
        report_period_end IS NULL OR report_period_start IS NULL
        OR report_period_end >= report_period_start
    ),
    CONSTRAINT filings_member_match_valid CHECK (
        member_id IS NOT NULL
        OR member_match_status IN ('unmatched', 'ambiguous', 'rejected')
    )
);

CREATE TABLE IF NOT EXISTS member_match_candidates (
    member_match_candidate_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    filing_id BIGINT NOT NULL REFERENCES filings(filing_id) ON DELETE CASCADE,
    candidate_member_id BIGINT NOT NULL REFERENCES members(member_id),
    candidate_rank SMALLINT CHECK (candidate_rank IS NULL OR candidate_rank > 0),
    match_score NUMERIC(5,4) CHECK (match_score IS NULL OR match_score BETWEEN 0 AND 1),
    name_score NUMERIC(5,4) CHECK (name_score IS NULL OR name_score BETWEEN 0 AND 1),
    office_score NUMERIC(5,4) CHECK (office_score IS NULL OR office_score BETWEEN 0 AND 1),
    term_score NUMERIC(5,4) CHECK (term_score IS NULL OR term_score BETWEEN 0 AND 1),
    match_reasons JSONB NOT NULL DEFAULT '{}'::jsonb,
    decision TEXT NOT NULL DEFAULT 'pending' CHECK (
        decision IN ('pending', 'accepted', 'rejected')
    ),
    reviewed_at TIMESTAMPTZ,
    reviewed_by TEXT,
    CONSTRAINT member_match_candidates_unique UNIQUE (filing_id, candidate_member_id)
);

CREATE TABLE IF NOT EXISTS selection_batches (
    selection_batch_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_name TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    filter_definition JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (
        status IN (
            'draft', 'queued', 'running', 'complete', 'partially_complete',
            'canceled', 'failed'
        )
    ),
    filings_selected BIGINT NOT NULL DEFAULT 0 CHECK (filings_selected >= 0),
    filings_completed BIGINT NOT NULL DEFAULT 0 CHECK (filings_completed >= 0),
    notes TEXT,
    CONSTRAINT selection_batches_times_valid CHECK (
        (started_at IS NULL OR started_at >= created_at)
        AND (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at)
    ),
    CONSTRAINT selection_batches_counts_valid CHECK (
        filings_completed <= filings_selected
    )
);

CREATE TABLE IF NOT EXISTS filing_selections (
    filing_selection_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    filing_id BIGINT NOT NULL REFERENCES filings(filing_id) ON DELETE CASCADE,
    selection_batch_id BIGINT NOT NULL REFERENCES selection_batches(selection_batch_id) ON DELETE CASCADE,
    selection_reason TEXT NOT NULL,
    priority SMALLINT NOT NULL DEFAULT 100 CHECK (priority >= 0),
    selected_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    selected_by TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT filing_selections_unique UNIQUE (filing_id, selection_batch_id)
);

CREATE TABLE IF NOT EXISTS documents (
    document_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    filing_id BIGINT NOT NULL REFERENCES filings(filing_id) ON DELETE CASCADE,
    document_type TEXT NOT NULL,
    source_url TEXT,
    local_path TEXT,
    mime_type TEXT,
    file_size_bytes BIGINT CHECK (file_size_bytes IS NULL OR file_size_bytes >= 0),
    content_hash TEXT,
    downloaded_at TIMESTAMPTZ,
    http_status SMALLINT CHECK (http_status IS NULL OR http_status BETWEEN 100 AND 599),
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    page_count INTEGER CHECK (page_count IS NULL OR page_count >= 0),
    has_embedded_text BOOLEAN,
    requires_ocr BOOLEAN,
    verification_status TEXT NOT NULL DEFAULT 'unverified' CHECK (
        verification_status IN (
            'unverified', 'verified', 'corrupt', 'wrong_document', 'unavailable'
        )
    ),
    source_snapshot_id BIGINT REFERENCES source_snapshots(source_snapshot_id),
    CONSTRAINT documents_filing_hash_unique UNIQUE (filing_id, content_hash)
);

CREATE TABLE IF NOT EXISTS document_jobs (
    document_job_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    filing_id BIGINT NOT NULL REFERENCES filings(filing_id) ON DELETE CASCADE,
    document_id BIGINT REFERENCES documents(document_id) ON DELETE CASCADE,
    job_type TEXT NOT NULL CHECK (
        job_type IN (
            'download', 'verify', 'extract_text', 'ocr', 'parse',
            'validate', 'resolve_ticker', 'review'
        )
    ),
    status TEXT NOT NULL DEFAULT 'queued' CHECK (
        status IN (
            'queued', 'running', 'failed_retryable', 'failed_permanent',
            'needs_review', 'complete', 'canceled'
        )
    ),
    priority SMALLINT NOT NULL DEFAULT 100 CHECK (priority >= 0),
    attempt_count SMALLINT NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts SMALLINT NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
    queued_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    next_attempt_at TIMESTAMPTZ,
    worker_name TEXT,
    software_version TEXT,
    error_type TEXT,
    error_message TEXT,
    CONSTRAINT document_jobs_attempts_valid CHECK (attempt_count <= max_attempts),
    CONSTRAINT document_jobs_times_valid CHECK (
        (started_at IS NULL OR started_at >= queued_at)
        AND (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at)
    )
);

CREATE TABLE IF NOT EXISTS document_extractions (
    document_extraction_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    document_job_id BIGINT REFERENCES document_jobs(document_job_id) ON DELETE SET NULL,
    extraction_type TEXT NOT NULL CHECK (
        extraction_type IN ('embedded_text', 'ocr', 'table', 'structured_parser')
    ),
    extractor_name TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    output_path TEXT,
    output_hash TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    quality_score NUMERIC(5,4) CHECK (
        quality_score IS NULL OR quality_score BETWEEN 0 AND 1
    ),
    characters_extracted BIGINT CHECK (
        characters_extracted IS NULL OR characters_extracted >= 0
    ),
    pages_processed INTEGER CHECK (pages_processed IS NULL OR pages_processed >= 0),
    warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_preferred BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT document_extractions_times_valid CHECK (
        finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at
    ),
    CONSTRAINT document_extractions_version_unique UNIQUE (
        document_id, extraction_type, extractor_name, extractor_version, output_hash
    )
);

CREATE TABLE IF NOT EXISTS securities (
    security_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    security_type TEXT NOT NULL,
    issuer_name TEXT,
    security_name TEXT NOT NULL,
    primary_exchange TEXT,
    currency_code CHAR(3),
    is_publicly_traded BOOLEAN,
    active_from DATE,
    active_to DATE,
    source_snapshot_id BIGINT REFERENCES source_snapshots(source_snapshot_id),
    CONSTRAINT securities_dates_valid CHECK (
        active_to IS NULL OR active_from IS NULL OR active_to >= active_from
    )
);

CREATE TABLE IF NOT EXISTS security_identifiers (
    security_identifier_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    security_id BIGINT NOT NULL REFERENCES securities(security_id) ON DELETE CASCADE,
    identifier_type TEXT NOT NULL,
    identifier_value TEXT NOT NULL,
    exchange_code TEXT NOT NULL DEFAULT '',
    valid_from DATE,
    valid_to DATE,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    source_snapshot_id BIGINT REFERENCES source_snapshots(source_snapshot_id),
    CONSTRAINT security_identifiers_dates_valid CHECK (
        valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from
    ),
    CONSTRAINT security_identifiers_unique UNIQUE (
        identifier_type, identifier_value, exchange_code, valid_from
    )
);

CREATE TABLE IF NOT EXISTS trades (
    trade_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    filing_id BIGINT NOT NULL REFERENCES filings(filing_id) ON DELETE CASCADE,
    document_id BIGINT REFERENCES documents(document_id) ON DELETE SET NULL,
    document_extraction_id BIGINT REFERENCES document_extractions(document_extraction_id) ON DELETE SET NULL,
    source_row_number INTEGER NOT NULL CHECK (source_row_number > 0),
    transaction_date DATE,
    notification_date DATE,
    filed_date DATE,
    owner_type TEXT,
    owner_raw TEXT,
    transaction_type TEXT,
    transaction_type_raw TEXT,
    asset_name_raw TEXT NOT NULL,
    asset_type_code_raw TEXT,
    asset_type TEXT,
    security_id BIGINT REFERENCES securities(security_id) ON DELETE SET NULL,
    ticker_reported TEXT,
    ticker_inferred TEXT,
    ticker_inference_method TEXT,
    ticker_confidence NUMERIC(5,4) CHECK (
        ticker_confidence IS NULL OR ticker_confidence BETWEEN 0 AND 1
    ),
    amount_range_raw TEXT,
    amount_min NUMERIC(20,2) CHECK (amount_min IS NULL OR amount_min >= 0),
    amount_max NUMERIC(20,2) CHECK (amount_max IS NULL OR amount_max >= 0),
    amount_exact NUMERIC(20,2) CHECK (amount_exact IS NULL OR amount_exact >= 0),
    capital_gains_over_200 BOOLEAN,
    description_raw TEXT,
    is_amended BOOLEAN NOT NULL DEFAULT FALSE,
    supersedes_trade_id BIGINT REFERENCES trades(trade_id) ON DELETE SET NULL,
    parser_name TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    parse_confidence NUMERIC(5,4) CHECK (
        parse_confidence IS NULL OR parse_confidence BETWEEN 0 AND 1
    ),
    review_status TEXT NOT NULL DEFAULT 'unreviewed' CHECK (
        review_status IN ('unreviewed', 'accepted', 'corrected', 'rejected', 'needs_review')
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT trades_amount_range_valid CHECK (
        amount_max IS NULL OR amount_min IS NULL OR amount_max >= amount_min
    ),
    CONSTRAINT trades_not_own_supersession CHECK (
        supersedes_trade_id IS NULL OR supersedes_trade_id <> trade_id
    ),
    CONSTRAINT trades_source_row_unique UNIQUE (
        filing_id, source_row_number, parser_version
    )
);

CREATE TABLE IF NOT EXISTS trade_evidence (
    trade_evidence_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trade_id BIGINT NOT NULL REFERENCES trades(trade_id) ON DELETE CASCADE,
    document_id BIGINT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    document_extraction_id BIGINT REFERENCES document_extractions(document_extraction_id) ON DELETE SET NULL,
    field_name TEXT NOT NULL,
    page_number INTEGER CHECK (page_number IS NULL OR page_number > 0),
    source_text TEXT,
    bounding_box JSONB,
    image_path TEXT,
    confidence NUMERIC(5,4) CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1)
);

CREATE TABLE IF NOT EXISTS market_prices (
    market_price_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    security_id BIGINT NOT NULL REFERENCES securities(security_id) ON DELETE CASCADE,
    price_date DATE NOT NULL,
    open_price NUMERIC(24,8),
    high_price NUMERIC(24,8),
    low_price NUMERIC(24,8),
    close_price NUMERIC(24,8),
    adjusted_close_price NUMERIC(24,8),
    volume NUMERIC(30,4),
    currency_code CHAR(3) NOT NULL,
    price_source TEXT NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source_snapshot_id BIGINT REFERENCES source_snapshots(source_snapshot_id),
    CONSTRAINT market_prices_unique UNIQUE (security_id, price_date, price_source),
    CONSTRAINT market_prices_high_low_valid CHECK (
        high_price IS NULL OR low_price IS NULL OR high_price >= low_price
    )
);

CREATE TABLE IF NOT EXISTS corporate_actions (
    corporate_action_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    security_id BIGINT NOT NULL REFERENCES securities(security_id) ON DELETE CASCADE,
    action_type TEXT NOT NULL,
    effective_date DATE NOT NULL,
    ratio_or_terms JSONB NOT NULL DEFAULT '{}'::jsonb,
    related_security_id BIGINT REFERENCES securities(security_id) ON DELETE SET NULL,
    description TEXT,
    source_snapshot_id BIGINT REFERENCES source_snapshots(source_snapshot_id),
    CONSTRAINT corporate_actions_not_self_related CHECK (
        related_security_id IS NULL OR related_security_id <> security_id
    )
);

CREATE TABLE IF NOT EXISTS staging_members (
    staging_member_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_import_id BIGINT NOT NULL REFERENCES source_imports(source_import_id) ON DELETE CASCADE,
    source_row_number INTEGER NOT NULL CHECK (source_row_number > 0),
    raw_record JSONB NOT NULL,
    raw_full_name TEXT,
    normalized_name TEXT,
    raw_identifiers JSONB NOT NULL DEFAULT '{}'::jsonb,
    chamber TEXT,
    state_code TEXT,
    district_number INTEGER,
    party_raw TEXT,
    term_start_date DATE,
    term_end_date DATE,
    validation_status TEXT NOT NULL DEFAULT 'pending' CHECK (
        validation_status IN ('pending', 'valid', 'invalid', 'loaded', 'rejected')
    ),
    error_details JSONB NOT NULL DEFAULT '[]'::jsonb,
    member_id BIGINT REFERENCES members(member_id) ON DELETE SET NULL,
    CONSTRAINT staging_members_source_row_unique UNIQUE (
        source_import_id, source_row_number
    )
);

CREATE TABLE IF NOT EXISTS staging_committees (
    staging_committee_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_import_id BIGINT NOT NULL REFERENCES source_imports(source_import_id) ON DELETE CASCADE,
    source_row_number INTEGER NOT NULL CHECK (source_row_number > 0),
    raw_record JSONB NOT NULL,
    committee_code_raw TEXT,
    name_raw TEXT,
    chamber_raw TEXT,
    committee_type_raw TEXT,
    parent_code_raw TEXT,
    congress_start SMALLINT,
    congress_end SMALLINT,
    validation_status TEXT NOT NULL DEFAULT 'pending' CHECK (
        validation_status IN ('pending', 'valid', 'invalid', 'loaded', 'rejected')
    ),
    error_details JSONB NOT NULL DEFAULT '[]'::jsonb,
    committee_id BIGINT REFERENCES committees(committee_id) ON DELETE SET NULL,
    CONSTRAINT staging_committees_source_row_unique UNIQUE (
        source_import_id, source_row_number
    )
);

CREATE TABLE IF NOT EXISTS staging_committee_memberships (
    staging_committee_membership_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_import_id BIGINT NOT NULL REFERENCES source_imports(source_import_id) ON DELETE CASCADE,
    source_row_number INTEGER NOT NULL CHECK (source_row_number > 0),
    raw_record JSONB NOT NULL,
    bioguide_id_raw TEXT,
    member_name_raw TEXT,
    committee_code_raw TEXT,
    congress_number SMALLINT,
    party_side_raw TEXT,
    rank_raw TEXT,
    title_raw TEXT,
    start_date DATE,
    end_date DATE,
    validation_status TEXT NOT NULL DEFAULT 'pending' CHECK (
        validation_status IN ('pending', 'valid', 'invalid', 'loaded', 'rejected')
    ),
    error_details JSONB NOT NULL DEFAULT '[]'::jsonb,
    committee_membership_id BIGINT REFERENCES committee_memberships(committee_membership_id) ON DELETE SET NULL,
    CONSTRAINT staging_committee_memberships_source_row_unique UNIQUE (
        source_import_id, source_row_number
    )
);

CREATE TABLE IF NOT EXISTS staging_house_filings (
    staging_house_filing_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_import_id BIGINT NOT NULL REFERENCES source_imports(source_import_id) ON DELETE CASCADE,
    source_row_number INTEGER NOT NULL CHECK (source_row_number > 0),
    doc_id_raw TEXT,
    reporting_year_raw TEXT,
    filing_type_code_raw TEXT,
    first_name_raw TEXT,
    last_name_raw TEXT,
    office_raw TEXT,
    filed_date_raw TEXT,
    document_url_raw TEXT,
    raw_xml TEXT NOT NULL,
    validation_status TEXT NOT NULL DEFAULT 'pending' CHECK (
        validation_status IN ('pending', 'valid', 'invalid', 'loaded', 'rejected')
    ),
    error_details JSONB NOT NULL DEFAULT '[]'::jsonb,
    filing_id BIGINT REFERENCES filings(filing_id) ON DELETE SET NULL,
    CONSTRAINT staging_house_filings_source_row_unique UNIQUE (
        source_import_id, source_row_number
    )
);

CREATE TABLE IF NOT EXISTS staging_senate_filings (
    staging_senate_filing_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_import_id BIGINT NOT NULL REFERENCES source_imports(source_import_id) ON DELETE CASCADE,
    source_row_number INTEGER NOT NULL CHECK (source_row_number > 0),
    source_filing_id_raw TEXT,
    filing_url_raw TEXT,
    filer_name_raw TEXT,
    office_raw TEXT,
    filing_type_raw TEXT,
    filed_date_raw TEXT,
    report_period_start_raw TEXT,
    report_period_end_raw TEXT,
    raw_record JSONB NOT NULL,
    validation_status TEXT NOT NULL DEFAULT 'pending' CHECK (
        validation_status IN ('pending', 'valid', 'invalid', 'loaded', 'rejected')
    ),
    error_details JSONB NOT NULL DEFAULT '[]'::jsonb,
    filing_id BIGINT REFERENCES filings(filing_id) ON DELETE SET NULL,
    CONSTRAINT staging_senate_filings_source_row_unique UNIQUE (
        source_import_id, source_row_number
    )
);

CREATE TABLE IF NOT EXISTS staging_house_trades (
    staging_house_trade_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_import_id BIGINT REFERENCES source_imports(source_import_id) ON DELETE CASCADE,
    filing_id BIGINT NOT NULL REFERENCES filings(filing_id) ON DELETE CASCADE,
    document_extraction_id BIGINT REFERENCES document_extractions(document_extraction_id) ON DELETE SET NULL,
    source_row_number INTEGER NOT NULL CHECK (source_row_number > 0),
    transaction_date_raw TEXT,
    notification_date_raw TEXT,
    owner_raw TEXT,
    asset_name_raw TEXT,
    asset_type_code_raw TEXT,
    transaction_type_raw TEXT,
    amount_raw TEXT,
    ticker_raw TEXT,
    description_raw TEXT,
    raw_record JSONB NOT NULL,
    parse_warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    validation_status TEXT NOT NULL DEFAULT 'pending' CHECK (
        validation_status IN ('pending', 'valid', 'invalid', 'loaded', 'rejected')
    ),
    error_details JSONB NOT NULL DEFAULT '[]'::jsonb,
    trade_id BIGINT REFERENCES trades(trade_id) ON DELETE SET NULL,
    CONSTRAINT staging_house_trades_source_row_unique UNIQUE (
        filing_id, source_row_number, document_extraction_id
    )
);

CREATE TABLE IF NOT EXISTS staging_senate_trades (
    staging_senate_trade_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_import_id BIGINT REFERENCES source_imports(source_import_id) ON DELETE CASCADE,
    filing_id BIGINT NOT NULL REFERENCES filings(filing_id) ON DELETE CASCADE,
    document_extraction_id BIGINT REFERENCES document_extractions(document_extraction_id) ON DELETE SET NULL,
    source_row_number INTEGER NOT NULL CHECK (source_row_number > 0),
    transaction_date_raw TEXT,
    notification_date_raw TEXT,
    owner_raw TEXT,
    asset_name_raw TEXT,
    asset_type_code_raw TEXT,
    transaction_type_raw TEXT,
    amount_raw TEXT,
    ticker_raw TEXT,
    description_raw TEXT,
    raw_record JSONB NOT NULL,
    parse_warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    validation_status TEXT NOT NULL DEFAULT 'pending' CHECK (
        validation_status IN ('pending', 'valid', 'invalid', 'loaded', 'rejected')
    ),
    error_details JSONB NOT NULL DEFAULT '[]'::jsonb,
    trade_id BIGINT REFERENCES trades(trade_id) ON DELETE SET NULL,
    CONSTRAINT staging_senate_trades_source_row_unique UNIQUE (
        filing_id, source_row_number, document_extraction_id
    )
);

CREATE INDEX IF NOT EXISTS idx_source_imports_snapshot
    ON source_imports (source_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_member_names_normalized
    ON member_names (normalized_name);
CREATE INDEX IF NOT EXISTS idx_member_identifiers_member
    ON member_identifiers (member_id);
CREATE INDEX IF NOT EXISTS idx_member_terms_member_dates
    ON member_terms (member_id, term_start_date, term_end_date);
CREATE INDEX IF NOT EXISTS idx_member_terms_state_chamber_dates
    ON member_terms (state_code, chamber, term_start_date, term_end_date);
CREATE INDEX IF NOT EXISTS idx_member_term_party_affiliations_term_dates
    ON member_term_party_affiliations (member_term_id, start_date, end_date);
CREATE INDEX IF NOT EXISTS idx_member_family_relationships_member
    ON member_family_relationships (member_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_member_offices_source_id
    ON member_offices (member_id, source_office_id)
    WHERE source_office_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_committee_parent
    ON committees (parent_committee_id);
CREATE INDEX IF NOT EXISTS idx_committee_identifiers_committee
    ON committee_identifiers (committee_id);
CREATE INDEX IF NOT EXISTS idx_committee_memberships_member_congress
    ON committee_memberships (member_id, congress_number);
CREATE INDEX IF NOT EXISTS idx_committee_memberships_committee_congress
    ON committee_memberships (committee_id, congress_number);
CREATE INDEX IF NOT EXISTS idx_executive_terms_dates
    ON executive_terms (term_start_date, term_end_date);
CREATE INDEX IF NOT EXISTS idx_executive_identifiers_executive
    ON executive_identifiers (executive_id);
CREATE INDEX IF NOT EXISTS idx_filings_member_date
    ON filings (member_id, filed_date);
CREATE INDEX IF NOT EXISTS idx_filings_catalog_filter
    ON filings (chamber, filing_type, reporting_year, processing_status);
CREATE INDEX IF NOT EXISTS idx_filings_raw_name
    ON filings (raw_last_name, raw_first_name);
CREATE INDEX IF NOT EXISTS idx_filings_unresolved
    ON filings (member_match_status, filed_date)
    WHERE member_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_member_match_candidates_filing_rank
    ON member_match_candidates (filing_id, candidate_rank);
CREATE INDEX IF NOT EXISTS idx_filing_selections_active_priority
    ON filing_selections (is_active, priority, selected_at);
CREATE INDEX IF NOT EXISTS idx_documents_filing
    ON documents (filing_id);
CREATE INDEX IF NOT EXISTS idx_documents_hash
    ON documents (content_hash);
CREATE INDEX IF NOT EXISTS idx_document_jobs_queue
    ON document_jobs (status, priority, next_attempt_at, queued_at);
CREATE INDEX IF NOT EXISTS idx_document_extractions_document
    ON document_extractions (document_id, is_preferred);
CREATE INDEX IF NOT EXISTS idx_security_identifiers_lookup
    ON security_identifiers (identifier_type, identifier_value, valid_from, valid_to);
CREATE INDEX IF NOT EXISTS idx_trades_filing
    ON trades (filing_id);
CREATE INDEX IF NOT EXISTS idx_trades_transaction_date
    ON trades (transaction_date);
CREATE INDEX IF NOT EXISTS idx_trades_security_date
    ON trades (security_id, transaction_date);
CREATE INDEX IF NOT EXISTS idx_trades_ticker_reported
    ON trades (ticker_reported);
CREATE INDEX IF NOT EXISTS idx_trades_ticker_inferred
    ON trades (ticker_inferred);
CREATE INDEX IF NOT EXISTS idx_trade_evidence_trade
    ON trade_evidence (trade_id);
CREATE INDEX IF NOT EXISTS idx_market_prices_security_date
    ON market_prices (security_id, price_date);
CREATE INDEX IF NOT EXISTS idx_corporate_actions_security_date
    ON corporate_actions (security_id, effective_date);

CREATE UNIQUE INDEX IF NOT EXISTS uq_member_identifier_primary_type
    ON member_identifiers (member_id, identifier_type)
    WHERE is_primary;
CREATE UNIQUE INDEX IF NOT EXISTS uq_document_primary_per_filing
    ON documents (filing_id)
    WHERE is_primary;
CREATE UNIQUE INDEX IF NOT EXISTS uq_document_extraction_preferred
    ON document_extractions (document_id, extraction_type)
    WHERE is_preferred;
CREATE UNIQUE INDEX IF NOT EXISTS uq_security_identifier_primary_type
    ON security_identifiers (security_id, identifier_type)
    WHERE is_primary;
CREATE UNIQUE INDEX IF NOT EXISTS uq_member_match_candidate_accepted
    ON member_match_candidates (filing_id)
    WHERE decision = 'accepted';

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS members_set_updated_at ON members;
CREATE TRIGGER members_set_updated_at
BEFORE UPDATE ON members
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trades_set_updated_at ON trades;
CREATE TRIGGER trades_set_updated_at
BEFORE UPDATE ON trades
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();
"""


@dataclass(frozen=True)
class DatabaseConfig:
    """Connection information for the application and maintenance databases."""

    database_url: str
    database_name: str
    maintenance_url: str


def load_dotenv_if_available() -> None:
    """Load the project .env file when python-dotenv is installed."""

    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    project_root = Path(__file__).resolve().parent.parent
    load_dotenv(project_root / ".env")


def replace_database_name(database_url: str, database_name: str) -> str:
    """Return a PostgreSQL URL that points to a different database."""

    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("DATABASE_URL must use the postgres or postgresql scheme")
    path = f"/{database_name}"
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


def resolve_config(database_url_override: str | None = None) -> DatabaseConfig:
    """Resolve connection settings from an override or environment variables."""

    database_url = database_url_override or os.getenv("DATABASE_URL")
    if not database_url:
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("POSTGRES_PORT", "5433")
        database = os.getenv("POSTGRES_DB", "congress_trades")
        user = os.getenv("POSTGRES_USER")
        password = os.getenv("POSTGRES_PASSWORD")
        if not user or not password:
            raise ValueError(
                "Set POSTGRES_USER and POSTGRES_PASSWORD in the project .env file"
            )
        database_url = (
            f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}"
            f"@{host}:{port}/{database}"
        )

    parsed = urlsplit(database_url)
    database_name = parsed.path.lstrip("/")
    if not database_name:
        raise ValueError("DATABASE_URL must include a database name")

    maintenance_database = os.getenv("POSTGRES_MAINTENANCE_DB", "postgres")
    maintenance_url = replace_database_name(database_url, maintenance_database)
    return DatabaseConfig(database_url, database_name, maintenance_url)


def create_database_if_missing(config: DatabaseConfig) -> bool:
    """Create the application database and return True when it was created."""

    with psycopg2.connect(config.maintenance_url) as connection:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (config.database_name,),
            )
            if cursor.fetchone():
                return False
            cursor.execute(
                sql.SQL("CREATE DATABASE {} ENCODING 'UTF8'").format(
                    sql.Identifier(config.database_name)
                )
            )
    return True


def apply_schema(config: DatabaseConfig) -> None:
    """Apply all tables, constraints, indexes, functions, and triggers."""

    with psycopg2.connect(config.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(SCHEMA_SQL)
        connection.commit()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create the StockGov PostgreSQL database and initial schema."
    )
    parser.add_argument(
        "--database-url",
        help="Override DATABASE_URL for this run.",
    )
    parser.add_argument(
        "--skip-create-database",
        action="store_true",
        help="Do not connect to the maintenance database; only apply the schema.",
    )
    parser.add_argument(
        "--print-sql",
        action="store_true",
        help="Print the schema SQL and exit without connecting to PostgreSQL.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.print_sql:
        print(SCHEMA_SQL.strip())
        return 0

    load_dotenv_if_available()
    try:
        config = resolve_config(args.database_url)
        created = False
        if not args.skip_create_database:
            created = create_database_if_missing(config)
        apply_schema(config)
    except (ValueError, psycopg2.Error) as exc:
        print(f"Database setup failed: {exc}", file=sys.stderr)
        return 1

    database_action = "created" if created else "already existed"
    print(
        f"Database '{config.database_name}' {database_action}; "
        "StockGov schema is ready."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
