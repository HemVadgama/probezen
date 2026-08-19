from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin, urlparse, urlunparse

from .dependencies import (
    PROVIDERS,
    Usage,
    _display_host,
    _is_local,
    _slug,
    _source_files,
    analyze_usages,
)

Confidence = Literal["high", "medium", "unresolved"]


@dataclass(frozen=True)
class ApiCall:
    host: str
    service: str
    method: str | None
    endpoint: str | None
    url: str | None
    path: str
    line: int
    client: str
    confidence: Confidence
    evidence: str
    monitoring_eligible: bool
    monitoring_reason: str
    assumptions: tuple[Usage, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["assumptions"] = [item.to_dict() for item in self.assumptions]
        return value


@dataclass(frozen=True)
class UnresolvedCall:
    path: str
    line: int
    client: str
    expression: str
    reason: str
    confidence: Literal["unresolved"] = "unresolved"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ApiIntegration:
    host: str
    service: str
    calls: tuple[ApiCall, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "service": self.service,
            "calls": [call.to_dict() for call in self.calls],
            "monitoring_candidates": sum(call.monitoring_eligible for call in self.calls),
            "consumer_assumptions": sum(len(call.assumptions) for call in self.calls),
        }


@dataclass(frozen=True)
class RepositoryDiscovery:
    integrations: tuple[ApiIntegration, ...]
    unresolved: tuple[UnresolvedCall, ...]
    files_scanned: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "scope": "javascript-typescript-static-http-calls",
            "files_scanned": self.files_scanned,
            "network_requests_made": False,
            "integrations": [item.to_dict() for item in self.integrations],
            "unresolved": [item.to_dict() for item in self.unresolved],
        }


_CONST_START_RE = re.compile(r"\b(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=")
_AXIOS_INSTANCE_RE = re.compile(
    r"\b(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*axios\.create\s*\(\s*\{(.*?)\}\s*\)",
    re.DOTALL,
)
_BASE_URL_RE = re.compile(r"\bbaseURL\s*:\s*([^,}\n]+)")
_FETCH_RE = re.compile(r"(?<![\w$.])fetch\s*\(")
_AXIOS_METHOD_RE = re.compile(
    r"\b([A-Za-z_$][\w$]*|axios)\.(get|head|post|put|patch|delete)\s*\(", re.I
)


