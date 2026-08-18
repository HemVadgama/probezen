from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

LEVELS = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
COMMANDS = {"check", "doctor", "scan", "status"}


@dataclass(frozen=True)
class Execution:
    returncode: int
    payload: dict[str, Any]
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ActionResult:
    exit_code: int
    outcome: str
    findings: int
    highest_severity: str
    result_file: Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Probezen for GitHub Actions")
    parser.add_argument("--command", default=os.environ.get("PROBEZEN_ACTION_COMMAND", "check"))
    parser.add_argument("--fail-on", default=os.environ.get("PROBEZEN_ACTION_FAIL_ON", "high"))
    parser.add_argument(
        "--config", default=os.environ.get("PROBEZEN_ACTION_CONFIG", "probezen.yml")
    )
    parser.add_argument(
        "--working-directory",
        default=os.environ.get("PROBEZEN_ACTION_WORKING_DIRECTORY", "."),
    )
    parser.add_argument("--result-file", default=os.environ.get("PROBEZEN_ACTION_RESULT_FILE"))
    args = parser.parse_args()
    try:
        result = run_action(
            command=args.command,
            fail_on=args.fail_on,
            config=args.config,
            working_directory=Path(args.working_directory),
            result_file=Path(args.result_file) if args.result_file else None,
        )
    except (OSError, ValueError) as exc:
        message = str(exc)
        emit_annotation("error", f"Probezen Action configuration error: {message}")
        append_summary(f"## Probezen\n\n❌ Action configuration error: {message}\n")
        raise SystemExit(2) from exc
    raise SystemExit(result.exit_code)


