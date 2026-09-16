"""Shared validation mechanics; document processors supply domain rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


def validate_required(record: object, fields: Iterable[str]) -> ValidationResult:
    result = ValidationResult()
    for field in fields:
        value = getattr(record, field, None) if not isinstance(record, dict) else record.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            result.errors.append(f"missing required field: {field}")
    return result


def duplicate_keys(records: Sequence[object], key_fields: Iterable[str]) -> list[tuple[object, ...]]:
    fields = tuple(key_fields)
    seen: set[tuple[object, ...]] = set()
    duplicates: list[tuple[object, ...]] = []
    for record in records:
        key = tuple(record.get(field) if isinstance(record, dict) else getattr(record, field, None) for field in fields)
        if key in seen:
            duplicates.append(key)
        seen.add(key)
    return duplicates


def confidence_meets_target(confidence: float | None, target: float) -> bool:
    return confidence is not None and confidence >= target
