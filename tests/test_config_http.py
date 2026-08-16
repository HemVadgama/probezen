import httpx
import pytest

from driftlock.config import ConfigError, Endpoint, resolve_headers
from driftlock.http import RequestError, fetch, normalize_content_type


def test_env_header_resolution_and_missing_secret(monkeypatch):
    endpoint = Endpoint(
        "x", "https://example.test", headers={"Authorization": {"env": "TEST_TOKEN"}}
    )
    monkeypatch.setenv("TEST_TOKEN", "super-secret")
    assert resolve_headers(endpoint)["Authorization"] == "super-secret"
    monkeypatch.delenv("TEST_TOKEN")
    with pytest.raises(ConfigError, match="TEST_TOKEN") as error:
        resolve_headers(endpoint)
    assert "super-secret" not in str(error.value)


def test_content_type_normalization():
    assert normalize_content_type("Application/JSON; charset=utf-8") == "application/json"


def test_fetch_malformed_json_network_and_size(monkeypatch):
    def malformed(request):
        return httpx.Response(200, headers={"content-type": "application/json"}, content=b"{")

    monkeypatch.setattr(
        httpx,
        "stream",
        lambda *a, **k: httpx.Client(transport=httpx.MockTransport(malformed)).stream(*a, **k),
    )
    with pytest.raises(RequestError, match="malformed"):
        fetch(Endpoint("x", "https://example.test"))


def test_config_does_not_contain_resolved_secret(tmp_path, monkeypatch):
    from driftlock.config import load_endpoint, save_config

    save_config(
        tmp_path,
        {
            "version": 1,
            "checks": {
                "x": {"url": "https://example.test", "headers": {"Authorization": {"env": "TOKEN"}}}
            },
        },
    )
    monkeypatch.setenv("TOKEN", "private-value")
    endpoint = load_endpoint(tmp_path, "x")
    resolve_headers(endpoint)
    assert "private-value" not in (tmp_path / "driftlock.yml").read_text()
