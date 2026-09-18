"""Markdown report generation.

The report is the human-facing end of the pipeline, and it has one job that
matters more than looking good: **it must not overstate what the run showed.**
So every report states its own n, flags failed trials prominently, labels costs
as estimates, and carries an explicit caveats section. A framework that
produces confident-looking reports from three trials would be actively harmful.

The report is *derived*, never primary. It is regenerated from
``trials.jsonl`` by ``ashe-lab report``, which means an improved template can
be applied to runs recorded years earlier - the reason raw records are stored
richly and the report is stored as a convenience.

Markdown, not HTML, because Markdown renders on GitHub, in an editor, in a
terminal via ``glow``, and as plain text in fifty years. Static HTML publishing
is a Phase 4 item that will read these same records.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

#: How many example transcripts to include per condition.
DEFAULT_TRANSCRIPT_SAMPLES = 1

#: Truncation limit for quoted responses, to keep the report readable.
TRANSCRIPT_CHAR_LIMIT = 1200


def render_report(
    *,
    manifest: Dict[str, Any],
    results: Dict[str, Any],
    trials: List[Dict[str, Any]],
    transcript_samples: int = DEFAULT_TRANSCRIPT_SAMPLES,
) -> str:
    """Render the default Markdown report for one run."""
    lines: List[str] = []
    add = lines.append

    title = manifest.get("experiment_title") or manifest.get("experiment_id")
    add("# {0}".format(title))
    add("")
    add("**Run `{0}`** of experiment `{1}`".format(manifest.get("run_id"), manifest.get("experiment_id")))
    add("")

    _render_header_table(add, manifest, results)
    _render_question(add, manifest)
    _render_health(add, results)
    _render_summary_tables(add, results)
    _render_comparisons(add, results)
    _render_cost(add, results)
    _render_conditions(add, manifest)
    _render_transcripts(add, trials, transcript_samples)
    _render_errors(add, results)
    _render_caveats(add, results, manifest)
    _render_reproduction(add, manifest)

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def _render_header_table(add, manifest: Dict[str, Any], results: Dict[str, Any]) -> None:
    timing = manifest.get("timing") or {}
    counts = manifest.get("counts") or {}
    framework = manifest.get("framework") or {}

    commit = framework.get("git_commit")
    commit_display = (commit[:12] if isinstance(commit, str) else None) or "unknown"
    if framework.get("git_dirty"):
        commit_display += " (working tree had uncommitted changes)"

    add("| | |")
    add("|---|---|")
    add("| Started | {0} |".format(timing.get("started_at", "?")))
    add("| Finished | {0} |".format(timing.get("finished_at", "?")))
    add(
        "| Trials | {0} recorded, {1} ok, {2} failed |".format(
            counts.get("recorded_trials", "?"), counts.get("ok", "?"), counts.get("error", "?")
        )
    )
    add("| Spec hash | `{0}` |".format((manifest.get("spec_hash") or "")[:16]))
    add(
        "| Framework | ashe-agent-lab {0} @ {1} |".format(
            framework.get("version", "?"), commit_display
        )
    )
    cost = results.get("cost") or {}
    add(
        "| Estimated cost | {0} |".format(
            _format_cost(cost.get("estimated_usd"), cost.get("is_floor"))
        )
    )
    add("")


def _render_question(add, manifest: Dict[str, Any]) -> None:
    question = manifest.get("research_question")
    hypothesis = manifest.get("hypothesis")
    if not question and not hypothesis:
        return
    add("## Research question")
    add("")
    if question:
        add(question)
        add("")
    if hypothesis:
        add("**Hypothesis.** {0}".format(hypothesis))
        add("")


def _render_health(add, results: Dict[str, Any]) -> None:
    """Warn loudly when a run's shape undermines its own numbers."""
    totals = results.get("totals") or {}
    errors = int(totals.get("error") or 0)
    ok = int(totals.get("ok") or 0)
    total = int(totals.get("trials") or 0)
    retried = int(totals.get("retried_trials") or 0)

    warnings: List[str] = []
    if errors:
        share = (errors / total * 100.0) if total else 0.0
        warnings.append(
            "**{0} of {1} trials failed ({2:.0f}%).** Failed trials are preserved in "
            "`trials.jsonl` with `status: error` and are excluded from the statistics "
            "below. If failures are not evenly distributed across conditions, every "
            "comparison here is biased - check the error breakdown before reading on.".format(
                errors, total, share
            )
        )
    if retried:
        warnings.append(
            "{0} trial(s) needed more than one attempt. Every attempt is recorded in "
            "each trial's `attempts` array.".format(retried)
        )

    groups = results.get("groups") or []
    small_groups = [g for g in groups if int(g.get("n_ok") or 0) < 5]
    if small_groups and groups:
        warnings.append(
            "{0} of {1} groups have fewer than 5 successful trials. Group means at this "
            "sample size are illustrative only - they show whether the pipeline works, "
            "not whether an effect exists.".format(len(small_groups), len(groups))
        )
    if ok == 0:
        warnings.append("**No trial succeeded.** Nothing below should be interpreted.")

    if not warnings:
        return
    add("## Run health")
    add("")
    for warning in warnings:
        add("- " + warning)
    add("")


