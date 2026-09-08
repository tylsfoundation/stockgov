"""Independently validate House XML filing imports against PostgreSQL.

This read-only program does not import data and does not call importer code.
It compares every raw XML row, aggregate counts, normalized filings, member
matches, relationships, and deterministic samples.

Requirements: ``py -m pip install psycopg2-binary``
Example: ``py scripts/validate_house_filings.py``
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlunsplit

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError as exc:
    raise SystemExit("psycopg2-binary is required: py -m pip install psycopg2-binary") from exc

FILE_RE=re.compile(r"^(\d{4})FD\.xml$")
TAGS=("Prefix","Last","First","Suffix","FilingType","StateDst","Year","FilingDate","DocID")
TYPE_MAP={"A":"amendment","B":"blind_trust","C":"candidate_report","D":"candidate_threshold_declaration",
          "E":"termination_exemption","G":"gift_waiver","H":"new_filer","O":"annual_disclosure","P":"ptr",
          "R":"ptr_waiver","T":"termination","W":"candidate_withdrawal","X":"extension"}
SOURCE="house_clerk_financial_disclosure"

def project_root()->Path: return Path(__file__).resolve().parent.parent

class Tee:
    def __init__(self,path:Path):
        path.parent.mkdir(parents=True,exist_ok=True); self.file=path.open("w",encoding="utf-8")
    def write(self,msg:str="")->None: print(msg); print(msg,file=self.file); self.file.flush()
    def close(self)->None: self.file.close()

@dataclass
class Result:
    passed:int=0; warnings:int=0; failed:int=0

class Check:
    def __init__(self,out:Tee): self.out=out; self.result=Result()
    def ok(self,msg:str)->None: self.result.passed+=1; self.out.write(f"  PASS: {msg}")
    def warn(self,msg:str)->None: self.result.warnings+=1; self.out.write(f"  WARN: {msg}")
    def fail(self,msg:str)->None: self.result.failed+=1; self.out.write(f"  FAIL: {msg}")
    def test(self,condition:bool,good:str,bad:str)->None: self.ok(good) if condition else self.fail(bad)

def load_dotenv()->None:
    p=project_root()/".env"
    if not p.exists(): return
    for raw in p.read_text(encoding="utf-8-sig").splitlines():
        line=raw.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k,v=line.split("=",1); os.environ.setdefault(k.strip(),v.strip().strip('"').strip("'"))

def db_url(override:str|None)->str:
    load_dotenv()
    if override:return override
    if os.getenv("DATABASE_URL"):return os.environ["DATABASE_URL"]
    if not os.getenv("POSTGRES_USER") or not os.getenv("POSTGRES_PASSWORD"):raise RuntimeError("Database credentials are not configured")
    auth=f"{quote(os.environ['POSTGRES_USER'])}:{quote(os.environ['POSTGRES_PASSWORD'])}@{os.getenv('POSTGRES_HOST','localhost')}:{os.getenv('POSTGRES_PORT','5433')}"
    return urlunsplit(("postgresql",auth,"/"+os.getenv("POSTGRES_DB","congress_trades"),"",""))

def sha256(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):h.update(chunk)
    return h.hexdigest()

def value(node:ET.Element,tag:str)->str:
    c=node.find(tag); return "" if c is None or c.text is None else c.text.strip()

def url(rec:dict[str,str])->str:
    folder="ptr-pdfs" if rec["FilingType"]=="P" else "financial-pdfs"
    return f"https://disclosures-clerk.house.gov/public_disc/{folder}/{rec['Year']}/{rec['DocID']}.pdf"

def state_district(raw:str)->tuple[str|None,int|None,bool]:
    m=re.fullmatch(r"([A-Z]{2})(\d{2})",raw)
    return (m.group(1),int(m.group(2)),True) if m else (None,None,False)

def read_sources(source_dir:Path,current_year:int,out:Tee)->tuple[dict[str,list[dict[str,str]]],dict[str,str]]:
    paths=sorted(source_dir.glob("*FD.xml"),key=lambda p:p.name)
    expected={f"{y}FD.xml" for y in range(2008,current_year+1)}
    actual={p.name for p in paths}
    missing=sorted(expected-actual); extra=sorted(actual-expected)
    if missing: raise ValueError("Missing annual XML files: "+", ".join(missing))
    if extra: raise ValueError("Unexpected annual XML files: "+", ".join(extra))
    data={}; hashes={}
    for path in paths:
        root=ET.parse(path).getroot()
        if root.tag!="FinancialDisclosure":raise ValueError(f"{path.name}: invalid root {root.tag}")
        records=[]
        for number,node in enumerate(root.findall("Member"),1):
            tags=[c.tag for c in node]
            if set(tags)!=set(TAGS) or len(tags)!=len(TAGS):raise ValueError(f"{path.name} row {number}: unexpected fields")
            rec={tag:value(node,tag) for tag in TAGS}; rec["_row"]=str(number)
            rec["_xml"]=ET.tostring(node,encoding="unicode",short_empty_elements=True); rec["_url"]=url(rec) if rec["DocID"] else ""
            records.append(rec)
        data[path.name]=records; hashes[path.name]=sha256(path); out.write(f"  {path.name}: {len(records):,} rows")
    return data,hashes

def rows(cur:Any,sql:str,params:tuple[Any,...]=())->list[dict[str,Any]]:
    cur.execute(sql,params); return list(cur.fetchall())

def normalize_name(x:str)->str:
    x=unicodedata.normalize("NFKD",x).encode("ascii","ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9 ]+"," ",x.lower()).split())

def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description="Validate House filing XML imports")
    p.add_argument("--source-dir",type=Path,default=project_root()/"data"/"raw"/"houseofreptrans")
    p.add_argument("--log-file",type=Path,default=project_root()/"logs"/"house_filings_qa.log")
    p.add_argument("--current-year",type=int,default=datetime.now().year); p.add_argument("--database-url"); p.add_argument("--progress-every",type=int,default=250)
    a=p.parse_args(argv); out=Tee(a.log_file); check=Check(out); started=datetime.now().astimezone(); clock=time.monotonic(); exit_code=2
    out.write("="*72); out.write(f"House filing QA start time: {started.isoformat()}"); out.write(f"Source directory: {a.source_dir.resolve()}")
    try:
        out.write("\n[01] Parse canonical source XML files")
        source,hashes=read_sources(a.source_dir.resolve(),a.current_year,out); check.ok(f"All {len(source)} annual XML files parsed")
        raw_total=sum(map(len,source.values())); raw_types=Counter(r["FilingType"] for rs in source.values() for r in rs)
        raw_ptr={name[:4]:sum(r["FilingType"]=="P" for r in rs) for name,rs in source.items()}
        expected_latest={}
        for name in sorted(source):
            for r in source[name]: expected_latest[r["DocID"]]=r

        conn=psycopg2.connect(db_url(a.database_url),cursor_factory=RealDictCursor); conn.set_session(readonly=True,autocommit=False)
        try:
            with conn.cursor() as cur:
                out.write("\n[02] Verify schema")
                required={"filing_source_occurrences","staging_house_filings","filings","member_match_candidates"}
                found={r["table_name"] for r in rows(cur,"SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name=ANY(%s)",(list(required),))}
                check.test(found==required,"Required House filing tables exist",f"Missing tables: {sorted(required-found)}")
                cols={r["column_name"] for r in rows(cur,"SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='staging_house_filings'")}
                need={"prefix_raw","suffix_raw","state_district_raw"}; check.test(need<=cols,"Raw prefix, suffix, and StateDst columns exist",f"Missing staging columns: {sorted(need-cols)}")

                out.write("\n[03] Verify source snapshots and hashes")
                snaps=rows(cur,"SELECT source_snapshot_id,source_name,content_hash FROM source_snapshots WHERE source_type='house_financial_disclosure_xml'")
                by_name=defaultdict(list)
                for r in snaps:by_name[r["source_name"]].append(r)
                selected_snap={}
                for name,digest in hashes.items():
                    matches=[r for r in by_name[name] if r["content_hash"]==digest]
                    if len(matches)==1: selected_snap[name]=matches[0]["source_snapshot_id"]
                    else: check.fail(f"{name}: expected one matching snapshot hash, found {len(matches)}")
                if len(selected_snap)==len(source):check.ok("All source snapshot hashes match")

                out.write("\n[04] Compare every XML row with staging")
                stage_by_file={}; all_stage=[]
                for name,expected in source.items():
                    sid=selected_snap.get(name)
                    if sid is None:continue
                    got=rows(cur,"""SELECT DISTINCT ON (s.source_row_number) s.* FROM staging_house_filings s
                        JOIN source_imports i ON i.source_import_id=s.source_import_id WHERE i.source_snapshot_id=%s
                        ORDER BY s.source_row_number,s.staging_house_filing_id DESC""",(sid,))
                    stage_by_file[name]={r["source_row_number"]:r for r in got}; all_stage.extend(got)
                    check.test(len(got)==len(expected),f"{name}: staging count {len(got):,}",f"{name}: raw={len(expected):,}, staging={len(got):,}")
                    mismatches=[]
                    for rec in expected:
                        n=int(rec["_row"]); db=stage_by_file[name].get(n)
                        if not db:mismatches.append((n,"missing"));continue
                        comparisons={"doc_id_raw":rec["DocID"],"reporting_year_raw":rec["Year"],"filing_type_code_raw":rec["FilingType"],
                            "prefix_raw":rec["Prefix"],"first_name_raw":rec["First"],"last_name_raw":rec["Last"],"suffix_raw":rec["Suffix"],
                            "state_district_raw":rec["StateDst"],"filed_date_raw":rec["FilingDate"],"document_url_raw":rec["_url"],"raw_xml":rec["_xml"]}
                        bad=[k for k,v in comparisons.items() if (db.get(k) or "")!=v]
                        if bad:mismatches.append((n,",".join(bad)))
                    check.test(not mismatches,f"{name}: every staging field matches XML",f"{name}: {len(mismatches)} row mismatches; first={mismatches[:3]}")
                check.test(len(all_stage)==raw_total,f"Total staging count matches {raw_total:,} raw rows",f"Total staging={len(all_stage):,}, raw={raw_total:,}")

                out.write("\n[05] Verify normalized filing catalog")
                filings=rows(cur,"SELECT * FROM filings WHERE source=%s",(SOURCE,)); by_doc={r["source_filing_id"]:r for r in filings}
                check.test(len(filings)==len(expected_latest),f"Normalized unique DocID count {len(filings):,}",f"Normalized={len(filings):,}, expected unique DocIDs={len(expected_latest):,}")
                bad=[]; blank_dates=0; malformed=0
                for doc,rec in expected_latest.items():
                    db=by_doc.get(doc)
                    if not db:bad.append((doc,"missing"));continue
                    state,district,valid=state_district(rec["StateDst"])
                    if not rec["FilingDate"]: blank_dates+=1
                    if rec["StateDst"] and not valid: malformed+=1
                    expected_date=datetime.strptime(rec["FilingDate"],"%m/%d/%Y").date() if rec["FilingDate"] else None
                    values=[db["filing_type_code_raw"]==rec["FilingType"],db["filing_type"]==TYPE_MAP.get(rec["FilingType"],"unknown"),
                            db["reporting_year"]==int(rec["Year"]),db["filed_date"]==expected_date,db["state_code_guess"]==state,
                            db["district_guess"]==district,db["source_url"]==rec["_url"]]
                    if not all(values):bad.append((doc,"normalized values"))
                check.test(not bad,"Every normalized filing matches its latest source occurrence",f"Normalized filing mismatches: {len(bad)}; first={bad[:5]}")
                check.ok(f"Verified {blank_dates:,} unique filings with blank dates remain nullable")
                if malformed:check.ok(f"Verified {malformed:,} malformed nonblank StateDst values remain unparsed")

                out.write("\n[06] Verify occurrences, duplicates, types, and PTR totals")
                occurrences=rows(cur,"""SELECT o.source_snapshot_id,o.source_row_number,o.filing_id,o.index_year
                    FROM filing_source_occurrences o JOIN source_snapshots s ON s.source_snapshot_id=o.source_snapshot_id
                    WHERE s.source_type='house_financial_disclosure_xml'""")
                check.test(len(occurrences)==raw_total,f"Occurrence count preserves all {raw_total:,} source rows",f"Occurrences={len(occurrences):,}, raw={raw_total:,}")
                duplicate_rows=raw_total-len(expected_latest); check.ok(f"Duplicate source appearances preserved: {duplicate_rows:,}")
                db_types=Counter(r["filing_type_code_raw"] for r in filings)
                latest_types=Counter(r["FilingType"] for r in expected_latest.values())
                check.test(db_types==latest_types,f"Normalized filing-type counts match {dict(sorted(db_types.items()))}",f"Filing-type counts differ: db={db_types}, expected={latest_types}")
                for name,records in source.items():
                    year=name[:4]; sid=selected_snap.get(name)
                    if sid is None:continue
                    db_ptr=rows(cur,"""SELECT count(*) AS n FROM staging_house_filings s JOIN source_imports i ON i.source_import_id=s.source_import_id
                        WHERE i.source_snapshot_id=%s AND s.filing_type_code_raw='P'""",(sid,))[0]["n"]
                    check.test(db_ptr==raw_ptr[year],f"{year}: PTR count {db_ptr:,}",f"{year}: PTR db={db_ptr:,}, raw={raw_ptr[year]:,}")

                out.write("\n[07] Verify relationships and member matching")
                orphan_queries={
                    "staging filings/filing":"SELECT count(*) n FROM staging_house_filings s LEFT JOIN filings f ON f.filing_id=s.filing_id WHERE s.filing_id IS NOT NULL AND f.filing_id IS NULL",
                    "occurrences/filing":"SELECT count(*) n FROM filing_source_occurrences o LEFT JOIN filings f ON f.filing_id=o.filing_id WHERE f.filing_id IS NULL",
                    "match candidates":"SELECT count(*) n FROM member_match_candidates c LEFT JOIN filings f ON f.filing_id=c.filing_id LEFT JOIN members m ON m.member_id=c.candidate_member_id WHERE f.filing_id IS NULL OR m.member_id IS NULL",
                    "documents":"SELECT count(*) n FROM documents d LEFT JOIN filings f ON f.filing_id=d.filing_id WHERE f.filing_id IS NULL",
                    "filing selections":"SELECT count(*) n FROM filing_selections s LEFT JOIN filings f ON f.filing_id=s.filing_id WHERE f.filing_id IS NULL"}
                for label,sql in orphan_queries.items():
                    n=rows(cur,sql)[0]["n"];check.test(n==0,f"No orphaned {label}",f"Orphaned {label}: {n}")
                invalid=rows(cur,"""SELECT count(*) n FROM filings f WHERE f.source=%s AND
                    ((f.member_match_status IN ('unmatched','ambiguous','rejected') AND f.member_id IS NOT NULL)
                    OR (f.member_match_status='automatically_matched' AND NOT EXISTS
                       (SELECT 1 FROM member_terms t WHERE t.member_id=f.member_id AND t.chamber='house'
                        AND t.state_code=f.state_code_guess AND (f.district_guess IS NULL OR t.district_number=f.district_guess)
                        AND EXTRACT(YEAR FROM t.term_start_date)<=f.reporting_year AND EXTRACT(YEAR FROM t.term_end_date)>=f.reporting_year)))""",(SOURCE,))[0]["n"]
                check.test(invalid==0,"Member-match statuses and House terms are consistent",f"Invalid member matches: {invalid}")

                out.write("\n[08] Deterministic sample comparisons")
                for name,records in source.items():
                    chosen={0,len(records)//2,len(records)-1}
                    for predicate in (lambda r:r["FilingType"]=="P",lambda r:not r["FilingDate"],lambda r:not state_district(r["StateDst"])[2]):
                        idx=next((i for i,r in enumerate(records) if predicate(r)),None)
                        if idx is not None:chosen.add(idx)
                    dup=Counter(r["DocID"] for r in records);chosen.update(i for i,r in enumerate(records) if dup[r["DocID"]]>1)
                    for idx in sorted(chosen):
                        rec=records[idx];db=stage_by_file.get(name,{}).get(idx+1); filing=by_doc.get(rec["DocID"])
                        passed=bool(db and filing and (db.get("doc_id_raw") or "")==rec["DocID"] and (db.get("raw_xml") or "")==rec["_xml"])
                        member=f"{filing['member_id']}" if filing and filing["member_id"] else "unmatched"
                        out.write(f"  {'PASS' if passed else 'FAIL'} {name[:4]} row={idx+1} DocID={rec['DocID']} filer={rec['First']} {rec['Last']} type={rec['FilingType']} StateDst={rec['StateDst'] or '<blank>'} date={rec['FilingDate'] or '<blank>'} staging_id={db['staging_house_filing_id'] if db else '<missing>'} filing_id={filing['filing_id'] if filing else '<missing>'} member={member}")
                        if passed:check.result.passed+=1
                        else:check.result.failed+=1
        finally: conn.rollback();conn.close()
        exit_code=1 if check.result.failed else 0
    except (OSError,ValueError,ET.ParseError,RuntimeError,psycopg2.Error) as exc:
        out.write(f"\nCONFIGURATION OR EXECUTION ERROR: {exc}"); exit_code=2
    finally:
        elapsed=time.monotonic()-clock;out.write("\n[09] QA summary");out.write(f"  Passed checks : {check.result.passed}");out.write(f"  Warnings      : {check.result.warnings}");out.write(f"  Failed checks : {check.result.failed}")
        out.write(f"  RESULT: {'PASSED' if exit_code==0 else 'FAILED' if exit_code==1 else 'ERROR'}");out.write(f"QA end time: {datetime.now().astimezone().isoformat()}");out.write(f"Elapsed time: {elapsed:.2f} seconds");out.write(f"Exit status: {exit_code}");out.write("="*72);out.close()
    return exit_code

if __name__=="__main__":raise SystemExit(main())
