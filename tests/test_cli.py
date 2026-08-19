import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from typer.testing import CliRunner

from probezen import cli
from probezen.cli import app
from probezen.http import RequestError
from probezen.models import Observation

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
    source = tmp_path / "src" / "checkout.ts"
    source.parent.mkdir()
    source.write_text(
        'fetch("https://api.vendor.example/v1/products");\n'
        "const total = response.products.price * quantity;\n"
    )
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
                "--dependency",
                "api-vendor-example",
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
        assert "monitoring: configured" in config_text
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
        type_change = next(item for item in result["violations"] if item["kind"] == "type_change")
        assert type_change["level"] == "high"
        assert type_change["affected_code"][0]["path"] == "src/checkout.ts"
        assert any(item["kind"] == "enum_expansion" for item in result["warnings"])

        Handler.body = {"products": [{"id": "p1", "price": 20, "status": "ACTIVE"}]}
        warning = runner.invoke(app, ["check", "demo", "--json"])
        assert warning.exit_code == 0
        assert json.loads(warning.stdout)["healthy"] is True
        strict = runner.invoke(app, ["check", "demo", "--json", "--warnings-as-errors"])
        assert strict.exit_code == 1
        assert json.loads(strict.stdout)["healthy"] is False
        high_threshold = runner.invoke(app, ["check", "demo", "--json", "--fail-on", "high"])
        assert high_threshold.exit_code == 0
        medium_threshold = runner.invoke(app, ["check", "demo", "--json", "--fail-on", "medium"])
        assert medium_threshold.exit_code == 1

        Handler.body = {"products": []}
        empty = runner.invoke(app, ["check", "demo", "--json"])
        assert any(item["kind"] == "empty_array" for item in json.loads(empty.stdout)["warnings"])
    finally:
        server.shutdown()
        Handler.body = {"products": [{"id": "p1", "price": 20, "status": "active"}]}


def test_version_and_usage_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert "1.1.0" in runner.invoke(app, ["--version"]).stdout
    assert runner.invoke(app, ["check", "--json"]).exit_code == 2
    assert runner.invoke(app, ["check", "--json", "--fail-on", "urgent"]).exit_code == 2