def _render_summary_tables(add, results: Dict[str, Any]) -> None:
    groups = results.get("groups") or []
    evaluators = results.get("evaluators") or []
    if not groups:
        return

    group_by = results.get("group_by") or []

    add("## Results by group")
    add("")

    header = [_pretty(k) for k in group_by] + ["n ok", "n err"]
    for evaluator in evaluators:
        header.append("{0} (mean ± sd)".format(evaluator["id"]))
    add("| " + " | ".join(header) + " |")
    add("|" + "|".join(["---"] * len(header)) + "|")

    for group in groups:
        labels = group.get("labels") or {}
        row = [str(labels.get(k, "")) for k in group_by]
        row.append(str(group.get("n_ok", 0)))
        row.append(str(group.get("n_error", 0)))
        for evaluator in evaluators:
            stats = (group.get("metrics") or {}).get(evaluator["id"]) or {}
            row.append(_format_mean_sd(stats))
        add("| " + " | ".join(row) + " |")
    add("")

    if evaluators:
        add("### What each metric measures")
        add("")
        for evaluator in evaluators:
            description = evaluator.get("description") or "(no description given)"
            add("- **`{0}`** (`{1}`): {2}".format(evaluator["id"], evaluator["type"], description))
        add("")


def _render_comparisons(add, results: Dict[str, Any]) -> None:
    comparisons = results.get("comparisons") or []
    if not comparisons:
        return

    add("## Difference from control")
    add("")
    add(
        "_Raw differences of means. No significance testing has been performed - "
        "see Caveats._"
    )
    add("")

    for comparison in comparisons:
        stratum = comparison.get("stratum") or {}
        stratum_text = (
            " (" + ", ".join("{0}={1}".format(k, v) for k, v in sorted(stratum.items())) + ")"
            if stratum
            else ""
        )
        add(
            "### `{0}` vs control `{1}`{2}".format(
                comparison.get("treatment_condition"),
                comparison.get("control_condition"),
                stratum_text,
            )
        )
        add("")
        add(
            "n = {0} (control), {1} (treatment)".format(
                comparison.get("n_control_ok"), comparison.get("n_treatment_ok")
            )
        )
        add("")
        add("| Metric | Control | Treatment | Change | % change |")
        add("|---|---|---|---|---|")
        for metric_id, delta in sorted((comparison.get("metrics") or {}).items()):
            add(
                "| `{0}` | {1} | {2} | {3} | {4} |".format(
                    metric_id,
                    _num(delta.get("control_mean")),
                    _num(delta.get("treatment_mean")),
                    _signed(delta.get("absolute_change")),
                    _percent(delta.get("percent_change")),
                )
            )
        add("")


