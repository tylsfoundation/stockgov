# Document Parser Architecture Review and Fix Plan

The current script is a good first House PTR implementation, but it has reached the point where it should be split before adding Senate support. It is 766 lines and combines six different responsibilities:

- command-line arguments and filters: `parse_house_documents.py:172`
- database selection: `parse_house_documents.py:213`
- PDF text extraction: `parse_house_documents.py:270`
- House transaction recognition: `parse_house_documents.py:418`
- job retries and status transitions: `parse_house_documents.py:487`
- database writes and orchestration: `parse_house_documents.py:544` and `parse_house_documents.py:685`

The main problem is that only some of those responsibilities are actually House-specific.

## Naming recommendation

Eventually rename the user-facing command to `parse_documents.py`, but do not simply rename the existing file. Make `parse_documents.py` a thin, source-aware runner and temporarily retain `parse_house_documents.py` as a compatibility wrapper. That prevents existing commands and documentation from breaking.

## Recommended structure

```text
ingestion/
  documents/
    models.py
    config.py
    selectors.py
    runner.py
    repository.py
    extractors/
      pdf_text.py
      ocr.py
      html.py
    parsers/
      house_ptr.py
      house_annual.py
      senate_ptr_html.py
      senate_ptr_pdf.py

scripts/
  parse_documents.py
  parse_house_documents.py       # compatibility wrapper
  validate_document_parsing.py
```

The runner should follow this pipeline:

```text
select filing
  -> choose extractor
  -> save extraction evidence
  -> choose format-specific parser
  -> validate normalized rows
  -> persist trades
  -> update job status
```

## Functions and responsibilities to share

| Current function or responsibility | Reuse |
|---|---|
| `.env` and database configuration | Move to shared configuration |
| document selection and filtering | Generalize by source, chamber, filing type, and format |
| SHA-256 and atomic artifact handling | Reuse directly |
| job creation, retries, failure handling | Reuse directly |
| extraction metadata and status updates | Reuse through a repository |
| normalized transaction model | Reuse, with optional fields |
| PostgreSQL transaction/upsert logic | Reuse through a persistence layer |
| progress output and totals | Reuse in the runner |
| PDF extraction with `pypdf` | Reuse for House PDFs and Senate PDF filings |

## Logic that should remain format-specific

Keep these functions in a House PTR parser module rather than sharing them prematurely:

- `TRANSACTION_RE`
- `parse_amounts`
- `asset_block`
- `is_separator`
- `transaction_candidate`
- `parse_ptr_text`
- House-specific owner codes and form-layout assumptions

The Senate research notes show why a single parser will not work: Senate reports use UUIDs, many reports are structured HTML, and some are PDFs or scanned attachments. See `data/raw/senateforms/senateform.md` sections 2 and 5.5.

For Senate electronic PTRs, the parser should read HTML tables directly. It should not convert those reports to PDF or run OCR. Senate paper PDFs can reuse the PDF and OCR extractors, but they need a separate Senate text parser.

## Database alignment

The database schema already supports this direction:

- `document_jobs` has separate job types for extraction, OCR, parsing, and validation.
- `document_extractions` supports `embedded_text`, `ocr`, `table`, and `structured_parser`.
- Separate House and Senate staging trade tables already exist.

One schema issue should be addressed before adding another parser: the trade uniqueness constraint is currently based on:

```text
filing_id, source_row_number, parser_version
```

It does not include `parser_name`. Parser versions should either be globally namespaced, such as `house_ptr:1.1.0` and `senate_ptr_html:1.0.0`, or the constraint should include `parser_name`. Otherwise, two parser families can collide if they use the same version number.

## Extractor and parser interfaces

Introduce explicit interfaces rather than sharing functions through imports:

```python
class Extractor:
    def extract(self, document) -> ExtractionResult:
        ...

class FilingParser:
    def parse(self, content, filing_metadata) -> list[ParsedTransaction]:
        ...
```

The runner can then use a registry:

```text
(house, ptr, pdf)   -> HousePtrParser
(senate, ptr, html) -> SenatePtrHtmlParser
(senate, ptr, pdf)  -> SenatePtrPdfParser
```

## Migration sequence

1. Add parser tests for representative House documents, especially the known problematic files.
2. Move data classes and pure parsing helpers into modules without changing behavior.
3. Move database operations into a repository module.
4. Move job selection and retry handling into the shared runner.
5. Add the Senate HTML parser.
6. Add OCR as another extractor.
7. Introduce `scripts/parse_documents.py`.
8. Keep `parse_house_documents.py` as a wrapper until users and documentation have moved over.

## Recommendation

Rename the user-facing command eventually, split the implementation now, and share the ingestion infrastructure while keeping House and Senate recognition logic as separate adapters. This reuses the workflow plumbing without forcing incompatible House PDF and Senate HTML formats into one fragile parser.
