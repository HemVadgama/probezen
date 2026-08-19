from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .check import enforce
from .config import (
    ConfigError,
    Endpoint,
    config_path,
    is_credential_header,
    load_config,
    load_endpoint,
    resolve_headers,
    save_config,
)
from .contract import ContractError, load_lock, load_rules, lock_path, save_contract
from .demo import run_demo
from .dependencies import Dependency, discover, inventory_mapping
from .discovery import (
    RepositoryDiscovery,
    discover_repository,
    starter_checks,
    starter_dependencies,
)
from .http import RequestError, fetch
from .impact import explain_impact
from .infer import infer_candidates
from .models import Observation
from .reporting import display, finding_label, print_candidates, print_check
from .storage import (
    StorageError,
    clear_observations,
    connect,
    load_observations,
    save_observation,
)

app = typer.Typer(
    no_args_is_help=True,
    help="Detect behavior changes in APIs you do not control—even when they still return 200.",
)
console = Console(stderr=True)
output_console = Console()


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"probezen {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=version_callback, is_eager=True, help="Show version."),
    ] = None,
) -> None:
    """Catch changes in the observed behavior of external APIs."""


@app.command()
def demo() -> None:
    """See Probezen detect API drift locally—no setup or network required."""
    result = run_demo()
    candidates = {(item.rule, item.path): item for item in result.candidates}
    output_console.print("[bold]Probezen demo[/bold]\n")
    output_console.print("Learning normal API behavior from deterministic local responses...\n")
    for rule, path, label in (
        ("type", "user.id", "integer"),
        ("enum", "user.plan", '{"pro"}'),
        ("required", "user.email", "present"),
    ):
        if (rule, path) in candidates:
            output_console.print(f"  [green]✓[/green] {path:<16} {label}")
    output_console.print(
        f"\nContract learned and approved from {result.observations} observations."
    )
    output_console.print("\nSimulating an upstream response change...\n")
    output_console.print(f"  HTTP {result.current.status} OK\n")
    output_console.print("[bold red]DRIFT DETECTED[/bold red]\n")
    for finding in result.findings:
        if finding.kind not in {"type_change", "missing_required"}:
            continue
        output_console.print(f"  [red]{finding.path}[/red]")
        output_console.print(f"    expected: {display(finding.expected)}")
        output_console.print(f"    observed: {display(finding.actual)}")
        output_console.print(f"    {finding_label(finding.kind)}\n")
    output_console.print("[bold]The API never went down: it still returned HTTP 200.[/bold]")
    output_console.print("Its observed behavior changed, and Probezen detected the drift.\n")
    output_console.print("Try Probezen on a real API:")
    output_console.print("  probezen init")
    output_console.print("  probezen add example https://api.example.com/data")
    output_console.print("  probezen learn example")


@app.command()
def init() -> None:
    """Discover dependencies and initialize Probezen in this repository."""
    root = Path.cwd()
    config = config_path(root)
    result = discover(root)
    if not config.exists():
        save_config(
            root,
            {
                "version": 1,
                "dependencies": inventory_mapping(result),
                "checks": {},
            },
        )
    else:
        _merge_inventory(root, result)
    if not lock_path(root).exists():
        lock_path(root).write_text('{\n  "contracts": {},\n  "version": 1\n}\n')
    connect(root).close()
    ignore = root / ".gitignore"
    existing = ignore.read_text() if ignore.exists() else ""
    additions = [
        item for item in (".probezen/", ".env", "*.sqlite3") if item not in existing.splitlines()
    ]
    if additions:
        ignore.write_text(
            existing
            + ("" if not existing or existing.endswith("\n") else "\n")
            + "\n".join(additions)
            + "\n"
        )
    typer.echo("Scanning repository...\n")
    typer.echo(f"Detected ecosystem: {result.ecosystem}")
    typer.echo(f"Found {len(result.dependencies)} external dependencies:\n")
    for dependency in result.dependencies:
        typer.echo(dependency.name)
        typer.echo(f"  {', '.join(dependency.hosts)}")
        for location in dependency.discovered_from[:3]:
            typer.echo(f"  {location.path}:{location.line}")
        typer.echo()
    typer.echo(
        "Probezen configuration created or updated.\n\nNext:\n"
        "  probezen add NAME https://api.example.com/data\n"
        "  probezen learn NAME"
    )


