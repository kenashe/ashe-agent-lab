"""End-to-end runner tests, using the offline providers.

These are the tests that matter most, because they assert the framework's
central promise: that a run produces complete, honest evidence. In particular,
they assert the things that would be *easy* to get wrong in a way nobody
notices:

*   failed trials are recorded, not dropped;
*   every retry attempt is preserved with its error;
*   a failed trial gets no scores rather than zero scores;
*   rendered prompts are stored alongside responses.
"""

from __future__ import annotations

import json
import os

import pytest

from ashe_lab.errors import AsheLabError
from ashe_lab.runner import RunOptions, execute, plan_run
from ashe_lab.spec import parse_spec
from ashe_lab.storage import load_events, load_manifest, load_results, load_trials


def _run(spec_dict, runs_dir, **option_kwargs):
    spec = parse_spec(spec_dict, source_text="# in-memory test spec\n")
    options = RunOptions(runs_root=runs_dir, **option_kwargs)
    return spec, execute(spec, options)


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def test_plan_is_the_full_cross_product(two_condition_spec_dict):
    spec = parse_spec(two_condition_spec_dict)
    planned = plan_run(spec)
    assert len(planned) == 8  # 2 conditions x 1 model x 2 items x 2 trials
    keys = {p.key for p in planned}
    assert len(keys) == 8  # every trial key is unique


def test_plan_renders_prompts_before_anything_is_called(two_condition_spec_dict):
    """A templating error must cost nothing, not half a run."""
    spec = parse_spec(two_condition_spec_dict)
    planned = plan_run(spec)
    treatment = next(p for p in planned if p.condition.id == "treatment")
    assert "{{" not in treatment.user_prompt
    assert "What is 2 + 2?" in treatment.user_prompt or "mountain" in treatment.user_prompt
    assert treatment.system_prompt == "You are careful."


def test_plan_is_condition_major_so_partial_runs_have_a_known_shape(two_condition_spec_dict):
    spec = parse_spec(two_condition_spec_dict)
    order = [p.condition.id for p in plan_run(spec)]
    assert order == ["control"] * 4 + ["treatment"] * 4


def test_filters_narrow_the_plan(two_condition_spec_dict):
    spec = parse_spec(two_condition_spec_dict)
    planned = plan_run(
        spec, RunOptions(only_conditions=["control"], only_items=["item-one"], trials_override=1)
    )
    assert len(planned) == 1
    assert planned[0].condition.id == "control"


def test_max_trials_caps_the_plan(two_condition_spec_dict):
    spec = parse_spec(two_condition_spec_dict)
    assert len(plan_run(spec, RunOptions(max_trials=3))) == 3


def test_an_unknown_filter_value_is_an_error_naming_the_valid_ones(two_condition_spec_dict):
    spec = parse_spec(two_condition_spec_dict)
    with pytest.raises(AsheLabError) as excinfo:
        plan_run(spec, RunOptions(only_conditions=["nonexistent"]))
    message = str(excinfo.value)
    assert "nonexistent" in message
    assert "control" in message


def test_filters_that_exclude_everything_fail_fast(minimal_spec_dict, runs_dir):
    spec = parse_spec(minimal_spec_dict)
    with pytest.raises(AsheLabError) as excinfo:
        execute(spec, RunOptions(runs_root=runs_dir, max_trials=0))
    assert "nothing to run" in str(excinfo.value)


# ---------------------------------------------------------------------------
# A successful run
# ---------------------------------------------------------------------------


def test_successful_run_writes_every_expected_artifact(two_condition_spec_dict, runs_dir):
    _, result = _run(two_condition_spec_dict, runs_dir)

    assert result.trial_count == 8
    assert result.ok_count == 8
    assert result.error_count == 0

    for filename in (
        "manifest.json",
        "experiment.snapshot.yaml",
        "trials.jsonl",
        "events.jsonl",
        "results.json",
        "trials.csv",
        "report.md",
        "checksums.sha256",
    ):
        assert os.path.isfile(os.path.join(result.run_path, filename)), filename


