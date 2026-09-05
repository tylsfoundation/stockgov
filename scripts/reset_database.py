"""Remove StockGov table data or drop the StockGov tables.

This utility is intentionally interactive. It performs no database operation
until the user supplies a valid action and answers the confirmation prompt.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ACTIONS = ("drop", "delete")

# Dependency order is unnecessary for TRUNCATE/DROP because CASCADE is explicit,
# but keeping the complete allowlist ensures unrelated public tables are ignored.
STOCKGOV_TABLES = (
    "staging_senate_trades",
    "staging_house_trades",
    "staging_senate_filings",
    "staging_house_filings",
    "staging_committee_memberships",
    "staging_committees",
    "staging_members",
    "corporate_actions",
    "market_prices",
    "trade_evidence",
    "trades",
    "security_identifiers",
    "securities",
    "document_extractions",
    "document_jobs",
    "documents",
    "filing_selections",
    "selection_batches",
    "member_match_candidates",
    "filings",
    "executive_terms",
    "executive_identifiers",
    "executives",
    "committee_memberships",
    "committee_congresses",
    "committee_identifiers",
    "committees",
    "member_social_accounts",
    "member_offices",
    "leadership_roles",
    "member_term_party_affiliations",
    "member_terms",
    "member_family_relationships",
    "member_identifiers",
    "member_names",
    "members",
    "source_imports",
    "source_snapshots",
)


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_dotenv() -> None:
    path = project_root() / ".env"
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
    load_dotenv()
    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError(
            "DATABASE_URL is not set. Add it to C:\\Home\\StockGov\\.env "
            "or supply --database-url."
        )
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Drop StockGov tables or delete all rows while preserving tables."
    )
    parser.add_argument("action", nargs="?", help="Required action: drop or delete")
    parser.add_argument(
        "--database-url",
        help="PostgreSQL connection URL; defaults to DATABASE_URL from .env.",
    )
    return parser.parse_args(argv)


def confirm(action: str) -> bool:
    effect = (
        "DROP every StockGov table and its stored data"
        if action == "drop"
        else "DELETE all rows from every StockGov table and reset identity values"
    )
    answer = input(f"{effect}. Proceed? [y/n]: ").strip().casefold()
    return answer in {"y", "yes"}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    action = (args.action or "").strip().casefold()
    if action not in ACTIONS:
        print("Available actions:")
        print("  drop   - remove all StockGov tables and their data")
        print("  delete - remove all rows but preserve the table structure")
        return 2

    if not confirm(action):
        print("Cancelled. The database was not changed.")
        return 0

    try:
        import psycopg2
        from psycopg2 import sql
    except ImportError as exc:
        raise RuntimeError(
            "The psycopg2-binary package is required. Install it with: "
            "py -m pip install psycopg2-binary"
        ) from exc

    statement = (
        sql.SQL("DROP TABLE IF EXISTS {} CASCADE")
        if action == "drop"
        else sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE")
    )
    table_list = sql.SQL(", ").join(sql.Identifier(name) for name in STOCKGOV_TABLES)

    with psycopg2.connect(database_url(args.database_url)) as connection:
        with connection.cursor() as cursor:
            cursor.execute(statement.format(table_list))

    if action == "drop":
        print(f"Dropped {len(STOCKGOV_TABLES)} StockGov tables.")
    else:
        print(f"Deleted all rows from {len(STOCKGOV_TABLES)} StockGov tables.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
