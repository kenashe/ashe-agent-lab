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
    # Three arms: control, the fact-check treatment, and the matched
    # attention / demand-characteristic control.
    assert len(spec.conditions) == 3
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


def test_shipped_experiment_treatment_is_the_control_plus_only_a_preamble():
    """The treatment template must be *byte-identical* to the control, plus a preamble.

    The assertion above checks that the right pieces are present. That is not
    enough: it would happily pass if the treatment gained an extra blank line
    between {{question}} and {{answer_instruction}}, or lost one, or changed
    the indentation of the shared portion. Any of those is an uncontrolled
    second manipulation - the treatment would differ from the control in
    layout as well as in content, and a measured difference could be caused by
    either.

    Expressing the requirement as exact suffix equality catches whitespace and
    layout drift in *either* direction, including drift introduced by editing
    the YAML block scalars, which is exactly the kind of change that looks
    harmless in a diff.
    """
    spec = load_spec(os.path.join(REPO_ROOT, "experiments", "fact-check-awareness-001"))
    control = spec.condition_by_id("control").user_template
    treatment = spec.condition_by_id("fact-check-announced").user_template

    assert treatment.endswith(control), (
        "the treatment template's shared portion is no longer byte-identical to "
        "the control's, so the two conditions differ in layout as well as in the "
        "intended manipulation.\n"
        "  control            : {0!r}\n"
        "  treatment tail     : {1!r}\n"
        "Make the common portion identical, or state explicitly in the "
        "experiment notes why it must differ.".format(control, treatment[-len(control):])
    )

    preamble = treatment[: -len(control)]
    assert "fact-check" in preamble.lower(), (
        "the only text the treatment adds must be the fact-check announcement; "
        "found preamble {0!r}".format(preamble)
    )
    assert preamble.endswith("\n\n"), (
        "the preamble must be separated from the shared portion by exactly one "
        "blank line, matching the separator used inside the shared portion; "
        "found {0!r}".format(preamble[-4:])
    )
    assert not preamble.endswith("\n\n\n"), (
        "the preamble is separated from the shared portion by more than one "
        "blank line; found {0!r}".format(preamble[-4:])
    )


#: The shipped experiment's arms. Named here so a test failure says which arm
#: drifted rather than making the reader count list indices.
_SHIPPED_CONTROL = "control"
_SHIPPED_TREATMENTS = ("fact-check-announced", "presentation-review-announced")

#: Vocabulary the attention control's preamble must not contain. The whole
#: point of that arm is to hold prominence and third-party observation
#: constant while removing every trace of factual scrutiny; a single word from
#: this list would reintroduce the thing being controlled for and silently
#: collapse the three-arm design back into two.
_FACTUAL_SCRUTINY_LEXICON = (
    "fact",
    "accura",
    "accurate",
    "verif",
    "true",
    "truth",
    "correct",
    "incorrect",
    "error",
    "mistake",
    "evidence",
    "cite",
    "citation",
    "source",
    "uncertain",
    "certainty",
    "claim",
    "check",
    "confirm",
    "validat",
    "misinform",
    "false",
)


def test_shipped_experiment_has_one_control_and_two_matched_treatments():
    """The three-arm structure itself, asserted explicitly."""
    spec = load_spec(os.path.join(REPO_ROOT, "experiments", "fact-check-awareness-001"))

    ids = [c.id for c in spec.conditions]
    assert ids == [_SHIPPED_CONTROL] + list(_SHIPPED_TREATMENTS), (
        "the shipped experiment's arms changed: {0!r}. Update this test "
        "deliberately if the design really changed.".format(ids)
    )
    assert spec.control_condition is not None
    assert spec.control_condition.id == _SHIPPED_CONTROL


def test_shipped_experiment_every_arm_shares_a_byte_identical_task():
    """Every arm must end with the control's exact bytes.

    Generalises the two-arm suffix check to all treatments. The question and
    the answer instruction are the *task*; if any arm presents the task even
    slightly differently - an extra blank line, changed indentation, a
    reworded instruction - then that arm differs from the others in more than
    its preamble, and any measured difference has two candidate causes.
    """
    spec = load_spec(os.path.join(REPO_ROOT, "experiments", "fact-check-awareness-001"))
    control = spec.condition_by_id(_SHIPPED_CONTROL)

    for treatment_id in _SHIPPED_TREATMENTS:
        treatment = spec.condition_by_id(treatment_id)

        assert treatment.system_prompt == control.system_prompt, (
            "arm {0!r} has a different system prompt from the control:\n"
            "  control  : {1!r}\n  treatment: {2!r}".format(
                treatment_id, control.system_prompt, treatment.system_prompt
            )
        )
        assert treatment.user_template.endswith(control.user_template), (
            "arm {0!r} no longer ends with the control's exact bytes, so the "
            "shared task differs between conditions.\n"
            "  control      : {1!r}\n"
            "  arm tail     : {2!r}".format(
                treatment_id,
                control.user_template,
                treatment.user_template[-len(control.user_template):],
            )
        )

        preamble = treatment.user_template[: -len(control.user_template)]
        assert preamble.endswith("\n\n"), (
            "arm {0!r}: preamble must be separated from the shared task by "
            "exactly one blank line; found {1!r}".format(treatment_id, preamble[-4:])
        )
        assert not preamble.endswith("\n\n\n"), (
            "arm {0!r}: more than one blank line between preamble and shared "
            "task; found {1!r}".format(treatment_id, preamble[-4:])
        )


