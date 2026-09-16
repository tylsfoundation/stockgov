"""Shared document and PDF text extraction services.

This module is document-type neutral.  It preserves page boundaries and
returns extraction metadata; callers decide whether the text is usable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    from pypdf import PdfReader
except ImportError as exc:  # pragma: no cover - command-line dependency
    raise RuntimeError("pypdf is required for PDF extraction") from exc

try:
    import pdfplumber
except ImportError as exc:  # pragma: no cover - command-line dependency
    raise RuntimeError("pdfplumber is required for PDF extraction") from exc


PYPDF_EXTRACTOR_NAME = "pypdf"
PYPDF_EXTRACTOR_VERSION = "1.2.0"
PDFPLUMBER_EXTRACTOR_NAME = "pdfplumber_layout"
PDFPLUMBER_EXTRACTOR_VERSION = "1.0.0"
PAGE_MARKER_RE = re.compile(r"^\[\[PAGE\s+(\d+)\]\]$")


@dataclass
class ExtractedDocument:
    text: str
    page_count: int
    has_embedded_text: bool
    warnings: list[str] = field(default_factory=list)
    extractor_name: str = PYPDF_EXTRACTOR_NAME
    extractor_version: str = PYPDF_EXTRACTOR_VERSION
    extraction_type: str = "embedded_text"


def _clean_line(value: str) -> str:
    value = value.translate(
        str.maketrans(
            {
                "\x00": "",
                "\ufffd": " ",
                "\u00a0": " ",
                "\u00ad": "-",
                "\u2010": "-",
                "\u2011": "-",
                "\u2012": "-",
                "\u2013": "-",
                "\u2014": "-",
                "\u2015": "-",
                "\u2212": "-",
            }
        )
    )
    value = "".join(
        char
        if (ord(char) >= 32 or char in "\t\r\n")
        and not 0xD800 <= ord(char) <= 0xDFFF
        else " "
        for char in value
    )
    return re.sub(r"\s+", " ", value).strip()


def _document_from_pages(
    page_lines: list[str], page_count: int, warnings: list[str],
    extractor_name: str, extractor_version: str,
) -> ExtractedDocument:
    text = "\n".join(line for line in page_lines if line).strip() + "\n"
    has_text = any(
        line and not PAGE_MARKER_RE.fullmatch(line)
        for line in text.splitlines()
    )
    if not has_text:
        warnings.append("no embedded text extracted; OCR is required")
    return ExtractedDocument(
        text=text,
        page_count=page_count,
        has_embedded_text=has_text,
        warnings=warnings,
        extractor_name=extractor_name,
        extractor_version=extractor_version,
    )


class DocumentExtractionService:
    """Extract text from PDFs with page-delimited output."""

    def extract(self, path: Path, *, layout: bool = False) -> ExtractedDocument:
        if layout:
            return self.extract_layout(path)
        return self.extract_embedded(path)

    def extract_embedded(self, path: Path) -> ExtractedDocument:
        reader = PdfReader(str(path), strict=False)
        page_lines: list[str] = []
        warnings: list[str] = []
        for page_number, page in enumerate(reader.pages, 1):
            page_lines.append(f"[[PAGE {page_number}]]")
            try:
                raw = page.extract_text() or ""
            except Exception as exc:  # pypdf can fail on one malformed page
                warnings.append(
                    f"page {page_number} extraction failed: {type(exc).__name__}: {exc}"
                )
                raw = ""
            page_lines.extend(_clean_line(line) for line in raw.splitlines())
        return _document_from_pages(
            page_lines, len(reader.pages), warnings,
            PYPDF_EXTRACTOR_NAME, PYPDF_EXTRACTOR_VERSION,
        )

    def extract_layout(self, path: Path) -> ExtractedDocument:
        page_lines: list[str] = []
        warnings: list[str] = []
        with pdfplumber.open(path) as pdf:
            page_count = len(pdf.pages)
            for page_number, page in enumerate(pdf.pages, 1):
                page_lines.append(f"[[PAGE {page_number}]]")
                try:
                    raw = page.extract_text(layout=True, x_density=7.25, y_density=13) or ""
                except Exception as exc:
                    warnings.append(
                        f"page {page_number} layout extraction failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    raw = ""
                page_lines.extend(_clean_line(line) for line in raw.splitlines())
        return _document_from_pages(
            page_lines, page_count, warnings,
            PDFPLUMBER_EXTRACTOR_NAME, PDFPLUMBER_EXTRACTOR_VERSION,
        )
