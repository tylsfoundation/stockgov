"""Generic OCR service for document parsers.

The service knows nothing about House transactions or any other domain.  It
converts each PDF page to an image, delegates recognition to Tesseract through
``pytesseract``, and returns page-delimited text plus OCR metadata.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class OCRServiceUnavailable(RuntimeError):
    """Raised when the configured OCR engine or rasterizer is unavailable."""


@dataclass
class OCRResult:
    text: str
    page_count: int
    characters_extracted: int
    bytes_extracted: int
    extractor_name: str
    extractor_version: str
    warnings: list[str] = field(default_factory=list)
    pages_processed: int = 0
    extraction_type: str = "ocr"


class TesseractOCRService:
    """Run generic page OCR using Tesseract and pdf2image."""

    extractor_name = "tesseract"

    def __init__(
        self,
        *,
        dpi: int = 200,
        language: str = "eng",
        tesseract_cmd: str | None = None,
        poppler_path: str | None = None,
    ):
        self.dpi = dpi
        self.language = language
        self.tesseract_cmd = tesseract_cmd or os.getenv("TESSERACT_CMD")
        self.poppler_path = poppler_path or os.getenv("POPPLER_PATH")

    def _resolve_tesseract(self) -> str:
        """Resolve PATH first, then an explicitly configured executable."""

        discovered = shutil.which("tesseract")
        if discovered:
            return discovered
        configured = self.tesseract_cmd
        if configured:
            discovered = shutil.which(configured)
            if discovered:
                return discovered
            configured_path = Path(configured)
            if configured_path.is_file():
                return str(configured_path)
        raise OCRServiceUnavailable(
            "Tesseract executable was not found on PATH or at TESSERACT_CMD; "
            "install Tesseract OCR or set TESSERACT_CMD to its executable"
        )

    def _resolve_poppler(self) -> str | None:
        """Return a configured Poppler directory or verify PATH discovery."""

        if self.poppler_path:
            poppler_dir = Path(self.poppler_path)
            if any(
                (poppler_dir / executable).is_file()
                for executable in ("pdftoppm.exe", "pdftoppm", "pdftocairo.exe", "pdftocairo")
            ):
                return str(poppler_dir)
            raise OCRServiceUnavailable(
                f"Poppler was not found at POPPLER_PATH={self.poppler_path}; "
                "set POPPLER_PATH to the directory containing pdftoppm"
            )
        if shutil.which("pdftoppm") or shutil.which("pdftocairo"):
            return None
        raise OCRServiceUnavailable(
            "Poppler rendering tools were not found on PATH; install Poppler "
            "or set POPPLER_PATH to its bin directory"
        )

    def extract(self, path: Path) -> OCRResult:
        try:
            import pytesseract
            from pdf2image import convert_from_path
        except ImportError as exc:  # pragma: no cover - dependency installation path
            raise OCRServiceUnavailable(
                "OCR requires pytesseract and pdf2image; install the OCR dependencies"
            ) from exc

        tesseract_path = self._resolve_tesseract()
        poppler_path = self._resolve_poppler()
        pytesseract.pytesseract.tesseract_cmd = tesseract_path

        try:
            version = str(pytesseract.get_tesseract_version()).splitlines()[0].strip()
            images = convert_from_path(str(path), dpi=self.dpi, poppler_path=poppler_path)
        except Exception as exc:
            raise OCRServiceUnavailable(
                f"OCR setup failed using Tesseract at {tesseract_path}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        if not images:
            raise OCRServiceUnavailable("PDF rendering produced no pages; OCR output was discarded")

        page_lines: list[str] = []
        warnings: list[str] = []
        for page_number, image in enumerate(images, 1):
            page_lines.append(f"[[PAGE {page_number}]]")
            try:
                page_lines.append(pytesseract.image_to_string(image, lang=self.language).strip())
            except Exception as exc:
                warnings.append(f"page {page_number} OCR failed: {type(exc).__name__}: {exc}")
        if warnings:
            raise OCRServiceUnavailable(
                "OCR failed for one or more pages; partial OCR output was discarded: "
                + "; ".join(warnings)
            )
        text = "\n".join(page_lines).strip() + "\n"
        return OCRResult(
            text=text,
            page_count=len(images),
            characters_extracted=len(text),
            bytes_extracted=len(text.encode("utf-8")),
            extractor_name=self.extractor_name,
            extractor_version=version,
            warnings=warnings,
            pages_processed=len(images),
        )


class TesseractJsOCRService:
    """Optional OCR backend using the open-source Tesseract.js wrapper.

    This adapter is useful on hosts where the Tesseract executable is not
    installed.  The OCR contract remains identical and contains no document
    or transaction-specific logic.
    """

    extractor_name = "tesseract.js"

    def __init__(
        self,
        *,
        node_command: str | None = None,
        module_root: str | None = None,
        dpi: int = 200,
        language: str = "eng",
    ):
        self.node_command = node_command or os.getenv("TESSERACT_JS_NODE", "node")
        self.module_root = module_root or os.getenv("TESSERACT_JS_MODULE_ROOT")
        self.dpi = dpi
        self.language = language

    def extract(self, path: Path) -> OCRResult:
        if not self.module_root:
            raise OCRServiceUnavailable(
                "TESSERACT_JS_MODULE_ROOT is required for the Tesseract.js OCR backend"
            )
        try:
            from pdf2image import convert_from_path
        except ImportError as exc:  # pragma: no cover - dependency installation path
            raise OCRServiceUnavailable("OCR requires pdf2image") from exc

        script = r"""