@app.command()
def scan(
    json_output: Annotated[bool, typer.Option("--json", help="Emit stable JSON only.")] = False,
    no_write: Annotated[
        bool, typer.Option("--no-write", help="Analyze without updating the dependency inventory.")
    ] = False,
) -> None:
    """Scan source code for dependencies and fragile assumptions."""
    root = Path.cwd()
    try:
        load_config(root)
        result = discover(root)
        if not no_write:
            _merge_inventory(root, result)
        if json_output:
            typer.echo(json.dumps(result.to_dict(), sort_keys=True))
            return
        typer.echo(f"Probezen scan · {result.ecosystem}\n")
        typer.echo(f"Dependencies found: {len(result.dependencies)}")
        typer.echo(f"Source files scanned: {result.files_scanned}\n")
        if not result.dependencies:
            typer.echo(
                "No external API dependencies found in supported JavaScript/TypeScript source."
            )
        for dependency in result.dependencies:
            typer.echo(
                f"{dependency.risk.upper():6}  {dependency.name}  ({', '.join(dependency.hosts)})"
            )
            for usage in dependency.usages[:3]:
                guard = "guarded" if usage.guarded else usage.reason
                typer.echo(f"        {usage.path}:{usage.line} · {usage.field} · {guard}")
        if not no_write:
            typer.echo("\nUpdated dependency inventory in probezen.yml.")
    except ConfigError as exc:
        fail(str(exc), json_output)


@app.command("discover")
def discover_command(
    json_output: Annotated[bool, typer.Option("--json", help="Emit stable JSON only.")] = False,
    write: Annotated[
        bool, typer.Option("--write", help="Create a starter configuration from safe GET calls.")
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show evidence and unresolved calls.")
    ] = False,
) -> None:
    """Find supported third-party HTTP calls without executing them."""
    root = Path.cwd()
    result = discover_repository(root)
    written: list[str] = []
    write_error: str | None = None
    if write:
        config = config_path(root)
        if config.exists():
            write_error = f"Configuration already exists at {config}; no files were changed."
        else:
            checks = starter_checks(result)
            if checks:
                save_config(
                    root,
                    {
                        "version": 1,
                        "dependencies": starter_dependencies(result),
                        "checks": checks,
                    },
                )
                written = sorted(checks)
            else:
                write_error = "No sufficiently confident, safe GET calls are available to write."

    if json_output:
        payload = result.to_dict()
        payload["write"] = {
            "requested": write,
            "configuration_written": bool(written),
            "checks": written,
            "error": write_error,
        }
        typer.echo(json.dumps(payload, sort_keys=True))
        if write_error:
            raise typer.Exit(2)
        return

    _print_discovery(result, verbose)
    if written:
        typer.echo(
            f"\nCreated probezen.yml with {len(written)} safe GET check(s).\n"
            "Review the generated URLs, then run `probezen learn NAME`."
        )
    elif write_error:
        fail(write_error)
    elif any(call.monitoring_eligible for item in result.integrations for call in item.calls):
        typer.echo("\nRun `probezen discover --write` to create a starter configuration.")


def _print_discovery(result: RepositoryDiscovery, verbose: bool) -> None:
    call_count = sum(len(item.calls) for item in result.integrations)
    if not call_count:
        typer.echo("No supported third-party API integrations were confidently discovered.\n")
        typer.echo(
            "Probezen currently detects direct fetch calls and direct/static-instance Axios "
            "calls in JavaScript and TypeScript. You can configure an endpoint manually with "
            "`probezen init` followed by `probezen add NAME URL`."
        )
        if result.unresolved:
            typer.echo(
                f"\n{len(result.unresolved)} external call candidate(s) could not be resolved."
            )
            if not verbose:
                typer.echo("Run with `--verbose` for details.")
    else:
        typer.echo("Discovered third-party API integrations\n")
        for integration in result.integrations:
            typer.echo(f"{integration.service} · {integration.host}\n")
            for call in integration.calls:
                method = call.method or "UNKNOWN"
                endpoint = call.endpoint or "(unresolved path)"
                typer.echo(f"  {method} {endpoint}")
                typer.echo(f"  {call.path}:{call.line}")
                typer.echo(f"  Confidence: {call.confidence}")
                if not call.monitoring_eligible:
                    typer.echo(f"  Monitoring: not eligible ({call.monitoring_reason})")
                if call.assumptions:
                    typer.echo("  Consumer assumptions:")
                    for assumption in call.assumptions:
                        typer.echo(f"    {assumption.field} · {assumption.reason}")
                        typer.echo(f"      {assumption.path}:{assumption.line}")
                if verbose:
                    typer.echo(f"  Evidence: {call.client} · {call.evidence}")
                typer.echo()
        candidates = sum(
            call.monitoring_eligible for item in result.integrations for call in item.calls
        )
        typer.echo(
            f"Found {call_count} call(s) across {len(result.integrations)} integration(s); "
            f"{candidates} monitoring candidate(s)."
        )
        if result.unresolved:
            typer.echo(f"{len(result.unresolved)} additional call(s) could not be fully resolved.")
            if not verbose:
                typer.echo("Run with `--verbose` for details.")
    if verbose and result.unresolved:
        typer.echo("\nUnresolved calls")
        for item in result.unresolved:
            typer.echo(f"  {item.path}:{item.line} · {item.client} · {item.reason}")
            if item.expression:
                typer.echo(f"    {item.expression}")


