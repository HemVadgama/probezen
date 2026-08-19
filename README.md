# Probezen

**Your dependencies can break without going down.**

An API can keep returning `200 OK` while changing a field, returning `null`, introducing an
enum value, slowing down, or producing a much larger payload. Probezen finds the external APIs
your application uses and warns when their behavior changes in ways that could affect your code.

Uptime monitoring tells you whether an API is online. Probezen tells you whether it still works
for your application.

```text
HIGH — Dependency behavior changed

  products[].price

  Previously: number
  Now:        string

  Likely affected:
  src/cart/calculateTotal.ts:42
    const total = response.price * quantity

  Reason: Used in arithmetic.
  Confidence: high
```

Probezen is deterministic, local-first, and does not require an account, hosted service, or AI.

## Install and run

Python 3.12+ is required. Install [Probezen from PyPI](https://pypi.org/project/probezen/) as an
isolated command-line application with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install probezen
probezen --help
```

Or use [pipx](https://pipx.pypa.io/):

```bash
pipx install probezen
probezen --help
```

That is all the setup the CLI needs—no repository clone, account, service, or API key is
required. Start in the application you want to inspect:

```bash
cd your-application
probezen init
probezen doctor
```

`pip install probezen` is also supported, although `uv tool` and `pipx` keep CLI dependencies in
an isolated environment.

## GitHub Action — no CLI installation required

**Catch breaking API changes before your users do.** Add one workflow and Probezen will install
itself, inspect the repository, publish a concise job summary, and annotate likely affected code.

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
          fail-on: high
```

No Probezen account, GitHub App, write permission, or hosted backend is required. On a repository
without Probezen configuration, the first run performs safe dependency discovery and provides
setup guidance without claiming drift. Commit `probezen.yml` and `probezen.lock.json` after
configuring and approving monitored behavior; subsequent runs enforce that baseline.

## Install from source

To try the latest code from `main` instead of the released package:

```bash
uv tool install git+https://github.com/HemVadgama/probezen
```

Contributors working from a clone can use the verified development workflow:

```bash
uv sync --extra dev
uv run probezen --help
```

## Start with your application

```bash
probezen init
probezen doctor
probezen scan
probezen check
```

`init` safely scans JavaScript and TypeScript source—it never imports or executes application
code. It detects literal HTTP URLs, known SDK imports, and URL environment variables whose safe
example values appear in `.env.example`, `.env.sample`, or `.env.template`. It writes a
human-readable dependency inventory to `probezen.yml`.

`doctor` provides value immediately, before monitoring history exists. It reports dependency risk
and conservative code assumptions such as numeric use, unguarded string methods, array indexing,
and fixed enum comparisons. `scan` refreshes that analysis. `status` reports monitoring readiness.

Dynamic URLs, custom wrappers, and runtime-only configuration can be added manually; discovery is
deliberately conservative.

## Monitor behavior

The original advanced workflow remains available:

```bash
probezen add vendor https://api.vendor.example/v1/products --dependency vendor-example
probezen sample vendor --count 10 --interval 1
probezen infer vendor
probezen approve vendor --all
probezen check vendor
```

Observations are evidence, not expectations. `infer` proposes a candidate baseline only after
sufficient consistent evidence. `approve` writes selected rules to the deterministic,
commit-friendly `probezen.lock.json`. `check` enforces only approved rules.

Probezen currently detects:

- HTTP status and JSON/content-type changes;
- required fields, stable types, nullability, and small enums;
- historically nonempty arrays becoming empty;
- large latency and response-size changes after at least 10 observations;
- additive fields as safe by default.

Operational thresholds are intentionally wide: four times the observed maximum, with floors of
1 second for latency and 1 MiB for payload size. This avoids treating ordinary variance as drift.

## Code impact and severity

For discovered JavaScript/TypeScript dependencies, Probezen connects matching response-field
changes to likely affected source. Unguarded arithmetic, string methods, array indexing, and enum
comparisons raise impact. Nearby guards lower confidence. Logging is not treated as a fragile
assumption.

Each result keeps the compatible `breaking` or `warning` contract classification and adds an
explainable `level` (`medium` or `high` today), confidence, likely code locations, reason, and
recommended action. Probezen says “likely affected,” not “definitely broken.”

Exit codes remain stable:

- `0`: healthy (warnings do not fail by default)
- `1`: approved contract violation by default, or a finding at/above `--fail-on LEVEL`
- `2`: configuration, network, or Probezen error

`probezen check` now checks all configured endpoints by default. The existing `--all` flag and
named form remain supported.

## Action reference and automation

Machine output is versioned and stable:

```bash
probezen doctor --json
probezen scan --json --no-write
probezen status --json
probezen check --json
```

The Marketplace-ready Action supports pull requests, pushes, and scheduled monitoring:

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
      - id: probezen
        uses: HemVadgama/probezen@v1
        with:
          command: check
          fail-on: high
          config: probezen.yml
          upload-results: "true"
        env:
          VENDOR_TOKEN: ${{ secrets.VENDOR_TOKEN }}
```

Use `@v1` for compatible v1 updates or pin an exact release tag/SHA for an immutable dependency.

### Inputs

| Input | Default | Purpose |
| --- | --- | --- |
| `command` | `check` | Run `check`, `doctor`, `scan`, or `status`. |
| `fail-on` | `high` | Fail on `low`, `medium`, `high`, or `critical` and above. |
| `config` | `probezen.yml` | Configuration path relative to the working directory. |
| `working-directory` | `.` | Application directory in a monorepo. |
| `upload-results` | `false` | Upload the versioned JSON result as an artifact. |
| `artifact-name` | `probezen-results` | Artifact name when uploads are enabled. |

### Outputs

| Output | Meaning |
| --- | --- |
| `outcome` | `healthy`, `changes-detected`, `setup-required`, `baseline-required`, `monitoring-error`, or `configuration-error`. |
| `findings` | Number of behavioral change findings. |
| `highest-severity` | Highest observed finding or dependency-risk level. |
| `result-file` | Absolute path to the versioned JSON result. |

The Action writes GitHub-native annotations and a Markdown job summary. Findings with impact at or
above `fail-on` are errors; lower-impact findings are warnings. The JSON result can be consumed by
later steps or uploaded without granting repository write access.

```yaml
- id: probezen
  uses: HemVadgama/probezen@v1
  with:
    fail-on: medium

- if: always()
  run: echo "Probezen outcome: ${{ steps.probezen.outputs.outcome }}"
```

### CI behavior

| Situation | Outcome | Step result |
| --- | --- | --- |
| No changes | `healthy` | Pass |
| First run needs setup | `setup-required` | Pass with guidance |
| Baseline needs approval | `baseline-required` | Pass with warning; no drift asserted |
| Change below `fail-on` | `changes-detected` | Pass with warning annotations |
| Change at/above `fail-on` | `changes-detected` | Fail with error annotations |
| Dependency unavailable or response unusable | `monitoring-error` | Operational failure, explicitly not labeled drift |
| Invalid configuration | `configuration-error` | Configuration failure |

Scheduled workflows use exactly the same deterministic baseline and exit behavior. Keep API
credentials in GitHub Actions secrets and reference only environment-variable names from
`probezen.yml`. The Action itself requests no permissions and reads no secrets unless you map them
into `env` for a configured endpoint.

The same CLI commands and exit statuses work in any CI system.

## Security and storage

Probezen stores runtime history in `.probezen/history.sqlite3`; it stores response metadata and
structural path summaries, never raw response bodies. `Authorization`, cookies, API keys, and
credential-like headers must reference environment variables and are never persisted or printed.
Common secret fields such as `token`, `password`, and `api_key` retain type evidence but never
their string values.

Configure additional private fields and accepted noise when adding a check:

```bash
probezen add vendor https://api.vendor.example/v1/customer \
  --header-env Authorization=VENDOR_TOKEN \
  --sensitive-path customer.email \
  --ignore-path metadata.request_id
```

Commit `probezen.yml` and `probezen.lock.json`. Do not commit `.probezen/` or `.env` files. Review
generated configuration before sharing it. Probezen has no telemetry.

## Configuration and compatibility

Version 1 configuration and lock files remain valid. A v0.1 file with only `checks` is loaded
unchanged; `init` or `scan` adds the optional `dependencies` inventory. Existing `add`, `list`,
`sample`, `infer`, `approve`, `validate`, and `check --all` workflows continue to work.

```yaml
version: 1
dependencies:
  stripe:
    name: Stripe
    type: api
    provider: stripe
    hosts: [api.stripe.com]
checks:
  stripe-prices:
    dependency: stripe
    url: https://api.stripe.com/v1/prices
    headers:
      Authorization:
        env: STRIPE_TOKEN
    sensitive_paths: [customer.email]
    ignore_paths: [metadata.request_id]
```

## How inference stays quiet

At least three observations are required for structural candidates; small enums and operational
signals need ten. IDs and highly varied strings are rejected as enums. Exact array lengths and
narrow numeric ranges are not inferred. Optional new fields do not fail checks. Nothing is
enforced until explicitly approved.

## Limitations

- Discovery and impact analysis target JavaScript/TypeScript; Probezen itself is a Python CLI.
- Static analysis is lexical and conservative, not a whole-program type analysis. Aliased fields,
  runtime-generated URLs, and custom HTTP abstractions may require manual configuration.
- Active monitoring currently supports JSON GET endpoints.
- History is local; scheduled monitoring is ordinary CI rather than a hosted scheduler.
- Probezen detects behavioral compatibility, not semantic correctness or availability SLAs.

See [examples/typescript-app](examples/typescript-app) for realistic dependency usage and
[CONTRIBUTING.md](CONTRIBUTING.md) for development instructions.
