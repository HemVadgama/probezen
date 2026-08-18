from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

RiskLevel = Literal["low", "medium", "high"]
Confidence = Literal["low", "medium", "high"]

SOURCE_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}
IGNORED_DIRECTORIES = {
    ".git",
    ".probezen",
    ".venv",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "vendor",
    "generated",
    ".next",
}
PROVIDERS: dict[str, tuple[str, str]] = {
    "api.stripe.com": ("stripe", "Stripe"),
    "api.openai.com": ("openai", "OpenAI"),
    "api.github.com": ("github", "GitHub"),
    "api.twilio.com": ("twilio", "Twilio"),
    "api.sendgrid.com": ("sendgrid", "SendGrid"),
    "maps.googleapis.com": ("google-maps", "Google Maps"),
}
SDK_PROVIDERS: dict[str, tuple[str, str, str]] = {
    "stripe": ("stripe", "Stripe", "api.stripe.com"),
    "openai": ("openai", "OpenAI", "api.openai.com"),
    "twilio": ("twilio", "Twilio", "api.twilio.com"),
    "@sendgrid/mail": ("sendgrid", "SendGrid", "api.sendgrid.com"),
    "@octokit/rest": ("github", "GitHub", "api.github.com"),
    "octokit": ("github", "GitHub", "api.github.com"),
}
URL_RE = re.compile(r"https?://([^/'\"`\s?#]+)([^'\"`\s]*)", re.IGNORECASE)
IMPORT_RE = re.compile(
    r"(?:from\s+|require\s*\(\s*|import\s*\(\s*)['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)
ENV_RE = re.compile(r"(?:process\.env\.|import\.meta\.env\.)([A-Z][A-Z0-9_]*(?:URL|HOST))")
PROPERTY = r"[A-Za-z_$][\w$]*"
CHAIN = rf"({PROPERTY}(?:\?*\.{PROPERTY})+)"


@dataclass(frozen=True)
class SourceLocation:
    path: str
    line: int
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Usage:
    field: str
    kind: str
    path: str
    line: int
    code: str
    guarded: bool
    confidence: Confidence
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Dependency:
    id: str
    name: str
    provider: str | None
    hosts: list[str] = field(default_factory=list)
    discovered_from: list[SourceLocation] = field(default_factory=list)
    usages: list[Usage] = field(default_factory=list)
    sdk: str | None = None
    version_pinned: bool = False
    validation_detected: bool = False
    fallback_detected: bool = False
    risk: RiskLevel = "low"
    risk_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "hosts": sorted(self.hosts),
            "discovered_from": [item.to_dict() for item in self.discovered_from],
            "usages": [item.to_dict() for item in self.usages],
            "sdk": self.sdk,
            "version_pinned": self.version_pinned,
            "validation_detected": self.validation_detected,
            "fallback_detected": self.fallback_detected,
            "risk": self.risk,
            "risk_reasons": self.risk_reasons,
        }


@dataclass(frozen=True)
class DiscoveryResult:
    ecosystem: str
    dependencies: tuple[Dependency, ...]
    files_scanned: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "ecosystem": self.ecosystem,
            "files_scanned": self.files_scanned,
            "dependencies": [item.to_dict() for item in self.dependencies],
        }


