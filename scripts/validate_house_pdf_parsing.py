"""Pull House PDF-ingestion records from PostgreSQL and write a QA log.

The script is deliberately read-only.  It follows each selected filing through
its document, parse job, text extraction, and parsed trades, then records the
database values and consistency checks in a log file.  It can optionally check
that the PDF and extracted text files still exist and match their stored hashes.

Examples::

    py scripts/validate_house_pdf_parsing.py
    py scripts/validate_house_pdf_parsing.py --docid 20016861 --check-files
    py scripts/validate_house_pdf_parsing.py --year 2025 --log-file logs/ptr_2025.log

Requirements: ``py -m pip install psycopg2-binary``
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlunsplit

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError as exc:  # pragma: no cover - exercised when dependency is absent
    raise SystemExit(
        "psycopg2-binary is required: py -m pip install psycopg2-binary"
    ) from exc


SOURCE = "house_clerk_financial_disclosure"
PARSER_NAME = "house_ptr_pdf"
PARSER_VERSION = "1.3.0"
TRANSACTION_SIGNATURE_RE = re.compile(
    r"[PSE]\s*(?:\(\s*partial\s*\))?\s*"
    r"\d{1,2}/\d{1,2}/\d{4}\s*"
    r"\d{1,2}/\d{1,2}/\d{4}\s*"
    r"(?:Spouse\s*/\s*DC\s+Over\s+\$?\s*[\d,]+|"
    r"N/?A|<\s*\$?\s*[\d,]+|"
    r"\$?\s*[\d,]+(?:\s*[-\u00ad\u2010-\u2015\u2212]\s*\$?\s*[\d,]+)?)",
    re.IGNORECASE,
)


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_dotenv() -> None:
    path = project_root() / ".env"
    if not path.is_file():
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
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    if not user or not password:
        raise RuntimeError("Database credentials are not configured")
    auth = (
        f"{quote(user)}:{quote(password)}@{os.getenv('POSTGRES_HOST', 'localhost')}"
        f":{os.getenv('POSTGRES_PORT', '5433')}"
    )
    return urlunsplit(
        ("postgresql", auth, "/" + os.getenv("POSTGRES_DB", "congress_trades"), "", "")
    )


class Tee:
    """Write each log line to both the console and the requested file."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("w", encoding="utf-8")

    def write(self, message: str = "") -> None:
        print(message, flush=True)
        print(message, file=self.file, flush=True)

    def close(self) -> None:
        self.file.close()


@dataclass
class Result:
    passed: int = 0
    warnings: int = 0
    failed: int = 0


class Check:
    def __init__(self, output: Tee) -> None:
        self.output = output
        self.result = Result()

    def ok(self, message: str) -> None:
        self.result.passed += 1
        self.output.write(f"  PASS: {message}")

    def warn(self, message: str) -> None:
        self.result.warnings += 1
        self.output.write(f"  WARN: {message}")

    def fail(self, message: str) -> None:
        self.result.failed += 1
        self.output.write(f"  FAIL: {message}")

    def test(self, condition: bool, good: str, bad: str) -> None:
        self.ok(good) if condition else self.fail(bad)


