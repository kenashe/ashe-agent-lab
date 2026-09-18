"""The execution engine.

The runner's whole job is to turn a validated spec into a directory full of
honest evidence. It is deliberately boring: a nested loop, a retry wrapper, and
a record writer. No concurrency, no queue, no scheduler.

Why no concurrency in Phase 0? Because sequential execution makes the event log
a true chronological narrative, keeps rate-limit behaviour predictable, and
removes the single largest source of hard-to-reproduce bugs. The cost is wall
clock time on large runs; parallelism is a Phase 1 item (see ROADMAP.md) and the
storage format already tolerates it, since trials are independent records.

The important invariant
----------------------
**Every attempt is recorded, including the ones that failed.** A trial that
succeeded on its third try preserves all three attempts with their errors,
timings, and status codes. A trial that never succeeded is written with
``status: "error"`` rather than omitted, because a missing row would silently
bias the aggregate: if the fact-check condition times out more often, dropping
those rows makes it look better, not worse.
"""

from __future__ import annotations

import itertools
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from . import metrics, report as report_module
from .errors import AsheLabError, ProviderError, ProviderNotConfigured
from .evaluators import EvaluationContext, run_evaluators
from .ids import (
    content_hash,
    env_fingerprint,
    iso_utc,
    new_run_id,
    trial_key,
    utc_now,
)
from .pricing import estimate_cost
from .providers import Message, ModelRequest, ProviderCache
from .spec import (
    Condition,
    ExperimentSpec,
    Item,
    ModelSpec,
    render_template,
    variables_for,
)
from .storage import RunWriter

#: A trial's ``status`` field takes one of these values.
STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_SKIPPED = "skipped"


@dataclass
class RunOptions:
    """Everything that can vary between two runs of the same spec.

    Recorded in the manifest, so a narrowed run ("just the control condition,
    one trial, to check the prompt renders") is distinguishable from a full one
    when read back later.
    """

    trials_override: Optional[int] = None
    only_conditions: List[str] = field(default_factory=list)
    only_models: List[str] = field(default_factory=list)
    only_items: List[str] = field(default_factory=list)
    max_trials: Optional[int] = None
    runs_root: Optional[str] = None
    run_id: Optional[str] = None
    seal: bool = True
    #: Seconds to wait between trials. A crude but effective way to stay under
    #: a rate limit without building a rate limiter.
    delay_seconds: float = 0.0
    label: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trials_override": self.trials_override,
            "only_conditions": list(self.only_conditions),
            "only_models": list(self.only_models),
            "only_items": list(self.only_items),
            "max_trials": self.max_trials,
            "delay_seconds": self.delay_seconds,
            "seal": self.seal,
            "label": self.label,
        }


@dataclass
class PlannedTrial:
    """One unit of work, fully resolved before anything is called."""

    index: int
    condition: Condition
    model: ModelSpec
    item: Item
    trial_index: int
    system_prompt: Optional[str]
    user_prompt: str

    @property
    def key(self) -> str:
        return trial_key(self.condition.id, self.model.alias, self.item.id, self.trial_index)


@dataclass
class RunResult:
    """What a completed run hands back to the CLI."""

    run_id: str
    run_path: str
    experiment_id: str
    trial_count: int
    ok_count: int
    error_count: int
    results: Dict[str, Any]
    report_path: str


def plan_run(spec: ExperimentSpec, options: Optional[RunOptions] = None) -> List[PlannedTrial]:
    """Resolve the spec into an ordered list of trials, rendering every prompt.

    Prompts are rendered here, before any network call, so a templating error
    costs nothing. Ordering is condition-major, then model, then item, then
    repetition - which means a run killed halfway has complete coverage of the
    early conditions rather than partial coverage of all of them. That is the
    less useful failure mode, and it is chosen deliberately: partial data with
    a known shape beats partial data with an unknown one.
    """
    options = options or RunOptions()

    conditions = _filter_by_id(spec.conditions, options.only_conditions, "condition")
    models = _filter_by_attr(spec.models, "alias", options.only_models, "model")
    items = _filter_by_id(spec.items, options.only_items, "item")
    trials_per_cell = options.trials_override or spec.defaults.trials

    planned: List[PlannedTrial] = []
    counter = itertools.count()
    for condition in conditions:
        for model in models:
            for item in items:
                for trial_index in range(trials_per_cell):
                    variables = variables_for(spec.variables, condition, item)
                    system_prompt = (
                        render_template(condition.system_prompt, variables)
                        if condition.system_prompt
                        else None
                    )
                    user_prompt = render_template(condition.user_template, variables)
                    planned.append(
                        PlannedTrial(
                            index=next(counter),
                            condition=condition,
                            model=model,
                            item=item,
                            trial_index=trial_index,
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                        )
                    )

    if options.max_trials is not None:
        planned = planned[: options.max_trials]
    return planned


