from __future__ import annotations

from typing import Any

from rich.console import Console

from .models import Candidate, Finding, Observation

console = Console()


def print_candidates(name: str, candidates: list[Candidate]) -> None:
    console.print(f"[bold]Candidate invariants · {name}[/bold]\n")
    if not candidates:
        console.print("Not enough consistent evidence yet (at least 3 observations required).")
        return
    for candidate in candidates:
        expected = candidate.expected
        if candidate.rule == "enum":
            expected = " ∈ {" + ", ".join(repr(value) for value in expected) + "}"
        else:
            expected = f" = {expected}"
        console.print(
            f"\\[{candidate.id}] [bold]{candidate.rule.upper()}[/bold]  {candidate.path}{expected}"
        )
        console.print(
            f"    Evidence: {candidate.explanation} ({candidate.confidence:.0%} confidence)\n"
        )


def print_check(name: str, observation: Observation, findings: list[Finding]) -> None:
    console.print(f"[bold]Probezen · {name}[/bold]\n")
    breaking = [item for item in findings if item.severity == "breaking"]
    warnings = [item for item in findings if item.severity == "warning"]
    if not findings:
        console.print(f"[green]✓[/green] HTTP status       {observation.status}")
        console.print(
            f"[green]✓[/green] Content type      {observation.content_type or '(missing)'}"
        )
        console.print("[green]✓[/green] Approved contract  satisfied\n")
        console.print("No contract violations detected.")
        return
    if breaking:
        console.print("[red]✗ Behavioral contract violated[/red]\n")
    else:
        console.print("[yellow]! Contract warnings detected[/yellow]\n")
    for finding in findings:
        style = "red" if finding.level in {"critical", "high"} else "yellow"
        console.print(f"[{style}]{finding.level.upper()}[/{style}] — Dependency behavior changed")
        console.print(f"\n  {finding.path}\n")
        console.print(f"  Previously: {display(finding.expected)}")
        console.print(f"  Now:        {display(finding.actual)}")
        if finding.affected_code:
            console.print("\n  Likely affected:")
            for usage in finding.affected_code:
                console.print(f"  {usage['path']}:{usage['line']}")
                console.print(f"    {usage['code']}")
        console.print(f"\n  Reason: {finding.reason}")
        console.print(f"  Confidence: {finding.confidence}")
        console.print(f"  Recommended action: {finding.suggested_action}\n")
    console.print(f"{len(breaking)} breaking changes, {len(warnings)} warnings.")


def display(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)
