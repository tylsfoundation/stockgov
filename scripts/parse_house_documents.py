"""Extract House PTR PDFs and load normalized transactions into StockGov.

The implementation targets House Periodic Transaction Reports (filing code
``P``).  It calls the shared PDF extraction service, evaluates the result with
House rules, requests the generic OCR service when needed, feeds OCR text
through the same transaction parser, and stores the validated preferred
extraction in ``document_extractions`` before inserting trades. Jobs and
parser versions make interrupted runs safe to resume.

Examples::

    py scripts/parse_house_documents.py --year 2025 --limit 10 --dry-run
    py scripts/parse_house_documents.py --year 2025 --limit 10
    py scripts/parse_house_documents.py --docid 20016861
    py scripts/parse_house_documents.py --all --retry-failed

Requirements: ``py -m pip install psycopg2-binary pypdf pdfplumber``
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

try:
    import psycopg2
    from psycopg2.extras import Json, RealDictCursor
except ImportError as exc:  # pragma: no cover - exercised by command-line users
    raise SystemExit(
        "psycopg2-binary is required: "
        "py -m pip install psycopg2-binary pypdf pdfplumber"
    ) from exc

try:
    from document_extraction import (
        DocumentExtractionService,
        ExtractedDocument,
        PDFPLUMBER_EXTRACTOR_NAME,
        PDFPLUMBER_EXTRACTOR_VERSION,
        PYPDF_EXTRACTOR_NAME,
        PYPDF_EXTRACTOR_VERSION,
    )
    from ocr_service import OCRResult, OCRServiceUnavailable, default_ocr_service
except ImportError:  # Imported as scripts.parse_house_documents by the orchestrator.
    from scripts.document_extraction import (
        DocumentExtractionService,
        ExtractedDocument,
        PDFPLUMBER_EXTRACTOR_NAME,
        PDFPLUMBER_EXTRACTOR_VERSION,
        PYPDF_EXTRACTOR_NAME,
        PYPDF_EXTRACTOR_VERSION,
    )
    from scripts.ocr_service import OCRResult, OCRServiceUnavailable, default_ocr_service
try:
    from ingestion.common.persistence import database_url as shared_database_url
    from ingestion.common.artifacts import (
        artifact_path as shared_artifact_path,
        content_hash as shared_content_hash,
        write_immutable_text,
    )
except ImportError:
    project = str(Path(__file__).resolve().parent.parent)
    if project not in sys.path:
        sys.path.insert(0, project)
    from ingestion.common.persistence import database_url as shared_database_url
    from ingestion.common.artifacts import (
        artifact_path as shared_artifact_path,
        content_hash as shared_content_hash,
        write_immutable_text,
    )


SOURCE = "house_clerk_financial_disclosure"
PARSER_NAME = "house_ptr_pdf"
PARSER_VERSION = "1.3.0"
OWNER_CODES = {
    "SP": "spouse",
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
DEFAULT_STALE_JOB_TIMEOUT_SECONDS = 3600

# House PDF text often removes the space between the two dates and the amount.
# Searching instead of matching from the beginning also handles page-break text
# such as ``... Common Stock (ABC) [ST]P 01/01/2025...``.
TRANSACTION_RE = re.compile(
    r"(?P<type>[PSE])\s*(?P<partial>\(\s*partial\s*\))?\s*"
    r"(?P<transaction_date>\d{1,2}/\d{1,2}/\d{4})\s*"
    r"(?P<notification_date>\d{1,2}/\d{1,2}/\d{4})\s*"
    r"(?P<amount> Spouse\s*/\s*DC\s+Over\s+\$?\s*[\d,]+ |"
    r" N/?A | <\s*\$?\s*[\d,]+ | \$?\s*[\d,]+"
    r"(?:\s*-\s*\$?\s*[\d,]+)? )"
    r"\s*(?P<capital_gains>Yes|No)?",
    re.IGNORECASE | re.VERBOSE,
)
ASSET_TYPE_RE = re.compile(r"\[\s*([A-Za-z0-9]{1,6})\s*\]\s*$")
TICKER_RE = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,9})\)", re.IGNORECASE)
ASSET_CONTINUATION_RE = re.compile(
    r"(?P<asset_fragment>.*?\([A-Z][A-Z0-9.\-]{0,9}\))\s*"
    r"\[\s*(?P<asset_type>[A-Za-z0-9]{1,6})\s*\]$",
    re.IGNORECASE,
)
DOC_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
PAGE_MARKER_RE = re.compile(r"^\[\[PAGE\s+(\d+)\]\]$")
AMENDMENT_ROW_ID_RE = re.compile(
    r"^(?P<source_transaction_id>\d{10})\s*"
    r"(?P<owner>SP|JT|DC|DS|JS)?\s*(?P<asset>.+)$",
    re.IGNORECASE,
)
MONEY_ONLY_RE = re.compile(r"^\$?\s*\d[\d,]*\s*$")
NO_TRANSACTIONS_RE = re.compile(
    r"\b(?:no|none)\s+(?:reportable\s+)?transactions?\b|"
    r"\btransactions?\s*:\s*(?:no|none)\b",
    re.IGNORECASE,
)
INVALID_ASSET_RE = re.compile(
    r"periodic\s+transaction\s+report|filer\s+information|"
    r"owner\s*asset\s*transaction|date\s+notification\s+date\s+amount|"
    r"clerk\s+of\s+the\s+house|legislative\s+resource\s+center",
    re.IGNORECASE,
)
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
    source_page_number: int | None
    source_transaction_id_raw: str | None
    validation_errors: list[str]


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
    return shared_database_url(override)


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
    parser.add_argument(
        "--non-ocr-only",
        action="store_true",
        help="Process only documents whose preserved extraction is not marked requires_ocr",
    )
    parser.add_argument(
        "--ocr-only",
        action="store_true",
        help="Process only documents currently marked requires_ocr",
    )
    parser.add_argument("--dry-run", action="store_true", help="List eligible documents without changes")
    parser.add_argument("--max-attempts", type=int, default=3, help="Maximum attempts per job")
    parser.add_argument(
        "--stale-job-timeout-seconds",
        type=int,
        default=None,
        help="Recover running jobs older than this timeout (default: STALE_JOB_TIMEOUT_SECONDS or 3600)",
    )
    parser.add_argument("--database-url", help=argparse.SUPPRESS)
    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    filters = (args.docid, args.filing_id, args.member, args.year, args.from_year, args.to_year)
    if not args.all and not any(value is not None for value in filters):
        parser.error("specify --all or a DocID, filing ID, member, or year filter")
    if args.all and any(value is not None for value in filters):
        parser.error("--all cannot be combined with selection filters")
    if args.non_ocr_only and args.ocr_only:
        parser.error("--non-ocr-only cannot be combined with --ocr-only")
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
    if args.stale_job_timeout_seconds is not None and args.stale_job_timeout_seconds < 1:
        parser.error("--stale-job-timeout-seconds must be positive")


def stale_job_timeout_seconds(args: argparse.Namespace) -> int:
    configured = getattr(args, "stale_job_timeout_seconds", None)
    if configured is not None:
        return configured
    load_dotenv()
    try:
        value = int(os.getenv("STALE_JOB_TIMEOUT_SECONDS", str(DEFAULT_STALE_JOB_TIMEOUT_SECONDS)))
    except ValueError as exc:
        raise ValueError("STALE_JOB_TIMEOUT_SECONDS must be an integer") from exc
    if value < 1:
        raise ValueError("STALE_JOB_TIMEOUT_SECONDS must be positive")
    return value


def is_stale_running_job(
    started_at: datetime | None,
    *,
    now: datetime | None = None,
    timeout_seconds: int = DEFAULT_STALE_JOB_TIMEOUT_SECONDS,
) -> bool:
    """Return whether a running job has exceeded the explicit recovery window."""

    if started_at is None:
        return True
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be positive")
    current = now or datetime.now(timezone.utc)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current - started_at >= timedelta(seconds=timeout_seconds)


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
    if args.non_ocr_only:
        clauses.append("d.requires_ocr IS FALSE")
    if args.ocr_only:
        clauses.append("d.requires_ocr IS TRUE")
    query = f"""
        SELECT d.document_id, d.filing_id, d.local_path, d.content_hash,
               d.requires_ocr,
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
    value = value.translate(
        str.maketrans(
            {
                "\x00": "",
                "\ufffd": " ",
                "\u00a0": " ",
                "\u00ad": "-",
                "\u2010": "-",
                "\u2011": "-",
                "\u2012": "-",
                "\u2013": "-",
                "\u2014": "-",
                "\u2015": "-",
                "\u2212": "-",
            }
        )
    )
    value = "".join(
        char
        if (ord(char) >= 32 or char in "\t\r\n")
        and not 0xD800 <= ord(char) <= 0xDFFF
        else " "
        for char in value
    )
    return re.sub(r"\s+", " ", value).strip()


