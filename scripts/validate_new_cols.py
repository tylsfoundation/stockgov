"""Validate required StockGov schema columns.

This script is read-only. It connects to the existing PostgreSQL database
and verifies that expected schema columns exist.

Exit codes:
    0 = all required columns exist
    1 = one or more required columns are missing
    2 = configuration or database connection error
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import quote

import psycopg2


# Columns introduced by the current create_database.py migration section.
REQUIRED_COLUMNS = {
    "staging_house_filings": {
        "prefix_raw",
        "suffix_raw",
        "state_district_raw",
    },
}


def load_dotenv_if_available() -> None:
    """Load the project .env file if python-dotenv is installed."""

    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    project_root = Path(__file__).resolve().parent.parent
    load_dotenv(project_root / ".env")


def get_database_url() -> str:
    """Build the PostgreSQL connection URL using project configuration."""

    database_url = os.getenv("DATABASE_URL")

    if database_url:
        return database_url

    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5433")
    database = os.getenv("POSTGRES_DB", "congress_trades")
    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")

    if not user or not password:
        raise ValueError(
            "Set POSTGRES_USER and POSTGRES_PASSWORD in the project .env file"
        )

    return (
        f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}"
        f"@{host}:{port}/{database}"
    )


def get_table_columns(connection, table_name: str) -> set[str]:
    """Return all column names for a table in the public schema."""

    query = """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = %s
        ORDER BY ordinal_position;
    """

    with connection.cursor() as cursor:
        cursor.execute(query, (table_name,))
        return {row[0] for row in cursor.fetchall()}


def validate_columns(connection) -> bool:
    """Validate all required tables and columns."""

    all_valid = True

    print("=" * 70)
    print("StockGov Schema Column Validation")
    print("=" * 70)

    for table_name, required_columns in REQUIRED_COLUMNS.items():
        actual_columns = get_table_columns(connection, table_name)

        print(f"\nTable: {table_name}")

        if not actual_columns:
            print("  FAIL: Table does not exist.")
            all_valid = False
            continue

        for column_name in sorted(required_columns):
            if column_name in actual_columns:
                print(f"  PASS: {column_name}")
            else:
                print(f"  FAIL: {column_name} is missing")
                all_valid = False

    print("\n" + "=" * 70)

    if all_valid:
        print("RESULT: PASS - All required schema columns are present.")
    else:
        print("RESULT: FAIL - One or more required schema columns are missing.")

    print("=" * 70)

    return all_valid


def main() -> int:
    load_dotenv_if_available()

    try:
        database_url = get_database_url()

        with psycopg2.connect(database_url) as connection:
            valid = validate_columns(connection)

        return 0 if valid else 1

    except (ValueError, psycopg2.Error) as exc:
        print(f"Schema validation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())