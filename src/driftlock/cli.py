from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

from . import __version__
from .check import enforce
from .config import ConfigError, load_config, load_endpoint, save_config
from .contract import ContractError, load_rules, lock_path, save_contract
from .http import RequestError, fetch
from .infer import infer_candidates
from .reporting import print_candidates, print_check
from .storage import StorageError, connect, load_observations, save_observation

app = typer.Typer(no_args_is_help=True, help="Detect behavioral drift in third-party APIs.")
console = Console(stderr=True)


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"driftlock {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=version_callback, is_eager=True, help="Show version."),
    ] = None,
) -> None:
    """Observe APIs, approve inferred contracts, and detect behavioral drift."""


@app.command()
def init() -> None:
    """Initialize Driftlock in the current repository."""
    root = Path.cwd()
    config = root / "driftlock.yml"
    if not config.exists():
        save_config(root, {"version": 1, "checks": {}})
    if not lock_path(root).exists():
        lock_path(root).write_text('{\n  "contracts": {},\n  "version": 1\n}\n')
    connect(root).close()
    ignore = root / ".gitignore"
    existing = ignore.read_text() if ignore.exists() else ""
    additions = [
        item for item in (".driftlock/", ".env", "*.sqlite3") if item not in existing.splitlines()
    ]
    if additions:
        ignore.write_text(
            existing
            + ("" if not existing or existing.endswith("\n") else "\n")
            + "\n".join(additions)
            + "\n"
        )
    typer.echo("Initialized Driftlock: driftlock.yml, driftlock.lock.json, .driftlock/")


@app.command("add")
def add_check(
    name: str,
    url: str,
    expected_status: Annotated[int, typer.Option(help="Expected HTTP status.")] = 200,
    timeout: Annotated[float, typer.Option(help="Request timeout in seconds.")] = 10.0,
    description: Annotated[str | None, typer.Option(help="Optional description.")] = None,
) -> None:
    """Add a JSON GET endpoint to driftlock.yml."""
    root = Path.cwd()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", name):
        fail("Check names may contain letters, numbers, underscores, and hyphens")
    try:
        data = load_config(root)
        checks = data.setdefault("checks", {})
        if name in checks:
            fail(f"Check '{name}' already exists")
        item: dict[str, Any] = {
            "url": url,
            "method": "GET",
            "expected_status": expected_status,
            "timeout_seconds": timeout,
        }
        if description:
            item["description"] = description
        checks[name] = item
        save_config(root, data)
        load_endpoint(root, name)
    except ConfigError as exc:
        fail(str(exc))
    typer.echo(f"Added check '{name}'")


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
            fail("Choose --all or one or more --candidate IDs (shown by 'driftlock infer')")
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
) -> None:
    """Fetch endpoints and enforce approved behavioral contracts."""
    root = Path.cwd()
    if (name is None) == (not all_checks):
        fail("Provide a check name or --all, but not both", json_output)
    try:
        names = sorted(load_config(root).get("checks", {})) if all_checks else [str(name)]
        results = []
        any_breaking = False
        for item in names:
            endpoint = load_endpoint(root, item)
            observation = fetch(endpoint)
            findings = enforce(observation, load_rules(root, item), endpoint.expected_status)
            breaking = [finding for finding in findings if finding.severity == "breaking"]
            warnings = [finding for finding in findings if finding.severity == "warning"]
            any_breaking = any_breaking or bool(breaking)
            result = {
                "check": item,
                "healthy": not breaking,
                "violations": [finding.to_dict() for finding in breaking],
                "warnings": [finding.to_dict() for finding in warnings],
            }
            results.append(result)
            if not json_output:
                print_check(item, observation, findings)
        if json_output:
            typer.echo(
                json.dumps(
                    results[0]
                    if len(results) == 1
                    else {"checks": results, "healthy": not any_breaking},
                    sort_keys=True,
                )
            )
        if any_breaking:
            raise typer.Exit(1)
    except (ConfigError, ContractError, RequestError, StorageError) as exc:
        fail(str(exc), json_output)


def fail(message: str, json_output: bool = False) -> None:
    if json_output:
        typer.echo(json.dumps({"error": message, "healthy": False}, sort_keys=True))
    else:
        console.print(f"[red]Error:[/red] {message}")
    raise typer.Exit(2)


if __name__ == "__main__":
    app()
