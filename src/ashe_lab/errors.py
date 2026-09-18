"""Exception hierarchy for Ashe Agent Lab.

Every failure the framework raises on purpose inherits from :class:`AsheLabError`,
so a caller (the CLI, a future web publisher, another agent's script) can
distinguish "the framework said no" from "Python blew up".
"""

from __future__ import annotations


class AsheLabError(Exception):
    """Base class for all deliberate framework errors."""


class SpecError(AsheLabError):
    """An experiment definition is malformed, invalid, or internally inconsistent."""


class ProviderError(AsheLabError):
    """A provider adapter could not fulfil a request.

    ``retryable`` tells the runner whether another attempt is worth making.
    Transport hiccups, rate limits and 5xx responses are retryable; a bad API
    key or a malformed request is not.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: object = None,
        provider: object = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.provider = provider


class ProviderNotConfigured(ProviderError):
    """A provider adapter is missing required configuration, e.g. an API key.

    Never retryable: no amount of waiting will produce a credential.
    """

    def __init__(self, message: str, *, provider: object = None) -> None:
        super().__init__(message, retryable=False, provider=provider)


class RegistryError(AsheLabError):
    """An unknown provider or evaluator name was requested."""


class StorageError(AsheLabError):
    """A run directory could not be created, or an immutability rule was violated."""


class IntegrityError(AsheLabError):
    """A stored run's contents do not match its recorded checksums."""
