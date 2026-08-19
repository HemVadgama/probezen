from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .check import enforce
from .infer import infer_candidates
from .models import Observation
from .paths import traverse


@dataclass(frozen=True)
class BenchmarkResult:
    breaking_total: int
    breaking_detected: int
    warning_total: int
    warning_detected: int
    stable_total: int
    stable_passed: int
    unsupported_total: int
    false_negatives: tuple[str, ...]
    false_positives: tuple[str, ...]

    @property
    def successful(self) -> bool:
        return not self.false_negatives and not self.false_positives


def run_benchmark(root: Path) -> BenchmarkResult:
    manifest = json.loads((root / "cases.json").read_text())
    totals = {"breaking": 0, "warning": 0, "stable": 0, "unsupported": 0}
    passed = {"breaking": 0, "warning": 0, "stable": 0}
    false_negatives: list[str] = []
    false_positives: list[str] = []
    for case in manifest["synthetic_cases"]:
        classification = case["classification"]
        totals[classification] += 1
        before = json.loads((root / case["before"]).read_text())["observations"]
        after = json.loads((root / case["after"]).read_text())
        candidates = infer_candidates([_observation(item) for item in before])
        findings = enforce(_observation(after), [item.to_dict() for item in candidates], 200)
        kinds = {item.kind for item in findings}
        expected = set(case.get("expected_kinds", []))
        if classification in {"breaking", "warning"}:
            if expected <= kinds:
                passed[classification] += 1
            else:
                false_negatives.append(case["id"])
        elif classification == "stable":
            if findings:
                false_positives.append(case["id"])
            else:
                passed["stable"] += 1
    return BenchmarkResult(
        breaking_total=totals["breaking"],
        breaking_detected=passed["breaking"],
        warning_total=totals["warning"],
        warning_detected=passed["warning"],
        stable_total=totals["stable"],
        stable_passed=passed["stable"],
        unsupported_total=totals["unsupported"],
        false_negatives=tuple(false_negatives),
        false_positives=tuple(false_positives),
    )


def print_benchmark(result: BenchmarkResult) -> None:
    print("Probezen drift benchmark\n")
    print(f"Breaking cases detected:     {result.breaking_detected} / {result.breaking_total}")
    print(f"Warning drift detected:      {result.warning_detected} / {result.warning_total}")
    print(f"Stable cases correctly pass: {result.stable_passed} / {result.stable_total}")
    print(f"Intentionally unsupported:   {result.unsupported_total}")
    print(f"\nFalse negatives: {len(result.false_negatives)}")
    for identifier in result.false_negatives:
        print(f"  - {identifier}")
    print(f"False positives: {len(result.false_positives)}")
    for identifier in result.false_positives:
        print(f"  - {identifier}")


def _observation(body: Any) -> Observation:
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return Observation(200, "application/json", 10.0, len(encoded), True, traverse(body))


def main() -> None:
    root = Path.cwd() / "benchmarks"
    result = run_benchmark(root)
    print_benchmark(result)
    if not result.successful:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
