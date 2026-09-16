"""Shared database connection and document-state helpers."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import quote, urlunsplit


def database_url(override: str | None = None) -> str:
    if override:
        return override
    value = os.getenv("DATABASE_URL")
    if value:
        return value
    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    if user and password:
        authority = (
            f"{quote(user, safe='')}:{quote(password, safe='')}"
            f"@{os.getenv('POSTGRES_HOST', 'localhost')}:{os.getenv('POSTGRES_PORT', '5433')}"
        )
        return urlunsplit(("postgresql", authority, "/" + os.getenv("POSTGRES_DB", "congress_trades"), "", ""))
    raise RuntimeError("DATABASE_URL or POSTGRES_USER/POSTGRES_PASSWORD is not configured")


@contextmanager
def connect(url: str | None = None) -> Iterator[Any]:
    try:
        import psycopg2
    except ImportError as exc:  # pragma: no cover - runtime dependency
        raise RuntimeError("psycopg2-binary is required for database persistence") from exc
    connection = psycopg2.connect(database_url(url))
    try:
        yield connection
    finally:
        connection.close()


def set_document_status(cursor: Any, document_id: int, status: str) -> None:
    cursor.execute(
        "UPDATE documents SET document_completeness_status=%s WHERE document_id=%s",
        (status, document_id),
    )


def move_after_commit(path: Any, destination: Any, *, dry_run: bool = False) -> None:
    """Move a source only after the caller's DB transaction has committed."""

    if dry_run:
        return
    from pathlib import Path
    source = Path(path)
    target = Path(destination) / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    source.replace(target)