def rows(cursor: Any, sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cursor.execute(sql, parameters)
    return list(cursor.fetchall())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_path(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else project_root() / path


def build_filters(args: argparse.Namespace) -> tuple[str, list[Any]]:
    clauses = [
        "f.source = %s",
        "f.chamber = 'house'",
        "f.filing_type_code_raw = 'P'",
    ]
    parameters: list[Any] = [SOURCE]
    if not args.include_non_primary:
        clauses.append("d.is_primary = TRUE")
    if args.docid:
        clauses.append("f.source_filing_id = ANY(%s)")
        parameters.append(args.docid)
    if args.member:
        clauses.append("f.raw_full_name ILIKE %s")
        parameters.append(f"%{args.member}%")
    if args.year is not None:
        clauses.append("f.reporting_year = %s")
        parameters.append(args.year)
    if args.from_year is not None:
        clauses.append("f.reporting_year >= %s")
        parameters.append(args.from_year)
    if args.to_year is not None:
        clauses.append("f.reporting_year <= %s")
        parameters.append(args.to_year)
    return " AND ".join(clauses), parameters


def selected_documents(
    cursor: Any, args: argparse.Namespace
) -> list[dict[str, Any]]:
    where, parameters = build_filters(args)
    limit_sql = " LIMIT %s" if args.limit is not None else ""
    if args.limit is not None:
        parameters.append(args.limit)
    return rows(
        cursor,
        f"""
        SELECT
            d.document_id,
            d.filing_id,
            d.document_type,
            d.source_url,
            d.local_path,
            d.file_size_bytes,
            d.content_hash,
            d.downloaded_at,
            d.http_status,
            d.is_primary,
            d.page_count,
            d.has_embedded_text,
            d.requires_ocr,
            d.detected_form_version,
            d.document_completeness_status,
            d.verification_status,
            f.source_filing_id AS doc_id,
            f.reporting_year,
            f.filing_type_code_raw,
            f.raw_full_name,
            f.processing_status AS filing_processing_status,
            j.document_job_id,
            j.status AS parse_job_status,
            j.attempt_count AS parse_attempt_count,
            j.finished_at AS parse_finished_at,
            j.error_type AS parse_error_type,
            j.error_message AS parse_error_message,
            e.document_extraction_id,
            e.document_job_id AS extraction_job_id,
            e.extraction_type,
            e.extractor_name,
            e.extractor_version,
            e.output_path,
            e.output_hash,
            e.characters_extracted,
            e.bytes_extracted,
            e.pages_processed,
            e.quality_score,
            e.warnings AS extraction_warnings,
            e.is_preferred,
            COALESCE(t.trade_count, 0)::bigint AS trade_count,
            t.first_transaction_date,
            t.last_transaction_date,
            COALESCE(s.staged_loaded_count, 0)::bigint AS staged_loaded_count,
            COALESCE(s.staged_invalid_count, 0)::bigint AS staged_invalid_count
        FROM documents d
        JOIN filings f ON f.filing_id = d.filing_id
        LEFT JOIN LATERAL (
            SELECT dj.document_job_id, dj.status, dj.attempt_count, dj.finished_at,
                   dj.error_type, dj.error_message
            FROM document_jobs dj
            WHERE dj.document_id = d.document_id AND dj.job_type = 'parse'
            ORDER BY dj.document_job_id DESC
            LIMIT 1
        ) j ON TRUE
        LEFT JOIN LATERAL (
            SELECT de.document_extraction_id, de.document_job_id, de.extraction_type,
                   de.extractor_name, de.extractor_version, de.output_path,
                   de.output_hash, de.characters_extracted, de.bytes_extracted,
                   de.pages_processed,
                   de.quality_score, de.warnings, de.is_preferred
            FROM document_extractions de
            WHERE de.document_id = d.document_id
              AND de.extraction_type IN ('embedded_text', 'ocr')
            ORDER BY de.is_preferred DESC, de.document_extraction_id DESC
            LIMIT 1
        ) e ON TRUE
        LEFT JOIN LATERAL (
            SELECT count(*) AS trade_count,
                   min(tr.transaction_date) AS first_transaction_date,
                   max(tr.transaction_date) AS last_transaction_date
            FROM trades tr
            WHERE tr.document_id = d.document_id
              AND tr.document_extraction_id = e.document_extraction_id
              AND tr.parser_name = %s
              AND tr.parser_version = %s
              AND tr.is_current_parser_result IS TRUE
        ) t ON TRUE
        LEFT JOIN LATERAL (
            SELECT
                count(*) FILTER (
                    WHERE sh.validation_status = 'loaded'
                ) AS staged_loaded_count,
                count(*) FILTER (
                    WHERE sh.validation_status = 'invalid'
                ) AS staged_invalid_count
            FROM staging_house_trades sh
            WHERE sh.document_extraction_id = e.document_extraction_id
              AND sh.parser_name = %s
              AND sh.parser_version = %s
        ) s ON TRUE
        WHERE {where}
        ORDER BY f.reporting_year NULLS LAST, f.source_filing_id, d.document_id
        {limit_sql}
        """,
        tuple([PARSER_NAME, PARSER_VERSION, PARSER_NAME, PARSER_VERSION, *parameters]),
    )


def trade_rows(cursor: Any, extraction_ids: list[int]) -> list[dict[str, Any]]:
    if not extraction_ids:
        return []
    return rows(
        cursor,
        """
        SELECT t.document_id, t.trade_id, t.source_row_number,
               t.source_transaction_id_raw,
               t.transaction_date, t.notification_date, t.owner_type,
               t.asset_name_raw, t.ticker_reported, t.ticker_inferred,
               t.transaction_type, t.amount_range_raw, t.parse_confidence,
               t.review_status, t.document_extraction_id, t.parser_version
        FROM trades t
        WHERE t.document_extraction_id = ANY(%s)
          AND t.parser_name = %s
          AND t.parser_version = %s
          AND t.is_current_parser_result IS TRUE
        ORDER BY t.document_id, t.source_row_number, t.trade_id
        """,
        (extraction_ids, PARSER_NAME, PARSER_VERSION),
    )


def check_schema(cursor: Any, check: Check) -> None:
    required_tables = {
        "documents",
        "document_jobs",
        "document_extractions",
        "filings",
        "staging_house_trades",
        "trades",
    }
    found = {
        row["table_name"]
        for row in rows(
            cursor,
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = ANY(%s)
            """,
            (list(required_tables),),
        )
    }
    check.test(
        found == required_tables,
        "Required PDF-ingestion tables exist",
        f"Missing PDF-ingestion tables: {sorted(required_tables - found)}",
    )
    required_columns = {
        "documents": {"local_path", "content_hash", "document_completeness_status"},
        "document_jobs": {"document_id", "job_type", "status"},
        "document_extractions": {
            "document_id",
            "document_job_id",
            "output_path",
            "output_hash",
            "bytes_extracted",
            "is_preferred",
        },
        "trades": {
            "document_id",
            "document_extraction_id",
            "source_row_number",
            "parser_name",
            "parser_version",
            "source_page_number",
            "source_transaction_id_raw",
            "is_current_parser_result",
        },
        "staging_house_trades": {
            "document_extraction_id",
            "source_row_number",
            "source_page_number",
            "source_transaction_id_raw",
            "validation_status",
            "parser_name",
            "parser_version",
        },
    }
    column_rows = rows(
        cursor,
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = ANY(%s)
        """,
        (list(required_columns),),
    )
    actual: dict[str, set[str]] = defaultdict(set)
    for row in column_rows:
        actual[row["table_name"]].add(row["column_name"])
    missing = {
        table: sorted(columns - actual.get(table, set()))
        for table, columns in required_columns.items()
        if columns - actual.get(table, set())
    }
    check.test(not missing, "Required PDF-ingestion columns exist", f"Missing columns: {missing}")


def check_global_integrity(cursor: Any, check: Check) -> None:
    orphan_count = rows(
        cursor,
        """
        SELECT count(*) AS n
        FROM trades t
        LEFT JOIN documents d ON d.document_id = t.document_id
        LEFT JOIN filings f ON f.filing_id = t.filing_id
        LEFT JOIN document_extractions e
               ON e.document_extraction_id = t.document_extraction_id
        WHERE t.parser_name = %s
          AND t.parser_version = %s
          AND (d.document_id IS NULL OR f.filing_id IS NULL
               OR t.document_id IS NULL OR t.filing_id <> d.filing_id
               OR t.document_extraction_id IS NULL
               OR e.document_id IS NULL OR e.document_id <> d.document_id)
        """,
        (PARSER_NAME, PARSER_VERSION),
    )[0]["n"]
    check.test(
        orphan_count == 0,
        "Parsed trades have valid filing, document, and extraction links",
        f"Orphaned or mis-linked parsed trades: {orphan_count}",
    )
    duplicate_count = rows(
        cursor,
        """
        SELECT count(*) AS n
        FROM (
            SELECT filing_id, source_row_number, parser_name, parser_version
            FROM trades
            WHERE parser_name = %s AND parser_version = %s
              AND is_current_parser_result IS TRUE
            GROUP BY filing_id, source_row_number, parser_name, parser_version
            HAVING count(*) > 1
        ) duplicates
        """,
        (PARSER_NAME, PARSER_VERSION),
    )[0]["n"]
    check.test(
        duplicate_count == 0,
        "No duplicate source rows exist for the PDF parser",
        f"Duplicate PDF-parser source rows: {duplicate_count}",
    )


def check_artifacts(document: dict[str, Any], check: Check) -> None:
    label = f"DocID {document['doc_id']}"
    pdf = artifact_path(document.get("local_path"))
    if pdf is None or not pdf.is_file():
        check.fail(f"{label}: PDF file is missing ({document.get('local_path') or '<no path>'})")
    else:
        actual_hash = sha256(pdf)
        expected_hash = (document.get("content_hash") or "").lower()
        check.test(
            not expected_hash or actual_hash.lower() == expected_hash,
            f"{label}: PDF SHA-256 matches documents.content_hash",
            f"{label}: PDF hash mismatch (db={expected_hash or '<blank>'}, file={actual_hash})",
        )
        expected_size = document.get("file_size_bytes")
        if expected_size is not None:
            check.test(
                pdf.stat().st_size == expected_size,
                f"{label}: PDF size matches file_size_bytes",
                f"{label}: PDF size mismatch (db={expected_size}, file={pdf.stat().st_size})",
            )

    output = artifact_path(document.get("output_path"))
    if output is None or not output.is_file():
        check.fail(f"{label}: extracted-text file is missing ({document.get('output_path') or '<no path>'})")
        return
    actual_hash = sha256(output)
    expected_hash = (document.get("output_hash") or "").lower()
    check.test(
        bool(expected_hash) and actual_hash.lower() == expected_hash,
        f"{label}: extracted-text SHA-256 matches output_hash",
        f"{label}: extracted-text hash mismatch (db={expected_hash or '<blank>'}, file={actual_hash})",
    )
    extracted_text = output.read_text(encoding="utf-8", errors="replace")
    expected_chars = document.get("characters_extracted")
    if expected_chars is not None:
        actual_chars = len(extracted_text)
        check.test(
            actual_chars == expected_chars,
            f"{label}: extracted character count matches",
            f"{label}: extracted character count mismatch (db={expected_chars}, file={actual_chars})",
        )
    signature_count = len(TRANSACTION_SIGNATURE_RE.findall(extracted_text))
    trade_count = int(document.get("trade_count") or 0)
    check.test(
        signature_count == trade_count,
        f"{label}: transaction coverage is complete ({trade_count} signature(s)/trade(s))",
        f"{label}: transaction coverage mismatch "
        f"(text signatures={signature_count}, current {PARSER_VERSION} trades={trade_count})",
    )


def print_document(
    output: Tee, document: dict[str, Any], document_trades: list[dict[str, Any]]
) -> None:
    output.write(f"\nDocID {document['doc_id']} | filing_id={document['filing_id']} | document_id={document['document_id']}")
    output.write(
        f"  filer={document.get('raw_full_name') or '<blank>'} | year={document.get('reporting_year') or '<blank>'}"
        f" | type={document.get('filing_type_code_raw') or '<blank>'}"
    )
    output.write(
        f"  document={document.get('document_type') or '<blank>'} | primary={document.get('is_primary')}"
        f" | verification={document.get('verification_status') or '<blank>'}"
    )
    output.write(f"  local_path={document.get('local_path') or '<blank>'}")
    output.write(
        f"  pages={document.get('page_count')!s} | embedded_text={document.get('has_embedded_text')!s}"
        f" | requires_ocr={document.get('requires_ocr')!s} | completeness={document.get('document_completeness_status') or '<blank>'}"
    )
    output.write(
        f"  parse_job={document.get('document_job_id') or '<none>'}"
        f"/{document.get('parse_job_status') or 'not_started'}"
        f" (attempts={document.get('parse_attempt_count') or 0})"
    )
    output.write(
        f"  extraction={document.get('document_extraction_id') or '<none>'}"
        f"/{document.get('extraction_type') or 'none'}"
        f" extractor={document.get('extractor_name') or '<none>'}"
        f"/{document.get('extractor_version') or '<none>'}"
        f" preferred={document.get('is_preferred')!s}"
        f" chars={document.get('characters_extracted')!s}"
        f" pages={document.get('pages_processed')!s}"
    )
    output.write(
        f"  trades={document.get('trade_count') or 0}"
        f" | staging_loaded={document.get('staged_loaded_count') or 0}"
        f" | staging_invalid={document.get('staged_invalid_count') or 0}"
        f" | transaction_dates={document.get('first_transaction_date') or '<none>'}"
        f"..{document.get('last_transaction_date') or '<none>'}"
    )
    if document.get("parse_error_message"):
        output.write(f"  parse_error={document['parse_error_message']}")
    if document.get("output_path"):
        output.write(f"  extracted_text_path={document['output_path']}")
    for trade in document_trades[:5]:
        output.write(
            f"  trade_id={trade['trade_id']} row={trade['source_row_number']}"
            f" source_transaction_id={trade.get('source_transaction_id_raw') or '<blank>'}"
            f" date={trade.get('transaction_date') or '<blank>'}"
            f" owner={trade.get('owner_type') or '<blank>'}"
            f" asset={trade.get('asset_name_raw') or '<blank>'}"
            f" ticker={trade.get('ticker_reported') or trade.get('ticker_inferred') or '<blank>'}"
            f" type={trade.get('transaction_type') or '<blank>'}"
            f" amount={trade.get('amount_range_raw') or '<blank>'}"
            f" confidence={trade.get('parse_confidence')!s}"
        )
    if len(document_trades) > 5:
        output.write(f"  ... {len(document_trades) - 5} additional trades omitted from detail")


def validate_document(document: dict[str, Any], check: Check, check_files: bool) -> None:
    label = f"DocID {document['doc_id']}"
    check.test(
        bool(document.get("document_id")) and bool(document.get("filing_id")),
        f"{label}: document and filing identities are present",
        f"{label}: document or filing identity is missing",
    )
    job_status = document.get("parse_job_status")
    extraction_id = document.get("document_extraction_id")
    if job_status == "complete":
        check.test(
            extraction_id is not None,
            f"{label}: completed parse has an extraction row",
            f"{label}: parse is complete but has no extraction row",
        )
        if extraction_id is not None:
            check.test(
                bool(document.get("is_preferred")),
                f"{label}: extraction is marked preferred",
                f"{label}: extraction exists but is not marked preferred",
            )
        if document.get("document_completeness_status") in {"parsed", "parsed_no_transactions"}:
            check.ok(
                f"{label}: document completeness is "
                f"{document.get('document_completeness_status')}"
            )
        else:
            check.warn(
                f"{label}: parse completed with completeness status "
                f"{document.get('document_completeness_status') or '<blank>'}"
            )
        if document.get("trade_count", 0):
            check.ok(f"{label}: {document['trade_count']} parsed trade(s) are linked")
        elif document.get("document_completeness_status") == "parsed":
            check.fail(
                f"{label}: document is marked parsed but has no current "
                f"{PARSER_VERSION} trades"
            )
        else:
            check.warn(f"{label}: parse completed but no trades were extracted")
    elif job_status in {"failed_retryable", "failed_permanent"}:
        check.fail(f"{label}: latest parse job status is {job_status}")
    elif job_status == "needs_review":
        check.warn(f"{label}: latest parse job needs review")
    elif job_status:
        check.warn(f"{label}: latest parse job is {job_status}")
    else:
        check.warn(f"{label}: no parse job exists yet")
    if document.get("requires_ocr"):
        check.warn(f"{label}: document is flagged as requiring OCR")
    staged_invalid_count = int(document.get("staged_invalid_count") or 0)
    check.test(
        staged_invalid_count == 0,
        f"{label}: no current staged rows failed validation",
        f"{label}: {staged_invalid_count} current staged row(s) failed validation",
    )
    staged_loaded_count = int(document.get("staged_loaded_count") or 0)
    trade_count = int(document.get("trade_count") or 0)
    check.test(
        staged_loaded_count == trade_count,
        f"{label}: staged loaded rows match linked trades ({trade_count})",
        f"{label}: staged loaded rows ({staged_loaded_count}) do not match "
        f"linked trades ({trade_count})",
    )
    if check_files:
        check_artifacts(document, check)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pull House PDF records from PostgreSQL and write a read-only QA log"
    )
    parser.add_argument("--docid", action="append", help="House DocID; repeat for multiple IDs")
    parser.add_argument("--member", help="Case-insensitive filer-name fragment")
    parser.add_argument("--year", type=int)
    parser.add_argument("--from-year", type=int)
    parser.add_argument("--to-year", type=int)
    parser.add_argument("--limit", type=int, help="Maximum number of documents to log")
    parser.add_argument(
        "--include-non-primary",
        action="store_true",
        help="Include non-primary document rows in addition to primary documents",
    )
    parser.add_argument(
        "--check-files",
        action="store_true",
        help="Verify local PDF/text files and their stored hashes",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=project_root() / "logs" / "house_pdf_qa.log",
    )
    parser.add_argument("--database-url")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.from_year is not None and args.to_year is not None and args.from_year > args.to_year:
        parser.error("--from-year cannot be greater than --to-year")

    output = Tee(args.log_file.resolve())
    check = Check(output)
    started = datetime.now().astimezone()
    clock = time.monotonic()
    exit_code = 2
    output.write("=" * 78)
    output.write(f"House PDF database QA start time: {started.isoformat()}")
    output.write(f"Log file: {args.log_file.resolve()}")
    output.write(f"File checks: {'enabled' if args.check_files else 'disabled'}")
    output.write(f"Parser under test: {PARSER_NAME}/{PARSER_VERSION}")
    try:
        connection = psycopg2.connect(database_url(args.database_url), cursor_factory=RealDictCursor)
        connection.set_session(readonly=True, autocommit=False)
        try:
            with connection.cursor() as cursor:
                output.write("\n[01] Verify PDF-ingestion schema")
                check_schema(cursor, check)
                output.write("\n[02] Pull selected filings, documents, jobs, extractions, and trades")
                documents = selected_documents(cursor, args)
                extraction_ids = [
                    int(document["document_extraction_id"])
                    for document in documents
                    if document.get("document_extraction_id") is not None
                ]
                parsed_trades = trade_rows(cursor, extraction_ids)
                by_document: dict[int, list[dict[str, Any]]] = defaultdict(list)
                for trade in parsed_trades:
                    by_document[int(trade["document_id"])].append(trade)
                output.write(f"  Selected documents: {len(documents):,}")
                output.write(f"  Linked parsed trades: {len(parsed_trades):,}")
                if not documents:
                    check.warn("No House PTR documents matched the supplied filters")
                for document in documents:
                    print_document(output, document, by_document[int(document["document_id"])])
                    validate_document(document, check, args.check_files)

                output.write("\n[03] Check parsed-trade relationships and duplicate source rows")
                check_global_integrity(cursor, check)

                job_counts = Counter(document.get("parse_job_status") or "not_started" for document in documents)
                completeness_counts = Counter(
                    document.get("document_completeness_status") or "<blank>" for document in documents
                )
                output.write("\n[04] QA summary by selected document")
                output.write(f"  Parse jobs   : {dict(sorted(job_counts.items()))}")
                output.write(f"  Completeness : {dict(sorted(completeness_counts.items()))}")
                output.write(f"  Extractions  : {sum(document.get('document_extraction_id') is not None for document in documents):,}")
                output.write(f"  Trades       : {len(parsed_trades):,}")
        finally:
            connection.rollback()
            connection.close()
        exit_code = 1 if check.result.failed else 0
    except (OSError, RuntimeError, psycopg2.Error) as exc:
        output.write(f"\nCONFIGURATION OR EXECUTION ERROR: {exc}")
        exit_code = 2
    finally:
        elapsed = time.monotonic() - clock
        output.write("\n[05] Result")
        output.write(f"  Passed checks : {check.result.passed}")
        output.write(f"  Warnings      : {check.result.warnings}")
        output.write(f"  Failed checks : {check.result.failed}")
        output.write(f"  RESULT        : {'PASSED' if exit_code == 0 else 'FAILED' if exit_code == 1 else 'ERROR'}")
        output.write(f"  QA end time   : {datetime.now().astimezone().isoformat()}")
        output.write(f"  Elapsed       : {elapsed:.2f} seconds")
        output.write(f"  Exit status   : {exit_code}")
        output.write("=" * 78)
        output.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