_EXTRACTION_SERVICE = DocumentExtractionService()


def extract_pdf(path: Path) -> ExtractedDocument:
    """Compatibility wrapper around the shared PDF extraction service."""

    return _EXTRACTION_SERVICE.extract_embedded(path)


def extract_pdf_layout(path: Path) -> ExtractedDocument:
    """Compatibility wrapper around the shared layout extraction service."""

    return _EXTRACTION_SERVICE.extract_layout(path)


def extraction_text_path(
    pdf_path: Path, extracted: ExtractedDocument, output_hash: str | None = None
) -> Path:
    """Return a content-addressed path for an immutable extraction artifact."""

    if output_hash is None:
        output_hash = shared_content_hash(extracted.text.encode("utf-8", errors="replace"))
    return shared_artifact_path(
        pdf_path, extracted.extractor_name, extracted.extractor_version, output_hash
    )


def write_text_atomic(path: Path, text: str) -> None:
    """Compatibility wrapper that now preserves immutable artifact bytes."""

    write_immutable_text(path, text)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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
    if "OVER" in raw.upper() and parsed:
        return parsed[0], None, None
    if raw.startswith("<") and parsed:
        return None, parsed[0], None
    if len(parsed) >= 2:
        return parsed[0], parsed[1], None
    if parsed:
        return None, None, parsed[0]
    return None, None, None


def is_separator(line: str) -> bool:
    if PAGE_MARKER_RE.fullmatch(line):
        return True
    if re.fullmatch(r"[A-Za-z]", line):
        return True
    if SEPARATOR_RE.search(line):
        return True
    if re.match(r"^[A-Z]\s*:", line, re.IGNORECASE):
        return True
    if re.match(r"^[A-Z]\s+[A-Z]\s*:", line, re.IGNORECASE):
        return True
    # Some PDFs encode labels one character at a time (``F     S : New``).
    # Removing the extraction whitespace recovers the useful label prefixes.
    compact = re.sub(r"[^A-Za-z0-9]+", "", line).upper()
    if compact == "200":
        return True
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
            "INVESTMENTVEHICLE",
            "LOCATION",
            "TRANSACTIONTYPE",
            "ICERTIFY",
            "DIGITALLYSIGNED",
            "PERIODICTRANSACTIONREPORT",
            "CLERKOFTHEHOUSEOFREPRESENTATIVES",
            "FILERINFORMATION",
            "TRANSACTIONSIDOWNERASSETTRANSACTIONTYPE",
            "OWNERASSETTRANSACTIONTYPEDATENOTIFICATIONDATEAMOUNT",
            "THISPAGEWILLBEPUBLICLYDISCLOSED",
        )
    )


