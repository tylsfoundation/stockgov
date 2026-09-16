"""Small tests for generic document discovery and orchestration mechanics."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ingestion.common.config import ProcessingConfig
from ingestion.common.discovery import discover_documents
from ingestion.common.validation import duplicate_keys, validate_required
from ingestion.common.artifacts import artifact_path, content_hash, write_immutable_text
from ingestion.orchestrator import DocumentOrchestrator, ProcessingOutcome
from scripts.reset_house_ptr_data import main as reset_main


class _Processor:
    def __init__(self) -> None:
        self.paths: list[Path] = []

    def process(self, path: Path, **_: object):
        self.paths.append(path)
        return type(
            "Result",
            (),
            {"outcome": ProcessingOutcome(success=True, destination_category="processed")},
        )()


class DocumentPipelineTests(unittest.TestCase):
    def test_empty_directory_discovers_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual([], discover_documents(Path(temp)))

    def test_discovery_deduplicates_identical_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "a.pdf").write_bytes(b"same")
            (root / "b.pdf").write_bytes(b"same")
            found = discover_documents(root)
            self.assertEqual(1, len(found))

    def test_orchestrator_continues_and_summarizes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "a.pdf").write_bytes(b"a")
            processor = _Processor()
            config = ProcessingConfig(database_url=None, document_root=root, incoming_directory=None, processed_directory=None, review_directory=None)
            summary = DocumentOrchestrator(config, {"test": processor}).run("test")
            self.assertEqual((1, 1, 1), (summary.discovered, summary.processed, summary.parsed))

    def test_shared_required_and_duplicate_checks(self) -> None:
        records = [{"name": "a"}, {"name": "a"}]
        self.assertTrue(validate_required(records[0], ["name"]).valid)
        self.assertEqual([("a",)], duplicate_keys(records, ["name"]))

    def test_orchestrator_counts_generic_skips(self) -> None:
        class SkippingProcessor:
            def process(self, path: Path, **_: object):
                return type("Result", (), {"outcome": ProcessingOutcome(success=False, skipped=True)})()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "a.pdf").write_bytes(b"a")
            config = ProcessingConfig(database_url=None, document_root=root, incoming_directory=None, processed_directory=None, review_directory=None)
            summary = DocumentOrchestrator(config, {"test": SkippingProcessor()}).run("test")
            self.assertEqual(1, summary.discovered)
            self.assertEqual(1, summary.skipped)
            self.assertEqual(0, summary.processed)

    def test_content_addressed_artifacts_are_immutable_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "20000001.pdf"
            source.write_bytes(b"pdf")
            first = "first extraction"
            first_hash = content_hash(first.encode())
            first_path = artifact_path(source, "pypdf", "1.2.0", first_hash)
            write_immutable_text(first_path, first)
            write_immutable_text(first_path, first)
            second = "different extraction"
            second_hash = content_hash(second.encode())
            second_path = artifact_path(source, "pypdf", "1.2.0", second_hash)
            write_immutable_text(second_path, second)
            self.assertNotEqual(first_path, second_path)
            self.assertEqual(first, first_path.read_text())
            self.assertEqual(second, second_path.read_text())
            self.assertEqual(first_hash, content_hash(first_path.read_bytes()))
            self.assertEqual(second_hash, content_hash(second_path.read_bytes()))

    def test_reset_requires_explicit_confirmation(self) -> None:
        self.assertEqual(2, reset_main([]))


if __name__ == "__main__":
    unittest.main()
