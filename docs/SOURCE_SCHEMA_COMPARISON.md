# Congress Source-to-Database Comparison

**Prepared:** September 4, 2026
**Scope:** Current files downloaded from `unitedstates/congress-legislators` compared with StockGov's `scripts/create_database.py` and `scripts/load_congress_data.py`.

## 1. Downloaded source inventory

StockGov now retains every published representation offered by the upstream project:

| Dataset | YAML | JSON | CSV |
|---|:---:|:---:|:---:|
| legislators-current | Yes | Yes | Yes |
| legislators-historical | Yes | Yes | Yes |
| legislators-social-media | Yes | Yes | No |
| committees-current | Yes | Yes | No |
| committee-membership-current | Yes | Yes | Yes |
| committees-historical | Yes | Yes | No |
| legislators-district-offices | Yes | Yes | Yes |
| executive | Yes | Yes | No |

YAML and JSON contain equivalent nested source data. CSV is a convenient flattened subset and is not authoritative for complete loading. The rebuilt loader should continue using YAML as its canonical input, while recording and optionally validating the matching JSON and CSV files.

## 2. Changes since the V1 snapshot

The refreshed files do not add previously unseen field names. They do change current records:

- `legislators-current` adds Everton Blair Jr. (GA-13, term beginning September 1, 2026).
- `legislators-current` adds Aisha Wahab (CA-14, term beginning September 2, 2026).
- State Senator David Graham's Capitol office changes from B33 Russell to 211 Russell.
- A Senate Environment and Public Works subcommittee is renamed from “Fisheries, Wildlife, and Water” to “Fisheries, Water, and Wildlife.”
- `committee-membership-current` changes several Senate Appropriations assignments and ranks.
- Susan Collins becomes chairman of Senate Appropriations Legislative Branch Subcommittee.
- Ron Johnson becomes chairman of the Senate Budget Committee.
- Several House Judiciary, Oversight, Veterans' Affairs, Small Business, and other subcommittee assignments change.

`legislators-current.yaml`, `committees-current.yaml`, and `committee-membership-current.yaml` differ from their V1 copies. The other five YAML datasets are byte-for-byte unchanged.

## 3. Fields already handled correctly

No new dedicated columns are required for these fields:

- All member external identifiers—including Bioguide, FEC, GovTrack, LIS, OpenSecrets, Wikidata, Google entity, ICPSR, C-SPAN, VoteSmart, Ballotpedia, Pictorial, House History, and previous IDs—fit in `member_identifiers`.
- Preferred, official, nickname, and former names fit in `member_names`.
- Basic biography fields fit in `members`.
- Basic service dates, state, district, Senate class/rank, party, and accession method fit in `member_terms`.
- Leadership positions fit in `leadership_roles`.
- Basic committee identities and historical names fit in `committees` and `committee_congresses`.
- Current committee membership, party side, rank, and title fit in `committee_memberships`.
- Basic Capitol and district office addresses fit in `member_offices`.
- Named social accounts fit in `member_social_accounts`; numeric social IDs can fit in `member_identifiers`.
- Basic president/vice-president identities and service dates fit in `executives` and `executive_terms`.
- Complete YAML records are retained as JSON in staging tables, providing a lossless audit copy even when a normalized column is absent.

## 4. Normalized schema gaps

### 4.1 Members and family

Source fields not queryable in normalized tables:

- `family[].name`
- `family[].relation`

Recommended addition: `member_family_relationships` with member, relative name, relationship type, optional linked member, validity dates, and source snapshot.

### 4.2 Congressional terms and party changes

Source fields not normalized:

- `terms[].caucus`
- `terms[].end-type`
- `terms[].party_affiliations[].party`
- `terms[].party_affiliations[].caucus`
- `terms[].party_affiliations[].start`
- `terms[].party_affiliations[].end`
- `terms[].url`
- `terms[].contact_form`
- `terms[].rss_url`

Recommended changes:

- Add `caucus_party_code`, `caucus_party_name_raw`, `term_end_type`, `official_website_url`, `contact_form_url`, and `rss_url` to `member_terms`.
- Add `member_term_party_affiliations` for dated party/caucus periods. A separate table is necessary because one term may contain multiple affiliation periods.

