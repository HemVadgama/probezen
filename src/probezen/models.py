from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

JsonType = Literal["string", "integer", "number", "boolean", "object", "array", "null"]
Severity = Literal["info", "warning", "breaking"]


@dataclass(frozen=True)
class PathMetric:
    path: str
    types: tuple[JsonType, ...]
    values: tuple[str, ...] = ()
    array_length: int | None = None
    occurrences: int = 1


@dataclass(frozen=True)
class Observation:
    status: int
    content_type: str
    latency_ms: float
    response_bytes: int
    is_json: bool
    paths: tuple[PathMetric, ...]


@dataclass(frozen=True)
class Candidate:
    id: str
    rule: str
    path: str
    expected: Any
    observations: int
    confidence: float
    explanation: str
    severity: Severity

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Finding:
    severity: Severity
    kind: str
    path: str
    expected: Any
    actual: Any
    message: str = field(default="")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