def is_repeated_page_table_header(line: str) -> bool:
    """Return whether a line is part of the repeated PTR table heading."""

    compact = re.sub(r"[^A-Za-z0-9]+", "", line).upper()
    return compact in {"TYPE", "DATE", "GAINS", "200"} or compact.startswith(
        ("IDOWNERASSETTRANSACTION", "DATENOTIFICATION", "AMOUNTCAP")
    )


def is_asset_continuation_boundary(line: str) -> bool:
    """Return whether text after a fragment confirms a PTR row boundary."""

    return bool(
        re.match(
            r"^(?:Filing\s+Status\s*:|F\s+S\s*:|"
            r"Subholding\s+Of\s*:|S\s+O\s*:|"
            r"(?:SP|JT|DC|DS|JS)\s+)",
            line,
            re.IGNORECASE,
        )
    )


def page_asset_continuation(
    lines: list[str], page_marker_index: int
) -> tuple[str, str, int] | None:
    """Find a tightly bounded asset fragment at the start of the next page.

    Only repeated table-heading lines may precede the fragment.  Filing labels,
    owner rows, transactions, dates, and another page marker stop the search.
    The returned integer is the number of lines after the page marker that can
    be skipped after the fragment has been attached to the prior transaction.
    """

    cursor = page_marker_index + 1
    search_limit = min(len(lines), page_marker_index + 13)
    while cursor < search_limit and is_repeated_page_table_header(lines[cursor]):
        cursor += 1

    fragments: list[str] = []
    while cursor < search_limit and len(fragments) < 3:
        line = lines[cursor]
        if (
            PAGE_MARKER_RE.fullmatch(line)
            or is_separator(line)
            or TRANSACTION_RE.search(line)
            or re.match(r"^(?:SP|JT|DC|DS|JS)\s+", line, re.IGNORECASE)
            or re.search(r"\d{1,2}/\d{1,2}/\d{4}", line)
            or ":" in line
            or len(line) > 100
        ):
            return None
        fragments.append(line)
        candidate = clean_line(" ".join(fragments))
        match = ASSET_CONTINUATION_RE.fullmatch(candidate)
        if match:
            asset_fragment = clean_line(match.group("asset_fragment"))
            next_index = cursor + 1
            if (
                len(asset_fragment) > 100
                or next_index >= len(lines)
                or not is_asset_continuation_boundary(lines[next_index])
            ):
                return None
            return (
                asset_fragment,
                match.group("asset_type").upper(),
                cursor - page_marker_index,
            )
        if ASSET_TYPE_RE.search(candidate):
            return None
        cursor += 1
    return None


def strip_table_header(value: str) -> str:
    """Remove form and repeated table headings that precede an asset."""

    value = re.sub(
        r"^.*(?:Owner\s*Asset\s*Transaction\s*Type\s*Date\s*Notification\s*Date\s*Amount|"
        r"Transactions?\s*ID\s*Owner\s*Asset\s*Transaction\s*Type\s*Date\s*Notification\s*Date\s*Amount)",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"^.*?(?:Cap\.?\s*Gains?\s*>?\s*\$?\s*200\??)",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"^.*Filing\s*Status\s*:\s*(?:New|Amendment)?",
        "",
        value,
        flags=re.IGNORECASE,
    )
    # Flattened legacy PDFs can glue a prior row's DESCRIPTION field to the
    # next owner/asset row.  Keep the next asset when it has a PTR owner code,
    # an asset-type marker, and no intervening table boundary.
    value = re.sub(
        r"DESCRIPTION\s*:.*?(?=(?:SP|JT|DC|DS|JS)[A-Za-z][^[]*\[[A-Za-z0-9]{1,6}\])",
        "",
        value,
        flags=re.IGNORECASE,
    )
    return value.strip()


def split_packed_transaction_lines(lines: list[str]) -> list[str]:
    """Split physical text lines containing multiple complete transactions.

    Some older House PDFs flatten an entire page or table row into one text
    line.  Splitting only at complete ``TRANSACTION_RE`` matches preserves the
    signatures and lets the normal parser handle each asset independently.
    """

    expanded: list[str] = []
    for line in lines:
        matches = list(TRANSACTION_RE.finditer(line))
        if len(matches) <= 1:
            expanded.append(line)
            continue
        start = 0
        for match in matches:
            expanded.append(line[start : match.end()])
            start = match.end()
        if line[start:].strip():
            expanded.append(line[start:])
    return expanded


def ticker_from_asset(asset_name: str) -> str | None:
    ticker_matches = TICKER_RE.findall(asset_name)
    return ticker_matches[-1].upper() if ticker_matches else None


def append_asset_continuation(
    row: TransactionRow, asset_fragment: str, asset_type_code: str
) -> None:
    """Complete a previously parsed row without changing its row identity."""

    row.asset_name_raw = clean_line(f"{row.asset_name_raw} {asset_fragment}")
    row.asset_type_code_raw = asset_type_code
    row.asset_type = ASSET_TYPES.get(asset_type_code, asset_type_code.lower())
    row.ticker_reported = ticker_from_asset(row.asset_name_raw)
    row.parse_confidence = Decimal("0.90")
    if row.ticker_reported:
        row.parse_confidence = Decimal("0.95")
    row.validation_errors = validate_transaction(row)


