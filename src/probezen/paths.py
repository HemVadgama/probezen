from __future__ import annotations

from collections import defaultdict
from typing import Any

from .models import JsonType, PathMetric

SENSITIVE_NAMES = {
    "access_token",
    "api_key",
    "authorization",
    "cookie",
    "credit_card",
    "password",
    "refresh_token",
    "secret",
    "token",
}


def json_type(value: Any) -> JsonType:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def traverse(value: Any, sensitive_paths: tuple[str, ...] = ()) -> tuple[PathMetric, ...]:
    """Aggregate arbitrary JSON by stable, index-free paths."""
    types: dict[str, list[JsonType]] = defaultdict(list)
    values: dict[str, list[str]] = defaultdict(list)
    lengths: dict[str, int] = {}
    occurrences: dict[str, int] = defaultdict(int)

    def visit(item: Any, path: str) -> None:
        kind = json_type(item)
        if path:
            types[path].append(kind)
            occurrences[path] += 1
        if isinstance(item, dict):
            for key in sorted(item):
                child = f"{path}.{key}" if path else key
                visit(item[key], child)
        elif isinstance(item, list):
            array_path = f"{path}[]" if path else "[]"
            lengths[array_path] = len(item)
            for child in item:
                visit(child, array_path)
        elif path and isinstance(item, str) and not _sensitive(path, sensitive_paths):
            values[path].append(item)

    visit(value, "")
    all_paths = sorted(set(types) | set(lengths))
    return tuple(
        PathMetric(
            path=path,
            types=tuple(sorted(set(types[path]))),
            values=tuple(sorted(set(values[path]))),
            array_length=lengths.get(path),
            occurrences=occurrences[path],
        )
        for path in all_paths
    )


def _sensitive(path: str, configured: tuple[str, ...]) -> bool:
    normalized = path.lower().replace("[]", "").replace("-", "_")
    leaf = normalized.rsplit(".", 1)[-1]
    return leaf in SENSITIVE_NAMES or any(
        normalized == item.lower().replace("[]", "").replace("-", "_") for item in configured
    )
