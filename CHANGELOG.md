# Changelog

## 1.0.0 — 2026-08-17

### Added

- Marketplace-ready `Probezen Dependency Reliability` Action metadata with branding, documented
  inputs and outputs, a stable Python entry point, and optional JSON artifact uploads.
- GitHub job summaries and source-linked warning/error annotations.
- First-run and baseline-approval guidance that does not mislabel setup states as drift.
- Explicit separation between dependency reachability failures and confirmed behavioral changes.
- Threshold-aware `probezen check --fail-on LEVEL` behavior for CI.
- A GitHub-hosted runner workflow covering configured and first-run Action experiences.
- Custom configuration paths through `PROBEZEN_CONFIG` and the Action `config` input.

### Distribution

- Prepared stable Action major-version usage through `HemVadgama/probezen@v1`.
- Marketplace publication uses the public Action tag `v1`, aligned with Python package `1.0.0`.

## 0.2.0 — 2026-08-17

### Added

- Safe JavaScript/TypeScript discovery for literal URLs, known SDKs, and example environment URLs.
- A Git-friendly dependency inventory in the existing version 1 configuration format.
- `doctor`, `scan`, and `status`; `init` now discovers dependencies.
- Conservative code impact analysis and explainable severity, confidence, and next actions.
- Conservative latency and response-size baseline signals after 10 observations.
- Configurable sensitive paths and ignored finding paths.
- Versioned dependency JSON and richer compatible check JSON.
- A reusable composite GitHub Action and scheduled-CI documentation.

### Security

- Values at common secret response paths and configured sensitive paths are excluded from stored
  enum evidence. Raw response bodies and credential headers remain unpersisted.

### Compatibility

- Version 1 configuration and lock formats remain supported.
- Existing advanced commands and exit codes are unchanged.
- `probezen check` without a name now checks all endpoints; `--all` remains supported.
