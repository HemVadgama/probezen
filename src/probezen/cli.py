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
    is_credential_header,
    load_config,
    load_endpoint,
    resolve_headers,
    save_config,
)
from .contract import ContractError, load_lock, load_rules, lock_path, save_contract
from .dependencies import Dependency, discover, inventory_mapping
from .http import RequestError, fetch
from .impact import explain_impact
from .infer import infer_candidates
from .reporting import print_candidates, print_check
from .storage import StorageError, connect, load_observations, save_observation

app = typer.Typer(
    no_args_is_help=True,
    help="Find external API dependencies and detect changes that could break your application.",
)
console = Console(stderr=True)


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
    """Keep external dependency changes from silently breaking your application."""


@app.command()
def init() -> None:
    """Discover dependencies and initialize Probezen in this repository."""
    root = Path.cwd()
    config = root / "probezen.yml"
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
        "Probezen configuration created or updated.\n\nNext:\n  probezen doctor\n  probezen scan"
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


@app.command()
def doctor(
    json_output: Annotated[bool, typer.Option("--json", help="Emit stable JSON only.")] = False,
) -> None:
    """Explain dependency risk before behavioral history exists."""
    root = Path.cwd()
    try:
        load_config(root)
        result = discover(root)
        summary = {level: 0 for level in ("high", "medium", "low")}
        for dependency in result.dependencies:
            summary[dependency.risk] += 1
        payload = {
            "schema_version": 1,
            "dependencies_analyzed": len(result.dependencies),
            "summary": summary,
            "dependencies": [item.to_dict() for item in result.dependencies],
        }
        if json_output:
            typer.echo(json.dumps(payload, sort_keys=True))
            return
        typer.echo("Probezen Dependency Doctor\n")
        typer.echo(f"Dependencies analyzed: {len(result.dependencies)}\n")
        for level in ("high", "medium", "low"):
            typer.echo(f"{level.upper():8} {summary[level]}")
        if not result.dependencies:
            typer.echo("\nNo supported external dependencies detected.")
            return
        highest = min(
            result.dependencies, key=lambda item: {"high": 0, "medium": 1, "low": 2}[item.risk]
        )
        typer.echo(f"\nHighest risk: {highest.name} ({highest.risk.upper()})\n\nWhy:")
        for reason in highest.risk_reasons:
            typer.echo(f"  • {reason}")
        if highest.usages:
            usage = next((item for item in highest.usages if not item.guarded), highest.usages[0])
            typer.echo(
                f"\nPotentially fragile assumption:\n\n{usage.path}:{usage.line}\n  {usage.code}"
            )
            typer.echo(f"\n{usage.field} is {usage.reason}.")
        typer.echo(
            "\nSuggested next step:\n  Configure an endpoint with 'probezen add', then sample it."
        )
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
    typer.echo(f"Added check '{name}'")


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
        for index in range(count):
            observation = fetch(endpoint)
            save_observation(root, name, observation)
            suffix = (
                "JSON structure captured"
                if observation.is_json
                else "non-JSON; structural inference skipped"
            )
            content_type = observation.content_type or "(missing)"
            typer.echo(
                f"[{index + 1}/{count}] {observation.status} {content_type} · "
                f"{observation.response_bytes} bytes · {suffix}"
            )
            if index + 1 < count and interval:
                time.sleep(interval)
    except (ConfigError, RequestError, StorageError) as exc:
        fail(str(exc))


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
        save_contract(Path.cwd(), name, selected)
        typer.echo(f"Approved {len(selected)} rules for '{name}'")
    except (StorageError, ContractError) as exc:
        fail(str(exc))


@app.command()
def check(
    name: Annotated[str | None, typer.Argument(help="Check name; omit with --all.")] = None,
    all_checks: Annotated[bool, typer.Option("--all", help="Run every configured check.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit stable JSON only.")] = False,
    warnings_as_errors: Annotated[
        bool,
        typer.Option("--warnings-as-errors", help="Exit 1 when warnings are detected."),
    ] = False,
) -> None:
    """Fetch endpoints and enforce approved behavioral contracts."""
    root = Path.cwd()
    if name is not None and all_checks:
        fail("Provide a check name or --all, but not both", json_output)
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
            failed = bool(breaking) or (warnings_as_errors and bool(warnings))
            any_failure = any_failure or failed
            result = {
                "check": item,
                "dependency": dependency.id if dependency else endpoint.dependency,
                "healthy": not failed,
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
        typer.echo(json.dumps({"error": message, "healthy": False}, sort_keys=True))
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


if __name__ == "__main__":
    app()
