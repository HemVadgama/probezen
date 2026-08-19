# Stable check JSON

`probezen check --json` writes JSON only and keeps human formatting on the normal command path.
The top-level `schema_version` is currently `1`. Additive fields may appear within version 1;
removing fields or changing their meaning requires a schema-version change.

For one named endpoint:

```json
{
  "schema_version": 1,
  "check": "vendor",
  "dependency": null,
  "healthy": false,
  "fail_on": "breaking",
  "violations": [],
  "warnings": []
}
```

Each finding contains:

```json
{
  "severity": "breaking",
  "level": "high",
  "kind": "type_change",
  "path": "products[].price",
  "expected": "number",
  "actual": "string",
  "message": "",
  "confidence": "high",
  "affected_code": [],
  "reason": "The current response violates an explicitly approved expectation.",
  "suggested_action": "Update or guard the consuming code, or verify the provider's response contract."
}
```

`severity` is the contract category (`breaking` or `warning`). `level` is the impact level used by
`--fail-on`. `kind` is a stable supported drift identifier such as `missing_required`,
`type_change`, `nullability_change`, `enum_expansion`, `empty_array`, or `status_change`.

When checking multiple endpoints, the top level contains `checks` with the single-endpoint objects,
an aggregate `healthy` boolean, and a severity-count `summary`. Usage/configuration errors exit 2
and return:

```json
{"schema_version": 1, "healthy": false, "error": "actionable message"}
```

Consumers should branch first on the process exit code and `schema_version`, then on `healthy` and
the finding arrays. They should tolerate additive keys.
