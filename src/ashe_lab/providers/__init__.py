"""Provider registry.

A spec names a provider with a string; this module maps that string to a class
and instantiates it lazily. Lazy instantiation matters: constructing a provider
reads credentials from the environment, and a run that only uses ``echo``
should never need an ``OPENAI_API_KEY`` to exist.

Registration is explicit rather than plugin-discovered. Import-time magic and
entry-point scanning make a repository harder for a newcomer - human or agent -
to reason about, and the cost of one line per adapter is not worth avoiding.

To add a provider, see AGENTS.md -> "Adding a model provider".
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from ..errors import RegistryError
from .anthropic_messages import AnthropicMessagesProvider
from .base import (
    BaseProvider,
    Message,
    ModelRequest,
    ModelResponse,
    Provider,
    Usage,
    messages_to_text,
)
from .echo import EchoProvider, FailingProvider
from .openai_chat import OpenAIChatProvider

#: name -> zero-argument factory. Add one line here to register an adapter.
_REGISTRY: Dict[str, Callable[[], Provider]] = {
    EchoProvider.name: EchoProvider,
    FailingProvider.name: FailingProvider,
    OpenAIChatProvider.name: OpenAIChatProvider,
    AnthropicMessagesProvider.name: AnthropicMessagesProvider,
}


def register_provider(name: str, factory: Callable[[], Provider]) -> None:
    """Register a provider factory at run time.

    Mainly for tests and for out-of-tree adapters. In-tree adapters should be
    added to ``_REGISTRY`` above so they are discoverable by reading the file.
    """
    if not name:
        raise RegistryError("provider name must be a non-empty string")
    _REGISTRY[name] = factory


def available_providers() -> List[str]:
    """Sorted list of registered provider names."""
    return sorted(_REGISTRY)


def get_provider(name: str) -> Provider:
    """Instantiate the provider registered under ``name``.

    Raises :class:`~ashe_lab.errors.RegistryError` with the list of valid names
    if it is unknown - an error an agent can act on without reading source.
    """
    factory = _REGISTRY.get(name)
    if factory is None:
        raise RegistryError(
            "unknown provider {0!r}. Registered providers: {1}. "
            "Add one in src/ashe_lab/providers/ and register it in "
            "src/ashe_lab/providers/__init__.py.".format(
                name, ", ".join(available_providers())
            )
        )
    return factory()


class ProviderCache:
    """Instantiates each provider at most once per run.

    Some adapters keep useful per-run state (the ``failing`` provider's attempt
    counter; a future adapter's HTTP connection pool), and re-reading the
    environment for every one of hundreds of trials is pointless work.
    """

    def __init__(self) -> None:
        self._instances: Dict[str, Provider] = {}

    def get(self, name: str) -> Provider:
        if name not in self._instances:
            self._instances[name] = get_provider(name)
        return self._instances[name]

    def described(self) -> Dict[str, Any]:
        """Non-secret descriptions of every provider used, for the manifest."""
        out: Dict[str, Any] = {}
        for name, instance in sorted(self._instances.items()):
            try:
                out[name] = instance.describe()
            except Exception as exc:  # pragma: no cover - defensive
                out[name] = {"provider": name, "describe_failed": str(exc)}
        return out


__all__ = [
    "AnthropicMessagesProvider",
    "BaseProvider",
    "EchoProvider",
    "FailingProvider",
    "Message",
    "ModelRequest",
    "ModelResponse",
    "OpenAIChatProvider",
    "Provider",
    "ProviderCache",
    "Usage",
    "available_providers",
    "get_provider",
    "messages_to_text",
    "register_provider",
]