def test_shipped_experiment_treatment_preambles_are_matched_for_prominence():
    """The two preambles must be comparable in length and shape.

    An attention control only controls for prominence if it *is* comparably
    prominent. A one-line control against a five-line treatment would confound
    the contrast with sheer instruction volume - the very thing it exists to
    rule out. A 25% word-count tolerance is loose enough to allow natural
    phrasing and tight enough to catch a rewrite that changes the register.
    """
    spec = load_spec(os.path.join(REPO_ROOT, "experiments", "fact-check-awareness-001"))
    control = spec.condition_by_id(_SHIPPED_CONTROL).user_template

    preambles = {}
    for treatment_id in _SHIPPED_TREATMENTS:
        template = spec.condition_by_id(treatment_id).user_template
        preambles[treatment_id] = template[: -len(control)]

    counts = {k: len(v.split()) for k, v in preambles.items()}
    lines = {k: len(v.strip().splitlines()) for k, v in preambles.items()}

    assert len(set(lines.values())) == 1, (
        "treatment preambles differ in line count {0!r}; they should be "
        "visually matched blocks".format(lines)
    )

    low, high = min(counts.values()), max(counts.values())
    assert high <= low * 1.25, (
        "treatment preambles are not matched for length: {0!r}. The attention "
        "control only controls for prominence if it is comparably "
        "prominent.".format(counts)
    )

    # Both must frame the review as external and after the fact, which is the
    # component being held constant.
    for treatment_id, preamble in preambles.items():
        lowered = preamble.lower()
        assert "third party" in lowered, (
            "arm {0!r}: preamble must keep the third-party framing constant "
            "across treatments; found {1!r}".format(treatment_id, preamble)
        )


def test_attention_control_preamble_avoids_factual_scrutiny_vocabulary():
    """The attention control must not smuggle in the thing it controls for.

    This is the test that protects the *meaning* of the three-arm design
    rather than its layout. If someone rewords the presentation-review
    preamble and reaches for "we'll check your response" or "inconsistencies
    or errors", the arm stops being a clean attention control and the
    experiment quietly loses its ability to answer its own research question.
    """
    spec = load_spec(os.path.join(REPO_ROOT, "experiments", "fact-check-awareness-001"))
    control = spec.condition_by_id(_SHIPPED_CONTROL).user_template
    attention = spec.condition_by_id("presentation-review-announced").user_template
    preamble = attention[: -len(control)].lower()

    found = sorted(w for w in _FACTUAL_SCRUTINY_LEXICON if w in preamble)
    assert not found, (
        "the attention-control preamble contains vocabulary implying factual "
        "scrutiny: {0}. That defeats its purpose - it must announce review of "
        "something unrelated to factual content.\n  preamble: {1!r}".format(
            found, preamble
        )
    )

    # And the fact-check arm must still clearly be about factual scrutiny,
    # otherwise the two arms have converged from the other direction.
    treatment = spec.condition_by_id("fact-check-announced").user_template
    fact_preamble = treatment[: -len(control)].lower()
    assert "fact" in fact_preamble, (
        "the fact-check arm's preamble no longer mentions factual checking; "
        "found {0!r}".format(fact_preamble)
    )


def test_shipped_experiment_uses_one_separator_style_across_conditions():
    """Whitespace around the shared placeholders must match across conditions.

    A second, narrower guard on the same failure mode, asserted directly on the
    text between the two placeholders every condition shares. It exists because
    it fails with a message naming the exact separator that drifted, which is
    faster to act on than a suffix-equality failure when someone has edited the
    YAML by hand.
    """
    spec = load_spec(os.path.join(REPO_ROOT, "experiments", "fact-check-awareness-001"))

    separators = {}
    for condition in spec.conditions:
        template = condition.user_template
        start = template.index("{{question}}") + len("{{question}}")
        end = template.index("{{answer_instruction}}")
        separators[condition.id] = template[start:end]

    distinct = set(separators.values())
    assert len(distinct) == 1, (
        "conditions disagree on the whitespace between {{question}} and "
        "{{answer_instruction}}: {0!r}. Every condition must present the "
        "question and the answer instruction identically.".format(separators)
    )
    assert distinct == {"\n\n"}, (
        "expected exactly one blank line between {{question}} and "
        "{{answer_instruction}}, got {0!r}".format(distinct)
    )

    # And no condition may sneak in leading or trailing whitespace.
    for condition in spec.conditions:
        assert condition.user_template == condition.user_template.strip(), (
            "condition {0!r} has leading or trailing whitespace in its "
            "user_template: {1!r}".format(condition.id, condition.user_template)
        )


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