const path = require('path');
const args = JSON.parse(process.argv[1]);
const { createWorker } = require(path.join(args.moduleRoot, 'node_modules', 'tesseract.js'));
(async () => {
  const worker = await createWorker(args.language, 1, { cachePath: args.cachePath });
  const pages = [];
  for (const image of args.images) {
    const result = await worker.recognize(image);
    pages.push(result.data.text || '');
  }
  await worker.terminate();
  process.stdout.write(JSON.stringify({ pages }));
})().catch((error) => {
  process.stderr.write(`${error.name || 'Error'}: ${error.message || error}`);
  process.exit(1);
});
"""
        try:
            with tempfile.TemporaryDirectory(prefix="stockgov_ocr_") as temp_dir:
                images = convert_from_path(str(path), dpi=self.dpi)
                image_paths: list[str] = []
                for index, image in enumerate(images, 1):
                    image_path = Path(temp_dir) / f"page-{index}.png"
                    image.save(image_path, format="PNG")
                    image_paths.append(str(image_path))
                payload = json.dumps(
                    {
                        "moduleRoot": self.module_root,
                        "language": self.language,
                        "cachePath": temp_dir,
                        "images": image_paths,
                    }
                )
                completed = subprocess.run(
                    [self.node_command, "-e", script, payload],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
                if completed.returncode:
                    raise OCRServiceUnavailable(
                        f"Tesseract.js failed: {completed.stderr.strip()[:1000]}"
                    )
                response = json.loads(completed.stdout)
        except OCRServiceUnavailable:
            raise
        except Exception as exc:
            raise OCRServiceUnavailable(
                f"Tesseract.js setup failed: {type(exc).__name__}: {exc}"
            ) from exc

        pages = response.get("pages") or []
        if len(pages) != len(images):
            raise OCRServiceUnavailable(
                f"Tesseract.js returned {len(pages)} page(s) for {len(images)} rendered page(s); "
                "partial OCR output was discarded"
            )
        page_lines: list[str] = []
        for page_number, page_text in enumerate(pages, 1):
            page_lines.extend([f"[[PAGE {page_number}]]", str(page_text).strip()])
        text = "\n".join(page_lines).strip() + "\n"
        return OCRResult(
            text=text,
            page_count=len(pages),
            characters_extracted=len(text),
            bytes_extracted=len(text.encode("utf-8")),
            extractor_name=self.extractor_name,
            extractor_version="5.x",
            pages_processed=len(pages),
        )


def default_ocr_service() -> TesseractOCRService | TesseractJsOCRService:
    """Return the configured generic OCR service."""

    if os.getenv("OCR_BACKEND", "tesseract").lower() in {"tesseract.js", "tesseract_js"}:
        return TesseractJsOCRService()
    return TesseractOCRService()
