"""Generic document-processing configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv() -> None:
    path = Path(__file__).resolve().parents[2] / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc


@dataclass(frozen=True)
class ProcessingConfig:
    database_url: str | None
    document_root: Path
    incoming_directory: Path | None
    processed_directory: Path | None
    review_directory: Path | None
    max_retry_attempts: int = 2
    stale_job_timeout_seconds: int = 3600
    expected_accuracy: float = 0.95
    current_expected_accuracy: float = 0.99
    historical_expected_accuracy: float = 0.95
    debug: bool = False
    dry_run: bool = False

    @classmethod
    def from_environment(cls) -> "ProcessingConfig":
        load_dotenv()
        root = Path(os.getenv("DOCUMENT_ROOT", "data/raw")).expanduser()
        def optional_path(name: str) -> Path | None:
            value = os.getenv(name)
            return Path(value).expanduser() if value else None
        try:
            retries = int(os.getenv("MAX_RETRY_ATTEMPTS", "2"))
        except ValueError as exc:
            raise ValueError("MAX_RETRY_ATTEMPTS must be an integer") from exc
        if retries < 1:
            raise ValueError("MAX_RETRY_ATTEMPTS must be positive")
        try:
            stale_timeout = int(os.getenv("STALE_JOB_TIMEOUT_SECONDS", "3600"))
        except ValueError as exc:
            raise ValueError("STALE_JOB_TIMEOUT_SECONDS must be an integer") from exc
        if stale_timeout < 1:
            raise ValueError("STALE_JOB_TIMEOUT_SECONDS must be positive")
        return cls(
            database_url=os.getenv("DATABASE_URL"),
            document_root=root,
            incoming_directory=optional_path("INCOMING_DIRECTORY"),
            processed_directory=optional_path("PROCESSED_DIRECTORY"),
            review_directory=optional_path("REVIEW_DIRECTORY"),
            max_retry_attempts=retries,
            stale_job_timeout_seconds=stale_timeout,
            expected_accuracy=_float("EXPECTED_ACCURACY", 0.95),
            current_expected_accuracy=_float("CURRENT_EXPECTED_ACCURACY", 0.99),
            historical_expected_accuracy=_float("HISTORICAL_EXPECTED_ACCURACY", 0.95),
            debug=_bool("GLOBAL_DEBUG"),
            dry_run=_bool("GLOBAL_DRY_RUN"),
        )
