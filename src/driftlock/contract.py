from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Candidate


class ContractError(Exception):
    pass


def lock_path(root: Path) -> Path:
    return root / "driftlock.lock.json"


def load_lock(root: Path) -> dict[str, Any]:
    path = lock_path(root)
    if not path.exists():
        return {"version": 1, "contracts": {}}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"Could not read driftlock.lock.json: {exc}") from exc
    if (
        not isinstance(data, dict)
        or data.get("version") != 1
        or not isinstance(data.get("contracts"), dict)
    ):
        raise ContractError("driftlock.lock.json must contain version 1 and contracts")
    return data


def save_contract(root: Path, name: str, candidates: list[Candidate]) -> None:
    data = load_lock(root)
    data["contracts"][name] = {
        "rules": [candidate.to_dict() for candidate in sorted(candidates, key=lambda item: item.id)]
    }
    lock_path(root).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def load_rules(root: Path, name: str) -> list[dict[str, Any]]:
    contract = load_lock(root)["contracts"].get(name)
    if not isinstance(contract, dict) or not isinstance(contract.get("rules"), list):
        raise ContractError(
            f"No approved contract for '{name}'; run 'driftlock approve {name} --all'"
        )
    return list(contract["rules"])
