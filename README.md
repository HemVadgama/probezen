# Probezen

**Catch when an API changes without going down.**

```text
GET /products → 200 OK

Yesterday: {"price": 19.99}
Today:     {"price": "19.99"}

HTTP status: 200 OK
Probezen: type drift detected at price
```

Probezen watches the observed behavior of APIs your software depends on. It is particularly useful
when you do not control the API and need to know when a successful response changes underneath
your application.

## See it in 10 seconds

```bash
uvx probezen demo
```

The demo is offline and creates no files. It runs Probezen's real inference and enforcement engine
against deterministic built-in responses:

```text
Probezen demo

Learning normal API behavior...
  ✓ user.id          integer
  ✓ user.plan        {"pro"}
  ✓ user.email       present

Simulating an upstream response change...
  HTTP 200 OK

DRIFT DETECTED
  user.id: integer → string
  user.email: required → missing

The API never went down. Its behavior changed.
```

## How it works

1. Probezen observes repeated JSON responses without storing their raw bodies.
2. It proposes a small set of consistent behaviors: required fields, stable types, nullability,
   enum-like values, and historically nonempty arrays.
3. You review and approve the proposed contract stored in `probezen.lock.json`.
4. Future checks report when a response violates that approved behavior, even if the request still
   succeeds.

The contract stays with your code, produces readable Git diffs, and is never changed silently.
Probezen complements existing tests and monitoring by focusing specifically on drift in external
API responses.

## Install

Python 3.12+ is required. Install the released CLI in an isolated environment:

```bash
uv tool install probezen
```

or:

```bash
pipx install probezen
```

`pip install probezen` also works. Source and contributor setup is documented in
[CONTRIBUTING.md](CONTRIBUTING.md).

## Monitor your first API

From the application that depends on the API:

```bash
probezen init
probezen add github https://api.github.com/users/octocat
probezen learn github
probezen check github
```

That is the recommended workflow: **add → learn → check**.

`learn` collects three new observations by default, displays every candidate invariant and its
evidence, then asks before writing `probezen.lock.json`. Use `--count 10` when you also want
enum-like and operational candidates. For a reviewed noninteractive workflow:

```bash
probezen learn github --count 10 --approve-all
```

The lower-level `sample`, `infer`, and `approve` commands remain available when you want control
over each step.

## Inspect and accept intentional changes

The observed contract lives with your code. Inspect it at any time:

```bash
probezen show github
probezen show github --json
```

When a provider change is legitimate, Probezen never accepts it silently:

```bash
probezen update github
git diff -- probezen.lock.json
```

`update` first displays the current drift, asks for confirmation, collects fresh evidence for
that endpoint, and rewrites only its approved contract. Review the deterministic lock-file diff
before committing it. `show` reports both the baseline observation count and when it was learned.

## Authenticated APIs

Store the **environment-variable name**, never the secret, in configuration:

```bash
export GITHUB_AUTH_HEADER="Bearer <token>"
probezen add private-api https://api.example.com/v1/account \
  --header-env Authorization=GITHUB_AUTH_HEADER
probezen learn private-api
```

The environment variable contains the complete header value. Probezen supports arbitrary
non-secret headers with `--header NAME=VALUE` and secret or credential-like headers with
`--header-env NAME=ENV_VAR`. Resolved values are used only for the request; they are never written
to configuration, history, contracts, JSON output, or logs.

Diagnose configuration and missing CI variables without making a live request:

```bash
probezen doctor
```

## CI in one step

Commit `probezen.yml` and `probezen.lock.json`, but not `.probezen/`. Then add:

```yaml
name: Probezen
on:
  pull_request:
  push:
    branches: [main]
  schedule:
    - cron: "0 */6 * * *"

permissions:
  contents: read

jobs:
  probezen:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: HemVadgama/probezen@v1
        with:
          command: check
          fail-on: high
        env:
          GITHUB_AUTH_HEADER: ${{ secrets.GITHUB_AUTH_HEADER }}
```

`check` checks every configured endpoint when no name is supplied. The compatible `--all` flag
remains available in generic CI:

```bash
probezen check --all
```

Exit codes are stable:

- `0`: approved contract satisfied (warnings do not fail by default)
- `1`: breaking drift, or a finding at/above `--fail-on`
- `2`: configuration, authentication, network, or Probezen error

For machine consumers, `probezen check --json` emits a versioned, deterministic structure with
`schema_version`, health, threshold, violations, warnings, and a multi-endpoint summary. See the
[stable JSON reference](docs/check-json.md) and
[complete CI example](examples/ci/probezen.yml).

## What gets learned

Probezen requires repeated consistent evidence and explicit approval:

- structural candidates require at least 3 observations;
- required fields must be present in every observation and every item of an observed array;
- enum-like values require at least 10 values, no more than 8 unique short members, and low
  variability;
- nullability is enforced only after consistent non-null evidence;
- nonempty-array warnings apply only if every baseline observation was nonempty;
- latency and response-size warnings require 10 observations and use wide thresholds;
- changing scalar values, optional fields, and additive fields do not fail checks by default.

Nothing is enforced until it appears in the readable `probezen.lock.json` and you approve it.
Raw bodies are never stored; local SQLite history retains only response metadata and structural
path summaries. From a repository checkout, run the honest regression benchmark with:

```bash
python -m probezen.benchmark
```

See [benchmarks/README.md](benchmarks/README.md) for scope and results.

## Supported drift

- HTTP status and JSON/content-type changes
- required fields disappearing
- stable JSON types changing
- historically non-null values becoming null
- small enum-like sets gaining a value
- historically nonempty arrays becoming empty
- large latency or response-size changes after sufficient evidence

Human output explains the path, expected and observed behavior, severity, why it matters, likely
affected JavaScript/TypeScript code when discoverable, and the suggested action.

## Limitations

- Active monitoring currently supports HTTP GET endpoints and JSON response inference.
- Discovery and code-impact analysis currently target JavaScript and TypeScript.
- Probezen cannot infer semantic business correctness; `19.99 → 99.99` is invisible if the type
  and approved invariants remain valid.
- It focuses on observed response drift; it does not provide broad request generation, complete
  API specification validation, or hosted availability alerting.
- Authenticated APIs require ordinary credentials supplied by your environment.
- Live checks inherit provider outages, rate limits, and response variability.
- Inference protects only conservative patterns supported by enough observations; uncertain
  behavior is intentionally missed rather than over-enforced.
- History is local; scheduled monitoring is ordinary CI, not a hosted scheduler.

More examples:

- [Public API](examples/public-api/README.md)
- [Authenticated API](examples/authenticated-api/README.md)
- [Intentional drift update](examples/intentional-drift/README.md)
- [Product fit and boundaries](docs/product-fit.md)
- [GitHub Marketplace and release guide](docs/MARKETPLACE.md)
- [Security policy](SECURITY.md)

Probezen is MIT licensed and has no telemetry.
