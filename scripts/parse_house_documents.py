"""Extract House PTR PDFs and load normalized transactions into StockGov.

The first implementation targets House Periodic Transaction Reports (filing
code ``P``).  It extracts text with pypdf, stores the versioned extraction in
``document_extractions``, and inserts one normalized row per transaction into
``trades``.  Jobs and parser versions make interrupted runs safe to resume.

Examples::

    py scripts/parse_house_documents.py --year 2025 --limit 10 --dry-run
    py scripts/parse_house_documents.py --year 2025 --limit 10
    py scripts/parse_house_documents.py --docid 20016861
    py scripts/parse_house_documents.py --all --retry-failed

Requirements: ``py -m pip install psycopg2-binary pypdf``
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, urlunsplit

try:
    import psycopg2
    from psycopg2.extras import Json, RealDictCursor
except ImportError as exc:  # pragma: no cover - exercised by command-line users
    raise SystemExit(
        "psycopg2-binary is required: py -m pip install psycopg2-binary pypdf"
    ) from exc

try:
    from pypdf import PdfReader
except ImportError as exc:  # pragma: no cover - exercised by command-line users
    raise SystemExit("pypdf is required: py -m pip install pypdf") from exc


SOURCE = "house_clerk_financial_disclosure"
PARSER_NAME = "house_ptr_pdf"
PARSER_VERSION = "1.0.0"
EXTRACTOR_NAME = "pypdf"
EXTRACTOR_VERSION = "1.0.0"
OWNER_CODES = {
    "SP": "self",
    "JT": "joint",
    "DC": "dependent_child",
    "DS": "spouse",
    "JS": "joint_spouse",
}
ASSET_TYPES = {
    "ST": "stock",
    "OP": "option",
    "MF": "mutual_fund",
    "EF": "exchange_traded_fund",
    "CT": "cryptocurrency",
    "OT": "other",
}
RETRYABLE_JOB_STATUSES = {"failed_retryable"}
FINAL_JOB_STATUSES = {"complete", "needs_review", "failed_permanent"}

# House PDF text often removes the space between the two dates and the amount.
# Searching instead of matching from the beginning also handles page-break text
# such as ``... Common Stock (ABC) [ST]P 01/01/2025...``.
TRANSACTION_RE = re.compile(
    r"(?P<type>[PSE])\s*(?P<partial>\(\s*partial\s*\))?\s*"
    r"(?P<transaction_date>\d{1,2}/\d{1,2}/\d{4})\s*"
    r"(?P<notification_date>\d{1,2}/\d{1,2}/\d{4})\s*"
    r"(?P<amount> N/?A | <\s*\$?\s*[\d,]+ | \$?\s*[\d,]+"
    r"(?:\s*-\s*\$?\s*[\d,]+)? )"
    r"\s*(?P<capital_gains>Yes|No)?",
    re.IGNORECASE | re.VERBOSE,
)
ASSET_TYPE_RE = re.compile(r"\[\s*([A-Za-z0-9]{1,6})\s*\]\s*$")
TICKER_RE = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,9})\)")
DOC_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
SEPARATOR_RE = re.compile(
    r"^(?:ID\s+Owner|Filing\s+ID|Name\s*:|Status\s*:|State/District\s*:|"
    r"Filing\s+Status\s*:|Spouse\s+Occupation\s*:|Spouse\s+Employer\s*:|"
    r"Spouse\s+Account\s*:|Description\s*:|Investment\s+Vehicle\s*:|"
    r"Location\s*:|Transaction\s+Type|I\s+CERTIFY|Digitally\s+Signed)",
    re.IGNORECASE,
)


@dataclass
class TransactionRow:
    source_row_number: int
    transaction_date: date | None
    notification_date: date | None
    owner_type: str | None
    owner_raw: str | None
    transaction_type: str
    transaction_type_raw: str
    asset_name_raw: str
    asset_type_code_raw: str | None
    asset_type: str | None
    ticker_reported: str | None
    amount_range_raw: str | None
    amount_min: Decimal | None
    amount_max: Decimal | None
    amount_exact: Decimal | None
    capital_gains_over_200: bool | None
    description_raw: str | None
    is_partial_sale: bool
    parse_confidence: Decimal


@dataclass
class ExtractedDocument:
    text: str
    page_count: int
    has_embedded_text: bool
    warnings: list[str]


@dataclass
class Totals:
    selected: int = 0
    skipped: int = 0
    parsed: int = 0
    trades: int = 0
    review: int = 0
    failed: int = 0


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
    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    if not user or not password:
        raise RuntimeError(
            "Set DATABASE_URL or POSTGRES_USER and POSTGRES_PASSWORD in .env"
        )
    authority = (
        f"{quote(user, safe='')}:{quote(password, safe='')}"
        f"@{os.getenv('POSTGRES_HOST', 'localhost')}:{os.getenv('POSTGRES_PORT', '5433')}"
    )
    return urlunsplit(
        ("postgresql", authority, "/" + os.getenv("POSTGRES_DB", "congress_trades"), "", "")
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract House PTR PDFs and insert normalized trades"
    )
    parser.add_argument("--all", action="store_true", help="Process every eligible House PTR")
    parser.add_argument("--docid", help="Process one exact House DocID")
    parser.add_argument("--filing-id", type=int, help="Process one database filing_id")
    parser.add_argument("--member", help="Filter by matched member or stored filer name")
    parser.add_argument("--year", type=int, help="One reporting year")
    parser.add_argument("--from-year", type=int, help="Inclusive first reporting year")
    parser.add_argument("--to-year", type=int, help="Inclusive last reporting year")
    parser.add_argument("--limit", type=int, help="Maximum number of documents")
    parser.add_argument("--retry-failed", action="store_true", help="Retry failed retryable jobs")
    parser.add_argument("--reprocess", action="store_true", help="Reprocess completed or review jobs")
    parser.add_argument("--dry-run", action="store_true", help="List eligible documents without changes")
    parser.add_argument("--max-attempts", type=int, default=3, help="Maximum attempts per job")
    parser.add_argument("--database-url", help=argparse.SUPPRESS)
    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    filters = (args.docid, args.filing_id, args.member, args.year, args.from_year, args.to_year)
    if not args.all and not any(value is not None for value in filters):
        parser.error("specify --all or a DocID, filing ID, member, or year filter")
    if args.all and any(value is not None for value in filters):
        parser.error("--all cannot be combined with selection filters")
    if args.year is not None and (args.from_year is not None or args.to_year is not None):
        parser.error("--year cannot be combined with --from-year or --to-year")
    if args.from_year is not None and args.to_year is not None and args.from_year > args.to_year:
        parser.error("--from-year cannot be greater than --to-year")
    for label, value in (("--year", args.year), ("--from-year", args.from_year), ("--to-year", args.to_year)):
        if value is not None and not 1900 <= value <= datetime.now().year:
            parser.error(f"{label} must be between 1900 and {datetime.now().year}")
    if args.docid is not None and not DOC_ID_RE.fullmatch(args.docid):
        parser.error("--docid contains unsafe characters")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.max_attempts < 1:
        parser.error("--max-attempts must be positive")


def select_documents(cursor: Any, args: argparse.Namespace) -> list[dict[str, Any]]:
    clauses = [
        "f.source = %s",
        "f.chamber = 'house'",
        "f.filing_type_code_raw = 'P'",
        "d.is_primary",
        "d.verification_status = 'verified'",
        "d.local_path IS NOT NULL",
    ]
    params: list[Any] = [SOURCE]
    if args.docid:
        clauses.append("f.source_filing_id = %s")
        params.append(args.docid)
    if args.filing_id:
        clauses.append("f.filing_id = %s")
        params.append(args.filing_id)
    if args.member:
        clauses.append(
            "(m.preferred_name ILIKE %s OR f.raw_full_name ILIKE %s "
            "OR EXISTS (SELECT 1 FROM member_names mn "
            "WHERE mn.member_id = m.member_id AND mn.full_name ILIKE %s))"
        )
        pattern = f"%{args.member}%"
        params.extend([pattern, pattern, pattern])
    if args.year is not None:
        clauses.append("f.reporting_year = %s")
        params.append(args.year)
    if args.from_year is not None:
        clauses.append("f.reporting_year >= %s")
        params.append(args.from_year)
    if args.to_year is not None:
        clauses.append("f.reporting_year <= %s")
        params.append(args.to_year)
    query = f"""
        SELECT d.document_id, d.filing_id, d.local_path, d.content_hash,
               f.source_filing_id AS doc_id, f.reporting_year,
               f.filing_type_code_raw, f.filed_date, f.raw_full_name,
               f.source_url, m.preferred_name
        FROM documents d
        JOIN filings f ON f.filing_id = d.filing_id
        LEFT JOIN members m ON m.member_id = f.member_id
        WHERE {' AND '.join(clauses)}
        ORDER BY f.reporting_year, f.source_filing_id, d.document_id
    """
    if args.limit is not None:
        query += " LIMIT %s"
        params.append(args.limit)
    cursor.execute(query, tuple(params))
    return list(cursor.fetchall())


def clean_line(value: str) -> str:
    value = value.replace("\x00", "").replace("\ufffd", " ")
    value = "".join(char if ord(char) >= 32 or char in "\t\r\n" else " " for char in value)
    return re.sub(r"\s+", " ", value).strip()


def extract_pdf(path: Path) -> ExtractedDocument:
    reader = PdfReader(str(path), strict=False)
    page_lines: list[str] = []
    warnings: list[str] = []
    for page_number, page in enumerate(reader.pages, 1):
        try:
            raw = page.extract_text() or ""
        except Exception as exc:  # pypdf can fail on one malformed page
            warnings.append(f"page {page_number} extraction failed: {type(exc).__name__}: {exc}")
            raw = ""
        page_lines.extend(clean_line(line) for line in raw.splitlines())
        page_lines.append("")
    text = "\n".join(line for line in page_lines if line).strip() + "\n"
    has_text = bool(text.strip())
    if not has_text:
        warnings.append("no embedded text extracted; OCR is required")
    return ExtractedDocument(text, len(reader.pages), has_text, warnings)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_text(text, encoding="utf-8")
    partial.replace(path)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%m/%d/%Y").date()
    except (TypeError, ValueError):
        return None


def parse_amounts(value: str) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    raw = value.strip().replace(" ", "")
    if not raw or raw.upper() in {"N/A", "NA"}:
        return None, None, None
    numbers = re.findall(r"\d[\d,]*", raw)
    try:
        parsed = [Decimal(number.replace(",", "")) for number in numbers]
    except InvalidOperation:
        return None, None, None
    if len(parsed) >= 2:
        return parsed[0], parsed[1], None
    if parsed:
        return None, None, parsed[0]
    return None, None, None


def is_separator(line: str) -> bool:
    if SEPARATOR_RE.search(line):
        return True
    if re.match(r"^[A-Z]\s*:", line, re.IGNORECASE):
        return True
    if re.match(r"^[A-Z]\s+[A-Z]\s*:", line, re.IGNORECASE):
        return True
    # Some PDFs encode labels one character at a time (``F     S : New``).
    # Removing the extraction whitespace recovers the useful label prefixes.
    compact = re.sub(r"[^A-Za-z0-9]+", "", line).upper()
    return compact.startswith(
        (
            "IDOWNER",
            "FILINGID",
            "FILINGSTATUS",
            "SPOUSEOCCUPATION",
            "SPOUSEEMPLOYER",
            "SPOUSEACCOUNT",
            "TYPE",
            "DATENOTIFICATION",
            "DATE",
            "AMOUNTCAP",
            "GAINS200",
            "GAINS",
            "200",
            "INVESTMENTVEHICLE",
            "LOCATION",
            "TRANSACTIONTYPE",
            "ICERTIFY",
            "DIGITALLYSIGNED",
        )
    )


def asset_block(
    buffer: list[str],
) -> tuple[str, str | None, str | None, str | None, str | None]:
    """Return asset text, owner code, asset type code, and ticker from prior lines."""

    if not buffer:
        return "Unknown asset", None, None, None, None
    marker_indexes = [
        index for index, line in enumerate(buffer) if ASSET_TYPE_RE.search(line)
    ]
    marker_index = marker_indexes[-1] if marker_indexes else len(buffer) - 1
    start = marker_index
    while start > 0:
        previous = buffer[start - 1]
        if is_separator(previous):
            break
        if marker_indexes and start - 1 in marker_indexes[:-1]:
            break
        start -= 1
    lines = [line for line in buffer[start : marker_index + 1] if line and not is_separator(line)]
    if not lines:
        return "Unknown asset", None, None, None, None

    asset_type_code = None
    type_match = ASSET_TYPE_RE.search(lines[-1])
    if type_match:
        asset_type_code = type_match.group(1).upper()
        lines[-1] = ASSET_TYPE_RE.sub("", lines[-1]).strip()
    lines = [line for line in lines if line]

    owner_raw = None
    owner_type = None
    if lines:
        owner_match = re.match(r"^([A-Z]{2})\s+(.+)$", lines[0])
        if owner_match and owner_match.group(1) in OWNER_CODES:
            owner_raw = owner_match.group(1)
            owner_type = OWNER_CODES[owner_raw]
            lines[0] = owner_match.group(2).strip()
    asset_name = re.sub(r"\s+", " ", " ".join(lines)).strip(" -") or "Unknown asset"
    ticker_matches = TICKER_RE.findall(asset_name)
    ticker = ticker_matches[-1] if ticker_matches else None
    return asset_name, owner_type, owner_raw, asset_type_code, ticker


def transaction_candidate(line: str, following: str | None) -> tuple[str, re.Match[str] | None, int]:
    candidate = line
    match = TRANSACTION_RE.search(candidate)
    consumed = 0
    needs_continuation = bool(
        match
        and following
        and candidate[match.end() :].strip().startswith("-")
        and re.match(r"^\$?\s*\d", following)
    )
    if (not match or needs_continuation) and following:
        candidate = f"{line} {following}"
        match = TRANSACTION_RE.search(candidate)
        consumed = 1 if match else 0
    return candidate, match, consumed


def parse_ptr_text(text: str) -> tuple[list[TransactionRow], list[str]]:
    lines = [clean_line(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    rows: list[TransactionRow] = []
    warnings: list[str] = []
    buffer: list[str] = []
    last_row: TransactionRow | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        following = lines[index + 1] if index + 1 < len(lines) else None
        candidate, match, consumed = transaction_candidate(line, following)
        if not match:
            description_match = re.search(r"Description\s*:\s*(.+)$", line, re.IGNORECASE)
            if description_match and last_row is not None:
                last_row.description_raw = description_match.group(1).strip()
            else:
                buffer.append(line)
            index += 1
            continue

        prefix = candidate[: match.start()].strip()
        if prefix:
            buffer.append(prefix)
        asset_name, owner_type, owner_raw, asset_type_code, ticker = asset_block(buffer)
        transaction_type_raw = match.group("type").upper()
        is_partial = bool(match.group("partial"))
        if is_partial:
            transaction_type_raw += " (partial)"
        transaction_type = {"P": "purchase", "S": "sale", "E": "exchange"}.get(
            transaction_type_raw[0], transaction_type_raw[0].lower()
        )
        amount_raw = re.sub(r"\s+", " ", match.group("amount")).strip()
        amount_min, amount_max, amount_exact = parse_amounts(amount_raw)
        cap_text = match.group("capital_gains")
        capital_gains = None if not cap_text else cap_text.lower() == "yes"
        confidence = Decimal("0.90") if asset_type_code else Decimal("0.70")
        if ticker:
            confidence = min(Decimal("0.98"), confidence + Decimal("0.05"))
        row = TransactionRow(
            source_row_number=len(rows) + 1,
            transaction_date=parse_date(match.group("transaction_date")),
            notification_date=parse_date(match.group("notification_date")),
            owner_type=owner_type,
            owner_raw=owner_raw,
            transaction_type=transaction_type,
            transaction_type_raw=transaction_type_raw,
            asset_name_raw=asset_name,
            asset_type_code_raw=asset_type_code,
            asset_type=ASSET_TYPES.get(asset_type_code, asset_type_code.lower() if asset_type_code else None),
            ticker_reported=ticker,
            amount_range_raw=amount_raw,
            amount_min=amount_min,
            amount_max=amount_max,
            amount_exact=amount_exact,
            capital_gains_over_200=capital_gains,
            description_raw=None,
            is_partial_sale=is_partial and transaction_type == "sale",
            parse_confidence=confidence,
        )
        rows.append(row)
        last_row = row
        buffer = []
        index += 1 + consumed
    if not rows:
        warnings.append("no transaction rows matched the PTR parser")
    return rows, warnings


def ensure_parse_job(cursor: Any, document: dict[str, Any], args: argparse.Namespace) -> tuple[int | None, bool]:
    cursor.execute(
        """SELECT document_job_id,status FROM document_jobs
           WHERE filing_id=%s AND document_id=%s AND job_type='parse'
           ORDER BY document_job_id DESC LIMIT 1""",
        (document["filing_id"], document["document_id"]),
    )
    existing = cursor.fetchone()
    if existing:
        status = existing["status"]
        if status in FINAL_JOB_STATUSES and not args.reprocess:
            return existing["document_job_id"], False
        if status == "running" and not args.reprocess:
            return existing["document_job_id"], False
        if status in RETRYABLE_JOB_STATUSES and not args.retry_failed and not args.reprocess:
            return existing["document_job_id"], False
        cursor.execute(
            """UPDATE document_jobs SET status='queued',document_id=%s,max_attempts=%s,
               attempt_count=0,started_at=NULL,finished_at=NULL,next_attempt_at=NULL,
               error_type=NULL,error_message=NULL WHERE document_job_id=%s""",
            (document["document_id"], args.max_attempts, existing["document_job_id"]),
        )
        return existing["document_job_id"], True
    cursor.execute(
        """INSERT INTO document_jobs (filing_id,document_id,job_type,status,max_attempts)
           VALUES (%s,%s,'parse','queued',%s) RETURNING document_job_id""",
        (document["filing_id"], document["document_id"], args.max_attempts),
    )
    return cursor.fetchone()["document_job_id"], True


def mark_running(cursor: Any, job_id: int) -> int:
    cursor.execute(
        """UPDATE document_jobs SET status='running',started_at=CURRENT_TIMESTAMP,
           finished_at=NULL,next_attempt_at=NULL,attempt_count=attempt_count+1,
           error_type=NULL,error_message=NULL
           WHERE document_job_id=%s AND status IN ('queued','failed_retryable')
             AND attempt_count < max_attempts
           RETURNING attempt_count""",
        (job_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise RuntimeError(f"Parse job {job_id} is not eligible to run")
    return row["attempt_count"]


def mark_failure(connection: Any, job_id: int, error: Exception) -> None:
    with connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """UPDATE document_jobs SET status='failed_permanent',finished_at=CURRENT_TIMESTAMP,
                   error_type=%s,error_message=%s WHERE document_job_id=%s""",
                (type(error).__name__, str(error)[:4000], job_id),
            )


def record_document_and_trades(
    connection: Any,
    document: dict[str, Any],
    job_id: int,
    extracted: ExtractedDocument,
    rows: list[TransactionRow],
    parse_warnings: list[str],
) -> tuple[int, int]:
    pdf_path = Path(document["local_path"]).resolve()
    text_path = pdf_path.with_suffix(".txt")
    text_bytes = extracted.text.encode("utf-8")
    output_hash = sha256_bytes(text_bytes)
    write_text_atomic(text_path, extracted.text)
    all_warnings = extracted.warnings + parse_warnings
    status = "parsed" if extracted.has_embedded_text and rows else (
        "needs_ocr" if not extracted.has_embedded_text else "needs_review"
    )
    with connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """UPDATE documents SET page_count=%s,has_embedded_text=%s,requires_ocr=%s,
                   document_completeness_status=%s WHERE document_id=%s""",
                (
                    extracted.page_count,
                    extracted.has_embedded_text,
                    not extracted.has_embedded_text,
                    status,
                    document["document_id"],
                ),
            )
            cursor.execute(
                """UPDATE document_extractions SET is_preferred=FALSE
                   WHERE document_id=%s AND extraction_type='embedded_text'""",
                (document["document_id"],),
            )
            cursor.execute(
                """INSERT INTO document_extractions
                   (document_id,document_job_id,extraction_type,extractor_name,extractor_version,
                    output_path,output_hash,started_at,finished_at,quality_score,
                    characters_extracted,pages_processed,warnings,is_preferred)
                   VALUES (%s,%s,'embedded_text',%s,%s,%s,%s,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,
                           %s,%s,%s,%s,TRUE)
                   ON CONFLICT (document_id,extraction_type,extractor_name,extractor_version,output_hash)
                   DO UPDATE SET document_job_id=EXCLUDED.document_job_id,output_path=EXCLUDED.output_path,
                       finished_at=CURRENT_TIMESTAMP,quality_score=EXCLUDED.quality_score,
                       characters_extracted=EXCLUDED.characters_extracted,pages_processed=EXCLUDED.pages_processed,
                       warnings=EXCLUDED.warnings,is_preferred=TRUE
                   RETURNING document_extraction_id""",
                (
                    document["document_id"],
                    job_id,
                    EXTRACTOR_NAME,
                    EXTRACTOR_VERSION,
                    str(text_path),
                    output_hash,
                    Decimal("1.0") if extracted.has_embedded_text else Decimal("0.0"),
                    len(text_bytes),
                    extracted.page_count,
                    Json(all_warnings),
                ),
            )
            extraction_id = cursor.fetchone()["document_extraction_id"]
            cursor.execute(
                """DELETE FROM trades
                   WHERE filing_id=%s AND document_id=%s AND parser_name=%s AND parser_version=%s""",
                (document["filing_id"], document["document_id"], PARSER_NAME, PARSER_VERSION),
            )
            for row in rows:
                cursor.execute(
                    """INSERT INTO trades
                       (filing_id,document_id,document_extraction_id,source_row_number,
                        transaction_date,notification_date,filed_date,owner_type,owner_raw,
                        transaction_type,transaction_type_raw,asset_name_raw,asset_type_code_raw,
                        asset_type,ticker_reported,amount_range_raw,amount_min,amount_max,amount_exact,
                        capital_gains_over_200,description_raw,is_partial_sale,
                        is_annual_report_transaction,transaction_sequence,parser_name,parser_version,
                        parse_confidence,review_status)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE,%s,%s,%s,%s,'unreviewed')
                       ON CONFLICT (filing_id,source_row_number,parser_version)
                       DO UPDATE SET document_id=EXCLUDED.document_id,
                           document_extraction_id=EXCLUDED.document_extraction_id,
                           transaction_date=EXCLUDED.transaction_date,
                           notification_date=EXCLUDED.notification_date,
                           filed_date=EXCLUDED.filed_date,owner_type=EXCLUDED.owner_type,
                           owner_raw=EXCLUDED.owner_raw,transaction_type=EXCLUDED.transaction_type,
                           transaction_type_raw=EXCLUDED.transaction_type_raw,asset_name_raw=EXCLUDED.asset_name_raw,
                           asset_type_code_raw=EXCLUDED.asset_type_code_raw,asset_type=EXCLUDED.asset_type,
                           ticker_reported=EXCLUDED.ticker_reported,amount_range_raw=EXCLUDED.amount_range_raw,
                           amount_min=EXCLUDED.amount_min,amount_max=EXCLUDED.amount_max,
                           amount_exact=EXCLUDED.amount_exact,capital_gains_over_200=EXCLUDED.capital_gains_over_200,
                           description_raw=EXCLUDED.description_raw,is_partial_sale=EXCLUDED.is_partial_sale,
                           is_annual_report_transaction=EXCLUDED.is_annual_report_transaction,
                           transaction_sequence=EXCLUDED.transaction_sequence,
                           parse_confidence=EXCLUDED.parse_confidence,updated_at=CURRENT_TIMESTAMP
                       RETURNING trade_id""",
                    (
                        document["filing_id"],
                        document["document_id"],
                        extraction_id,
                        row.source_row_number,
                        row.transaction_date,
                        row.notification_date,
                        document["filed_date"],
                        row.owner_type,
                        row.owner_raw,
                        row.transaction_type,
                        row.transaction_type_raw,
                        row.asset_name_raw,
                        row.asset_type_code_raw,
                        row.asset_type,
                        row.ticker_reported,
                        row.amount_range_raw,
                        row.amount_min,
                        row.amount_max,
                        row.amount_exact,
                        row.capital_gains_over_200,
                        row.description_raw,
                        row.is_partial_sale,
                        row.source_row_number,
                        PARSER_NAME,
                        PARSER_VERSION,
                        row.parse_confidence,
                    ),
                )
            cursor.execute(
                """UPDATE document_jobs SET document_id=%s,status=%s,finished_at=CURRENT_TIMESTAMP,
                   error_type=NULL,error_message=%s WHERE document_job_id=%s""",
                (
                    document["document_id"],
                    "complete" if status == "parsed" else "needs_review",
                    "; ".join(all_warnings)[:4000] if all_warnings else None,
                    job_id,
                ),
            )
            cursor.execute(
                "UPDATE filings SET processing_status=%s WHERE filing_id=%s",
                (status, document["filing_id"]),
            )
    return extraction_id, len(rows)


def run(args: argparse.Namespace) -> int:
    totals = Totals()
    connection = psycopg2.connect(database_url(args.database_url), cursor_factory=RealDictCursor)
    try:
        with connection:
            with connection.cursor() as cursor:
                documents = select_documents(cursor, args)
        totals.selected = len(documents)
        print(f"Eligible PTR documents: {totals.selected:,}")
        if args.dry_run:
            for document in documents:
                print(
                    f"  {document['reporting_year']} DocID={document['doc_id']} "
                    f"filer={document['preferred_name'] or document['raw_full_name'] or 'unmatched'} "
                    f"path={document['local_path']}"
                )
            print("Dry run complete; no database or filesystem changes were made")
            return 0

        for number, document in enumerate(documents, 1):
            with connection:
                with connection.cursor() as cursor:
                    job_id, should_run = ensure_parse_job(cursor, document, args)
            if not should_run or job_id is None:
                totals.skipped += 1
                continue
            print(
                f"[{number:,}/{len(documents):,}] {document['reporting_year']} "
                f"DocID={document['doc_id']} path={document['local_path']}",
                flush=True,
            )
            try:
                with connection:
                    with connection.cursor() as cursor:
                        attempt = mark_running(cursor, job_id)
                extracted = extract_pdf(Path(document["local_path"]))
                rows, warnings = parse_ptr_text(extracted.text)
                _, trade_count = record_document_and_trades(
                    connection, document, job_id, extracted, rows, warnings
                )
                if trade_count:
                    totals.parsed += 1
                    totals.trades += trade_count
                else:
                    totals.review += 1
                print(
                    f"  attempt={attempt} pages={extracted.page_count} "
                    f"trades={trade_count} status={'parsed' if trade_count else 'needs_review'}",
                    flush=True,
                )
            except Exception as exc:
                # Keep the job record useful even when a PDF is corrupt or a
                # parser assumption does not fit an older form version.
                mark_failure(connection, job_id, exc)
                totals.failed += 1
                print(f"  failed_permanent: {type(exc).__name__}: {exc}", flush=True)
        print(
            f"Totals: selected={totals.selected:,} parsed={totals.parsed:,} "
            f"trades={totals.trades:,} needs_review={totals.review:,} "
            f"skipped={totals.skipped:,} failed={totals.failed:,}"
        )
        return 1 if totals.failed else 0
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(parser, args)
    try:
        return run(args)
    except KeyboardInterrupt:
        print("Parse interrupted; completed jobs are preserved and the run can be resumed", file=sys.stderr)
        return 130
    except (RuntimeError, OSError, psycopg2.Error) as exc:
        print(f"House PDF parsing failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
