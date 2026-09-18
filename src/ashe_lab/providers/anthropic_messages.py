"""Adapter for the Anthropic Messages API.

Included as the second HTTP adapter for a specific reason: it has a genuinely
different request shape from chat completions - the system prompt is a
top-level field rather than a message, ``max_tokens`` is required, and content
arrives as a list of typed blocks. Supporting both from day one proves the
provider boundary is a real abstraction and not an OpenAI-shaped hole.

Credentials come from ``ANTHROPIC_API_KEY``.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from ..errors import ProviderError, ProviderNotConfigured
from . import _http
from .base import BaseProvider, ModelRequest, ModelResponse, Usage, http_timeout

DEFAULT_BASE_URL = "https://api.anthropic.com"
DEFAULT_API_VERSION = "2023-06-01"

#: The Messages API requires max_tokens. If a spec does not set one, use this
#: rather than failing - but record that a default was substituted.
FALLBACK_MAX_TOKENS = 1024

_KEEP_HEADERS = (
    "request-id",
    "anthropic-ratelimit-requests-remaining",
    "anthropic-ratelimit-tokens-remaining",
    "anthropic-ratelimit-requests-reset",
)


class AnthropicMessagesProvider(BaseProvider):
    """Calls ``POST /v1/messages``.

    Translation performed by this adapter:

    *   ``system`` role messages are hoisted out of the message list into the
        top-level ``system`` field (joined with blank lines if there are
        several), because the Messages API does not accept a system message.
    *   ``max_output_tokens`` maps to the required ``max_tokens``.
    *   Text is reassembled by concatenating ``type == "text"`` content blocks;
        non-text blocks are noted in metadata.
    """

    name = "anthropic_messages"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        api_version: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._base_url = (
            base_url or os.environ.get("ANTHROPIC_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self._api_version = (
            api_version or os.environ.get("ANTHROPIC_API_VERSION") or DEFAULT_API_VERSION
        )
        self._timeout = timeout if timeout is not None else http_timeout()

    def describe(self) -> Dict[str, Any]:
        return {
            "provider": self.name,
            "kind": "http-messages",
            "network": True,
            "base_url": self._base_url,
            "api_version": self._api_version,
            "credentials_required": True,
            "credentials_present": bool(self._api_key),
            "env_var": "ANTHROPIC_API_KEY",
        }

    def complete(self, request: ModelRequest) -> ModelResponse:
        if not self._api_key:
            raise ProviderNotConfigured(
                "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill it in, "
                "or export the variable in your shell.",
                provider=self.name,
            )

        system_parts = [m.content for m in request.messages if m.role == "system"]
        conversation = [m.to_dict() for m in request.messages if m.role != "system"]
        if not conversation:
            raise ProviderError(
                "anthropic_messages requires at least one non-system message",
                retryable=False,
                provider=self.name,
            )

        max_tokens = request.max_output_tokens
        substituted_default = max_tokens is None
        if max_tokens is None:
            max_tokens = FALLBACK_MAX_TOKENS

        payload: Dict[str, Any] = dict(request.options or {})
        payload.update(
            {
                "model": request.model,
                "messages": conversation,
                "max_tokens": max_tokens,
            }
        )
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        # The Messages API has no seed parameter. Recording that fact is more
        # useful than silently dropping the value.

        body, headers = _http.post_json(
            self._base_url + "/v1/messages",
            payload,
            {
                "x-api-key": self._api_key,
                "anthropic-version": self._api_version,
            },
            self._timeout,
            provider=self.name,
        )

        blocks: List[Any] = body.get("content") or []
        text_parts: List[str] = []
        block_types: List[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_types.append(str(block.get("type")))
            if block.get("type") == "text":
                text_parts.append(str(block.get("text", "")))

        usage_raw = body.get("usage") or {}
        input_tokens = _http.first_present(usage_raw, "input_tokens")
        output_tokens = _http.first_present(usage_raw, "output_tokens")
        total = None
        if isinstance(input_tokens, int) and isinstance(output_tokens, int):
            total = input_tokens + output_tokens

        usage = Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total,
            extra={
                k: v
                for k, v in usage_raw.items()
                if k not in ("input_tokens", "output_tokens")
            },
        )

        return ModelResponse(
            text="".join(text_parts),
            model_reported=body.get("model"),
            finish_reason=body.get("stop_reason"),
            usage=usage,
            provider_request_id=body.get("id") or headers.get("request-id"),
            raw_metadata={
                "content_block_types": block_types,
                "stop_sequence": body.get("stop_sequence"),
                "seed_unsupported": request.seed is not None,
                "max_tokens_defaulted": substituted_default,
                "response_headers": _http.pick_headers(headers, _KEEP_HEADERS),
                "base_url": self._base_url,
            },
        )
