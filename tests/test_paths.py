import pytest

from probezen.paths import json_type, traverse


def as_map(value: object):
    return {item.path: item for item in traverse(value)}


def test_nested_arrays_are_aggregated_without_indices():
    metrics = as_map({"products": [{"id": "p1", "price": 20}, {"id": "p2", "price": 2.5}]})
    assert metrics["products[]"].array_length == 2
    assert metrics["products[].id"].types == ("string",)
    assert metrics["products[].price"].types == ("integer", "number")
    assert metrics["products[].id"].occurrences == 2


def test_arrays_of_scalars_heterogeneity_empty_and_null():
    metrics = as_map({"mixed": [True, 1, "x", None], "empty": [], "value": None})
    assert metrics["mixed[]"].types == ("boolean", "integer", "null", "string")
    assert metrics["empty[]"].array_length == 0
    assert metrics["value"].types == ("null",)


def test_bool_is_not_integer():
    assert json_type(True) == "boolean"
    assert json_type(1) == "integer"


def test_deep_structure_is_deterministic_and_missing_is_absent():
    metrics = traverse({"z": {"b": {"c": 1}}, "a": {}})
    assert [item.path for item in metrics] == sorted(item.path for item in metrics)
    assert "not_here" not in {item.path for item in metrics}


def test_non_json_python_value_is_rejected():
    with pytest.raises(TypeError):
        traverse({"bad": {1, 2}})
