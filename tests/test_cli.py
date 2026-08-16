import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from typer.testing import CliRunner

from driftlock.cli import app

runner = CliRunner()


class Handler(BaseHTTPRequestHandler):
    body = {"products": [{"id": "p1", "price": 20, "status": "active"}]}

    def do_GET(self):
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
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/products"
    try:
        assert runner.invoke(app, ["init"]).exit_code == 0
        assert runner.invoke(app, ["add", "demo", url]).exit_code == 0
        assert runner.invoke(app, ["sample", "demo", "--count", "10"]).exit_code == 0
        assert "Candidate invariants" in runner.invoke(app, ["infer", "demo"]).stdout
        assert runner.invoke(app, ["approve", "demo", "--all"]).exit_code == 0
        healthy = runner.invoke(app, ["check", "demo", "--json"])
        assert healthy.exit_code == 0
        assert json.loads(healthy.stdout)["healthy"] is True

        Handler.body = {"products": [{"id": "p1", "price": "20", "status": "ACTIVE"}]}
        changed = runner.invoke(app, ["check", "demo", "--json"])
        assert changed.exit_code == 1
        result = json.loads(changed.stdout)
        assert any(item["kind"] == "type_change" for item in result["violations"])
        assert any(item["kind"] == "enum_expansion" for item in result["warnings"])

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
