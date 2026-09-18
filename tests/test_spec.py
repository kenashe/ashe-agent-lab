"""Tests for the experiment specification: parsing, validation, templating.

The spec is the framework's contract, so these tests are mostly about what the
loader *refuses*. Every rejection here corresponds to a way an experiment could
silently measure the wrong thing.
"""

from __future__ import annotations

import os

import pytest

from ashe_lab.errors import SpecError
from ashe_lab.spec import (
    discover_experiments,
    find_placeholders,
    load_spec,
    parse_spec,
    render_template,
    variables_for,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_minimal_spec_parses(minimal_spec_dict):
    spec = parse_spec(minimal_spec_dict)
    assert spec.id == "unit-test-experiment"
    assert spec.kind == "single_agent.v0"
    assert len(spec.models) == 1
    assert spec.control_condition is not None
    assert spec.control_condition.id == "control"


def test_planned_trial_count_is_the_cross_product(two_condition_spec_dict):
    spec = parse_spec(two_condition_spec_dict)
    # 2 conditions x 1 model x 2 items x 2 trials
    assert spec.planned_trial_count() == 8


def test_spec_hash_ignores_formatting_but_not_content(minimal_spec_dict):
    first = parse_spec(minimal_spec_dict)
    second = parse_spec(dict(minimal_spec_dict))
    assert first.spec_hash == second.spec_hash

    changed = dict(minimal_spec_dict)
    changed["conditions"] = [
        {"id": "control", "is_control": True, "user_template": "Different: {{question}}"}
    ]
    assert parse_spec(changed).spec_hash != first.spec_hash


def test_model_and_condition_lookup(two_condition_spec_dict):
    spec = parse_spec(two_condition_spec_dict)
    assert spec.model_by_alias("fixture").provider == "echo"
    assert spec.condition_by_id("treatment").is_control is False
    with pytest.raises(SpecError):
        spec.model_by_alias("nonexistent")


# ---------------------------------------------------------------------------
# Rejections. Each of these is a bug the loader exists to catch.
# ---------------------------------------------------------------------------


def test_unknown_top_level_key_is_rejected(minimal_spec_dict):
    """A typo'd key must fail loudly, never be ignored.

    This is the single most important validation rule: an ignored key means an
    experiment that does not test what its author believed it tested.
    """
    minimal_spec_dict["conditons"] = []  # deliberate typo
    with pytest.raises(SpecError) as excinfo:
        parse_spec(minimal_spec_dict)
    assert "conditons" in str(excinfo.value)
    assert "unknown key" in str(excinfo.value)


def test_unknown_nested_key_is_rejected(minimal_spec_dict):
    minimal_spec_dict["models"][0]["temprature"] = 0.5
    with pytest.raises(SpecError) as excinfo:
        parse_spec(minimal_spec_dict)
    assert "temprature" in str(excinfo.value)


def test_missing_id_is_rejected(minimal_spec_dict):
    del minimal_spec_dict["id"]
    with pytest.raises(SpecError) as excinfo:
        parse_spec(minimal_spec_dict)
    assert "root.id" in str(excinfo.value)


def test_non_slug_id_is_rejected(minimal_spec_dict):
    minimal_spec_dict["id"] = "Not A Slug!"
    with pytest.raises(SpecError) as excinfo:
        parse_spec(minimal_spec_dict)
    assert "root.id" in str(excinfo.value)


def test_empty_models_is_rejected(minimal_spec_dict):
    minimal_spec_dict["models"] = []
    with pytest.raises(SpecError) as excinfo:
        parse_spec(minimal_spec_dict)
    assert "at least one model" in str(excinfo.value)


def test_empty_items_is_rejected(minimal_spec_dict):
    minimal_spec_dict["items"] = []
    with pytest.raises(SpecError) as excinfo:
        parse_spec(minimal_spec_dict)
    assert "at least one item" in str(excinfo.value)


def test_duplicate_condition_ids_are_rejected(minimal_spec_dict):
    minimal_spec_dict["conditions"] = [
        {"id": "control", "user_template": "a {{question}}"},
        {"id": "control", "user_template": "b {{question}}"},
    ]
    with pytest.raises(SpecError) as excinfo:
        parse_spec(minimal_spec_dict)
    assert "duplicate condition id" in str(excinfo.value)


def test_duplicate_model_aliases_are_rejected(minimal_spec_dict):
    minimal_spec_dict["models"].append(
        {"alias": "fixture", "provider": "echo", "model": "other"}
    )
    with pytest.raises(SpecError) as excinfo:
        parse_spec(minimal_spec_dict)
    assert "duplicate model alias" in str(excinfo.value)


def test_two_controls_are_rejected(minimal_spec_dict):
    minimal_spec_dict["conditions"] = [
        {"id": "a", "is_control": True, "user_template": "{{question}}"},
        {"id": "b", "is_control": True, "user_template": "{{question}}"},
    ]
    with pytest.raises(SpecError) as excinfo:
        parse_spec(minimal_spec_dict)
    assert "is_control" in str(excinfo.value)


def test_undefined_template_placeholder_is_rejected_at_load_time(minimal_spec_dict):
    """Catch the expensive mistake before any money is spent."""
    minimal_spec_dict["conditions"] = [
        {"id": "control", "user_template": "{{question}} and {{typo_variable}}"}
    ]
    with pytest.raises(SpecError) as excinfo:
        parse_spec(minimal_spec_dict)
    message = str(excinfo.value)
    assert "typo_variable" in message
    assert "item-one" in message  # names the item it failed for


def test_unsupported_kind_is_rejected(minimal_spec_dict):
    minimal_spec_dict["kind"] = "multi_agent.v9"
    with pytest.raises(SpecError) as excinfo:
        parse_spec(minimal_spec_dict)
    assert "unsupported kind" in str(excinfo.value)


def test_future_schema_version_is_rejected(minimal_spec_dict):
    minimal_spec_dict["schema_version"] = 99
    with pytest.raises(SpecError) as excinfo:
        parse_spec(minimal_spec_dict)
    assert "schema_version" in str(excinfo.value)


def test_zero_trials_is_rejected(minimal_spec_dict):
    minimal_spec_dict["defaults"]["trials"] = 0
    with pytest.raises(SpecError):
        parse_spec(minimal_spec_dict)


def test_unknown_group_by_key_is_rejected(minimal_spec_dict):
    minimal_spec_dict["analysis"] = {"group_by": ["condition", "phase_of_moon"]}
    with pytest.raises(SpecError) as excinfo:
        parse_spec(minimal_spec_dict)
    assert "phase_of_moon" in str(excinfo.value)


def test_all_problems_are_reported_at_once(minimal_spec_dict):
    """Authors should see every error in one pass, not one per attempt."""
    del minimal_spec_dict["id"]
    minimal_spec_dict["models"] = []
    minimal_spec_dict["items"] = []
    with pytest.raises(SpecError) as excinfo:
        parse_spec(minimal_spec_dict)
    message = str(excinfo.value)
    assert "root.id" in message
    assert "at least one model" in message
    assert "at least one item" in message
    assert "3 problem(s)" in message


# ---------------------------------------------------------------------------
# Templating
# ---------------------------------------------------------------------------


def test_placeholders_use_double_braces_so_single_braces_pass_through():
    """Prompts routinely contain JSON. Single braces must survive untouched."""
    template = 'Return {"answer": value} for {{question}}'
    assert find_placeholders(template) == ["question"]
    rendered = render_template(template, {"question": "why?"})
    assert rendered == 'Return {"answer": value} for why?'


def test_render_template_rejects_missing_variables():
    with pytest.raises(SpecError) as excinfo:
        render_template("{{present}} {{absent}}", {"present": "x"})
    assert "absent" in str(excinfo.value)


def test_render_template_handles_whitespace_in_braces():
    assert render_template("{{ name }}", {"name": "value"}) == "value"


def test_render_template_stringifies_non_strings():
    assert render_template("{{count}}", {"count": 42}) == "42"


def test_variable_precedence_is_item_over_condition_over_experiment(
    two_condition_spec_dict,
):
    two_condition_spec_dict["variables"] = {"scope": "experiment", "only_top": "yes"}
    two_condition_spec_dict["conditions"][0]["variables"] = {"scope": "condition"}
    two_condition_spec_dict["items"][0]["vars"]["scope"] = "item"
    spec = parse_spec(two_condition_spec_dict)

    merged = variables_for(spec.variables, spec.conditions[0], spec.items[0])
    assert merged["scope"] == "item"
    assert merged["only_top"] == "yes"

    # Condition-level wins where the item is silent.
    merged_other = variables_for(spec.variables, spec.conditions[0], spec.items[1])
    assert merged_other["scope"] == "condition"


def test_condition_and_item_ids_are_always_available_as_variables(minimal_spec_dict):
    spec = parse_spec(minimal_spec_dict)
    merged = variables_for(spec.variables, spec.conditions[0], spec.items[0])
    assert merged["condition_id"] == "control"
    assert merged["item_id"] == "item-one"


# ---------------------------------------------------------------------------
# Loading from disk, and the shipped experiment
# ---------------------------------------------------------------------------


def test_shipped_example_experiment_is_valid():
    """The repository must never contain a broken example."""
    spec = load_spec(os.path.join(REPO_ROOT, "experiments", "fact-check-awareness-001"))
    assert spec.id == "fact-check-awareness-001"
    assert spec.control_condition is not None
    assert spec.control_condition.id == "control"
    assert len(spec.conditions) == 2
    assert len(spec.items) >= 3
    assert len(spec.evaluators) >= 4
    assert spec.research_question


def test_shipped_experiment_conditions_differ_only_by_the_manipulation():
    """The contrast must be the announcement, not an accidental prompt change.

    If the answer instruction or the question presentation drifted between
    conditions, the experiment would be confounded at the source. This asserts
    the treatment template contains the control template's operative parts.
    """
    spec = load_spec(os.path.join(REPO_ROOT, "experiments", "fact-check-awareness-001"))
    control = spec.condition_by_id("control")
    treatment = spec.condition_by_id("fact-check-announced")

    assert control.system_prompt == treatment.system_prompt
    assert "{{question}}" in control.user_template
    assert "{{question}}" in treatment.user_template
    assert "{{answer_instruction}}" in control.user_template
    assert "{{answer_instruction}}" in treatment.user_template
    assert "fact-check" in treatment.user_template.lower()
    assert "fact-check" not in control.user_template.lower()


def test_spec_source_text_is_preserved_verbatim():
    """The snapshot written into a run must be the author's bytes, comments included."""
    path = os.path.join(REPO_ROOT, "experiments", "fact-check-awareness-001")
    spec = load_spec(path)
    assert spec.source_text is not None
    assert spec.source_text.startswith("#")  # the header comment survived
    with open(os.path.join(path, "experiment.yaml"), "r", encoding="utf-8") as handle:
        assert spec.source_text == handle.read()


def test_load_spec_resolves_a_bare_experiment_id(monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    spec = load_spec("fact-check-awareness-001")
    assert spec.id == "fact-check-awareness-001"


def test_load_spec_gives_an_actionable_error_for_a_missing_experiment(monkeypatch, tmp_path):
    monkeypatch.chdir(str(tmp_path))
    with pytest.raises(SpecError) as excinfo:
        load_spec("no-such-experiment")
    message = str(excinfo.value)
    assert "no-such-experiment" in message
    assert "Tried" in message  # tells the reader where it looked


def test_invalid_yaml_is_reported_as_such(tmp_path):
    path = tmp_path / "experiment.yaml"
    path.write_text("id: broken\n  bad indentation: [\n", encoding="utf-8")
    with pytest.raises(SpecError) as excinfo:
        load_spec(str(path))
    assert "not valid YAML" in str(excinfo.value)


def test_empty_file_is_reported(tmp_path):
    path = tmp_path / "experiment.yaml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(SpecError) as excinfo:
        load_spec(str(path))
    assert "empty" in str(excinfo.value)


def test_discover_experiments_finds_the_shipped_one(monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    assert "fact-check-awareness-001" in discover_experiments()
