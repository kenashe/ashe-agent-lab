"""Tests for the deterministic evaluators.

Two properties matter most and are asserted repeatedly below:

*   **Purity.** The same text must always score the same, because stored
    evidence gets re-scored years later.
*   **Honest nulls.** An unmeasurable case returns ``None`` with a note, never
    a fabricated zero. A zero and an unknown are different facts, and
    conflating them corrupts every aggregate that follows.
"""

from __future__ import annotations

import pytest

from ashe_lab.errors import RegistryError
from ashe_lab.evaluators import (
    EvaluationContext,
    available_evaluators,
    get_evaluator,
    register_evaluator,
    run_evaluators,
    validate_evaluator_types,
)
from ashe_lab.evaluators.builtin import (
    certainty_markers,
    contains_expected,
    hedging_markers,
    is_empty_response,
    keyword_count,
    numeric_claim_count,
    refusal_marker,
    regex_present,
    response_length_chars,
    response_length_words,
    sentence_count,
)
from ashe_lab.spec import EvaluatorSpec


def context(text, **kwargs):
    defaults = dict(
        response_text=text,
        condition_id="control",
        model_alias="fixture",
        item_id="item-one",
    )
    defaults.update(kwargs)
    return EvaluationContext(**defaults)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_builtin_evaluators_are_registered():
    names = available_evaluators()
    assert "builtin.hedging_markers" in names
    assert "builtin.response_length_words" in names


def test_unknown_evaluator_type_error_lists_valid_types():
    with pytest.raises(RegistryError) as excinfo:
        get_evaluator("builtin.vibes")
    assert "builtin.vibes" in str(excinfo.value)
    assert "builtin.keyword_count" in str(excinfo.value)


def test_register_evaluator_adds_a_metric():
    register_evaluator(
        "unit-test.constant",
        lambda ctx, params: __import__(
            "ashe_lab.evaluators.builtin", fromlist=["EvaluationResult"]
        ).EvaluationResult(value=7.0),
    )
    assert "unit-test.constant" in available_evaluators()


def test_validate_evaluator_types_catches_a_typo():
    problems = validate_evaluator_types(
        [EvaluatorSpec(id="good", type="builtin.response_length_words"),
         EvaluatorSpec(id="bad", type="builtin.nonexistent")]
    )
    assert len(problems) == 1
    assert "bad" in problems[0]


# ---------------------------------------------------------------------------
# Length metrics
# ---------------------------------------------------------------------------


def test_response_length_chars():
    assert response_length_chars(context("abcde"), {}).value == 5.0


def test_response_length_words():
    assert response_length_words(context("one two three"), {}).value == 3.0


def test_length_metrics_are_zero_for_empty_text_not_null():
    """Here zero IS the measurement: the response really is zero words long."""
    assert response_length_words(context(""), {}).value == 0.0
    assert response_length_chars(context(""), {}).value == 0.0


def test_sentence_count_is_labelled_approximate():
    result = sentence_count(context("One. Two! Three?"), {})
    assert result.value == 3.0
    assert "approximate" in result.note


# ---------------------------------------------------------------------------
# Keyword counting
# ---------------------------------------------------------------------------


def test_keyword_count_counts_occurrences():
    result = keyword_count(context("maybe yes, maybe no"), {"keywords": ["maybe"]})
    assert result.value == 2.0
    assert result.detail["matches"] == {"maybe": 2}


def test_keyword_count_requires_keywords():
    from ashe_lab.errors import AsheLabError

    with pytest.raises(AsheLabError):
        keyword_count(context("text"), {})


def test_keyword_count_whole_word_prevents_substring_false_positives():
    """"certain" must not match inside "uncertainty"."""
    text = "There is uncertainty here."
    strict = keyword_count(context(text), {"keywords": ["certain"], "whole_word": True})
    loose = keyword_count(context(text), {"keywords": ["certain"], "whole_word": False})
    assert strict.value == 0.0
    assert loose.value == 1.0


def test_keyword_count_case_insensitive_by_default():
    assert keyword_count(context("Maybe"), {"keywords": ["maybe"]}).value == 1.0
    assert (
        keyword_count(
            context("Maybe"), {"keywords": ["maybe"], "case_insensitive": False}
        ).value
        == 0.0
    )


def test_keyword_count_normalisation_controls_for_verbosity():
    """The confound this framework's first experiment is most exposed to.

    Two responses with the same hedge count but different lengths must not be
    scored as equally hedgy.
    """
    short = context("maybe " + "word " * 4)  # 1 hedge in 5 words
    long = context("maybe " + "word " * 99)  # 1 hedge in 100 words
    params = {"keywords": ["maybe"], "normalise": "per_100_words"}
    assert keyword_count(short, params).value == pytest.approx(20.0)
    assert keyword_count(long, params).value == pytest.approx(1.0)


def test_normalised_count_is_null_for_an_empty_response():
    result = keyword_count(
        context(""), {"keywords": ["maybe"], "normalise": "per_100_words"}
    )
    assert result.value is None
    assert "empty" in result.note


def test_keyword_count_rejects_an_unknown_normalisation():
    from ashe_lab.errors import AsheLabError

    with pytest.raises(AsheLabError):
        keyword_count(context("x"), {"keywords": ["x"], "normalise": "per_furlong"})


# ---------------------------------------------------------------------------
# Hedging and certainty
# ---------------------------------------------------------------------------


