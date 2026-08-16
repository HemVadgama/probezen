from driftlock.check import enforce
from driftlock.infer import infer_candidates
from driftlock.models import Observation
from driftlock.paths import traverse


def observation(body, *, status=200, content_type="application/json"):
    return Observation(
        status, content_type, 1.0, 100, content_type == "application/json", traverse(body)
    )


def rules_for(observations):
    return [item.to_dict() for item in infer_candidates(observations)]


def test_insufficient_evidence_returns_no_candidates():
    assert infer_candidates([observation({"id": 1})] * 2) == []


def test_infers_stable_required_type_nullability_enum_and_cardinality():
    observations = [observation({"items": [{"id": i, "status": "active"}]}) for i in range(10)]
    candidates = infer_candidates(observations)
    pairs = {(item.rule, item.path) for item in candidates}
    assert ("required", "items[].id") in pairs
    assert ("type", "items[].id") in pairs
    assert ("non_null", "items[].id") in pairs
    assert ("enum", "items[].status") in pairs
    cardinality = next(
        item for item in candidates if item.rule == "cardinality" and item.path == "items[]"
    )
    assert cardinality.expected["always_nonempty"] is True
    assert all(item.id == f"c{index:03d}" for index, item in enumerate(candidates, 1))


def test_enum_cutoff_rejects_identifiers_and_many_members():
    observations = [observation({"id": f"unique-{i}", "state": f"s{i % 9}"}) for i in range(18)]
    enum_paths = {item.path for item in infer_candidates(observations) if item.rule == "enum"}
    assert "id" not in enum_paths
    assert "state" not in enum_paths


def test_enforcement_detects_breaking_and_warning_changes():
    baseline = [observation({"items": [{"id": 1, "status": "active"}]}) for _ in range(10)]
    findings = enforce(
        observation({"items": [{"id": "1", "status": "NEW", "optional": 1}]}), rules_for(baseline)
    )
    kinds = {(item.kind, item.path) for item in findings}
    assert ("type_change", "items[].id") in kinds
    assert ("enum_expansion", "items[].status") in kinds
    assert not any(item.path.endswith("optional") for item in findings)


def test_missing_null_empty_status_and_non_json():
    baseline = [observation({"items": [{"id": 1}]}) for _ in range(3)]
    rules = rules_for(baseline)
    assert any(
        item.kind == "missing_required" for item in enforce(observation({"items": [{}]}), rules)
    )
    assert any(
        item.kind == "nullability_change"
        for item in enforce(observation({"items": [{"id": None}]}), rules)
    )
    assert any(item.kind == "empty_array" for item in enforce(observation({"items": []}), rules))
    assert any(
        item.kind == "status_change"
        for item in enforce(observation({"items": [{"id": 1}]}, status=500), rules)
    )
    html = Observation(200, "text/html", 1, 20, False, ())
    assert any(item.kind == "json_expected" for item in enforce(html, rules))


def test_explicit_expected_status_is_enforced_without_inferred_status_rule():
    current = observation({"ok": True}, status=201)
    findings = enforce(current, [], expected_status=200)
    assert [(item.kind, item.expected, item.actual) for item in findings] == [
        ("status_change", 200, 201)
    ]