@app.command()
def doctor(
    json_output: Annotated[bool, typer.Option("--json", help="Emit stable JSON only.")] = False,
) -> None:
    """Validate local setup, credentials, contracts, and dependency risk offline."""
    root = Path.cwd()
    try:
        config = load_config(root)
        lock = load_lock(root)
        names = sorted(config.get("checks", {}))
        issues: list[dict[str, str]] = []
        ready = 0
        for name in names:
            try:
                endpoint = load_endpoint(root, name)
                resolve_headers(endpoint)
                load_rules(root, name)
                ready += 1
            except (ConfigError, ContractError) as exc:
                issues.append({"check": name, "message": str(exc)})
        for name in sorted(set(lock["contracts"]) - set(names)):
            try:
                load_rules(root, name)
            except ContractError as exc:
                issues.append({"check": name, "message": str(exc)})
            else:
                issues.append(
                    {
                        "check": name,
                        "message": (
                            "Contract has no configured endpoint; remove it or add the endpoint"
                        ),
                    }
                )
        result = discover(root)
        summary = {level: 0 for level in ("high", "medium", "low")}
        for dependency in result.dependencies:
            summary[dependency.risk] += 1
        payload = {
            "schema_version": 1,
            "dependencies_analyzed": len(result.dependencies),
            "summary": summary,
            "dependencies": [item.to_dict() for item in result.dependencies],
            "setup": {
                "version": __version__,
                "configuration_valid": True,
                "endpoints_configured": len(names),
                "endpoints_ready": ready,
                "approved_contracts": len(lock["contracts"]),
                "network_requests_made": False,
            },
            "issues": issues,
        }
        if json_output:
            typer.echo(json.dumps(payload, sort_keys=True))
            if issues:
                raise typer.Exit(2)
            return
        typer.echo("Probezen doctor\n")
        typer.echo(f"✓ Probezen {__version__}")
        typer.echo(f"✓ Configuration valid: {config_path(root)}")
        typer.echo(f"✓ {len(names)} endpoints configured")
        typer.echo(f"✓ {len(lock['contracts'])} approved contracts readable")
        if not issues:
            typer.echo("✓ Required authentication environment variables available")
        for issue in issues:
            typer.echo(f"✗ {issue['check']}: {issue['message']}")
        typer.echo("✓ No live API requests made\n")
        typer.echo(f"Dependencies analyzed: {len(result.dependencies)}")
        for level in ("high", "medium", "low"):
            typer.echo(f"{level.upper():8} {summary[level]}")
        if not result.dependencies:
            typer.echo("\nNo supported source dependencies detected.")
        else:
            highest = min(
                result.dependencies,
                key=lambda item: {"high": 0, "medium": 1, "low": 2}[item.risk],
            )
            typer.echo(f"\nHighest dependency risk: {highest.name} ({highest.risk.upper()})")
        if issues:
            typer.echo(f"\n{len(issues)} setup issue(s) found.")
            raise typer.Exit(2)
    except ConfigError as exc:
        fail(str(exc), json_output)


