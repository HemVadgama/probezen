# `probezen discover --json`

Discovery emits a deterministic, versioned JSON document. Version 1 contains:

- `schema_version`: currently `1`;
- `scope`: the deliberately limited static-analysis scope;
- `files_scanned` and `network_requests_made`;
- `integrations`, ordered by host, with normalized calls, locations, confidence, evidence,
  monitoring eligibility, and supported consumer assumptions;
- `unresolved`, ordered by source location, for HTTP calls whose URL cannot be reconstructed;
- `write`, describing whether `--write` was requested and what it changed.

Call confidence is `high` for a fully static URL and method, `medium` when a host and useful path
pattern are known but dynamic values remain, and `unresolved` calls appear in the separate
`unresolved` collection. `monitoring_eligible` is independent of discovery confidence. In V1,
only high-confidence, concrete GET calls can be generated as Probezen checks.

The command performs static inspection only. It does not execute application requests, read
runtime environment-variable values, or emit request headers, cookies, API keys, or URL query
values.
