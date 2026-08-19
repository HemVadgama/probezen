from pathlib import Path

from probezen.benchmark import run_benchmark

ROOT = Path(__file__).parents[1]


def test_drift_benchmark_has_no_false_results():
    result = run_benchmark(ROOT / "benchmarks")
    assert (result.breaking_detected, result.breaking_total) == (3, 3)
    assert (result.warning_detected, result.warning_total) == (2, 2)
    assert (result.stable_passed, result.stable_total) == (3, 3)
    assert result.unsupported_total == 1
    assert result.false_negatives == ()
    assert result.false_positives == ()