def test_trial_records_preserve_the_prompt_and_the_response(two_condition_spec_dict, runs_dir):
    _, result = _run(two_condition_spec_dict, runs_dir)
    trials = load_trials(result.run_path)

    for trial in trials:
        assert trial["status"] == "ok"
        assert trial["prompt"]["user"]
        assert "{{" not in trial["prompt"]["user"]
        assert trial["response"]["text"]
        # The literal request sent to the provider, not a reconstruction.
        assert trial["request"]["messages"][-1]["content"] == trial["prompt"]["user"]
        assert trial["request_hash"]
        assert trial["spec_hash"]
        assert trial["recorded_at"].endswith("Z")


def test_trial_records_carry_usage_and_cost(two_condition_spec_dict, runs_dir):
    _, result = _run(two_condition_spec_dict, runs_dir)
    trial = load_trials(result.run_path)[0]
    assert trial["response"]["usage"]["input_tokens"] > 0
    assert trial["cost"]["cost_usd"] == 0.0  # echo is free, and priced as such
    assert trial["cost"]["rate_as_of"]


def test_trial_records_carry_evaluator_scores(two_condition_spec_dict, runs_dir):
    _, result = _run(two_condition_spec_dict, runs_dir)
    trial = load_trials(result.run_path)[0]
    assert trial["scores"]["words"]["value"] > 0


def test_every_trial_key_is_unique_in_storage(two_condition_spec_dict, runs_dir):
    _, result = _run(two_condition_spec_dict, runs_dir)
    keys = [t["trial_key"] for t in load_trials(result.run_path)]
    assert len(keys) == len(set(keys))


def test_manifest_records_what_is_needed_to_interpret_the_run(
    two_condition_spec_dict, runs_dir
):
    spec, result = _run(two_condition_spec_dict, runs_dir, label="unit test")
    manifest = load_manifest(result.run_path)

    assert manifest["schema"] == "manifest.v0"
    assert manifest["run_id"] == result.run_id
    assert manifest["spec_hash"] == spec.spec_hash
    assert manifest["counts"]["ok"] == 8
    assert manifest["options"]["label"] == "unit test"
    assert manifest["framework"]["name"] == "ashe-agent-lab"
    assert manifest["environment"]["python_version"]
    assert manifest["timing"]["started_at"].endswith("Z")
    # The full spec is embedded, so the manifest alone explains the run.
    assert manifest["spec"]["conditions"][0]["id"] == "control"
    # And the providers used are described without credentials.
    assert manifest["providers"]["echo"]["network"] is False


def test_spec_snapshot_is_the_verbatim_source(minimal_spec_dict, runs_dir):
    spec = parse_spec(minimal_spec_dict, source_text="# exact bytes\nid: whatever\n")
    result = execute(spec, RunOptions(runs_root=runs_dir))
    with open(os.path.join(result.run_path, "experiment.snapshot.yaml"), encoding="utf-8") as f:
        assert f.read() == "# exact bytes\nid: whatever\n"


def test_events_log_narrates_the_run(two_condition_spec_dict, runs_dir):
    _, result = _run(two_condition_spec_dict, runs_dir)
    events = load_events(result.run_path)
    types = [e["event"] for e in events]
    assert types[0] == "run_started"
    assert "run_finalized" in types
    assert types.count("trial_started") == 8
    assert types.count("trial_finished") == 8
    assert all(e["ts"].endswith("Z") for e in events)


def test_results_group_by_condition_and_model(two_condition_spec_dict, runs_dir):
    _, result = _run(two_condition_spec_dict, runs_dir)
    results = load_results(result.run_path)
    assert results["group_by"] == ["condition", "model"]
    assert len(results["groups"]) == 2
    assert {g["labels"]["condition"] for g in results["groups"]} == {"control", "treatment"}
    assert all(g["n_ok"] == 4 for g in results["groups"])


def test_results_include_a_control_comparison(two_condition_spec_dict, runs_dir):
    _, result = _run(two_condition_spec_dict, runs_dir)
    comparisons = load_results(result.run_path)["comparisons"]
    assert len(comparisons) == 1
    assert comparisons[0]["control_condition"] == "control"
    assert comparisons[0]["treatment_condition"] == "treatment"
    # And it says out loud that it is not a result.
    assert "not a result" in comparisons[0]["interpretation"]


