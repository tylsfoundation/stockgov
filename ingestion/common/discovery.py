"""Reusable filesystem discovery, hashing, and duplicate handling."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class DiscoveredDocument:
    path: Path
    content_hash: str
    size_bytes: int


def content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def discover_documents(
    root: Path,
    *,
    extensions: Iterable[str] = (".pdf", ".html", ".htm", ".xml", ".txt"),
    known_hashes: set[str] | None = None,
) -> list[DiscoveredDocument]:
    """Return unprocessed files under ``root``; empty roots return an empty list."""

    if not root.exists() or not root.is_dir():
        return []
    allowed = {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in extensions}
    seen = set(known_hashes or ())
    documents: list[DiscoveredDocument] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in allowed:
            continue
        digest = content_hash(path)
        if digest in seen:
            continue
        seen.add(digest)
        documents.append(DiscoveredDocument(path, digest, path.stat().st_size))
    return documents
