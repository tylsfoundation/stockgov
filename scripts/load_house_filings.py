"""Load House annual financial-disclosure XML indexes into StockGov.

The XML files are filing catalogs, not transaction rows. Every source row is
preserved in staging and in ``filing_source_occurrences``; ``filings`` contains
one normalized row per House DocID. No PDFs are downloaded.

Requirements: ``py -m pip install psycopg2-binary``

Examples:
    py scripts/load_house_filings.py --validate-only
    py scripts/load_house_filings.py --import
    py scripts/load_house_filings.py --files 2025FD.xml 2026FD.xml
    py scripts/load_house_filings.py --skip-member-matching
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, urlsplit, urlunsplit

try:
    import psycopg2
    from psycopg2.extras import Json
except ImportError as exc:
    raise SystemExit("psycopg2-binary is required: py -m pip install psycopg2-binary") from exc

FILE_RE = re.compile(r"^(\d{4})FD\.xml$")
EXPECTED_TAGS = ("Prefix", "Last", "First", "Suffix", "FilingType", "StateDst", "Year", "FilingDate", "DocID")
TYPE_MAP = {
    "A": "amendment", "B": "blind_trust", "C": "candidate_report",
    "D": "candidate_threshold_declaration", "E": "termination_exemption",
    "G": "gift_waiver", "H": "new_filer", "O": "annual_disclosure",
    "P": "ptr", "R": "ptr_waiver", "T": "termination",
    "W": "candidate_withdrawal", "X": "extension",
}
SOURCE = "house_clerk_financial_disclosure"
INDEX_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"

@dataclass
class Counts:
    read: int = 0
    inserted: int = 0
    updated: int = 0
    rejected: int = 0
    warnings: int = 0

    def add(self, other: "Counts") -> None:
        for key in vars(self): setattr(self, key, getattr(self, key) + getattr(other, key))

def project_root() -> Path: return Path(__file__).resolve().parent.parent

def load_dotenv(root: Path) -> None:
    path = root / ".env"
    if not path.exists(): return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

def database_url(override: str | None) -> str:
    load_dotenv(project_root())
    if override: return override
    if os.getenv("DATABASE_URL"): return os.environ["DATABASE_URL"]
    required = ["POSTGRES_USER", "POSTGRES_PASSWORD"]
    missing = [x for x in required if not os.getenv(x)]
    if missing: raise RuntimeError("Missing database configuration: " + ", ".join(missing))
    user, password = quote(os.environ["POSTGRES_USER"]), quote(os.environ["POSTGRES_PASSWORD"])
    host, port = os.getenv("POSTGRES_HOST", "localhost"), os.getenv("POSTGRES_PORT", "5433")
    db = os.getenv("POSTGRES_DB", "congress_trades")
    return urlunsplit(("postgresql", f"{user}:{password}@{host}:{port}", f"/{db}", "", ""))

def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""): h.update(chunk)
    return h.hexdigest()

def text(node: ET.Element, tag: str) -> str:
    child = node.find(tag)
    return "" if child is None or child.text is None else child.text.strip()

def normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", value.lower()).split())

def parse_date(value: str) -> date | None:
    if not value: return None
    return datetime.strptime(value, "%m/%d/%Y").date()

def parse_state_district(value: str) -> tuple[str | None, int | None, str | None]:
    if not value: return None, None, "StateDst is blank"
    match = re.fullmatch(r"([A-Z]{2})(\d{2})", value)
    if not match: return None, None, f"Malformed StateDst: {value}"
    return match.group(1), int(match.group(2)), None

def document_url(year: str, docid: str, code: str) -> str:
    folder = "ptr-pdfs" if code == "P" else "financial-pdfs"
    return f"https://disclosures-clerk.house.gov/public_disc/{folder}/{year}/{docid}.pdf"

def discover(source_dir: Path, selected: Iterable[str] | None, current_year: int) -> list[Path]:
    if not source_dir.is_dir(): raise ValueError(f"Source directory does not exist: {source_dir}")
    names = list(selected or [])
    paths = [source_dir / name for name in names] if names else list(source_dir.glob("*FD.xml"))
    result = []
    for path in paths:
        m = FILE_RE.fullmatch(path.name)
        if not m: raise ValueError(f"Not a canonical House XML filename: {path.name}")
        year = int(m.group(1))
        if year < 2008 or year > current_year: raise ValueError(f"File year outside 2008-{current_year}: {path.name}")
        if not path.is_file(): raise ValueError(f"Required source file is missing: {path}")
        result.append(path)
    if not result: raise ValueError("No canonical House XML files were found")
    return sorted(set(result), key=lambda p: int(p.name[:4]))

def parse_xml(path: Path) -> list[ET.Element]:
    root = ET.parse(path).getroot()
    if root.tag != "FinancialDisclosure": raise ValueError(f"{path.name}: expected FinancialDisclosure root, found {root.tag}")
    rows = root.findall("Member")
    for number, row in enumerate(rows, 1):
        tags = [c.tag for c in row]
        if set(tags) != set(EXPECTED_TAGS) or len(tags) != len(EXPECTED_TAGS):
            raise ValueError(f"{path.name} row {number}: unexpected Member elements {tags}")
    return rows

class Loader:
    def __init__(self, connection: Any, progress_every: int, match_members: bool):
        self.connection, self.progress_every, self.match_members = connection, progress_every, match_members

    @staticmethod
    def snapshot(cursor: Any, path: Path, year: int) -> tuple[int, bool]:
        sha = digest(path)
        cursor.execute("SELECT source_snapshot_id FROM source_snapshots WHERE source_name=%s AND content_hash=%s", (path.name, sha))
        row = cursor.fetchone()
        if row: return row[0], False
        cursor.execute("""INSERT INTO source_snapshots
            (source_name,source_type,source_url,local_path,coverage_start_date,coverage_end_date,retrieved_at,content_hash,file_size_bytes,format_version,notes)
            VALUES (%s,'house_financial_disclosure_xml',%s,%s,%s,%s,%s,%s,%s,'House FinancialDisclosure XML','Official annual House disclosure index') RETURNING source_snapshot_id""",
            (path.name, INDEX_URL.format(year=year), str(path.resolve()), date(year,1,1), date(year,12,31),
             datetime.fromtimestamp(path.stat().st_mtime, timezone.utc), sha, path.stat().st_size))
        return cursor.fetchone()[0], True

    @staticmethod
    def begin_import(cursor: Any, snapshot_id: int) -> int:
        cursor.execute("""INSERT INTO source_imports (source_snapshot_id,import_type,importer_version,status)
            VALUES (%s,'house_filing_index','1.0.0','running') RETURNING source_import_id""", (snapshot_id,))
        return cursor.fetchone()[0]

    @staticmethod
    def finish_import(cursor: Any, import_id: int, counts: Counts, status: str, error: str | None = None) -> None:
        cursor.execute("""UPDATE source_imports SET finished_at=CURRENT_TIMESTAMP,status=%s,records_read=%s,
            records_inserted=%s,records_updated=%s,records_rejected=%s,error_summary=%s WHERE source_import_id=%s""",
            (status, counts.read, counts.inserted, counts.updated, counts.rejected, error, import_id))

    @staticmethod
    def existing_stage(cursor: Any, snapshot_id: int, row_number: int) -> int | None:
        cursor.execute("""SELECT s.staging_house_filing_id FROM staging_house_filings s
            JOIN source_imports i ON i.source_import_id=s.source_import_id
            WHERE i.source_snapshot_id=%s AND s.source_row_number=%s ORDER BY s.staging_house_filing_id LIMIT 1""",
            (snapshot_id, row_number))
        row = cursor.fetchone(); return row[0] if row else None

    @staticmethod
    def upsert_filing(cursor: Any, rec: dict[str,str], parsed_date: date | None,
                      state: str | None, district: int | None, url: str,
                      snapshot_id: int, import_id: int) -> tuple[int, bool]:
        full = " ".join(x for x in (rec["Prefix"], rec["First"], rec["Last"], rec["Suffix"]) if x)
        cursor.execute("SELECT filing_id FROM filings WHERE source=%s AND source_filing_id=%s", (SOURCE, rec["DocID"]))
        old = cursor.fetchone()
        values = (rec["FilingType"], TYPE_MAP.get(rec["FilingType"], "unknown"), int(rec["Year"]), parsed_date,
                  rec["First"], rec["Last"], full, rec["StateDst"] or None, state, district, url, snapshot_id, import_id)
        if old:
            cursor.execute("""UPDATE filings SET filing_type_code_raw=%s,filing_type=%s,reporting_year=%s,filed_date=%s,
                raw_first_name=%s,raw_last_name=%s,raw_full_name=%s,raw_office=%s,state_code_guess=%s,district_guess=%s,
                source_url=%s,source_snapshot_id=%s,source_import_id=%s WHERE filing_id=%s""", values + (old[0],))
            return old[0], False
        cursor.execute("""INSERT INTO filings
            (source,source_filing_id,chamber,filing_type_code_raw,filing_type,reporting_year,filed_date,
             raw_first_name,raw_last_name,raw_full_name,raw_office,state_code_guess,district_guess,source_url,
             source_snapshot_id,source_import_id) VALUES (%s,%s,'house',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
             RETURNING filing_id""", (SOURCE, rec["DocID"]) + values)
        return cursor.fetchone()[0], True

    @staticmethod
    def candidates(cursor: Any, filing_id: int, rec: dict[str,str], state: str | None, district: int | None) -> list[dict[str,Any]]:
        source_name = normalize_name(f"{rec['First']} {rec['Last']}")
        cursor.execute("""SELECT DISTINCT m.member_id,m.preferred_name,n.normalized_name,
                   t.state_code,t.district_number,t.term_start_date,t.term_end_date
            FROM members m JOIN member_names n ON n.member_id=m.member_id
            JOIN member_terms t ON t.member_id=m.member_id
            WHERE t.chamber='house' AND (%s IS NULL OR t.state_code=%s)
              AND EXTRACT(YEAR FROM t.term_start_date)<=%s AND EXTRACT(YEAR FROM t.term_end_date)>=%s""",
            (state, state, int(rec["Year"]), int(rec["Year"])))
        grouped: dict[int,dict[str,Any]] = {}
        for mid, preferred, candidate_name, cstate, cdistrict, start, end in cursor.fetchall():
            name_score = SequenceMatcher(None, source_name, candidate_name).ratio()
            if name_score < .70: continue
            office = 1.0 if state and state == cstate and district is not None and district == cdistrict else (.65 if state and state == cstate else 0.0)
            term = 1.0
            score = .62 * name_score + .23 * office + .15 * term
            item = {"id":mid,"preferred":preferred,"name":name_score,"office":office,"term":term,"score":score,
                    "reasons":{"source_name":source_name,"candidate_name":candidate_name,"state":cstate,"district":cdistrict,
                               "term_start":str(start),"term_end":str(end)}}
            if mid not in grouped or score > grouped[mid]["score"]: grouped[mid] = item
        return sorted(grouped.values(), key=lambda x: (-x["score"], x["id"]))

    def match(self, cursor: Any, filing_id: int, rec: dict[str,str], state: str | None, district: int | None) -> None:
        rows = self.candidates(cursor, filing_id, rec, state, district)
        cursor.execute("DELETE FROM member_match_candidates WHERE filing_id=%s", (filing_id,))
        auto = bool(rows and rows[0]["score"] >= .90 and rows[0]["office"] == 1 and
                    (len(rows) == 1 or rows[0]["score"] - rows[1]["score"] >= .08))
        for rank, item in enumerate(rows, 1):
            cursor.execute("""INSERT INTO member_match_candidates
                (filing_id,candidate_member_id,candidate_rank,match_score,name_score,office_score,term_score,match_reasons,decision)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (filing_id,item["id"],rank,item["score"],item["name"],item["office"],item["term"],Json(item["reasons"]),
                 "accepted" if auto and rank == 1 else "pending"))
        if auto:
            cursor.execute("UPDATE filings SET member_id=%s,member_match_status='automatically_matched',member_match_method='name_state_district_term',member_match_confidence=%s WHERE filing_id=%s", (rows[0]["id"],rows[0]["score"],filing_id))
        else:
            status = "ambiguous" if rows else "unmatched"
            cursor.execute("UPDATE filings SET member_id=NULL,member_match_status=%s,member_match_method=%s,member_match_confidence=%s WHERE filing_id=%s",
                           (status,"candidate_scoring" if rows else None,rows[0]["score"] if rows else None,filing_id))

    def load_file(self, path: Path) -> Counts:
        started=time.monotonic(); year=int(path.name[:4]); rows=parse_xml(path); counts=Counts()
        print(f"Loading {path.name}: {len(rows):,} source rows", flush=True)
        with self.connection:
            with self.connection.cursor() as cur:
                snapshot_id,_ = self.snapshot(cur,path,year); import_id=self.begin_import(cur,snapshot_id)
                try:
                    for number,node in enumerate(rows,1):
                        counts.read += 1
                        rec={tag:text(node,tag) for tag in EXPECTED_TAGS}; errors=[]; warnings=[]
                        for required in ("DocID","Year","FilingType","First","Last"):
                            if not rec[required]: errors.append(f"{required} is blank")
                        if rec["Year"] and rec["Year"] != str(year): errors.append(f"Year {rec['Year']} does not match filename year {year}")
                        try: filed=parse_date(rec["FilingDate"])
                        except ValueError: filed=None; errors.append(f"Invalid FilingDate: {rec['FilingDate']}")
                        state,district,state_warning=parse_state_district(rec["StateDst"])
                        if state_warning: warnings.append(state_warning)
                        if rec["FilingType"] not in TYPE_MAP: warnings.append(f"Unknown FilingType: {rec['FilingType']}")
                        url=document_url(rec["Year"] or str(year),rec["DocID"],rec["FilingType"]) if rec["DocID"] else ""
                        raw_xml=ET.tostring(node,encoding="unicode",short_empty_elements=True)
                        existing=self.existing_stage(cur,snapshot_id,number)
                        filing_id=None; inserted=False
                        if not errors:
                            filing_id,inserted=self.upsert_filing(cur,rec,filed,state,district,url,snapshot_id,import_id)
                            if self.match_members: self.match(cur,filing_id,rec,state,district)
                            cur.execute("""INSERT INTO filing_source_occurrences
                                (filing_id,source_snapshot_id,source_import_id,source_row_number,index_year)
                                VALUES (%s,%s,%s,%s,%s) ON CONFLICT (source_snapshot_id,source_row_number)
                                DO UPDATE SET filing_id=EXCLUDED.filing_id,source_import_id=EXCLUDED.source_import_id,index_year=EXCLUDED.index_year""",
                                (filing_id,snapshot_id,import_id,number,year))
                        status="rejected" if errors else "loaded"
                        details=errors+warnings
                        if existing:
                            cur.execute("""UPDATE staging_house_filings SET doc_id_raw=%s,reporting_year_raw=%s,filing_type_code_raw=%s,
                                prefix_raw=%s,first_name_raw=%s,last_name_raw=%s,suffix_raw=%s,state_district_raw=%s,filed_date_raw=%s,
                                document_url_raw=%s,raw_xml=%s,validation_status=%s,error_details=%s,filing_id=%s WHERE staging_house_filing_id=%s""",
                                (rec["DocID"],rec["Year"],rec["FilingType"],rec["Prefix"],rec["First"],rec["Last"],rec["Suffix"],rec["StateDst"],rec["FilingDate"],url,raw_xml,status,Json(details),filing_id,existing))
                            counts.updated += 1
                        else:
                            cur.execute("""INSERT INTO staging_house_filings
                                (source_import_id,source_row_number,doc_id_raw,reporting_year_raw,filing_type_code_raw,prefix_raw,
                                 first_name_raw,last_name_raw,suffix_raw,state_district_raw,filed_date_raw,document_url_raw,raw_xml,
                                 validation_status,error_details,filing_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                                (import_id,number,rec["DocID"],rec["Year"],rec["FilingType"],rec["Prefix"],rec["First"],rec["Last"],rec["Suffix"],rec["StateDst"],rec["FilingDate"],url,raw_xml,status,Json(details),filing_id))
                            counts.inserted += 1
                        counts.rejected += bool(errors); counts.warnings += len(warnings)
                        if number % self.progress_every == 0 or number == len(rows):
                            print(f"  {number:,}/{len(rows):,} inserted={counts.inserted:,} updated={counts.updated:,} rejected={counts.rejected:,} warnings={counts.warnings:,}",flush=True)
                    self.finish_import(cur,import_id,counts,"partially_complete" if counts.rejected else "complete")
                except Exception as exc:
                    self.finish_import(cur,import_id,counts,"failed",str(exc)); raise
        print(f"  Completed {path.name} in {time.monotonic()-started:.2f}s",flush=True)
        return counts

def build_parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(description="Load canonical House financial-disclosure XML indexes")
    p.add_argument("--source-dir",type=Path,default=project_root()/"data"/"raw"/"houseofreptrans")
    p.add_argument("--files",nargs="+")
    mode=p.add_mutually_exclusive_group()
    mode.add_argument("--import",dest="do_import",action="store_true",help="Import validated XML records into PostgreSQL")
    mode.add_argument("--validate-only",action="store_true",help="Validate source files without connecting to PostgreSQL")
    p.add_argument("--progress-every",type=int,default=250); p.add_argument("--current-year",type=int,default=datetime.now().year)
    p.add_argument("--skip-member-matching",action="store_true"); p.add_argument("--database-url")
    return p

def parse_args(argv: list[str] | None=None) -> argparse.Namespace:
    return build_parser().parse_args(argv)

def main(argv: list[str] | None=None) -> int:
    raw_args=list(sys.argv[1:] if argv is None else argv)
    parser=build_parser()
    if not raw_args:
        parser.print_help()
        return 0
    args=parser.parse_args(raw_args)
    if not args.do_import and not args.validate_only:
        parser.error("select one operation: --import or --validate-only")
    started=datetime.now().astimezone(); clock=time.monotonic()
    print(f"House filing import start: {started.isoformat()}"); print(f"Source directory: {args.source_dir.resolve()}")
    try:
        paths=discover(args.source_dir.resolve(),args.files,args.current_year)
        print(f"Files discovered: {len(paths)} ({paths[0].name} through {paths[-1].name})")
        total_rows=0
        for path in paths:
            rows=parse_xml(path); total_rows+=len(rows); print(f"  Valid {path.name}: {len(rows):,} rows")
        if args.validate_only:
            print(f"Validation complete: {total_rows:,} source rows; no database connection made"); return 0
        if args.progress_every < 1: raise ValueError("--progress-every must be positive")
        conn=psycopg2.connect(database_url(args.database_url))
        try:
            total=Counts(); loader=Loader(conn,args.progress_every,not args.skip_member_matching)
            for path in paths: total.add(loader.load_file(path))
        finally: conn.close()
        print(f"Totals: read={total.read:,} inserted={total.inserted:,} updated={total.updated:,} rejected={total.rejected:,} warnings={total.warnings:,}")
        return 1 if total.rejected else 0
    except (OSError,ValueError,ET.ParseError,psycopg2.Error,RuntimeError) as exc:
        print(f"House filing import failed: {exc}",file=sys.stderr); return 2
    finally:
        print(f"House filing import end: {datetime.now().astimezone().isoformat()}"); print(f"Elapsed: {time.monotonic()-clock:.2f}s")

if __name__ == "__main__": raise SystemExit(main())
