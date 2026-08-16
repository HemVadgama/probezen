import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from typer.testing import CliRunner

from probezen.cli import app

runner = CliRunner()


class Handler(BaseHTTPRequestHandler):
    body = {"products": [{"id": "p1", "price": 20, "status": "active"}]}
    last_path = ""
    last_token = ""

    def do_GET(self):
        Handler.last_path = self.path
        Handler.last_token = self.headers.get("X-Vendor-Token", "")
        payload = json.dumps(self.body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        pass


def test_complete_cli_workflow(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VENDOR_TOKEN", "runtime-secret")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/products"
    try:
        assert runner.invoke(app, ["init"]).exit_code == 0
        added = runner.invoke(
            app,
            [
                "add",
                "demo",
                url,
                "--header",
                "Accept=application/json",
                "--header-env",
                "X-Vendor-Token=VENDOR_TOKEN",
                "--query",
                "locale=en",
            ],
        )
        assert added.exit_code == 0
        assert runner.invoke(app, ["sample", "demo", "--count", "10"]).exit_code == 0
        assert Handler.last_path.endswith("?locale=en")
        assert Handler.last_token == "runtime-secret"
        config_text = (tmp_path / "probezen.yml").read_text()
        assert "VENDOR_TOKEN" in config_text
        assert "runtime-secret" not in config_text
        inferred = runner.invoke(app, ["infer", "demo"]).stdout
        assert "Candidate invariants" in inferred
        assert "[c001]" in inferred
        assert runner.invoke(app, ["approve", "demo", "--all"]).exit_code == 0
        assert runner.invoke(app, ["validate"]).exit_code == 0
        listed = runner.invoke(app, ["list", "--json"])
        listed_check = json.loads(listed.stdout)["checks"][0]
        assert listed_check["observations"] == 10
        assert listed_check["approved_rules"] > 0
        healthy = runner.invoke(app, ["check", "demo", "--json"])
        assert healthy.exit_code == 0
        assert json.loads(healthy.stdout)["healthy"] is True

        Handler.body = {"products": [{"id": "p1", "price": "20", "status": "ACTIVE"}]}
        changed = runner.invoke(app, ["check", "demo", "--json"])
        assert changed.exit_code == 1
        result = json.loads(changed.stdout)
        assert any(item["kind"] == "type_change" for item in result["violations"])
        assert any(item["kind"] == "enum_expansion" for item in result["warnings"])

        Handler.body = {"products": [{"id": "p1", "price": 20, "status": "ACTIVE"}]}
        warning = runner.invoke(app, ["check", "demo", "--json"])
        assert warning.exit_code == 0
        assert json.loads(warning.stdout)["healthy"] is True
        strict = runner.invoke(app, ["check", "demo", "--json", "--warnings-as-errors"])
        assert strict.exit_code == 1
        assert json.loads(strict.stdout)["healthy"] is False

        Handler.body = {"products": []}
        empty = runner.invoke(app, ["check", "demo", "--json"])
        assert any(item["kind"] == "empty_array" for item in json.loads(empty.stdout)["warnings"])
    finally:
        server.shutdown()
        Handler.body = {"products": [{"id": "p1", "price": 20, "status": "active"}]}


def test_version_and_usage_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert "0.1.0" in runner.invoke(app, ["--version"]).stdout
    assert runner.invoke(app, ["check", "--json"]).exit_code == 2


def test_add_rejects_literal_credentials_and_bad_pairs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0
    literal = runner.invoke(
        app,
        ["add", "bad", "https://example.test", "--header", "Authorization=secret"],
    )
    assert literal.exit_code == 2
    assert "--header-env" in literal.output
    malformed = runner.invoke(
        app,
        ["add", "bad", "https://example.test", "--query", "missing-value"],
    )
    assert malformed.exit_code == 2


def test_validate_reports_missing_environment_variable_without_value(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    result = runner.invoke(
        app,
        [
            "add",
            "secure",
            "https://example.test",
            "--header-env",
            "Authorization=ABSENT_TOKEN",
        ],
    )
    assert result.exit_code == 0
    validated = runner.invoke(app, ["validate", "--json"])
    assert validated.exit_code == 2
    assert "ABSENT_TOKEN" in validated.stdout