def execute(
    spec: ExperimentSpec,
    options: Optional[RunOptions] = None,
    *,
    on_progress: Optional[Callable[[str], None]] = None,
) -> RunResult:
    """Run an experiment and write an immutable run directory.

    ``on_progress`` receives short human-readable status lines. The runner has
    no opinion about terminals; the CLI passes a printer, tests pass nothing.
    """
    options = options or RunOptions()
    emit = on_progress or (lambda _message: None)

    planned = plan_run(spec, options)
    if not planned:
        raise AsheLabError(
            "nothing to run: the filters you supplied excluded every trial"
        )

    run_id = options.run_id or new_run_id()
    providers = ProviderCache()
    started_at = utc_now()

    records: List[Dict[str, Any]] = []

    with RunWriter(spec.id, run_id, options.runs_root, seal=options.seal) as writer:
        writer.write_spec_snapshot(
            spec.source_text
            if spec.source_text is not None
            else "# spec was constructed in memory; no source YAML available\n"
        )
        writer.append_event(
            "run_started",
            {
                "run_id": run_id,
                "experiment_id": spec.id,
                "spec_hash": spec.spec_hash,
                "planned_trials": len(planned),
                "options": options.to_dict(),
            },
        )
        emit(
            "run {0}: {1} trial(s) planned for experiment {2}".format(
                run_id, len(planned), spec.id
            )
        )

        for planned_trial in planned:
            if options.delay_seconds and planned_trial.index > 0:
                time.sleep(options.delay_seconds)

            record = _execute_trial(
                spec, planned_trial, providers, writer, emit, total=len(planned)
            )
            writer.append_trial(record)
            records.append(record)

        ok_count = sum(1 for r in records if r["status"] == STATUS_OK)
        error_count = len(records) - ok_count
        finished_at = utc_now()

        results = metrics.aggregate(spec, records, run_id=run_id)
        manifest = _build_manifest(
            spec=spec,
            options=options,
            run_id=run_id,
            started_at=iso_utc(started_at),
            finished_at=iso_utc(finished_at),
            planned=len(planned),
            ok_count=ok_count,
            error_count=error_count,
            providers=providers,
            results=results,
        )
        report_markdown = report_module.render_report(
            manifest=manifest, results=results, trials=records
        )
        csv_columns, csv_rows = metrics.trials_to_csv_rows(spec, records)

        run_path = writer.finalize(
            manifest=manifest,
            results=results,
            report_markdown=report_markdown,
            trial_rows=csv_rows,
            csv_columns=csv_columns,
        )

    emit(
        "run {0} complete: {1} ok, {2} error(s) -> {3}".format(
            run_id, ok_count, error_count, run_path
        )
    )

    import os

    return RunResult(
        run_id=run_id,
        run_path=run_path,
        experiment_id=spec.id,
        trial_count=len(records),
        ok_count=ok_count,
        error_count=error_count,
        results=results,
        report_path=os.path.join(run_path, "report.md"),
    )


# ---------------------------------------------------------------------------
# One trial
# ---------------------------------------------------------------------------