### 4.3 Committees and subcommittees

Source fields not normalized:

- `house_committee_id`
- `senate_committee_id`
- `jurisdiction`
- `jurisdiction_source`
- `address`
- `phone`
- `rss_url`
- `minority_url`
- `minority_rss_url`
- `youtube_id`
- `wikipedia`
- Subcommittee `address`, `phone`, and `wikipedia`

Recommended changes:

- Add `committee_identifiers` so Thomas, House, Senate, Wikipedia, and future identifiers are not conflated into one `committee_code`.
- Add committee contact and descriptive columns to `committees`: jurisdiction text/source, address, phone, RSS URLs, minority website, YouTube ID, and Wikipedia name.

### 4.4 Committee membership

The source supplies `chamber` on joint-committee memberships. The loader currently discards it.

Recommended change: add `member_chamber` to `committee_memberships`, nullable for non-joint committees and constrained to `house` or `senate` when supplied.

The source represents ex-officio status through free-text `title`; it does not currently provide a separate `ex_officio` field. The loader checks a nonexistent `ex_officio` key, causing `is_ex_officio` to remain false even when the title is `Ex Officio`.

Recommended loader correction: derive `is_ex_officio` case-insensitively from `title` while still accepting a future explicit source flag.

### 4.5 District offices

Source fields not represented cleanly:

- `offices[].id`
- `offices[].suite`
- `offices[].hours`
- `offices[].latitude`
- `offices[].longitude`

The current loader embeds the source office ID in `office_type` and combines suite and hours into `address_line_2`, making direct queries unnecessarily difficult.

Recommended additions to `member_offices`:

- `source_office_id`
- `suite`
- `hours_text`
- `latitude`
- `longitude`

Keep `office_type` as a stable category such as `capitol` or `district`.

### 4.6 Social-media identifiers

Twitter and Instagram numeric IDs are stored as generic member identifiers. YouTube's channel ID is currently treated as a second YouTube account rather than a persistent platform identifier.

Recommended change: add optional `platform_account_id` to `member_social_accounts`. Continue supporting generic identifiers for backwards compatibility, but store Twitter, Instagram, and YouTube IDs next to their handles when they can be matched.

### 4.7 Executive branch

Source fields not normalized on executives:

- Gender
- Name suffix, nickname, and official full name
- All identifiers except Bioguide
- Term accession method (`election`, `succession`, or `appointment`)

Recommended changes:

- Expand `executives` with suffix, nickname, official name, and gender.
- Add `executive_identifiers`, mirroring `member_identifiers`.
- Add `accession_method` to `executive_terms`.

## 5. Loader differences

The current loader reads only the eight YAML filenames, which is appropriate for canonical loading. It needs these changes after schema approval:

1. Load the new relational fields and tables listed above.
2. Derive committee ex-officio status from the title.
3. Preserve office ID, suite, hours, latitude, and longitude separately.
4. Join social handle IDs to their platform accounts.
5. Load complete executive names, identifiers, gender, and accession method.
6. Continue retaining full raw YAML records in staging.
7. Record all downloaded representations in source provenance, without importing the same logical records three times.
8. Treat YAML as canonical and use JSON/CSV for validation and convenient export only.

## 6. Database-only fields

Many StockGov tables and fields intentionally have no counterpart in these congressional directory files. They should remain because they support provenance, PTR processing, analytics, and future sources:

- Source snapshots and import runs
- Filing catalog, identity matching, and user selection batches
- Downloaded documents, processing jobs, and extraction results
- Trades and trade evidence
- Securities and security identifiers
- Market prices and corporate actions
- House/Senate filing and trade staging tables
- Internal identity keys, timestamps, validation status, and processing metadata

These are not schema mismatches; they are StockGov application infrastructure.

## 7. Recommended decision

Approve the normalized additions in Section 4 and the loader corrections in Section 5. Do not attempt to create a dedicated database column for every external identifier or import YAML, JSON, and CSV as duplicate data. The generic identifier tables and raw staging records already provide the right extensibility and audit trail.

No database schema or loader changes described here have been applied, and no StockGov Python script has been executed.
