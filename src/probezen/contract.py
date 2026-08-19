from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import Candidate


class ContractError(Exception):
    pass


SUPPORTED_RULES = {
    "cardinality",
    "content_type",
    "enum",
    "latency",
    "non_null",
    "required",
    "response_size",
    "status",
    "type",
}


def lock_path(root: Path) -> Path:
    return root / "probezen.lock.json"


def load_lock(root: Path) -> dict[str, Any]:
    path = lock_path(root)
    if not path.exists():
        return {"version": 1, "contracts": {}}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"Could not read probezen.lock.json: {exc}") from exc
    if (
        not isinstance(data, dict)
        or data.get("version") != 1
        or not isinstance(data.get("contracts"), dict)
    ):
        raise ContractError("probezen.lock.json must contain version 1 and contracts")
    return data


def save_contract(
    root: Path, name: str, candidates: list[Candidate], *, sample_count: int | None = None
) -> None:
    data = load_lock(root)
    data["contracts"][name] = {
        "learned_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "learned_from_observations": sample_count,
        "rules": [
            candidate.to_dict() for candidate in sorted(candidates, key=lambda item: item.id)
        ],
    }
    lock_path(root).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def load_rules(root: Path, name: str) -> list[dict[str, Any]]:
    contract = load_lock(root)["contracts"].get(name)
    if not isinstance(contract, dict) or not isinstance(contract.get("rules"), list):
        raise ContractError(
            f"No approved contract for '{name}'; run 'probezen approve {name} --all'"
        )
    rules = list(contract["rules"])
    for index, rule in enumerate(rules, 1):
        if not isinstance(rule, dict):
            raise ContractError(f"Contract '{name}' rule {index} must be a mapping")
        kind = rule.get("rule")
        path = rule.get("path")
        if kind not in SUPPORTED_RULES:
            raise ContractError(f"Contract '{name}' rule {index} has unsupported kind '{kind}'")
        if not isinstance(path, str) or not path:
            raise ContractError(f"Contract '{name}' rule {index} must have a nonempty path")
        if "expected" not in rule:
            raise ContractError(f"Contract '{name}' rule {index} is missing expected behavior")
        expected = rule["expected"]
        if kind == "enum" and (
            not isinstance(expected, list) or not all(isinstance(item, str) for item in expected)
        ):
            raise ContractError(f"Contract '{name}' enum rule {index} must contain strings")
        if kind in {"cardinality", "latency", "response_size"} and not isinstance(expected, dict):
            raise ContractError(f"Contract '{name}' {kind} rule {index} must contain a mapping")
        if kind == "cardinality" and not isinstance(expected.get("always_nonempty"), bool):
            raise ContractError(
                f"Contract '{name}' cardinality rule {index} must define always_nonempty"
            )
        if kind == "latency" and (
            not isinstance(expected.get("warn_above_ms"), int | float)
            or isinstance(expected.get("warn_above_ms"), bool)
        ):
            raise ContractError(f"Contract '{name}' latency rule {index} must define warn_above_ms")
        if kind == "response_size" and (
            not isinstance(expected.get("warn_above_bytes"), int)
            or isinstance(expected.get("warn_above_bytes"), bool)
        ):
            raise ContractError(
                f"Contract '{name}' response_size rule {index} must define warn_above_bytes"
            )
        if kind == "status" and (not isinstance(expected, int) or isinstance(expected, bool)):
            raise ContractError(f"Contract '{name}' status rule {index} must contain an integer")
        if kind in {"content_type", "type"} and not isinstance(expected, str):
            raise ContractError(f"Contract '{name}' {kind} rule {index} must contain a string")
        if kind in {"non_null", "required"} and not isinstance(expected, bool):
            raise ContractError(f"Contract '{name}' {kind} rule {index} must contain a boolean")
        observations = rule.get("observations", 0)
        confidence = rule.get("confidence", 0)
        explanation = rule.get("explanation", "")
        severity = rule.get("severity", "breaking")
        if not isinstance(observations, int) or isinstance(observations, bool) or observations < 0:
            raise ContractError(f"Contract '{name}' rule {index} has invalid observation evidence")
        if (
            not isinstance(confidence, int | float)
            or isinstance(confidence, bool)
            or not 0 <= confidence <= 1
        ):
            raise ContractError(f"Contract '{name}' rule {index} has invalid confidence")
        if not isinstance(explanation, str):
            raise ContractError(f"Contract '{name}' rule {index} has invalid explanation")
        if severity not in {"breaking", "warning", "info"}:
            raise ContractError(f"Contract '{name}' rule {index} has invalid severity")
    return rules
