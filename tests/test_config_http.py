import httpx
import pytest

from probezen.config import ConfigError, Endpoint, resolve_headers
from probezen.http import RequestError, fetch, normalize_content_type


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


def test_fetch_rejects_oversized_response(monkeypatch):
    def large(request):
        return httpx.Response(
            200, headers={"content-type": "application/json"}, content=b'{"value":"large"}'
        )

    monkeypatch.setattr(
        httpx,
        "stream",
        lambda *a, **k: httpx.Client(transport=httpx.MockTransport(large)).stream(*a, **k),
    )
    with pytest.raises(RequestError, match="maximum size"):
        fetch(Endpoint("x", "https://example.test", max_response_bytes=5))


def test_fetch_sanitizes_timeout_error_and_redacts_response_values(monkeypatch):
    def timed_out(request):
        raise httpx.ReadTimeout("secret-bearing detail", request=request)

    monkeypatch.setattr(
        httpx,
        "stream",
        lambda *a, **k: httpx.Client(transport=httpx.MockTransport(timed_out)).stream(*a, **k),
    )
    with pytest.raises(RequestError, match="ReadTimeout") as error:
        fetch(Endpoint("x", "https://example.test"))
    assert "secret-bearing" not in str(error.value)

    def response(request):
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"token": "secret", "customer": {"email": "private@example.com"}},
        )

    monkeypatch.setattr(
        httpx,
        "stream",
        lambda *a, **k: httpx.Client(transport=httpx.MockTransport(response)).stream(*a, **k),
    )
    observation = fetch(Endpoint("x", "https://example.test", sensitive_paths=("customer.email",)))
    metrics = {item.path: item for item in observation.paths}
    assert metrics["token"].values == ()
    assert metrics["customer.email"].values == ()


def test_config_does_not_contain_resolved_secret(tmp_path, monkeypatch):
    from probezen.config import load_endpoint, save_config

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
    assert "private-value" not in (tmp_path / "probezen.yml").read_text()


def test_literal_credential_header_in_manual_config_is_rejected():
    endpoint = Endpoint(
        "x",
        "https://example.test",
        headers={"X-Access-Token": "must-not-be-persisted"},
    )
    with pytest.raises(ConfigError, match="environment variable") as error:
        resolve_headers(endpoint)
    assert "must-not-be-persisted" not in str(error.value)


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("expected_status", 99, "expected_status"),
        ("timeout_seconds", 0, "timeout_seconds"),
        ("max_response_bytes", 0, "max_response_bytes"),
        ("description", 123, "description"),
        ("headers", {"Authorization": {"env": ""}}, "environment variable"),
        ("sensitive_paths", "not-a-list", "sensitive_paths"),
        ("ignore_paths", "not-a-list", "ignore_paths"),
    ],
)
def test_endpoint_bounds_are_validated(tmp_path, field, value, message):
    from probezen.config import load_endpoint, save_config

    save_config(
        tmp_path,
        {"version": 1, "checks": {"x": {"url": "https://example.test", field: value}}},
    )
    with pytest.raises(ConfigError, match=message):
        load_endpoint(tmp_path, "x")
