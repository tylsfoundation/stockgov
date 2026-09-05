"""Export all StockGov information for the example member, Mike Crapo.

The output is structured Markdown saved with a .log extension so it can be
pasted into an LLM and turned into a polished Word document. Database access is
read only.

Requirements:
    py -m pip install psycopg2-binary

Example:
    py scripts/export_example_member.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError as exc:
    raise SystemExit(
        "psycopg2-binary is required: py -m pip install psycopg2-binary"
    ) from exc


EXAMPLE_MEMBER_NAME = "Mike Crapo"
EXAMPLE_MEMBER_BIOGUIDE = "C000880"


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_dotenv(root: Path) -> None:
    path = root / ".env"
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def database_url(override: str | None) -> str:
    if override:
        return override
    load_dotenv(project_root())
    value = os.getenv("DATABASE_URL")
    if not value:
        raise ValueError("DATABASE_URL is not set in C:\\Home\\StockGov\\.env")
    return value


def display(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).replace("\r", " ").replace("\n", " ").strip() or "—"


def cell(value: Any) -> str:
    return display(value).replace("|", "\\|")


def table(rows: list[dict[str, Any]], empty_message: str = "No records stored.") -> str:
    if not rows:
        return f"_{empty_message}_\n"
    columns = list(rows[0].keys())
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(cell(row.get(column)) for column in columns) + " |")
    return "\n".join(lines) + "\n"


def bullets(row: dict[str, Any]) -> str:
    return "\n".join(f"- **{key}:** {display(value)}" for key, value in row.items()) + "\n"


class MemberExporter:
    def __init__(self, connection: Any) -> None:
        self.connection = connection
        self.step_number = 0

    def step(self, title: str) -> None:
        self.step_number += 1
        print(f"[{self.step_number:02d}] {title}", flush=True)

    def query(self, sql_text: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(sql_text, parameters)
            return [dict(row) for row in cursor.fetchall()]

    def find_member(self) -> dict[str, Any]:
        self.step(f"Resolve {EXAMPLE_MEMBER_NAME} by Bioguide ID")
        rows = self.query(
            """
            SELECT m.member_id, m.preferred_name, m.first_name, m.middle_name,
                   m.last_name, m.suffix, m.nickname, m.date_of_birth,
                   m.gender, m.is_living, m.created_at, m.updated_at,
                   mi.identifier_value AS bioguide_id
            FROM members m
            JOIN member_identifiers mi USING (member_id)
            WHERE mi.identifier_type = 'bioguide'
              AND mi.identifier_value = %s
            """,
            (EXAMPLE_MEMBER_BIOGUIDE,),
        )
        if len(rows) != 1:
            raise RuntimeError(
                f"Expected one member for Bioguide {EXAMPLE_MEMBER_BIOGUIDE}; "
                f"found {len(rows)}"
            )
        return rows[0]

    def collect(self, member_id: int) -> dict[str, list[dict[str, Any]]]:
        sections: dict[str, list[dict[str, Any]]] = {}

        self.step("Read names and external identifiers")
        sections["names"] = self.query(
            """
            SELECT name_type, full_name, first_name, middle_name, last_name,
                   suffix, valid_from, valid_to
            FROM member_names WHERE member_id = %s
            ORDER BY name_type, valid_from NULLS FIRST, full_name
            """, (member_id,)
        )
        sections["identifiers"] = self.query(
            """
            SELECT identifier_type, identifier_value, is_primary,
                   valid_from, valid_to
            FROM member_identifiers WHERE member_id = %s
            ORDER BY identifier_type, identifier_value
            """, (member_id,)
        )

        self.step("Read congressional terms and party-affiliation periods")
        sections["terms"] = self.query(
            """
            SELECT member_term_id, chamber, term_start_date, term_end_date,
                   congress_start, congress_end, state_code, district_number,
                   senate_class, senate_state_rank, party_code, party_name_raw,
                   caucus_party_code, caucus_party_name_raw, term_type,
                   term_end_type, official_website_url, contact_form_url, rss_url
            FROM member_terms WHERE member_id = %s
            ORDER BY term_start_date
            """, (member_id,)
        )
        sections["party_affiliations"] = self.query(
            """
            SELECT a.member_term_id, t.chamber, a.party_code, a.party_name_raw,
                   a.caucus_party_code, a.caucus_party_name_raw,
                   a.start_date, a.end_date
            FROM member_term_party_affiliations a
            JOIN member_terms t USING (member_term_id)
            WHERE t.member_id = %s ORDER BY a.start_date
            """, (member_id,)
        )

        self.step("Read family and leadership information")
        sections["family"] = self.query(
            """
            SELECT f.relative_name, f.relationship_type,
                   related.preferred_name AS linked_member_name,
                   f.valid_from, f.valid_to
            FROM member_family_relationships f
            LEFT JOIN members related ON related.member_id = f.related_member_id
            WHERE f.member_id = %s
            ORDER BY f.relationship_type, f.relative_name
            """, (member_id,)
        )
        sections["leadership"] = self.query(
            """
            SELECT chamber, role_title, party_code, start_date, end_date,
                   congress_start, congress_end
            FROM leadership_roles WHERE member_id = %s
            ORDER BY start_date, role_title
            """, (member_id,)
        )

        self.step("Read offices and official social accounts")
        sections["offices"] = self.query(
            """
            SELECT office_type, source_office_id, building, room, suite,
                   address_line_1, address_line_2, city, state_code, postal_code,
                   phone, fax, hours_text, latitude, longitude,
                   valid_from, valid_to
            FROM member_offices WHERE member_id = %s
            ORDER BY office_type, valid_from NULLS LAST, city NULLS FIRST
            """, (member_id,)
        )
        sections["social"] = self.query(
            """
            SELECT platform, account_name, platform_account_id, account_url,
                   is_official, valid_from, valid_to
            FROM member_social_accounts WHERE member_id = %s
            ORDER BY platform, account_name
            """, (member_id,)
        )

        self.step("Read committee and subcommittee assignments")
        sections["committees"] = self.query(
            """
            SELECT cm.congress_number, c.chamber AS committee_chamber,
                   cm.member_chamber, c.committee_code, c.name AS committee_name,
                   c.committee_type, parent.committee_code AS parent_code,
                   parent.name AS parent_committee, cm.party_side, cm.rank,
                   cm.title, cm.is_ex_officio, cm.start_date, cm.end_date,
                   c.website_url, c.minority_website_url, c.jurisdiction_text,
                   c.address, c.phone
            FROM committee_memberships cm
            JOIN committees c USING (committee_id)
            LEFT JOIN committees parent ON parent.committee_id = c.parent_committee_id
            WHERE cm.member_id = %s
            ORDER BY cm.congress_number, COALESCE(parent.name, c.name),
                     c.parent_committee_id NULLS FIRST, c.name
            """, (member_id,)
        )

        self.step("Read financial-disclosure filings and selections")
        sections["filings"] = self.query(
            """
            SELECT filing_id, source, source_filing_id, chamber,
                   filing_type_code_raw, filing_type, reporting_year, filed_date,
                   report_period_start, report_period_end, raw_full_name,
                   raw_office, state_code_guess, district_guess,
                   member_match_status, member_match_method,
                   member_match_confidence, source_url, discovered_at,
                   processing_status
            FROM filings WHERE member_id = %s
            ORDER BY filed_date, source_filing_id
            """, (member_id,)
        )
        sections["selections"] = self.query(
            """
            SELECT fs.filing_selection_id, fs.filing_id, fs.selection_batch_id,
                   sb.batch_name, fs.selection_reason, fs.priority,
                   fs.selected_at, fs.selected_by, fs.is_active
            FROM filing_selections fs
            JOIN filings f USING (filing_id)
            LEFT JOIN selection_batches sb USING (selection_batch_id)
            WHERE f.member_id = %s
            ORDER BY fs.selected_at
            """, (member_id,)
        )

        self.step("Read documents, processing jobs, and extractions")
        sections["documents"] = self.query(
            """
            SELECT d.document_id, d.filing_id, d.document_type, d.source_url,
                   d.local_path, d.mime_type, d.file_size_bytes, d.content_hash,
                   d.downloaded_at, d.http_status, d.is_primary, d.page_count,
                   d.has_embedded_text, d.requires_ocr, d.verification_status
            FROM documents d JOIN filings f USING (filing_id)
            WHERE f.member_id = %s ORDER BY d.filing_id, d.document_id
            """, (member_id,)
        )
        sections["jobs"] = self.query(
            """
            SELECT j.document_job_id, j.filing_id, j.document_id, j.job_type,
                   j.status, j.priority, j.attempt_count, j.max_attempts,
                   j.queued_at, j.started_at, j.finished_at, j.next_attempt_at,
                   j.worker_name, j.software_version, j.error_type, j.error_message
            FROM document_jobs j JOIN filings f USING (filing_id)
            WHERE f.member_id = %s ORDER BY j.queued_at, j.document_job_id
            """, (member_id,)
        )
        sections["extractions"] = self.query(
            """
            SELECT e.document_extraction_id, e.document_id, e.document_job_id,
                   e.extraction_type, e.extractor_name, e.extractor_version,
                   e.output_path, e.output_hash, e.started_at, e.finished_at,
                   e.quality_score, e.characters_extracted, e.pages_processed,
                   e.warnings, e.is_preferred
            FROM document_extractions e
            JOIN documents d USING (document_id)
            JOIN filings f USING (filing_id)
            WHERE f.member_id = %s
            ORDER BY e.document_id, e.document_extraction_id
            """, (member_id,)
        )

        self.step("Read transactions, securities, and extraction evidence")
        sections["trades"] = self.query(
            """
            SELECT t.trade_id, t.filing_id, t.document_id,
                   t.document_extraction_id, t.source_row_number,
                   t.transaction_date, t.notification_date, t.filed_date,
                   t.owner_type, t.owner_raw, t.transaction_type,
                   t.transaction_type_raw, t.asset_name_raw,
                   t.asset_type_code_raw, t.asset_type, t.security_id,
                   s.issuer_name, s.security_name, s.security_type,
                   t.ticker_reported, t.ticker_inferred,
                   t.ticker_inference_method, t.ticker_confidence,
                   t.amount_range_raw, t.amount_min, t.amount_max,
                   t.amount_exact, t.capital_gains_over_200,
                   t.description_raw, t.is_amended, t.supersedes_trade_id,
                   t.parser_name, t.parser_version, t.parse_confidence,
                   t.review_status
            FROM trades t
            JOIN filings f USING (filing_id)
            LEFT JOIN securities s USING (security_id)
            WHERE f.member_id = %s
            ORDER BY t.transaction_date, t.filing_id, t.source_row_number
            """, (member_id,)
        )
        sections["trade_evidence"] = self.query(
            """
            SELECT e.trade_evidence_id, e.trade_id, e.document_id,
                   e.document_extraction_id, e.field_name, e.page_number,
                   e.source_text, e.bounding_box, e.image_path, e.confidence
            FROM trade_evidence e
            JOIN trades t USING (trade_id)
            JOIN filings f USING (filing_id)
            WHERE f.member_id = %s
            ORDER BY e.trade_id, e.field_name, e.page_number
            """, (member_id,)
        )

        self.step("Read source provenance supporting the member record")
        sections["sources"] = self.query(
            """
            WITH member_sources AS (
                SELECT source_snapshot_id FROM members WHERE member_id = %s
                UNION SELECT source_snapshot_id FROM member_names WHERE member_id = %s
                UNION SELECT source_snapshot_id FROM member_identifiers WHERE member_id = %s
                UNION SELECT source_snapshot_id FROM member_terms WHERE member_id = %s
                UNION SELECT a.source_snapshot_id
                      FROM member_term_party_affiliations a
                      JOIN member_terms t USING (member_term_id)
                      WHERE t.member_id = %s
                UNION SELECT source_snapshot_id FROM member_family_relationships WHERE member_id = %s
                UNION SELECT source_snapshot_id FROM leadership_roles WHERE member_id = %s
                UNION SELECT source_snapshot_id FROM member_offices WHERE member_id = %s
                UNION SELECT source_snapshot_id FROM member_social_accounts WHERE member_id = %s
                UNION SELECT source_snapshot_id FROM committee_memberships WHERE member_id = %s
                UNION SELECT source_snapshot_id FROM filings WHERE member_id = %s
            )
            SELECT s.source_name, s.source_type, s.source_url, s.local_path,
                   s.coverage_start_date, s.coverage_end_date, s.retrieved_at,
                   s.content_hash, s.file_size_bytes, s.format_version, s.notes
            FROM source_snapshots s
            JOIN member_sources ms USING (source_snapshot_id)
            WHERE s.source_snapshot_id IS NOT NULL
            ORDER BY s.source_name, s.retrieved_at
            """, (member_id,) * 11
        )
        return sections


def build_report(member: dict[str, Any], sections: dict[str, list[dict[str, Any]]]) -> str:
    generated = datetime.now().astimezone()
    counts = {name: len(rows) for name, rows in sections.items()}
    lines = [
        "# StockGov Example Member Data Export",
        "",
        "## Instructions for the receiving LLM",
        "",
        "Use only the facts in this export. Do not invent missing details. Preserve data-quality notes and distinguish current information from historical information. Format the result as a professional member research profile suitable for a Word document. Empty sections mean that StockGov currently has no records in that domain; they do not prove that no such real-world records exist.",
        "",
        "## Export metadata",
        "",
        f"- **Generated:** {generated.isoformat()}",
        "- **Database:** StockGov PostgreSQL",
        "- **Export subject:** Mike Crapo",
        f"- **Bioguide ID:** {EXAMPLE_MEMBER_BIOGUIDE}",
        "- **Data policy:** Database contents only; no web research was performed by this export.",
        "",
        "## Core member record",
        "",
        bullets(member),
        "## Stored-record counts",
        "",
        table([{"category": name, "records": count} for name, count in counts.items()]),
    ]

    report_sections = (
        ("Names", "names"),
        ("External identifiers", "identifiers"),
        ("Congressional terms", "terms"),
        ("Dated party and caucus affiliations", "party_affiliations"),
        ("Family relationships", "family"),
        ("Leadership roles", "leadership"),
        ("Offices", "offices"),
        ("Official social-media accounts", "social"),
        ("Committee and subcommittee assignments", "committees"),
        ("Financial-disclosure filings", "filings"),
        ("Filing selections", "selections"),
        ("Downloaded documents", "documents"),
        ("Document-processing jobs", "jobs"),
        ("Document extractions", "extractions"),
        ("PTR transactions and securities", "trades"),
        ("Transaction extraction evidence", "trade_evidence"),
        ("Supporting source snapshots", "sources"),
    )
    for title, key in report_sections:
        lines.extend((f"## {title}", "", table(sections[key])))

    lines.extend((
        "## Data-completeness notice",
        "",
        "This export reports everything currently connected to the member in StockGov. A zero-record section means the corresponding ingestion stage has not produced member-linked records; it must not be stated as evidence that the member has no filings, transactions, documents, or other real-world activity.",
        "",
    ))
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export all stored StockGov information for Mike Crapo."
    )
    parser.add_argument("--database-url", help="Override DATABASE_URL from .env.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output_path = project_root() / "logs" / "example_member.log"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    started = datetime.now().astimezone()
    started_clock = time.monotonic()
    print(f"Start time: {started.isoformat()}", flush=True)
    print(f"Output file: {output_path}", flush=True)
    connection = None
    try:
        connection = psycopg2.connect(database_url(args.database_url))
        connection.set_session(readonly=True, autocommit=False)
        exporter = MemberExporter(connection)
        member = exporter.find_member()
        sections = exporter.collect(int(member["member_id"]))
        exporter.step("Format the LLM-ready member report")
        report = build_report(member, sections)
        output_path.write_text(report, encoding="utf-8")
        print(f"Report written: {output_path}", flush=True)
        print(f"Report size: {output_path.stat().st_size:,} bytes", flush=True)
        return 0
    except (OSError, ValueError, RuntimeError, psycopg2.Error) as exc:
        print(f"Member export failed: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        if connection is not None:
            connection.rollback()
            connection.close()
        finished = datetime.now().astimezone()
        print(f"End time: {finished.isoformat()}", flush=True)
        print(f"Elapsed: {time.monotonic() - started_clock:.2f} seconds", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