def discover(root: Path) -> DiscoveryResult:
    """Safely inspect JS/TS source without importing or executing the application."""
    env_hosts = _environment_hosts(root)
    dependencies: dict[str, Dependency] = {}
    scanned = 0
    ts_seen = False
    js_seen = False
    for path in _source_files(root):
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        scanned += 1
        ts_seen = ts_seen or path.suffix in {".ts", ".tsx"}
        js_seen = js_seen or path.suffix in SOURCE_SUFFIXES
        relative = path.relative_to(root).as_posix()
        matches: list[tuple[Dependency, int, str]] = []
        for match in URL_RE.finditer(text):
            host = match.group(1).lower().split(":", 1)[0]
            if _is_local(host):
                continue
            dependency = _get_dependency(dependencies, host)
            dependency.version_pinned |= bool(re.search(r"/(?:v|api/v)\d+(?:/|$)", match.group(2)))
            matches.append((dependency, _line(text, match.start()), f"URL: {host}"))
        for match in IMPORT_RE.finditer(text):
            package = match.group(1)
            provider = SDK_PROVIDERS.get(package)
            if provider is None:
                continue
            identifier, name, host = provider
            dependency = dependencies.setdefault(
                identifier, Dependency(identifier, name, identifier, [host], sdk=package)
            )
            dependency.sdk = package
            matches.append((dependency, _line(text, match.start()), f"SDK: {package}"))
        for match in ENV_RE.finditer(text):
            variable = match.group(1)
            host = env_hosts.get(variable)
            if not host or _is_local(host):
                continue
            dependency = _get_dependency(dependencies, host)
            matches.append((dependency, _line(text, match.start()), f"Environment URL: {variable}"))
        for dependency, line, evidence in matches:
            location = SourceLocation(relative, line, evidence)
            if location not in dependency.discovered_from:
                dependency.discovered_from.append(location)
            dependency.validation_detected |= bool(
                re.search(
                    r"\b(?:zod|io-ts|superstruct|valibot|ajv)\b|\.safeParse\s*\(|\.parse\s*\(", text
                )
            )
            dependency.fallback_detected |= bool(
                re.search(r"\b(?:try\s*\{|catch\s*\(|retry|fallback)\b", text, re.I)
            )
            dependency.version_pinned |= bool(re.search(r"apiVersion\s*[:=]", text))
        unique_dependencies = {dependency.id for dependency, _, _ in matches}
        for usage in analyze_usages(text, relative):
            candidates = [item for item in matches if item[1] <= usage.line]
            if len(unique_dependencies) == 1:
                target = matches[0][0]
            elif candidates:
                target = max(candidates, key=lambda item: item[1])[0]
            else:
                continue
            if usage not in target.usages:
                target.usages.append(usage)
    for dependency in dependencies.values():
        dependency.discovered_from.sort(key=lambda item: (item.path, item.line, item.evidence))
        dependency.usages.sort(key=lambda item: (item.path, item.line, item.field, item.kind))
        _assess_risk(dependency)
    ecosystem = (
        "TypeScript / Node.js" if ts_seen else "JavaScript / Node.js" if js_seen else "Unknown"
    )
    return DiscoveryResult(
        ecosystem, tuple(sorted(dependencies.values(), key=lambda x: x.id)), scanned
    )


def analyze_usages(text: str, relative_path: str) -> list[Usage]:
    usages: list[Usage] = []
    analysis_text = URL_RE.sub(lambda match: " " * len(match.group(0)), text)
    patterns: list[tuple[str, re.Pattern[str], str]] = [
        (
            "string_method",
            re.compile(CHAIN + r"\.(?:toLowerCase|toUpperCase|trim|split|replace)\s*\("),
            "called as a string without a null check",
        ),
        (
            "array_index",
            re.compile(CHAIN + r"\s*\[\s*(?:0|1)\s*\]"),
            "indexed without checking the array length",
        ),
        (
            "numeric",
            re.compile(
                rf"(?:\+=|-=|\*=|/=|[+\-*/]\s*)\s*{CHAIN}|{CHAIN}\s*(?:[+\-*/]|\+=|-=|\*=|/=)"
            ),
            "used in arithmetic",
        ),
        (
            "enum",
            re.compile(CHAIN + r"\s*(?:===|!==|==|!=)\s*['\"][^'\"]+['\"]"),
            "compared with a fixed string value",
        ),
    ]
    for kind, pattern, reason in patterns:
        for match in pattern.finditer(analysis_text):
            chain = next((group for group in match.groups() if group and "." in group), "")
            if not chain:
                continue
            field = _field_path(chain)
            line_number = _line(text, match.start())
            line_text = text.splitlines()[line_number - 1].strip()[:240]
            guarded = _is_guarded(text, match.start(), chain)
            usages.append(
                Usage(
                    field,
                    kind,
                    relative_path,
                    line_number,
                    line_text,
                    guarded,
                    "medium" if guarded else "high",
                    reason if not guarded else f"{reason}, but a nearby guard was detected",
                )
            )
    return usages


def inventory_mapping(result: DiscoveryResult) -> dict[str, Any]:
    return {
        dependency.id: {
            "name": dependency.name,
            "type": "api",
            **({"provider": dependency.provider} if dependency.provider else {}),
            "hosts": dependency.hosts,
            "discovered_from": [
                {"path": item.path, "line": item.line, "evidence": item.evidence}
                for item in dependency.discovered_from
            ],
            "monitoring": "unconfigured",
            "risk": dependency.risk,
            "risk_reasons": dependency.risk_reasons,
            "assumptions": [
                {
                    "field": usage.field,
                    "kind": usage.kind,
                    "path": usage.path,
                    "line": usage.line,
                    "guarded": usage.guarded,
                }
                for usage in dependency.usages
            ],
        }
        for dependency in result.dependencies
    }


