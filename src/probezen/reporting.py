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
    console.print(f"[bold]Probezen check · {name}[/bold]\n")
    console.print(
        f"HTTP {observation.status} · {observation.content_type or '(no content type)'}\n"
    )
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
        console.print("[bold red]DRIFT DETECTED[/bold red]\n")
    else:
        console.print("[yellow]! Contract warnings detected[/yellow]\n")
    for finding in findings:
        style = "red" if finding.level in {"critical", "high"} else "yellow"
        console.print(
            f"[{style}]{finding.severity.upper()} · {finding.level.upper()}[/{style}]  "
            f"{finding.path}"
        )
        console.print(f"  expected: {display(finding.expected)}")
        console.print(f"  observed: {display(finding.actual)}")
        console.print(f"  change:   {finding_label(finding.kind)}")
        if finding.affected_code:
            console.print("\n  Likely affected:")
            for usage in finding.affected_code:
                console.print(f"  {usage['path']}:{usage['line']}")
                console.print(f"    {usage['code']}")
        console.print(f"\n  Reason: {finding.reason}")
        console.print(f"  Confidence: {finding.confidence}")
        console.print(f"  Recommended action: {finding.suggested_action}\n")
    console.print(
        f"{len(findings)} changes detected ({len(breaking)} breaking, {len(warnings)} warnings)."
    )
    if breaking:
        console.print("Exit code: 1")


def display(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def finding_label(kind: str) -> str:
    return {
        "missing_required": "Required field disappeared.",
        "type_change": "Value type changed.",
        "enum_expansion": "Observed a new enum-like value.",
        "empty_array": "Historically nonempty array became empty.",
        "nullability_change": "Historically non-null value became null.",
        "status_change": "HTTP status changed.",
        "content_type_change": "Response content type changed.",
        "json_expected": "Expected JSON but received a non-JSON response.",
        "latency_increase": "Latency exceeded the approved threshold.",
        "payload_size_increase": "Response size exceeded the approved threshold.",
    }.get(kind, "Observed behavior differs from the approved contract.")
