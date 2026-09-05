"""Download the congress-legislators datasets used by StockGov.

The downloader keeps exactly one backup of each existing source file. For
example, ``legislators-current.yaml`` becomes
``legislators-currentV1.yaml`` before its replacement is downloaded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


BASE_URL = "https://unitedstates.github.io/congress-legislators"

DATASETS: dict[str, tuple[str, ...]] = {
    "legislators-current": ("yaml", "json", "csv"),
    "legislators-historical": ("yaml", "json", "csv"),
    "legislators-social-media": ("yaml", "json"),
    "committees-current": ("yaml", "json"),
    "committee-membership-current": ("yaml", "json", "csv"),
    "committees-historical": ("yaml", "json"),
    "legislators-district-offices": ("yaml", "json", "csv"),
    "executive": ("yaml", "json"),
}


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def default_output_directory() -> Path:
    return project_root() / "data" / "raw" / "congress"


def backup_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}V1{path.suffix}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_files() -> list[tuple[str, str]]:
    return [
        (name, extension)
        for name, extensions in DATASETS.items()
        for extension in extensions
    ]


def rotate_backups(output_directory: Path) -> None:
    """Replace old V1 backups with the current source files."""
    for name, extension in source_files():
        current = output_directory / f"{name}.{extension}"
        backup = backup_path(current)

        if backup.exists():
            backup.unlink()
            print(f"Removed old backup: {backup.name}", flush=True)

        if current.exists():
            current.replace(backup)
            print(f"Backed up: {current.name} -> {backup.name}", flush=True)


def download_file(url: str, destination: Path, timeout: int) -> None:
    """Download one file to a temporary path and atomically install it."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "StockGov source downloader/1.0"},
    )
    temporary_name: str | None = None

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status} for {url}")

            content_length = response.headers.get("Content-Length")
            expected_size = int(content_length) if content_length else None

            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{destination.name}.",
                suffix=".download",
                dir=destination.parent,
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                shutil.copyfileobj(response, temporary)

        temporary_path = Path(temporary_name)
        actual_size = temporary_path.stat().st_size
        if actual_size == 0:
            raise RuntimeError(f"Downloaded an empty file from {url}")
        if expected_size is not None and actual_size != expected_size:
            raise RuntimeError(
                f"Incomplete download for {url}: expected {expected_size:,} bytes, "
                f"received {actual_size:,}"
            )

        os.replace(temporary_path, destination)
        temporary_name = None
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Back up and download all StockGov congress source files."
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=default_output_directory(),
        help="Destination directory (default: data/raw/congress).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="HTTP timeout in seconds for each file (default: 120).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output_directory = args.output_directory.resolve()
    output_directory.mkdir(parents=True, exist_ok=True)

    print(f"Destination: {output_directory}", flush=True)
    print(f"Preparing {len(source_files())} source files...", flush=True)
    rotate_backups(output_directory)

    manifest: dict[str, object] = {
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_base_url": BASE_URL,
        "files": [],
    }

    try:
        for position, (name, extension) in enumerate(source_files(), start=1):
            filename = f"{name}.{extension}"
            url = f"{BASE_URL}/{filename}"
            destination = output_directory / filename
            print(
                f"[{position:02d}/{len(source_files()):02d}] Downloading {filename}...",
                flush=True,
            )
            download_file(url, destination, args.timeout)
            details = {
                "filename": filename,
                "url": url,
                "bytes": destination.stat().st_size,
                "sha256": sha256(destination),
            }
            manifest["files"].append(details)  # type: ignore[union-attr]
            print(
                f"    Complete: {details['bytes']:,} bytes",
                flush=True,
            )
    except (OSError, RuntimeError, urllib.error.URLError) as exc:
        print(f"Download failed: {exc}", file=sys.stderr, flush=True)
        print(
            "Existing files already rotated to V1 backups; completed downloads "
            "were left in place so the operation can be diagnosed safely.",
            file=sys.stderr,
            flush=True,
        )
        return 1

    manifest_path = output_directory / "download_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Manifest written: {manifest_path}", flush=True)
    print("All congressional source files downloaded successfully.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
