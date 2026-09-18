"""Tests for aggregation and report rendering.

The recurring theme: statistics must never invent information. ``None`` where
there is no data, sample standard deviation undefined for n=1, unpriced trials
making a cost total a floor, and a report that states its own sample size
rather than letting a reader assume it is adequate.
"""

from __future__ import annotations

from ashe_lab.metrics import aggregate, describe, trials_to_csv_rows
from ashe_lab.pricing import estimate_cost, lookup_rates
from ashe_lab.report import render_report
from ashe_lab.spec import parse_spec


# ---------------------------------------------------------------------------
# describe()
# ---------------------------------------------------------------------------


def test_describe_computes_the_usual_statistics():
    stats = describe([1, 2, 3, 4])
    assert stats["n"] == 4
    assert stats["mean"] == 2.5
    assert stats["median"] == 2.5
    assert stats["min"] == 1
    assert stats["max"] == 4
    assert stats["sum"] == 10


def test_describe_of_nothing_is_null_not_zero():
    """An empty group has no mean. Reporting 0 would be a fabrication."""
    stats = describe([])
    assert stats["n"] == 0
    for key in ("mean", "median", "min", "max", "stdev", "sum"):
        assert stats[key] is None


def test_stdev_is_undefined_for_a_single_observation():
    """The spread of one number is unknown, not zero."""
    assert describe([5])["stdev"] is None
    assert describe([5, 7])["stdev"] is not None


def test_stdev_is_the_sample_formula():
    # Sample stdev of [2, 4, 4, 4, 5, 5, 7, 9] is 2.13809... (population is 2.0)
    assert round(describe([2, 4, 4, 4, 5, 5, 7, 9])["stdev"], 4) == 2.1381


def test_median_handles_odd_counts():
    assert describe([3, 1, 2])["median"] == 2


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _trial(condition, model="m1", status="ok", scores=None, cost=None, usage=None):
    record = {
        "condition_id": condition,
        "model_alias": model,
        "item_id": "i1",
        "status": status,
        "attempt_count": 1,
        "duration_seconds": 0.5,
        "trial_key": "{0}|{1}|i1|000".format(condition, model),
        "scores": scores or {},
        "cost": cost or {"cost_usd": 0.001, "rate_as_of": "2026-09-18"},
        "response": {"usage": usage or {"input_tokens": 10, "output_tokens": 20}},
    }
    if status != "ok":
        record["error"] = {"type": "ProviderError", "message": "boom"}
        record["response"] = None
    return record


def _score(value):
    return {"value": value, "note": None, "detail": {}}


def test_aggregate_separates_ok_from_error_counts(two_condition_spec_dict):
    spec = parse_spec(two_condition_spec_dict)
    trials = [
        _trial("control", scores={"words": _score(10)}),
        _trial("control", status="error"),
        _trial("treatment", scores={"words": _score(20)}),
    ]
    results = aggregate(spec, trials, run_id="r1")

    assert results["totals"]["ok"] == 2
    assert results["totals"]["error"] == 1

    control = next(g for g in results["groups"] if g["labels"]["condition"] == "control")
    assert control["n_trials"] == 2
    assert control["n_ok"] == 1
    assert control["n_error"] == 1
    # The mean uses only the successful trial.
    assert control["metrics"]["words"]["mean"] == 10
    assert control["metrics"]["words"]["n"] == 1


def test_null_scores_are_counted_as_unmeasured_not_averaged_as_zero(
    two_condition_spec_dict,
):
    """The subtlest way a framework can lie: a null silently becoming a zero."""
    spec = parse_spec(two_condition_spec_dict)
    trials = [
        _trial("control", scores={"words": _score(10)}),
        _trial("control", scores={"words": _score(None)}),
    ]
    results = aggregate(spec, trials, run_id="r1")
    control = next(g for g in results["groups"] if g["labels"]["condition"] == "control")

    assert control["metrics"]["words"]["mean"] == 10  # not 5
    assert control["metrics"]["words"]["n"] == 1
    assert control["metrics"]["words"]["n_unmeasured"] == 1


