"""House PTR document processor built on the shared document services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ingestion.common.discovery import DiscoveredDocument
from ingestion.orchestrator import DocumentSelection, ProcessingOutcome
from scripts.parse_house_documents import (
    HouseDocumentParse,
    record_document_and_trades,
    process_house_document,
)


@dataclass
class ProcessorResult:
    document: HouseDocumentParse
    status: str
    trades: int = 0
    outcome: ProcessingOutcome | None = None


class HousePtrProcessor:
    """Thin adapter that keeps House recognition rules out of the orchestrator."""

    name = "house_ptr"

    def select_documents(
        self,
        documents: list[DiscoveredDocument],
        *,
        from_year: int | None = None,
        requires_ocr: bool = False,
        database_url: str | None = None,
    ) -> DocumentSelection:
        """Filter discovered House PDFs using filing metadata from PostgreSQL."""

        if from_year is None and not requires_ocr:
            return DocumentSelection(documents)
        if not database_url:
            raise RuntimeError(
                "DATABASE_URL is required for House PTR metadata filters; "
                "year and OCR selection cannot be inferred from file paths"
            )
        if not documents:
            return DocumentSelection(documents, ("House PTR selection: Documents selected: 0",))

        try:
            import psycopg2
        except ImportError as exc:  # pragma: no cover - environment setup path
            raise RuntimeError("psycopg2-binary is required for House PTR metadata filters") from exc

        hashes = [document.content_hash for document in documents]
        base_clauses = [
            "f.source = 'house_clerk_financial_disclosure'",
            "f.chamber = 'house'",
            "f.filing_type_code_raw = 'P'",
            "d.is_primary = TRUE",
            "d.local_path IS NOT NULL",
            "d.content_hash = ANY(%s)",
        ]
        base_params: list[Any] = [hashes]
        with psycopg2.connect(database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT d.content_hash, f.reporting_year, d.requires_ocr
                    FROM documents d
                    JOIN filings f ON f.filing_id = d.filing_id
                    WHERE {' AND '.join(base_clauses)}
                    """,
                    tuple(base_params),
                )
                eligible_rows = cursor.fetchall()

                clauses = list(base_clauses)
                params = list(base_params)
                if from_year is not None:
                    clauses.append("f.reporting_year >= %s")
                    params.append(from_year)
                if requires_ocr:
                    clauses.append("d.requires_ocr IS TRUE")
                cursor.execute(
                    f"""
                    SELECT d.content_hash
                    FROM documents d
                    JOIN filings f ON f.filing_id = d.filing_id
                    WHERE {' AND '.join(clauses)}
                    """,
                    tuple(params),
                )
                selected_hashes = {str(row[0]) for row in cursor.fetchall()}

        selected = [document for document in documents if document.content_hash in selected_hashes]
        excluded_before = 0
        if from_year is not None:
            excluded_before = sum(
                1
                for content_hash, reporting_year, row_requires_ocr in eligible_rows
                if content_hash not in selected_hashes
                and reporting_year is not None
                and reporting_year < from_year
                and (not requires_ocr or row_requires_ocr is True)
            )
        messages = [
            "House PTR selection",
            f"Reporting year: {from_year}+" if from_year is not None else "Reporting year: all",
            f"Requires OCR: {'yes' if requires_ocr else 'no'}",
            f"Documents selected: {len(selected):,}",
        ]
        if from_year is not None:
            messages.append(f"Documents excluded before {from_year}: {excluded_before:,}")
        return DocumentSelection(selected, tuple(messages))

    def process(
        self,
        path: Path,
        *,
        requires_ocr: bool = False,
        extraction_service: Any | None = None,
        ocr_service: Any | None = None,
    ) -> ProcessorResult:
        result = process_house_document(
            path,
            requires_ocr=requires_ocr,
            extraction_service=extraction_service,
            ocr_service=ocr_service,
        )
        success = result.extraction_usable
        status = "parsed" if success else "needs_review"
        return ProcessorResult(
            result,
            status,
            outcome=ProcessingOutcome(
                success=success,
                needs_review=not success,
                destination_category="processed" if success else "review",
            ),
        )

    def persist(
        self,
        connection: Any,
        metadata: dict[str, Any],
        job_id: int,
        result: ProcessorResult,
    ) -> ProcessorResult:
        _, trade_count, status = record_document_and_trades(
            connection,
            metadata,
            job_id,
            result.document.extracted,
            result.document.rows,
            result.document.warnings,
            force_review=not result.document.extraction_usable,
        )
        result.status = status
        result.trades = trade_count
        success = status in {"parsed", "parsed_no_transactions"}
        result.outcome = ProcessingOutcome(
            success=success,
            needs_review=not success,
            destination_category="processed" if success else "review",
        )
        return result
