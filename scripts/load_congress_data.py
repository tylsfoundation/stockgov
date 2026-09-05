"""Load congress-legislators YAML reference data into StockGov PostgreSQL.

The default paths are resolved from this file's location, so when this script is
stored at C:/Home/StockGov/scripts/load_congress_data.py it reads every supported
YAML file under C:/Home/StockGov/data/raw/congress.

The loader is idempotent at the normalized-table level. It also creates source
snapshot/import audit records and preserves complete source rows in the existing
staging tables where applicable.

Requirements:
    py -m pip install PyYAML psycopg2-binary python-dotenv

Examples:
    py scripts/load_congress_data.py --validate-only
    py scripts/load_congress_data.py
    py scripts/load_congress_data.py --current-congress 119
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

try:
    import yaml
except ImportError as exc:  # pragma: no cover - dependency error path
    raise SystemExit("PyYAML is required: py -m pip install PyYAML") from exc

try:
    import psycopg2
    from psycopg2.extras import Json
except ImportError as exc:  # pragma: no cover - dependency error path
    raise SystemExit(
        "psycopg2-binary is required: py -m pip install psycopg2-binary"
    ) from exc


IMPORTER_VERSION = "1.0.3"
SUPPORTED_FILES = (
    "legislators-historical.yaml",
    "legislators-current.yaml",
    "committees-historical.yaml",
    "committees-current.yaml",
    "committee-membership-current.yaml",
    "legislators-district-offices.yaml",
    "legislators-social-media.yaml",
    "executive.yaml",
)


@dataclass
class Counts:
    read: int = 0
    inserted: int = 0
    updated: int = 0
    rejected: int = 0

    def add(self, other: "Counts") -> None:
        self.read += other.read
        self.inserted += other.inserted
        self.updated += other.updated
        self.rejected += other.rejected


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_dotenv(project: Path) -> None:
    """Load the project .env when python-dotenv is installed."""

    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(project / ".env")


def database_url(override: str | None) -> str:
    value = override or os.getenv("DATABASE_URL")
    if value:
        return value
    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    if not user or not password:
        raise ValueError(
            "Set DATABASE_URL or POSTGRES_USER and POSTGRES_PASSWORD in .env"
        )
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5433")
    database = os.getenv("POSTGRES_DB", "congress_trades")
    return (
        f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}"
        f"@{host}:{port}/{database}"
    )


def read_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


def normalized_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(character for character in value if not unicodedata.combining(character))
    value = re.sub(r"[^a-z0-9]+", " ", value.casefold())
    return " ".join(value.split())


def full_name(name: dict[str, Any]) -> str:
    if name.get("official_full"):
        return str(name["official_full"]).strip()
    parts = [
        name.get("first"),
        name.get("middle"),
        name.get("last"),
        name.get("suffix"),
    ]
    return " ".join(str(part).strip() for part in parts if part)


def party_code(value: Any) -> str | None:
    if value in (None, ""):
        return None
    party = str(value).strip()
    known = {
        "democrat": "D",
        "democratic": "D",
        "republican": "R",
        "independent": "I",
        "libertarian": "L",
        "federalist": "F",
        "whig": "W",
    }
    return known.get(party.casefold(), party)


def congress_for_day(value: date) -> int:
    """Return the Congress active on a date, including historical start rules."""

    if value < date(1789, 3, 4):
        return 1
    number = ((value.year - 1789) // 2) + 1
    if value.year % 2 == 1:
        start_month_day = (3, 4) if value.year <= 1933 else (1, 3)
        if (value.month, value.day) < start_month_day:
            number -= 1
    return max(1, number)


def current_congress(today: date | None = None) -> int:
    return congress_for_day(today or date.today())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def jsonable(value: Any) -> Any:
    """Convert YAML-loaded objects to JSON-safe values."""

    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def scalar_values(value: Any) -> Iterable[str]:
    if value is None:
        return ()
    if isinstance(value, list):
        return (str(item) for item in value if item not in (None, ""))
    return (str(value),)


def chamber_from_term(term_type: str) -> str:
    mapping = {"rep": "house", "sen": "senate"}
    try:
        return mapping[term_type]
    except KeyError as exc:
        raise ValueError(f"Unknown congressional term type: {term_type!r}") from exc


def normalized_district(value: Any) -> int | None:
    """Map source district sentinels to the normalized database representation.

    congress-legislators uses ``-1`` in 1,359 historical House terms when a
    conventional numbered district is unavailable. The complete source value is
    retained in staging_members.raw_record; the normalized member_terms table
    uses NULL because its district constraint permits only zero or positive
    numbers. District zero remains zero and represents an at-large district.
    """

    if value in (None, ""):
        return None
    district = int(value)
    return district if district >= 0 else None


def committee_chamber(value: Any) -> str:
    text = str(value or "").casefold()
    if text in {"house", "senate", "joint"}:
        return text
    raise ValueError(f"Unknown committee chamber: {value!r}")


def committee_kind(name: str, chamber: str, is_subcommittee: bool) -> str:
    if is_subcommittee:
        return "subcommittee"
    lowered = name.casefold()
    if chamber == "joint":
        return "joint"
    if "select" in lowered or "task force" in lowered:
        return "select"
    if "special" in lowered:
        return "special"
    return "standing"


def subcommittee_code(parent_code: str, child_code: Any) -> str:
    child = str(child_code).strip()
    return child if child.startswith(parent_code) else f"{parent_code}{child}"


def social_url(platform: str, account: str) -> str:
    account = account.strip()
    if account.startswith(("http://", "https://")):
        return account
    bases = {
        "twitter": "https://twitter.com/",
        "facebook": "https://www.facebook.com/",
        "instagram": "https://www.instagram.com/",
        "youtube": "https://www.youtube.com/user/",
        "youtube_id": "https://www.youtube.com/channel/",
    }
    if platform == "mastodon":
        return account
    return f"{bases.get(platform, platform + ':')}{account.lstrip('@')}"


class CongressLoader:
    def __init__(
        self,
        connection: Any,
        source_dir: Path,
        congress_number: int,
        progress_every: int = 250,
    ):
        self.connection = connection
        self.source_dir = source_dir
        self.congress_number = congress_number
        self.progress_every = max(0, progress_every)

    @staticmethod
    def announce(message: str) -> None:
        print(message, flush=True)

    def parsed(self, path: Path, count: int) -> None:
        self.announce(f"  Parsed {count:,} source records from {path.name}; loading ...")

    def progress(
        self,
        filename: str,
        completed: int,
        total: int,
        counts: Counts,
        force: bool = False,
    ) -> None:
        if not force and (
            self.progress_every == 0 or completed % self.progress_every != 0
        ):
            return
        percent = (completed / total * 100) if total else 100.0
        self.announce(
            f"  {filename}: {completed:,}/{total:,} ({percent:5.1f}%) "
            f"inserted={counts.inserted:,} updated={counts.updated:,} "
            f"rejected={counts.rejected:,}"
        )

    def verify_schema(self) -> None:
        required = (
            "source_snapshots",
            "source_imports",
            "members",
            "member_names",
            "member_identifiers",
            "member_terms",
            "member_term_party_affiliations",
            "member_family_relationships",
            "leadership_roles",
            "member_offices",
            "member_social_accounts",
            "committees",
            "committee_identifiers",
            "committee_congresses",
            "committee_memberships",
            "executives",
            "executive_identifiers",
            "executive_terms",
            "staging_members",
            "staging_committees",
            "staging_committee_memberships",
        )
        with self.connection.cursor() as cursor:
            missing = []
            for table in required:
                cursor.execute("SELECT to_regclass(%s)", (f"public.{table}",))
                if cursor.fetchone()[0] is None:
                    missing.append(table)
        if missing:
            raise RuntimeError(
                "Database schema is missing tables: " + ", ".join(missing)
                + ". Run scripts/create_database.py first."
            )

    def register_companion_sources(self) -> None:
        """Record downloaded JSON/CSV representations without loading duplicates."""
        companions = sorted(
            path for path in self.source_dir.iterdir()
            if path.is_file()
            and path.suffix.lower() in {".json", ".csv"}
            and not path.stem.endswith("V1")
        )
        with self.connection:
            with self.connection.cursor() as cursor:
                for path in companions:
                    file_format = path.suffix.lstrip(".").upper()
                    cursor.execute(
                        """
                        INSERT INTO source_snapshots (
                            source_name, source_type, source_url, local_path,
                            content_hash, file_size_bytes, format_version, notes
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (source_name, content_hash) DO UPDATE SET
                            local_path = EXCLUDED.local_path,
                            file_size_bytes = EXCLUDED.file_size_bytes,
                            retrieved_at = CURRENT_TIMESTAMP,
                            notes = EXCLUDED.notes
                        """,
                        (
                            f"congress-legislators/{path.name}",
                            f"congressional_reference_{path.suffix.lstrip('.').lower()}",
                            "https://unitedstates.github.io/congress-legislators/"
                            + path.name,
                            str(path), sha256_file(path), path.stat().st_size,
                            file_format,
                            "Companion representation; YAML is canonical for loading",
                        ),
                    )

    def begin_import(self, cursor: Any, path: Path, import_type: str) -> tuple[int, int]:
        digest = sha256_file(path)
        stat = path.stat()
        cursor.execute(
            """
            INSERT INTO source_snapshots (
                source_name, source_type, source_url, local_path,
                content_hash, file_size_bytes, format_version, notes
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_name, content_hash) DO UPDATE SET
                local_path = EXCLUDED.local_path,
                file_size_bytes = EXCLUDED.file_size_bytes,
                retrieved_at = CURRENT_TIMESTAMP,
                notes = EXCLUDED.notes
            RETURNING source_snapshot_id
            """,
            (
                f"congress-legislators/{path.name}",
                "congressional_reference_yaml",
                f"https://github.com/unitedstates/congress-legislators/blob/main/{path.name}",
                str(path),
                digest,
                stat.st_size,
                "YAML",
                "CC0 source copied into data/raw/congress",
            ),
        )
        snapshot_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO source_imports (
                source_snapshot_id, import_type, importer_version, status
            ) VALUES (%s, %s, %s, 'running')
            RETURNING source_import_id
            """,
            (snapshot_id, import_type, IMPORTER_VERSION),
        )
        return snapshot_id, cursor.fetchone()[0]

    @staticmethod
    def finish_import(cursor: Any, import_id: int, counts: Counts) -> None:
        status = "partially_complete" if counts.rejected else "complete"
        cursor.execute(
            """
            UPDATE source_imports SET
                finished_at = CURRENT_TIMESTAMP,
                status = %s,
                records_read = %s,
                records_inserted = %s,
                records_updated = %s,
                records_rejected = %s,
                error_summary = %s
            WHERE source_import_id = %s
            """,
            (
                status,
                counts.read,
                counts.inserted,
                counts.updated,
                counts.rejected,
                f"{counts.rejected} source rows rejected" if counts.rejected else None,
                import_id,
            ),
        )

    @staticmethod
    def member_by_identifier(cursor: Any, kind: str, value: str) -> int | None:
        cursor.execute(
            """
            SELECT member_id FROM member_identifiers
            WHERE identifier_type = %s AND identifier_value = %s
            """,
            (kind, value),
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def member_by_bioguide(self, cursor: Any, value: Any) -> int | None:
        if not value:
            return None
        return self.member_by_identifier(cursor, "bioguide", str(value))

    @staticmethod
    def upsert_identifier(
        cursor: Any,
        member_id: int,
        kind: str,
        value: str,
        snapshot_id: int,
        primary: bool = False,
    ) -> bool:
        cursor.execute(
            """
            SELECT member_identifier_id, member_id
            FROM member_identifiers
            WHERE identifier_type = %s AND identifier_value = %s
            """,
            (kind, value),
        )
        row = cursor.fetchone()
        if row:
            if row[1] != member_id:
                raise ValueError(
                    f"Identifier {kind}:{value} already belongs to member {row[1]}"
                )
            cursor.execute(
                """
                UPDATE member_identifiers SET
                    is_primary = is_primary OR %s,
                    source_snapshot_id = %s
                WHERE member_identifier_id = %s
                """,
                (primary, snapshot_id, row[0]),
            )
            return False
        cursor.execute(
            """
            INSERT INTO member_identifiers (
                member_id, identifier_type, identifier_value,
                is_primary, source_snapshot_id
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            (member_id, kind, value, primary, snapshot_id),
        )
        return True

    @staticmethod
    def upsert_name(
        cursor: Any,
        member_id: int,
        name_type: str,
        value: str,
        parts: dict[str, Any],
        snapshot_id: int,
        valid_from: date | None = None,
        valid_to: date | None = None,
    ) -> bool:
        norm = normalized_name(value)
        cursor.execute(
            """
            SELECT member_name_id FROM member_names
            WHERE member_id = %s AND name_type = %s AND normalized_name = %s
              AND valid_from IS NOT DISTINCT FROM %s
            """,
            (member_id, name_type, norm, valid_from),
        )
        row = cursor.fetchone()
        values = (
            value,
            parts.get("first"),
            parts.get("middle"),
            parts.get("last"),
            parts.get("suffix"),
            valid_to,
            snapshot_id,
        )
        if row:
            cursor.execute(
                """
                UPDATE member_names SET
                    full_name = %s, first_name = %s, middle_name = %s,
                    last_name = %s, suffix = %s, valid_to = %s,
                    source_snapshot_id = %s
                WHERE member_name_id = %s
                """,
                (*values, row[0]),
            )
            return False
        cursor.execute(
            """
            INSERT INTO member_names (
                member_id, name_type, full_name, first_name, middle_name,
                last_name, suffix, normalized_name, valid_from, valid_to,
                source_snapshot_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                member_id,
                name_type,
                value,
                parts.get("first"),
                parts.get("middle"),
                parts.get("last"),
                parts.get("suffix"),
                norm,
                valid_from,
                valid_to,
                snapshot_id,
            ),
        )
        return True

    @staticmethod
    def stage_member(
        cursor: Any,
        import_id: int,
        row_number: int,
        raw: Any,
        identifiers: dict[str, Any],
        member_id: int | None,
        status: str = "loaded",
        error: str | None = None,
        term: dict[str, Any] | None = None,
        raw_name: str | None = None,
    ) -> None:
        term = term or {}
        chamber = None
        if term.get("type") in {"rep", "sen"}:
            chamber = chamber_from_term(str(term["type"]))
        errors = [error] if error else []
        cursor.execute(
            """
            INSERT INTO staging_members (
                source_import_id, source_row_number, raw_record, raw_full_name,
                normalized_name, raw_identifiers, chamber, state_code,
                district_number, party_raw, term_start_date, term_end_date,
                validation_status, error_details, member_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                import_id,
                row_number,
                Json(jsonable(raw)),
                raw_name,
                normalized_name(raw_name) if raw_name else None,
                Json(jsonable(identifiers or {})),
                chamber,
                term.get("state"),
                term.get("district"),
                term.get("party"),
                parse_date(term.get("start")),
                parse_date(term.get("end")),
                status,
                Json(errors),
                member_id,
            ),
        )

    def load_legislators(self, path: Path, is_current: bool) -> Counts:
        records = read_yaml(path) or []
        if not isinstance(records, list):
            raise ValueError(f"{path.name} must contain a YAML list")
        self.parsed(path, len(records))
        counts = Counts()
        with self.connection:
            with self.connection.cursor() as cursor:
                snapshot_id, import_id = self.begin_import(cursor, path, "legislators")
                for row_number, record in enumerate(records, 1):
                    counts.read += 1
                    identifiers = record.get("id") or {}
                    name = record.get("name") or {}
                    bio = record.get("bio") or {}
                    bioguide = identifiers.get("bioguide")
                    if not bioguide or not name.get("first") or not name.get("last"):
                        self.stage_member(
                            cursor, import_id, row_number, record, identifiers, None,
                            "rejected", "Missing Bioguide ID, first name, or last name",
                            raw_name=full_name(name),
                        )
                        counts.rejected += 1
                        self.progress(path.name, row_number, len(records), counts)
                        continue

                    member_id = self.member_by_bioguide(cursor, bioguide)
                    preferred = full_name(name)
                    living = True if is_current else (False if bio.get("death") else None)
                    if member_id:
                        cursor.execute(
                            """
                            UPDATE members SET
                                preferred_name = %s, first_name = %s, middle_name = %s,
                                last_name = %s, suffix = %s, nickname = %s,
                                date_of_birth = %s, gender = %s,
                                is_living = COALESCE(%s, is_living),
                                source_snapshot_id = %s
                            WHERE member_id = %s
                            """,
                            (
                                preferred, name["first"], name.get("middle"),
                                name["last"], name.get("suffix"), name.get("nickname"),
                                parse_date(bio.get("birthday")), bio.get("gender"),
                                living, snapshot_id, member_id,
                            ),
                        )
                        counts.updated += 1
                    else:
                        cursor.execute(
                            """
                            INSERT INTO members (
                                preferred_name, first_name, middle_name, last_name,
                                suffix, nickname, date_of_birth, gender, is_living,
                                source_snapshot_id
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            RETURNING member_id
                            """,
                            (
                                preferred, name["first"], name.get("middle"),
                                name["last"], name.get("suffix"), name.get("nickname"),
                                parse_date(bio.get("birthday")), bio.get("gender"),
                                living, snapshot_id,
                            ),
                        )
                        member_id = cursor.fetchone()[0]
                        counts.inserted += 1

                    for kind, raw_value in identifiers.items():
                        for value in scalar_values(raw_value):
                            inserted = self.upsert_identifier(
                                cursor, member_id, str(kind), value, snapshot_id,
                                primary=(kind == "bioguide"),
                            )
                            counts.inserted += int(inserted)
                            counts.updated += int(not inserted)

                    inserted = self.upsert_name(
                        cursor, member_id, "preferred", preferred, name, snapshot_id
                    )
                    counts.inserted += int(inserted)
                    counts.updated += int(not inserted)
                    official = name.get("official_full")
                    if official and normalized_name(str(official)) != normalized_name(preferred):
                        inserted = self.upsert_name(
                            cursor, member_id, "official", str(official), name, snapshot_id
                        )
                        counts.inserted += int(inserted)
                        counts.updated += int(not inserted)
                    if name.get("nickname"):
                        alias = f"{name['nickname']} {name['last']}"
                        inserted = self.upsert_name(
                            cursor, member_id, "nickname", alias, name, snapshot_id
                        )
                        counts.inserted += int(inserted)
                        counts.updated += int(not inserted)

                    for old_name in record.get("other_names") or []:
                        value = full_name(old_name)
                        inserted = self.upsert_name(
                            cursor,
                            member_id,
                            "former",
                            value,
                            old_name,
                            snapshot_id,
                            parse_date(old_name.get("start")),
                            parse_date(old_name.get("end")),
                        )
                        counts.inserted += int(inserted)
                        counts.updated += int(not inserted)

                    for relative in record.get("family") or []:
                        relative_name = str(relative.get("name") or "").strip()
                        relationship = str(relative.get("relation") or "").strip()
                        if not relative_name or not relationship:
                            continue
                        cursor.execute(
                            """
                            INSERT INTO member_family_relationships (
                                member_id, relative_name, relationship_type,
                                normalized_relative_name, source_snapshot_id
                            ) VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT (
                                member_id, normalized_relative_name, relationship_type
                            ) DO UPDATE SET
                                relative_name = EXCLUDED.relative_name,
                                source_snapshot_id = EXCLUDED.source_snapshot_id
                            RETURNING (xmax = 0)
                            """,
                            (
                                member_id, relative_name, relationship,
                                normalized_name(relative_name), snapshot_id,
                            ),
                        )
                        inserted = bool(cursor.fetchone()[0])
                        counts.inserted += int(inserted)
                        counts.updated += int(not inserted)

                    terms = record.get("terms") or []
                    for term in terms:
                        self.upsert_term(cursor, member_id, term, snapshot_id, counts)
                        self.upsert_capitol_office(cursor, member_id, term, snapshot_id, counts)
                    for role in record.get("leadership_roles") or []:
                        self.upsert_leadership(cursor, member_id, role, snapshot_id, counts)

                    latest_term = terms[-1] if terms else {}
                    self.stage_member(
                        cursor, import_id, row_number, record, identifiers,
                        member_id, term=latest_term, raw_name=preferred,
                    )
                    self.progress(path.name, row_number, len(records), counts)
                self.finish_import(cursor, import_id, counts)
                self.progress(path.name, len(records), len(records), counts, force=True)
        return counts

    @staticmethod
    def upsert_term(
        cursor: Any,
        member_id: int,
        term: dict[str, Any],
        snapshot_id: int,
        counts: Counts,
    ) -> None:
        start = parse_date(term.get("start"))
        end = parse_date(term.get("end"))
        if not start or not end:
            raise ValueError(f"Congressional term lacks dates: {term}")
        chamber = chamber_from_term(str(term.get("type")))
        state = str(term.get("state") or "").upper()
        if len(state) != 2:
            raise ValueError(f"Invalid term state: {term.get('state')!r}")
        district = normalized_district(term.get("district")) if chamber == "house" else None
        senate_class = term.get("class") if chamber == "senate" else None
        state_rank = term.get("state_rank") if chamber == "senate" else None
        cursor.execute(
            """
            INSERT INTO member_terms (
                member_id, chamber, term_start_date, term_end_date,
                congress_start, congress_end, state_code, district_number,
                senate_class, senate_state_rank, party_code, party_name_raw,
                caucus_party_code, caucus_party_name_raw, term_type,
                term_end_type, official_website_url, contact_form_url, rss_url,
                source_snapshot_id
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (member_id, chamber, term_start_date, state_code) DO UPDATE SET
                term_end_date = EXCLUDED.term_end_date,
                congress_start = EXCLUDED.congress_start,
                congress_end = EXCLUDED.congress_end,
                district_number = EXCLUDED.district_number,
                senate_class = EXCLUDED.senate_class,
                senate_state_rank = EXCLUDED.senate_state_rank,
                party_code = EXCLUDED.party_code,
                party_name_raw = EXCLUDED.party_name_raw,
                caucus_party_code = EXCLUDED.caucus_party_code,
                caucus_party_name_raw = EXCLUDED.caucus_party_name_raw,
                term_type = EXCLUDED.term_type,
                term_end_type = EXCLUDED.term_end_type,
                official_website_url = EXCLUDED.official_website_url,
                contact_form_url = EXCLUDED.contact_form_url,
                rss_url = EXCLUDED.rss_url,
                source_snapshot_id = EXCLUDED.source_snapshot_id
            RETURNING member_term_id, (xmax = 0)
            """,
            (
                member_id, chamber, start, end,
                congress_for_day(start), congress_for_day(end - timedelta(days=1)),
                state, district, senate_class, state_rank,
                party_code(term.get("party")), term.get("party"),
                party_code(term.get("caucus")), term.get("caucus"),
                term.get("how") or "regular", term.get("end-type"),
                term.get("url"), term.get("contact_form"), term.get("rss_url"),
                snapshot_id,
            ),
        )
        member_term_id, inserted = cursor.fetchone()
        inserted = bool(inserted)
        counts.inserted += int(inserted)
        counts.updated += int(not inserted)

        for affiliation in term.get("party_affiliations") or []:
            affiliation_start = parse_date(affiliation.get("start"))
            affiliation_end = parse_date(affiliation.get("end"))
            if not affiliation_start or not affiliation_end:
                continue
            cursor.execute(
                """
                INSERT INTO member_term_party_affiliations (
                    member_term_id, party_code, party_name_raw,
                    caucus_party_code, caucus_party_name_raw,
                    start_date, end_date, source_snapshot_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (member_term_id, start_date) DO UPDATE SET
                    party_code = EXCLUDED.party_code,
                    party_name_raw = EXCLUDED.party_name_raw,
                    caucus_party_code = EXCLUDED.caucus_party_code,
                    caucus_party_name_raw = EXCLUDED.caucus_party_name_raw,
                    end_date = EXCLUDED.end_date,
                    source_snapshot_id = EXCLUDED.source_snapshot_id
                RETURNING (xmax = 0)
                """,
                (
                    member_term_id,
                    party_code(affiliation.get("party")), affiliation.get("party"),
                    party_code(affiliation.get("caucus")), affiliation.get("caucus"),
                    affiliation_start, affiliation_end, snapshot_id,
                ),
            )
            affiliation_inserted = bool(cursor.fetchone()[0])
            counts.inserted += int(affiliation_inserted)
            counts.updated += int(not affiliation_inserted)

    @staticmethod
    def upsert_capitol_office(
        cursor: Any,
        member_id: int,
        term: dict[str, Any],
        snapshot_id: int,
        counts: Counts,
    ) -> None:
        address = term.get("address")
        office = term.get("office")
        if not any((address, office, term.get("phone"), term.get("fax"))):
            return
        start = parse_date(term.get("start"))
        end = parse_date(term.get("end"))
        cursor.execute(
            """
            SELECT member_office_id FROM member_offices
            WHERE member_id = %s AND office_type = 'capitol'
              AND valid_from IS NOT DISTINCT FROM %s
            """,
            (member_id, start),
        )
        row = cursor.fetchone()
        values = (
            office, address, term.get("phone"), term.get("fax"),
            end, snapshot_id,
        )
        if row:
            cursor.execute(
                """
                UPDATE member_offices SET building = %s, address_line_1 = %s,
                    phone = %s, fax = %s, valid_to = %s, source_snapshot_id = %s
                WHERE member_office_id = %s
                """,
                (*values, row[0]),
            )
            counts.updated += 1
        else:
            cursor.execute(
                """
                INSERT INTO member_offices (
                    member_id, office_type, building, address_line_1,
                    phone, fax, valid_from, valid_to, source_snapshot_id
                ) VALUES (%s, 'capitol', %s, %s, %s, %s, %s, %s, %s)
                """,
                (member_id, office, address, term.get("phone"), term.get("fax"),
                 start, end, snapshot_id),
            )
            counts.inserted += 1

    @staticmethod
    def upsert_leadership(
        cursor: Any,
        member_id: int,
        role: dict[str, Any],
        snapshot_id: int,
        counts: Counts,
    ) -> None:
        title = role.get("title")
        if not title:
            return
        start = parse_date(role.get("start"))
        end = parse_date(role.get("end"))
        cursor.execute(
            """
            SELECT leadership_role_id FROM leadership_roles
            WHERE member_id = %s AND role_title = %s
              AND start_date IS NOT DISTINCT FROM %s
            """,
            (member_id, title, start),
        )
        row = cursor.fetchone()
        chamber = role.get("chamber")
        values = (
            chamber, party_code(role.get("party")), end,
            congress_for_day(start) if start else None,
            congress_for_day(end - timedelta(days=1)) if end else None,
            snapshot_id,
        )
        if row:
            cursor.execute(
                """
                UPDATE leadership_roles SET chamber = %s, party_code = %s,
                    end_date = %s, congress_start = %s, congress_end = %s,
                    source_snapshot_id = %s
                WHERE leadership_role_id = %s
                """,
                (*values, row[0]),
            )
            counts.updated += 1
        else:
            cursor.execute(
                """
                INSERT INTO leadership_roles (
                    member_id, chamber, role_title, party_code,
                    start_date, end_date, congress_start, congress_end,
                    source_snapshot_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (member_id, chamber, title, party_code(role.get("party")),
                 start, end, values[3], values[4], snapshot_id),
            )
            counts.inserted += 1

    def load_committees(self, path: Path, is_current: bool) -> Counts:
        records = read_yaml(path) or []
        if not isinstance(records, list):
            raise ValueError(f"{path.name} must contain a YAML list")
        total_rows = sum(1 + len(record.get("subcommittees") or []) for record in records)
        self.parsed(path, total_rows)
        counts = Counts()
        with self.connection:
            with self.connection.cursor() as cursor:
                snapshot_id, import_id = self.begin_import(cursor, path, "committees")
                staging_row = 0
                for record in records:
                    chamber = committee_chamber(record.get("type"))
                    parent_code = str(record.get("thomas_id") or "").strip()
                    if not parent_code:
                        counts.read += 1
                        counts.rejected += 1
                        staging_row += 1
                        self.stage_committee(
                            cursor, import_id, staging_row, record, None, None,
                            "rejected", "Missing committee thomas_id",
                        )
                        continue
                    parent_id, inserted = self.upsert_committee(
                        cursor, parent_code, chamber,
                        committee_kind(str(record.get("name") or parent_code), chamber, False),
                        str(record.get("name") or parent_code), None,
                        is_current, record.get("url"), record, snapshot_id,
                    )
                    counts.read += 1
                    counts.inserted += int(inserted)
                    counts.updated += int(not inserted)
                    self.upsert_committee_identifiers(
                        cursor, parent_id, parent_code, record, snapshot_id, counts
                    )
                    staging_row += 1
                    self.stage_committee(
                        cursor, import_id, staging_row, record, parent_id, None, "loaded", None
                    )
                    self.upsert_committee_congresses(
                        cursor, parent_id, parent_code, record, is_current,
                        snapshot_id, counts,
                    )

                    for child in record.get("subcommittees") or []:
                        staging_row += 1
                        counts.read += 1
                        child_code = subcommittee_code(parent_code, child.get("thomas_id"))
                        child_name = str(child.get("name") or child_code)
                        child_id, inserted = self.upsert_committee(
                            cursor, child_code, chamber, "subcommittee", child_name,
                            parent_id, is_current, child.get("url"), child, snapshot_id,
                        )
                        counts.inserted += int(inserted)
                        counts.updated += int(not inserted)
                        self.upsert_committee_identifiers(
                            cursor, child_id, child_code, child, snapshot_id, counts,
                            is_subcommittee=True,
                        )
                        raw_child = {"parent": record, "subcommittee": child}
                        self.stage_committee(
                            cursor, import_id, staging_row, raw_child, child_id,
                            parent_code, "loaded", None,
                        )
                        self.upsert_committee_congresses(
                            cursor, child_id, child_code, child, is_current,
                            snapshot_id, counts,
                        )
                    self.progress(path.name, counts.read, total_rows, counts)
                self.finish_import(cursor, import_id, counts)
                self.progress(path.name, counts.read, total_rows, counts, force=True)
        return counts

    @staticmethod
    def upsert_committee(
        cursor: Any,
        code: str,
        chamber: str,
        kind: str,
        name: str,
        parent_id: int | None,
        is_current: bool,
        website: Any,
        metadata: dict[str, Any],
        snapshot_id: int,
    ) -> tuple[int, bool]:
        cursor.execute("SELECT committee_id FROM committees WHERE committee_code = %s", (code,))
        row = cursor.fetchone()
        if row:
            cursor.execute(
                """
                UPDATE committees SET
                    chamber = %s, committee_type = %s, name = %s, name_raw = %s,
                    parent_committee_id = %s,
                    is_current = is_current OR %s,
                    website_url = COALESCE(%s, website_url),
                    minority_website_url = COALESCE(%s, minority_website_url),
                    jurisdiction_text = COALESCE(%s, jurisdiction_text),
                    jurisdiction_source_url = COALESCE(%s, jurisdiction_source_url),
                    address = COALESCE(%s, address), phone = COALESCE(%s, phone),
                    rss_url = COALESCE(%s, rss_url),
                    minority_rss_url = COALESCE(%s, minority_rss_url),
                    youtube_channel_id = COALESCE(%s, youtube_channel_id),
                    wikipedia_name = COALESCE(%s, wikipedia_name),
                    source_snapshot_id = %s
                WHERE committee_id = %s
                """,
                (chamber, kind, name, name, parent_id, is_current,
                 website, metadata.get("minority_url"), metadata.get("jurisdiction"),
                 metadata.get("jurisdiction_source"), metadata.get("address"),
                 metadata.get("phone"), metadata.get("rss_url"),
                 metadata.get("minority_rss_url"), metadata.get("youtube_id"),
                 metadata.get("wikipedia"), snapshot_id, row[0]),
            )
            return row[0], False
        cursor.execute(
            """
            INSERT INTO committees (
                committee_code, chamber, committee_type, name, name_raw,
                parent_committee_id, is_current, website_url, minority_website_url,
                jurisdiction_text, jurisdiction_source_url, address, phone, rss_url,
                minority_rss_url, youtube_channel_id, wikipedia_name,
                source_snapshot_id
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s
            )
            RETURNING committee_id
            """,
            (code, chamber, kind, name, name, parent_id,
             is_current, website, metadata.get("minority_url"),
             metadata.get("jurisdiction"), metadata.get("jurisdiction_source"),
             metadata.get("address"), metadata.get("phone"), metadata.get("rss_url"),
             metadata.get("minority_rss_url"), metadata.get("youtube_id"),
             metadata.get("wikipedia"), snapshot_id),
        )
        return cursor.fetchone()[0], True

    @staticmethod
    def upsert_committee_identifiers(
        cursor: Any,
        committee_id: int,
        canonical_code: str,
        record: dict[str, Any],
        snapshot_id: int,
        counts: Counts,
        is_subcommittee: bool = False,
    ) -> None:
        identifiers = {"thomas": canonical_code}
        if not is_subcommittee:
            identifiers.update({
                "house_committee": record.get("house_committee_id"),
                "senate_committee": record.get("senate_committee_id"),
            })
        for kind, raw_value in identifiers.items():
            if raw_value in (None, ""):
                continue
            cursor.execute(
                """
                INSERT INTO committee_identifiers (
                    committee_id, identifier_type, identifier_value,
                    is_primary, source_snapshot_id
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (identifier_type, identifier_value) DO UPDATE SET
                    committee_id = EXCLUDED.committee_id,
                    is_primary = committee_identifiers.is_primary OR EXCLUDED.is_primary,
                    source_snapshot_id = EXCLUDED.source_snapshot_id
                RETURNING (xmax = 0)
                """,
                (committee_id, kind, str(raw_value), kind == "thomas", snapshot_id),
            )
            inserted = bool(cursor.fetchone()[0])
            counts.inserted += int(inserted)
            counts.updated += int(not inserted)

    @staticmethod
    def stage_committee(
        cursor: Any,
        import_id: int,
        row_number: int,
        raw: Any,
        committee_id: int | None,
        parent_code: str | None,
        status: str,
        error: str | None,
    ) -> None:
        value = raw.get("subcommittee", raw) if isinstance(raw, dict) else {}
        cursor.execute(
            """
            INSERT INTO staging_committees (
                source_import_id, source_row_number, raw_record,
                committee_code_raw, name_raw, chamber_raw, committee_type_raw,
                parent_code_raw, congress_start, congress_end,
                validation_status, error_details, committee_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                import_id, row_number, Json(jsonable(raw)), value.get("thomas_id"),
                value.get("name"), value.get("type"),
                "subcommittee" if parent_code else None, parent_code,
                min(value.get("congresses") or [None]),
                max(value.get("congresses") or [None]),
                status, Json([error] if error else []), committee_id,
            ),
        )

    def upsert_committee_congresses(
        self,
        cursor: Any,
        committee_id: int,
        code: str,
        record: dict[str, Any],
        is_current: bool,
        snapshot_id: int,
        counts: Counts,
    ) -> None:
        congresses = [self.congress_number] if is_current else (record.get("congresses") or [])
        names = record.get("names") or {}
        default_name = str(record.get("name") or code)
        for number in congresses:
            number = int(number)
            historical_name = names.get(number, names.get(str(number), default_name))
            cursor.execute(
                """
                INSERT INTO committee_congresses (
                    committee_id, congress_number, name, committee_code,
                    is_active, source_snapshot_id
                ) VALUES (%s, %s, %s, %s, TRUE, %s)
                ON CONFLICT (committee_id, congress_number) DO UPDATE SET
                    name = EXCLUDED.name,
                    committee_code = EXCLUDED.committee_code,
                    is_active = TRUE,
                    source_snapshot_id = EXCLUDED.source_snapshot_id
                RETURNING (xmax = 0)
                """,
                (committee_id, number, historical_name, code, snapshot_id),
            )
            inserted = bool(cursor.fetchone()[0])
            counts.inserted += int(inserted)
            counts.updated += int(not inserted)

    def ensure_committee_placeholder(
        self, cursor: Any, code: str, snapshot_id: int
    ) -> int:
        cursor.execute("SELECT committee_id FROM committees WHERE committee_code = %s", (code,))
        row = cursor.fetchone()
        if row:
            return row[0]
        prefix = code[:1].upper()
        chamber = {"H": "house", "S": "senate", "J": "joint"}.get(prefix, "joint")
        kind = "subcommittee" if len(code) > 4 else ("joint" if chamber == "joint" else "other")
        parent_id = None
        if kind == "subcommittee":
            cursor.execute("SELECT committee_id FROM committees WHERE committee_code = %s", (code[:4],))
            parent = cursor.fetchone()
            parent_id = parent[0] if parent else None
        cursor.execute(
            """
            INSERT INTO committees (
                committee_code, chamber, committee_type, name, name_raw,
                parent_committee_id, is_current, source_snapshot_id
            ) VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s)
            RETURNING committee_id
            """,
            (code, chamber, kind, f"Unresolved committee {code}",
             f"Unresolved committee {code}", parent_id, snapshot_id),
        )
        return cursor.fetchone()[0]

    def load_memberships(self, path: Path) -> Counts:
        groups = read_yaml(path) or {}
        if not isinstance(groups, dict):
            raise ValueError(f"{path.name} must contain a YAML mapping")
        total_rows = sum(len(rows or []) for rows in groups.values())
        self.parsed(path, total_rows)
        counts = Counts()
        with self.connection:
            with self.connection.cursor() as cursor:
                snapshot_id, import_id = self.begin_import(
                    cursor, path, "committee_memberships"
                )
                row_number = 0
                for committee_code, memberships in groups.items():
                    committee_id = self.ensure_committee_placeholder(
                        cursor, str(committee_code), snapshot_id
                    )
                    for record in memberships or []:
                        row_number += 1
                        counts.read += 1
                        member_id = self.member_by_bioguide(cursor, record.get("bioguide"))
                        if not member_id:
                            self.stage_membership(
                                cursor, import_id, row_number, record, committee_code,
                                None, "rejected", "Bioguide ID was not loaded",
                            )
                            counts.rejected += 1
                            self.progress(path.name, row_number, total_rows, counts)
                            continue
                        cursor.execute(
                            """
                            SELECT committee_membership_id FROM committee_memberships
                            WHERE committee_id = %s AND member_id = %s
                              AND congress_number = %s AND start_date IS NULL
                            """,
                            (committee_id, member_id, self.congress_number),
                        )
                        existing = cursor.fetchone()
                        values = (
                            record.get("chamber"), record.get("party"), record.get("rank"),
                            record.get("title"),
                            bool(record.get("ex_officio"))
                            or str(record.get("title") or "").strip().casefold()
                            == "ex officio",
                            snapshot_id,
                        )
                        if existing:
                            cursor.execute(
                                """
                                UPDATE committee_memberships SET
                                    member_chamber = %s, party_side = %s, rank = %s, title = %s,
                                    is_ex_officio = %s, source_snapshot_id = %s
                                WHERE committee_membership_id = %s
                                """,
                                (*values, existing[0]),
                            )
                            membership_id = existing[0]
                            counts.updated += 1
                        else:
                            cursor.execute(
                                """
                                INSERT INTO committee_memberships (
                                    committee_id, member_id, congress_number,
                                    member_chamber, party_side, rank, title, is_ex_officio,
                                    source_snapshot_id
                                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                                RETURNING committee_membership_id
                                """,
                                (committee_id, member_id, self.congress_number, *values),
                            )
                            membership_id = cursor.fetchone()[0]
                            counts.inserted += 1
                        self.stage_membership(
                            cursor, import_id, row_number, record, committee_code,
                            membership_id, "loaded", None,
                        )
                        self.progress(path.name, row_number, total_rows, counts)
                self.finish_import(cursor, import_id, counts)
                self.progress(path.name, total_rows, total_rows, counts, force=True)
        return counts

    def stage_membership(
        self,
        cursor: Any,
        import_id: int,
        row_number: int,
        raw: dict[str, Any],
        committee_code: Any,
        membership_id: int | None,
        status: str,
        error: str | None,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO staging_committee_memberships (
                source_import_id, source_row_number, raw_record,
                bioguide_id_raw, member_name_raw, committee_code_raw,
                congress_number, party_side_raw, rank_raw, title_raw,
                validation_status, error_details, committee_membership_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                import_id, row_number, Json(jsonable(raw)), raw.get("bioguide"),
                raw.get("name"), committee_code, self.congress_number,
                raw.get("party"), str(raw.get("rank")) if raw.get("rank") else None,
                raw.get("title"), status, Json([error] if error else []), membership_id,
            ),
        )

    def load_district_offices(self, path: Path) -> Counts:
        records = read_yaml(path) or []
        self.parsed(path, len(records))
        counts = Counts()
        with self.connection:
            with self.connection.cursor() as cursor:
                snapshot_id, import_id = self.begin_import(cursor, path, "district_offices")
                for row_number, record in enumerate(records, 1):
                    counts.read += 1
                    identifiers = record.get("id") or {}
                    member_id = self.member_by_bioguide(cursor, identifiers.get("bioguide"))
                    if not member_id:
                        self.stage_member(
                            cursor, import_id, row_number, record, identifiers, None,
                            "rejected", "Bioguide ID was not loaded",
                        )
                        counts.rejected += 1
                        self.progress(path.name, row_number, len(records), counts)
                        continue
                    self.stage_member(
                        cursor, import_id, row_number, record, identifiers, member_id
                    )
                    for office in record.get("offices") or []:
                        office_key = str(office.get("id") or f"{row_number}-{office.get('city', '')}")
                        office_type = "district"
                        cursor.execute(
                            """
                            SELECT member_office_id FROM member_offices
                            WHERE member_id = %s AND source_office_id = %s
                            """,
                            (member_id, office_key),
                        )
                        existing = cursor.fetchone()
                        values = (
                            office_type, office_key, office.get("building"),
                            office.get("suite"), office.get("address"),
                            office.get("city"), office.get("state"), office.get("zip"),
                            office.get("phone"), office.get("fax"), office.get("hours"),
                            office.get("latitude"), office.get("longitude"), snapshot_id,
                        )
                        if existing:
                            cursor.execute(
                                """
                                UPDATE member_offices SET
                                    office_type = %s, source_office_id = %s,
                                    building = %s, suite = %s, address_line_1 = %s,
                                    city = %s, state_code = %s, postal_code = %s,
                                    phone = %s, fax = %s, hours_text = %s,
                                    latitude = %s, longitude = %s, source_snapshot_id = %s
                                WHERE member_office_id = %s
                                """,
                                (*values, existing[0]),
                            )
                            counts.updated += 1
                        else:
                            cursor.execute(
                                """
                                INSERT INTO member_offices (
                                    member_id, office_type, source_office_id, building,
                                    suite, address_line_1, city, state_code, postal_code,
                                    phone, fax, hours_text, latitude, longitude,
                                    source_snapshot_id
                                ) VALUES (
                                    %s, %s, %s, %s, %s, %s, %s, %s,
                                    %s, %s, %s, %s, %s, %s, %s
                                )
                                """,
                                (member_id, *values),
                            )
                            counts.inserted += 1
                    self.progress(path.name, row_number, len(records), counts)
                self.finish_import(cursor, import_id, counts)
                self.progress(path.name, len(records), len(records), counts, force=True)
        return counts

    def load_social(self, path: Path) -> Counts:
        records = read_yaml(path) or []
        self.parsed(path, len(records))
        counts = Counts()
        with self.connection:
            with self.connection.cursor() as cursor:
                snapshot_id, import_id = self.begin_import(cursor, path, "social_accounts")
                for row_number, record in enumerate(records, 1):
                    counts.read += 1
                    identifiers = record.get("id") or {}
                    member_id = self.member_by_bioguide(cursor, identifiers.get("bioguide"))
                    if not member_id:
                        self.stage_member(
                            cursor, import_id, row_number, record, identifiers, None,
                            "rejected", "Bioguide ID was not loaded",
                        )
                        counts.rejected += 1
                        self.progress(path.name, row_number, len(records), counts)
                        continue
                    self.stage_member(
                        cursor, import_id, row_number, record, identifiers, member_id
                    )
                    for kind, raw_value in identifiers.items():
                        for value in scalar_values(raw_value):
                            inserted = self.upsert_identifier(
                                cursor, member_id, str(kind), value, snapshot_id,
                                primary=(kind == "bioguide"),
                            )
                            counts.inserted += int(inserted)
                            counts.updated += int(not inserted)
                    social = record.get("social") or {}
                    for platform, raw_account in social.items():
                        if raw_account in (None, ""):
                            continue
                        account = str(raw_account)
                        if platform.endswith("_id"):
                            inserted = self.upsert_identifier(
                                cursor, member_id, f"social_{platform}", account,
                                snapshot_id,
                            )
                            counts.inserted += int(inserted)
                            counts.updated += int(not inserted)
                            base_platform = platform.removesuffix("_id")
                            cursor.execute(
                                """
                                UPDATE member_social_accounts SET
                                    platform_account_id = %s,
                                    source_snapshot_id = %s
                                WHERE member_id = %s AND platform = %s
                                """,
                                (account, snapshot_id, member_id, base_platform),
                            )
                            if platform == "youtube_id" and not social.get("youtube"):
                                channel_url = social_url(platform, account)
                                cursor.execute(
                                    """
                                    INSERT INTO member_social_accounts (
                                        member_id, platform, account_name,
                                        platform_account_id, account_url,
                                        is_official, source_snapshot_id
                                    ) VALUES (%s, 'youtube', NULL, %s, %s, TRUE, %s)
                                    ON CONFLICT (platform, account_url) DO UPDATE SET
                                        platform_account_id = EXCLUDED.platform_account_id,
                                        is_official = TRUE,
                                        source_snapshot_id = EXCLUDED.source_snapshot_id
                                    """,
                                    (member_id, account, channel_url, snapshot_id),
                                )
                            continue
                        normalized_platform = platform
                        url = social_url(platform, account)
                        platform_account_id = social.get(f"{platform}_id")
                        cursor.execute(
                            """
                            SELECT member_social_account_id, member_id
                            FROM member_social_accounts
                            WHERE platform = %s AND account_url = %s
                            """,
                            (normalized_platform, url),
                        )
                        existing = cursor.fetchone()
                        if existing:
                            if existing[1] != member_id:
                                raise ValueError(
                                    f"Social account {normalized_platform}:{url} belongs to another member"
                                )
                            cursor.execute(
                                """
                                UPDATE member_social_accounts SET
                                    account_name = %s, platform_account_id = %s,
                                    is_official = TRUE,
                                    source_snapshot_id = %s
                                WHERE member_social_account_id = %s
                                """,
                                (account, platform_account_id, snapshot_id, existing[0]),
                            )
                            counts.updated += 1
                        else:
                            cursor.execute(
                                """
                                INSERT INTO member_social_accounts (
                                    member_id, platform, account_name, account_url,
                                    platform_account_id, is_official, source_snapshot_id
                                ) VALUES (%s, %s, %s, %s, %s, TRUE, %s)
                                """,
                                (
                                    member_id, normalized_platform, account, url,
                                    platform_account_id, snapshot_id,
                                ),
                            )
                            counts.inserted += 1
                    self.progress(path.name, row_number, len(records), counts)
                self.finish_import(cursor, import_id, counts)
                self.progress(path.name, len(records), len(records), counts, force=True)
        return counts

    def load_executives(self, path: Path) -> Counts:
        records = read_yaml(path) or []
        self.parsed(path, len(records))
        counts = Counts()
        with self.connection:
            with self.connection.cursor() as cursor:
                snapshot_id, import_id = self.begin_import(cursor, path, "executives")
                for row_number, record in enumerate(records, 1):
                    counts.read += 1
                    identifiers = record.get("id") or {}
                    name = record.get("name") or {}
                    bio = record.get("bio") or {}
                    bioguide = identifiers.get("bioguide")
                    value = full_name(name)
                    birthday = parse_date(bio.get("birthday"))
                    if bioguide:
                        cursor.execute(
                            "SELECT executive_id FROM executives WHERE bioguide_id = %s",
                            (bioguide,),
                        )
                    else:
                        # Thirteen historical executive records have no Bioguide ID.
                        # Name plus birth date supplies a stable idempotent fallback.
                        cursor.execute(
                            """
                            SELECT executive_id FROM executives
                            WHERE full_name = %s AND date_of_birth IS NOT DISTINCT FROM %s
                            """,
                            (value, birthday),
                        )
                    existing = cursor.fetchone()
                    if existing:
                        executive_id = existing[0]
                        cursor.execute(
                            """
                            UPDATE executives SET
                                full_name = %s, first_name = %s, middle_name = %s,
                                last_name = %s, suffix = %s, nickname = %s,
                                official_full_name = %s, date_of_birth = %s,
                                gender = %s, source_snapshot_id = %s
                            WHERE executive_id = %s
                            """,
                            (value, name.get("first"), name.get("middle"), name.get("last"),
                             name.get("suffix"), name.get("nickname"),
                             name.get("official_full"), birthday, bio.get("gender"),
                             snapshot_id, executive_id),
                        )
                        counts.updated += 1
                    else:
                        cursor.execute(
                            """
                            INSERT INTO executives (
                                full_name, first_name, middle_name, last_name,
                                suffix, nickname, official_full_name, date_of_birth,
                                gender, bioguide_id, source_snapshot_id
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            RETURNING executive_id
                            """,
                            (value, name.get("first"), name.get("middle"), name.get("last"),
                             name.get("suffix"), name.get("nickname"),
                             name.get("official_full"), birthday, bio.get("gender"),
                             bioguide, snapshot_id),
                        )
                        executive_id = cursor.fetchone()[0]
                        counts.inserted += 1
                    for kind, raw_value in identifiers.items():
                        for identifier_value in scalar_values(raw_value):
                            cursor.execute(
                                """
                                INSERT INTO executive_identifiers (
                                    executive_id, identifier_type, identifier_value,
                                    is_primary, source_snapshot_id
                                ) VALUES (%s, %s, %s, %s, %s)
                                ON CONFLICT (identifier_type, identifier_value) DO UPDATE SET
                                    executive_id = EXCLUDED.executive_id,
                                    is_primary = executive_identifiers.is_primary
                                        OR EXCLUDED.is_primary,
                                    source_snapshot_id = EXCLUDED.source_snapshot_id
                                RETURNING (xmax = 0)
                                """,
                                (
                                    executive_id, str(kind), identifier_value,
                                    kind == "bioguide", snapshot_id,
                                ),
                            )
                            identifier_inserted = bool(cursor.fetchone()[0])
                            counts.inserted += int(identifier_inserted)
                            counts.updated += int(not identifier_inserted)
                    # The complete executive row, including gender, identifiers,
                    # and succession method, remains preserved in staging.
                    self.stage_member(
                        cursor, import_id, row_number, record, identifiers, None,
                        raw_name=value,
                    )
                    office_sequence: dict[str, int] = {}
                    for term in record.get("terms") or []:
                        office = {"prez": "president", "viceprez": "vice_president"}.get(
                            term.get("type"), str(term.get("type"))
                        )
                        office_sequence[office] = office_sequence.get(office, 0) + 1
                        start = parse_date(term.get("start"))
                        end = parse_date(term.get("end"))
                        if not start or not end:
                            raise ValueError(f"Executive term lacks dates: {term}")
                        cursor.execute(
                            """
                            INSERT INTO executive_terms (
                                executive_id, office, term_start_date, term_end_date,
                                party_code, accession_method, term_number, source_snapshot_id
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (executive_id, office, term_start_date) DO UPDATE SET
                                term_end_date = EXCLUDED.term_end_date,
                                party_code = EXCLUDED.party_code,
                                accession_method = EXCLUDED.accession_method,
                                term_number = EXCLUDED.term_number,
                                source_snapshot_id = EXCLUDED.source_snapshot_id
                            RETURNING (xmax = 0)
                            """,
                            (executive_id, office, start, end,
                             party_code(term.get("party")), term.get("how"),
                             office_sequence[office],
                             snapshot_id),
                        )
                        inserted = bool(cursor.fetchone()[0])
                        counts.inserted += int(inserted)
                        counts.updated += int(not inserted)
                    self.progress(path.name, row_number, len(records), counts)
                self.finish_import(cursor, import_id, counts)
                self.progress(path.name, len(records), len(records), counts, force=True)
        return counts

    def load_file(self, filename: str) -> Counts:
        path = self.source_dir / filename
        if filename == "legislators-historical.yaml":
            return self.load_legislators(path, False)
        if filename == "legislators-current.yaml":
            return self.load_legislators(path, True)
        if filename == "committees-historical.yaml":
            return self.load_committees(path, False)
        if filename == "committees-current.yaml":
            return self.load_committees(path, True)
        if filename == "committee-membership-current.yaml":
            return self.load_memberships(path)
        if filename == "legislators-district-offices.yaml":
            return self.load_district_offices(path)
        if filename == "legislators-social-media.yaml":
            return self.load_social(path)
        if filename == "executive.yaml":
            return self.load_executives(path)
        raise ValueError(f"Unsupported YAML file: {filename}")


def validate_source_directory(
    source_dir: Path,
    selected: Iterable[str],
    parse_contents: bool = True,
) -> dict[str, int]:
    if not source_dir.is_dir():
        raise ValueError(f"Source directory does not exist: {source_dir}")
    discovered = {
        path.name for path in source_dir.glob("*.yaml")
        if not path.stem.endswith("V1")
    }
    unsupported = sorted(discovered - set(SUPPORTED_FILES))
    if unsupported:
        raise ValueError(
            "Unsupported YAML files would be skipped: " + ", ".join(unsupported)
        )
    results: dict[str, int] = {}
    for filename in selected:
        path = source_dir / filename
        if not path.is_file():
            raise ValueError(f"Required source file is missing: {path}")
        if not parse_contents:
            continue
        print(f"Checking {filename} ...", flush=True)
        data = read_yaml(path)
        if filename == "committee-membership-current.yaml":
            if not isinstance(data, dict):
                raise ValueError(f"{filename} must contain a YAML mapping")
            results[filename] = sum(len(rows or []) for rows in data.values())
        else:
            if not isinstance(data, list):
                raise ValueError(f"{filename} must contain a YAML list")
            results[filename] = len(data)
        print(f"  Valid: {results[filename]:,} top-level records", flush=True)
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(
        description="Load congress-legislators YAML data into StockGov PostgreSQL."
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=root / "data" / "raw" / "congress",
        help="Congress YAML directory (default: data/raw/congress under StockGov).",
    )
    parser.add_argument("--database-url", help="Override DATABASE_URL for this run.")
    parser.add_argument(
        "--current-congress",
        type=int,
        default=current_congress(),
        help="Congress number assigned to current committee memberships.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Parse and count all selected YAML files without connecting to PostgreSQL.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=250,
        help="Print database-load progress every N source rows; use 0 to disable.",
    )
    parser.add_argument(
        "--files",
        nargs="+",
        choices=SUPPORTED_FILES,
        help="Load only selected files. The default loads all supported files.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source_dir = args.source_dir.resolve()
    selected = tuple(args.files or SUPPORTED_FILES)
    print(f"Source directory: {source_dir}", flush=True)
    if args.validate_only:
        print("Validating YAML source files ...", flush=True)
    else:
        print(
            f"Preparing to load {len(selected)} YAML files into PostgreSQL "
            f"(progress every {args.progress_every} rows) ...",
            flush=True,
        )
    try:
        validation = validate_source_directory(
            source_dir, selected, parse_contents=args.validate_only
        )
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"Source validation failed: {exc}", file=sys.stderr)
        return 1

    if args.validate_only:
        print(
            f"Validation complete: {sum(validation.values()):,} records "
            f"across {len(validation)} files.",
            flush=True,
        )
        return 0

    load_dotenv(project_root())
    connection = None
    try:
        connection_url = database_url(args.database_url)
        # Do not wrap this connection in ``with psycopg2.connect(...)`` here.
        # Each file loader uses ``with self.connection`` to create one atomic
        # transaction per source file. Entering it here as well would attempt
        # to re-enter the same connection context recursively.
        connection = psycopg2.connect(connection_url)
        loader = CongressLoader(
            connection,
            source_dir,
            args.current_congress,
            progress_every=args.progress_every,
        )
        loader.verify_schema()
        connection.rollback()  # End the read-only schema-check transaction.
        loader.register_companion_sources()
        print("Recorded JSON/CSV companion files in source provenance.", flush=True)
        total = Counts()
        for filename in selected:
            print(f"Loading {filename} ...", flush=True)
            counts = loader.load_file(filename)
            total.add(counts)
            print(
                f"  read={counts.read:,} inserted={counts.inserted:,} "
                f"updated={counts.updated:,} rejected={counts.rejected:,}",
                flush=True,
            )
    except (OSError, ValueError, RuntimeError, yaml.YAMLError, psycopg2.Error) as exc:
        print(f"Congress data load failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if connection is not None and not connection.closed:
            connection.close()

    print(
        "Congress data load complete: "
        f"read={total.read:,} inserted={total.inserted:,} "
        f"updated={total.updated:,} rejected={total.rejected:,}."
    )
    return 2 if total.rejected else 0


if __name__ == "__main__":
    raise SystemExit(main())
