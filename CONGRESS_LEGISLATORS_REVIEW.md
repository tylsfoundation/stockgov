# Congress-Legislators Repository Review

## Overview

**Project:** [congress-legislators](https://github.com/unitedstates/congress-legislators)  
**Status:** Actively maintained authoritative source for U.S. Congressional member data  
**License:** CC0 1.0 (Public Domain Dedication)  
**Formats:** YAML (source), JSON, CSV (converted formats)  
**Scope:** 1789–Present (all members of Congress + presidents/vice presidents)  

---

## Data Files Provided

### Current Legislative Data
| File | Record Count | Key Fields | Coverage |
|------|--------------|-----------|----------|
| **legislators-current.yaml** | ~535 members | Bioguide ID, name, birth date, gender, party, state, chamber, district, terms, leadership roles | Current House/Senate members only |
| **legislators-historical.yaml** | ~11,000+ members | Same as current | All former members (1789–present) |
| **legislators-social-media.yaml** | ~500+ members | Twitter, YouTube, Facebook, Instagram, Mastodon handles | Current members with verified official accounts |
| **committees-current.yaml** | ~300 committees | Committee type, name, jurisdiction, subcommittees | Current House, Senate, Joint committees |
| **committee-membership-current.yaml** | ~5,500 memberships | Member bioguide ID, committee/subcommittee ID, rank, party status, title | Current committee assignments with seniority |
| **committees-historical.yaml** | ~400+ committees | Name variations, Congress numbers where active | Historical committees from 93rd Congress (1973–present) |
| **legislators-district-offices.yaml** | ~2,000+ offices | Address, city, state, zip, phone, fax, hours, GPS coordinates | District office locations for current members |
| **executive.yaml** | ~50+ terms | Presidents, vice presidents, terms served | U.S. Presidents/VPs (1789–present) |

---

## Data Dictionary & Field Structure

### Legislator Record Example
```yaml
- id:
    bioguide: C000127        # PRIMARY KEY - Use this for deduplication
    thomas: '00172'          # Legacy Thomas.gov ID
    govtrack: 300018         # GovTrack.us ID
    opensecrets: N00007836   # OpenSecrets.org ID
    fec: [S8WA00194]        # FEC Committee IDs (list)
    cspan: 26137            # C-SPAN video ID
    wikipedia: Maria Cantwell
    ballotpedia: Maria Cantwell
  name:
    first: Maria
    middle: null
    last: Cantwell
    suffix: null
    nickname: null
    official_full: Maria Cantwell
  bio:
    birthday: '1958-10-13'
    gender: F              # M or F
  terms:
    - type: rep            # "rep" (House) or "sen" (Senate)
      start: '1993-01-05'
      end: '1995-01-03'
      state: WA
      party: Democrat      # Democrat, Republican, Independent
      district: 1          # 0 = At-large, -1 = unknown (historical)
      url: http://cantwell.senate.gov
      address: 311 HART SENATE OFFICE BUILDING WASHINGTON DC 20510
      phone: 202-224-3441
      fax: 202-228-0514
      contact_form: URL
      office: Room/Building code
      rss_url: URL
  leadership_roles:        # Optional - only for party leadership
    - title: Minority Leader
      chamber: senate
      start: '2007-01-04'
      end: '2009-01-06'
```

### Key ID Cross-walks
- **bioguide** — Best primary key; stable congressional record identifier
- **thomas** — Legacy; used in older THOMAS.gov (now Congress.gov)
- **govtrack** — GovTrack.us numeric ID
- **opensecrets** — Center for Responsive Politics
- **fec** — Federal Election Commission committee IDs (list)
- **cspan** — C-SPAN video database ID
- **wikipedia/ballotpedia** — Page names (not URLs)
- **icpsr** — Keith Poole's VoteView.com historical roll call data

---

## Committee Data Structure

### Committee Records
```yaml
- type: house           # "house", "senate", or "joint"
  name: House Committee on Agriculture
  thomas_id: HSAG       # 4-letter THOMAS code
  house_committee_id: AG   # 2-letter House code (last 2 of THOMAS)
  senate_committee_id: null # For Senate/Joint committees
  url: http://agriculture.house.gov/
  jurisdiction: The U.S. House Committee on Agriculture...
  jurisdiction_source: http://en.wikipedia.org/wiki/House_Committee_on_Agriculture
  subcommittees:
    - name: Subcommittee on Conservation, Energy, and Forestry
      thomas_id: '01'   # Zero-padded 2-digit code
```

### Committee Membership
```yaml
HSAG:                    # Committee ID (thomas_id)
  - bioguide: L000491
    name: Frank D. Lucas  # Debug only — use bioguide for joins
    party: majority       # "majority" or "minority"
    rank: 1              # 1 = Chair/Ranking Member (most senior)
    title: Chair
    chamber: null        # For joint committees only
```

---

## Data Quality & Characteristics

### Strengths
✅ **Authoritative & Well-Curated**
- Maintained by [unitedstates](https://github.com/unitedstates/) community (Sunlight Labs, ProPublica, GovTrack, FiveThirtyEight, MapLight)
- Combines manual volunteer edits + automated imports from primary sources
- Public domain (CC0) — no licensing restrictions

✅ **Comprehensive Historical Coverage**
- 230+ years of U.S. Congressional data (1789–present)
- ~11,000+ member records (current + historical)
- Tracks party changes, name changes, leadership roles

✅ **Multiple Export Formats**
- YAML (source, human-readable, easy to diff)
- JSON (programmatic access)
- CSV (spreadsheet-friendly)

✅ **Rich Cross-walk IDs**
- Links to 11+ external databases (GovTrack, OpenSecrets, Congress.gov, VoteView, etc.)
- Enables joining with other political data sources

✅ **Real Committee Memberships**
- Current + historical committee assignments
- Seniority ranking (useful for understanding committee influence)
- Subcommittee structure

✅ **District-Level Granularity**
- House district assignments
- District office locations with GPS coordinates
- Useful for geographic analysis

### Limitations

❌ **No Real-Time Updates**
- Manual maintenance; delays between Congressional action and data updates
- Committee changes may lag by weeks/months
- Not suitable for high-frequency ingestion

❌ **No Member Financial Data**
- No stock holdings, net worth, or financial disclosures
- No transaction history (this is why we need SEC Form 4 + PTR scrapers)
- Only biographical + role information

❌ **Limited Historical Committee Data**
- Committee memberships current-only (no historical assignments except in some cases)
- Historical committees only from 93rd Congress (1973 forward)
- Pre-1973 committee data spotty

❌ **No Voting Records or Legislation**
- Only member identifiers, not roll call votes
- No bill sponsorship or voting patterns
- No committee vote histories

❌ **Some Missing Data**
- ~13% of House historical PDFs not OCR'd (still relevant here for member verification)
- Some legacy name/ID discrepancies
- Wikipedia/Ballotpedia fields may be outdated

❌ **District Office Data Crowdsourced**
- Relies on volunteers scraping member websites
- May be incomplete or outdated for some members

---

## Use Cases for StockGov

### ✅ **PERFECT FITS** — Use directly
1. **Member Master Data**
   - Normalize member records with bioguide ID as primary key
   - Import current members, historical members, name aliases
   - Join current members with congressional trade disclosures

2. **Chamber/Party/State Classification**
   - Classify trades by member party, chamber, state
   - Filter/segment analysis by Republican vs. Democrat vs. Independent

3. **Committee Membership Context**
   - Link trades to committee positions (e.g., "trades by Finance Committee members")
   - Rank committee influence by member seniority

4. **Leadership Identification**
   - Flag trades by party leaders (Majority Leader, Minority Leader, etc.)
   - Segment analysis by leadership vs. rank-and-file

5. **Multi-Source Deduplication**
   - Use bioguide ID + name to match congress-legislators data with:
     - Quantgress congressional trades
     - SEC Form 4 insider trading (via name matching)
     - FEC donor records (via names + states)

6. **Geographic/District Analysis**
   - Map trades against member state/district
   - Find regional trading patterns

### ⚠️ **PARTIAL FITS** — Need augmentation
1. **Member Tenure Tracking**
   - Has term dates, but no mid-term changes (resignations, expulsions)
   - Need to augment with "term_status" tracking from SEC filings

2. **Committee Changes During Term**
   - Historical assignments pre-1973 incomplete
   - Would need external data source for full committee history

### ❌ **NOT PROVIDED** — Must source separately
1. **Member Financial Disclosures** → Use Senate PTRs + House disclosures + SEC Form 4
2. **Company Events** → Use SEC EDGAR, press releases, contracts
3. **Stock Prices** → Use Yahoo Finance, IEX, Polygon
4. **Performance Returns** → Must calculate from price data
5. **Lobbying Records** → Use LDA.gov (Quantgress scrapes this)
6. **Trading Performance Benchmarks** → Must calculate vs. SPY

---

## Recommended Integration Pattern

### Phase 1: Load Member Master Data
```python
# Pseudo-code
import yaml

# Load current members
with open('legislators-current.yaml') as f:
    current_members = yaml.safe_load(f)

# Load historical for reference
with open('legislators-historical.yaml') as f:
    historical_members = yaml.safe_load(f)

# Normalize to PostgreSQL
# CREATE TABLE members (
#   member_id INT PRIMARY KEY,
#   bioguide_id VARCHAR(10) UNIQUE NOT NULL,
#   first_name VARCHAR(255),
#   last_name VARCHAR(255),
#   full_name VARCHAR(255),
#   birth_date DATE,
#   gender CHAR(1),
#   wikipedia_url VARCHAR(500),
#   ballotpedia_url VARCHAR(500),
#   -- cross-walk IDs for joining other datasets
#   thomas_id VARCHAR(10),
#   govtrack_id INT,
#   opensecrets_id VARCHAR(10),
#   fec_ids TEXT,  -- Array/JSON
#   cspan_id INT,
#   created_at TIMESTAMP,
#   updated_at TIMESTAMP
# );

# CREATE TABLE member_terms (
#   term_id INT PRIMARY KEY,
#   member_id INT NOT NULL REFERENCES members(member_id),
#   chamber VARCHAR(10),  -- 'house' or 'senate'
#   state VARCHAR(2),
#   party VARCHAR(20),    -- 'Democrat', 'Republican', 'Independent'
#   caucus VARCHAR(20),   -- For independents
#   term_start DATE,
#   term_end DATE,
#   district INT,
#   class INT,            -- For senators
#   state_rank VARCHAR(10), -- 'senior' or 'junior'
#   url VARCHAR(500),
#   phone VARCHAR(20),
#   office_address TEXT
# );

# CREATE TABLE leadership_roles (
#   role_id INT PRIMARY KEY,
#   member_id INT NOT NULL REFERENCES members(member_id),
#   title VARCHAR(255),
#   chamber VARCHAR(10),
#   role_start DATE,
#   role_end DATE
# );
```

### Phase 2: Load Committee Structure
```python
# CREATE TABLE committees (
#   committee_id VARCHAR(10) PRIMARY KEY,
#   committee_name VARCHAR(500),
#   committee_type VARCHAR(20),  -- 'house', 'senate', 'joint'
#   thomas_id VARCHAR(10),
#   house_committee_id VARCHAR(10),
#   senate_committee_id VARCHAR(10),
#   jurisdiction TEXT,
#   parent_committee_id VARCHAR(10)  -- For subcommittees
# );

# CREATE TABLE committee_memberships (
#   membership_id INT PRIMARY KEY,
#   committee_id VARCHAR(10) NOT NULL REFERENCES committees(committee_id),
#   member_id INT NOT NULL REFERENCES members(member_id),
#   party VARCHAR(20),  -- 'majority' or 'minority'
#   rank INT,
#   title VARCHAR(255),
#   chamber VARCHAR(10)
# );
```

### Phase 3: Join with Trade Data
```sql
-- Example: Find all trades by Democrats on the Finance Committee
SELECT
  m.last_name,
  m.first_name,
  t.ticker,
  t.transaction_date,
  t.amount,
  c.committee_name
FROM trades t
JOIN members m ON t.bioguide_id = m.bioguide_id
JOIN committee_memberships cm ON m.member_id = cm.member_id
JOIN committees c ON cm.committee_id = c.committee_id
WHERE c.committee_name LIKE '%Finance%'
  AND m.party = 'Democrat'
  AND t.transaction_date >= '2024-01-01'
ORDER BY t.transaction_date DESC;
```

---

## Data Maintenance & Freshness

### Update Cadence
- **Source:** GitHub repository (unitedstates/congress-legislators)
- **Frequency:** Manual updates (typically weekly–monthly)
- **Lag:** May be 1–4 weeks behind live Congressional action
- **CI:** CircleCI runs validation on each commit

### How to Stay Current
1. **Pull from GitHub** (recommended for StockGov)
   ```bash
   git clone https://github.com/unitedstates/congress-legislators.git
   git pull  # Before each ingestion run
   ```

2. **Use Their Downloads** (if not embedding in repo)
   - YAML: https://unitedstates.github.io/congress-legislators/legislators-current.yaml
   - JSON: https://unitedstates.github.io/congress-legislators/legislators-current.json

3. **Subscribe to Changes** (GitHub watch/star)
   - Get notified of updates

### Validation
- CircleCI runs automated tests on each commit
- Schema validation, cross-reference checks
- No automated OCR or ML inference (manual reviews only)

---

## Field Mapping for StockGov Database

| StockGov Column | congress-legislators Field | Notes |
|---|---|---|
| `member_id` | `id.bioguide` | PRIMARY KEY — stable across all updates |
| `first_name` | `name.first` | May be informal/preferred name |
| `last_name` | `name.last` | UTF-8; may contain accents |
| `full_name` | `name.official_full` | Full formal name for display |
| `birth_date` | `bio.birthday` | Format: YYYY-MM-DD |
| `gender` | `bio.gender` | 'M' or 'F' |
| `party` | `terms[].party` | 'Democrat', 'Republican', 'Independent' |
| `state` | `terms[].state` | 2-letter USPS code |
| `chamber` | `terms[].type` | 'sen' or 'rep' |
| `district` | `terms[].district` | House only; 0=at-large; -1=unknown |
| `office_address` | `terms[].address` | Washington office only |
| `office_phone` | `terms[].phone` | |
| `wikipedia_url` | `id.wikipedia` | Page name (prepend `https://en.wikipedia.org/wiki/`) |
| `opensecrets_id` | `id.opensecrets` | For donor/lobbyist joins |
| `external_ids` | Multiple `id.*` fields | Store as JSON for flexibility |

---

## Practical Integration Steps

### Step 1: Add to Repository
```bash
# In c:\Home\StockGov\
git submodule add https://github.com/unitedstates/congress-legislators.git data/congress-legislators
# OR simply keep existing clone at congress-legislators-main/
```

### Step 2: Parse & Validate
```python
import yaml
from datetime import datetime

def load_legislators():
    with open('congress-legislators-main/legislators-current.yaml') as f:
        current = yaml.safe_load(f)
    with open('congress-legislators-main/legislators-historical.yaml') as f:
        historical = yaml.safe_load(f)
    return current + historical

def extract_current_members(members):
    """Filter for members currently in office."""
    now = datetime.now().date()
    current = []
    for member in members:
        for term in member.get('terms', []):
            if term['end'] >= now:  # Term hasn't ended
                current.append((member, term))
    return current
```

### Step 3: Load into PostgreSQL
```python
import psycopg2
from datetime import datetime

def load_members_to_db(members):
    conn = psycopg2.connect("dbname=congress_trades user=postgres")
    cur = conn.cursor()
    
    for member in members:
        ids = member.get('id', {})
        name = member.get('name', {})
        bio = member.get('bio', {})
        
        # Insert member
        cur.execute("""
            INSERT INTO members (bioguide_id, first_name, last_name, birth_date, gender)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (bioguide_id) DO UPDATE SET updated_at = NOW()
        """, (
            ids.get('bioguide'),
            name.get('first'),
            name.get('last'),
            bio.get('birthday'),
            bio.get('gender')
        ))
        
        # Insert cross-walk IDs as JSON
        member_id = cur.fetchone()[0]
        external_ids = {k: v for k, v in ids.items() if k != 'bioguide'}
        cur.execute("""
            UPDATE members SET external_ids = %s WHERE member_id = %s
        """, (json.dumps(external_ids), member_id))
        
        # Insert terms
        for term in member.get('terms', []):
            cur.execute("""
                INSERT INTO member_terms 
                (member_id, chamber, state, party, term_start, term_end, district)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                member_id,
                term.get('type'),
                term.get('state'),
                term.get('party'),
                term.get('start'),
                term.get('end'),
                term.get('district')
            ))
    
    conn.commit()
    cur.close()
    conn.close()
```

---

## Licensing & Reuse

- **License:** CC0 1.0 (Public Domain Dedication)
- **Restrictions:** None — data is public domain in the U.S.
- **Attribution:** Not required but appreciated (credit unitedstates/congress-legislators)
- **Modifications:** Allowed; no need to track changes
- **Commercial Use:** Permitted

---

## Summary Scorecard

| Capability | Rating | Notes |
|---|---|---|
| **Member Directory** | ⭐⭐⭐⭐⭐ | Comprehensive, authoritative, ~99% complete |
| **Historical Data** | ⭐⭐⭐⭐ | 230+ years; some gaps pre-1973 for committees |
| **Data Quality** | ⭐⭐⭐⭐⭐ | Curated by respected organizations, well-validated |
| **Freshness** | ⭐⭐⭐ | Weekly–monthly updates; acceptable for StockGov |
| **Cross-walk IDs** | ⭐⭐⭐⭐ | Links to 11+ external databases; very useful |
| **API/Access** | ⭐⭐⭐⭐ | Multiple formats; easy to parse |
| **Financial Data** | ❌ | Not included (use SEC/PTR sources) |
| **Real-Time Updates** | ⭐⭐ | Manual curation; best as daily batch load |
| **Documentation** | ⭐⭐⭐⭐⭐ | Excellent README with detailed data dictionaries |

---

## Conclusion

**congress-legislators-main is ESSENTIAL for StockGov's member data layer.** It provides:
- ✅ The authoritative member directory (current + historical)
- ✅ Committee structure and memberships
- ✅ Cross-walk IDs for joining with Quantgress + SEC data
- ✅ Party/state/chamber classification
- ✅ Leadership identification
- ✅ Public domain licensing (no restrictions)

**What it does NOT provide (and what must be sourced elsewhere):**
- ❌ Member financial disclosures → Use Senate PTRs + House + SEC Form 4
- ❌ Stock transaction data → Use congressional PTRs + SEC Form 4
- ❌ Stock prices → Use Yahoo Finance / IEX Cloud
- ❌ Company events → Use SEC EDGAR + press releases
- ❌ Real-time updates → Best as daily batch; consider web scraping for urgent changes

**Recommendation:** Load congress-legislators-main into PostgreSQL as the foundation for all member-related queries. Use its bioguide ID as the universal join key for trades, committees, leadership, and other member attributes.