@app.command()
def status(
    json_output: Annotated[bool, typer.Option("--json", help="Emit stable JSON only.")] = False,
) -> None:
    """Show inventory and monitoring readiness without making network requests."""
    root = Path.cwd()
    try:
        config = load_config(root)
        contracts = load_lock(root).get("contracts", {})
        dependencies = config.get("dependencies", {})
        checks = config.get("checks", {})
        payload = {
            "schema_version": 1,
            "dependencies": len(dependencies),
            "monitored_endpoints": len(checks),
            "baselined_endpoints": len(contracts),
            "unbaselined_endpoints": max(0, len(checks) - len(contracts)),
        }
        if json_output:
            typer.echo(json.dumps(payload, sort_keys=True))
        else:
            typer.echo("Probezen Status\n")
            typer.echo(f"Dependencies:          {payload['dependencies']}")
            typer.echo(f"Monitored endpoints:   {payload['monitored_endpoints']}")
            typer.echo(f"Baselined endpoints:   {payload['baselined_endpoints']}")
            typer.echo(f"Unbaselined endpoints: {payload['unbaselined_endpoints']}")
    except (ConfigError, ContractError) as exc:
        fail(str(exc), json_output)


@app.command("add")
def add_check(
    name: str,
    url: str,
    expected_status: Annotated[int, typer.Option(help="Expected HTTP status.")] = 200,
    timeout: Annotated[float, typer.Option(help="Request timeout in seconds.")] = 10.0,
    description: Annotated[str | None, typer.Option(help="Optional description.")] = None,
    dependency: Annotated[
        str | None, typer.Option(help="Dependency inventory ID for code impact analysis.")
    ] = None,
    header: Annotated[
        list[str] | None,
        typer.Option("--header", help="Non-secret request header as NAME=VALUE; repeatable."),
    ] = None,
    header_env: Annotated[
        list[str] | None,
        typer.Option(
            "--header-env",
            help="Secret request header as NAME=ENV_VAR; repeatable.",
        ),
    ] = None,
    query: Annotated[
        list[str] | None,
        typer.Option("--query", help="Query parameter as NAME=VALUE; repeatable."),
    ] = None,
    max_response_bytes: Annotated[
        int,
        typer.Option(min=1, help="Maximum response size in bytes."),
    ] = 2 * 1024 * 1024,
    sensitive_path: Annotated[
        list[str] | None,
        typer.Option("--sensitive-path", help="Response path whose values must never be retained."),
    ] = None,
    ignore_path: Annotated[
        list[str] | None,
        typer.Option("--ignore-path", help="Approved finding path glob to ignore."),
    ] = None,
) -> None:
    """Add a JSON GET endpoint to probezen.yml."""
    root = Path.cwd()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", name):
        fail("Check names may contain letters, numbers, underscores, and hyphens")
    try:
        parsed_url = urlparse(url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            fail("URL must use http or https")
        if not 100 <= expected_status <= 599:
            fail("Expected status must be between 100 and 599")
        if timeout <= 0:
            fail("Timeout must be positive")
        literal_headers = parse_pairs(header or [], "header")
        env_headers = parse_pairs(header_env or [], "environment-backed header")
        queries = parse_pairs(query or [], "query parameter")
        duplicates = set(literal_headers) & set(env_headers)
        if duplicates:
            fail(f"Headers configured twice: {', '.join(sorted(duplicates))}")
        unsafe = sorted(key for key in literal_headers if is_credential_header(key))
        if unsafe:
            fail(f"Credential-like headers must use --header-env: {', '.join(unsafe)}")
        data = load_config(root)
        checks = data.setdefault("checks", {})
        if name in checks:
            fail(f"Check '{name}' already exists")
        item: dict[str, Any] = {
            "url": url,
            "method": "GET",
            "expected_status": expected_status,
            "timeout_seconds": timeout,
            "max_response_bytes": max_response_bytes,
        }
        configured_headers: dict[str, Any] = dict(literal_headers)
        configured_headers.update({key: {"env": value} for key, value in env_headers.items()})
        if configured_headers:
            item["headers"] = configured_headers
        if queries:
            item["query"] = queries
        if description:
            item["description"] = description
        if dependency:
            item["dependency"] = dependency
        if sensitive_path:
            item["sensitive_paths"] = sorted(set(sensitive_path))
        if ignore_path:
            item["ignore_paths"] = sorted(set(ignore_path))
        checks[name] = item
        if dependency and isinstance(data.get("dependencies", {}).get(dependency), dict):
            data["dependencies"][dependency]["monitoring"] = "configured"
        save_config(root, data)
        load_endpoint(root, name)
    except ConfigError as exc:
        fail(str(exc))
    typer.echo(f"Added endpoint '{name}'.\n\nNext:\n  probezen learn {name}")


@app.command("list")
def list_checks(
    json_output: Annotated[bool, typer.Option("--json", help="Emit stable JSON only.")] = False,
) -> None:
    """List configured checks, samples, and contract status."""
    root = Path.cwd()
    try:
        names = sorted(load_config(root).get("checks", {}))
        contracts = load_lock(root)["contracts"]
        rows = []
        for name in names:
            endpoint = load_endpoint(root, name)
            contract = contracts.get(name, {})
            rules = contract.get("rules", []) if isinstance(contract, dict) else []
            rows.append(
                {
                    "name": name,
                    "url": endpoint.url,
                    "observations": len(load_observations(root, name)),
                    "approved_rules": len(rules),
                }
            )
        if json_output:
            typer.echo(json.dumps({"checks": rows}, sort_keys=True))
            return
        if not rows:
            typer.echo("No checks configured. Add one with 'probezen add NAME URL'.")
            return
        table = Table(title="Probezen checks")
        for heading in ("Name", "URL", "Samples", "Approved rules"):
            table.add_column(heading)
        for row in rows:
            table.add_row(
                str(row["name"]),
                str(row["url"]),
                str(row["observations"]),
                str(row["approved_rules"]),
            )
        Console().print(table)
    except (ConfigError, ContractError, StorageError) as exc:
        fail(str(exc), json_output)


@app.command()
def validate(
    json_output: Annotated[bool, typer.Option("--json", help="Emit stable JSON only.")] = False,
) -> None:
    """Validate configuration, environment variables, and approved contracts offline."""
    root = Path.cwd()
    try:
        names = sorted(load_config(root).get("checks", {}))
        if not names:
            fail("No checks configured", json_output)
        results = []
        for name in names:
            endpoint = load_endpoint(root, name)
            resolve_headers(endpoint)
            load_rules(root, name)
            results.append({"check": name, "valid": True})
        if json_output:
            typer.echo(json.dumps({"valid": True, "checks": results}, sort_keys=True))
        else:
            typer.echo(f"Configuration valid: {len(results)} checks ready")
    except (ConfigError, ContractError) as exc:
        fail(str(exc), json_output)


@app.command()
def sample(
    name: str,
    count: Annotated[int, typer.Option(min=1, max=100, help="Number of observations.")] = 1,
    interval: Annotated[float, typer.Option(min=0, help="Seconds between observations.")] = 0,
) -> None:
    """Observe an endpoint and store response metadata locally."""
    root = Path.cwd()
    try:
        endpoint = load_endpoint(root, name)
        _collect_samples(root, endpoint, count, interval)
    except (ConfigError, RequestError, StorageError) as exc:
        fail(f"Could not sample '{name}'.\n\n{exc}\n\nRun 'probezen doctor' to check setup.")


@app.command()
def learn(
    name: str,
    count: Annotated[
        int, typer.Option(min=3, max=100, help="New observations to collect before inference.")
    ] = 3,
    interval: Annotated[float, typer.Option(min=0, help="Seconds between observations.")] = 0,
    approve_all: Annotated[
        bool, typer.Option("--approve-all", help="Approve every candidate without prompting.")
    ] = False,
    no_approve: Annotated[
        bool, typer.Option("--no-approve", help="Show candidates without approving them.")
    ] = False,
) -> None:
    """Collect evidence, infer a contract, and explicitly approve it."""
    if approve_all and no_approve:
        fail("Choose --approve-all or --no-approve, not both")
    root = Path.cwd()
    try:
        endpoint = load_endpoint(root, name)
        typer.echo(f"Learning normal behavior for '{name}'...\n")
        _collect_samples(root, endpoint, count, interval)
        observations = load_observations(root, name)
        candidates = infer_candidates(observations)
        print_candidates(name, candidates)
        if not candidates:
            fail(f"Not enough consistent evidence for '{name}'. Collect at least 3 observations.")
        if no_approve:
            typer.echo(f"Review complete. Approve later with: probezen approve {name} --all")
            return
        approved = approve_all or typer.confirm("Approve these candidates as the baseline?")
        if not approved:
            typer.echo("No contract changed. Review candidates with 'probezen infer'.")
            return
        save_contract(root, name, candidates, sample_count=len(observations))
        typer.echo(
            f"\nApproved {len(candidates)} rules for '{name}'.\n\nNext:\n  probezen check {name}"
        )
    except (ConfigError, RequestError, StorageError, ContractError) as exc:
        fail(f"Could not learn '{name}'.\n\n{exc}\n\nRun 'probezen doctor' to check setup.")


@app.command()
def infer(name: str) -> None:
    """Display conservative candidate invariants without activating them."""
    try:
        candidates = infer_candidates(load_observations(Path.cwd(), name))
        print_candidates(name, candidates)
    except StorageError as exc:
        fail(str(exc))


@app.command()
def approve(
    name: str,
    all_rules: Annotated[
        bool, typer.Option("--all", help="Approve every current candidate.")
    ] = False,
    candidate: Annotated[
        list[str] | None, typer.Option("--candidate", "-c", help="Candidate ID to approve.")
    ] = None,
) -> None:
    """Approve inferred candidates into the committed lock file."""
    try:
        candidates = infer_candidates(load_observations(Path.cwd(), name))
        if not candidates:
            fail("No candidates available; collect at least 3 consistent observations")
        if not all_rules and not candidate:
            fail("Choose --all or one or more --candidate IDs (shown by 'probezen infer')")
        selected = (
            candidates
            if all_rules
            else [item for item in candidates if item.id in set(candidate or [])]
        )
        unknown = set(candidate or []) - {item.id for item in candidates}
        if unknown:
            fail(f"Unknown candidate IDs: {', '.join(sorted(unknown))}")
        observations = load_observations(Path.cwd(), name)
        save_contract(Path.cwd(), name, selected, sample_count=len(observations))
        typer.echo(
            f"Approved {len(selected)} rules for '{name}'.\n\nNext:\n  probezen check {name}"
        )
    except (StorageError, ContractError) as exc:
        fail(str(exc))


@app.command()
def show(
    name: str,
    json_output: Annotated[bool, typer.Option("--json", help="Emit stable JSON only.")] = False,
) -> None:
    """Show approved rules, unapproved candidates, and supporting evidence."""
    root = Path.cwd()
    try:
        endpoint = load_endpoint(root, name)
        observations = load_observations(root, name)
        lock = load_lock(root)
        contract = lock["contracts"].get(name, {})
        approved = load_rules(root, name) if name in lock["contracts"] else []
        inferred = infer_candidates(observations)
        approved_keys = {_rule_key(item) for item in approved if isinstance(item, dict)}
        unapproved = [item for item in inferred if _rule_key(item.to_dict()) not in approved_keys]
        payload = {
            "schema_version": 1,
            "check": name,
            "url": endpoint.url,
            "observations": len(observations),
            "learned_from_observations": (
                contract.get("learned_from_observations", 0) if isinstance(contract, dict) else 0
            ),
            "learned_at": contract.get("learned_at") if isinstance(contract, dict) else None,
            "approved_rules": approved,
            "unapproved_candidates": [item.to_dict() for item in unapproved],
        }
        if json_output:
            typer.echo(json.dumps(payload, sort_keys=True))
            return
        typer.echo(f"Probezen contract · {name}\n")
        typer.echo(f"Endpoint: {endpoint.url}")
        typer.echo(f"Evidence: {len(observations)} observations")
        typer.echo(f"Learned: {payload['learned_at'] or 'unknown (legacy contract)'}")
        typer.echo(f"Approved rules: {len(approved)}\n")
        if approved:
            for rule in approved:
                typer.echo(
                    f"  {str(rule.get('rule', '')).upper():12} "
                    f"{rule.get('path')} = {display(rule.get('expected'))}"
                )
                typer.echo(
                    f"    {rule.get('explanation', 'Approved expectation')} · "
                    f"{float(rule.get('confidence', 0)):.0%} confidence"
                )
        else:
            typer.echo("  No approved contract yet.")
        typer.echo(f"\nUnapproved candidates: {len(unapproved)}")
        if unapproved:
            typer.echo(f"Review with: probezen infer {name}")
    except (ConfigError, ContractError, StorageError) as exc:
        fail(str(exc), json_output)


@app.command()
def update(
    name: str,
    count: Annotated[
        int, typer.Option(min=3, max=100, help="Fresh observations for the replacement baseline.")
    ] = 10,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Confirm the displayed drift noninteractively.")
    ] = False,
) -> None:
    """Review current drift, then explicitly relearn one endpoint's contract."""
    root = Path.cwd()
    try:
        endpoint = load_endpoint(root, name)
        current = fetch(endpoint)
        findings = [
            explain_impact(item, None)
            for item in enforce(
                current,
                load_rules(root, name),
                endpoint.expected_status,
                endpoint.ignore_paths,
            )
        ]
        if not findings:
            typer.echo(f"'{name}' still satisfies its approved contract. Nothing to update.")
            return
        print_check(name, current, findings)
        confirmed = yes or typer.confirm(
            "Accept this provider change and replace the approved baseline?"
        )
        if not confirmed:
            typer.echo("No contract changed.")
            return
        _validate_learnable(endpoint, current)
        fresh = [current]
        if count > 1:
            typer.echo(f"[1/{count}] Reusing the reviewed response as baseline evidence")
            fresh.extend(_fetch_samples(endpoint, count - 1, 0, progress_offset=1, total=count))
        clear_observations(root, name)
        for observation in fresh:
            save_observation(root, name, observation)
        observations = load_observations(root, name)
        candidates = infer_candidates(observations)
        if not candidates:
            fail("Fresh responses were not consistent enough to form a replacement contract")
        save_contract(root, name, candidates, sample_count=len(observations))
        typer.echo(
            f"\nUpdated '{name}' from {len(observations)} fresh observations.\n"
            "Review and commit probezen.lock.json."
        )
    except (ConfigError, ContractError, RequestError, StorageError) as exc:
        fail(f"Could not update '{name}'.\n\n{exc}")


