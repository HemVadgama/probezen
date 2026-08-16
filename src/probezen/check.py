from __future__ import annotations

from typing import Any

from .models import Finding, Observation


def enforce(
    observation: Observation,
    rules: list[dict[str, Any]],
    expected_status: int | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    metrics = {metric.path: metric for metric in observation.paths}
    if expected_status is not None and observation.status != expected_status:
        findings.append(
            Finding(
                "breaking",
                "status_change",
                "$status",
                expected_status,
                observation.status,
            )
        )
    if not observation.is_json and any(
        rule["path"] not in {"$status", "$content-type"} for rule in rules
    ):
        findings.append(
            Finding(
                "breaking",
                "json_expected",
                "$response",
                "JSON",
                observation.content_type or "non-JSON",
                "Expected a JSON response",
            )
        )
    for rule in rules:
        kind = rule["rule"]
        path = rule["path"]
        expected = rule["expected"]
        metric = metrics.get(path)
        if (
            kind == "status"
            and observation.status != expected
            and not any(item.kind == "status_change" for item in findings)
        ):
            findings.append(
                Finding("breaking", "status_change", path, expected, observation.status)
            )
        elif kind == "content_type" and observation.content_type != expected:
            findings.append(
                Finding("warning", "content_type_change", path, expected, observation.content_type)
            )
        elif kind == "required" and metric is None:
            findings.append(Finding("breaking", "missing_required", path, "required", "missing"))
        elif kind == "type" and metric is not None:
            actual = sorted(value for value in metric.types if value != "null")
            if actual and actual != [expected]:
                findings.append(
                    Finding(
                        "breaking",
                        "type_change",
                        path,
                        expected,
                        actual[0] if len(actual) == 1 else actual,
                    )
                )
        elif kind == "non_null" and metric is not None and "null" in metric.types:
            findings.append(Finding("warning", "nullability_change", path, "non-null", "null"))
        elif kind == "enum" and metric is not None:
            unexpected = sorted(set(metric.values) - set(expected))
            if unexpected:
                findings.append(Finding("warning", "enum_expansion", path, expected, unexpected))
        elif (
            kind == "cardinality"
            and metric is not None
            and metric.array_length is not None
            and expected.get("always_nonempty")
            and metric.array_length == 0
        ):
            findings.append(Finding("warning", "empty_array", path, "nonempty", 0))
    return sorted(findings, key=lambda item: (item.severity, item.path, item.kind))