def test_evaluator_failures_are_counted_separately(two_condition_spec_dict):
    spec = parse_spec(two_condition_spec_dict)
    trials = [
        _trial(
            "control",
            scores={"words": {"value": None, "note": "broke", "detail": {"failed": True}}},
        )
    ]
    results = aggregate(spec, trials, run_id="r1")
    control = next(g for g in results["groups"] if g["labels"]["condition"] == "control")
    assert control["metrics"]["words"]["n_evaluator_errors"] == 1


def test_comparison_against_control_reports_raw_differences(two_condition_spec_dict):
    spec = parse_spec(two_condition_spec_dict)
    trials = [
        _trial("control", scores={"words": _score(10)}),
        _trial("treatment", scores={"words": _score(15)}),
    ]
    comparison = aggregate(spec, trials, run_id="r1")["comparisons"][0]
    delta = comparison["metrics"]["words"]

    assert delta["control_mean"] == 10
    assert delta["treatment_mean"] == 15
    assert delta["absolute_change"] == 5
    assert delta["percent_change"] == 50.0


def test_percent_change_is_null_when_the_control_mean_is_zero(two_condition_spec_dict):
    """Division by zero must produce a stated null, not an exception or an inf."""
    spec = parse_spec(two_condition_spec_dict)
    trials = [
        _trial("control", scores={"words": _score(0)}),
        _trial("treatment", scores={"words": _score(5)}),
    ]
    delta = aggregate(spec, trials, run_id="r1")["comparisons"][0]["metrics"]["words"]
    assert delta["absolute_change"] == 5
    assert delta["percent_change"] is None
    assert "undefined" in delta["note"]


def test_comparison_is_null_when_a_group_has_no_data(two_condition_spec_dict):
    spec = parse_spec(two_condition_spec_dict)
    trials = [
        _trial("control", scores={"words": _score(10)}),
        _trial("treatment", status="error"),
    ]
    delta = aggregate(spec, trials, run_id="r1")["comparisons"][0]["metrics"]["words"]
    assert delta["absolute_change"] is None
    assert "insufficient data" in delta["note"]


def test_no_comparisons_without_a_declared_control(minimal_spec_dict):
    minimal_spec_dict["conditions"] = [
        {"id": "a", "user_template": "{{question}}"},
        {"id": "b", "user_template": "{{question}}"},
    ]
    spec = parse_spec(minimal_spec_dict)
    results = aggregate(spec, [_trial("a"), _trial("b")], run_id="r1")
    assert results["comparisons"] == []


def test_token_summary_excludes_unreported_usage(two_condition_spec_dict):
    spec = parse_spec(two_condition_spec_dict)
    trials = [
        _trial("control", usage={"input_tokens": 10, "output_tokens": 5}),
        _trial("control", usage={"input_tokens": None, "output_tokens": None}),
    ]
    tokens = aggregate(spec, trials, run_id="r1")["tokens"]
    assert tokens["input_tokens_total"] == 10  # the null was not counted as 0
    assert tokens["trials_with_input_usage"] == 1
    assert tokens["trials_missing_usage"] == 1


def test_cost_summary_is_flagged_as_a_floor_when_a_trial_is_unpriced(
    two_condition_spec_dict,
):
    spec = parse_spec(two_condition_spec_dict)
    trials = [
        _trial("control", cost={"cost_usd": 0.01, "rate_as_of": "2026-09-18"}),
        _trial("control", cost={"cost_usd": None, "rate_as_of": None}),
    ]
    cost = aggregate(spec, trials, run_id="r1")["cost"]
    assert cost["estimated_usd"] == 0.01
    assert cost["is_floor"] is True
    assert cost["trials_unpriced"] == 1


def test_results_always_carry_caveats(two_condition_spec_dict):
    spec = parse_spec(two_condition_spec_dict)
    caveats = aggregate(spec, [_trial("control")], run_id="r1")["caveats"]
    assert any("No significance testing" in c for c in caveats)
    assert any("estimates" in c for c in caveats)


def test_retried_trials_are_counted(two_condition_spec_dict):
    spec = parse_spec(two_condition_spec_dict)
    trial = _trial("control")
    trial["attempt_count"] = 3
    totals = aggregate(spec, [trial], run_id="r1")["totals"]
    assert totals["retried_trials"] == 1
    assert totals["attempts"] == 3


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


