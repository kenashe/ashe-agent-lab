"""The provider interface: the framework's only contact point with vendors.

Everything vendor-specific in Ashe Agent Lab lives behind this boundary. The
runner constructs a :class:`ModelRequest`, hands it to a :class:`Provider`, and
receives a :class:`ModelResponse`. It never learns which company answered.

Adding a provider is therefore a closed, local task: write one class with one
method, register it, done. See AGENTS.md -> "Adding a model provider".

Why a Protocol and not a base class
-----------------------------------
A provider is a single behaviour, ``complete()``. Requiring inheritance from a
framework class would couple third-party adapters to our import graph forever.
Any object with the right method works, which also makes test doubles trivial.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:  # pragma: no cover - typing_extensions fallback for older Pythons
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover
    from typing_extensions import Protocol, runtime_checkable  # type: ignore


#: Default per-request HTTP timeout, overridable via ASHE_LAB_HTTP_TIMEOUT.
DEFAULT_TIMEOUT_SECONDS = 120.0


def http_timeout() -> float:
    """Resolve the HTTP timeout from the environment, with a sane default."""
    raw = os.environ.get("ASHE_LAB_HTTP_TIMEOUT")
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class Message:
    """One chat message. ``role`` is one of ``system``, ``user``, ``assistant``.

    A plain role/content pair is the lowest common denominator across every
    current chat API, which is exactly why it is the wire format here. Adapters
    translate it into whatever shape their vendor wants.
    """

    role: str
    content: str

    def to_dict(self) -> Dict[str, Any]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class ModelRequest:
    """Everything a provider needs to produce one completion.

    Fully serialisable: the exact request is written into ``trials.jsonl``, so
    the preserved evidence includes the literal input, not a reconstruction of
    it.
    """

    model: str
    messages: List[Message]
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_output_tokens: Optional[int] = None
    seed: Optional[int] = None
    #: Adapter-specific passthrough from the spec's ``model.options``.
    options: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "messages": [m.to_dict() for m in self.messages],
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_output_tokens": self.max_output_tokens,
            "seed": self.seed,
            "options": dict(self.options),
        }


@dataclass(frozen=True)
class Usage:
    """Token accounting, as reported by the provider.

    Every field is optional and defaults to ``None``, never ``0``. The
    distinction matters: ``0`` is a measurement, ``None`` means the provider
    did not tell us. Reports render unknowns as unknown rather than quietly
    averaging them in as zero.
    """

    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    #: Cache and reasoning token counts, where a vendor reports them.
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "extra": dict(self.extra),
        }

    @property
    def is_empty(self) -> bool:
        return (
            self.input_tokens is None
            and self.output_tokens is None
            and self.total_tokens is None
        )


@dataclass(frozen=True)
class ModelResponse:
    """One completion plus the metadata needed to audit it later."""

    text: str
    #: The model identifier the provider says actually served the request.
    #: Often more specific than what was asked for (a dated snapshot behind an
    #: alias), which makes it essential evidence for "was this the same model?".
    model_reported: Optional[str] = None
    finish_reason: Optional[str] = None
    usage: Usage = field(default_factory=Usage)
    #: Provider-side request id, where available. The thing you quote in a
    #: support ticket.
    provider_request_id: Optional[str] = None
    #: Non-secret provider metadata worth keeping (response headers of interest,
    #: system fingerprints, safety flags).
    raw_metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "model_reported": self.model_reported,
            "finish_reason": self.finish_reason,
            "usage": self.usage.to_dict(),
            "provider_request_id": self.provider_request_id,
            "raw_metadata": dict(self.raw_metadata),
        }


@runtime_checkable
class Provider(Protocol):
    """The entire contract an adapter must satisfy.

    Implementations must:

    *   expose ``name``, the string used in a spec's ``provider:`` field;
    *   implement ``complete(request) -> ModelResponse``;
    *   raise :class:`~ashe_lab.errors.ProviderError` with ``retryable`` set
        correctly on failure, and
        :class:`~ashe_lab.errors.ProviderNotConfigured` when credentials are
        absent;
    *   never log, echo, or embed credentials in a response or exception.

    Implementations must NOT retry internally: the runner owns retry policy so
    that every attempt is recorded as evidence.
    """

    name: str

    def complete(self, request: ModelRequest) -> ModelResponse:  # pragma: no cover
        ...

    def describe(self) -> Dict[str, Any]:  # pragma: no cover
        """Non-secret description of the adapter's configuration.

        Written into ``manifest.json``. Must never include an API key. Report
        credential presence as a boolean, and endpoints as bare URLs.
        """
        ...


class BaseProvider:
    """Optional convenience base. Implements ``describe()`` and nothing else.

    Inheriting is not required - see :class:`Provider`. It exists so simple
    adapters do not have to restate boilerplate.
    """

    name = "base"

    def describe(self) -> Dict[str, Any]:
        return {"provider": self.name}

    def complete(self, request: ModelRequest) -> ModelResponse:  # pragma: no cover
        raise NotImplementedError(
            "{0} must implement complete()".format(type(self).__name__)
        )


def messages_to_text(messages: List[Message]) -> str:
    """Flatten a message list into a readable transcript.

    Used by offline adapters and by report rendering. Not a wire format.
    """
    return "\n\n".join(
        "[{0}]\n{1}".format(message.role.upper(), message.content) for message in messages
    )