def validate_transaction(row: TransactionRow) -> list[str]:
    errors: list[str] = []
    if row.transaction_date is None:
        errors.append("invalid or missing transaction date")
    if row.transaction_type not in {"purchase", "sale", "exchange"}:
        errors.append("unrecognized transaction type")
    if not row.asset_name_raw or row.asset_name_raw == "Unknown asset":
        errors.append("missing asset name")
    elif INVALID_ASSET_RE.search(row.asset_name_raw):
        errors.append("asset name contains a form or table header")
    if row.amount_range_raw and row.amount_range_raw.upper() not in {"N/A", "NA"}:
        if row.amount_min is None and row.amount_max is None and row.amount_exact is None:
            errors.append("unparseable transaction amount")
    if row.amount_min is not None and row.amount_max is not None and row.amount_max < row.amount_min:
        errors.append("transaction amount range is reversed")
    return errors


def asset_block(
    buffer: list[str],
) -> tuple[str, str | None, str | None, str | None, str | None, str | None]:
    """Return asset, owner, type, ticker, and amendment transaction ID."""

    if not buffer:
        return "Unknown asset", None, None, None, None, None
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
        return "Unknown asset", None, None, None, None, None

    asset_type_code = None
    type_match = ASSET_TYPE_RE.search(lines[-1])
    if type_match:
        asset_type_code = type_match.group(1).upper()
        lines[-1] = ASSET_TYPE_RE.sub("", lines[-1]).strip()
    lines = [line for line in lines if line]

    owner_raw = None
    owner_type = None
    source_transaction_id_raw = None
    if lines:
        amendment_match = AMENDMENT_ROW_ID_RE.match(lines[0])
        if amendment_match:
            source_transaction_id_raw = amendment_match.group("source_transaction_id")
            amendment_owner = amendment_match.group("owner")
            if amendment_owner:
                owner_raw = amendment_owner.upper()
                owner_type = OWNER_CODES[owner_raw]
            lines[0] = amendment_match.group("asset").strip()
        owner_match = re.match(r"^([A-Z]{2})\s+(.+)$", lines[0])
        if not owner_match:
            # Flattened legacy PDFs sometimes glue SP/DC/JT to the asset name.
            owner_match = re.match(r"^([A-Z]{2})(?=[A-Z]?[a-z])(.+)$", lines[0])
        if owner_match and owner_match.group(1) in OWNER_CODES:
            owner_raw = owner_match.group(1)
            owner_type = OWNER_CODES[owner_raw]
            lines[0] = owner_match.group(2).strip()
    asset_name = strip_table_header(re.sub(r"\s+", " ", " ".join(lines))).strip(" -") or "Unknown asset"
    ticker = ticker_from_asset(asset_name)
    if owner_type is None:
        owner_type = "self"
    return (
        asset_name,
        owner_type,
        owner_raw,
        asset_type_code,
        ticker,
        source_transaction_id_raw,
    )


def transaction_candidate(line: str, following: str | None) -> tuple[str, re.Match[str] | None, int]:
    candidate = line
    match = TRANSACTION_RE.search(candidate)
    consumed = 0
    needs_continuation = bool(
        match
        and following
        and candidate[match.end() :].strip().startswith("-")
        and MONEY_ONLY_RE.match(following)
    )
    may_contain_asset_fragment = bool(
        line
        and not is_separator(line)
        and not re.fullmatch(r"[A-Za-z]", line)
    )
    if following and (needs_continuation or (not match and may_contain_asset_fragment)):
        candidate = f"{line} {following}"
        match = TRANSACTION_RE.search(candidate)
        consumed = 1 if match else 0
    return candidate, match, consumed


def transaction_signature_count(text: str) -> int:
    """Count independently recognizable transaction signatures in extracted text."""

    return sum(1 for _ in TRANSACTION_RE.finditer(text))


def layout_fallback_reason(
    extracted: ExtractedDocument,
    rows: list[TransactionRow],
) -> str | None:
    """Explain why embedded text should be retried with layout extraction."""

    if not extracted.has_embedded_text:
        return "pypdf extracted no embedded text"
    signature_count = transaction_signature_count(extracted.text)
    if signature_count != len(rows):
        return (
            f"transaction coverage mismatch: detected {signature_count} signature(s) "
            f"but parsed {len(rows)} row(s)"
        )
    invalid_count = sum(bool(row.validation_errors) for row in rows)
    if invalid_count:
        return f"{invalid_count} parsed row(s) failed validation"
    if not rows and not NO_TRANSACTIONS_RE.search(extracted.text):
        return "embedded text contained no recognizable transaction rows"
    return None


def parse_quality(text: str, rows: list[TransactionRow]) -> tuple[int, int, int]:
    """Rank a parse by coverage, validation, and usable row count."""

    signatures = transaction_signature_count(text)
    explicitly_empty = bool(not rows and NO_TRANSACTIONS_RE.search(text))
    complete = int((bool(rows) or explicitly_empty) and signatures == len(rows))
    invalid_count = sum(bool(row.validation_errors) for row in rows)
    valid_count = len(rows) - invalid_count
    return complete, valid_count, -invalid_count


def extract_and_parse_document(
    path: Path,
    extraction_service: DocumentExtractionService | None = None,
) -> tuple[ExtractedDocument, list[TransactionRow], list[str]]:
    """Use the shared extraction service before House-specific evaluation."""

    service = extraction_service or _EXTRACTION_SERVICE
    extracted = service.extract_embedded(path)
    rows, warnings = parse_ptr_text(extracted.text)
    reason = layout_fallback_reason(extracted, rows)
    if reason is None:
        return extracted, rows, warnings

    try:
        layout_extracted = service.extract_layout(path)
        layout_rows, layout_warnings = parse_ptr_text(layout_extracted.text)
    except Exception as exc:
        extracted.warnings.append(
            f"layout fallback failed after {reason}: {type(exc).__name__}: {exc}"
        )
        return extracted, rows, warnings

    layout_is_better = (
        layout_extracted.has_embedded_text and not extracted.has_embedded_text
    ) or parse_quality(layout_extracted.text, layout_rows) > parse_quality(
        extracted.text, rows
    )
    if layout_is_better:
        layout_extracted.warnings.insert(0, f"layout fallback selected: {reason}")
        return layout_extracted, layout_rows, layout_warnings

    extracted.warnings.append(f"layout fallback did not improve parse: {reason}")
    return extracted, rows, warnings