@app.command()
def check(
    name: Annotated[str | None, typer.Argument(help="Check name; omit to check all.")] = None,
    all_checks: Annotated[bool, typer.Option("--all", help="Run every configured check.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit stable JSON only.")] = False,
    warnings_as_errors: Annotated[
        bool,
        typer.Option("--warnings-as-errors", help="Exit 1 when warnings are detected."),
    ] = False,
    fail_on: Annotated[
        str | None,
        typer.Option(
            "--fail-on",
            help="Lowest impact level that fails: low, medium, high, or critical.",
        ),
    ] = None,
) -> None:
    """Fetch endpoints and enforce approved behavioral contracts."""
    root = Path.cwd()
    if name is not None and all_checks:
        fail("Provide a check name or --all, but not both", json_output)
    if fail_on is not None and fail_on not in {"low", "medium", "high", "critical"}:
        fail("--fail-on must be low, medium, high, or critical", json_output)
    threshold = "low" if warnings_as_errors else fail_on
    try:
        config = load_config(root)
        names = sorted(config.get("checks", {})) if name is None or all_checks else [str(name)]
        if not names:
            fail(
                "No monitored endpoints configured; add one with 'probezen add NAME URL'",
                json_output,
            )
        discovered = discover(root).dependencies
        results = []
        any_failure = False
        for item in names:
            endpoint = load_endpoint(root, item)
            observation = fetch(endpoint)
            dependency = _dependency_for_endpoint(endpoint.url, endpoint.dependency, discovered)
            findings = [
                explain_impact(finding, dependency)
                for finding in enforce(
                    observation,
                    load_rules(root, item),
                    endpoint.expected_status,
                    endpoint.ignore_paths,
                )
            ]
            breaking = [finding for finding in findings if finding.severity == "breaking"]
            warnings = [finding for finding in findings if finding.severity == "warning"]
            failed = (
                _fails_threshold(findings, threshold) if threshold is not None else bool(breaking)
            )
            any_failure = any_failure or failed
            result = {
                "check": item,
                "dependency": dependency.id if dependency else endpoint.dependency,
                "healthy": not failed,
                "fail_on": threshold or "breaking",
                "violations": [finding.to_dict() for finding in breaking],
                "warnings": [finding.to_dict() for finding in warnings],
            }
            results.append(result)
            if not json_output:
                print_check(item, observation, findings)
        if json_output:
            payload = (
                {"schema_version": 1, **results[0]}
                if len(results) == 1
                else {
                    "schema_version": 1,
                    "checks": results,
                    "healthy": not any_failure,
                    "summary": _check_summary(results),
                }
            )
            typer.echo(json.dumps(payload, sort_keys=True))
        if any_failure:
            raise typer.Exit(1)
    except (ConfigError, ContractError, RequestError, StorageError) as exc:
        fail(str(exc), json_output)


