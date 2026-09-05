"""Validate the StockGov congressional reference-data import.

The program reads the canonical YAML sources, compares them with PostgreSQL,
checks relational integrity, and prints numbered progress messages. It performs
read-only SQL and never changes database data.

Requirements:
    py -m pip install PyYAML psycopg2-binary

Example:
    py scripts/validate_congress_data.py
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml
except ImportError as exc:
    raise SystemExit("PyYAML is required: py -m pip install PyYAML") from exc

try:
    import psycopg2
except ImportError as exc:
    raise SystemExit(
        "psycopg2-binary is required: py -m pip install psycopg2-binary"
    ) from exc


CANONICAL_FILES = (
    "legislators-historical.yaml",
    "legislators-current.yaml",
    "committees-historical.yaml",
    "committees-current.yaml",
    "committee-membership-current.yaml",
    "legislators-district-offices.yaml",
    "legislators-social-media.yaml",
    "executive.yaml",
)

REQUIRED_TABLES = (
    "source_snapshots", "source_imports", "members", "member_names",
    "member_identifiers", "member_terms", "member_term_party_affiliations",
    "member_family_relationships", "leadership_roles", "member_offices",
    "member_social_accounts", "committees", "committee_identifiers",
    "committee_congresses", "committee_memberships", "executives",
    "executive_identifiers", "executive_terms", "filings",
    "member_match_candidates", "selection_batches", "filing_selections",
    "documents", "document_jobs", "document_extractions", "securities",
    "security_identifiers", "trades", "trade_evidence", "market_prices",
    "corporate_actions", "staging_members", "staging_committees",
    "staging_committee_memberships", "staging_house_filings",
    "staging_senate_filings", "staging_house_trades", "staging_senate_trades",
)


class TeeStream:
    """Write identical text to the terminal and the QA log file."""

    def __init__(self, terminal: Any, log_file: Any) -> None:
        self.terminal = terminal
        self.log_file = log_file

    def write(self, text: str) -> int:
        self.terminal.write(text)
        self.log_file.write(text)
        return len(text)

    def flush(self) -> None:
        self.terminal.flush()
        self.log_file.flush()

    def isatty(self) -> bool:
        return bool(getattr(self.terminal, "isatty", lambda: False)())

    @property
    def encoding(self) -> str:
        return getattr(self.terminal, "encoding", None) or "utf-8"


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
        raise ValueError("DATABASE_URL is not set in the project .env file")
    return value


def read_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as source:
        return yaml.safe_load(source)


def parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def normalized_name(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    return " ".join(text.casefold().replace(".", " ").replace(",", " ").split())


def scalar_values(value: Any) -> Iterable[str]:
    if value in (None, ""):
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value if item not in (None, ""))
    return (str(value),)


def committee_codes(records: list[dict[str, Any]]) -> set[str]:
    results: set[str] = set()
    for record in records:
        parent = str(record.get("thomas_id") or "").strip()
        if not parent:
            continue
        results.add(parent)
        for child in record.get("subcommittees") or []:
            child_id = str(child.get("thomas_id") or "").strip().zfill(2)
            results.add(parent + child_id)
    return results


@dataclass
class Result:
    passed: int = 0
    warnings: int = 0
    failed: int = 0


class Validator:
    def __init__(self, connection: Any, source_dir: Path, progress_every: int) -> None:
        self.connection = connection
        self.source_dir = source_dir
        self.progress_every = max(1, progress_every)
        self.result = Result()
        self.step_number = 0
        self.sources: dict[str, Any] = {}

    def step(self, title: str) -> None:
        self.step_number += 1
        print(f"\n[{self.step_number:02d}] {title}", flush=True)

    def progress(self, label: str, completed: int, total: int) -> None:
        if completed % self.progress_every == 0 or completed == total:
            percent = completed / total * 100 if total else 100
            print(f"     {label}: {completed:,}/{total:,} ({percent:5.1f}%)", flush=True)

    def pass_check(self, message: str) -> None:
        self.result.passed += 1
        print(f"  PASS: {message}", flush=True)

    def warn(self, message: str) -> None:
        self.result.warnings += 1
        print(f"  WARN: {message}", flush=True)

    def fail(self, message: str) -> None:
        self.result.failed += 1
        print(f"  FAIL: {message}", flush=True)

    def equal(self, label: str, expected: Any, actual: Any) -> None:
        if expected == actual:
            self.pass_check(f"{label}: {actual!r}")
        else:
            self.fail(f"{label}: expected {expected!r}, database has {actual!r}")

    def scalar(self, query: str, parameters: tuple[Any, ...] = ()) -> Any:
        with self.connection.cursor() as cursor:
            cursor.execute(query, parameters)
            return cursor.fetchone()[0]

    def rows(self, query: str, parameters: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        with self.connection.cursor() as cursor:
            cursor.execute(query, parameters)
            return list(cursor.fetchall())

    def load_sources(self) -> None:
        self.step("Validate and parse canonical YAML files")
        for position, filename in enumerate(CANONICAL_FILES, start=1):
            path = self.source_dir / filename
            if not path.is_file():
                self.fail(f"Missing source file: {path}")
                continue
            try:
                self.sources[filename] = read_yaml(path)
                size = path.stat().st_size
                print(f"  [{position}/{len(CANONICAL_FILES)}] {filename}: {size:,} bytes", flush=True)
            except (OSError, yaml.YAMLError) as exc:
                self.fail(f"Cannot parse {filename}: {exc}")
        if len(self.sources) == len(CANONICAL_FILES):
            self.pass_check("All eight canonical YAML files parsed successfully")

    def verify_schema(self) -> None:
        self.step("Verify required PostgreSQL tables and columns")
        actual = set(
            row[0] for row in self.rows(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
        )
        missing = sorted(set(REQUIRED_TABLES) - actual)
        extra = sorted(actual - set(REQUIRED_TABLES))
        if missing:
            self.fail("Missing tables: " + ", ".join(missing))
        else:
            self.pass_check(f"All {len(REQUIRED_TABLES)} required tables exist")
        if extra:
            self.warn("Additional public tables: " + ", ".join(extra))

        required_columns = {
            "member_terms": {"caucus_party_code", "term_end_type", "official_website_url"},
            "member_offices": {"source_office_id", "suite", "hours_text", "latitude", "longitude"},
            "member_social_accounts": {"platform_account_id"},
            "committees": {"jurisdiction_text", "minority_website_url", "wikipedia_name"},
            "committee_memberships": {"member_chamber", "is_ex_officio"},
            "executives": {"official_full_name", "gender", "suffix", "nickname"},
            "executive_terms": {"accession_method"},
        }
        for table, expected in required_columns.items():
            columns = set(
                row[0] for row in self.rows(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = %s", (table,)
                )
            )
            absent = sorted(expected - columns)
            if absent:
                self.fail(f"{table} missing columns: {', '.join(absent)}")
            else:
                self.pass_check(f"{table} contains its new normalized columns")

    def verify_snapshots(self) -> None:
        self.step("Verify source snapshots and file hashes")
        database: dict[str, set[str]] = {}
        for name, digest in self.rows(
                "SELECT source_name, content_hash FROM source_snapshots "
                "WHERE source_name LIKE 'congress-legislators/%%'"
            ):
            database.setdefault(name, set()).add(digest)
        current_files = sorted(
            path for path in self.source_dir.iterdir()
            if path.is_file()
            and path.suffix.lower() in {".yaml", ".json", ".csv"}
            and not path.stem.endswith("V1")
            and path.name != "download_manifest.json"
        )
        for path in current_files:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            key = f"congress-legislators/{path.name}"
            if digest not in database.get(key, set()):
                self.fail(f"Snapshot missing or hash mismatch: {path.name}")
            else:
                self.result.passed += 1
        self.pass_check(f"Checked snapshot hashes for {len(current_files)} source files")

    def verify_members(self) -> None:
        self.step("Compare members, identifiers, names, terms, and new member relationships")
        records = (
            (self.sources.get("legislators-historical.yaml") or [])
            + (self.sources.get("legislators-current.yaml") or [])
        )
        source_by_bioguide = {
            str(record["id"]["bioguide"]): record
            for record in records if (record.get("id") or {}).get("bioguide")
        }
        db_members = {
            row[0]: row[1:]
            for row in self.rows(
                "SELECT mi.identifier_value, m.first_name, m.middle_name, m.last_name, "
                "m.suffix, m.nickname, m.date_of_birth, m.gender "
                "FROM member_identifiers mi JOIN members m USING (member_id) "
                "WHERE mi.identifier_type = 'bioguide'"
            )
        }
        self.equal("Unique Bioguide member count", len(source_by_bioguide), len(db_members))

        mismatches = 0
        for position, (bioguide, record) in enumerate(source_by_bioguide.items(), start=1):
            name = record.get("name") or {}
            bio = record.get("bio") or {}
            expected = (
                name.get("first"), name.get("middle"), name.get("last"),
                name.get("suffix"), name.get("nickname"),
                parse_date(bio.get("birthday")), bio.get("gender"),
            )
            if db_members.get(bioguide) != expected:
                mismatches += 1
                if mismatches <= 10:
                    self.fail(f"Member core mismatch for Bioguide {bioguide}")
            self.progress("member core fields", position, len(source_by_bioguide))
        if mismatches == 0:
            self.pass_check("All member core fields match their YAML records")
        elif mismatches > 10:
            self.fail(f"Additional member core mismatches not printed: {mismatches - 10}")

        expected_terms = sum(len(record.get("terms") or []) for record in records)
        self.equal("Congressional term count", expected_terms, self.scalar("SELECT count(*) FROM member_terms"))
        expected_affiliations = sum(
            len(term.get("party_affiliations") or [])
            for record in records for term in record.get("terms") or []
        )
        self.equal(
            "Dated party-affiliation count", expected_affiliations,
            self.scalar("SELECT count(*) FROM member_term_party_affiliations"),
        )
        expected_family = {
            (
                str(record["id"]["bioguide"]),
                normalized_name(relative.get("name")),
                str(relative.get("relation") or "").strip(),
            )
            for record in records for relative in record.get("family") or []
            if relative.get("name") and relative.get("relation")
        }
        self.equal(
            "Family relationship count", len(expected_family),
            self.scalar("SELECT count(*) FROM member_family_relationships"),
        )
        raw_roles = [
            (
                str(record["id"]["bioguide"]),
                str(role.get("title") or "").strip(),
                parse_date(role.get("start")),
            )
            for record in records
            for role in record.get("leadership_roles") or []
            if role.get("title")
        ]
        unique_roles = set(raw_roles)
        duplicate_role_count = len(raw_roles) - len(unique_roles)
        self.equal(
            "Unique leadership-role count", len(unique_roles),
            self.scalar("SELECT count(*) FROM leadership_roles"),
        )
        if duplicate_role_count:
            self.warn(
                f"Source contains {duplicate_role_count:,} redundant leadership-role "
                "record(s) with the same member, title, and start date; the loader "
                "correctly merges them"
            )

        expected_identifiers = {
            (str(kind), value)
            for record in records
            for kind, raw in (record.get("id") or {}).items()
            for value in scalar_values(raw)
        }
        db_identifiers = set(self.rows("SELECT identifier_type, identifier_value FROM member_identifiers"))
        missing_identifiers = expected_identifiers - db_identifiers
        if missing_identifiers:
            self.fail(f"Missing {len(missing_identifiers):,} member identifiers")
        else:
            self.pass_check(f"All {len(expected_identifiers):,} legislator identifiers are present")

    def verify_offices_and_social(self) -> None:
        self.step("Compare district offices and social accounts")
        office_records = self.sources.get("legislators-district-offices.yaml") or []
        expected_offices = sum(len(record.get("offices") or []) for record in office_records)
        actual_offices = self.scalar("SELECT count(*) FROM member_offices WHERE office_type = 'district'")
        self.equal("District-office count", expected_offices, actual_offices)
        missing_office_ids = self.scalar(
            "SELECT count(*) FROM member_offices WHERE office_type = 'district' "
            "AND source_office_id IS NULL"
        )
        self.equal("District offices missing source IDs", 0, missing_office_ids)

        source_social = self.sources.get("legislators-social-media.yaml") or []
        expected_handles = sum(
            1 for record in source_social
            for key, value in (record.get("social") or {}).items()
            if value not in (None, "") and not key.endswith("_id")
        )
        id_only_youtube = sum(
            1 for record in source_social
            if (record.get("social") or {}).get("youtube_id")
            and not (record.get("social") or {}).get("youtube")
        )
        self.equal(
            "Social-account count", expected_handles + id_only_youtube,
            self.scalar("SELECT count(*) FROM member_social_accounts"),
        )

    def verify_committees(self) -> None:
        self.step("Compare committees, subcommittees, identifiers, and memberships")
        historical = self.sources.get("committees-historical.yaml") or []
        current = self.sources.get("committees-current.yaml") or []
        expected_codes = committee_codes(historical) | committee_codes(current)
        actual_codes = set(row[0] for row in self.rows("SELECT committee_code FROM committees"))
        missing = expected_codes - actual_codes
        unexpected = actual_codes - expected_codes
        if missing:
            self.fail(f"Missing {len(missing):,} committee codes")
        else:
            self.pass_check(f"All {len(expected_codes):,} source committee codes are present")
        if unexpected:
            self.warn(f"Database has {len(unexpected):,} placeholder/non-source committee codes")

        memberships = self.sources.get("committee-membership-current.yaml") or {}
        expected_memberships = sum(len(rows or []) for rows in memberships.values())
        self.equal(
            "Current committee-membership count", expected_memberships,
            self.scalar("SELECT count(*) FROM committee_memberships"),
        )
        expected_ex_officio = sum(
            1 for rows in memberships.values() for record in rows or []
            if str(record.get("title") or "").strip().casefold() == "ex officio"
            or bool(record.get("ex_officio"))
        )
        self.equal(
            "Ex-officio membership count", expected_ex_officio,
            self.scalar("SELECT count(*) FROM committee_memberships WHERE is_ex_officio"),
        )
        expected_joint_chambers = sum(
            1 for rows in memberships.values() for record in rows or []
            if record.get("chamber") in {"house", "senate"}
        )
        self.equal(
            "Memberships with explicit joint-committee chamber", expected_joint_chambers,
            self.scalar("SELECT count(*) FROM committee_memberships WHERE member_chamber IS NOT NULL"),
        )

    def verify_executives(self) -> None:
        self.step("Compare executives, identifiers, and executive terms")
        records = self.sources.get("executive.yaml") or []
        self.equal("Executive count", len(records), self.scalar("SELECT count(*) FROM executives"))
        expected_terms = sum(len(record.get("terms") or []) for record in records)
        self.equal("Executive-term count", expected_terms, self.scalar("SELECT count(*) FROM executive_terms"))
        expected_identifiers = {
            (str(kind), value)
            for record in records for kind, raw in (record.get("id") or {}).items()
            for value in scalar_values(raw)
        }
        actual_identifiers = set(
            self.rows("SELECT identifier_type, identifier_value FROM executive_identifiers")
        )
        missing = expected_identifiers - actual_identifiers
        if missing:
            self.fail(f"Missing {len(missing):,} executive identifiers")
        else:
            self.pass_check(f"All {len(expected_identifiers):,} executive identifiers are present")

    def verify_relations(self) -> None:
        self.step("Check relational integrity, duplicates, and staging coverage")
        orphan_checks = {
            "member names": "SELECT count(*) FROM member_names n LEFT JOIN members m USING (member_id) WHERE m.member_id IS NULL",
            "member identifiers": "SELECT count(*) FROM member_identifiers i LEFT JOIN members m USING (member_id) WHERE m.member_id IS NULL",
            "member terms": "SELECT count(*) FROM member_terms t LEFT JOIN members m USING (member_id) WHERE m.member_id IS NULL",
            "term affiliations": "SELECT count(*) FROM member_term_party_affiliations a LEFT JOIN member_terms t USING (member_term_id) WHERE t.member_term_id IS NULL",
            "family relationships": "SELECT count(*) FROM member_family_relationships f LEFT JOIN members m USING (member_id) WHERE m.member_id IS NULL",
            "committee memberships/member": "SELECT count(*) FROM committee_memberships cm LEFT JOIN members m USING (member_id) WHERE m.member_id IS NULL",
            "committee memberships/committee": "SELECT count(*) FROM committee_memberships cm LEFT JOIN committees c USING (committee_id) WHERE c.committee_id IS NULL",
            "executive terms": "SELECT count(*) FROM executive_terms et LEFT JOIN executives e USING (executive_id) WHERE e.executive_id IS NULL",
        }
        for label, query in orphan_checks.items():
            self.equal(f"Orphan {label}", 0, self.scalar(query))

        duplicate_checks = {
            "Bioguide member identifiers": "SELECT count(*) FROM (SELECT identifier_value FROM member_identifiers WHERE identifier_type='bioguide' GROUP BY identifier_value HAVING count(*)>1) q",
            "member terms": "SELECT count(*) FROM (SELECT member_id,chamber,term_start_date,state_code FROM member_terms GROUP BY 1,2,3,4 HAVING count(*)>1) q",
            "committee codes": "SELECT count(*) FROM (SELECT committee_code FROM committees GROUP BY committee_code HAVING count(*)>1) q",
            "committee memberships": "SELECT count(*) FROM (SELECT committee_id,member_id,congress_number,start_date FROM committee_memberships GROUP BY 1,2,3,4 HAVING count(*)>1) q",
        }
        for label, query in duplicate_checks.items():
            self.equal(f"Duplicate {label}", 0, self.scalar(query))

        source_counts = {
            "legislators-historical.yaml": len(self.sources.get("legislators-historical.yaml") or []),
            "legislators-current.yaml": len(self.sources.get("legislators-current.yaml") or []),
            "legislators-social-media.yaml": len(self.sources.get("legislators-social-media.yaml") or []),
            "legislators-district-offices.yaml": len(self.sources.get("legislators-district-offices.yaml") or []),
            "executive.yaml": len(self.sources.get("executive.yaml") or []),
        }
        expected_staging_members = sum(source_counts.values())
        self.equal(
            "Staging member-source row count", expected_staging_members,
            self.scalar("SELECT count(*) FROM staging_members"),
        )
        expected_staging_committees = sum(
            1 + len(record.get("subcommittees") or [])
            for filename in ("committees-historical.yaml", "committees-current.yaml")
            for record in self.sources.get(filename) or []
        )
        self.equal(
            "Staging committee row count", expected_staging_committees,
            self.scalar("SELECT count(*) FROM staging_committees"),
        )

        rejected = sum(
            self.scalar(f"SELECT count(*) FROM {table} WHERE validation_status = 'rejected'")
            for table in (
                "staging_members", "staging_committees",
                "staging_committee_memberships",
            )
        )
        if rejected:
            self.warn(f"{rejected:,} rejected congressional staging rows require review")
        else:
            self.pass_check("No congressional staging rows were rejected")

    def run(self) -> int:
        self.load_sources()
        if len(self.sources) != len(CANONICAL_FILES):
            self.fail("Cannot continue complete QA because source files are missing")
        else:
            self.verify_schema()
            self.verify_snapshots()
            self.verify_members()
            self.verify_offices_and_social()
            self.verify_committees()
            self.verify_executives()
            self.verify_relations()

        self.step("QA summary")
        print(f"  Passed checks : {self.result.passed:,}")
        print(f"  Warnings      : {self.result.warnings:,}")
        print(f"  Failed checks : {self.result.failed:,}")
        if self.result.failed:
            print("  RESULT: FAILED — review the failures above.", flush=True)
            return 1
        if self.result.warnings:
            print("  RESULT: PASSED WITH WARNINGS.", flush=True)
        else:
            print("  RESULT: PASSED.", flush=True)
        return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(
        description="Read-only QA comparison of congressional YAML and PostgreSQL."
    )
    parser.add_argument(
        "--source-dir", type=Path,
        default=root / "data" / "raw" / "congress",
        help="Canonical source directory (default: data/raw/congress).",
    )
    parser.add_argument("--database-url", help="Override DATABASE_URL from .env.")
    parser.add_argument(
        "--progress-every", type=int, default=1000,
        help="Print comparison progress every N members (default: 1000).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source_dir = args.source_dir.resolve()
    print("StockGov Congressional Import QA", flush=True)
    print(f"Source directory: {source_dir}", flush=True)
    print("Database access: read only", flush=True)
    try:
        connection = psycopg2.connect(database_url(args.database_url))
        connection.set_session(readonly=True, autocommit=False)
        try:
            return Validator(connection, source_dir, args.progress_every).run()
        finally:
            connection.rollback()
            connection.close()
    except (OSError, ValueError, psycopg2.Error) as exc:
        print(f"QA setup failed: {exc}", file=sys.stderr, flush=True)
        return 2


def run_with_log() -> int:
    """Run QA while mirroring stdout and stderr to qaresults.log."""
    log_directory = project_root() / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    log_path = log_directory / "qaresults.log"
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    started_at = datetime.now().astimezone()
    started_clock = time.monotonic()
    exit_code = 2

    with log_path.open("w", encoding="utf-8", newline="") as log_file:
        sys.stdout = TeeStream(original_stdout, log_file)
        sys.stderr = TeeStream(original_stderr, log_file)
        try:
            print("=" * 72, flush=True)
            print(f"QA start time : {started_at.isoformat()}", flush=True)
            print(f"QA log file   : {log_path}", flush=True)
            print("=" * 72, flush=True)
            exit_code = main()
            return exit_code
        except Exception:
            # Preserve unexpected tracebacks in both destinations.
            import traceback

            traceback.print_exc()
            exit_code = 2
            return exit_code
        finally:
            finished_at = datetime.now().astimezone()
            elapsed = time.monotonic() - started_clock
            print("\n" + "=" * 72, flush=True)
            print(f"QA end time   : {finished_at.isoformat()}", flush=True)
            print(f"Elapsed time  : {elapsed:.2f} seconds", flush=True)
            print(f"Exit status   : {exit_code}", flush=True)
            print("=" * 72, flush=True)
            sys.stdout = original_stdout
            sys.stderr = original_stderr


if __name__ == "__main__":
    raise SystemExit(run_with_log())
