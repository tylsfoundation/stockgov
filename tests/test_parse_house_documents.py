"""Focused regression tests for House PTR text parsing."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import parse_house_documents as parser  # noqa: E402
import validate_house_pdf_parsing as qa  # noqa: E402
from ocr_service import OCRResult  # noqa: E402


DOC_20030891_TEXT = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "raw"
    / "house_documents"
    / "2025"
    / "ptr"
    / "20030891.pypdf-1.2.0.txt"
)
DOC_20009299_TEXT = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "raw"
    / "house_documents"
    / "2018"
    / "ptr"
    / "20009299.pypdf-1.2.0.txt"
)


class HousePtrParserTests(unittest.TestCase):
    def parse(self, *lines: str) -> tuple[list[parser.TransactionRow], list[str]]:
        return parser.parse_ptr_text("[[PAGE 1]]\n" + "\n".join(lines) + "\n")

    def test_separator_does_not_consume_following_transaction(self) -> None:
        rows, _ = self.parse(
            "$200",
            "Abbott Laboratories (ABT) [ST]",
            "P 01/02/2025 01/03/2025 $1,001 - $15,000",
        )

        self.assertEqual(1, len(rows))
        self.assertEqual("Abbott Laboratories (ABT)", rows[0].asset_name_raw)
        self.assertEqual([], rows[0].validation_errors)

    def test_fresh_running_job_is_protected(self) -> None:
        started = datetime.now(timezone.utc)
        cursor = _JobCursor({
            "document_job_id": 7,
            "status": "running",
            "attempt_count": 1,
            "max_attempts": 3,
            "started_at": started,
        }, stale=False)
        job_id, should_run = parser.ensure_parse_job(
            cursor,
            {"filing_id": 1, "document_id": 2},
            SimpleNamespace(
                max_attempts=3,
                reprocess=False,
                retry_failed=False,
                stale_job_timeout_seconds=3600,
            ),
        )
        self.assertEqual((7, False), (job_id, should_run))
        self.assertFalse(any("UPDATE document_jobs SET status='queued'" in sql for sql, _ in cursor.calls))

    def test_stale_running_job_is_recovered_without_resetting_attempts(self) -> None:
        cursor = _JobCursor({
            "document_job_id": 8,
            "status": "running",
            "attempt_count": 1,
            "max_attempts": 3,
            "started_at": datetime.now(timezone.utc) - timedelta(hours=2),
        }, stale=True)
        job_id, should_run = parser.ensure_parse_job(
            cursor,
            {"filing_id": 1, "document_id": 2},
            SimpleNamespace(
                max_attempts=3,
                reprocess=False,
                retry_failed=False,
                stale_job_timeout_seconds=3600,
            ),
        )
        self.assertEqual((8, True), (job_id, should_run))
        self.assertTrue(any("status='queued'" in sql for sql, _ in cursor.calls))
        self.assertEqual(1, cursor.existing["attempt_count"])

    def test_recovered_job_can_be_marked_running_again(self) -> None:
        cursor = _JobCursor(None, running_attempt=2)
        self.assertEqual(2, parser.mark_running(cursor, 8))
        self.assertTrue(any("status='running'" in sql for sql, _ in cursor.calls))

    def test_retry_failed_does_not_reset_exhausted_attempts(self) -> None:
        cursor = _JobCursor({
            "document_job_id": 9,
            "status": "failed_retryable",
            "attempt_count": 3,
            "max_attempts": 3,
            "started_at": None,
        })
        job_id, should_run = parser.ensure_parse_job(
            cursor,
            {"filing_id": 1, "document_id": 2},
            SimpleNamespace(
                max_attempts=3,
                reprocess=False,
                retry_failed=True,
                stale_job_timeout_seconds=3600,
            ),
        )
        self.assertEqual((9, False), (job_id, should_run))

    def test_reprocess_creates_a_new_auditable_job_cycle(self) -> None:
        cursor = _JobCursor({
            "document_job_id": 10,
            "status": "complete",
            "attempt_count": 1,
            "max_attempts": 3,
            "started_at": None,
        }, inserted_job_id=11)
        job_id, should_run = parser.ensure_parse_job(
            cursor,
            {"filing_id": 1, "document_id": 2},
            SimpleNamespace(
                max_attempts=3,
                reprocess=True,
                retry_failed=False,
                stale_job_timeout_seconds=3600,
            ),
        )
        self.assertEqual((11, True), (job_id, should_run))
        self.assertTrue(any("INSERT INTO document_jobs" in sql for sql, _ in cursor.calls))

    def test_spaced_form_label_does_not_become_the_asset(self) -> None:
        rows, _ = self.parse(
            "S O:",
            "Microsoft Corporation (MSFT) [ST]",
            "S 02/01/2025 02/03/2025 $15,001 - $50,000",
        )

        self.assertEqual(1, len(rows))
        self.assertEqual("Microsoft Corporation (MSFT)", rows[0].asset_name_raw)

    def test_one_letter_form_fragment_is_a_separator(self) -> None:
        rows, _ = self.parse(
            "F",
            "Apple Inc. (AAPL) [ST]",
            "P 03/01/2025 03/02/2025 $1,001 - $15,000",
        )

        self.assertEqual("Apple Inc. (AAPL)", rows[0].asset_name_raw)

    def test_split_amount_range_uses_the_following_line(self) -> None:
        rows, _ = self.parse(
            "NVIDIA Corporation (NVDA) [ST] P 04/01/2025 04/02/2025 $1,001 -",
            "$15,000",
        )

        self.assertEqual(1, len(rows))
        self.assertEqual("1001", str(rows[0].amount_min))
        self.assertEqual("15000", str(rows[0].amount_max))
        self.assertIsNone(rows[0].amount_exact)

    def test_spouse_or_dependent_child_over_amount_is_supported(self) -> None:
        rows, _ = self.parse(
            "Private investment [OT]",
            "P 05/01/2025 05/02/2025 Spouse/DC Over $1,000,000",
        )

        self.assertEqual(1, len(rows))
        self.assertEqual("1000000", str(rows[0].amount_min))
        self.assertIsNone(rows[0].amount_max)
        self.assertIsNone(rows[0].amount_exact)

    def test_amendment_transaction_id_and_owner_are_preserved(self) -> None:
        rows, _ = self.parse(
            "2000086356SP 3M Company (MMM) [ST]",
            "S 06/01/2025 06/02/2025 $1,001 - $15,000",
        )

        self.assertEqual(1, len(rows))
        self.assertEqual("2000086356", rows[0].source_transaction_id_raw)
        self.assertEqual("spouse", rows[0].owner_type)
        self.assertEqual("3M Company (MMM)", rows[0].asset_name_raw)

    def test_soft_hyphen_is_normalized_in_amount_range(self) -> None:
        rows, _ = self.parse(
            "Index fund [MF] P 07/01/2025 07/02/2025 $15,001 \u00ad $50,000",
        )

        self.assertEqual(1, len(rows))
        self.assertEqual("15001", str(rows[0].amount_min))
        self.assertEqual("50000", str(rows[0].amount_max))

    def test_packed_transactions_are_split_without_layout_fallback(self) -> None:
        text = (
            "[[PAGE 1]]\n"
            "Asset One [ST] P 01/01/2020 01/02/2020 $1,001 - $15,000 "
            "Asset Two [ST] S 02/01/2020 02/02/2020 $15,001 - $50,000\n"
        )
        extracted = parser.ExtractedDocument(
            text=text,
            page_count=1,
            has_embedded_text=True,
            warnings=[],
            extractor_name=parser.PYPDF_EXTRACTOR_NAME,
            extractor_version=parser.PYPDF_EXTRACTOR_VERSION,
        )
        rows, _ = parser.parse_ptr_text(text)

        self.assertEqual(2, parser.transaction_signature_count(text))
        self.assertEqual(2, len(rows))
        self.assertIsNone(parser.layout_fallback_reason(extracted, rows))

    def test_description_glued_to_next_transaction_keeps_asset(self) -> None:
        rows, _ = self.parse(
            "DESCRIPTION: Pooled Investment Fund/Venture Capital"
            "SPMonterey Peninsula WTR COPS [GS]"
            "S 02/14/2019 02/28/2019 $500,001 - $1,000,000",
        )

        self.assertEqual(1, len(rows))
        self.assertEqual("Monterey Peninsula WTR COPS", rows[0].asset_name_raw)
        self.assertEqual("spouse", rows[0].owner_type)
        self.assertEqual([], rows[0].validation_errors)

    def test_page_boundary_continuation_supports_partial_sale(self) -> None:
        rows, _ = self.parse(
            "SP Example Corporation CommonS (partial) "
            "06/17/2025 08/13/2025 $1,001 - $15,000",
            "[[PAGE 2]]",
            "ID Owner Asset Transaction",
            "Type",
            "Date Notification",
            "Date",
            "Amount Cap.",
            "Gains >",
            "$200?",
            "Stock (EXM) [ST]",
            "F S: New",
        )

        self.assertEqual(1, len(rows))
        self.assertEqual("sale", rows[0].transaction_type)
        self.assertTrue(rows[0].is_partial_sale)
        self.assertEqual("Example Corporation Common Stock (EXM)", rows[0].asset_name_raw)
        self.assertEqual("EXM", rows[0].ticker_reported)

    def test_page_boundary_does_not_merge_a_new_transaction_asset(self) -> None:
        rows, _ = self.parse(
            "SP Prior Corporation CommonP "
            "06/17/2025 08/13/2025 $1,001 - $15,000",
            "[[PAGE 2]]",
            "ID Owner Asset Transaction",
            "Type",
            "Date Notification",
            "Date",
            "Amount Cap.",
            "Gains >",
            "$200?",
            "Unrelated Corporation (NEW) [ST]",
            "P 06/18/2025 08/13/2025 $15,001 - $50,000",
        )

        self.assertEqual(2, len(rows))
        self.assertEqual("Prior Corporation Common", rows[0].asset_name_raw)
        self.assertIsNone(rows[0].ticker_reported)
        self.assertEqual("Unrelated Corporation (NEW)", rows[1].asset_name_raw)
        self.assertEqual("NEW", rows[1].ticker_reported)

    def test_qa_signature_detector_accepts_glued_types_only_with_full_rows(self) -> None:
        text = (
            "Common StockP 06/17/2025 08/13/2025 $1,001 - $15,000\n"
            "Common StockS 06/18/2025 08/13/2025 $15,001 - $50,000\n"
            "Common StockS (partial) 06/19/2025 08/13/2025 $50,001 - $100,000\n"
            "These incomplete strings StockP and StockS must not count.\n"
        )

        self.assertEqual(3, len(qa.TRANSACTION_SIGNATURE_RE.findall(text)))

    def test_house_orchestrator_requests_generic_ocr_and_reuses_parser(self) -> None:
        class StubExtractionService:
            def extract_embedded(self, path: Path) -> parser.ExtractedDocument:
                return parser.ExtractedDocument(
                    "[[PAGE 1]]\n", 1, False, ["no embedded text"], "pypdf", "1.2.0"
                )

            def extract_layout(self, path: Path) -> parser.ExtractedDocument:
                return self.extract_embedded(path)

        class StubOCRService:
            def __init__(self) -> None:
                self.calls = 0

            def extract(self, path: Path) -> OCRResult:
                self.calls += 1
                text = (
                    "[[PAGE 1]]\nExample Corporation (EXM) [ST] P "
                    "06/17/2025 08/13/2025 $1,001 - $15,000\n"
                )
                return OCRResult(
                    text=text,
                    page_count=1,
                    characters_extracted=len(text),
                    bytes_extracted=len(text.encode("utf-8")),
                    extractor_name="tesseract",
                    extractor_version="5.3.0",
                    pages_processed=1,
                )

        ocr = StubOCRService()
        result = parser.process_house_document(
            Path("unused.pdf"),
            requires_ocr=True,
            extraction_service=StubExtractionService(),
            ocr_service=ocr,
        )

        self.assertEqual(1, ocr.calls)
        self.assertTrue(result.ocr_attempted)
        self.assertTrue(result.ocr_selected)
        self.assertEqual("ocr", result.extracted.extraction_type)
        self.assertEqual(1, len(result.rows))
        self.assertEqual("EXM", result.rows[0].ticker_reported)
        self.assertEqual([], result.rows[0].validation_errors)

    def test_ocr_coverage_failure_keeps_document_in_review_without_rows(self) -> None:
        class StubExtractionService:
            def extract_embedded(self, path: Path) -> parser.ExtractedDocument:
                return parser.ExtractedDocument("[[PAGE 1]]\n", 1, False, ["no embedded text"], "pypdf", "1.2.0")

            def extract_layout(self, path: Path) -> parser.ExtractedDocument:
                return self.extract_embedded(path)

        class StubOCRService:
            def extract(self, path: Path) -> OCRResult:
                text = (
                    "[[PAGE 1]]\n"
                    "Asset One (ONE) [ST] P 01/01/2025 01/02/2025 $1,001 - $15,000\n"
                    "Asset Two (TWO) [ST] P 02/01/2025 02/02/2025 $1,001 - $15,000\n"
                )
                return OCRResult(text, 1, len(text), len(text.encode()), "tesseract", "5.3.0", pages_processed=1)

        parsed_row = SimpleNamespace(validation_errors=[])
        with mock.patch.object(
            parser,
            "parse_ptr_text",
            side_effect=[([], ["no rows"]), ([], ["no rows"]), ([parsed_row], [])],
        ):
            result = parser.process_house_document(
                Path("unused.pdf"),
                requires_ocr=True,
                extraction_service=StubExtractionService(),
                ocr_service=StubOCRService(),
            )

        self.assertTrue(result.ocr_attempted)
        self.assertFalse(result.ocr_selected)
        self.assertFalse(result.extraction_usable)
        self.assertEqual([], result.rows)
        self.assertIn("OCR text failed House validation", " ".join(result.warnings))

    def test_ocr_validation_failure_keeps_document_in_review_without_rows(self) -> None:
        class StubExtractionService:
            def extract_embedded(self, path: Path) -> parser.ExtractedDocument:
                return parser.ExtractedDocument("[[PAGE 1]]\n", 1, False, ["no embedded text"], "pypdf", "1.2.0")

            def extract_layout(self, path: Path) -> parser.ExtractedDocument:
                return self.extract_embedded(path)

        class StubOCRService:
            def extract(self, path: Path) -> OCRResult:
                text = "[[PAGE 1]]\nAsset One (ONE) [ST] P 01/01/2025 01/02/2025 $1,001 - $15,000\n"
                return OCRResult(text, 1, len(text), len(text.encode()), "tesseract", "5.3.0", pages_processed=1)

        invalid_row = SimpleNamespace(validation_errors=["missing asset name"])
        with mock.patch.object(
            parser,
            "parse_ptr_text",
            side_effect=[([], ["no rows"]), ([], ["no rows"]), ([invalid_row], [])],
        ):
            result = parser.process_house_document(
                Path("unused.pdf"),
                requires_ocr=True,
                extraction_service=StubExtractionService(),
                ocr_service=StubOCRService(),
            )

        self.assertFalse(result.ocr_selected)
        self.assertEqual([], result.rows)
        self.assertIn("OCR text failed House validation", " ".join(result.warnings))

    def test_ocr_unavailable_does_not_load_normal_rows_for_requires_ocr_document(self) -> None:
        class StubExtractionService:
            def extract_embedded(self, path: Path) -> parser.ExtractedDocument:
                text = "[[PAGE 1]]\nAsset One (ONE) [ST] P 01/01/2025 01/02/2025 $1,001 - $15,000\n"
                return parser.ExtractedDocument(text, 1, True, [], "pypdf", "1.2.0")

            def extract_layout(self, path: Path) -> parser.ExtractedDocument:
                return self.extract_embedded(path)

        class UnavailableOCRService:
            def extract(self, path: Path) -> OCRResult:
                raise parser.OCRServiceUnavailable("Tesseract executable was not found")

        result = parser.process_house_document(
            Path("unused.pdf"),
            requires_ocr=True,
            extraction_service=StubExtractionService(),
            ocr_service=UnavailableOCRService(),
        )

        self.assertTrue(result.ocr_attempted)
        self.assertFalse(result.extraction_usable)
        self.assertEqual([], result.rows)
        self.assertIn("OCR unavailable", " ".join(result.warnings))

    @unittest.skipUnless(DOC_20009299_TEXT.is_file(), "House PTR 20009299 fixture is unavailable")
    def test_document_20009299_packed_transactions_are_all_parsed(self) -> None:
        text = DOC_20009299_TEXT.read_text(encoding="utf-8")
        rows, warnings = parser.parse_ptr_text(text)

        self.assertEqual(3, len(rows))
        self.assertEqual(3, parser.transaction_signature_count(text))
        self.assertNotIn("coverage mismatch", " ".join(warnings))
        self.assertEqual(["sale", "purchase", "sale"], [row.transaction_type for row in rows])
        self.assertEqual(
            ["2018-03-08", "2018-03-08", "2018-03-28"],
            [row.transaction_date.isoformat() for row in rows],
        )
        self.assertEqual(["u.S. Treasury Bills"] * 3, [row.asset_name_raw for row in rows])

    @unittest.skipUnless(DOC_20030891_TEXT.is_file(), "House PTR 20030891 fixture is unavailable")
    def test_document_20030891_page_boundary_regressions(self) -> None:
        text = DOC_20030891_TEXT.read_text(encoding="utf-8")
        rows, _ = parser.parse_ptr_text(text)
        expected = {
            78: ("Autodesk, Inc. - Common Stock (ADSK)", "ADSK", "purchase", "2025-06-17"),
            87: ("Avient Corporation Common Stock (AVNT)", "AVNT", "sale", "2025-06-17"),
            96: ("Berkshire Hathaway Inc. New Common Stock (BRK.B)", "BRK.B", "purchase", "2025-06-09"),
            105: ("Black Hills Corporation Common Stock (BKH)", "BKH", "purchase", "2025-06-11"),
            241: ("Equitable Holdings, Inc. Common Stock (EQH)", "EQH", "purchase", "2025-06-17"),
            504: ("Patrick Industries, Inc. - Common Stock (PATK)", "PATK", "sale", "2025-06-17"),
        }

        self.assertEqual(722, len(rows))
        self.assertEqual(722, len({row.source_row_number for row in rows}))
        self.assertEqual(722, parser.transaction_signature_count(text))
        self.assertEqual(722, len(qa.TRANSACTION_SIGNATURE_RE.findall(text)))
        for source_row_number, values in expected.items():
            asset_name, ticker, transaction_type, transaction_date = values
            row = rows[source_row_number - 1]
            with self.subTest(source_row_number=source_row_number):
                self.assertEqual(source_row_number, row.source_row_number)
                self.assertEqual(asset_name, row.asset_name_raw)
                self.assertEqual(ticker, row.ticker_reported)
                self.assertEqual(transaction_type, row.transaction_type)
                self.assertEqual(transaction_date, row.transaction_date.isoformat())
                self.assertEqual("2025-08-13", row.notification_date.isoformat())
                self.assertEqual("$1,001 - $15,000", row.amount_range_raw)


class _JobCursor:
    def __init__(self, existing: dict | None, stale: bool | None = None, inserted_job_id: int = 99, running_attempt: int | None = None) -> None:
        self.existing = existing
        self.stale = stale
        self.inserted_job_id = inserted_job_id
        self.running_attempt = running_attempt
        self.calls: list[tuple[str, tuple]] = []
        self._result = None

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.calls.append((sql, params))
        if "SELECT document_job_id,status,attempt_count,max_attempts,started_at" in sql:
            self._result = self.existing
        elif "SELECT (%s <" in sql:
            self._result = {"stale": self.stale}
        elif "RETURNING document_job_id" in sql:
            self._result = {"document_job_id": self.inserted_job_id}
        elif "status='running'" in sql:
            self._result = {"attempt_count": self.running_attempt or 1}

    def fetchone(self):
        result = self._result
        self._result = None
        return result


if __name__ == "__main__":
    unittest.main()
