"""Reset derived House PTR parsing data while preserving source identities/files.

The operation is intentionally destructive and requires ``--confirm``.  Use
``--dry-run`` first to inspect the exact row counts and scope.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any


def load_dotenv() -> None:
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def database_url(override: str | None) -> str:
    load_dotenv()
    value = override or os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is not configured")
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reset derived House PTR parsing data")
    parser.add_argument("--dry-run", action="store_true", help="Show scope without modifying the database")
    parser.add_argument("--confirm", action="store_true", help="Authorize the destructive reset")
    parser.add_argument("--database-url", help="Override DATABASE_URL")
    return parser.parse_args(argv)


TARGET_QUERIES = {
    "trade_evidence": """
        SELECT count(*) FROM trade_evidence te
        WHERE te.trade_id IN (SELECT trade_id FROM trades WHERE parser_name='house_ptr_pdf')
           OR te.document_id IN (SELECT d.document_id FROM documents d JOIN filings f ON f.filing_id=d.filing_id WHERE f.chamber='house' AND f.filing_type_code_raw='P')
           OR te.document_extraction_id IN (SELECT de.document_extraction_id FROM document_extractions de WHERE de.document_id IN (SELECT d.document_id FROM documents d JOIN filings f ON f.filing_id=d.filing_id WHERE f.chamber='house' AND f.filing_type_code_raw='P'))
    """,
    "staging_house_trades": "SELECT count(*) FROM staging_house_trades",
    "trades": "SELECT count(*) FROM trades WHERE parser_name='house_ptr_pdf'",
    "document_extractions": """
        SELECT count(*) FROM document_extractions de
        WHERE de.document_id IN (SELECT d.document_id FROM documents d JOIN filings f ON f.filing_id=d.filing_id WHERE f.chamber='house' AND f.filing_type_code_raw='P')
    """,
    "parse_jobs": """
        SELECT count(*) FROM document_jobs j
        WHERE j.job_type='parse' AND j.filing_id IN (SELECT filing_id FROM filings WHERE chamber='house' AND filing_type_code_raw='P')
    """,
    "documents_reset": """
        SELECT count(*) FROM documents d JOIN filings f ON f.filing_id=d.filing_id
        WHERE f.chamber='house' AND f.filing_type_code_raw='P' AND (d.page_count IS NOT NULL OR d.has_embedded_text IS NOT NULL OR d.document_completeness_status IS NOT NULL)
    """,
}


def counts(cursor: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for name, query in TARGET_QUERIES.items():
        cursor.execute(query)
        result[name] = int(cursor.fetchone()[0])
    return result


def reset(cursor: Any) -> None:
    cursor.execute("""
        DELETE FROM trade_evidence te
        WHERE te.trade_id IN (SELECT trade_id FROM trades WHERE parser_name='house_ptr_pdf')
           OR te.document_id IN (SELECT d.document_id FROM documents d JOIN filings f ON f.filing_id=d.filing_id WHERE f.chamber='house' AND f.filing_type_code_raw='P')
           OR te.document_extraction_id IN (SELECT de.document_extraction_id FROM document_extractions de WHERE de.document_id IN (SELECT d.document_id FROM documents d JOIN filings f ON f.filing_id=d.filing_id WHERE f.chamber='house' AND f.filing_type_code_raw='P'))
    """)
    # No other table references staging rows; transactional TRUNCATE removes
    # the physical rows before the parent trade delete and avoids an O(n²)
    # foreign-key scan on the unindexed staging trade_id column.
    cursor.execute("TRUNCATE TABLE staging_house_trades RESTART IDENTITY")
    cursor.execute("DELETE FROM trades WHERE parser_name='house_ptr_pdf'")
    cursor.execute("""
        DELETE FROM document_extractions de
        WHERE de.document_id IN (SELECT d.document_id FROM documents d JOIN filings f ON f.filing_id=d.filing_id WHERE f.chamber='house' AND f.filing_type_code_raw='P')
    """)
    cursor.execute("""
        DELETE FROM document_jobs j
        WHERE j.job_type='parse' AND j.filing_id IN (SELECT filing_id FROM filings WHERE chamber='house' AND filing_type_code_raw='P')
    """)
    cursor.execute("""
        UPDATE documents d SET page_count=NULL, has_embedded_text=NULL,
            document_completeness_status=NULL
        FROM filings f
        WHERE f.filing_id=d.filing_id AND f.chamber='house' AND f.filing_type_code_raw='P'
    """)
    cursor.execute("""
        UPDATE filings SET processing_status='verified'
        WHERE chamber='house' AND filing_type_code_raw='P'
    """)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.dry_run and not args.confirm:
        print("Refusing destructive reset without --confirm. Use --dry-run first.")
        return 2
    try:
        import psycopg2
    except ImportError as exc:
        raise RuntimeError("psycopg2-binary is required") from exc
    with psycopg2.connect(database_url(args.database_url)) as connection:
        with connection.cursor() as cursor:
            before = counts(cursor)
            print("House PTR reset scope (rows before):")
            for name, value in before.items():
                print(f"  {name}: {value:,}")
            if args.dry_run:
                print("Dry run complete; no database changes were made.")
                return 0
            reset(cursor)
            after = counts(cursor)
            print("House PTR reset complete (rows after):")
            for name, value in after.items():
                print(f"  {name}: {value:,}")
            print("Preserved: source PDFs, documents, filings, members, and source metadata.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