def _execute_trial(
    spec: ExperimentSpec,
    planned: PlannedTrial,
    providers: ProviderCache,
    writer: RunWriter,
    emit: Callable[[str], None],
    total: int,
) -> Dict[str, Any]:
    """Execute one trial with retries and build its complete record."""
    messages: List[Message] = []
    if planned.system_prompt:
        messages.append(Message(role="system", content=planned.system_prompt))
    messages.append(Message(role="user", content=planned.user_prompt))

    request = ModelRequest(
        model=planned.model.model,
        messages=messages,
        temperature=_first_set(planned.model.temperature, spec.defaults.temperature),
        top_p=_first_set(planned.model.top_p, spec.defaults.top_p),
        max_output_tokens=_first_set(
            planned.model.max_output_tokens, spec.defaults.max_output_tokens
        ),
        seed=_first_set(planned.model.seed, spec.defaults.seed),
        options=dict(planned.model.options),
    )

    retries = spec.defaults.retries
    attempts: List[Dict[str, Any]] = []
    response = None
    final_error: Optional[Dict[str, Any]] = None

    writer.append_event(
        "trial_started",
        {"trial_key": planned.key, "index": planned.index, "model": planned.model.alias},
    )

    for attempt_number in range(1, retries.max_attempts + 1):
        attempt_started = utc_now()
        monotonic_start = time.monotonic()
        try:
            provider = providers.get(planned.model.provider)
            response = provider.complete(request)
            attempts.append(
                {
                    "attempt": attempt_number,
                    "started_at": iso_utc(attempt_started),
                    "duration_seconds": round(time.monotonic() - monotonic_start, 6),
                    "outcome": "ok",
                }
            )
            break
        except ProviderNotConfigured as exc:
            final_error = _error_payload(exc, retryable=False)
            attempts.append(
                _failed_attempt(attempt_number, attempt_started, monotonic_start, final_error)
            )
            break  # a missing credential will not appear on retry
        except ProviderError as exc:
            final_error = _error_payload(exc, retryable=exc.retryable)
            attempts.append(
                _failed_attempt(attempt_number, attempt_started, monotonic_start, final_error)
            )
            if not exc.retryable or attempt_number >= retries.max_attempts:
                break
            backoff = min(
                retries.initial_backoff_seconds
                * (retries.backoff_multiplier ** (attempt_number - 1)),
                retries.max_backoff_seconds,
            )
            writer.append_event(
                "trial_retry",
                {
                    "trial_key": planned.key,
                    "attempt": attempt_number,
                    "backoff_seconds": backoff,
                    "error": final_error["message"],
                },
            )
            if backoff > 0:
                time.sleep(backoff)
        except Exception as exc:  # unexpected adapter bug
            final_error = {
                "type": type(exc).__name__,
                "message": str(exc),
                "retryable": False,
                "unexpected": True,
                "traceback": traceback.format_exc(limit=8),
            }
            attempts.append(
                _failed_attempt(attempt_number, attempt_started, monotonic_start, final_error)
            )
            break

    total_duration = sum(a.get("duration_seconds") or 0.0 for a in attempts)

    record: Dict[str, Any] = {
        "schema": "trial.v0",
        "trial_key": planned.key,
        "trial_index_in_run": planned.index,
        "recorded_at": iso_utc(),
        "experiment_id": spec.id,
        "spec_hash": spec.spec_hash,
        "condition_id": planned.condition.id,
        "condition_is_control": planned.condition.is_control,
        "model_alias": planned.model.alias,
        "provider": planned.model.provider,
        "model_requested": planned.model.model,
        "item_id": planned.item.id,
        "repetition": planned.trial_index,
        "request": request.to_dict(),
        "request_hash": content_hash(request.to_dict()),
        "prompt": {
            "system": planned.system_prompt,
            "user": planned.user_prompt,
        },
        "attempts": attempts,
        "attempt_count": len(attempts),
        "duration_seconds": round(total_duration, 6),
    }

    if response is not None:
        record["status"] = STATUS_OK
        record["response"] = response.to_dict()
        record["model_reported"] = response.model_reported
        record["error"] = None
        record["cost"] = estimate_cost(
            planned.model.provider,
            response.model_reported or planned.model.model,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )
        record["scores"] = run_evaluators(
            spec.evaluators,
            EvaluationContext(
                response_text=response.text,
                condition_id=planned.condition.id,
                model_alias=planned.model.alias,
                item_id=planned.item.id,
                item_vars=dict(planned.item.vars),
                item_expected=planned.item.expected,
                prompt_text=planned.user_prompt,
            ),
        )
        emit("  [{0}/{1}] ok   {2}".format(planned.index + 1, total, planned.key))
    else:
        record["status"] = STATUS_ERROR
        record["response"] = None
        record["model_reported"] = None
        record["error"] = final_error
        record["cost"] = {
            "cost_usd": None,
            "cost_note": "trial failed; no usage to price",
            "rate_as_of": None,
            "input_rate_per_mtok": None,
            "output_rate_per_mtok": None,
        }
        # Scores are absent rather than zero. A failed trial has no measurement,
        # and a zero would be indistinguishable from a real zero in aggregation.
        record["scores"] = {}
        emit(
            "  [{0}/{1}] FAIL {2}: {3}".format(
                planned.index + 1,
                total,
                planned.key,
                (final_error or {}).get("message", "unknown"),
            )
        )

    writer.append_event(
        "trial_finished",
        {
            "trial_key": planned.key,
            "status": record["status"],
            "attempts": len(attempts),
            "duration_seconds": record["duration_seconds"],
        },
    )
    return record