def _render_cost(add, results: Dict[str, Any]) -> None:
    cost = results.get("cost") or {}
    tokens = results.get("tokens") or {}
    duration = results.get("duration") or {}

    add("## Cost, tokens, and timing")
    add("")
    add("| | |")
    add("|---|---|")
    add(
        "| Estimated cost | {0} |".format(
            _format_cost(cost.get("estimated_usd"), cost.get("is_floor"))
        )
    )
    add(
        "| Trials priced / unpriced | {0} / {1} |".format(
            cost.get("trials_priced", 0), cost.get("trials_unpriced", 0)
        )
    )
    if cost.get("rate_dates"):
        add("| Price list dates | {0} |".format(", ".join(cost["rate_dates"])))
    add("| Input tokens | {0} |".format(tokens.get("input_tokens_total", 0)))
    add("| Output tokens | {0} |".format(tokens.get("output_tokens_total", 0)))
    if tokens.get("trials_missing_usage"):
        add(
            "| Trials without usage data | {0} |".format(tokens["trials_missing_usage"])
        )
    add("| Mean trial duration | {0} s |".format(_num(duration.get("mean"))))
    add("| Total model time | {0} s |".format(_num(duration.get("sum"))))
    add("")


def _render_conditions(add, manifest: Dict[str, Any]) -> None:
    """Reproduce the exact prompts, because the manipulation *is* the prompt."""
    spec = manifest.get("spec") or {}
    conditions = spec.get("conditions") or []
    items = spec.get("items") or []
    if not conditions:
        return

    add("## Conditions as executed")
    add("")
    for condition in conditions:
        marker = " _(control)_" if condition.get("is_control") else ""
        add("### `{0}`{1}".format(condition.get("id"), marker))
        add("")
        if condition.get("description"):
            add(condition["description"])
            add("")
        if condition.get("system_prompt"):
            add("**System prompt**")
            add("")
            add("```text")
            add(str(condition["system_prompt"]))
            add("```")
            add("")
        add("**User template**")
        add("")
        add("```text")
        add(str(condition.get("user_template", "")))
        add("```")
        add("")

    if items:
        add("### Stimulus items")
        add("")
        add("| Item | Variables | Expected |")
        add("|---|---|---|")
        for item in items:
            variables = ", ".join(
                "{0}={1}".format(k, _truncate(str(v), 80))
                for k, v in sorted((item.get("vars") or {}).items())
            )
            add(
                "| `{0}` | {1} | {2} |".format(
                    item.get("id"),
                    _escape_cell(variables) or "—",
                    _escape_cell(_truncate(str(item.get("expected")), 60))
                    if item.get("expected") is not None
                    else "—",
                )
            )
        add("")


def _render_transcripts(add, trials: List[Dict[str, Any]], samples: int) -> None:
    """Quote a couple of real exchanges. Aggregates hide the interesting part."""
    if samples <= 0 or not trials:
        return

    by_condition: Dict[str, List[Dict[str, Any]]] = {}
    for trial in trials:
        if trial.get("status") != "ok":
            continue
        by_condition.setdefault(str(trial.get("condition_id")), []).append(trial)
    if not by_condition:
        return

    add("## Sample transcripts")
    add("")
    add(
        "_Illustrative excerpts. The complete set of prompts and responses is in "
        "`trials.jsonl`._"
    )
    add("")

    for condition_id in sorted(by_condition):
        for trial in by_condition[condition_id][:samples]:
            response = trial.get("response") or {}
            add(
                "<details><summary><code>{0}</code> · {1} · item <code>{2}</code></summary>".format(
                    condition_id, trial.get("model_alias"), trial.get("item_id")
                )
            )
            add("")
            prompt = trial.get("prompt") or {}
            if prompt.get("system"):
                add("**System**")
                add("")
                add("```text")
                add(str(prompt["system"]))
                add("```")
                add("")
            add("**User**")
            add("")
            add("```text")
            add(str(prompt.get("user", "")))
            add("```")
            add("")
            add("**Response**")
            add("")
            add("```text")
            add(_truncate(str(response.get("text", "")), TRANSCRIPT_CHAR_LIMIT))
            add("```")
            add("")
            add("</details>")
            add("")