def test_longest_prefix_wins_in_the_price_table():
    """gpt-4o-mini must not be priced as gpt-4o."""
    mini = lookup_rates("openai_chat", "gpt-4o-mini-2024-07-18")
    full = lookup_rates("openai_chat", "gpt-4o-2024-11-20")
    assert mini is not None and full is not None
    assert mini[0] < full[0]


def test_unknown_model_produces_a_null_cost_with_an_actionable_note():
    """Silence would be worse; a wrong number would be much worse."""
    estimate = estimate_cost("openai_chat", "some-future-model-2031", 100, 50)
    assert estimate["cost_usd"] is None
    assert "pricing.py" in estimate["cost_note"]


def test_missing_usage_produces_a_null_cost():
    estimate = estimate_cost("openai_chat", "gpt-4o-mini", None, None)
    assert estimate["cost_usd"] is None
    assert "no token usage" in estimate["cost_note"]


def test_cost_is_computed_from_the_rate_table():
    # 1M input at $0.15/Mtok + 1M output at $0.60/Mtok = $0.75
    estimate = estimate_cost("openai_chat", "gpt-4o-mini", 1_000_000, 1_000_000)
    assert round(estimate["cost_usd"], 4) == 0.75
    assert estimate["rate_as_of"]


def test_every_estimate_is_dated():
    """An undated price is not auditable."""
    estimate = estimate_cost("openai_chat", "gpt-4o-mini", 10, 10)
    assert estimate["rate_as_of"]
    assert "as of" in estimate["cost_note"]


def test_partial_usage_is_noted_in_the_estimate():
    estimate = estimate_cost("openai_chat", "gpt-4o-mini", 100, None)
    assert estimate["cost_usd"] is not None
    assert "output tokens unreported" in estimate["cost_note"]


# ---------------------------------------------------------------------------
# CSV rows
# ---------------------------------------------------------------------------


def test_csv_columns_are_stable_and_include_a_column_per_evaluator(
    two_condition_spec_dict,
):
    spec = parse_spec(two_condition_spec_dict)
    columns, rows = trials_to_csv_rows(spec, [_trial("control", scores={"words": _score(3)})])
    assert columns[0] == "trial_key"
    assert "score_words" in columns
    assert rows[0]["score_words"] == 3
    # "condition" is renamed to avoid clashing with reserved names in R.
    assert "run_condition" in columns
    assert "condition" not in columns


def test_csv_row_for_a_failed_trial_has_nulls_not_zeros(two_condition_spec_dict):
    spec = parse_spec(two_condition_spec_dict)
    _, rows = trials_to_csv_rows(spec, [_trial("control", status="error")])
    assert rows[0]["status"] == "error"
    assert rows[0]["response_chars"] is None
    assert rows[0]["score_words"] is None
    assert rows[0]["error_type"] == "ProviderError"


def test_csv_error_messages_are_single_line(two_condition_spec_dict):
    spec = parse_spec(two_condition_spec_dict)
    trial = _trial("control", status="error")
    trial["error"]["message"] = "line one\nline two"
    _, rows = trials_to_csv_rows(spec, [trial])
    assert "\n" not in rows[0]["error_message"]


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def _manifest(spec, **overrides):
    manifest = {
        "run_id": "20260918T120000Z-abcd1234",
        "experiment_id": spec.id,
        "experiment_title": spec.title,
        "spec_hash": spec.spec_hash,
        "research_question": spec.research_question,
        "hypothesis": spec.hypothesis,
        "framework": {"version": "0.1.0", "git_commit": "a" * 40, "git_dirty": False},
        "timing": {"started_at": "2026-09-18T12:00:00Z", "finished_at": "2026-09-18T12:00:10Z"},
        "counts": {"planned_trials": 2, "recorded_trials": 2, "ok": 2, "error": 0},
        "providers": {"echo": {"kind": "offline-deterministic"}},
        "spec": spec.to_dict(),
    }
    manifest.update(overrides)
    return manifest


