"""The experiment specification: the central contract of Ashe Agent Lab.

An experiment is a YAML file. This module turns that file into validated,
immutable Python objects, and nothing else in the framework is allowed to
reach into raw YAML dictionaries. If you want to change what an experiment can
express, this is the file you change, and ``docs/EXPERIMENT_SPEC.md`` is the
document you update alongside it.

Design commitments
------------------
*   **Provider-independent.** Nothing here knows that OpenAI or Anthropic
    exist. A model entry names a ``provider`` adapter by string; the registry
    resolves it at run time.
*   **Explicit ``kind`` discriminator.** Every spec declares
    ``kind: single_agent.v0``. Future shapes (multi-agent conversations,
    turn-based games) are added as new ``kind`` values and new parser branches,
    so old specs keep parsing forever.
*   **Fail loudly at load time.** A spec that would produce ambiguous evidence
    is rejected before a single token is spent. Validation errors accumulate so
    the author sees every problem at once, not one per run.
*   **Unknown keys are errors, not silence.** A typo'd key that was quietly
    ignored would mean an experiment that did not test what its author thought
    it tested. That is the one failure mode this framework exists to prevent.

The unit of evidence
--------------------
A *trial* is one (condition, model, item, repetition) tuple. The runner takes
the cross product of conditions x models x items x ``trials`` and executes each
resulting trial once, recording everything.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import yaml

from .errors import SpecError
from .ids import content_hash, is_slug

#: The only spec shape Phase 0 understands. See ROADMAP.md for planned kinds.
KIND_SINGLE_AGENT_V0 = "single_agent.v0"
SUPPORTED_KINDS = (KIND_SINGLE_AGENT_V0,)

#: Bumped only for breaking changes to the spec format. Additive changes
#: (new optional keys) do not bump it.
CURRENT_SCHEMA_VERSION = 0

#: Filename convention inside an experiment directory.
EXPERIMENT_FILENAME = "experiment.yaml"


# ---------------------------------------------------------------------------
# Validation plumbing
# ---------------------------------------------------------------------------


class _Validator:
    """Accumulates human-readable problems instead of raising on the first one."""

    def __init__(self) -> None:
        self.problems: List[str] = []

    def add(self, where: str, message: str) -> None:
        self.problems.append("{0}: {1}".format(where, message))

    def require_mapping(self, where: str, value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            self.add(where, "expected a mapping, got {0}".format(_typename(value)))
            return {}
        return value

    def require_list(self, where: str, value: Any) -> List[Any]:
        if not isinstance(value, list):
            self.add(where, "expected a list, got {0}".format(_typename(value)))
            return []
        return value

    def require_nonempty_str(self, where: str, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            self.add(where, "expected a non-empty string")
            return ""
        return value

    def require_slug(self, where: str, value: Any) -> str:
        if not is_slug(value):
            self.add(
                where,
                "expected a lowercase identifier of 1-64 characters matching "
                "[a-z0-9][a-z0-9._-]*[a-z0-9] (no leading or trailing "
                "punctuation, no spaces, no uppercase), got {0!r}".format(value),
            )
            return ""
        return value

    def require_positive_int(self, where: str, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            self.add(where, "expected an integer >= 1, got {0!r}".format(value))
            return 1
        return value

    def optional_number(
        self, where: str, value: Any, minimum: Optional[float] = None
    ) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self.add(where, "expected a number, got {0!r}".format(value))
            return None
        if minimum is not None and value < minimum:
            self.add(where, "expected a number >= {0}, got {1!r}".format(minimum, value))
            return None
        return float(value)

    def reject_unknown(self, where: str, mapping: Dict[str, Any], allowed: Tuple[str, ...]) -> None:
        unknown = sorted(set(mapping) - set(allowed))
        if unknown:
            self.add(
                where,
                "unknown key(s) {0}; allowed keys are {1}. "
                "Unknown keys are rejected on purpose - a silently ignored typo "
                "means an experiment that does not test what you think it tests.".format(
                    ", ".join(repr(k) for k in unknown),
                    ", ".join(sorted(allowed)),
                ),
            )

    def raise_if_problems(self, source: str) -> None:
        if self.problems:
            bullets = "\n".join("  - " + p for p in self.problems)
            raise SpecError(
                "{0} invalid ({1} problem(s)):\n{2}".format(
                    source, len(self.problems), bullets
                )
            )


def _typename(value: Any) -> str:
    return type(value).__name__


# ---------------------------------------------------------------------------
# Spec object model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetryPolicy:
    """How hard to try again when a provider call fails retryably."""

    max_attempts: int = 3
    initial_backoff_seconds: float = 1.0
    backoff_multiplier: float = 2.0
    max_backoff_seconds: float = 30.0

    ALLOWED = (
        "max_attempts",
        "initial_backoff_seconds",
        "backoff_multiplier",
        "max_backoff_seconds",
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_attempts": self.max_attempts,
            "initial_backoff_seconds": self.initial_backoff_seconds,
            "backoff_multiplier": self.backoff_multiplier,
            "max_backoff_seconds": self.max_backoff_seconds,
        }


@dataclass(frozen=True)
class SamplingDefaults:
    """Generation settings applied to every trial unless a model overrides them.

    ``seed`` is passed through to providers that support it. Note honestly that
    most hosted models are not bit-reproducible even with a fixed seed; the
    framework records what it asked for, and does not pretend the result is
    deterministic. See ARCHITECTURE.md -> "What reproducibility means here".
    """

    trials: int = 1
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_output_tokens: Optional[int] = None
    seed: Optional[int] = None
    retries: RetryPolicy = field(default_factory=RetryPolicy)

    ALLOWED = (
        "trials",
        "temperature",
        "top_p",
        "max_output_tokens",
        "seed",
        "retries",
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trials": self.trials,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_output_tokens": self.max_output_tokens,
            "seed": self.seed,
            "retries": self.retries.to_dict(),
        }


@dataclass(frozen=True)
class ModelSpec:
    """One model under test, addressed by a stable local ``alias``.

    The alias is what appears in results and reports. It stays constant while
    ``model`` changes, which is what makes "rerun this 2026 experiment against
    a 2028 model" a one-line edit rather than a reanalysis.
    """

    alias: str
    provider: str
    model: str
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_output_tokens: Optional[int] = None
    seed: Optional[int] = None
    #: Adapter-specific escape hatch, passed through verbatim. Use sparingly:
    #: anything in here is by definition not portable across providers.
    options: Dict[str, Any] = field(default_factory=dict)
    notes: Optional[str] = None

    ALLOWED = (
        "alias",
        "provider",
        "model",
        "temperature",
        "top_p",
        "max_output_tokens",
        "seed",
        "options",
        "notes",
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alias": self.alias,
            "provider": self.provider,
            "model": self.model,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_output_tokens": self.max_output_tokens,
            "seed": self.seed,
            "options": dict(self.options),
            "notes": self.notes,
        }


@dataclass(frozen=True)
class Item:
    """One stimulus presented identically across every condition.

    Holding the stimulus set separate from the conditions is what makes a
    within-item comparison possible: condition is the only thing that varies.
    ``vars`` are substituted into condition templates.
    """

    id: str
    vars: Dict[str, Any] = field(default_factory=dict)
    expected: Optional[Any] = None
    tags: List[str] = field(default_factory=list)

    ALLOWED = ("id", "vars", "expected", "tags")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "vars": dict(self.vars),
            "expected": copy.deepcopy(self.expected),
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class Condition:
    """One experimental arm: the manipulation being tested.

    ``system_prompt`` and ``user_template`` are rendered with ``{{var}}``
    placeholders drawn from the item's ``vars`` plus the experiment's
    ``variables`` block.
    """

    id: str
    user_template: str
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    #: Condition-scoped template variables. Override experiment-level ones.
    variables: Dict[str, Any] = field(default_factory=dict)
    is_control: bool = False
    tags: List[str] = field(default_factory=list)

    ALLOWED = (
        "id",
        "user_template",
        "description",
        "system_prompt",
        "variables",
        "is_control",
        "tags",
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_template": self.user_template,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "variables": dict(self.variables),
            "is_control": self.is_control,
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class EvaluatorSpec:
    """One deterministic measurement applied to every response.

    Phase 0 ships only deterministic, offline evaluators. ``type`` is resolved
    through the evaluator registry, so an ``llm_judge.*`` or ``human.*``
    evaluator can be added later without touching the spec format.
    """

    id: str
    type: str
    params: Dict[str, Any] = field(default_factory=dict)
    description: Optional[str] = None

    ALLOWED = ("id", "type", "params", "description")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "params": dict(self.params),
            "description": self.description,
        }


@dataclass(frozen=True)
class AnalysisSpec:
    """How results should be grouped and reported.

    Phase 0 computes descriptive statistics only: n, mean, median, min, max,
    standard deviation. No significance testing, on purpose - see
    ROADMAP.md Phase 3. Reporting a p-value from three trials would be worse
    than reporting nothing.
    """

    group_by: List[str] = field(default_factory=lambda: ["condition", "model"])
    report_template: str = "default"

    ALLOWED = ("group_by", "report_template")

    VALID_GROUP_KEYS = ("condition", "model", "item")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "group_by": list(self.group_by),
            "report_template": self.report_template,
        }


@dataclass(frozen=True)
class ExperimentSpec:
    """A fully validated experiment definition."""

    id: str
    title: str
    kind: str
    schema_version: int
    research_question: Optional[str]
    hypothesis: Optional[str]
    notes: Optional[str]
    tags: List[str]
    defaults: SamplingDefaults
    models: List[ModelSpec]
    items: List[Item]
    conditions: List[Condition]
    evaluators: List[EvaluatorSpec]
    analysis: AnalysisSpec
    #: Experiment-wide template variables, overridable per condition.
    variables: Dict[str, Any]
    #: Absolute path the spec was loaded from, or None if built in memory.
    source_path: Optional[str]
    #: Verbatim YAML text as loaded. Snapshotted byte-for-byte into the run
    #: directory so the run records the spec *as executed*, not as it looks
    #: after somebody edits the file next week.
    source_text: Optional[str]

    ALLOWED = (
        "id",
        "title",
        "kind",
        "schema_version",
        "research_question",
        "hypothesis",
        "notes",
        "tags",
        "defaults",
        "models",
        "items",
        "conditions",
        "evaluation",
        "analysis",
        "variables",
    )

    # -- derived views ----------------------------------------------------

    @property
    def spec_hash(self) -> str:
        """Content hash of the semantic spec, ignoring comments and formatting.

        Two runs with the same ``spec_hash`` executed the same experiment
        design. Whitespace and comment edits do not change it; a changed prompt
        does.
        """
        return content_hash(self.to_dict())

    def model_by_alias(self, alias: str) -> ModelSpec:
        for model in self.models:
            if model.alias == alias:
                return model
        raise SpecError("no model with alias {0!r} in experiment {1!r}".format(alias, self.id))

    def condition_by_id(self, condition_id: str) -> Condition:
        for condition in self.conditions:
            if condition.id == condition_id:
                return condition
        raise SpecError(
            "no condition with id {0!r} in experiment {1!r}".format(condition_id, self.id)
        )

    @property
    def control_condition(self) -> Optional[Condition]:
        """The condition flagged ``is_control: true``, if any."""
        for condition in self.conditions:
            if condition.is_control:
                return condition
        return None

    def planned_trial_count(self) -> int:
        """Total trials a full run will execute."""
        return (
            len(self.conditions) * len(self.models) * len(self.items) * self.defaults.trials
        )

    def to_dict(self) -> Dict[str, Any]:
        """Semantic representation, used for hashing and for ``manifest.json``."""
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "schema_version": self.schema_version,
            "research_question": self.research_question,
            "hypothesis": self.hypothesis,
            "notes": self.notes,
            "tags": list(self.tags),
            "variables": dict(self.variables),
            "defaults": self.defaults.to_dict(),
            "models": [m.to_dict() for m in self.models],
            "items": [i.to_dict() for i in self.items],
            "conditions": [c.to_dict() for c in self.conditions],
            "evaluators": [e.to_dict() for e in self.evaluators],
            "analysis": self.analysis.to_dict(),
        }


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_spec(path: str) -> ExperimentSpec:
    """Load and validate an experiment from a YAML file or experiment directory.

    ``path`` may be the YAML file itself, or a directory containing
    ``experiment.yaml``.
    """
    resolved = resolve_spec_path(path)
    try:
        with open(resolved, "r", encoding="utf-8") as handle:
            source_text = handle.read()
    except OSError as exc:
        raise SpecError("cannot read experiment file {0}: {1}".format(resolved, exc))

    try:
        raw = yaml.safe_load(source_text)
    except yaml.YAMLError as exc:
        raise SpecError("{0} is not valid YAML: {1}".format(resolved, exc))

    if raw is None:
        raise SpecError("{0} is empty".format(resolved))

    return parse_spec(raw, source_path=resolved, source_text=source_text)


def resolve_spec_path(path: str) -> str:
    """Turn a file path, directory path, or experiment id into a YAML file path.

    Resolution order:

    1. ``path`` is an existing file -> use it.
    2. ``path`` is a directory -> ``path/experiment.yaml``.
    3. ``path`` looks like a bare experiment id -> ``experiments/<id>/experiment.yaml``.
    """
    if os.path.isfile(path):
        return os.path.abspath(path)
    if os.path.isdir(path):
        candidate = os.path.join(path, EXPERIMENT_FILENAME)
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
        raise SpecError(
            "directory {0} contains no {1}".format(path, EXPERIMENT_FILENAME)
        )
    candidate = os.path.join("experiments", path, EXPERIMENT_FILENAME)
    if os.path.isfile(candidate):
        return os.path.abspath(candidate)
    raise SpecError(
        "cannot find an experiment at {0!r}. Tried: the path itself, "
        "{0}/{1}, and experiments/{0}/{1}.".format(path, EXPERIMENT_FILENAME)
    )


def parse_spec(
    raw: Any,
    *,
    source_path: Optional[str] = None,
    source_text: Optional[str] = None,
) -> ExperimentSpec:
    """Validate a raw mapping and build an :class:`ExperimentSpec`.

    Raises :class:`SpecError` listing every problem found.
    """
    source = source_path or "<in-memory spec>"
    v = _Validator()

    root = v.require_mapping("root", raw)
    v.raise_if_problems(source)  # nothing else makes sense if the root is wrong

    v.reject_unknown("root", root, ExperimentSpec.ALLOWED)

    kind = root.get("kind", KIND_SINGLE_AGENT_V0)
    if kind not in SUPPORTED_KINDS:
        v.add(
            "root.kind",
            "unsupported kind {0!r}. This build understands: {1}. "
            "A newer spec kind means you need a newer Ashe Agent Lab.".format(
                kind, ", ".join(SUPPORTED_KINDS)
            ),
        )

    schema_version = root.get("schema_version", CURRENT_SCHEMA_VERSION)
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        v.add("root.schema_version", "expected an integer")
        schema_version = CURRENT_SCHEMA_VERSION
    elif schema_version > CURRENT_SCHEMA_VERSION:
        v.add(
            "root.schema_version",
            "spec declares version {0} but this build supports up to {1}".format(
                schema_version, CURRENT_SCHEMA_VERSION
            ),
        )

    experiment_id = v.require_slug("root.id", root.get("id"))
    title = v.require_nonempty_str("root.title", root.get("title"))

    tags = _string_list(v, "root.tags", root.get("tags", []))
    variables = v.require_mapping("root.variables", root.get("variables", {}))

    defaults = _parse_defaults(v, root.get("defaults", {}))
    models = _parse_models(v, root.get("models"))
    items = _parse_items(v, root.get("items"))
    conditions = _parse_conditions(v, root.get("conditions"))
    evaluators = _parse_evaluation(v, root.get("evaluation", {}))
    analysis = _parse_analysis(v, root.get("analysis", {}))

    # Cross-cutting checks -------------------------------------------------
    controls = [c for c in conditions if c.is_control]
    if len(controls) > 1:
        v.add(
            "root.conditions",
            "more than one condition is flagged is_control: {0}".format(
                ", ".join(c.id for c in controls)
            ),
        )

    _check_templates(v, conditions, items, variables)

    v.raise_if_problems(source)

    return ExperimentSpec(
        id=experiment_id,
        title=title,
        kind=kind,
        schema_version=schema_version,
        research_question=_optional_str(root.get("research_question")),
        hypothesis=_optional_str(root.get("hypothesis")),
        notes=_optional_str(root.get("notes")),
        tags=tags,
        defaults=defaults,
        models=models,
        items=items,
        conditions=conditions,
        evaluators=evaluators,
        analysis=analysis,
        variables=dict(variables),
        source_path=source_path,
        source_text=source_text,
    )


# ---------------------------------------------------------------------------
# Section parsers
# ---------------------------------------------------------------------------


def _parse_defaults(v: _Validator, raw: Any) -> SamplingDefaults:
    mapping = v.require_mapping("root.defaults", raw)
    v.reject_unknown("root.defaults", mapping, SamplingDefaults.ALLOWED)

    retries_raw = v.require_mapping("root.defaults.retries", mapping.get("retries", {}))
    v.reject_unknown("root.defaults.retries", retries_raw, RetryPolicy.ALLOWED)
    retries = RetryPolicy(
        max_attempts=v.require_positive_int(
            "root.defaults.retries.max_attempts", retries_raw.get("max_attempts", 3)
        ),
        initial_backoff_seconds=v.optional_number(
            "root.defaults.retries.initial_backoff_seconds",
            retries_raw.get("initial_backoff_seconds", 1.0),
            minimum=0.0,
        )
        or 0.0,
        backoff_multiplier=v.optional_number(
            "root.defaults.retries.backoff_multiplier",
            retries_raw.get("backoff_multiplier", 2.0),
            minimum=1.0,
        )
        or 1.0,
        max_backoff_seconds=v.optional_number(
            "root.defaults.retries.max_backoff_seconds",
            retries_raw.get("max_backoff_seconds", 30.0),
            minimum=0.0,
        )
        or 0.0,
    )

    max_tokens = mapping.get("max_output_tokens")
    if max_tokens is not None:
        max_tokens = v.require_positive_int("root.defaults.max_output_tokens", max_tokens)

    seed = mapping.get("seed")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        v.add("root.defaults.seed", "expected an integer")
        seed = None

    return SamplingDefaults(
        trials=v.require_positive_int("root.defaults.trials", mapping.get("trials", 1)),
        temperature=v.optional_number(
            "root.defaults.temperature", mapping.get("temperature"), minimum=0.0
        ),
        top_p=v.optional_number("root.defaults.top_p", mapping.get("top_p"), minimum=0.0),
        max_output_tokens=max_tokens,
        seed=seed,
        retries=retries,
    )


def _parse_models(v: _Validator, raw: Any) -> List[ModelSpec]:
    entries = v.require_list("root.models", raw)
    if not entries:
        v.add("root.models", "at least one model is required")
        return []

    models: List[ModelSpec] = []
    seen: set = set()
    for index, entry in enumerate(entries):
        where = "root.models[{0}]".format(index)
        mapping = v.require_mapping(where, entry)
        if not mapping:
            continue
        v.reject_unknown(where, mapping, ModelSpec.ALLOWED)

        alias = v.require_slug(where + ".alias", mapping.get("alias"))
        if alias and alias in seen:
            v.add(where + ".alias", "duplicate model alias {0!r}".format(alias))
        seen.add(alias)

        max_tokens = mapping.get("max_output_tokens")
        if max_tokens is not None:
            max_tokens = v.require_positive_int(where + ".max_output_tokens", max_tokens)

        seed = mapping.get("seed")
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
            v.add(where + ".seed", "expected an integer")
            seed = None

        options = v.require_mapping(where + ".options", mapping.get("options", {}))

        models.append(
            ModelSpec(
                alias=alias,
                provider=v.require_nonempty_str(where + ".provider", mapping.get("provider")),
                model=v.require_nonempty_str(where + ".model", mapping.get("model")),
                temperature=v.optional_number(
                    where + ".temperature", mapping.get("temperature"), minimum=0.0
                ),
                top_p=v.optional_number(where + ".top_p", mapping.get("top_p"), minimum=0.0),
                max_output_tokens=max_tokens,
                seed=seed,
                options=dict(options),
                notes=_optional_str(mapping.get("notes")),
            )
        )
    return models


def _parse_items(v: _Validator, raw: Any) -> List[Item]:
    entries = v.require_list("root.items", raw)
    if not entries:
        v.add(
            "root.items",
            "at least one item is required. An item is a stimulus presented "
            "identically in every condition.",
        )
        return []

    items: List[Item] = []
    seen: set = set()
    for index, entry in enumerate(entries):
        where = "root.items[{0}]".format(index)
        mapping = v.require_mapping(where, entry)
        if not mapping:
            continue
        v.reject_unknown(where, mapping, Item.ALLOWED)

        item_id = v.require_slug(where + ".id", mapping.get("id"))
        if item_id and item_id in seen:
            v.add(where + ".id", "duplicate item id {0!r}".format(item_id))
        seen.add(item_id)

        item_vars = v.require_mapping(where + ".vars", mapping.get("vars", {}))
        for key in item_vars:
            if not isinstance(key, str) or not key.isidentifier():
                v.add(
                    where + ".vars",
                    "variable name {0!r} must be a valid Python identifier".format(key),
                )

        items.append(
            Item(
                id=item_id,
                vars=dict(item_vars),
                expected=mapping.get("expected"),
                tags=_string_list(v, where + ".tags", mapping.get("tags", [])),
            )
        )
    return items


def _parse_conditions(v: _Validator, raw: Any) -> List[Condition]:
    entries = v.require_list("root.conditions", raw)
    if not entries:
        v.add("root.conditions", "at least one condition is required")
        return []

    conditions: List[Condition] = []
    seen: set = set()
    for index, entry in enumerate(entries):
        where = "root.conditions[{0}]".format(index)
        mapping = v.require_mapping(where, entry)
        if not mapping:
            continue
        v.reject_unknown(where, mapping, Condition.ALLOWED)

        condition_id = v.require_slug(where + ".id", mapping.get("id"))
        if condition_id and condition_id in seen:
            v.add(where + ".id", "duplicate condition id {0!r}".format(condition_id))
        seen.add(condition_id)

        is_control = mapping.get("is_control", False)
        if not isinstance(is_control, bool):
            v.add(where + ".is_control", "expected true or false")
            is_control = False

        conditions.append(
            Condition(
                id=condition_id,
                user_template=v.require_nonempty_str(
                    where + ".user_template", mapping.get("user_template")
                ),
                description=_optional_str(mapping.get("description")),
                system_prompt=_optional_str(mapping.get("system_prompt")),
                variables=dict(
                    v.require_mapping(where + ".variables", mapping.get("variables", {}))
                ),
                is_control=is_control,
                tags=_string_list(v, where + ".tags", mapping.get("tags", [])),
            )
        )
    return conditions


def _parse_evaluation(v: _Validator, raw: Any) -> List[EvaluatorSpec]:
    mapping = v.require_mapping("root.evaluation", raw)
    v.reject_unknown("root.evaluation", mapping, ("evaluators",))

    entries = v.require_list("root.evaluation.evaluators", mapping.get("evaluators", []))
    evaluators: List[EvaluatorSpec] = []
    seen: set = set()
    for index, entry in enumerate(entries):
        where = "root.evaluation.evaluators[{0}]".format(index)
        entry_map = v.require_mapping(where, entry)
        if not entry_map:
            continue
        v.reject_unknown(where, entry_map, EvaluatorSpec.ALLOWED)

        evaluator_id = v.require_slug(where + ".id", entry_map.get("id"))
        if evaluator_id and evaluator_id in seen:
            v.add(where + ".id", "duplicate evaluator id {0!r}".format(evaluator_id))
        seen.add(evaluator_id)

        evaluators.append(
            EvaluatorSpec(
                id=evaluator_id,
                type=v.require_nonempty_str(where + ".type", entry_map.get("type")),
                params=dict(v.require_mapping(where + ".params", entry_map.get("params", {}))),
                description=_optional_str(entry_map.get("description")),
            )
        )
    return evaluators


def _parse_analysis(v: _Validator, raw: Any) -> AnalysisSpec:
    mapping = v.require_mapping("root.analysis", raw)
    v.reject_unknown("root.analysis", mapping, AnalysisSpec.ALLOWED)

    group_by = _string_list(
        v, "root.analysis.group_by", mapping.get("group_by", ["condition", "model"])
    )
    for key in group_by:
        if key not in AnalysisSpec.VALID_GROUP_KEYS:
            v.add(
                "root.analysis.group_by",
                "unknown grouping key {0!r}; valid keys are {1}".format(
                    key, ", ".join(AnalysisSpec.VALID_GROUP_KEYS)
                ),
            )
    if not group_by:
        group_by = ["condition", "model"]

    return AnalysisSpec(
        group_by=group_by,
        report_template=mapping.get("report_template", "default") or "default",
    )


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


def find_placeholders(template: str) -> List[str]:
    """Return the ``{{name}}`` placeholder names used in ``template``.

    ``{{`` / ``}}`` double braces are used deliberately so prompts can contain
    literal single braces - JSON examples, code snippets, and format strings
    are extremely common in prompts and must pass through untouched.
    """
    import re

    return re.findall(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", template)


def render_template(template: str, variables: Dict[str, Any]) -> str:
    """Substitute ``{{name}}`` placeholders from ``variables``.

    Raises :class:`SpecError` on a missing placeholder rather than rendering an
    empty string: a prompt with a silently blank slot is corrupted evidence.
    """
    import re

    missing: List[str] = []

    def _replace(match: "re.Match") -> str:
        name = match.group(1).strip()
        if name not in variables:
            missing.append(name)
            return ""
        value = variables[name]
        return value if isinstance(value, str) else str(value)

    rendered = re.sub(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", _replace, template)
    if missing:
        raise SpecError(
            "template references undefined variable(s): {0}. "
            "Define them in the item's `vars`, the condition's `variables`, or "
            "the experiment's top-level `variables`.".format(", ".join(sorted(set(missing))))
        )
    return rendered


def variables_for(
    spec_variables: Dict[str, Any],
    condition: Condition,
    item: Item,
) -> Dict[str, Any]:
    """Merge template variables in precedence order.

    Later wins: experiment-level < condition-level < item-level. Item values
    win because the item is the most specific scope, which is what lets one
    condition template serve a whole stimulus set.
    """
    merged: Dict[str, Any] = {}
    merged.update(spec_variables)
    merged.update(condition.variables)
    merged.update(item.vars)
    merged.setdefault("condition_id", condition.id)
    merged.setdefault("item_id", item.id)
    return merged


def _check_templates(
    v: _Validator,
    conditions: List[Condition],
    items: List[Item],
    spec_variables: Dict[str, Any],
) -> None:
    """Verify every condition template renders for every item, at load time.

    This is the check that catches the expensive mistake: a typo'd placeholder
    discovered three hours and forty dollars into a run.
    """
    for condition in conditions:
        for item in items:
            available = variables_for(spec_variables, condition, item)
            for field_name, template in (
                ("user_template", condition.user_template),
                ("system_prompt", condition.system_prompt),
            ):
                if not template:
                    continue
                for name in find_placeholders(template):
                    if name not in available:
                        v.add(
                            "root.conditions[{0}].{1}".format(condition.id, field_name),
                            "placeholder {{{{{0}}}}} is undefined for item {1!r}. "
                            "Available variables: {2}".format(
                                name, item.id, ", ".join(sorted(available)) or "(none)"
                            ),
                        )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _string_list(v: _Validator, where: str, value: Any) -> List[str]:
    entries = v.require_list(where, value)
    out: List[str] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, str):
            v.add("{0}[{1}]".format(where, index), "expected a string")
            continue
        out.append(entry)
    return out


def discover_experiments(root: str = "experiments") -> List[str]:
    """List experiment directories that contain an ``experiment.yaml``."""
    if not os.path.isdir(root):
        return []
    found = []
    for name in sorted(os.listdir(root)):
        if os.path.isfile(os.path.join(root, name, EXPERIMENT_FILENAME)):
            found.append(name)
    return found
