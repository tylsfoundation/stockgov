"""House PTR document processor built on the shared document services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ingestion.orchestrator import ProcessingOutcome
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
