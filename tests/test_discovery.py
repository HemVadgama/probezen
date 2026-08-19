import json

import yaml
from typer.testing import CliRunner

from probezen.cli import app
from probezen.discovery import discover_repository, starter_checks

runner = CliRunner()


def write_source(root, text, name="client.ts"):
    source = root / "src" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(text)
    return source


def test_fetch_constants_templates_methods_and_assumptions(tmp_path):
    write_source(
        tmp_path,
        """const BASE = "https://api.spotify.com/v1";
const response = await fetch(`${BASE}/tracks/${trackId}`);
const body = await response.json();
const duration = body.item.duration_ms * 2;
const first = body.item.album.images[0];
fetch("https://api.spotify.com/v1/events", {method: "POST"});
""",
    )

    result = discover_repository(tmp_path)

    calls = result.integrations[0].calls
    assert calls[0].endpoint == "/v1/events"
    assert calls[0].method == "POST"
    assert calls[0].monitoring_eligible is False
    dynamic = calls[1]
    assert dynamic.endpoint == "/v1/tracks/{trackId}"
    assert dynamic.confidence == "medium"
    assert {item.kind for item in dynamic.assumptions} == {"array_index", "numeric"}


def test_direct_axios_static_instance_and_multiple_hosts(tmp_path):
    write_source(
        tmp_path,
        """axios.get("https://api.example.com/status");
const github = axios.create({baseURL: "https://api.github.com/v1/"});
github.delete("users/42");
""",
    )

    result = discover_repository(tmp_path)

    assert [item.host for item in result.integrations] == ["api.example.com", "api.github.com"]
    github = result.integrations[1].calls[0]
    assert github.endpoint == "/v1/users/{id}"
    assert github.method == "DELETE"
    assert github.monitoring_eligible is False


def test_dynamic_local_unsupported_and_excluded_are_not_false_positives(tmp_path):
    write_source(
        tmp_path,
        """fetch(buildRuntimeUrl(account));
fetch("http://localhost:3000/internal");
got.get("https://unsupported.example/data");
const documentation = "https://docs.example/not-a-call";
const example = 'fetch("https://string.example/not-a-call")';
// fetch("https://comment.example/not-a-call");
/* axios.get("https://comment.example/not-a-call"); */
function fetch(value) { return value; }
const unrelated = response.price * 2;
""",
    )
    generated = tmp_path / "node_modules" / "package"
    generated.mkdir(parents=True)
    (generated / "index.js").write_text('fetch("https://noise.example/data")')
    build = tmp_path / "dist"
    build.mkdir()
    (build / "bundle.js").write_text('fetch("https://noise.example/data")')

    result = discover_repository(tmp_path)

    assert result.integrations == ()
    assert len(result.unresolved) == 1
    assert result.unresolved[0].expression == "buildRuntimeUrl(account)"


def test_credentials_embedded_in_url_are_redacted_and_never_written(tmp_path):
    write_source(tmp_path, 'fetch("https://actual-secret@api.example.com/status");')
    result = discover_repository(tmp_path)
    call = result.integrations[0].calls[0]
    assert call.monitoring_eligible is False
    assert call.url is None
    assert "actual-secret" not in json.dumps(result.to_dict())


def test_write_is_explicit_safe_deterministic_and_refuses_overwrite(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_source(
        tmp_path,
        """fetch("https://b.example/status?api_key=must-not-appear");
axios.put("https://a.example/resource", {enabled: true});
fetch("https://c.example/unknown", requestOptions);
fetch(dynamicUrl, {method: methodName, headers: {Authorization: token}});
""",
    )

    first = runner.invoke(app, ["discover", "--json"])
    second = runner.invoke(app, ["discover", "--json"])
    assert first.exit_code == 0
    assert first.stdout == second.stdout
    payload = json.loads(first.stdout)
    assert payload["network_requests_made"] is False
    assert "must-not-appear" not in first.stdout
    unknown = next(item for item in payload["integrations"] if item["host"] == "c.example")
    assert unknown["calls"][0]["confidence"] == "medium"
    assert unknown["calls"][0]["monitoring_eligible"] is False
    assert not (tmp_path / "probezen.yml").exists()

    written = runner.invoke(app, ["discover", "--write"])
    assert written.exit_code == 0
    config_text = (tmp_path / "probezen.yml").read_text()
    assert "must-not-appear" not in config_text
    config = yaml.safe_load(config_text)
    assert len(config["checks"]) == 1
    assert next(iter(config["checks"].values()))["method"] == "GET"

    original = config_text
    refused = runner.invoke(app, ["discover", "--write"])
    assert refused.exit_code == 2
    assert "already exists" in refused.output
    assert (tmp_path / "probezen.yml").read_text() == original


def test_starter_checks_deduplicate_identical_calls(tmp_path):
    write_source(
        tmp_path,
        """fetch("https://api.example.com/status");
fetch("https://api.example.com/status");
""",
    )
    assert len(starter_checks(discover_repository(tmp_path))) == 1


def test_no_integrations_is_success_and_malformed_source_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_source(tmp_path, 'fetch("https://broken.example/path"')
    result = runner.invoke(app, ["discover"])
    assert result.exit_code == 0
    assert "No supported third-party API integrations" in result.output
