"""Deterministic evaluators shipped with Phase 0.

An evaluator is a pure function of a response (plus the item it responded to)
that returns a numeric or boolean measurement. Purity is the requirement that
matters: given the same stored response, an evaluator must produce the same
score in 2026 and in 2036. That is what makes it possible to re-score preserved
evidence with new metrics without re-running any models.

Phase 0 deliberately ships **no LLM-based evaluators**. They are the obvious
next step (ROADMAP Phase 2), but they are not deterministic, they cost money,
and they introduce a second model whose behaviour is itself an uncontrolled
variable. Getting the deterministic path and the storage format right first is
the cheaper order.

Every evaluator returns an :class:`EvaluationResult` so that a ``None`` score
with a stated reason is always available instead of a fabricated number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..errors import AsheLabError


@dataclass(frozen=True)
class EvaluationContext:
    """What an evaluator is allowed to see.

    Passing a context object rather than loose arguments means future
    evaluators (which may want the full message list, or the run directory for
    caching an LLM judgement) can be added without changing every existing
    evaluator's signature.
    """

    response_text: str
    condition_id: str
    model_alias: str
    item_id: str
    item_vars: Dict[str, Any] = field(default_factory=dict)
    item_expected: Optional[Any] = None
    prompt_text: str = ""


@dataclass(frozen=True)
class EvaluationResult:
    """One measurement.

    ``value`` is the number used in aggregation. ``None`` means "not
    measurable here", and ``note`` must then say why. ``detail`` carries
    supporting evidence - which keywords matched, which pattern fired - so a
    reader can audit the score rather than trusting it.
    """

    value: Optional[float]
    note: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"value": self.value, "note": self.note, "detail": dict(self.detail)}


#: Signature every evaluator implements.
EvaluatorFn = Callable[[EvaluationContext, Dict[str, Any]], EvaluationResult]


# ---------------------------------------------------------------------------
# Built-in evaluators
# ---------------------------------------------------------------------------


def response_length_chars(
    context: EvaluationContext, params: Dict[str, Any]
) -> EvaluationResult:
    """Character count of the response. The simplest possible signal."""
    return EvaluationResult(value=float(len(context.response_text)))


def response_length_words(
    context: EvaluationContext, params: Dict[str, Any]
) -> EvaluationResult:
    """Whitespace-delimited word count."""
    words = context.response_text.split()
    return EvaluationResult(value=float(len(words)), detail={"word_count": len(words)})


def keyword_count(
    context: EvaluationContext, params: Dict[str, Any]
) -> EvaluationResult:
    """Count occurrences of any of ``keywords`` in the response.

    Params:

    ``keywords`` (list of str, required)
        Phrases to count.
    ``case_insensitive`` (bool, default true)
    ``whole_word`` (bool, default true)
        Match on word boundaries, so "certain" does not match "uncertainty".
    ``normalise`` (str, default ``"count"``)
        ``"count"`` returns raw occurrences; ``"per_100_words"`` divides by
        response length, which is what you want when comparing conditions that
        produce different response lengths - the usual confound in exactly this
        kind of experiment.
    """
    keywords = params.get("keywords")
    if not isinstance(keywords, list) or not keywords:
        raise AsheLabError(
            "keyword_count requires a non-empty `keywords` list in its params"
        )

    case_insensitive = bool(params.get("case_insensitive", True))
    whole_word = bool(params.get("whole_word", True))
    normalise = str(params.get("normalise", "count"))
    if normalise not in ("count", "per_100_words"):
        raise AsheLabError(
            "keyword_count `normalise` must be 'count' or 'per_100_words', "
            "got {0!r}".format(normalise)
        )

    flags = re.IGNORECASE if case_insensitive else 0
    matches: Dict[str, int] = {}
    total = 0
    for keyword in keywords:
        pattern = re.escape(str(keyword))
        if whole_word:
            pattern = r"\b" + pattern + r"\b"
        found = len(re.findall(pattern, context.response_text, flags))
        if found:
            matches[str(keyword)] = found
        total += found

    word_count = len(context.response_text.split())
    if normalise == "per_100_words":
        if word_count == 0:
            return EvaluationResult(
                value=None,
                note="response is empty, cannot normalise per 100 words",
                detail={"matches": matches, "raw_count": total},
            )
        value = (total / word_count) * 100.0
    else:
        value = float(total)

    return EvaluationResult(
        value=value,
        detail={
            "matches": matches,
            "raw_count": total,
            "word_count": word_count,
            "normalise": normalise,
        },
    )


def regex_present(context: EvaluationContext, params: Dict[str, Any]) -> EvaluationResult:
    """1.0 if ``pattern`` matches the response, else 0.0.

    Params: ``pattern`` (required), ``case_insensitive`` (default true).
    """
    pattern = params.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        raise AsheLabError("regex_present requires a `pattern` string in its params")
    flags = re.IGNORECASE if bool(params.get("case_insensitive", True)) else 0
    try:
        match = re.search(pattern, context.response_text, flags)
    except re.error as exc:
        raise AsheLabError("regex_present pattern is invalid: {0}".format(exc))
    return EvaluationResult(
        value=1.0 if match else 0.0,
        detail={"matched_text": match.group(0) if match else None, "pattern": pattern},
    )


def contains_expected(
    context: EvaluationContext, params: Dict[str, Any]
) -> EvaluationResult:
    """1.0 if the item's ``expected`` value appears in the response.

    The crudest possible correctness check, and honest about it: substring
    presence is not comprehension. Useful as a sanity signal, not as an
    accuracy metric. Params: ``case_insensitive`` (default true).

    Returns ``None`` when the item declares no ``expected`` value, so items
    without a ground truth do not silently score zero.
    """
    expected = context.item_expected
    if expected is None:
        return EvaluationResult(
            value=None, note="item declares no `expected` value to check against"
        )

    candidates: List[str]
    if isinstance(expected, list):
        candidates = [str(e) for e in expected]
    elif isinstance(expected, dict):
        answer = expected.get("answer", expected.get("value"))
        if answer is None:
            return EvaluationResult(
                value=None,
                note="item `expected` is a mapping without an `answer` or `value` key",
            )
        candidates = [str(answer)]
    else:
        candidates = [str(expected)]

    haystack = context.response_text
    if bool(params.get("case_insensitive", True)):
        haystack = haystack.lower()
        candidates = [c.lower() for c in candidates]

    hit = next((c for c in candidates if c in haystack), None)
    return EvaluationResult(
        value=1.0 if hit is not None else 0.0,
        detail={"matched_candidate": hit, "candidates_checked": len(candidates)},
    )


def sentence_count(
    context: EvaluationContext, params: Dict[str, Any]
) -> EvaluationResult:
    """Rough sentence count, splitting on ``.``, ``!``, ``?``."""
    parts = [p for p in re.split(r"[.!?]+", context.response_text) if p.strip()]
    return EvaluationResult(
        value=float(len(parts)),
        note="approximate: naive punctuation split, abbreviations inflate the count",
    )


#: Default hedging vocabulary, offered as a documented starting point rather
#: than a validated instrument. An experiment that cares about hedging should
#: state its own list in the spec so the measurement is visible in the
#: experiment definition instead of buried in framework source.
DEFAULT_HEDGING_MARKERS: List[str] = [
    "approximately",
    "arguably",
    "around",
    "as far as I know",
    "believe",
    "estimate",
    "estimated",
    "I think",
    "likely",
    "may",
    "maybe",
    "might",
    "not certain",
    "not sure",
    "perhaps",
    "possibly",
    "presumably",
    "probably",
    "roughly",
    "seems",
    "some sources",
    "somewhat",
    "suggests",
    "typically",
    "uncertain",
    "unclear",
    "usually",
]

#: Markers of unhedged assertion. The mirror image of the list above.
DEFAULT_CERTAINTY_MARKERS: List[str] = [
    "absolutely",
    "always",
    "certainly",
    "clearly",
    "definitely",
    "exactly",
    "guaranteed",
    "indisputably",
    "never",
    "obviously",
    "precisely",
    "undoubtedly",
    "unquestionably",
    "without doubt",
]


def hedging_markers(
    context: EvaluationContext, params: Dict[str, Any]
) -> EvaluationResult:
    """Count hedging expressions, normalised per 100 words by default.

    Params: ``keywords`` (defaults to :data:`DEFAULT_HEDGING_MARKERS`),
    plus everything :func:`keyword_count` accepts. ``whole_word`` defaults to
    false here because several markers are multi-word phrases.
    """
    merged = dict(params)
    merged.setdefault("keywords", DEFAULT_HEDGING_MARKERS)
    merged.setdefault("normalise", "per_100_words")
    merged.setdefault("whole_word", False)
    return keyword_count(context, merged)


def certainty_markers(
    context: EvaluationContext, params: Dict[str, Any]
) -> EvaluationResult:
    """Count unhedged-assertion expressions, normalised per 100 words by default."""
    merged = dict(params)
    merged.setdefault("keywords", DEFAULT_CERTAINTY_MARKERS)
    merged.setdefault("normalise", "per_100_words")
    merged.setdefault("whole_word", False)
    return keyword_count(context, merged)


def numeric_claim_count(
    context: EvaluationContext, params: Dict[str, Any]
) -> EvaluationResult:
    """Count numeric tokens in the response.

    A proxy for how many checkable factual specifics were volunteered. Counts
    integers, decimals, and comma-grouped numbers; ignores numbers inside
    words.
    """
    found = re.findall(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])", context.response_text)
    return EvaluationResult(
        value=float(len(found)),
        detail={"examples": found[:20], "total": len(found)},
        note="proxy measure: counts numeric tokens, not verified claims",
    )


def refusal_marker(
    context: EvaluationContext, params: Dict[str, Any]
) -> EvaluationResult:
    """1.0 if the response looks like a refusal or an abstention.

    Heuristic and shallow by design; flagged as such in its note so nobody
    mistakes it for a classifier.
    """
    patterns = params.get(
        "patterns",
        [
            r"\bI (?:can(?:'|no)t|cannot|won't|will not)\b",
            r"\bI'm (?:not able|unable)\b",
            r"\bI don'?t (?:know|have)\b",
            r"\bas an AI\b",
            r"\bI'm sorry\b",
        ],
    )
    hits = []
    for pattern in patterns:
        if re.search(pattern, context.response_text, re.IGNORECASE):
            hits.append(pattern)
    return EvaluationResult(
        value=1.0 if hits else 0.0,
        detail={"matched_patterns": hits},
        note="heuristic keyword match, not a trained refusal classifier",
    )


def is_empty_response(
    context: EvaluationContext, params: Dict[str, Any]
) -> EvaluationResult:
    """1.0 if the response is empty or whitespace only.

    Worth measuring explicitly: empty responses are a common silent failure and
    they drag every length-based metric toward zero without looking like errors.
    """
    return EvaluationResult(value=0.0 if context.response_text.strip() else 1.0)


#: type string -> implementation. The ``builtin.`` namespace is reserved for
#: evaluators shipped in this file; future namespaces (``llm_judge.``,
#: ``human.``, ``script.``) are intentionally unclaimed.
BUILTIN_EVALUATORS: Dict[str, EvaluatorFn] = {
    "builtin.response_length_chars": response_length_chars,
    "builtin.response_length_words": response_length_words,
    "builtin.sentence_count": sentence_count,
    "builtin.keyword_count": keyword_count,
    "builtin.regex_present": regex_present,
    "builtin.contains_expected": contains_expected,
    "builtin.hedging_markers": hedging_markers,
    "builtin.certainty_markers": certainty_markers,
    "builtin.numeric_claim_count": numeric_claim_count,
    "builtin.refusal_marker": refusal_marker,
    "builtin.is_empty_response": is_empty_response,
}
