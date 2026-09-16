"""Immutable, content-addressed text artifacts shared by document processors."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


def content_hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def artifact_path(source_path: Path, extractor_name: str, extractor_version: str, output_hash: str) -> Path:
    """Return a stable path whose name includes the exact output identity."""

    extractor = re.sub(r"[^A-Za-z0-9_.-]+", "_", extractor_name)
    version = re.sub(r"[^A-Za-z0-9_.-]+", "_", extractor_version)
    return source_path.with_name(f"{source_path.stem}.{extractor}-{version}.{output_hash}.txt")


def write_immutable_text(path: Path, text: str) -> tuple[Path, str, int]:
    """Write text once and verify existing content before reusing it.

    A pre-existing path is never replaced.  This makes a database row's
    output_path safe to resolve even if a later extraction produces different
    bytes or a database transaction fails after the file is written.
    """

    data = text.encode("utf-8", errors="replace")
    output_hash = content_hash(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_bytes()
        if existing != data:
            raise ValueError(f"immutable extraction artifact differs: {path}")
    else:
        partial = path.with_suffix(path.suffix + ".part")
        with partial.open("wb") as stream:
            stream.write(data)
        partial.replace(path)
    return path, output_hash, len(data)
