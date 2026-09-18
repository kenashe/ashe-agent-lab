"""Evaluator registry.

Mirrors the provider registry deliberately: same shape, same explicitness, so
learning one teaches you the other. A spec names an evaluator by ``type``
string; this module resolves it to a callable.

An evaluator failing must never destroy a run. A model response that cost money
and time is the valuable artifact; a metric that raised an exception is a bug
to fix later, against data you still have. So :func:`run_evaluators` catches
per-evaluator exceptions and records them as a ``None`` score with an ``error``
note, then carries on.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..errors import RegistryError
from .builtin import (
    BUILTIN_EVALUATORS,
    DEFAULT_CERTAINTY_MARKERS,
    DEFAULT_HEDGING_MARKERS,
    EvaluationContext,
    EvaluationResult,
    EvaluatorFn,
)

_REGISTRY: Dict[str, EvaluatorFn] = dict(BUILTIN_EVALUATORS)


def register_evaluator(type_name: str, fn: EvaluatorFn) -> None:
    """Register an evaluator implementation under ``type_name``."""
    if not type_name:
        raise RegistryError("evaluator type must be a non-empty string")
    _REGISTRY[type_name] = fn


def available_evaluators() -> List[str]:
    """Sorted list of registered evaluator type strings."""
    return sorted(_REGISTRY)


def get_evaluator(type_name: str) -> EvaluatorFn:
    """Resolve an evaluator type string to its implementation."""
    fn = _REGISTRY.get(type_name)
    if fn is None:
        raise RegistryError(
            "unknown evaluator type {0!r}. Registered types: {1}. "
            "Add one in src/ashe_lab/evaluators/builtin.py and register it in "
            "src/ashe_lab/evaluators/__init__.py.".format(
                type_name, ", ".join(available_evaluators())
            )
        )
    return fn


def run_evaluators(
    evaluator_specs: List[Any],
    context: EvaluationContext,
) -> Dict[str, Dict[str, Any]]:
    """Apply every evaluator to one response.

    ``evaluator_specs`` is a list of :class:`~ashe_lab.spec.EvaluatorSpec`.
    Returns ``{evaluator_id: result_dict}``. An evaluator that raises is
    recorded as a failure rather than propagating, so a metric bug cannot
    discard expensive evidence.
    """
    scores: Dict[str, Dict[str, Any]] = {}
    for evaluator_spec in evaluator_specs:
        try:
            fn = get_evaluator(evaluator_spec.type)
            result = fn(context, dict(evaluator_spec.params))
        except Exception as exc:
            scores[evaluator_spec.id] = EvaluationResult(
                value=None,
                note="evaluator raised {0}: {1}".format(type(exc).__name__, exc),
                detail={"evaluator_type": evaluator_spec.type, "failed": True},
            ).to_dict()
            continue
        payload = result.to_dict()
        payload["evaluator_type"] = evaluator_spec.type
        scores[evaluator_spec.id] = payload
    return scores


def validate_evaluator_types(evaluator_specs: List[Any]) -> List[str]:
    """Return a list of problems for unknown evaluator types.

    Called by ``ashe-lab validate`` so a bad ``type`` is caught before a run
    rather than appearing as a column of nulls afterwards.
    """
    problems: List[str] = []
    for evaluator_spec in evaluator_specs:
        if evaluator_spec.type not in _REGISTRY:
            problems.append(
                "evaluator {0!r} has unknown type {1!r}; registered types: {2}".format(
                    evaluator_spec.id, evaluator_spec.type, ", ".join(available_evaluators())
                )
            )
    return problems


__all__ = [
    "DEFAULT_CERTAINTY_MARKERS",
    "DEFAULT_HEDGING_MARKERS",
    "EvaluationContext",
    "EvaluationResult",
    "EvaluatorFn",
    "available_evaluators",
    "get_evaluator",
    "register_evaluator",
    "run_evaluators",
    "validate_evaluator_types",
]