def test_report_renders_the_essential_sections(two_condition_spec_dict):
    spec = parse_spec(two_condition_spec_dict)
    trials = [
        _trial("control", scores={"words": _score(10)}),
        _trial("treatment", scores={"words": _score(14)}),
    ]
    for trial in trials:
        trial["prompt"] = {"system": None, "user": "What is 2 + 2?"}
        trial["response"] = {
            "text": "Four.",
            "usage": {"input_tokens": 5, "output_tokens": 2},
            "finish_reason": "stop",
        }
    results = aggregate(spec, trials, run_id="r1")
    markdown = render_report(manifest=_manifest(spec), results=results, trials=trials)

    assert markdown.startswith("# ")
    for heading in (
        "## Research question",
        "## Results by group",
        "## Difference from control",
        "## Cost, tokens, and timing",
        "## Conditions as executed",
        "## Sample transcripts",
        "## Caveats",
        "## Reproducing this run",
    ):
        assert heading in markdown, heading


def test_report_reproduces_the_exact_prompts(two_condition_spec_dict):
    """The manipulation *is* the prompt; a report that hides it is useless."""
    spec = parse_spec(two_condition_spec_dict)
    results = aggregate(spec, [_trial("control")], run_id="r1")
    markdown = render_report(manifest=_manifest(spec), results=results, trials=[])
    assert "This will be checked. {{question}}" in markdown
    assert "You are careful." in markdown


def test_report_states_its_own_sample_size_limitation(two_condition_spec_dict):
    spec = parse_spec(two_condition_spec_dict)
    trials = [_trial("control", scores={"words": _score(10)})]
    results = aggregate(spec, trials, run_id="r1")
    markdown = render_report(
        manifest=_manifest(spec, counts={"recorded_trials": 1, "ok": 1, "error": 0}),
        results=results,
        trials=trials,
    )
    assert "pipeline check" in markdown
    assert "fewer than 5 successful trials" in markdown


def test_report_warns_that_echo_results_are_meaningless(two_condition_spec_dict):
    """The single most dangerous misreading this framework could enable."""
    spec = parse_spec(two_condition_spec_dict)
    results = aggregate(spec, [_trial("control")], run_id="r1")
    markdown = render_report(manifest=_manifest(spec), results=results, trials=[])
    assert "offline `echo` provider" in markdown
    assert "means nothing" in markdown


def test_report_flags_a_dirty_working_tree(two_condition_spec_dict):
    """If the recorded commit does not describe the code that ran, say so."""
    spec = parse_spec(two_condition_spec_dict)
    results = aggregate(spec, [_trial("control")], run_id="r1")
    manifest = _manifest(spec)
    manifest["framework"]["git_dirty"] = True
    markdown = render_report(manifest=manifest, results=results, trials=[])
    assert "uncommitted changes" in markdown


def test_report_renders_a_null_metric_as_a_dash_not_a_zero(two_condition_spec_dict):
    spec = parse_spec(two_condition_spec_dict)
    results = aggregate(spec, [_trial("control", status="error")], run_id="r1")
    markdown = render_report(manifest=_manifest(spec), results=results, trials=[])
    assert "—" in markdown


def test_report_lists_failures_when_there_are_any(two_condition_spec_dict):
    spec = parse_spec(two_condition_spec_dict)
    trials = [_trial("control", status="error")]
    results = aggregate(spec, trials, run_id="r1")
    markdown = render_report(
        manifest=_manifest(spec, counts={"recorded_trials": 1, "ok": 0, "error": 1}),
        results=results,
        trials=trials,
    )
    assert "## Failures" in markdown
    assert "ProviderError" in markdown


def test_report_output_is_deterministic(two_condition_spec_dict):
    """So that regenerating a report twice does not churn checksums."""
    spec = parse_spec(two_condition_spec_dict)
    trials = [_trial("control", scores={"words": _score(10)})]
    results = aggregate(spec, trials, run_id="r1")
    manifest = _manifest(spec)
    first = render_report(manifest=manifest, results=results, trials=trials)
    second = render_report(manifest=manifest, results=results, trials=trials)
    assert first == second


def test_report_ends_with_a_single_newline(two_condition_spec_dict):
    spec = parse_spec(two_condition_spec_dict)
    results = aggregate(spec, [_trial("control")], run_id="r1")
    markdown = render_report(manifest=_manifest(spec), results=results, trials=[])
    assert markdown.endswith("\n")
    assert not markdown.endswith("\n\n")