def run_action(
    *,
    command: str,
    fail_on: str,
    config: str,
    working_directory: Path,
    result_file: Path | None = None,
) -> ActionResult:
    if command not in COMMANDS:
        raise ValueError(f"command must be one of: {', '.join(sorted(COMMANDS))}")
    if fail_on not in LEVELS or fail_on == "info":
        raise ValueError("fail-on must be low, medium, high, or critical")
    if any(character in f"{config}{working_directory}" for character in ("\r", "\n")):
        raise ValueError("config and working-directory must not contain newlines")
    root = working_directory.resolve()
    if not root.is_dir():
        raise ValueError(f"working-directory does not exist: {working_directory}")
    config_path = Path(config)
    absolute_config = config_path if config_path.is_absolute() else root / config_path
    environment = dict(os.environ)
    environment["PROBEZEN_CONFIG"] = str(absolute_config)
    bootstrapped = False
    if not absolute_config.exists():
        initialized = _execute(["init"], root, environment, json_output=False)
        if initialized.returncode != 0:
            return _finish_error(initialized, root, result_file, "configuration-error")
        bootstrapped = True

    arguments = [command, "--json"]
    if command == "check":
        arguments.extend(("--fail-on", fail_on))
    execution = _execute(arguments, root, environment)
    payload = execution.payload
    error = str(payload.get("error", ""))
    setup_needed = bootstrapped or (
        execution.returncode == 2 and "No monitored endpoints configured" in error
    )
    baseline_needed = execution.returncode == 2 and "No approved contract" in error

    if setup_needed:
        outcome = "setup-required"
        exit_code = 0
        doctor = _execute(["doctor", "--json"], root, environment)
        if doctor.returncode == 0:
            payload = doctor.payload
            error = ""
            _emit_dependency_risks(payload)
        emit_annotation(
            "warning",
            "Probezen discovered this repository, but monitored endpoints still need "
            "configuration.",
        )
    elif baseline_needed:
        outcome = "baseline-required"
        exit_code = 0
        emit_annotation(
            "warning",
            "Probezen needs an approved baseline before this endpoint can be enforced.",
        )
    elif execution.returncode == 2:
        outcome = _error_outcome(error)
        exit_code = 2
        label = (
            "Dependency could not be checked"
            if outcome == "monitoring-error"
            else "Probezen failed"
        )
        emit_annotation("error", f"{label}: {error or 'unknown error'}")
    else:
        findings = list(_findings(payload))
        for finding in findings:
            emit_finding(finding, fail_on)
        if command in {"doctor", "scan"}:
            _emit_dependency_risks(payload)
        outcome = "changes-detected" if findings else "healthy"
        exit_code = execution.returncode

    findings_list = list(_findings(payload))
    highest = _highest(findings_list)
    if highest == "none":
        highest = _highest_dependency_risk(payload)
    destination = result_file or _default_result_file(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    result_payload = {
        "action_schema_version": 1,
        "command": command,
        "fail_on": fail_on,
        "outcome": outcome,
        "probezen": payload,
    }
    destination.write_text(json.dumps(result_payload, indent=2, sort_keys=True) + "\n")
    append_summary(render_summary(outcome, command, fail_on, payload, findings_list, error))
    write_outputs(outcome, len(findings_list), highest, destination)
    print(
        f"Probezen outcome: {outcome} · findings: {len(findings_list)} · "
        f"highest severity: {highest}"
    )
    if execution.stderr and exit_code == 2:
        print(execution.stderr.rstrip(), file=sys.stderr)
    return ActionResult(exit_code, outcome, len(findings_list), highest, destination)


def _execute(
    arguments: list[str],
    root: Path,
    environment: dict[str, str],
    *,
    json_output: bool = True,
) -> Execution:
    completed = subprocess.run(
        [sys.executable, "-m", "probezen.cli", *arguments],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    payload: dict[str, Any] = {}
    if json_output and completed.stdout.strip():
        try:
            decoded = json.loads(completed.stdout)
            if isinstance(decoded, dict):
                payload = decoded
        except json.JSONDecodeError:
            payload = {"error": "Probezen returned invalid JSON"}
    return Execution(completed.returncode, payload, completed.stdout, completed.stderr)


def _finish_error(
    execution: Execution,
    root: Path,
    result_file: Path | None,
    outcome: str,
) -> ActionResult:
    destination = result_file or _default_result_file(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    error = str(execution.payload.get("error", "Probezen initialization failed"))
    destination.write_text(
        json.dumps({"action_schema_version": 1, "outcome": outcome, "error": error}, indent=2)
        + "\n"
    )
    emit_annotation("error", error)
    append_summary(f"## Probezen\n\n❌ {error}\n")
    write_outputs(outcome, 0, "none", destination)
    return ActionResult(2, outcome, 0, "none", destination)


def _findings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    checks = payload.get("checks")
    results = checks if isinstance(checks, list) else [payload]
    findings: list[dict[str, Any]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        for key in ("violations", "warnings"):
            values = result.get(key, [])
            if isinstance(values, list):
                findings.extend(item for item in values if isinstance(item, dict))
    return findings


def emit_finding(finding: dict[str, Any], fail_on: str) -> None:
    level = str(finding.get("level", "medium"))
    kind = "error" if LEVELS.get(level, 0) >= LEVELS[fail_on] else "warning"
    affected = finding.get("affected_code", [])
    location = affected[0] if isinstance(affected, list) and affected else {}
    title = f"Probezen — {level.upper()} dependency change"
    message = " · ".join(
        value
        for value in (
            str(finding.get("path", "Dependency behavior changed")),
            _change(finding),
            str(finding.get("reason", "")),
        )
        if value
    )
    emit_annotation(
        kind,
        message,
        title=title,
        file=str(location.get("path", "")),
        line=location.get("line"),
    )


def _emit_dependency_risks(payload: dict[str, Any]) -> None:
    dependencies = payload.get("dependencies", [])
    if not isinstance(dependencies, list):
        return
    for dependency in dependencies:
        if not isinstance(dependency, dict) or dependency.get("risk") not in {"high", "medium"}:
            continue
        sources = dependency.get("discovered_from", [])
        location = sources[0] if isinstance(sources, list) and sources else {}
        reasons = dependency.get("risk_reasons", [])
        reason = (
            reasons[0] if isinstance(reasons, list) and reasons else "Fragile assumptions detected"
        )
        dependency_name = dependency.get("name", dependency.get("id", "dependency"))
        title = f"Probezen — {str(dependency['risk']).upper()} dependency risk: {dependency_name}"
        emit_annotation(
            "warning",
            str(reason),
            title=title,
            file=str(location.get("path", "")),
            line=location.get("line"),
        )


def emit_annotation(
    kind: str,
    message: str,
    *,
    title: str = "Probezen",
    file: str = "",
    line: Any = None,
    stream: TextIO | None = None,
) -> None:
    properties = [f"title={_command_escape(title, property_value=True)}"]
    if file:
        properties.append(f"file={_command_escape(file, property_value=True)}")
    if isinstance(line, int) and line > 0:
        properties.append(f"line={line}")
    target = stream or sys.stdout
    print(
        f"::{kind} {','.join(properties)}::{_command_escape(message)}",
        file=target,
    )


def render_summary(
    outcome: str,
    command: str,
    fail_on: str,
    payload: dict[str, Any],
    findings: list[dict[str, Any]],
    error: str,
) -> str:
    heading = "## Probezen — Catch breaking API changes before your users do\n\n"
    if command in {"doctor", "scan"} and outcome == "healthy":
        return heading + _dependency_summary(payload)
    if outcome == "healthy":
        return heading + f"✅ No actionable dependency changes detected by `{command}`.\n"
    if outcome == "setup-required":
        return (
            heading
            + "⚠️ Probezen initialized this repository, but monitoring setup is not committed "
            + "yet.\n\n"
            + _dependency_summary(payload)
            + "\n"
            + "Run `probezen init`, configure endpoints with `probezen add`, collect observations, "
            + "approve a baseline, and commit `probezen.yml` plus `probezen.lock.json`.\n"
        )
    if outcome == "baseline-required":
        return (
            heading
            + "⚠️ A monitored endpoint does not have an approved baseline.\n\n"
            + "Collect observations with `probezen sample`, review `probezen infer`, then approve "
            + "and commit the baseline. No behavioral drift was asserted.\n"
        )
    if outcome in {"monitoring-error", "configuration-error"}:
        context = (
            "The dependency could not be reached. This is a monitoring error, not confirmed drift."
            if outcome == "monitoring-error"
            else "Probezen could not validate the repository configuration."
        )
        return heading + f"❌ {context}\n\n`{_markdown(error or 'Unknown error')}`\n"
    lines = [
        heading,
        f"Detected **{len(findings)}** change(s). CI fails on **{fail_on.upper()}** or above.\n",
    ]
    for finding in findings[:20]:
        level = str(finding.get("level", "medium")).upper()
        path = _markdown(str(finding.get("path", "Dependency behavior")))
        lines.append(f"### {level} — `{path}`\n")
        lines.append(f"- Previously: `{_markdown(str(finding.get('expected', 'unknown')))}`")
        lines.append(f"- Now: `{_markdown(str(finding.get('actual', 'unknown')))}`")
        reason = str(finding.get("reason", ""))
        if reason:
            lines.append(f"- Reason: {_markdown(reason)}")
        affected = finding.get("affected_code", [])
        if isinstance(affected, list) and affected:
            location = affected[0]
            lines.append(
                f"- Likely affected: `{_markdown(str(location.get('path', '')))}:"
                f"{location.get('line', '')}`"
            )
        lines.append("")
    if len(findings) > 20:
        lines.append(
            f"_{len(findings) - 20} additional findings are available in the JSON result._"
        )
    return "\n".join(lines).rstrip() + "\n"


def append_summary(markdown: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with Path(path).open("a") as handle:
            handle.write(markdown)


def write_outputs(outcome: str, findings: int, highest: str, result_file: Path) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with Path(path).open("a") as handle:
        handle.write(f"outcome={outcome}\n")
        handle.write(f"findings={findings}\n")
        handle.write(f"highest-severity={highest}\n")
        handle.write(f"result-file={result_file}\n")


def _highest(findings: list[dict[str, Any]]) -> str:
    levels = [str(item.get("level", "medium")) for item in findings]
    return max(levels, key=lambda level: LEVELS.get(level, -1), default="none")


def _highest_dependency_risk(payload: dict[str, Any]) -> str:
    dependencies = payload.get("dependencies", [])
    if not isinstance(dependencies, list):
        return "none"
    levels = [str(item.get("risk", "low")) for item in dependencies if isinstance(item, dict)]
    return max(levels, key=lambda level: LEVELS.get(level, -1), default="none")


def _dependency_summary(payload: dict[str, Any]) -> str:
    dependencies = payload.get("dependencies", [])
    if not isinstance(dependencies, list) or not dependencies:
        return "✅ No supported external dependencies were detected.\n"
    counts = {level: 0 for level in ("high", "medium", "low")}
    for item in dependencies:
        if isinstance(item, dict) and item.get("risk") in counts:
            counts[str(item["risk"])] += 1
    lines = [
        f"Analyzed **{len(dependencies)}** external dependencies.\n",
        "| Risk | Dependencies |",
        "| --- | ---: |",
        f"| HIGH | {counts['high']} |",
        f"| MEDIUM | {counts['medium']} |",
        f"| LOW | {counts['low']} |\n",
    ]
    for item in dependencies:
        if not isinstance(item, dict) or item.get("risk") not in {"high", "medium"}:
            continue
        name = _markdown(str(item.get("name", item.get("id", "Dependency"))))
        lines.append(f"### {str(item['risk']).upper()} — {name}")
        reasons = item.get("risk_reasons", [])
        if isinstance(reasons, list):
            lines.extend(f"- {_markdown(str(reason))}" for reason in reasons[:5])
        usages = item.get("usages", [])
        if isinstance(usages, list) and usages:
            usage = usages[0]
            lines.append(
                f"- Likely fragile code: `{_markdown(str(usage.get('path', '')))}:"
                f"{usage.get('line', '')}`"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _error_outcome(error: str) -> str:
    network_markers = (
        "HTTP request failed",
        "Response exceeded maximum size",
        "Response declared JSON but was malformed",
    )
    return (
        "monitoring-error"
        if any(marker in error for marker in network_markers)
        else "configuration-error"
    )


def _default_result_file(root: Path) -> Path:
    runner_temp = os.environ.get("RUNNER_TEMP")
    return (
        Path(runner_temp) / "probezen-result.json"
        if runner_temp
        else root / ".probezen" / "action-result.json"
    )


def _change(finding: dict[str, Any]) -> str:
    expected = finding.get("expected")
    actual = finding.get("actual")
    return f"{expected} → {actual}" if expected is not None or actual is not None else ""


def _command_escape(value: str, *, property_value: bool = False) -> str:
    escaped = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if property_value:
        escaped = escaped.replace(":", "%3A").replace(",", "%2C")
    return escaped


def _markdown(value: str) -> str:
    return value.replace("|", "\\|").replace("`", "'").replace("\r", " ").replace("\n", " ")


if __name__ == "__main__":
    main()
