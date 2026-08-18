# Changelog

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