def _source_files(root: Path) -> Iterable[Path]:
    ignore_patterns = _gitignore_patterns(root)
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        relative = path.relative_to(root)
        if any(part in IGNORED_DIRECTORIES for part in relative.parts[:-1]):
            continue
        relative_text = relative.as_posix()
        if any(fnmatch.fnmatch(relative_text, pattern) for pattern in ignore_patterns):
            continue
        if path.stat().st_size > 1_000_000:
            continue
        yield path


def _gitignore_patterns(root: Path) -> list[str]:
    path = root / ".gitignore"
    if not path.exists():
        return []
    patterns: list[str] = []
    for raw in path.read_text(errors="ignore").splitlines():
        value = raw.strip()
        if not value or value.startswith(("#", "!")):
            continue
        value = value.lstrip("/").rstrip("/")
        patterns.extend((value, f"{value}/**") if "/" not in value else (value,))
    return patterns


def _environment_hosts(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for filename in (".env.example", ".env.sample", ".env.template"):
        path = root / filename
        if not path.exists():
            continue
        for raw in path.read_text(errors="ignore").splitlines():
            if "=" not in raw or raw.lstrip().startswith("#"):
                continue
            name, value = raw.split("=", 1)
            parsed = urlparse(value.strip().strip("'\""))
            if parsed.scheme in {"http", "https"} and parsed.hostname:
                result[name.strip()] = parsed.hostname.lower()
    return result


def _get_dependency(dependencies: dict[str, Dependency], host: str) -> Dependency:
    provider, name = PROVIDERS.get(host, (_slug(host), _display_host(host)))
    dependency = dependencies.setdefault(
        provider, Dependency(provider, name, provider if host in PROVIDERS else None)
    )
    if host not in dependency.hosts:
        dependency.hosts.append(host)
    return dependency


def _assess_risk(dependency: Dependency) -> None:
    reasons: list[str] = []
    unguarded = [usage for usage in dependency.usages if not usage.guarded]
    critical = any(
        re.search(r"(?:payment|billing|checkout|order)", item.path, re.I)
        for item in dependency.discovered_from
    )
    if not dependency.version_pinned:
        reasons.append("No API version pin detected")
    if unguarded:
        reasons.append(f"{len(unguarded)} potentially fragile unguarded response assumption(s)")
    if not dependency.validation_detected:
        reasons.append("No runtime response validation detected")
    if not dependency.fallback_detected:
        reasons.append("No fallback or error-handling pattern detected")
    if critical:
        reasons.append("Used in payment, billing, checkout, or order code")
    if critical and unguarded or len(unguarded) >= 3:
        dependency.risk = "high"
    elif reasons and (unguarded or not dependency.version_pinned):
        dependency.risk = "medium"
    else:
        dependency.risk = "low"
    dependency.risk_reasons = reasons or ["No high-confidence fragile assumptions detected"]


def _field_path(chain: str) -> str:
    parts = [part.replace("?", "") for part in chain.split(".")]
    while parts and parts[0] in {"response", "data", "result", "body", "json", "payload", "res"}:
        parts.pop(0)
    return ".".join(parts)


def _is_guarded(text: str, position: int, chain: str) -> bool:
    before = text[max(0, position - 300) : position]
    optional = "?." in chain
    plain = chain.replace("?.", ".")
    last = plain.rsplit(".", 1)[-1]
    guard = re.search(rf"if\s*\([^)]*(?:{re.escape(plain)}|\.{re.escape(last)})[^)]*\)", before)
    return optional or guard is not None


def _line(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def _slug(host: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-")
    return value[:80] or "dependency"


def _display_host(host: str) -> str:
    labels = host.split(".")
    meaningful = labels[-2] if len(labels) > 1 else labels[0]
    if meaningful in {"internal", "example"} and len(labels) > 2:
        meaningful = labels[-3]
    return f"{meaningful.replace('-', ' ').title()} API"


def _is_local(host: str) -> bool:
    return host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or host.endswith(".local")