def _failed_attempt(
    attempt_number: int,
    started: Any,
    monotonic_start: float,
    error: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "attempt": attempt_number,
        "started_at": iso_utc(started),
        "duration_seconds": round(time.monotonic() - monotonic_start, 6),
        "outcome": "error",
        "error": error,
    }


def _error_payload(exc: ProviderError, *, retryable: bool) -> Dict[str, Any]:
    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "retryable": retryable,
        "status_code": exc.status_code,
        "provider": exc.provider,
    }


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def _build_manifest(
    *,
    spec: ExperimentSpec,
    options: RunOptions,
    run_id: str,
    started_at: str,
    finished_at: str,
    planned: int,
    ok_count: int,
    error_count: int,
    providers: ProviderCache,
    results: Dict[str, Any],
) -> Dict[str, Any]:
    """Assemble ``manifest.json``: everything needed to interpret this run.

    The manifest answers, without reading any other file: what was run, from
    which spec, by which code, on what machine, when, with what outcome, and
    how much it cost.
    """
    from . import __version__

    return {
        "schema": "manifest.v0",
        "run_id": run_id,
        "experiment_id": spec.id,
        "experiment_title": spec.title,
        "kind": spec.kind,
        "schema_version": spec.schema_version,
        "spec_hash": spec.spec_hash,
        "spec_source_path": spec.source_path,
        "research_question": spec.research_question,
        "hypothesis": spec.hypothesis,
        "framework": {
            "name": "ashe-agent-lab",
            "version": __version__,
            "git_commit": _git_commit(),
            "git_dirty": _git_dirty(),
        },
        "environment": env_fingerprint(),
        "timing": {
            "started_at": started_at,
            "finished_at": finished_at,
        },
        "counts": {
            "planned_trials": planned,
            "recorded_trials": ok_count + error_count,
            "ok": ok_count,
            "error": error_count,
        },
        "options": options.to_dict(),
        "providers": providers.described(),
        "spec": spec.to_dict(),
        "cost_summary": results.get("cost", {}),
    }


def _git_commit() -> Optional[str]:
    """Current HEAD commit, if the framework is running from a git checkout.

    Pins the run to the exact code that produced it, which is the difference
    between "this result is reproducible" and "this result was produced by
    something resembling this code".
    """
    return _git(["rev-parse", "HEAD"])


def _git_dirty() -> Optional[bool]:
    """Whether the working tree had uncommitted changes at run time.

    A dirty tree means the recorded commit does not fully describe the code
    that ran. Recording that honestly is more useful than pretending otherwise.
    """
    output = _git(["status", "--porcelain"])
    if output is None:
        return None
    return bool(output.strip())


def _git(args: List[str]) -> Optional[str]:
    import os
    import subprocess

    try:
        completed = subprocess.run(
            ["git"] + args,
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.decode("utf-8", errors="replace").strip()


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def _filter_by_id(entries: List[Any], wanted: List[str], label: str) -> List[Any]:
    return _filter_by_attr(entries, "id", wanted, label)


def _filter_by_attr(
    entries: List[Any], attribute: str, wanted: List[str], label: str
) -> List[Any]:
    if not wanted:
        return list(entries)
    available = [getattr(e, attribute) for e in entries]
    unknown = [w for w in wanted if w not in available]
    if unknown:
        raise AsheLabError(
            "unknown {0}(s) {1}; this experiment defines: {2}".format(
                label, ", ".join(repr(u) for u in unknown), ", ".join(available)
            )
        )
    return [e for e in entries if getattr(e, attribute) in wanted]


def _first_set(*values: Any) -> Any:
    """Return the first non-None value. Implements per-model override of defaults."""
    for value in values:
        if value is not None:
            return value
    return None
