from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median
from typing import Any

from .models import Candidate, Observation, PathMetric

MIN_OBSERVATIONS = 3
MIN_ENUM_VALUES = 10
MAX_ENUM_MEMBERS = 8
MIN_OPERATIONAL_OBSERVATIONS = 10


def infer_candidates(observations: list[Observation]) -> list[Candidate]:
    if len(observations) < MIN_OBSERVATIONS:
        return []
    drafts: list[dict[str, Any]] = []
    total = len(observations)
    statuses = Counter(item.status for item in observations)
    if len(statuses) == 1:
        status = next(iter(statuses))
        drafts.append(
            _draft(
                "status",
                "$status",
                status,
                total,
                total,
                "breaking",
                f"status {status} in {total}/{total} observations",
            )
        )
    content_types = Counter(item.content_type for item in observations)
    if len(content_types) == 1:
        content_type = next(iter(content_types))
        drafts.append(
            _draft(
                "content_type",
                "$content-type",
                content_type,
                total,
                total,
                "warning",
                f"content type {content_type or '(missing)'} in {total}/{total} observations",
            )
        )
    if total >= MIN_OPERATIONAL_OBSERVATIONS:
        maximum_latency = max(item.latency_ms for item in observations)
        latency_limit = max(1000.0, maximum_latency * 4)
        drafts.append(
            _draft(
                "latency",
                "$latency",
                {"warn_above_ms": round(latency_limit, 1)},
                total,
                total,
                "warning",
                f"warn above {latency_limit:.1f} ms (4× observed maximum, minimum 1000 ms)",
            )
        )
        maximum_size = max(item.response_bytes for item in observations)
        size_limit = max(1024 * 1024, maximum_size * 4)
        drafts.append(
            _draft(
                "response_size",
                "$response-bytes",
                {"warn_above_bytes": size_limit},
                total,
                total,
                "warning",
                f"warn above {size_limit} bytes (4× observed maximum, minimum 1 MiB)",
            )
        )

    by_path: dict[str, list[tuple[Observation, PathMetric]]] = defaultdict(list)
    for observation in observations:
        for metric in observation.paths:
            by_path[metric.path].append((observation, metric))
    for path in sorted(by_path):
        entries = by_path[path]
        metrics = [metric for _, metric in entries]
        present = len(metrics)
        if present == total and all(
            _present_for_every_array_item(observation, metric) for observation, metric in entries
        ):
            drafts.append(
                _draft(
                    "required",
                    path,
                    True,
                    present,
                    total,
                    "breaking",
                    f"present in {present}/{total} observations",
                )
            )
        non_null_types = [kind for metric in metrics for kind in metric.types if kind != "null"]
        type_counts = Counter(non_null_types)
        if non_null_types and len(type_counts) == 1:
            kind = next(iter(type_counts))
            drafts.append(
                _draft(
                    "type",
                    path,
                    kind,
                    len(non_null_types),
                    len(non_null_types),
                    "breaking",
                    f"{kind} in {len(non_null_types)}/{len(non_null_types)} non-null values",
                )
            )
        null_count = sum("null" in metric.types for metric in metrics)
        if present == total and null_count == 0:
            drafts.append(
                _draft(
                    "non_null",
                    path,
                    True,
                    total,
                    total,
                    "warning",
                    f"null in 0/{total} observations",
                )
            )
        string_values = [value for metric in metrics for value in metric.values]
        unique = sorted(set(string_values))
        if (
            len(string_values) >= MIN_ENUM_VALUES
            and 0 < len(unique) <= MAX_ENUM_MEMBERS
            and all(len(value) <= 64 for value in unique)
            and len(unique) / len(string_values) <= 0.5
        ):
            drafts.append(
                _draft(
                    "enum",
                    path,
                    unique,
                    len(string_values),
                    len(string_values),
                    "warning",
                    f"{len(string_values)} string values; {len(unique)} unique",
                )
            )
        lengths = [metric.array_length for metric in metrics if metric.array_length is not None]
        if len(lengths) == total:
            numeric = [int(value) for value in lengths]
            expected = {
                "min": min(numeric),
                "max": max(numeric),
                "median": median(numeric),
                "always_nonempty": min(numeric) > 0,
            }
            drafts.append(
                _draft(
                    "cardinality",
                    path,
                    expected,
                    total,
                    total,
                    "warning",
                    f"min {min(numeric)}, max {max(numeric)}, "
                    f"median {median(numeric):g} across {total} observations",
                )
            )
    ordered = sorted(drafts, key=lambda item: (item["path"], item["rule"]))
    return [Candidate(id=f"c{index:03d}", **draft) for index, draft in enumerate(ordered, 1)]


def _present_for_every_array_item(observation: Observation, metric: PathMetric) -> bool:
    """Avoid marking a field required when it is optional within an array of objects."""
    if "[]" not in metric.path or metric.path.endswith("[]"):
        return True
    array_path = metric.path.rsplit("[]", 1)[0] + "[]"
    parent = next((item for item in observation.paths if item.path == array_path), None)
    if parent is None or parent.array_length is None:
        return True
    return metric.occurrences >= parent.array_length


def _draft(
    rule: str,
    path: str,
    expected: Any,
    supporting: int,
    total: int,
    severity: str,
    explanation: str,
) -> dict[str, Any]:
    return {
        "rule": rule,
        "path": path,
        "expected": expected,
        "observations": supporting,
        "confidence": supporting / total,
        "severity": severity,
        "explanation": explanation,
    }
