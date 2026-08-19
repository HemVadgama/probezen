from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


class ConfigError(Exception):
    pass


def is_credential_header(name: str) -> bool:
    normalized = name.strip().lower()
    return normalized in {
        "authorization",
        "cookie",
        "proxy-authorization",
        "x-api-key",
        "api-key",
    } or normalized.endswith(("-token", "-api-key", "-secret"))


@dataclass(frozen=True)
class Endpoint:
    name: str
    url: str
    method: str = "GET"
    expected_status: int = 200
    headers: dict[str, str | dict[str, str]] = field(default_factory=dict)
    query: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 10.0
    max_response_bytes: int = 2 * 1024 * 1024
    description: str | None = None
    dependency: str | None = None
    sensitive_paths: tuple[str, ...] = ()
    ignore_paths: tuple[str, ...] = ()


def config_path(root: Path) -> Path:
    override = os.environ.get("PROBEZEN_CONFIG")
    if not override:
        return root / "probezen.yml"
    path = Path(override)
    return path if path.is_absolute() else root / path


def load_config(root: Path) -> dict[str, Any]:
    path = config_path(root)
    if not path.exists():
        raise ConfigError("probezen.yml not found; run 'probezen init' first")
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Could not read probezen.yml: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ConfigError("probezen.yml must be a mapping with version: 1")
    if not isinstance(data.get("checks", {}), dict):
        raise ConfigError("probezen.yml 'checks' must be a mapping")
    if not isinstance(data.get("dependencies", {}), dict):
        raise ConfigError("probezen.yml 'dependencies' must be a mapping")
    return data


def save_config(root: Path, data: dict[str, Any]) -> None:
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def load_endpoint(root: Path, name: str) -> Endpoint:
    checks = load_config(root).get("checks", {})
    raw = checks.get(name)
    if not isinstance(raw, dict):
        available = ", ".join(sorted(checks)) or "none"
        raise ConfigError(
            f"Unknown endpoint '{name}'. Configured endpoints: {available}. "
            "Run 'probezen list' to inspect them."
        )
    url = raw.get("url")
    parsed_url = urlparse(url) if isinstance(url, str) else None
    if (
        not isinstance(url, str)
        or parsed_url is None
        or parsed_url.scheme not in {"http", "https"}
        or not parsed_url.netloc
    ):
        raise ConfigError(f"Check '{name}' must have an http(s) URL")
    method = str(raw.get("method", "GET")).upper()
    if method != "GET":
        raise ConfigError("Probezen currently supports GET endpoints only")
    try:
        endpoint = Endpoint(
            name=name,
            url=url,
            method=method,
            expected_status=int(raw.get("expected_status", 200)),
            headers=dict(raw.get("headers", {})),
            query={str(k): str(v) for k, v in dict(raw.get("query", {})).items()},
            timeout_seconds=float(raw.get("timeout_seconds", 10)),
            max_response_bytes=int(raw.get("max_response_bytes", 2 * 1024 * 1024)),
            description=raw.get("description"),
            dependency=raw.get("dependency"),
            sensitive_paths=tuple(str(value) for value in raw.get("sensitive_paths", [])),
            ignore_paths=tuple(str(value) for value in raw.get("ignore_paths", [])),
        )
        if not 100 <= endpoint.expected_status <= 599:
            raise ConfigError(f"Check '{name}' expected_status must be between 100 and 599")
        if endpoint.timeout_seconds <= 0:
            raise ConfigError(f"Check '{name}' timeout_seconds must be positive")
        if endpoint.max_response_bytes <= 0:
            raise ConfigError(f"Check '{name}' max_response_bytes must be positive")
        if endpoint.description is not None and not isinstance(endpoint.description, str):
            raise ConfigError(f"Check '{name}' description must be a string")
        if endpoint.dependency is not None and not isinstance(endpoint.dependency, str):
            raise ConfigError(f"Check '{name}' dependency must be a string")
        if not isinstance(raw.get("sensitive_paths", []), list):
            raise ConfigError(f"Check '{name}' sensitive_paths must be a list")
        if not isinstance(raw.get("ignore_paths", []), list):
            raise ConfigError(f"Check '{name}' ignore_paths must be a list")
        for header_name, header_value in endpoint.headers.items():
            if not isinstance(header_name, str) or not header_name.strip():
                raise ConfigError(f"Check '{name}' contains an invalid header name")
            if isinstance(header_value, dict):
                env_name = header_value.get("env")
                if set(header_value) != {"env"} or not isinstance(env_name, str) or not env_name:
                    raise ConfigError(
                        f"Header '{header_name}' must use a nonempty environment variable name"
                    )
            elif not isinstance(header_value, str):
                raise ConfigError(f"Header '{header_name}' must be a string or {{env: NAME}}")
        return endpoint
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid configuration for '{name}': {exc}") from exc


def resolve_headers(endpoint: Endpoint) -> dict[str, str]:
    from . import __version__

    resolved = {"User-Agent": f"Probezen/{__version__}"}
    for name, value in endpoint.headers.items():
        if isinstance(value, str):
            if is_credential_header(name):
                raise ConfigError(
                    f"Credential-like header '{name}' must use an environment variable"
                )
            resolved[name] = value
        elif isinstance(value, dict) and set(value) == {"env"}:
            env_name = value["env"]
            if not isinstance(env_name, str) or not env_name:
                raise ConfigError(f"Header '{name}' must use a nonempty environment variable name")
            secret = os.environ.get(env_name)
            if secret is None:
                raise ConfigError(f"Required environment variable '{env_name}' is not set")
            resolved[name] = secret
        else:
            raise ConfigError(f"Header '{name}' must be a string or {{env: NAME}}")
    return resolved