def test_hedging_markers_detect_hedges_and_ignore_confident_prose():
    hedged = "It is approximately 8,848 metres, though I believe estimates vary."
    confident = "It is exactly 8,848 metres. This is definitely correct."
    assert hedging_markers(context(hedged), {}).value > 0
    assert hedging_markers(context(confident), {}).value == 0


def test_certainty_markers_are_the_mirror_of_hedging():
    confident = "This is definitely and absolutely correct, without doubt."
    assert certainty_markers(context(confident), {}).value > 0
    assert hedging_markers(context(confident), {}).value == 0


def test_hedging_markers_default_to_normalised_output():
    result = hedging_markers(context("maybe " + "word " * 99), {})
    assert result.detail["normalise"] == "per_100_words"


def test_hedging_markers_match_multiword_phrases():
    result = hedging_markers(context("As far as I know, it is fine."), {})
    assert result.detail["raw_count"] >= 1


def test_marker_lists_can_be_overridden_by_the_spec():
    """An experiment should be able to state its own instrument, visibly."""
    result = hedging_markers(context("wibble wobble"), {"keywords": ["wibble"]})
    assert result.detail["raw_count"] == 1


# ---------------------------------------------------------------------------
# Expected-answer checking
# ---------------------------------------------------------------------------


def test_contains_expected_finds_a_string_answer():
    result = contains_expected(
        context("Voyager 1 launched in 1977.", item_expected="1977"), {}
    )
    assert result.value == 1.0


def test_contains_expected_accepts_a_list_of_acceptable_answers():
    result = contains_expected(
        context("It is 8,848 m.", item_expected=["8,848", "8848"]), {}
    )
    assert result.value == 1.0
    assert result.detail["matched_candidate"] == "8,848"


def test_contains_expected_returns_zero_when_absent():
    result = contains_expected(context("No idea.", item_expected="1977"), {})
    assert result.value == 0.0


def test_contains_expected_is_null_when_the_item_has_no_ground_truth():
    """Items without a truth value must not score zero and drag the mean down."""
    result = contains_expected(context("anything", item_expected=None), {})
    assert result.value is None
    assert "no `expected`" in result.note


def test_contains_expected_reads_a_mapping_answer():
    result = contains_expected(
        context("The answer is zinc.", item_expected={"answer": "zinc"}), {}
    )
    assert result.value == 1.0


# ---------------------------------------------------------------------------
# Other metrics
# ---------------------------------------------------------------------------


def test_regex_present():
    assert regex_present(context("value: 42"), {"pattern": r"\d+"}).value == 1.0
    assert regex_present(context("no digits"), {"pattern": r"\d+"}).value == 0.0


def test_regex_present_rejects_an_invalid_pattern():
    from ashe_lab.errors import AsheLabError

    with pytest.raises(AsheLabError):
        regex_present(context("x"), {"pattern": "([unclosed"})


def test_numeric_claim_count_counts_numbers_not_digits_in_words():
    result = numeric_claim_count(context("In 1977 it reached 8,848 m and 3.5 km/s"), {})
    assert result.value == 3.0


def test_numeric_claim_count_is_zero_for_prose():
    assert numeric_claim_count(context("No figures at all here."), {}).value == 0.0


def test_refusal_marker_flags_a_refusal():
    assert refusal_marker(context("I cannot answer that."), {}).value == 1.0
    assert refusal_marker(context("The answer is four."), {}).value == 0.0


def test_refusal_marker_admits_it_is_a_heuristic():
    assert "heuristic" in refusal_marker(context("anything"), {}).note


def test_is_empty_response():
    assert is_empty_response(context("   \n  "), {}).value == 1.0
    assert is_empty_response(context("words"), {}).value == 0.0


# ---------------------------------------------------------------------------
# Determinism and failure isolation
# ---------------------------------------------------------------------------


def test_every_builtin_evaluator_is_deterministic():
    """Re-scoring preserved evidence must reproduce the original numbers."""
    text = (
        "It is approximately 8,848 metres, established in 2020. "
        "I believe the figure is definitely disputed by some sources."
    )
    specs = [
        EvaluatorSpec(id=name.split(".")[-1], type=name)
        for name in available_evaluators()
        if name.startswith("builtin.") and name != "builtin.keyword_count"
    ]
    first = run_evaluators(specs, context(text, item_expected="8,848"))
    second = run_evaluators(specs, context(text, item_expected="8,848"))
    assert first == second


def test_a_failing_evaluator_does_not_destroy_the_trial():
    """Expensive evidence must survive a buggy metric."""
    specs = [
        EvaluatorSpec(id="broken", type="builtin.keyword_count", params={}),  # no keywords
        EvaluatorSpec(id="fine", type="builtin.response_length_words"),
    ]
    scores = run_evaluators(specs, context("some words"))

    assert scores["broken"]["value"] is None
    assert scores["broken"]["detail"]["failed"] is True
    assert "keywords" in scores["broken"]["note"]
    # The healthy evaluator still ran.
    assert scores["fine"]["value"] == 2.0


def test_an_unknown_evaluator_type_is_recorded_not_raised():
    scores = run_evaluators([EvaluatorSpec(id="ghost", type="builtin.nope")], context("x"))
    assert scores["ghost"]["value"] is None
    assert scores["ghost"]["detail"]["failed"] is True


def test_run_evaluators_records_the_evaluator_type_for_auditability():
    scores = run_evaluators(
        [EvaluatorSpec(id="words", type="builtin.response_length_words")], context("a b")
    )
    assert scores["words"]["evaluator_type"] == "builtin.response_length_words"
