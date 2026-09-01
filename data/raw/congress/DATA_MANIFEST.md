# Data Raw Files Manifest

## Source: congress-legislators-main/

Copied to `data/raw/congress/` for database ingestion. All files are read-only copies from the upstream [congress-legislators](https://github.com/unitedstates/congress-legislators) project.

---

## Files Copied (8 Complete Congressional Data Files)

### Core Member Data

#### 1. **legislators-current.yaml** (1.03 MB)
- **Purpose:** Current serving members of Congress
- **Records:** ~535 members (House + Senate)
- **Key Fields:** bioguide ID, name, birth date, gender, party, state, chamber, district, terms, leadership roles
- **Usage:** Load into `members` table; link with trade/SEC data
- **Update Frequency:** Weekly–monthly (upstream)

#### 2. **legislators-historical.yaml** (8.58 MB)
- **Purpose:** Historical members (1789–present)
- **Records:** ~11,000+ historical members
- **Key Fields:** Same as current, but includes all terms served
- **Usage:** Backfill member master data; enable historical analysis
- **Update Frequency:** Less frequent (cumulative archive)

### Committee Data

#### 3. **committees-current.yaml** (0.06 MB)
- **Purpose:** Current committees and subcommittees
- **Records:** ~300 committees
- **Key Fields:** Committee name, type (House/Senate/Joint), thomas_id, jurisdiction, subcommittees
- **Usage:** Load into `committees` table; reference for committee-trade analysis
- **Update Frequency:** Monthly (on structure changes)

#### 4. **committee-membership-current.yaml** (0.28 MB)
- **Purpose:** Current committee assignments
- **Records:** ~5,500 memberships
- **Key Fields:** Bioguide ID, committee ID, rank (seniority), party status (majority/minority), title (Chair, Ranking Member, etc.)
- **Usage:** Load into `committee_memberships` table; segment trades by committee membership
- **Update Frequency:** Weekly (on assignment changes)

#### 5. **committees-historical.yaml** (0.20 MB)
- **Purpose:** Historical committees (93rd Congress 1973–present)
- **Records:** ~400+ committees
- **Key Fields:** Committee name variations by Congress, active Congresses
- **Usage:** Optional; enables "Find all Finance Committee trades 1973–2000" type queries
- **Update Frequency:** Rarely (archive)

### Supplementary Member & Executive Data

#### 6. **legislators-district-offices.yaml** (0.31 MB)
- **Purpose:** District office locations and contact information
- **Records:** ~2,000+ district offices
- **Key Fields:** Address, city, state, zip, phone, fax, hours, GPS coordinates (latitude/longitude), office ID (bioguide-city)
- **Usage:** Load into `member_district_offices` table; enable geographic analysis of trading patterns
- **Update Frequency:** Monthly (office changes)

#### 7. **legislators-social-media.yaml** (0.10 MB)
- **Purpose:** Official social media accounts for current members
- **Records:** ~500+ members with verified official accounts
- **Key Fields:** Twitter, YouTube, Facebook, Instagram, Mastodon handles; bioguide ID
- **Usage:** Load into `member_social_media` table; optional for contact/outreach; link to trading activity sentiment analysis
- **Update Frequency:** Weekly (on new accounts)

#### 8. **executive.yaml** (0.03 MB)
- **Purpose:** U.S. Presidents and Vice Presidents
- **Records:** ~50+ executive branch terms
- **Key Fields:** Name, birth date, terms served (start/end), party, type (prez/viceprez)
- **Usage:** Load into `executives` and `executive_terms` tables; optional for executive-level trade analysis (Presidents/VPs who may have holdings)
- **Update Frequency:** Rarely (cumulative archive)

---

## Data Integrity & Licensing

- **Source:** https://github.com/unitedstates/congress-legislators
- **License:** CC0 1.0 (Public Domain) — No restrictions
- **Status:** Read-only copies (congress-legislators-main/ never modified)
- **Last Updated From Source:** (Update regularly by pulling from upstream)

---

## PostgreSQL Schema Mapping

### Member Loading (from legislators-current.yaml + legislators-historical.yaml)

```sql
-- After parsing YAML, load into:
CREATE TABLE members (
  member_id INT PRIMARY KEY,
  bioguide_id VARCHAR(10) UNIQUE NOT NULL,
  first_name VARCHAR(255),
  last_name VARCHAR(255),
  birth_date DATE,
  gender CHAR(1),
  external_ids JSONB  -- Contains thomas, govtrack, opensecrets, fec, etc.
);

CREATE TABLE member_terms (
  term_id INT PRIMARY KEY,
  member_id INT REFERENCES members,
  chamber VARCHAR(10),  -- 'house' or 'senate'
  state VARCHAR(2),
  party VARCHAR(20),
  term_start DATE,
  term_end DATE,
  district INT,         -- For House only
  url VARCHAR(500),
  phone VARCHAR(20),
  office_address TEXT
);

CREATE TABLE leadership_roles (
  role_id INT PRIMARY KEY,
  member_id INT REFERENCES members,
  title VARCHAR(255),
  chamber VARCHAR(10),
  role_start DATE,
  role_end DATE
);
```

### Committee Loading (from committees-current.yaml + committee-membership-current.yaml)

```sql
CREATE TABLE committees (
  committee_id VARCHAR(10) PRIMARY KEY,
  committee_name VARCHAR(500),
  committee_type VARCHAR(20),  -- 'house', 'senate', 'joint'
  thomas_id VARCHAR(10),
  house_committee_id VARCHAR(10),
  senate_committee_id VARCHAR(10),
  jurisdiction TEXT,
  parent_committee_id VARCHAR(10)  -- For subcommittees
);

CREATE TABLE committee_memberships (
  membership_id INT PRIMARY KEY,
  committee_id VARCHAR(10) REFERENCES committees,
  member_id INT REFERENCES members,
  party VARCHAR(20),  -- 'majority' or 'minority'
  rank INT,          -- 1 = Chair/Ranking Member
  title VARCHAR(255)
);
```

### District Offices & Social Media (from legislators-district-offices.yaml + legislators-social-media.yaml)

```sql
CREATE TABLE member_district_offices (
  office_id VARCHAR(50) PRIMARY KEY,  -- bioguide-city
  member_id INT REFERENCES members,
  address VARCHAR(255),
  building VARCHAR(255),
  city VARCHAR(100) NOT NULL,
  state VARCHAR(2) NOT NULL,
  zip VARCHAR(10),
  phone VARCHAR(20),
  fax VARCHAR(20),
  suite VARCHAR(50),
  hours TEXT,
  latitude DECIMAL(10, 6),
  longitude DECIMAL(10, 6)
);

CREATE TABLE member_social_media (
  social_media_id INT PRIMARY KEY,
  member_id INT UNIQUE REFERENCES members,
  twitter VARCHAR(255),
  youtube VARCHAR(255),
  youtube_id VARCHAR(255),
  instagram VARCHAR(255),
  instagram_id VARCHAR(255),
  facebook VARCHAR(255),
  mastodon VARCHAR(255)
);
```

### Executive Branch (from executive.yaml)

```sql
CREATE TABLE executives (
  executive_id INT PRIMARY KEY,
  bioguide_id VARCHAR(10),
  first_name VARCHAR(255),
  last_name VARCHAR(255),
  birth_date DATE,
  gender CHAR(1)
);

CREATE TABLE executive_terms (
  executive_term_id INT PRIMARY KEY,
  executive_id INT REFERENCES executives,
  term_type VARCHAR(20),  -- 'prez' or 'viceprez'
  party VARCHAR(20),
  term_start DATE,
  term_end DATE,
  how VARCHAR(20)  -- 'election', 'succession', 'appointment'
);
```

---

## Next Steps

1. **Create ingestion module** → `ingestion/members/loader.py`
   - Parse YAML files
   - Normalize data
   - Load into PostgreSQL members/member_terms/leadership_roles tables

2. **Create committee loader** → `ingestion/congress/committees_loader.py`
   - Parse committees-current.yaml
   - Parse committee-membership-current.yaml
   - Load into PostgreSQL committees/committee_memberships tables

3. **Link with trade data**
   - Join congressional trades with members via bioguide_id or name
   - Segment trades by committee membership

4. **Enable historical analysis**
   - Use legislators-historical.yaml to find which committee a member belonged to in a given year
   - Correlate with historical trades (from Quantgress)

---

## File Statistics

```
Total Size: 9.6 MB
File Count: 8 YAML files

legislators-historical.yaml       8.58 MB  (89.4%)  — Largest due to 230+ years of data
legislators-current.yaml          1.03 MB  (10.7%)
legislators-district-offices.yaml 0.31 MB  (3.2%)
committee-membership-current      0.28 MB  (2.9%)
committees-historical.yaml        0.20 MB  (2.1%)
legislators-social-media.yaml     0.10 MB  (1.0%)
committees-current.yaml           0.06 MB  (0.6%)
executive.yaml                    0.03 MB  (0.3%)
```

All files are UTF-8 encoded, YAML 1.1 format, maintained by the unitedstates community.