def test_two_runs_of_the_same_spec_get_separate_directories(minimal_spec_dict, runs_dir):
    """Re-running never overwrites. It produces new evidence alongside the old."""
    first_spec, first = _run(minimal_spec_dict, runs_dir)
    _, second = _run(minimal_spec_dict, runs_dir)
    assert first.run_path != second.run_path
    assert first.run_id != second.run_id
    # Same design, so the same spec hash: the runs are directly comparable.
    assert load_manifest(first.run_path)["spec_hash"] == load_manifest(second.run_path)["spec_hash"]


def test_echo_provider_makes_a_run_reproducible_trial_by_trial(minimal_spec_dict, runs_dir):
    """With a deterministic provider, two runs produce identical responses.

    This is the closest thing to bit-reproducibility the framework can offer,
    and it is why the fixture provider exists: it makes the *pipeline* testable
    even though hosted models are not deterministic.
    """
    _, first = _run(minimal_spec_dict, runs_dir)
    _, second = _run(minimal_spec_dict, runs_dir)
    first_texts = [t["response"]["text"] for t in load_trials(first.run_path)]
    second_texts = [t["response"]["text"] for t in load_trials(second.run_path)]
    assert first_texts == second_texts


# ---------------------------------------------------------------------------
# Failure handling: the part that protects against silent bias
# ---------------------------------------------------------------------------


def test_a_failing_trial_is_recorded_not_dropped(minimal_spec_dict, runs_dir):
    """Dropping failures would bias every aggregate that follows."""
    minimal_spec_dict["models"] = [
        {
            "alias": "broken",
            "provider": "failing",
            "model": "always-fails",
            "options": {"message": "upstream exploded", "retryable": False},
        }
    ]
    _, result = _run(minimal_spec_dict, runs_dir)

    assert result.ok_count == 0
    assert result.error_count == 1

    trial = load_trials(result.run_path)[0]
    assert trial["status"] == "error"
    assert trial["response"] is None
    assert "upstream exploded" in trial["error"]["message"]
    # The prompt is still preserved: we know exactly what was attempted.
    assert trial["prompt"]["user"]


def test_a_failed_trial_has_no_scores_rather_than_zero_scores(minimal_spec_dict, runs_dir):
    """A zero would be indistinguishable from a genuine measurement of zero."""
    minimal_spec_dict["models"] = [
        {"alias": "broken", "provider": "failing", "model": "x", "options": {"retryable": False}}
    ]
    _, result = _run(minimal_spec_dict, runs_dir)
    assert load_trials(result.run_path)[0]["scores"] == {}


def test_every_retry_attempt_is_preserved(minimal_spec_dict, runs_dir):
    """A trial that eventually succeeded must still show what went wrong first."""
    minimal_spec_dict["defaults"] = {
        "trials": 1,
        "retries": {"max_attempts": 4, "initial_backoff_seconds": 0.0},
    }
    minimal_spec_dict["models"] = [
        {
            "alias": "flaky",
            "provider": "failing",
            "model": "recovers",
            "options": {"fail_times": 2, "message": "transient blip"},
        }
    ]
    _, result = _run(minimal_spec_dict, runs_dir)

    trial = load_trials(result.run_path)[0]
    assert trial["status"] == "ok"
    assert trial["attempt_count"] == 3
    outcomes = [a["outcome"] for a in trial["attempts"]]
    assert outcomes == ["error", "error", "ok"]
    assert "transient blip" in trial["attempts"][0]["error"]["message"]

    events = [e for e in load_events(result.run_path) if e["event"] == "trial_retry"]
    assert len(events) == 2


def test_retries_stop_at_max_attempts(minimal_spec_dict, runs_dir):
    minimal_spec_dict["defaults"] = {
        "trials": 1,
        "retries": {"max_attempts": 2, "initial_backoff_seconds": 0.0},
    }
    minimal_spec_dict["models"] = [
        {"alias": "broken", "provider": "failing", "model": "x", "options": {"retryable": True}}
    ]
    _, result = _run(minimal_spec_dict, runs_dir)
    trial = load_trials(result.run_path)[0]
    assert trial["status"] == "error"
    assert trial["attempt_count"] == 2


def test_a_non_retryable_error_is_not_retried(minimal_spec_dict, runs_dir):
    """Retrying a bad API key is just a slower way to fail."""
    minimal_spec_dict["defaults"] = {
        "trials": 1,
        "retries": {"max_attempts": 5, "initial_backoff_seconds": 0.0},
    }
    minimal_spec_dict["models"] = [
        {"alias": "broken", "provider": "failing", "model": "x", "options": {"retryable": False}}
    ]
    _, result = _run(minimal_spec_dict, runs_dir)
    assert load_trials(result.run_path)[0]["attempt_count"] == 1