def parse_ptr_text(text: str) -> tuple[list[TransactionRow], list[str]]:
    cleaned_lines = []
    for raw_line in text.splitlines():
        line = clean_line(raw_line)
        if line:
            cleaned_lines.append(line)
    lines = split_packed_transaction_lines(cleaned_lines)
    lines = [line for line in lines if line]
    rows: list[TransactionRow] = []
    warnings: list[str] = []
    buffer: list[str] = []
    last_row: TransactionRow | None = None
    last_transaction_end_index: int | None = None
    current_page: int | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        page_match = PAGE_MARKER_RE.fullmatch(line)
        if page_match:
            consumed = 0
            if (
                last_row is not None
                and last_row.ticker_reported is None
                and last_transaction_end_index == index - 1
            ):
                continuation = page_asset_continuation(lines, index)
                if continuation is not None:
                    asset_fragment, asset_type_code, consumed = continuation
                    append_asset_continuation(
                        last_row, asset_fragment, asset_type_code
                    )
            current_page = int(page_match.group(1))
            buffer = []
            index += 1 + consumed
            continue
        following = lines[index + 1] if index + 1 < len(lines) else None
        if following and PAGE_MARKER_RE.fullmatch(following):
            following = None
        candidate, match, consumed = transaction_candidate(line, following)
        if not match:
            description_match = re.search(r"Description\s*:\s*(.+)$", line, re.IGNORECASE)
            if description_match and last_row is not None:
                last_row.description_raw = description_match.group(1).strip()
            elif is_separator(line):
                buffer = []
            else:
                buffer.append(line)
            index += 1
            continue

        prefix = strip_table_header(candidate[: match.start()].strip())
        if prefix:
            buffer.append(prefix)
        (
            asset_name,
            owner_type,
            owner_raw,
            asset_type_code,
            ticker,
            source_transaction_id_raw,
        ) = asset_block(buffer)
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
            source_page_number=current_page,
            source_transaction_id_raw=source_transaction_id_raw,
            validation_errors=[],
        )
        row.validation_errors = validate_transaction(row)
        if row.validation_errors:
            row.parse_confidence = min(row.parse_confidence, Decimal("0.25"))
        rows.append(row)
        last_row = row
        last_transaction_end_index = index + consumed
        buffer = []
        index += 1 + consumed
    if not rows:
        if NO_TRANSACTIONS_RE.search(text):
            warnings.append("filing explicitly reports no transactions")
        else:
            warnings.append("no transaction rows matched the PTR parser")
    invalid_count = sum(bool(row.validation_errors) for row in rows)
    if invalid_count:
        warnings.append(f"{invalid_count} parsed transaction row(s) failed validation")
    signature_count = transaction_signature_count(text)
    if signature_count != len(rows):
        warnings.append(
            f"transaction coverage mismatch: detected {signature_count} signature(s) "
            f"but parsed {len(rows)} row(s)"
        )
    return rows, warnings


@dataclass
class HouseDocumentParse:
    """Result of the House-owned extraction, OCR, parse, and validation flow."""

    extracted: ExtractedDocument
    rows: list[TransactionRow]
    warnings: list[str]
    extraction_usable: bool
    ocr_attempted: bool = False
    ocr_selected: bool = False


def house_extraction_usable(
    extracted: ExtractedDocument, rows: list[TransactionRow]
) -> bool:
    """Apply House validation rules to an extraction without persisting it."""

    if not extracted.text:
        return False
    if extracted.extraction_type == "ocr":
        explicitly_empty = bool(not rows and NO_TRANSACTIONS_RE.search(extracted.text))
        return bool(rows or explicitly_empty) and transaction_signature_count(extracted.text) == len(rows) and not any(
            row.validation_errors for row in rows
        )
    if not extracted.has_embedded_text:
        return False
    return layout_fallback_reason(extracted, rows) is None


def _ocr_as_extracted(result: OCRResult) -> ExtractedDocument:
    return ExtractedDocument(
        text=result.text,
        page_count=result.page_count,
        has_embedded_text=False,
        warnings=list(result.warnings),
        extractor_name=result.extractor_name,
        extractor_version=result.extractor_version,
        extraction_type=result.extraction_type,
    )


