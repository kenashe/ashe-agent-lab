"""Ashe Agent Lab: a small, durable framework for reproducible AI experiments.

Read ``README.md`` for orientation, ``ARCHITECTURE.md`` for how the pieces fit,
and ``AGENTS.md`` if you are a coding agent about to change something here.

Public surface, stable within a major version::

    from ashe_lab import load_spec, execute, RunOptions
    from ashe_lab.storage import load_trials, verify_run

Everything else is internal and may move.
"""

from __future__ import annotations

__version__ = "0.1.0"

#: Bump when a change would break existing stored runs or spec files.
#: Phase 0 is version 0: the format is stable enough to build on, and not yet
#: promised to be stable forever.
SCHEMA_GENERATION = 0

from .errors import (  # noqa: E402
    AsheLabError,
    IntegrityError,
    ProviderError,
    ProviderNotConfigured,
    RegistryError,
    SpecError,
    StorageError,
)
from .runner import RunOptions, RunResult, execute, plan_run  # noqa: E402
from .spec import ExperimentSpec, load_spec, parse_spec  # noqa: E402

__all__ = [
    "AsheLabError",
    "ExperimentSpec",
    "IntegrityError",
    "ProviderError",
    "ProviderNotConfigured",
    "RegistryError",
    "RunOptions",
    "RunResult",
    "SCHEMA_GENERATION",
    "SpecError",
    "StorageError",
    "__version__",
    "execute",
    "load_spec",
    "parse_spec",
    "plan_run",
]
