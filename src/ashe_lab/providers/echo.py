"""Offline deterministic providers: ``echo`` and ``failing``.

These exist so the framework is testable and demonstrable without credentials,
network access, or spend. That is not a convenience - it is load-bearing:

*   the full test suite runs in CI with no secrets;
*   a new contributor (human or agent) can execute a real end-to-end experiment
    within a minute of cloning;
*   the storage, report, and CLI layers are exercised against byte-stable
    output, so a diff in a test failure means a real regression rather than
    model variance.

``echo`` is deterministic: the same request always produces the same response,
derived by hashing the request. ``failing`` always fails, which is how the
retry and error-preservation paths get tested.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from ..errors import ProviderError
from .base import BaseProvider, Message, ModelRequest, ModelResponse, Usage

#: A small vocabulary the echo provider assembles responses from. Words are
#: chosen so that the built-in evaluators (hedging markers, keyword counts)
#: have something non-trivial to measure.
_VOCAB: List[str] = [
    "arguably",
    "the",
    "evidence",
    "suggests",
    "approximately",
    "verified",
    "sources",
    "indicate",
    "however",
    "certainly",
    "possibly",
    "records",
    "confirm",
    "estimates",
    "vary",
    "reportedly",
    "documented",
    "uncertain",
    "precisely",
    "measured",
]


def _approx_tokens(text: str) -> int:
    """Crude token estimate: ~4 characters per token.

    Deliberately crude and deliberately labelled as an estimate everywhere it
    surfaces. A real tokeniser would be a dependency; for an offline fixture
    provider the exact number carries no scientific weight.
    """
    return max(1, len(text) // 4)


class EchoProvider(BaseProvider):
    """Deterministic pseudo-model. No network, no credentials, no cost.

    The response is a function of the request alone, so runs are bit-identical
    across machines and years. Options (via a model entry's ``options``):

    ``sentences``
        How many sentences to emit. Default 3.
    ``prefix``
        Text prepended to the response. Useful for making a fixture obviously
        a fixture.
    ``echo_prompt``
        If true, append the final user message verbatim. Handy when you want a
        test to assert the prompt that was actually sent.
    """

    name = "echo"

    def describe(self) -> Dict[str, Any]:
        return {
            "provider": self.name,
            "kind": "offline-deterministic",
            "network": False,
            "credentials_required": False,
            "note": (
                "Fixture provider. Output is a hash-derived function of the "
                "request and carries no information about real model behaviour."
            ),
        }

    def complete(self, request: ModelRequest) -> ModelResponse:
        options = request.options or {}
        sentence_count = int(options.get("sentences", 3))
        prefix = str(options.get("prefix", ""))
        echo_prompt = bool(options.get("echo_prompt", False))

        seed_material = "|".join(
            [
                request.model,
                str(request.temperature),
                str(request.top_p),
                str(request.seed),
                str(request.max_output_tokens),
            ]
            + ["{0}:{1}".format(m.role, m.content) for m in request.messages]
        )
        digest = hashlib.sha256(seed_material.encode("utf-8")).digest()

        sentences: List[str] = []
        cursor = 0
        for sentence_index in range(max(1, sentence_count)):
            words: List[str] = []
            word_count = 6 + (digest[cursor % len(digest)] % 7)
            cursor += 1
            for _ in range(word_count):
                words.append(_VOCAB[digest[cursor % len(digest)] % len(_VOCAB)])
                cursor += 1
            sentence = " ".join(words)
            sentences.append(sentence[0].upper() + sentence[1:] + ".")

        text = " ".join(sentences)
        if prefix:
            text = prefix + text
        if echo_prompt:
            last_user = _last_user_message(request.messages)
            if last_user is not None:
                text = text + "\n\nPROMPT_ECHO: " + last_user

        input_text = "".join(m.content for m in request.messages)
        input_tokens = _approx_tokens(input_text)
        output_tokens = _approx_tokens(text)

        return ModelResponse(
            text=text,
            model_reported=request.model,
            finish_reason="stop",
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                extra={"token_counts_are": "estimated-4-chars-per-token"},
            ),
            provider_request_id="echo-" + hashlib.sha256(
                seed_material.encode("utf-8")
            ).hexdigest()[:16],
            raw_metadata={
                "deterministic": True,
                "request_digest": hashlib.sha256(
                    seed_material.encode("utf-8")
                ).hexdigest(),
            },
        )


class FailingProvider(BaseProvider):
    """Always raises. Used to test retry, error capture, and partial-run handling.

    Options:

    ``retryable``
        Whether the raised error is marked retryable. Default true.
    ``message``
        The error message. Default ``"synthetic failure"``.
    ``fail_times``
        Fail this many times, then succeed by delegating to ``echo``. Lets a
        test prove that retries recover AND that every attempt was recorded.
    """

    name = "failing"

    def __init__(self) -> None:
        self._attempts: Dict[str, int] = {}
        self._echo = EchoProvider()

    def describe(self) -> Dict[str, Any]:
        return {
            "provider": self.name,
            "kind": "offline-fault-injection",
            "network": False,
            "credentials_required": False,
        }

    def complete(self, request: ModelRequest) -> ModelResponse:
        options = request.options or {}
        fail_times = options.get("fail_times")
        key = request.model + "|" + "".join(m.content for m in request.messages)

        if fail_times is not None:
            seen = self._attempts.get(key, 0)
            self._attempts[key] = seen + 1
            if seen >= int(fail_times):
                return self._echo.complete(request)

        raise ProviderError(
            str(options.get("message", "synthetic failure")),
            retryable=bool(options.get("retryable", True)),
            provider=self.name,
        )


def _last_user_message(messages: List[Message]) -> Optional[str]:
    for message in reversed(messages):
        if message.role == "user":
            return message.content
    return None