def _render_errors(add, results: Dict[str, Any]) -> None:
    errors = results.get("errors") or {}
    by_type = errors.get("by_type") or {}
    if not by_type:
        return

    add("## Failures")
    add("")
    add("| Error type | Count |")
    add("|---|---|")
    for error_type, count in sorted(by_type.items(), key=lambda kv: (-kv[1], kv[0])):
        add("| `{0}` | {1} |".format(error_type, count))
    add("")

    examples = errors.get("examples") or []
    if examples:
        add("Examples:")
        add("")
        for example in examples:
            add(
                "- `{0}` after {1} attempt(s): {2}".format(
                    example.get("trial_key"),
                    example.get("attempts"),
                    _truncate(str(example.get("message")), 300),
                )
            )
        add("")


def _render_caveats(add, results: Dict[str, Any], manifest: Dict[str, Any]) -> None:
    add("## Caveats")
    add("")
    for caveat in results.get("caveats") or []:
        add("- " + caveat)

    totals = results.get("totals") or {}
    if int(totals.get("ok") or 0) < 30:
        add(
            "- This run has {0} successful trials in total. That is a pipeline check, "
            "not a study. Treat every number above as a demonstration that the "
            "measurement works.".format(totals.get("ok", 0))
        )

    providers = manifest.get("providers") or {}
    if any(p.get("kind") == "offline-deterministic" for p in providers.values()):
        add(
            "- **This run used the offline `echo` provider for at least one model.** "
            "Its responses are hash-derived text, not model behaviour. Any apparent "
            "difference between conditions is an artifact of the prompt bytes changing "
            "the hash, and means nothing."
        )
    add("")


def _render_reproduction(add, manifest: Dict[str, Any]) -> None:
    add("## Reproducing this run")
    add("")
    add("```bash")
    add("# Re-run the same spec (produces a NEW run directory; this one is immutable)")
    add("ashe-lab run {0}".format(manifest.get("experiment_id")))
    add("")
    add("# Verify this run's stored evidence has not been altered")
    add("ashe-lab verify runs/{0}/{1}".format(manifest.get("experiment_id"), manifest.get("run_id")))
    add("")
    add("# Regenerate this report from the preserved raw records")
    add("ashe-lab report runs/{0}/{1}".format(manifest.get("experiment_id"), manifest.get("run_id")))
    add("```")
    add("")
    add(
        "The exact experiment definition used is preserved verbatim in "
        "`experiment.snapshot.yaml` in this run directory. The spec hash above covers "
        "its semantic content, so an identical hash means an identical design."
    )
    add("")


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _format_mean_sd(stats: Dict[str, Any]) -> str:
    mean = stats.get("mean")
    stdev = stats.get("stdev")
    if mean is None:
        unmeasured = stats.get("n_unmeasured") or 0
        return "— ({0} unmeasured)".format(unmeasured) if unmeasured else "—"
    text = _num(mean)
    if stdev is not None:
        text += " ± " + _num(stdev)
    if stats.get("n_unmeasured"):
        text += " ({0} unmeasured)".format(stats["n_unmeasured"])
    return text


def _format_cost(value: Optional[float], is_floor: Optional[bool]) -> str:
    if value is None:
        return "unknown"
    prefix = "≥ " if is_floor else "~"
    if value == 0:
        return "$0.00 (no paid provider calls)"
    if value < 0.01:
        return "{0}${1:.6f}".format(prefix, value)
    return "{0}${1:.4f}".format(prefix, value)


def _num(value: Any) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number == int(number) and abs(number) < 1e15:
        return "{0:.0f}".format(number)
    if abs(number) >= 100:
        return "{0:.1f}".format(number)
    if abs(number) >= 1:
        return "{0:.2f}".format(number)
    return "{0:.3f}".format(number)


def _signed(value: Any) -> str:
    if value is None:
        return "—"
    number = float(value)
    return ("+" if number > 0 else "") + _num(number)


def _percent(value: Any) -> str:
    if value is None:
        return "—"
    number = float(value)
    return ("+" if number > 0 else "") + "{0:.1f}%".format(number)


def _pretty(key: str) -> str:
    return key.replace("_", " ").title()


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n… [truncated; full text in trials.jsonl]"


def _escape_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")
