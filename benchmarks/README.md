# Probezen drift benchmark

This regression suite exercises the same inference and enforcement functions as the CLI. It
measures both useful drift detection and false-positive resistance:

```bash
python -m probezen.benchmark
```

Each case has a manifest entry, a baseline fixture containing repeated observations, a current
response fixture, and explicit expected finding kinds. Synthetic cases are labeled as synthetic;
they represent common integration failures rather than claimed public incidents.

## Real incidents

No real-incident fixture is included yet. Public discussions sometimes describe symptoms without
preserving complete before/after response bodies, sampling conditions, or enough field context to
reconstruct an accurate learned contract. Probezen will not label a reconstruction as a real
incident without a durable public source that supports the exact shape difference. A future real
fixture must include that source, unmodified relevant response shapes, and the expected result.

## Scope

The suite currently covers required fields, stable types, nested objects, enum-like expansions,
nonempty arrays, optional fields within arrays, changing scalar values, and naturally variable
array lengths. Same-type semantic value changes are explicitly unsupported: Probezen detects
observable contract drift, not business-meaning changes.
