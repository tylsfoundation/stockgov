"""Unit tests for the generic local OCR service."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from ocr_service import OCRServiceUnavailable, TesseractOCRService  # noqa: E402


class OCRServiceTests(unittest.TestCase):
    def _fake_modules(self, pages: list[str], *, fail_page: int | None = None):
        images = [object() for _ in pages]

        def convert_from_path(_path: str, **_: object):
            return images

        def image_to_string(image: object, *, lang: str):
            index = images.index(image) + 1
            if fail_page == index:
                raise RuntimeError("synthetic page failure")
            return pages[index - 1]

        pytesseract = SimpleNamespace(
            pytesseract=SimpleNamespace(tesseract_cmd=None),
            get_tesseract_version=lambda: "5.3.0\n",
            image_to_string=image_to_string,
        )
        pdf2image = SimpleNamespace(convert_from_path=convert_from_path)
        return {"pytesseract": pytesseract, "pdf2image": pdf2image}

    def _run_fake(self, pages: list[str], *, fail_page: int | None = None):
        modules = self._fake_modules(pages, fail_page=fail_page)
        which = lambda name: "tesseract.exe" if name == "tesseract" else "pdftoppm.exe"
        with patch.dict(sys.modules, modules), patch("ocr_service.shutil.which", side_effect=which):
            return TesseractOCRService().extract(Path("fixture.pdf"))

    def test_missing_tesseract_is_explicit(self) -> None:
        modules = self._fake_modules(["unused"])
        with patch.dict(sys.modules, modules), patch(
            "ocr_service.shutil.which",
            side_effect=lambda name: "pdftoppm.exe" if name == "pdftoppm" else None,
        ):
            with self.assertRaisesRegex(OCRServiceUnavailable, "Tesseract executable was not found"):
                TesseractOCRService().extract(Path("fixture.pdf"))

    def test_configured_tesseract_path_is_used_when_path_is_missing(self) -> None:
        executable = Path("C:/configured/tesseract.exe")
        modules = self._fake_modules(["hello"])
        which = lambda name: "pdftoppm.exe" if name == "pdftoppm" else None
        with patch.dict(sys.modules, modules), patch("ocr_service.shutil.which", side_effect=which), patch.object(
            Path, "is_file", return_value=True
        ):
            result = TesseractOCRService(tesseract_cmd=str(executable)).extract(Path("fixture.pdf"))
        self.assertEqual(str(executable), modules["pytesseract"].pytesseract.tesseract_cmd)
        self.assertEqual("ocr", result.extraction_type)

    def test_one_page_ocr_preserves_page_marker(self) -> None:
        result = self._run_fake(["recognized text"])
        self.assertEqual(1, result.page_count)
        self.assertEqual(1, result.pages_processed)
        self.assertIn("[[PAGE 1]]\nrecognized text", result.text)

    def test_multi_page_ocr_preserves_order_and_markers(self) -> None:
        result = self._run_fake(["first page", "second page"])
        self.assertEqual(2, result.page_count)
        self.assertEqual(2, result.pages_processed)
        self.assertLess(result.text.index("[[PAGE 1]]"), result.text.index("[[PAGE 2]]"))
        self.assertIn("[[PAGE 2]]\nsecond page", result.text)

    def test_page_failure_discards_partial_ocr(self) -> None:
        with self.assertRaisesRegex(OCRServiceUnavailable, "partial OCR output was discarded"):
            self._run_fake(["first page", "broken page"], fail_page=2)

    def test_identical_ocr_is_deterministic(self) -> None:
        first = self._run_fake(["same", "output"])
        second = self._run_fake(["same", "output"])
        self.assertEqual(first.text, second.text)
        self.assertEqual(first.bytes_extracted, second.bytes_extracted)
        self.assertEqual(first.characters_extracted, second.characters_extracted)

    def test_pil_pages_use_closed_png_paths_and_cleanup(self) -> None:
        class FakeImage:
            def __init__(self) -> None:
                self.closed = False

            def save(self, path: Path, *, format: str) -> None:
                self.assert_format = format
                Path(path).write_bytes(b"png")

            def close(self) -> None:
                self.closed = True

        image = FakeImage()
        observed: list[str] = []

        def convert_from_path(_path: str, **_: object):
            return [image]

        def image_to_string(page: str, *, lang: str):
            observed.append(page)
            self.assertTrue(Path(page).exists())
            return "recognized"

        modules = {
            "pytesseract": SimpleNamespace(
                pytesseract=SimpleNamespace(tesseract_cmd=None),
                get_tesseract_version=lambda: "5.3.0\n",
                image_to_string=image_to_string,
            ),
            "pdf2image": SimpleNamespace(convert_from_path=convert_from_path),
        }
        which = lambda name: "tesseract.exe" if name == "tesseract" else "pdftoppm.exe"
        with patch.dict(sys.modules, modules), patch("ocr_service.shutil.which", side_effect=which):
            result = TesseractOCRService().extract(Path("fixture.pdf"))

        self.assertEqual("PNG", image.assert_format)
        self.assertTrue(image.closed)
        self.assertEqual(1, len(observed))
        self.assertFalse(Path(observed[0]).exists())
        self.assertIn("recognized", result.text)

    def test_temp_cleanup_retries_transient_windows_lock(self) -> None:
        service = TesseractOCRService()
        transient_lock = PermissionError(32, "file in use")
        with patch.object(Path, "unlink", side_effect=[transient_lock, None]) as unlink, patch(
            "ocr_service.time.sleep"
        ) as sleep:
            service._delete_temp_file(Path("fixture.png"))
        self.assertEqual(2, unlink.call_count)
        sleep.assert_called_once()


if __name__ == "__main__":
    unittest.main()