def test_demo_exercises_real_engine_without_files_or_network(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["demo"])
    assert result.exit_code == 0
    assert "HTTP 200 OK" in result.stdout
    assert "DRIFT DETECTED" in result.stdout
    assert "user.id" in result.stdout
    assert "user.email" in result.stdout
    assert "The API never went down" in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_learn_show_and_explicit_update_workflow(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/products"
    try:
        assert runner.invoke(app, ["init"]).exit_code == 0
        assert runner.invoke(app, ["add", "vendor", url]).exit_code == 0
        learned = runner.invoke(app, ["learn", "vendor", "--count", "3", "--approve-all"])
        assert learned.exit_code == 0
        assert "probezen check vendor" in learned.stdout

        shown = runner.invoke(app, ["show", "vendor", "--json"])
        payload = json.loads(shown.stdout)
        assert payload["observations"] == 3
        assert payload["learned_from_observations"] == 3
        assert payload["learned_at"].endswith("Z")
        assert payload["approved_rules"]
        assert payload["unapproved_candidates"] == []

        Handler.body = {"products": [{"id": "p1", "price": "20", "status": "active"}]}
        changed = runner.invoke(app, ["check", "vendor"])
        assert changed.exit_code == 1
        assert "DRIFT DETECTED" in changed.stdout
        assert "Value type changed" in changed.stdout

        original_lock = (tmp_path / "probezen.lock.json").read_text()
        real_fetch = cli.fetch
        fetch_count = 0

        def fail_during_relearn(endpoint):
            nonlocal fetch_count
            fetch_count += 1
            if fetch_count == 1:
                return real_fetch(endpoint)
            raise RequestError("temporary provider failure")

        monkeypatch.setattr(cli, "fetch", fail_during_relearn)
        interrupted = runner.invoke(app, ["update", "vendor", "--count", "3", "--yes"])
        assert interrupted.exit_code == 2
        assert (tmp_path / "probezen.lock.json").read_text() == original_lock
        assert (
            json.loads(runner.invoke(app, ["show", "vendor", "--json"]).stdout)["observations"] == 3
        )
        monkeypatch.setattr(cli, "fetch", real_fetch)

        updated = runner.invoke(app, ["update", "vendor", "--count", "3", "--yes"])
        assert updated.exit_code == 0
        assert "Review and commit probezen.lock.json" in updated.stdout
        assert runner.invoke(app, ["check", "vendor"]).exit_code == 0
    finally:
        server.shutdown()
        Handler.body = {"products": [{"id": "p1", "price": 20, "status": "active"}]}


def test_sampling_errors_explain_context_and_next_step(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    runner.invoke(app, ["add", "vendor", "https://example.test"])

    def malformed(_endpoint):
        raise RequestError("Response declared JSON but was malformed")

    monkeypatch.setattr(cli, "fetch", malformed)
    malformed_result = runner.invoke(app, ["sample", "vendor"])
    assert malformed_result.exit_code == 2
    assert "Could not sample 'vendor'" in malformed_result.output
    assert "malformed" in malformed_result.output
    assert "probezen doctor" in malformed_result.output

    monkeypatch.setattr(
        cli,
        "fetch",
        lambda _endpoint: Observation(401, "application/json", 1.0, 2, True, ()),
    )
    unauthorized = runner.invoke(app, ["sample", "vendor"])
    assert unauthorized.exit_code == 2
    assert "HTTP 401" in unauthorized.output
    assert "--header-env Authorization=API_AUTH_HEADER" in unauthorized.output

    monkeypatch.setattr(
        cli,
        "fetch",
        lambda _endpoint: Observation(200, "text/html", 1.0, 10, False, ()),
    )
    non_json = runner.invoke(app, ["sample", "vendor"])
    assert non_json.exit_code == 2
    assert "currently learns JSON responses only" in non_json.output


def test_malformed_contract_is_reported_without_traceback(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    runner.invoke(app, ["add", "vendor", "https://example.test"])
    (tmp_path / "probezen.lock.json").write_text(
        json.dumps(
            {
                "version": 1,
                "contracts": {
                    "vendor": {"rules": [{"rule": "invented", "path": "value", "expected": True}]}
                },
            }
        )
    )
    shown = runner.invoke(app, ["show", "vendor"])
    assert shown.exit_code == 2
    assert "unsupported kind 'invented'" in shown.output
    assert "Traceback" not in shown.output

    (tmp_path / "probezen.lock.json").write_text(
        json.dumps(
            {
                "version": 1,
                "contracts": {
                    "vendor": {
                        "rules": [
                            {
                                "rule": "type",
                                "path": "value",
                                "expected": "string",
                                "confidence": "certain",
                            }
                        ]
                    }
                },
            }
        )
    )
    invalid_metadata = runner.invoke(app, ["show", "vendor"])
    assert invalid_metadata.exit_code == 2
    assert "invalid confidence" in invalid_metadata.output
    assert "Traceback" not in invalid_metadata.output


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
    diagnosed = runner.invoke(app, ["doctor", "--json"])
    assert diagnosed.exit_code == 2
    diagnosis = json.loads(diagnosed.stdout)
    assert diagnosis["setup"]["network_requests_made"] is False
    assert diagnosis["issues"][0]["check"] == "secure"
    assert "ABSENT_TOKEN" in diagnosis["issues"][0]["message"]


def test_beginner_discovery_doctor_scan_and_status(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "src" / "payments"
    source.mkdir(parents=True)
    (tmp_path / "package.json").write_text('{"dependencies":{"stripe":"^18.0.0"}}')
    (source / "client.ts").write_text(
        """import Stripe from "stripe";
const response = await fetch("https://api.stripe.com/v1/prices");
const total = response.price * 2;
response.customer.email.toLowerCase();
"""
    )
    initialized = runner.invoke(app, ["init"])
    assert initialized.exit_code == 0
    assert "Stripe" in initialized.stdout
    config = (tmp_path / "probezen.yml").read_text()
    assert "api.stripe.com" in config
    doctor = runner.invoke(app, ["doctor", "--json"])
    report = json.loads(doctor.stdout)
    assert report["dependencies_analyzed"] == 1
    assert report["dependencies"][0]["risk"] == "high"
    assert report["dependencies"][0]["usages"][0]["path"] == "src/payments/client.ts"
    scanned = runner.invoke(app, ["scan", "--json", "--no-write"])
    assert json.loads(scanned.stdout)["ecosystem"] == "TypeScript / Node.js"
    status = json.loads(runner.invoke(app, ["status", "--json"]).stdout)
    assert status == {
        "baselined_endpoints": 0,
        "dependencies": 1,
        "monitored_endpoints": 0,
        "schema_version": 1,
        "unbaselined_endpoints": 0,
    }
