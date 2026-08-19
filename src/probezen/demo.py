from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .check import enforce
from .infer import infer_candidates
from .models import Candidate, Finding, Observation
from .paths import traverse

NORMAL_RESPONSE = {
    "user": {
        "email": "user@example.com",
        "id": 123,
        "plan": "pro",
    }
}

CHANGED_RESPONSE = {
    "user": {
        "id": "123",
        "plan": "pro",
    }
}


@dataclass(frozen=True)
class DemoResult:
    observations: int
    candidates: tuple[Candidate, ...]
    current: Observation
    findings: tuple[Finding, ...]


def run_demo() -> DemoResult:
    """Exercise the real inference and enforcement engine with deterministic responses."""
    baseline = [_observation(NORMAL_RESPONSE) for _ in range(10)]
    candidates = infer_candidates(baseline)
    current = _observation(CHANGED_RESPONSE)
    findings = enforce(
        current,
        [candidate.to_dict() for candidate in candidates],
        expected_status=200,
    )
    return DemoResult(len(baseline), tuple(candidates), current, tuple(findings))


def _observation(body: Any) -> Observation:
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return Observation(
        status=200,
        content_type="application/json",
        latency_ms=25.0,
        response_bytes=len(encoded),
        is_json=True,
        paths=traverse(body),
    )
