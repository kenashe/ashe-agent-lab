"""Aggregation: trial records in, ``results.json`` and ``trials.csv`` out.

Scope discipline
----------------
Phase 0 computes **descriptive statistics only**: n, mean, median, min, max,
sample standard deviation, and the count of unmeasurable trials. There is no
significance test, no effect size, no confidence interval.

That is a deliberate refusal, not an omission. A t-test on three trials per
condition produces a number that looks like evidence and is not, and the
framework's entire purpose is to avoid manufacturing false confidence. When
inferential statistics arrive (ROADMAP Phase 3) they will come with explicit
power considerations and a stated multiple-comparison policy.

What this module guarantees
---------------------------
*   Failed trials are counted, never silently dropped. Each group reports
    ``n_ok`` and ``n_error`` separately, so a condition that failed half its
    calls cannot masquerade as a clean result.
*   ``None`` scores are excluded from statistics and counted in
    ``n_unmeasured``, so an evaluator that could not measure something does not
    drag a mean toward zero.
*   Cost is reported as a floor when any trial is unpriced, with the number of
    unpriced trials stated.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .spec import ExperimentSpec

#: Group keys the spec's ``analysis.group_by`` may reference, mapped to the
#: trial-record field they read.
_GROUP_FIELDS = {
    "condition": "condition_id",
    "model": "model_alias",
    "item": "item_id",
}


def describe(values: Sequence[float]) -> Dict[str, Any]:
    """Descriptive statistics for a list of numbers.

    Returns ``None`` for every statistic when there is no data, rather than
    zero. ``stdev`` uses the sample formula (n-1) and is ``None`` for n < 2,
    because the spread of a single observation is not zero - it is unknown.
    """
    numbers = [float(v) for v in values]
    count = len(numbers)
    if count == 0:
        return {
            "n": 0,
            "mean": None,
            "median": None,
            "min": None,
            "max": None,
            "stdev": None,
            "sum": None,
        }

    ordered = sorted(numbers)
    midpoint = count // 2
    median = (
        ordered[midpoint]
        if count % 2 == 1
        else (ordered[midpoint - 1] + ordered[midpoint]) / 2.0
    )
    mean = sum(numbers) / count
    stdev: Optional[float] = None
    if count >= 2:
        variance = sum((x - mean) ** 2 for x in numbers) / (count - 1)
        stdev = math.sqrt(variance)

    return {
        "n": count,
        "mean": _round(mean),
        "median": _round(median),
        "min": _round(ordered[0]),
        "max": _round(ordered[-1]),
        "stdev": _round(stdev),
        "sum": _round(sum(numbers)),
    }


def aggregate(
    spec: ExperimentSpec, trials: List[Dict[str, Any]], *, run_id: str
) -> Dict[str, Any]:
    """Build the machine-readable ``results.json`` payload."""
    group_keys = [k for k in spec.analysis.group_by if k in _GROUP_FIELDS]
    if not group_keys:
        group_keys = ["condition", "model"]

    evaluator_ids = [e.id for e in spec.evaluators]

    groups: Dict[Tuple[str, ...], List[Dict[str, Any]]] = {}
    for trial in trials:
        signature = tuple(str(trial.get(_GROUP_FIELDS[k], "")) for k in group_keys)
        groups.setdefault(signature, []).append(trial)

    group_payloads: List[Dict[str, Any]] = []
    for signature in sorted(groups):
        members = groups[signature]
        group_payloads.append(
            _summarise_group(dict(zip(group_keys, signature)), members, evaluator_ids)
        )

    ok_trials = [t for t in trials if t.get("status") == "ok"]

    return {
        "schema": "results.v0",
        "run_id": run_id,
        "experiment_id": spec.id,
        "experiment_title": spec.title,
        "research_question": spec.research_question,
        "hypothesis": spec.hypothesis,
        "spec_hash": spec.spec_hash,
        "group_by": group_keys,
        "evaluators": [e.to_dict() for e in spec.evaluators],
        "totals": {
            "trials": len(trials),
            "ok": len(ok_trials),
            "error": len(trials) - len(ok_trials),
            "attempts": sum(int(t.get("attempt_count") or 0) for t in trials),
            "retried_trials": sum(
                1 for t in trials if int(t.get("attempt_count") or 0) > 1
            ),
        },
        "duration": describe(
            [float(t.get("duration_seconds") or 0.0) for t in trials if t.get("duration_seconds") is not None]
        ),
        "tokens": _token_summary(trials),
        "cost": _cost_summary(trials),
        "groups": group_payloads,
        "comparisons": _control_comparisons(spec, group_payloads, group_keys, evaluator_ids),
        "errors": _error_summary(trials),
        "caveats": [
            "Descriptive statistics only. No significance testing is performed; "
            "see ROADMAP.md Phase 3.",
            "Costs are estimates from a hand-maintained price table in "
            "src/ashe_lab/pricing.py, not billing data.",
            "Token counts are as reported by each provider; unreported values are "
            "null and excluded from sums, never counted as zero.",
        ],
    }


def _summarise_group(
    labels: Dict[str, str],
    members: List[Dict[str, Any]],
    evaluator_ids: List[str],
) -> Dict[str, Any]:
    ok_members = [m for m in members if m.get("status") == "ok"]

    metric_payloads: Dict[str, Any] = {}
    for evaluator_id in evaluator_ids:
        values: List[float] = []
        unmeasured = 0
        failed_evaluator = 0
        for member in ok_members:
            score = (member.get("scores") or {}).get(evaluator_id)
            if score is None:
                unmeasured += 1
                continue
            if score.get("detail", {}).get("failed"):
                failed_evaluator += 1
                unmeasured += 1
                continue
            value = score.get("value")
            if value is None:
                unmeasured += 1
                continue
            values.append(float(value))
        stats = describe(values)
        stats["n_unmeasured"] = unmeasured
        stats["n_evaluator_errors"] = failed_evaluator
        metric_payloads[evaluator_id] = stats

    return {
        "labels": labels,
        "key": "|".join("{0}={1}".format(k, v) for k, v in sorted(labels.items())),
        "n_trials": len(members),
        "n_ok": len(ok_members),
        "n_error": len(members) - len(ok_members),
        "metrics": metric_payloads,
        "tokens": _token_summary(ok_members),
        "cost": _cost_summary(ok_members),
        "duration_seconds": describe(
            [float(m.get("duration_seconds") or 0.0) for m in ok_members]
        ),
    }


def _control_comparisons(
    spec: ExperimentSpec,
    group_payloads: List[Dict[str, Any]],
    group_keys: List[str],
    evaluator_ids: List[str],
) -> List[Dict[str, Any]]:
    """Raw differences between each condition and the control, per metric.

    A difference of means and a percentage change. Nothing inferential: this is
    "here is the number you asked about", explicitly labelled as descriptive so
    a reader is not tempted to treat it as a finding.
    """
    control = spec.control_condition
    if control is None or "condition" not in group_keys:
        return []

    other_keys = [k for k in group_keys if k != "condition"]
    by_stratum: Dict[Tuple[str, ...], Dict[str, Dict[str, Any]]] = {}
    for group in group_payloads:
        labels = group["labels"]
        stratum = tuple(str(labels.get(k, "")) for k in other_keys)
        by_stratum.setdefault(stratum, {})[str(labels.get("condition"))] = group

    comparisons: List[Dict[str, Any]] = []
    for stratum in sorted(by_stratum):
        conditions = by_stratum[stratum]
        baseline = conditions.get(control.id)
        if baseline is None:
            continue
        for condition_id in sorted(conditions):
            if condition_id == control.id:
                continue
            treatment = conditions[condition_id]
            metric_deltas: Dict[str, Any] = {}
            for evaluator_id in evaluator_ids:
                control_mean = baseline["metrics"].get(evaluator_id, {}).get("mean")
                treatment_mean = treatment["metrics"].get(evaluator_id, {}).get("mean")
                if control_mean is None or treatment_mean is None:
                    metric_deltas[evaluator_id] = {
                        "control_mean": control_mean,
                        "treatment_mean": treatment_mean,
                        "absolute_change": None,
                        "percent_change": None,
                        "note": "insufficient data in one or both groups",
                    }
                    continue
                absolute = treatment_mean - control_mean
                percent = (
                    (absolute / control_mean) * 100.0 if control_mean not in (0, 0.0) else None
                )
                metric_deltas[evaluator_id] = {
                    "control_mean": control_mean,
                    "treatment_mean": treatment_mean,
                    "absolute_change": _round(absolute),
                    "percent_change": _round(percent),
                    "note": None
                    if percent is not None
                    else "control mean is zero; percent change undefined",
                }
            comparisons.append(
                {
                    "stratum": dict(zip(other_keys, stratum)),
                    "control_condition": control.id,
                    "treatment_condition": condition_id,
                    "n_control_ok": baseline["n_ok"],
                    "n_treatment_ok": treatment["n_ok"],
                    "metrics": metric_deltas,
                    "interpretation": (
                        "Descriptive difference of means only. With n="
                        "{0} and n={1} this is a direction, not a result.".format(
                            baseline["n_ok"], treatment["n_ok"]
                        )
                    ),
                }
            )
    return comparisons


def _token_summary(trials: List[Dict[str, Any]]) -> Dict[str, Any]:
    input_total = 0
    output_total = 0
    input_known = 0
    output_known = 0
    for trial in trials:
        usage = ((trial.get("response") or {}).get("usage") or {})
        if isinstance(usage.get("input_tokens"), int):
            input_total += usage["input_tokens"]
            input_known += 1
        if isinstance(usage.get("output_tokens"), int):
            output_total += usage["output_tokens"]
            output_known += 1
    total_trials = len(trials)
    return {
        "input_tokens_total": input_total,
        "output_tokens_total": output_total,
        "total_tokens": input_total + output_total,
        "trials_with_input_usage": input_known,
        "trials_with_output_usage": output_known,
        "trials_missing_usage": total_trials - min(input_known, output_known),
        "note": "sums cover only trials where the provider reported usage",
    }


def _cost_summary(trials: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = 0.0
    priced = 0
    unpriced = 0
    rate_dates = set()
    for trial in trials:
        cost = trial.get("cost") or {}
        value = cost.get("cost_usd")
        if isinstance(value, (int, float)):
            total += float(value)
            priced += 1
            if cost.get("rate_as_of"):
                rate_dates.add(cost["rate_as_of"])
        else:
            unpriced += 1
    return {
        "estimated_usd": round(total, 6),
        "is_floor": unpriced > 0,
        "trials_priced": priced,
        "trials_unpriced": unpriced,
        "rate_dates": sorted(rate_dates),
        "note": (
            "estimate from list prices in src/ashe_lab/pricing.py; "
            "a floor rather than a total when trials_unpriced > 0"
        ),
    }


def _error_summary(trials: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_type: Dict[str, int] = {}
    examples: List[Dict[str, Any]] = []
    for trial in trials:
        if trial.get("status") == "ok":
            continue
        error = trial.get("error") or {}
        error_type = str(error.get("type", "Unknown"))
        by_type[error_type] = by_type.get(error_type, 0) + 1
        if len(examples) < 5:
            examples.append(
                {
                    "trial_key": trial.get("trial_key"),
                    "type": error_type,
                    "message": error.get("message"),
                    "attempts": trial.get("attempt_count"),
                }
            )
    return {"by_type": by_type, "examples": examples}


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

#: Fixed leading columns, in a deliberate order: identity, then outcome, then
#: measurement. Stable across versions so downstream scripts keep working.
BASE_CSV_COLUMNS = [
    "trial_key",
    "experiment_id",
    "run_condition",
    "model_alias",
    "provider",
    "model_requested",
    "model_reported",
    "item_id",
    "repetition",
    "status",
    "attempt_count",
    "duration_seconds",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "finish_reason",
    "response_chars",
    "error_type",
    "error_message",
]


def trials_to_csv_rows(
    spec: ExperimentSpec, trials: List[Dict[str, Any]]
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Flatten trial records into CSV rows.

    One row per trial, one extra column per evaluator (prefixed ``score_``).
    Response text is deliberately **not** included: it contains newlines and
    commas, it can be very long, and ``trials.jsonl`` already holds it
    losslessly. The CSV is for statistics; the JSONL is for evidence.
    """
    score_columns = ["score_" + e.id for e in spec.evaluators]
    columns = BASE_CSV_COLUMNS + score_columns

    rows: List[Dict[str, Any]] = []
    for trial in trials:
        response = trial.get("response") or {}
        usage = response.get("usage") or {}
        error = trial.get("error") or {}
        row: Dict[str, Any] = {
            "trial_key": trial.get("trial_key"),
            "experiment_id": trial.get("experiment_id"),
            # Named run_condition rather than condition: "condition" is a
            # reserved word in some stats packages and an awkward column name
            # in R.
            "run_condition": trial.get("condition_id"),
            "model_alias": trial.get("model_alias"),
            "provider": trial.get("provider"),
            "model_requested": trial.get("model_requested"),
            "model_reported": trial.get("model_reported"),
            "item_id": trial.get("item_id"),
            "repetition": trial.get("repetition"),
            "status": trial.get("status"),
            "attempt_count": trial.get("attempt_count"),
            "duration_seconds": trial.get("duration_seconds"),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cost_usd": (trial.get("cost") or {}).get("cost_usd"),
            "finish_reason": response.get("finish_reason"),
            "response_chars": len(response.get("text") or "")
            if trial.get("status") == "ok"
            else None,
            "error_type": error.get("type"),
            "error_message": (error.get("message") or "").replace("\n", " ") or None,
        }
        for evaluator in spec.evaluators:
            score = (trial.get("scores") or {}).get(evaluator.id) or {}
            row["score_" + evaluator.id] = score.get("value")
        rows.append(row)
    return columns, rows


def _round(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), 6)
