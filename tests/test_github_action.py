import io
import json
from pathlib import Path

import pytest
import yaml

from probezen import github_action
from probezen.github_action import Execution, emit_annotation, render_summary, run_action

ROOT = Path(__file__).parents[1]


def test_marketplace_metadata_has_branding_inputs_outputs_and_stable_runner():
    metadata = yaml.safe_load((ROOT / "action.yml").read_text())
    assert metadata["name"] == "Probezen API Drift Check"
    assert metadata["description"].startswith("Catch when an API changes without going down")
    assert metadata["branding"] == {"icon": "shield", "color": "purple"}
    assert set(metadata["inputs"]) == {
        "command",
        "fail-on",
        "config",
        "working-directory",
        "upload-results",
        "artifact-name",
    }
    assert set(metadata["outputs"]) == {
        "outcome",
        "findings",
        "highest-severity",
        "result-file",
    }
    assert metadata["runs"]["using"] == "composite"
    assert list(ROOT.glob("action.y*ml")) == [ROOT / "action.yml"]


def test_annotation_escapes_commands_and_includes_location():
    output = io.StringIO()
    emit_annotation(
        "error",
        "changed 100%\ninspect",
        title="Probezen: high, change",
        file="src/client.ts",
        line=42,
        stream=output,
    )
    assert output.getvalue() == (
        "::error title=Probezen%3A high%2C change,file=src/client.ts,line=42::"
        "changed 100%25%0Ainspect\n"
    )


def test_first_run_bootstraps_and_returns_setup_guidance(tmp_path, monkeypatch):
    calls = []

    def execute(arguments, root, environment, *, json_output=True):
        calls.append(arguments)
        if arguments == ["init"]:
            (root / "probezen.yml").write_text("version: 1\nchecks: {}\n")
            return Execution(0, {}, "initialized", "")
        return Execution(
            0,
            {"schema_version": 1, "dependencies_analyzed": 2, "dependencies": []},
            '{"dependencies_analyzed": 2}',
            "",
        )

    monkeypatch.setattr(github_action, "_execute", execute)
    summary = tmp_path / "summary.md"
    outputs = tmp_path / "outputs.txt"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))

    result = run_action(
        command="check",
        fail_on="high",
        config="probezen.yml",
        working_directory=tmp_path,
    )

    assert result.exit_code == 0
    assert result.outcome == "setup-required"
    assert calls == [
        ["init"],
        ["check", "--json", "--fail-on", "high"],
        ["doctor", "--json"],
    ]
    assert "monitoring setup is not committed" in summary.read_text()
    assert "outcome=setup-required" in outputs.read_text()
    payload = json.loads(result.result_file.read_text())
    assert payload["action_schema_version"] == 1


def test_action_emits_code_annotation_and_summary_for_findings(tmp_path, monkeypatch, capsys):
    (tmp_path / "probezen.yml").write_text("version: 1\nchecks: {}\n")
    payload = {
        "schema_version": 1,
        "healthy": False,
        "violations": [
            {
                "level": "high",
                "path": "products[].price",
                "expected": "number",
                "actual": "string",
                "reason": "Used in arithmetic.",
                "affected_code": [
                    {"path": "src/cart.ts", "line": 12, "code": "total += product.price"}
                ],
            }
        ],
        "warnings": [],
    }
    monkeypatch.setattr(
        github_action,
        "_execute",
        lambda *args, **kwargs: Execution(1, payload, json.dumps(payload), ""),
    )
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    result = run_action(
        command="check",
        fail_on="high",
        config="probezen.yml",
        working_directory=tmp_path,
    )

    assert result.exit_code == 1
    assert result.outcome == "changes-detected"
    assert result.findings == 1
    assert result.highest_severity == "high"
    assert (
        "::error title=Probezen — HIGH dependency change,file=src/cart.ts,line=12::"
        in capsys.readouterr().out
    )
    assert "Likely affected: `src/cart.ts:12`" in summary.read_text()


def test_network_failure_is_not_reported_as_behavioral_drift(tmp_path, monkeypatch):
    (tmp_path / "probezen.yml").write_text("version: 1\nchecks: {}\n")
    payload = {"error": "HTTP request failed: ReadTimeout", "healthy": False}
    monkeypatch.setattr(
        github_action,
        "_execute",
        lambda *args, **kwargs: Execution(2, payload, json.dumps(payload), ""),
    )

    result = run_action(
        command="check",
        fail_on="high",
        config="probezen.yml",
        working_directory=tmp_path,
    )

    assert result.exit_code == 2
    assert result.outcome == "monitoring-error"
    assert result.findings == 0


def test_baseline_required_is_actionable_but_not_drift(tmp_path, monkeypatch):
    (tmp_path / "probezen.yml").write_text("version: 1\nchecks: {}\n")
    payload = {"error": "No approved contract for 'vendor'", "healthy": False}
    monkeypatch.setattr(
        github_action,
        "_execute",
        lambda *args, **kwargs: Execution(2, payload, json.dumps(payload), ""),
    )
    result = run_action(
        command="check",
        fail_on="high",
        config="probezen.yml",
        working_directory=tmp_path,
    )
    assert result.exit_code == 0
    assert result.outcome == "baseline-required"


@pytest.mark.parametrize("command", ["shell", "publish", ""])
def test_action_rejects_unknown_commands(tmp_path, command):
    with pytest.raises(ValueError, match="command must be one of"):
        run_action(
            command=command,
            fail_on="high",
            config="probezen.yml",
            working_directory=tmp_path,
        )


def test_summary_escapes_untrusted_markdown():
    summary = render_summary(
        "changes-detected",
        "check",
        "medium",
        {},
        [
            {
                "level": "medium",
                "path": "value`|unsafe",
                "expected": "old",
                "actual": "new",
                "affected_code": [],
            }
        ],
        "",
    )
    assert "`value'\\|unsafe`" in summary


def test_real_first_run_action_flow(tmp_path, monkeypatch):
    source = tmp_path / "src" / "client.ts"
    source.parent.mkdir()
    source.write_text(
        'fetch("https://api.vendor.example/v1/products");\nconst total = response.price * 2;\n'
    )
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "src"))

    result = run_action(
        command="check",
        fail_on="high",
        config="config/probezen.yml",
        working_directory=tmp_path,
    )

    assert result.exit_code == 0
    assert result.outcome == "setup-required"
    assert (tmp_path / "config" / "probezen.yml").exists()
    payload = json.loads(result.result_file.read_text())
    assert payload["probezen"]["dependencies_analyzed"] == 1
    assert payload["probezen"]["dependencies"][0]["risk"] == "medium"