def process_house_document(
    path: Path,
    *,
    requires_ocr: bool = False,
    extraction_service: DocumentExtractionService | None = None,
    ocr_service: Any | None = None,
) -> HouseDocumentParse:
    """Run the House orchestrator over one document.

    OCR is an alternate text source.  The generic OCR service does not decide
    when it runs, parse transactions, validate rows, or write to the database.
    """

    normal_extracted, normal_rows, normal_warnings = extract_and_parse_document(
        path, extraction_service
    )
    normal_usable = house_extraction_usable(normal_extracted, normal_rows)
    if not requires_ocr and normal_usable:
        return HouseDocumentParse(
            normal_extracted, normal_rows, normal_warnings, True
        )

    warnings = list(normal_warnings)
    reason = layout_fallback_reason(normal_extracted, normal_rows)
    if requires_ocr:
        warnings.insert(0, "document is marked requires_ocr; House parser requested OCR")
    elif reason:
        warnings.insert(0, f"House parser requested OCR after normal extraction: {reason}")
    service = ocr_service or default_ocr_service()
    try:
        ocr_result = service.extract(path)
    except OCRServiceUnavailable as exc:
        warnings.append(f"OCR unavailable: {exc}")
        return HouseDocumentParse(
            normal_extracted, [], warnings, False, ocr_attempted=True
        )
    except Exception as exc:
        warnings.append(f"OCR failed: {type(exc).__name__}: {exc}")
        return HouseDocumentParse(
            normal_extracted, [], warnings, False, ocr_attempted=True
        )

    ocr_extracted = _ocr_as_extracted(ocr_result)
    ocr_rows, ocr_warnings = parse_ptr_text(ocr_result.text)
    warnings.extend(ocr_warnings)
    ocr_usable = house_extraction_usable(ocr_extracted, ocr_rows)
    if ocr_usable:
        return HouseDocumentParse(
            ocr_extracted, ocr_rows, warnings, True,
            ocr_attempted=True, ocr_selected=True,
        )
    warnings.append("OCR text failed House validation; document remains needs_review")
    return HouseDocumentParse(
        normal_extracted, [], warnings, False, ocr_attempted=True
    )


def ensure_parse_job(cursor: Any, document: dict[str, Any], args: argparse.Namespace) -> tuple[int | None, bool]:
    cursor.execute(
        """SELECT document_job_id,status,attempt_count,max_attempts,started_at
           FROM document_jobs
           WHERE filing_id=%s AND document_id=%s AND job_type='parse'
           ORDER BY document_job_id DESC LIMIT 1""",
        (document["filing_id"], document["document_id"]),
    )
    existing = cursor.fetchone()
    if not existing:
        cursor.execute(
            """INSERT INTO document_jobs (filing_id,document_id,job_type,status,max_attempts)
               VALUES (%s,%s,'parse','queued',%s) RETURNING document_job_id""",
            (document["filing_id"], document["document_id"], args.max_attempts),
        )
        return cursor.fetchone()["document_job_id"], True

    job_id = existing["document_job_id"]
    status = existing["status"]
    attempts_exhausted = existing["attempt_count"] >= existing["max_attempts"]

    if status == "running":
        timeout = stale_job_timeout_seconds(args)
        if existing["started_at"] is None:
            stale = True
        else:
            cursor.execute(
                """SELECT (%s < CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')) AS stale""",
                (existing["started_at"], timeout),
            )
            stale = bool(cursor.fetchone()["stale"])
        if not stale:
            # A live worker owns this job, including when --reprocess was
            # requested.  Never steal an active parse.
            return job_id, False
        if attempts_exhausted:
            cursor.execute(
                """UPDATE document_jobs SET status='failed_permanent',finished_at=CURRENT_TIMESTAMP,
                   error_type=COALESCE(error_type,'stale_job_exhausted'),
                   error_message=COALESCE(error_message,'stale running job exhausted its lifetime retry budget')
                   WHERE document_job_id=%s AND status='running'""",
                (job_id,),
            )
            return job_id, False
        cursor.execute(
            """UPDATE document_jobs SET status='queued',finished_at=CURRENT_TIMESTAMP,
               next_attempt_at=CURRENT_TIMESTAMP,error_type='stale_job_recovered',
               error_message=CASE WHEN error_message IS NULL THEN
                   'Recovered stale running job after timeout'
                   ELSE error_message || '; recovered stale running job after timeout' END
               WHERE document_job_id=%s AND status='running'""",
            (job_id,),
        )
        if getattr(cursor, "rowcount", 1) == 0:
            # Another worker recovered the same stale row first.
            return job_id, False
        return job_id, True

    if status == "queued":
        return job_id, not attempts_exhausted

    if status == "failed_retryable":
        if args.reprocess:
            cursor.execute(
                """INSERT INTO document_jobs (filing_id,document_id,job_type,status,max_attempts)
                   VALUES (%s,%s,'parse','queued',%s) RETURNING document_job_id""",
                (document["filing_id"], document["document_id"], args.max_attempts),
            )
            return cursor.fetchone()["document_job_id"], True
        if attempts_exhausted or not args.retry_failed:
            return job_id, False
        cursor.execute(
            """UPDATE document_jobs SET status='queued',next_attempt_at=CURRENT_TIMESTAMP
               WHERE document_job_id=%s""",
            (job_id,),
        )
        return job_id, True

    if status in FINAL_JOB_STATUSES or status == "canceled":
        if not args.reprocess:
            return job_id, False
        # An explicit reprocess starts a new auditable cycle.  The previous
        # job's attempt history remains intact instead of being reset.
        cursor.execute(
            """INSERT INTO document_jobs (filing_id,document_id,job_type,status,max_attempts)
               VALUES (%s,%s,'parse','queued',%s) RETURNING document_job_id""",
            (document["filing_id"], document["document_id"], args.max_attempts),
        )
        return cursor.fetchone()["document_job_id"], True

    return job_id, False


