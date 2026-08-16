# Driftlock

**Detect behavioral drift in third-party APIs before it breaks your application.**

Your uptime monitor sees:

```text
200 OK
```

Driftlock sees:

```text
products[].price   integer → string
products[]         historically nonempty → empty
status             new value "suspended"
```

Driftlock is a local-first CLI that learns conservative, deterministic invariants from ordinary JSON API responses. You review and approve those candidates into a commit-friendly contract; later checks explain meaningful drift and return a CI-ready exit code.

## Installation

Python 3.12+ is required. With [uv](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/HemVadgama/driftlock
```

From a clone:

```bash
uv sync --extra dev
uv run driftlock --help
```

## 60-second quickstart

```bash
driftlock init
driftlock add github-user https://api.github.com/users/octocat
driftlock list
driftlock sample github-user --count 5 --interval 1
driftlock infer github-user
driftlock approve github-user --all
driftlock check github-user
```

`init` creates `driftlock.yml`, `driftlock.lock.json`, and an ignored `.driftlock/history.sqlite3`. Commit the YAML and lock file; never commit runtime history.

## Example

Ten observations of this response:

```json
{"products": [{"id": "p1", "price": 20, "status": "active"}]}
```

can produce required-field, stable-type, non-null, small-enum, and nonempty-array candidates. After approval, this response:

```json
{"products": [{"id": "p1", "price": "20", "status": "ACTIVE"}]}
```

reports `products[].price` as a breaking type change and `products[].status` as a warning for an unfamiliar enum value. `{"products": []}` warns that a historically nonempty array is empty. New fields are not breaking.

Run `driftlock check NAME --json` for stable machine output, or `driftlock check --all` in CI. Exit codes are `0` healthy, `1` contract violation, and `2` configuration/network/Driftlock failure. Warnings alone do not fail a check.

Use `driftlock validate` to catch malformed configuration, missing environment variables, or missing approved contracts without making an HTTP request. Teams that want warnings to fail CI can run `driftlock check --all --warnings-as-errors`.

## How inference works

After at least three observations, Driftlock may propose consistent HTTP status, normalized content type, required paths, stable JSON types, non-null paths, and array cardinality ranges. Small string enums require at least ten scalar values, no more than eight unique members, and low cardinality. Array lengths are summarized with min/max/median; exact lengths are never inferred.

Paths are deterministic and omit indices: `products[].id`. Types are `string`, `integer`, `number`, `boolean`, `object`, `array`, and `null`; booleans are not integers. MIME parameters such as charsets are ignored.

Inference is evidence, not activation. `infer` shows candidate IDs, counts, confidence, and explanations. Only `approve --all` or `approve --candidate cNNN` writes enforced rules.

## Behavioral contracts

`driftlock.lock.json` is deterministic, human-readable, versioned, and designed for code review. Required fields, type changes, HTTP status drift, and JSON becoming non-JSON are breaking. Nullability, enum expansion, content-type drift, and an always-nonempty array becoming empty are warnings. Additive fields are accepted.

## CI

```yaml
- name: Install Driftlock
  run: uv tool install git+https://github.com/HemVadgama/driftlock
- name: Check external contracts
  env:
    VENDOR_TOKEN: ${{ secrets.VENDOR_TOKEN }}
  run: driftlock check --all
```

Driftlock's own tests use a local HTTP server and never call an external API.

## Authentication and secrets

Put environment references—not values—in `driftlock.yml`:

```yaml
version: 1
checks:
  vendor:
    url: https://api.example.com/v1/items
    method: GET
    headers:
      Authorization:
        env: VENDOR_TOKEN
    timeout_seconds: 10
```

Environment variables are resolved only for the request and are never logged or persisted. Missing variables fail clearly without exposing values. Do not put literal credentials in configuration. Users are responsible for having permission to query configured APIs.

The same secure setup can be created without editing YAML:

```bash
driftlock add vendor https://api.example.com/v1/items \
  --header-env Authorization=VENDOR_TOKEN \
  --header Accept=application/json \
  --query locale=en
```

Credential-like headers such as `Authorization`, `Cookie`, and `X-API-Key` are rejected by `--header`; use `--header-env` so only the environment variable name is persisted.

## Configuration

Each check supports `url`, `method: GET`, `expected_status`, `headers`, `query`, `timeout_seconds`, `max_response_bytes` (default 2 MiB), and `description`. The repeatable `--header`, `--header-env`, and `--query` flags cover common integrations directly from the CLI. `driftlock list` shows each endpoint's observation and approved-rule counts. Requests use explicit timeouts, TLS verification, `Driftlock/0.1`, no retries, and no redirect following. Non-JSON endpoints retain HTTP metadata but do not receive structural inference.

## Design philosophy

Availability is not compatibility. Driftlock optimizes for low false-positive rates, transparent evidence, explicit human approval, and local reproducibility. It has no telemetry, analytics, AI, or hosted dependency.

## Limitations

v0.1 focuses on JSON GET endpoints and conservative deterministic inference. It cannot determine semantic correctness, cannot know which changes your application truly depends on, and does not replace provider contract tests. History is repository-local and checks run only when invoked. Response-size anomaly inference and user-authored severity promotion are not included yet.

## Roadmap

0.2 may add scheduled checks, a first-class GitHub Action, webhook/Slack output, and richer user-defined invariants. Later possibilities include passive observation, OpenAPI comparison, response replay, dependency history, and hosted monitoring.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Small changes that improve trustworthiness and reduce noise are especially welcome.
