"""Adapter for OpenAI-compatible ``/chat/completions`` endpoints.

Deliberately targets the *compatible* shape rather than OpenAI specifically.
The same adapter serves Azure OpenAI, OpenRouter, Together, Groq, Fireworks,
vLLM, llama.cpp's server, and LM Studio - anything that speaks chat
completions - by overriding ``OPENAI_BASE_URL``. One adapter, many back ends,
which is the whole point of the boundary.

Credentials come from ``OPENAI_API_KEY`` in the environment. Nothing in this
file writes a key anywhere.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from ..errors import ProviderError, ProviderNotConfigured
from . import _http
from .base import BaseProvider, ModelRequest, ModelResponse, Usage, http_timeout

DEFAULT_BASE_URL = "https://api.openai.com/v1"

#: Response headers kept in the trial record. Non-secret, and useful for
#: diagnosing throughput problems after the fact.
_KEEP_HEADERS = (
    "x-request-id",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-requests",
)


class OpenAIChatProvider(BaseProvider):
    """Calls an OpenAI-compatible chat completions endpoint.

    Model ``options`` passthrough: any key placed in a model entry's
    ``options`` is merged into the request payload verbatim, letting you reach
    vendor-specific parameters (``reasoning_effort``, ``response_format``,
    ``logprobs``) without this adapter needing to know about them. Keys set by
    the framework take precedence, so ``options`` cannot silently override the
    model or the messages.
    """

    name = "openai_chat"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._base_url = (
            base_url or os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self._timeout = timeout if timeout is not None else http_timeout()

    def describe(self) -> Dict[str, Any]:
        return {
            "provider": self.name,
            "kind": "http-chat-completions",
            "network": True,
            "base_url": self._base_url,
            "credentials_required": True,
            # Presence only. The key itself is never recorded.
            "credentials_present": bool(self._api_key),
            "env_var": "OPENAI_API_KEY",
        }

    def complete(self, request: ModelRequest) -> ModelResponse:
        if not self._api_key:
            raise ProviderNotConfigured(
                "OPENAI_API_KEY is not set. Copy .env.example to .env and fill it in, "
                "or export the variable in your shell.",
                provider=self.name,
            )

        payload: Dict[str, Any] = dict(request.options or {})
        payload.update(
            {
                "model": request.model,
                "messages": [m.to_dict() for m in request.messages],
            }
        )
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        if request.max_output_tokens is not None:
            # Newer OpenAI reasoning models require max_completion_tokens and
            # reject max_tokens. Let a spec force either via options; default to
            # the widely supported max_tokens.
            if "max_completion_tokens" not in payload:
                payload.setdefault("max_tokens", request.max_output_tokens)
        if request.seed is not None:
            payload["seed"] = request.seed

        body, headers = _http.post_json(
            self._base_url + "/chat/completions",
            payload,
            {"Authorization": "Bearer " + self._api_key},
            self._timeout,
            provider=self.name,
        )

        choices: List[Any] = body.get("choices") or []
        if not choices:
            raise ProviderError(
                "{0} returned no choices; body keys: {1}".format(
                    self.name, sorted(body.keys())
                ),
                retryable=True,
                provider=self.name,
            )

        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message") or {}
        text = message.get("content")
        if text is None:
            # A tool-call-only or refusal response. Preserve it as empty text
            # and keep the structure in metadata rather than crashing: an empty
            # response is data, not an error.
            text = ""

        usage_raw = body.get("usage") or {}
        usage = Usage(
            input_tokens=_http.first_present(usage_raw, "prompt_tokens", "input_tokens"),
            output_tokens=_http.first_present(
                usage_raw, "completion_tokens", "output_tokens"
            ),
            total_tokens=_http.first_present(usage_raw, "total_tokens"),
            extra={
                k: v
                for k, v in usage_raw.items()
                if k
                not in (
                    "prompt_tokens",
                    "completion_tokens",
                    "total_tokens",
                    "input_tokens",
                    "output_tokens",
                )
            },
        )

        return ModelResponse(
            text=text if isinstance(text, str) else str(text),
            model_reported=body.get("model"),
            finish_reason=choice.get("finish_reason"),
            usage=usage,
            provider_request_id=body.get("id") or headers.get("x-request-id"),
            raw_metadata={
                "system_fingerprint": body.get("system_fingerprint"),
                "choice_count": len(choices),
                "tool_calls_present": bool(message.get("tool_calls")),
                "refusal": message.get("refusal"),
                "response_headers": _http.pick_headers(headers, _KEEP_HEADERS),
                "base_url": self._base_url,
            },
        )