def mark_running(cursor: Any, job_id: int) -> int:
    cursor.execute(
        """UPDATE document_jobs SET status='running',started_at=CURRENT_TIMESTAMP,
           finished_at=NULL,next_attempt_at=NULL,attempt_count=attempt_count+1,
           worker_name=COALESCE(worker_name,'house_ptr_pdf')
           WHERE document_job_id=%s AND status IN ('queued','failed_retryable')
             AND attempt_count < max_attempts
           RETURNING attempt_count""",
        (job_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise RuntimeError(f"Parse job {job_id} is not eligible to run")
    return row["attempt_count"]


def mark_failure(connection: Any, job_id: int, error: Exception) -> str:
    with connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """UPDATE document_jobs
                   SET status=CASE WHEN attempt_count < max_attempts
                                   THEN 'failed_retryable' ELSE 'failed_permanent' END,
                       finished_at=CURRENT_TIMESTAMP,error_type=%s,
                       error_message=LEFT(CASE WHEN error_message IS NULL THEN %s
                           ELSE error_message || '; ' || %s END,4000)
                   WHERE document_job_id=%s RETURNING status""",
                (type(error).__name__, str(error)[:2000], str(error)[:2000], job_id),
            )
            row = cursor.fetchone()
            return row["status"] if row else "failed_permanent"


def record_document_and_trades(
    connection: Any,
    document: dict[str, Any],
    job_id: int,
    extracted: ExtractedDocument,
    rows: list[TransactionRow],
    parse_warnings: list[str],
    force_review: bool = False,
) -> tuple[int, int, str]:
    pdf_path = Path(document["local_path"]).resolve()
    text_bytes = extracted.text.encode("utf-8", errors="replace")
    output_hash = shared_content_hash(text_bytes)
    text_path = extraction_text_path(pdf_path, extracted, output_hash)
    write_immutable_text(text_path, extracted.text)
    # The stored hash is computed from the exact bytes written to the immutable
    # artifact, so a stale or partially replaced file cannot be referenced.
    if sha256_file(text_path) != output_hash:
        raise ValueError(f"extraction artifact hash mismatch: {text_path}")
    all_warnings = extracted.warnings + parse_warnings
    valid_rows = [row for row in rows if not row.validation_errors]
    invalid_rows = [row for row in rows if row.validation_errors]
    has_complete_coverage = transaction_signature_count(extracted.text) == len(rows)
    explicitly_empty = bool(
        not rows and NO_TRANSACTIONS_RE.search(extracted.text)
    )
    if force_review:
        status = "needs_review"
    elif extracted.extraction_type != "ocr" and not extracted.has_embedded_text:
        status = "needs_ocr"
    elif explicitly_empty:
        status = "parsed_no_transactions"
    elif valid_rows and not invalid_rows and has_complete_coverage:
        status = "parsed"
    else:
        status = "needs_review"
    with connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """UPDATE documents SET page_count=%s,has_embedded_text=%s,requires_ocr=%s,
                   document_completeness_status=%s WHERE document_id=%s""",
                (
                    extracted.page_count,
                    extracted.has_embedded_text,
                    extracted.extraction_type == "ocr" or not extracted.has_embedded_text,
                    status,
                    document["document_id"],
                ),
            )
            cursor.execute(
                """UPDATE document_extractions SET is_preferred=FALSE
                   WHERE document_id=%s""",
                (document["document_id"],),
            )
            cursor.execute(
                """INSERT INTO document_extractions
                   (document_id,document_job_id,extraction_type,extractor_name,extractor_version,
                    output_path,output_hash,started_at,finished_at,quality_score,
                    characters_extracted,bytes_extracted,pages_processed,warnings,is_preferred)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,
                           %s,%s,%s,%s,%s,TRUE)
                   ON CONFLICT (document_id,extraction_type,extractor_name,extractor_version,output_hash)
                   DO UPDATE SET document_job_id=EXCLUDED.document_job_id,output_path=EXCLUDED.output_path,
                       finished_at=CURRENT_TIMESTAMP,quality_score=EXCLUDED.quality_score,
                       characters_extracted=EXCLUDED.characters_extracted,
                       bytes_extracted=EXCLUDED.bytes_extracted,pages_processed=EXCLUDED.pages_processed,
                       warnings=EXCLUDED.warnings,is_preferred=TRUE
                   RETURNING document_extraction_id""",
                (
                     document["document_id"],
                     job_id,
                     extracted.extraction_type,
                     extracted.extractor_name,
                     extracted.extractor_version,
                    str(text_path),
                    output_hash,
                    Decimal("1.0") if status in {"parsed", "parsed_no_transactions"} else Decimal("0.0"),
                    len(extracted.text),
                    len(text_bytes),
                    extracted.page_count,
                    Json(all_warnings),
                ),
            )
            extraction_id = cursor.fetchone()["document_extraction_id"]
            cursor.execute(
                """UPDATE trades SET is_current_parser_result=FALSE,updated_at=CURRENT_TIMESTAMP
                   WHERE filing_id=%s AND document_id=%s AND parser_name=%s
                     AND is_current_parser_result""",
                (document["filing_id"], document["document_id"], PARSER_NAME),
            )
            for row in rows:
                raw_record = {
                    "source_page_number": row.source_page_number,
                    "source_transaction_id_raw": row.source_transaction_id_raw,
                    "transaction_date": row.transaction_date.isoformat() if row.transaction_date else None,
                    "notification_date": row.notification_date.isoformat() if row.notification_date else None,
                    "owner_type": row.owner_type,
                    "owner_raw": row.owner_raw,
                    "transaction_type": row.transaction_type,
                    "transaction_type_raw": row.transaction_type_raw,
                    "asset_name_raw": row.asset_name_raw,
                    "asset_type_code_raw": row.asset_type_code_raw,
                    "ticker_reported": row.ticker_reported,
                    "amount_range_raw": row.amount_range_raw,
                    "description_raw": row.description_raw,
                }
                cursor.execute(
                    """INSERT INTO staging_house_trades
                       (filing_id,document_extraction_id,source_row_number,source_page_number,
                        source_transaction_id_raw,
                        transaction_date_raw,notification_date_raw,owner_raw,asset_name_raw,
                        asset_type_code_raw,transaction_type_raw,amount_raw,ticker_raw,
                        description_raw,raw_record,parse_warnings,validation_status,error_details,
                        parser_name,parser_version,parse_confidence)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (filing_id,source_row_number,document_extraction_id)
                       DO UPDATE SET source_page_number=EXCLUDED.source_page_number,
                           source_transaction_id_raw=EXCLUDED.source_transaction_id_raw,
                           transaction_date_raw=EXCLUDED.transaction_date_raw,
                           notification_date_raw=EXCLUDED.notification_date_raw,
                           owner_raw=EXCLUDED.owner_raw,asset_name_raw=EXCLUDED.asset_name_raw,
                           asset_type_code_raw=EXCLUDED.asset_type_code_raw,
                           transaction_type_raw=EXCLUDED.transaction_type_raw,
                           amount_raw=EXCLUDED.amount_raw,ticker_raw=EXCLUDED.ticker_raw,
                           description_raw=EXCLUDED.description_raw,raw_record=EXCLUDED.raw_record,
                           parse_warnings=EXCLUDED.parse_warnings,
                           validation_status=EXCLUDED.validation_status,
                           error_details=EXCLUDED.error_details,trade_id=NULL,
                           parser_name=EXCLUDED.parser_name,parser_version=EXCLUDED.parser_version,
                           parse_confidence=EXCLUDED.parse_confidence
                       RETURNING staging_house_trade_id""",
                    (
                     document["filing_id"], extraction_id, row.source_row_number,
                     row.source_page_number, row.source_transaction_id_raw,
                        row.transaction_date.isoformat() if row.transaction_date else None,
                        row.notification_date.isoformat() if row.notification_date else None,
                        row.owner_raw, row.asset_name_raw, row.asset_type_code_raw,
                        row.transaction_type_raw, row.amount_range_raw, row.ticker_reported,
                        row.description_raw, Json(raw_record), Json(parse_warnings),
                        "invalid" if row.validation_errors else "valid",
                        Json(row.validation_errors), PARSER_NAME, PARSER_VERSION,
                        row.parse_confidence,
                    ),
                )
                staging_id = cursor.fetchone()["staging_house_trade_id"]
                if row.validation_errors:
                    continue
                cursor.execute(
                    """INSERT INTO trades
                       (filing_id,document_id,document_extraction_id,source_row_number,
                        source_page_number,source_transaction_id_raw,
                        transaction_date,notification_date,filed_date,owner_type,owner_raw,
                        transaction_type,transaction_type_raw,asset_name_raw,asset_type_code_raw,
                        asset_type,ticker_reported,amount_range_raw,amount_min,amount_max,amount_exact,
                        capital_gains_over_200,description_raw,is_partial_sale,
                        is_annual_report_transaction,transaction_sequence,is_current_parser_result,
                        parser_name,parser_version,
                        parse_confidence,review_status)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE,%s,TRUE,%s,%s,%s,'unreviewed')
                       ON CONFLICT (filing_id,source_row_number,parser_name,parser_version)
                       DO UPDATE SET document_id=EXCLUDED.document_id,
                            document_extraction_id=EXCLUDED.document_extraction_id,
                            source_page_number=EXCLUDED.source_page_number,
                            source_transaction_id_raw=EXCLUDED.source_transaction_id_raw,
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
                           is_current_parser_result=TRUE,
                           parse_confidence=EXCLUDED.parse_confidence,updated_at=CURRENT_TIMESTAMP
                       RETURNING trade_id""",
                    (
                        document["filing_id"],
                        document["document_id"],
                        extraction_id,
                        row.source_row_number,
                        row.source_page_number,
                        row.source_transaction_id_raw,
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
                trade_id = cursor.fetchone()["trade_id"]
                cursor.execute(
                    """UPDATE staging_house_trades
                       SET validation_status='loaded',trade_id=%s
                       WHERE staging_house_trade_id=%s""",
                    (trade_id, staging_id),
                )
            cursor.execute(
                """UPDATE document_jobs SET document_id=%s,status=%s,finished_at=CURRENT_TIMESTAMP,
                   error_type=NULL,error_message=%s WHERE document_job_id=%s""",
                (
                    document["document_id"],
                    "complete" if status in {"parsed", "parsed_no_transactions"} else "needs_review",
                    "; ".join(all_warnings)[:4000] if all_warnings else None,
                    job_id,
                ),
            )
            cursor.execute(
                "UPDATE filings SET processing_status=%s WHERE filing_id=%s",
                (status, document["filing_id"]),
            )
    return extraction_id, len(valid_rows), status


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
                processed = process_house_document(
                    Path(document["local_path"]),
                    requires_ocr=bool(document.get("requires_ocr")),
                )
                extracted, rows, warnings = (
                    processed.extracted, processed.rows, processed.warnings
                )
                _, trade_count, document_status = record_document_and_trades(
                    connection, document, job_id, extracted, rows, warnings,
                    force_review=processed.ocr_attempted and not processed.ocr_selected,
                )
                if document_status in {"parsed", "parsed_no_transactions"}:
                    totals.parsed += 1
                    totals.trades += trade_count
                else:
                    totals.review += 1
                print(
                    f"  attempt={attempt} pages={extracted.page_count} "
                    f"extractor={extracted.extractor_name}/{extracted.extractor_version} "
                    f"trades={trade_count} status={document_status}",
                    flush=True,
                )
            except Exception as exc:
                # Keep the job record useful even when a PDF is corrupt or a
                # parser assumption does not fit an older form version.
                failure_status = mark_failure(connection, job_id, exc)
                totals.failed += 1
                print(f"  {failure_status}: {type(exc).__name__}: {exc}", flush=True)
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