def discover_repository(root: Path) -> RepositoryDiscovery:
    """Statically discover conservative, call-level HTTP integrations."""
    calls: list[ApiCall] = []
    unresolved: list[UnresolvedCall] = []
    scanned = 0
    for source in _source_files(root):
        try:
            text = source.read_text(errors="ignore")
        except OSError:
            continue
        scanned += 1
        relative = source.relative_to(root).as_posix()
        code = _code_mask(text)
        constants = _constants(text, code)
        instances = _axios_instances(text, code, constants)
        detected: list[tuple[int, ApiCall]] = []

        for match in _FETCH_RE.finditer(code):
            prefix = code[max(0, match.start() - 30) : match.start()]
            if re.search(r"\bfunction\s+$", prefix):
                continue
            arguments = _call_arguments(text, match.end() - 1)
            if not arguments:
                unresolved.append(
                    _unresolved(relative, text, match.start(), "fetch", "", "missing URL argument")
                )
                continue
            expression = arguments[0]
            method = _fetch_method(arguments[1] if len(arguments) > 1 else "", constants)
            resolved = _resolve_url(expression, constants)
            call = _make_call(relative, text, match.start(), "fetch", method, resolved, expression)
            if call is None:
                if resolved is None:
                    unresolved.append(
                        _unresolved(
                            relative,
                            text,
                            match.start(),
                            "fetch",
                            expression,
                            "URL is dynamic or not locally resolvable",
                        )
                    )
            else:
                detected.append((match.start(), call))

        for match in _AXIOS_METHOD_RE.finditer(code):
            client, method = match.group(1), match.group(2).upper()
            if client != "axios" and client not in instances:
                continue
            arguments = _call_arguments(text, match.end() - 1)
            if not arguments:
                continue
            expression = arguments[0]
            resolved = _resolve_url(expression, constants, instances.get(client))
            mechanism = "axios" if client == "axios" else "axios instance"
            call = _make_call(
                relative, text, match.start(), mechanism, method, resolved, expression
            )
            if call is None:
                if resolved is None:
                    unresolved.append(
                        _unresolved(
                            relative,
                            text,
                            match.start(),
                            mechanism,
                            expression,
                            "URL is dynamic or the Axios baseURL is not statically resolvable",
                        )
                    )
            else:
                detected.append((match.start(), call))

        detected.sort(key=lambda item: item[0])
        usages = analyze_usages(text, relative)
        for index, (position, call) in enumerate(detected):
            start_line = _line(text, position)
            end_line = _line(text, detected[index + 1][0]) if index + 1 < len(detected) else 10**9
            roots = _response_roots(
                text, position, detected[index + 1][0] if index + 1 < len(detected) else len(text)
            )
            associated = tuple(
                sorted(
                    (
                        item
                        for item in usages
                        if start_line < item.line < end_line
                        and any(
                            re.search(rf"\b{re.escape(root)}(?:\?|)\.", item.code) for root in roots
                        )
                    ),
                    key=lambda item: (item.path, item.line, item.field, item.kind),
                )
            )
            calls.append(ApiCall(**{**asdict(call), "assumptions": associated}))

    calls.sort(
        key=lambda item: (item.host, item.endpoint or "", item.method or "", item.path, item.line)
    )
    unresolved.sort(key=lambda item: (item.path, item.line, item.client, item.expression))
    grouped: dict[str, list[ApiCall]] = {}
    for call in calls:
        grouped.setdefault(call.host, []).append(call)
    integrations = tuple(
        ApiIntegration(host, items[0].service, tuple(items))
        for host, items in sorted(grouped.items())
    )
    return RepositoryDiscovery(integrations, tuple(unresolved), scanned)


def starter_checks(result: RepositoryDiscovery) -> dict[str, dict[str, Any]]:
    """Return deterministic, secret-free checks supported by the current GET monitor."""
    checks: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for integration in result.integrations:
        for call in integration.calls:
            if not call.monitoring_eligible or call.url is None or call.url in seen:
                continue
            seen.add(call.url)
            path_label = (call.endpoint or "root").strip("/").replace("{", "").replace("}", "")
            base = _slug(f"{integration.host}-{path_label}")[:55]
            name = base
            suffix = 2
            while name in checks:
                name = f"{base}-{suffix}"
                suffix += 1
            checks[name] = {
                "url": call.url,
                "method": "GET",
                "expected_status": 200,
                "timeout_seconds": 10.0,
                "max_response_bytes": 2 * 1024 * 1024,
                "description": f"Discovered at {call.path}:{call.line}",
                "dependency": _provider_id(integration.host),
            }
    return checks


def starter_dependencies(result: RepositoryDiscovery) -> dict[str, dict[str, Any]]:
    dependencies: dict[str, dict[str, Any]] = {}
    for integration in result.integrations:
        identifier = _provider_id(integration.host)
        assumptions = {
            (usage.field, usage.kind, usage.path, usage.line, usage.guarded): usage
            for call in integration.calls
            for usage in call.assumptions
        }
        dependencies[identifier] = {
            "name": integration.service,
            "type": "api",
            **({"provider": identifier} if integration.host in PROVIDERS else {}),
            "hosts": [integration.host],
            "discovered_from": [
                {
                    "path": call.path,
                    "line": call.line,
                    "evidence": f"{call.client}: {call.method or 'unknown'} {call.endpoint}",
                }
                for call in integration.calls
            ],
            "monitoring": "configured"
            if any(call.monitoring_eligible for call in integration.calls)
            else "unconfigured",
            "risk": "low",
            "risk_reasons": ["Starter inventory; run probezen scan for code-risk analysis"],
            "assumptions": [
                {
                    "field": usage.field,
                    "kind": usage.kind,
                    "path": usage.path,
                    "line": usage.line,
                    "guarded": usage.guarded,
                }
                for usage in sorted(
                    assumptions.values(),
                    key=lambda item: (item.path, item.line, item.field, item.kind),
                )
            ],
        }
    return dependencies


