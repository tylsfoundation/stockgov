"""Select and download House financial-disclosure PDFs cataloged in StockGov.

The program requires ``--all`` or at least one filing filter. With no arguments
it prints usage and exits. ``--dry-run`` performs a read-only selection preview.
Completed, verified primary documents are skipped, so interrupted runs can be
resumed safely.

Requirements: ``py -m pip install psycopg2-binary``

Examples:
    py scripts/download_house_documents.py --year 2025 --filing-type P --limit 10 --dry-run
    py scripts/download_house_documents.py --member "Nancy Pelosi" --filing-type P
    py scripts/download_house_documents.py --state NH --from-year 2012 --to-year 2026 --filing-type P
    py scripts/download_house_documents.py --all --delay 2
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlunsplit

try:
    import psycopg2
    from psycopg2.extras import Json, RealDictCursor
except ImportError as exc:
    raise SystemExit("psycopg2-binary is required: py -m pip install psycopg2-binary") from exc

SOURCE = "house_clerk_financial_disclosure"
USER_AGENT = "StockGov/1.0 congressional-disclosure-research"
DOC_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504}


@dataclass
class Totals:
    selected: int = 0
    skipped: int = 0
    downloaded: int = 0
    retryable: int = 0
    permanent: int = 0


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_dotenv() -> None:
    path = project_root() / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def database_url(override: str | None) -> str:
    load_dotenv()
    if override:
        return override
    configured = os.getenv("DATABASE_URL")
    if configured:
        return configured
    if not os.getenv("POSTGRES_USER") or not os.getenv("POSTGRES_PASSWORD"):
        raise RuntimeError("Set DATABASE_URL or POSTGRES_USER and POSTGRES_PASSWORD in the project .env file")
    authority = (
        f"{quote(os.environ['POSTGRES_USER'])}:{quote(os.environ['POSTGRES_PASSWORD'])}"
        f"@{os.getenv('POSTGRES_HOST', 'localhost')}:{os.getenv('POSTGRES_PORT', '5433')}"
    )
    return urlunsplit(("postgresql", authority, "/" + os.getenv("POSTGRES_DB", "congress_trades"), "", ""))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download selected House disclosure PDFs from the filing catalog")
    parser.add_argument("--all", action="store_true", help="Select every eligible House filing")
    parser.add_argument("--member", help="Filter by matched member preferred or stored name")
    parser.add_argument("--bioguide", help="Filter by exact member Bioguide ID")
    parser.add_argument("--state", help="Filter by two-letter state code")
    parser.add_argument("--year", type=int, help="Filter by one reporting year")
    parser.add_argument("--from-year", type=int, help="Inclusive starting reporting year")
    parser.add_argument("--to-year", type=int, help="Inclusive ending reporting year")
    parser.add_argument("--filing-type", help="Raw House filing code such as P, O, A, or X")
    parser.add_argument("--limit", type=int, help="Maximum number of eligible filings")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between HTTP requests; default 1.0")
    parser.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout in seconds; default 60")
    parser.add_argument("--max-attempts", type=int, default=3, help="Maximum attempts per job; default 3")
    parser.add_argument("--retry-failed", action="store_true", help="Requeue existing retryable download jobs")
    parser.add_argument("--dry-run", action="store_true", help="Preview selection without changing the database or filesystem")
    parser.add_argument("--output-dir", type=Path, default=project_root() / "data" / "raw" / "house_documents")
    parser.add_argument("--database-url", help=argparse.SUPPRESS)
    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    filters = (args.member, args.bioguide, args.state, args.year, args.from_year, args.to_year, args.filing_type)
    if not args.all and not any(value is not None for value in filters):
        parser.error("specify --all or at least one member, state, year, or filing-type filter")
    if args.all and any(value is not None for value in filters):
        parser.error("--all cannot be combined with selection filters")
    if args.year is not None and (args.from_year is not None or args.to_year is not None):
        parser.error("--year cannot be combined with --from-year or --to-year")
    if args.from_year is not None and args.to_year is not None and args.from_year > args.to_year:
        parser.error("--from-year cannot be greater than --to-year")
    if args.state:
        args.state = args.state.upper()
        if not re.fullmatch(r"[A-Z]{2}", args.state):
            parser.error("--state must be a two-letter code")
    if args.filing_type:
        args.filing_type = args.filing_type.upper()
        if not re.fullmatch(r"[A-Z]", args.filing_type):
            parser.error("--filing-type must be a one-letter House filing code")
    for label, value in (("--year", args.year), ("--from-year", args.from_year), ("--to-year", args.to_year)):
        if value is not None and not 1900 <= value <= datetime.now().year:
            parser.error(f"{label} must be between 1900 and {datetime.now().year}")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.delay < 0 or args.timeout <= 0 or args.max_attempts < 1:
        parser.error("--delay must be nonnegative; --timeout and --max-attempts must be positive")


def select_filings(cursor: Any, args: argparse.Namespace) -> list[dict[str, Any]]:
    clauses = [
        "f.source = %s", "f.chamber = 'house'", "f.source_url IS NOT NULL",
        "NOT EXISTS (SELECT 1 FROM documents d WHERE d.filing_id=f.filing_id AND d.is_primary AND d.verification_status='verified')",
    ]
    params: list[Any] = [SOURCE]
    if args.member:
        clauses.append("(m.preferred_name ILIKE %s OR EXISTS (SELECT 1 FROM member_names mn WHERE mn.member_id=m.member_id AND mn.full_name ILIKE %s))")
        params.extend([f"%{args.member}%", f"%{args.member}%"])
    if args.bioguide:
        clauses.append("EXISTS (SELECT 1 FROM member_identifiers mi WHERE mi.member_id=m.member_id AND mi.identifier_type='bioguide' AND mi.identifier_value=%s)")
        params.append(args.bioguide)
    if args.state:
        clauses.append("f.state_code_guess=%s"); params.append(args.state)
    if args.year is not None:
        clauses.append("f.reporting_year=%s"); params.append(args.year)
    if args.from_year is not None:
        clauses.append("f.reporting_year>=%s"); params.append(args.from_year)
    if args.to_year is not None:
        clauses.append("f.reporting_year<=%s"); params.append(args.to_year)
    if args.filing_type:
        clauses.append("f.filing_type_code_raw=%s"); params.append(args.filing_type)
    sql = f"""SELECT f.filing_id,f.source_filing_id AS doc_id,f.reporting_year,f.filing_type_code_raw,
                     f.raw_full_name,f.state_code_guess,f.district_guess,f.source_url,f.member_id,m.preferred_name
              FROM filings f LEFT JOIN members m ON m.member_id=f.member_id
              WHERE {' AND '.join(clauses)} ORDER BY f.reporting_year,f.source_filing_id"""
    if args.limit is not None:
        sql += " LIMIT %s"; params.append(args.limit)
    cursor.execute(sql, tuple(params))
    return list(cursor.fetchall())


def batch_filter(args: argparse.Namespace) -> dict[str, Any]:
    return {key: value for key, value in {
        "all": args.all, "member": args.member, "bioguide": args.bioguide, "state": args.state,
        "year": args.year, "from_year": args.from_year, "to_year": args.to_year,
        "filing_type": args.filing_type, "limit": args.limit,
    }.items() if value not in (None, False)}


def create_batch(cursor: Any, args: argparse.Namespace, count: int) -> int:
    name = "House document download " + datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    cursor.execute("""INSERT INTO selection_batches
        (batch_name,requested_by,filter_definition,status,started_at,filings_selected)
        VALUES (%s,%s,%s,'running',CURRENT_TIMESTAMP,%s) RETURNING selection_batch_id""",
        (name, os.getenv("USERNAME") or os.getenv("USER") or "local_user", Json(batch_filter(args)), count))
    return cursor.fetchone()["selection_batch_id"]


def ensure_selection_and_job(cursor: Any, filing: dict[str, Any], batch_id: int, args: argparse.Namespace) -> tuple[int | None, bool]:
    cursor.execute("""INSERT INTO filing_selections
        (filing_id,selection_batch_id,selection_reason,priority,selected_by)
        VALUES (%s,%s,%s,100,%s) ON CONFLICT (filing_id,selection_batch_id) DO UPDATE SET is_active=TRUE
        RETURNING filing_selection_id""",
        (filing["filing_id"], batch_id, str(batch_filter(args)), os.getenv("USERNAME") or "local_user"))
    cursor.fetchone()
    cursor.execute("""SELECT document_job_id,status FROM document_jobs
        WHERE filing_id=%s AND job_type='download' AND status IN ('queued','running','failed_retryable')
        ORDER BY document_job_id DESC LIMIT 1""", (filing["filing_id"],))
    existing = cursor.fetchone()
    if existing:
        if existing["status"] == "failed_retryable" and args.retry_failed:
            cursor.execute("""UPDATE document_jobs SET status='queued',next_attempt_at=NULL,max_attempts=%s,
                error_type=NULL,error_message=NULL WHERE document_job_id=%s RETURNING document_job_id""",
                (args.max_attempts, existing["document_job_id"]))
            return cursor.fetchone()["document_job_id"], True
        return existing["document_job_id"], existing["status"] == "queued"
    cursor.execute("""INSERT INTO document_jobs (filing_id,job_type,status,max_attempts)
        VALUES (%s,'download','queued',%s) RETURNING document_job_id""", (filing["filing_id"], args.max_attempts))
    return cursor.fetchone()["document_job_id"], True


def safe_destination(base: Path, filing: dict[str, Any]) -> Path:
    docid = str(filing["doc_id"])
    if not DOC_ID_RE.fullmatch(docid):
        raise ValueError(f"Unsafe DocID: {docid!r}")
    year = int(filing["reporting_year"])
    category = "ptr" if filing["filing_type_code_raw"] == "P" else "financial"
    destination = (base.resolve() / str(year) / category / f"{docid}.pdf").resolve()
    if base.resolve() not in destination.parents:
        raise ValueError(f"Destination escaped output directory: {destination}")
    return destination


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_pdf(url: str, destination: Path, timeout: float) -> tuple[int, int, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    if partial.exists():
        partial.unlink()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/pdf"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, partial.open("wb") as output:
            status = int(response.status)
            content_type = response.headers.get_content_type()
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
        with partial.open("rb") as stream:
            signature = stream.read(5)
        if signature != b"%PDF-":
            partial.unlink(missing_ok=True)
            raise ValueError(f"Response is not a PDF (Content-Type {content_type})")
        size = partial.stat().st_size
        content_hash = sha256(partial)
        partial.replace(destination)
        return status, size, content_hash
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def mark_running(cursor: Any, job_id: int) -> int:
    cursor.execute("""UPDATE document_jobs SET status='running',started_at=CURRENT_TIMESTAMP,finished_at=NULL,
        attempt_count=attempt_count+1,error_type=NULL,error_message=NULL WHERE document_job_id=%s
        AND status IN ('queued','failed_retryable') AND attempt_count<max_attempts RETURNING attempt_count""", (job_id,))
    row = cursor.fetchone()
    if not row:
        raise RuntimeError(f"Job {job_id} is not eligible to run")
    return row["attempt_count"]


def mark_failure(connection: Any, job_id: int, retryable: bool, error: Exception, attempt: int, max_attempts: int) -> str:
    status = "failed_retryable" if retryable and attempt < max_attempts else "failed_permanent"
    next_attempt = datetime.now(timezone.utc) + timedelta(minutes=min(60, 2 ** attempt)) if status == "failed_retryable" else None
    with connection:
        with connection.cursor() as cursor:
            cursor.execute("""UPDATE document_jobs SET status=%s,finished_at=CURRENT_TIMESTAMP,next_attempt_at=%s,
                error_type=%s,error_message=%s WHERE document_job_id=%s""",
                (status, next_attempt, type(error).__name__, str(error)[:4000], job_id))
    return status


def record_success(connection: Any, filing: dict[str, Any], job_id: int, destination: Path,
                   status: int, size: int, content_hash: str) -> None:
    with connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE documents SET is_primary=FALSE WHERE filing_id=%s AND is_primary", (filing["filing_id"],))
            cursor.execute("""INSERT INTO documents
                (filing_id,document_type,source_url,local_path,mime_type,file_size_bytes,content_hash,downloaded_at,
                 http_status,is_primary,verification_status)
                VALUES (%s,'house_financial_disclosure_pdf',%s,%s,'application/pdf',%s,%s,CURRENT_TIMESTAMP,%s,TRUE,'verified')
                ON CONFLICT (filing_id,content_hash) DO UPDATE SET source_url=EXCLUDED.source_url,local_path=EXCLUDED.local_path,
                    file_size_bytes=EXCLUDED.file_size_bytes,downloaded_at=EXCLUDED.downloaded_at,http_status=EXCLUDED.http_status,
                    is_primary=TRUE,verification_status='verified' RETURNING document_id""",
                (filing["filing_id"], filing["source_url"], str(destination), size, content_hash, status))
            document_id = cursor.fetchone()["document_id"]
            cursor.execute("""UPDATE document_jobs SET document_id=%s,status='complete',finished_at=CURRENT_TIMESTAMP,
                next_attempt_at=NULL,error_type=NULL,error_message=NULL WHERE document_job_id=%s""", (document_id, job_id))
            cursor.execute("UPDATE filings SET processing_status='downloaded' WHERE filing_id=%s", (filing["filing_id"],))


def finish_batch(connection: Any, batch_id: int, completed: int, failed: bool) -> None:
    with connection:
        with connection.cursor() as cursor:
            cursor.execute("""UPDATE selection_batches SET finished_at=CURRENT_TIMESTAMP,filings_completed=%s,status=%s
                WHERE selection_batch_id=%s""", (completed, "partially_complete" if failed else "complete", batch_id))


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not raw_args:
        parser.print_help()
        return 0
    args = parser.parse_args(raw_args)
    validate_args(parser, args)
    started = datetime.now().astimezone(); clock = time.monotonic(); totals = Totals()
    print(f"House document download start: {started.isoformat()}")
    try:
        connection = psycopg2.connect(database_url(args.database_url), cursor_factory=RealDictCursor)
        try:
            if args.dry_run:
                connection.set_session(readonly=True)
                with connection.cursor() as cursor:
                    filings = select_filings(cursor, args)
                totals.selected = len(filings)
                print(f"Eligible filings: {totals.selected:,}")
                for filing in filings:
                    print(f"  {filing['reporting_year']} {filing['filing_type_code_raw']} DocID={filing['doc_id']} filer={filing['preferred_name'] or filing['raw_full_name']} url={filing['source_url']}")
                print("Dry run complete; no database or filesystem changes were made")
                return 0

            with connection:
                with connection.cursor() as cursor:
                    filings = select_filings(cursor, args); totals.selected = len(filings)
                    batch_id = create_batch(cursor, args, totals.selected)
                    runnable = []
                    for filing in filings:
                        job_id, should_run = ensure_selection_and_job(cursor, filing, batch_id, args)
                        if should_run and job_id is not None:
                            runnable.append((filing, job_id))
                        else:
                            totals.skipped += 1
            print(f"Eligible filings: {totals.selected:,}; runnable jobs: {len(runnable):,}; skipped active jobs: {totals.skipped:,}")
            for number, (filing, job_id) in enumerate(runnable, 1):
                destination = safe_destination(args.output_dir, filing)
                with connection:
                    with connection.cursor() as cursor:
                        attempt = mark_running(cursor, job_id)
                print(f"[{number:,}/{len(runnable):,}] {filing['reporting_year']} {filing['filing_type_code_raw']} DocID={filing['doc_id']} attempt={attempt}", flush=True)
                try:
                    http_status, size, content_hash = download_pdf(filing["source_url"], destination, args.timeout)
                    record_success(connection, filing, job_id, destination, http_status, size, content_hash)
                    totals.downloaded += 1
                    print(f"  saved {destination} ({size:,} bytes, sha256={content_hash[:12]}...)", flush=True)
                except urllib.error.HTTPError as exc:
                    state = mark_failure(connection, job_id, exc.code in RETRYABLE_HTTP, exc, attempt, args.max_attempts)
                    if state == "failed_retryable": totals.retryable += 1
                    else: totals.permanent += 1
                    print(f"  {state}: HTTP {exc.code} {exc.reason}", flush=True)
                except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                    state = mark_failure(connection, job_id, True, exc, attempt, args.max_attempts)
                    if state == "failed_retryable": totals.retryable += 1
                    else: totals.permanent += 1
                    print(f"  {state}: {exc}", flush=True)
                except (OSError, ValueError, RuntimeError) as exc:
                    mark_failure(connection, job_id, False, exc, attempt, args.max_attempts); totals.permanent += 1
                    print(f"  failed_permanent: {exc}", flush=True)
                if number < len(runnable) and args.delay:
                    time.sleep(args.delay)
            finish_batch(connection, batch_id, totals.downloaded, bool(totals.retryable or totals.permanent or totals.skipped))
        finally:
            connection.close()
        print(f"Totals: selected={totals.selected:,} downloaded={totals.downloaded:,} skipped={totals.skipped:,} retryable={totals.retryable:,} permanent={totals.permanent:,}")
        return 1 if totals.retryable or totals.permanent else 0
    except KeyboardInterrupt:
        print("Download interrupted; completed jobs are preserved and the run can be resumed", file=sys.stderr)
        return 130
    except (RuntimeError, OSError, psycopg2.Error) as exc:
        print(f"House document download failed: {exc}", file=sys.stderr)
        return 2
    finally:
        print(f"House document download end: {datetime.now().astimezone().isoformat()}")
        print(f"Elapsed: {time.monotonic()-clock:.2f}s")


if __name__ == "__main__":
    raise SystemExit(main())
