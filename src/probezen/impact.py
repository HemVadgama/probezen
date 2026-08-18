from __future__ import annotations

from dataclasses import replace

from .dependencies import Dependency, Usage
from .models import Finding


def explain_impact(finding: Finding, dependency: Dependency | None) -> Finding:
    """Attach deterministic, code-aware severity without changing contract semantics."""
    matches = _matching_usages(finding.path, dependency.usages if dependency else [])
    relevant = [usage for usage in matches if _usage_matches_change(usage, finding.kind)]
    if relevant:
        unguarded = [usage for usage in relevant if not usage.guarded]
        selected = unguarded or relevant
        level = "high" if unguarded else "medium"
        reason = selected[0].reason.capitalize() + "."
        confidence = "high" if unguarded else "medium"
    elif finding.severity == "breaking":
        selected = []
        level = "high"
        reason = "The current response violates an explicitly approved expectation."
        confidence = "high"
    else:
        selected = []
        level = "medium"
        reason = "The response differs from approved behavior; no direct code assumption was found."
        confidence = "medium"
    return replace(
        finding,
        level=level,  # type: ignore[arg-type]
        confidence=confidence,  # type: ignore[arg-type]
        affected_code=tuple(usage.to_dict() for usage in selected[:10]),
        reason=reason,
        suggested_action=_suggestion(finding),
    )


def _matching_usages(path: str, usages: list[Usage]) -> list[Usage]:
    normalized = path.replace("[]", "").lower()
    leaf = normalized.rsplit(".", 1)[-1]
    return [
        usage
        for usage in usages
        if usage.field.lower() == normalized
        or usage.field.lower().endswith(f".{normalized}")
        or usage.field.lower().rsplit(".", 1)[-1] == leaf
    ]


def _usage_matches_change(usage: Usage, kind: str) -> bool:
    return (
        kind == "type_change"
        and usage.kind in {"numeric", "string_method"}
        or kind == "nullability_change"
        and usage.kind == "string_method"
        or kind == "empty_array"
        and usage.kind == "array_index"
        or kind == "enum_expansion"
        and usage.kind == "enum"
        or kind == "missing_required"
    )


def _suggestion(finding: Finding) -> str:
    return {
        "type_change": (
            "Update or guard the consuming code, or verify the provider's response contract."
        ),
        "nullability_change": (
            "Handle null before using this field, then verify the provider change."
        ),
        "empty_array": "Handle an empty collection before indexing it.",
        "enum_expansion": "Handle the new value explicitly or add a safe default branch.",
        "missing_required": "Guard the missing field or verify whether the provider removed it.",
        "status_change": "Inspect provider status behavior, authentication, and rate limits.",
    }.get(finding.kind, "Inspect the dependency response and affected application code.")
