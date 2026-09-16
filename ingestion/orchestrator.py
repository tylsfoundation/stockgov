"""Generic document orchestrator with bulk and incremental modes."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from ingestion.common.config import ProcessingConfig
from ingestion.common.discovery import DiscoveredDocument, discover_documents
from ingestion.common.persistence import move_after_commit


@dataclass
class RunSummary:
    discovered: int = 0
    processed: int = 0
    parsed: int = 0
    review: int = 0
    failed: int = 0
    skipped: int = 0
    messages: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProcessingOutcome:
    """Generic processor result semantics shared across document types."""

    success: bool
    needs_review: bool = False
    skipped: bool = False
    destination_category: str | None = None


def outcome_for(result: Any) -> ProcessingOutcome:
    """Read generic outcome fields without knowing document-specific statuses."""

    outcome = getattr(result, "outcome", None)
    if isinstance(outcome, ProcessingOutcome):
        return outcome
    success = bool(getattr(result, "success", False))
    needs_review = bool(getattr(result, "needs_review", not success))
    skipped = bool(getattr(result, "skipped", False))
    category = getattr(result, "destination_category", None)
    if category is None:
        category = "review" if needs_review else "processed"
    return ProcessingOutcome(success, needs_review, skipped, category)


class DocumentOrchestrator:
    """Route discovered files to registered processors without domain logic."""

    def __init__(self, config: ProcessingConfig, processors: dict[str, Any], logger: logging.Logger | None = None):
        self.config = config
        self.processors = processors
        self.logger = logger or logging.getLogger(__name__)

    def discover(self, root: Path | None = None, known_hashes: set[str] | None = None) -> list[DiscoveredDocument]:
        return discover_documents(root or self.config.document_root, known_hashes=known_hashes)

    def run(
        self,
        document_type: str,
        *,
        root: Path | None = None,
        limit: int | None = None,
        requires_ocr: bool = False,
        handler: Callable[[Any, DiscoveredDocument], Any] | None = None,
    ) -> RunSummary:
        summary = RunSummary()
        documents = self.discover(root)
        if limit is not None:
            documents = documents[:limit]
        summary.discovered = len(documents)
        processor = self.processors.get(document_type)
        if processor is None:
            raise ValueError(f"No processor registered for {document_type!r}")
        for item in documents:
            try:
                result = handler(processor, item) if handler else processor.process(item.path, requires_ocr=requires_ocr)
                outcome = outcome_for(result)
                if outcome.skipped:
                    summary.skipped += 1
                    continue
                summary.processed += 1
                if outcome.success and not outcome.needs_review:
                    summary.parsed += 1
                else:
                    summary.review += 1
                # A handler represents the DB-backed path and must return
                # only after its transaction commits.  Discovery-only runs do
                # not move files.
                if handler and not self.config.debug and not self.config.dry_run:
                    destination = self.config.processed_directory if outcome.destination_category == "processed" else self.config.review_directory
                    if destination:
                        move_after_commit(item.path, destination)
                if self.config.debug:
                    self.logger.info("processed %s outcome=%s", item.path, outcome)
            except Exception as exc:  # continue a bulk run after one bad file
                summary.failed += 1
                summary.messages.append(f"{item.path}: {type(exc).__name__}: {exc}")
                self.logger.exception("document failed: %s", item.path)
        return summary