def fail(message: str, json_output: bool = False) -> None:
    if json_output:
        typer.echo(
            json.dumps({"schema_version": 1, "error": message, "healthy": False}, sort_keys=True)
        )
    else:
        console.print(f"[red]Error:[/red] {message}")
    raise typer.Exit(2)


def parse_pairs(values: list[str], label: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            fail(f"Invalid {label} '{value}'; expected NAME=VALUE")
        name, item_value = value.split("=", 1)
        name = name.strip()
        if not name or not item_value:
            fail(f"Invalid {label} '{value}'; name and value are required")
        if name in parsed:
            fail(f"Duplicate {label} '{name}'")
        parsed[name] = item_value
    return parsed


def _collect_samples(root: Path, endpoint: Endpoint, count: int, interval: float) -> None:
    for observation in _fetch_samples(endpoint, count, interval):
        save_observation(root, endpoint.name, observation)


def _fetch_samples(
    endpoint: Endpoint,
    count: int,
    interval: float,
    *,
    progress_offset: int = 0,
    total: int | None = None,
) -> list[Observation]:
    observations = []
    progress_total = total or count
    for index in range(count):
        observation = fetch(endpoint)
        _validate_learnable(endpoint, observation)
        observations.append(observation)
        content_type = observation.content_type or "(missing)"
        typer.echo(
            f"[{index + 1 + progress_offset}/{progress_total}] "
            f"HTTP {observation.status} · {content_type} · "
            f"{observation.response_bytes} bytes"
        )
        if index + 1 < count and interval:
            time.sleep(interval)
    return observations


def _validate_learnable(endpoint: Endpoint, observation: Observation) -> None:
    if observation.status != endpoint.expected_status:
        raise RequestError(
            f"Endpoint returned HTTP {observation.status} (expected "
            f"{endpoint.expected_status}).\n\n"
            "If authentication is required, configure the entire header value through an "
            "environment variable:\n"
            f"  probezen add {endpoint.name} URL --header-env Authorization=API_AUTH_HEADER\n\n"
            "If the new status is intentional, update expected_status in probezen.yml first."
        )
    if not observation.is_json:
        raise RequestError(
            f"Endpoint returned {observation.content_type or 'a non-JSON response'}. "
            "Probezen currently learns JSON responses only."
        )


def _rule_key(rule: dict[str, Any]) -> str:
    return json.dumps(
        {"rule": rule.get("rule"), "path": rule.get("path"), "expected": rule.get("expected")},
        sort_keys=True,
    )


def _merge_inventory(root: Path, result: Any) -> None:
    data = load_config(root)
    existing = data.setdefault("dependencies", {})
    for identifier, detected in inventory_mapping(result).items():
        current = existing.get(identifier, {})
        if not isinstance(current, dict):
            current = {}
        # Preserve user-owned metadata while refreshing deterministic discovery fields.
        existing[identifier] = {**detected, **current}
        for key in (
            "hosts",
            "discovered_from",
            "monitoring",
            "risk",
            "risk_reasons",
            "assumptions",
        ):
            existing[identifier][key] = detected[key]
    configured_ids = {
        raw.get("dependency")
        for raw in data.get("checks", {}).values()
        if isinstance(raw, dict) and isinstance(raw.get("dependency"), str)
    }
    configured_hosts = {
        (urlparse(raw.get("url", "")).hostname or "").lower()
        for raw in data.get("checks", {}).values()
        if isinstance(raw, dict)
    }
    detected_ids = set(inventory_mapping(result))
    for identifier, dependency in existing.items():
        if not isinstance(dependency, dict):
            continue
        raw_hosts = dependency.get("hosts", [])
        hosts = raw_hosts if isinstance(raw_hosts, list) else []
        monitored = identifier in configured_ids or any(
            isinstance(host, str) and host in configured_hosts for host in hosts
        )
        if monitored:
            dependency["monitoring"] = "configured"
        elif identifier not in detected_ids:
            dependency["monitoring"] = "not_detected"
    save_config(root, data)


def _dependency_for_endpoint(
    url: str, configured_id: str | None, dependencies: tuple[Dependency, ...]
) -> Dependency | None:
    if configured_id:
        match = next((item for item in dependencies if item.id == configured_id), None)
        if match:
            return match
    host = (urlparse(url).hostname or "").lower()
    return next((item for item in dependencies if host in item.hosts), None)


def _check_summary(results: list[dict[str, Any]]) -> dict[str, int]:
    summary = {level: 0 for level in ("critical", "high", "medium", "low", "info")}
    for result in results:
        for finding in result["violations"] + result["warnings"]:
            summary[finding["level"]] += 1
    return summary


def _fails_threshold(findings: list[Any], threshold: str) -> bool:
    ranks = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    return any(ranks[finding.level] >= ranks[threshold] for finding in findings)


if __name__ == "__main__":
    app()
