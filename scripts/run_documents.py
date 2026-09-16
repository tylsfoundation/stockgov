"""Thin entry point for the reusable document orchestrator.

Use ``parse_house_documents.py`` for the database-backed legacy command while
the shared orchestrator is being adopted by additional document processors.
This command is useful for discovery and processor smoke tests.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ingestion.common.config import ProcessingConfig
from ingestion.orchestrator import DocumentOrchestrator
from ingestion.processors.house_ptr import HousePtrProcessor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a configured document processor")
    parser.add_argument("--document-type", default="house_ptr")
    parser.add_argument("--mode", choices=("bulk", "incremental"), default="incremental")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--requires-ocr", action="store_true")
    args = parser.parse_args(argv)
    config = ProcessingConfig.from_environment()
    logging.basicConfig(level=logging.DEBUG if config.debug else logging.INFO)
    if args.root:
        root = args.root
    elif args.mode == "incremental":
        root = config.incoming_directory or config.document_root
    else:
        root = config.document_root
    orchestrator = DocumentOrchestrator(config, {"house_ptr": HousePtrProcessor()})
    summary = orchestrator.run(
        args.document_type,
        root=root,
        limit=args.limit,
        requires_ocr=args.requires_ocr,
    )
    print(
        f"mode={args.mode} discovered={summary.discovered} processed={summary.processed} "
        f"parsed={summary.parsed} review={summary.review} skipped={summary.skipped} "
        f"failed={summary.failed}"
    )
    for message in summary.messages:
        print(f"  {message}")
    return 1 if summary.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
