from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .config import Endpoint, resolve_headers
from .models import Observation, PathMetric
from .paths import traverse


class RequestError(Exception):
    pass


def normalize_content_type(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def fetch(endpoint: Endpoint) -> Observation:
    timeout = httpx.Timeout(endpoint.timeout_seconds)
    started = time.perf_counter()
    try:
        with httpx.stream(
            "GET",
            endpoint.url,
            headers=resolve_headers(endpoint),
            params=endpoint.query,
            timeout=timeout,
            follow_redirects=False,
        ) as response:
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > endpoint.max_response_bytes:
                    raise RequestError(
                        f"Response exceeded maximum size ({endpoint.max_response_bytes} bytes)"
                    )
                chunks.append(chunk)
            body = b"".join(chunks)
    except RequestError:
        raise
    except httpx.HTTPError as exc:
        raise RequestError(f"HTTP request failed: {exc.__class__.__name__}") from exc
    latency = (time.perf_counter() - started) * 1000
    content_type = normalize_content_type(response.headers.get("content-type"))
    is_json = content_type == "application/json" or content_type.endswith("+json")
    paths: tuple[PathMetric, ...] = ()
    if is_json:
        try:
            parsed: Any = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RequestError("Response declared JSON but was malformed") from exc
        paths = traverse(parsed)
    return Observation(response.status_code, content_type, latency, size, is_json, paths)