def _constants(text: str, code: str) -> dict[str, str]:
    raw: dict[str, str] = {}
    for match in _CONST_START_RE.finditer(code):
        ends = [
            index
            for index in (code.find(";", match.end()), code.find("\n", match.end()))
            if index >= 0
        ]
        end = min(ends) if ends else len(code)
        raw[match.group(1)] = text[match.end() : end].strip()
    resolved: dict[str, str] = {}
    for _ in range(4):
        for name, expression in raw.items():
            value = _resolve_expression(expression, resolved)
            if value is not None:
                resolved[name] = value
    return resolved


def _axios_instances(text: str, code: str, constants: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for match in _AXIOS_INSTANCE_RE.finditer(code):
        original_body = text[match.start(2) : match.end(2)]
        base = _BASE_URL_RE.search(original_body)
        if base:
            value = _resolve_expression(base.group(1).strip(), constants)
            if value and _http_url(value):
                result[match.group(1)] = value
    return result


def _resolve_url(expression: str, constants: dict[str, str], base: str | None = None) -> str | None:
    value = _resolve_expression(expression.strip(), constants)
    if value is None:
        return None
    if base and not _http_url(value):
        value = urljoin(base.rstrip("/") + "/", value.lstrip("/"))
    return value if _http_url(value) else None


def _resolve_expression(expression: str, constants: dict[str, str]) -> str | None:
    expression = expression.strip().strip("()")
    if re.fullmatch(r"[A-Za-z_$][\w$]*", expression):
        return constants.get(expression, "{" + expression + "}")
    if len(expression) >= 2 and expression[0] in "'\"" and expression[-1] == expression[0]:
        return expression[1:-1]
    if len(expression) >= 2 and expression[0] == "`" and expression[-1] == "`":
        body = expression[1:-1]
        failed = False

        def replace(match: re.Match[str]) -> str:
            nonlocal failed
            inner = match.group(1).strip()
            value = constants.get(inner)
            if value is not None:
                return value
            if re.fullmatch(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*", inner):
                return "{" + inner.rsplit(".", 1)[-1] + "}"
            failed = True
            return ""

        value = re.sub(r"\$\{([^}]+)\}", replace, body)
        return None if failed else value
    parts = _split_top_level(expression, "+")
    if len(parts) > 1:
        values = [_resolve_expression(part, constants) for part in parts]
        return (
            "".join(value for value in values if value is not None)
            if all(value is not None for value in values)
            else None
        )
    return None


def _make_call(
    path: str,
    text: str,
    position: int,
    client: str,
    method: str | None,
    resolved: str | None,
    expression: str,
) -> ApiCall | None:
    if resolved is None:
        return None
    parsed = urlparse(resolved)
    host = (parsed.hostname or "").lower()
    if not host or _is_local(host):
        return None
    endpoint = _normalized_path(parsed.path)
    credentials_in_url = parsed.username is not None or parsed.password is not None
    concrete = "{" not in resolved and not credentials_in_url
    confidence: Confidence = "high" if concrete and method is not None else "medium"
    clean_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", ""))
    eligible = confidence == "high" and method == "GET"
    if credentials_in_url:
        reason = "credentials embedded in a URL are never written"
    elif method not in {"GET", "HEAD"}:
        reason = "state-changing or unknown method"
    elif method == "HEAD":
        reason = "current Probezen monitoring supports GET only"
    elif confidence != "high":
        reason = "dynamic URL cannot become a runnable probe"
    else:
        reason = "safe GET with a fully resolved URL"
    _, service = PROVIDERS.get(host, (_slug(host), _display_host(host)))
    return ApiCall(
        host,
        service,
        method,
        endpoint,
        clean_url if concrete else None,
        path,
        _line(text, position),
        client,
        confidence,
        _redact_evidence(expression),
        eligible,
        reason,
    )


def _fetch_method(options: str, constants: dict[str, str]) -> str | None:
    if not options.strip():
        return "GET"
    match = re.search(r"\bmethod\s*:\s*([^,}\n]+)", options)
    if not match:
        stripped = options.strip()
        return "GET" if stripped.startswith("{") and "..." not in stripped else None
    value = _resolve_expression(match.group(1), constants)
    return value.upper() if value and re.fullmatch(r"[A-Za-z]+", value) else None


def _response_roots(text: str, position: int, end: int) -> set[str]:
    line_start = text.rfind("\n", 0, position) + 1
    prefix = text[line_start:position]
    assigned = re.search(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:await\s+)?$", prefix)
    if not assigned:
        return set()
    roots = {assigned.group(1)}
    segment = text[position:end]
    for _ in range(3):
        for match in re.finditer(
            r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:await\s+)?"
            r"([A-Za-z_$][\w$]*)\.(?:json\s*\(\)|data\b)",
            segment,
        ):
            if match.group(2) in roots:
                roots.add(match.group(1))
    return roots


def _call_arguments(text: str, opening: int) -> list[str]:
    if opening >= len(text) or text[opening] != "(":
        return []
    depth = 0
    quote: str | None = None
    escaped = False
    start = opening + 1
    parts: list[str] = []
    for index in range(opening + 1, min(len(text), opening + 5000)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in "'\"`":
            quote = char
        elif char in "({[":
            depth += 1
        elif char in "}])":
            if char == ")" and depth == 0:
                parts.append(text[start:index].strip())
                return parts
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    return []


def _split_top_level(value: str, separator: str) -> list[str]:
    parts: list[str] = []
    quote: str | None = None
    depth = 0
    start = 0
    for index, char in enumerate(value):
        if quote:
            if char == quote and (index == 0 or value[index - 1] != "\\"):
                quote = None
        elif char in "'\"`":
            quote = char
        elif char in "({[":
            depth += 1
        elif char in ")}]":
            depth -= 1
        elif char == separator and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
    parts.append(value[start:].strip())
    return parts


def _normalized_path(path: str) -> str:
    value = path or "/"
    value = re.sub(r"/\d+(?=/|$)", "/{id}", value)
    return value


def _http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def _redact_evidence(expression: str) -> str:
    # Evidence is deliberately structural: never echo query strings, headers, or token values.
    value = re.sub(r"[?#].*", "", expression.strip().replace("\n", " "))
    value = re.sub(r"(https?://)[^/@\s]+@", r"\1<redacted>@", value, flags=re.I)
    return value[:160]


def _unresolved(
    path: str, text: str, position: int, client: str, expression: str, reason: str
) -> UnresolvedCall:
    safe = expression.strip()
    if re.search(r"token|secret|password|authorization|cookie|api[_-]?key", safe, re.I):
        safe = "<redacted expression>"
    else:
        safe = re.sub(r"(['\"])(?:\\.|(?!\1).)*\1", "<literal>", safe)[:120]
    return UnresolvedCall(path, _line(text, position), client, safe, reason)


def _line(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def _provider_id(host: str) -> str:
    return PROVIDERS.get(host, (_slug(host), ""))[0]


def _code_mask(text: str) -> str:
    """Blank comments and string contents while preserving positions and newlines."""
    output = list(text)
    index = 0
    state: str | None = None
    escaped = False
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if state in {"'", '"', "`"}:
            if char != "\n":
                output[index] = " "
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == state:
                state = None
        elif state == "line_comment":
            if char == "\n":
                state = None
            else:
                output[index] = " "
        elif state == "block_comment":
            if char == "*" and following == "/":
                output[index] = output[index + 1] = " "
                index += 1
                state = None
            elif char != "\n":
                output[index] = " "
        elif char in "'\"`":
            output[index] = " "
            state = char
        elif char == "/" and following == "/":
            output[index] = output[index + 1] = " "
            index += 1
            state = "line_comment"
        elif char == "/" and following == "*":
            output[index] = output[index + 1] = " "
            index += 1
            state = "block_comment"
        index += 1
    return "".join(output)