def test_a_partially_failing_run_reports_both_outcomes(two_condition_spec_dict, runs_dir):
    """The realistic case: some trials work, some do not."""
    two_condition_spec_dict["models"] = [
        {"alias": "good", "provider": "echo", "model": "echo-deterministic-v1"},
        {
            "alias": "bad",
            "provider": "failing",
            "model": "x",
            "options": {"retryable": False},
        },
    ]
    two_condition_spec_dict["defaults"] = {
        "trials": 1,
        "retries": {"max_attempts": 1, "initial_backoff_seconds": 0.0},
    }
    _, result = _run(two_condition_spec_dict, runs_dir)

    assert result.ok_count == 4
    assert result.error_count == 4

    results = load_results(result.run_path)
    assert results["totals"]["ok"] == 4
    assert results["totals"]["error"] == 4
    assert results["errors"]["by_type"]["ProviderError"] == 4

    # The failing model's group is visible and clearly marked as empty.
    bad_groups = [g for g in results["groups"] if g["labels"]["model"] == "bad"]
    assert all(g["n_ok"] == 0 and g["n_error"] > 0 for g in bad_groups)
    assert all(g["metrics"]["words"]["mean"] is None for g in bad_groups)


def test_report_warns_loudly_when_trials_failed(two_condition_spec_dict, runs_dir):
    two_condition_spec_dict["models"] = [
        {"alias": "bad", "provider": "failing", "model": "x", "options": {"retryable": False}}
    ]
    two_condition_spec_dict["defaults"] = {
        "trials": 1,
        "retries": {"max_attempts": 1, "initial_backoff_seconds": 0.0},
    }
    _, result = _run(two_condition_spec_dict, runs_dir)
    with open(os.path.join(result.run_path, "report.md"), encoding="utf-8") as handle:
        report = handle.read()
    assert "Run health" in report
    assert "trials failed" in report
    assert "No trial succeeded" in report


def test_an_unexpected_adapter_exception_is_captured_with_a_traceback(
    minimal_spec_dict, runs_dir
):
    """A bug in a third-party adapter must not lose the run."""
    from ashe_lab.providers import register_provider

    class Exploding:
        name = "unit-test-exploding"

        def complete(self, request):
            raise ValueError("adapter bug")

        def describe(self):
            return {"provider": self.name}

    register_provider("unit-test-exploding", Exploding)
    minimal_spec_dict["models"] = [
        {"alias": "boom", "provider": "unit-test-exploding", "model": "x"}
    ]
    _, result = _run(minimal_spec_dict, runs_dir)

    trial = load_trials(result.run_path)[0]
    assert trial["status"] == "error"
    assert trial["error"]["type"] == "ValueError"
    assert trial["error"]["unexpected"] is True
    assert "traceback" in trial["error"]


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------


def test_trials_csv_has_one_row_per_trial_and_a_score_column_per_evaluator(
    two_condition_spec_dict, runs_dir
):
    import csv

    _, result = _run(two_condition_spec_dict, runs_dir)
    with open(os.path.join(result.run_path, "trials.csv"), encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 8
    assert "score_words" in rows[0]
    assert rows[0]["run_condition"] in ("control", "treatment")
    assert rows[0]["status"] == "ok"
    # Response text is deliberately excluded; the JSONL holds it losslessly.
    assert "response_text" not in rows[0]


def test_trials_jsonl_is_one_valid_json_object_per_line(minimal_spec_dict, runs_dir):
    _, result = _run(minimal_spec_dict, runs_dir)
    with open(os.path.join(result.run_path, "trials.jsonl"), encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                assert isinstance(json.loads(line), dict)


# ---------------------------------------------------------------------------
# Progress reporting
# ---------------------------------------------------------------------------


def test_progress_callback_receives_status_lines(minimal_spec_dict, runs_dir):
    spec = parse_spec(minimal_spec_dict)
    messages = []
    execute(spec, RunOptions(runs_root=runs_dir), on_progress=messages.append)
    assert any("planned" in m for m in messages)
    assert any("complete" in m for m in messages)
