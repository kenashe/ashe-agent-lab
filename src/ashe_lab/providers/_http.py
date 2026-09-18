"""Minimal JSON-over-HTTPS helper built on the standard library.

Why not ``requests`` or ``httpx``? Because a framework whose job is to still
run in five years should not carry dependencies it does not need, and
``urllib.request`` has been in the standard library since Python 2 and will
outlive every HTTP client library currently in fashion. The whole need here is
"POST some JSON, read some JSON, classify the error", which is forty lines.

Security note: this module never logs request bodies or headers, because
headers carry API keys. Error messages include status codes and response
bodies; response bodies from these APIs do not contain the request's
credentials.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

from ..errors import ProviderError

#: Status codes worth trying again: rate limiting, request timeout, and the
#: 5xx family. Everything else is the caller's fault and retrying is just a
#: slower way to fail.
RETRYABLE_STATUS = (408, 409, 425, 429, 500, 502, 503, 504, 529)


def post_json(
    url: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    timeout: float,
    *,
    provider: str,
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """POST ``payload`` as JSON and return ``(parsed_body, response_headers)``.

    Raises :class:`~ashe_lab.errors.ProviderError` with ``retryable`` set
    according to the HTTP status.
    """
    body = json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json", "Accept": "application/json"}
    request_headers.update(headers)

    request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            response_headers = {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as exc:
        detail = _safe_read(exc)
        raise ProviderError(
            "{0} HTTP {1}: {2}".format(provider, exc.code, detail),
            retryable=exc.code in RETRYABLE_STATUS,
            status_code=exc.code,
            provider=provider,
        )
    except urllib.error.URLError as exc:
        # DNS failure, connection refused, TLS problem, timeout. All transient
        # from our point of view, so retry is reasonable.
        raise ProviderError(
            "{0} transport error: {1}".format(provider, exc.reason),
            retryable=True,
            provider=provider,
        )
    except OSError as exc:  # socket timeout surfaces here on some versions
        raise ProviderError(
            "{0} socket error: {1}".format(provider, exc),
            retryable=True,
            provider=provider,
        )

    try:
        parsed = json.loads(raw)
    except ValueError:
        raise ProviderError(
            "{0} returned a non-JSON body (first 400 chars): {1!r}".format(
                provider, raw[:400]
            ),
            retryable=True,
            provider=provider,
        )

    if not isinstance(parsed, dict):
        raise ProviderError(
            "{0} returned JSON that is not an object: {1}".format(provider, type(parsed).__name__),
            retryable=False,
            provider=provider,
        )

    return parsed, response_headers


def _safe_read(exc: "urllib.error.HTTPError") -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")[:800]
    except Exception:  # pragma: no cover - defensive
        return "<no response body>"


def pick_headers(headers: Dict[str, str], wanted: Tuple[str, ...]) -> Dict[str, str]:
    """Extract a non-secret subset of response headers worth preserving.

    Rate-limit headers are genuinely useful when reading a run months later and
    wondering why it slowed down.
    """
    return {name: headers[name] for name in wanted if name in headers}


def first_present(mapping: Dict[str, Any], *keys: str) -> Optional[Any]:
    """Return the first key present in ``mapping``, else ``None``.

    Providers rename usage fields between API versions; this keeps adapters
    tolerant of that without branching everywhere.
    """
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None
