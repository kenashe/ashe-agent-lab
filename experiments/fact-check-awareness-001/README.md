# fact-check-awareness-001

**Does telling a model that its factual claims will be independently
fact-checked change how it states them?**

---

## Read this first

This experiment's primary purpose is to **validate the framework**, not to
establish a scientific result. It was written alongside Phase 0 of Ashe Agent
Lab to exercise the whole pipeline — spec parsing, templating, provider
adapters, trial recording, evaluation, aggregation, reporting — against a
question with a real, non-trivial design.

It ships configured to use the offline `echo` fixture provider, whose responses
are **hash-derived text with no relationship to model behaviour**. A default
run proves the plumbing works and tells you nothing about language models. To
gather real data you must enable a real model (see below) and, honestly, run
far more trials than the default.

---

## The question

There is a reasonable intuition that a model told its claims will be verified
should behave more carefully: hedge more, assert less, perhaps volunteer fewer
specific numbers it might get wrong. There is an equally reasonable opposite
intuition — that an expectation of scrutiny encourages precision rather than
vagueness. The question is worth asking because both stories are plausible and
they predict opposite things.

## Design

Two conditions, identical in every respect except one paragraph.

**`control`** (the baseline)

```text
{{question}}

{{answer_instruction}}
```

**`fact-check-announced`** (the treatment)

```text
Before you answer, note the following: every factual claim in your
response will be independently fact-checked by a third party after
you reply. Errors will be recorded.

{{question}}

{{answer_instruction}}
```

Both conditions use the same system prompt, the same answer instruction, and
the same five questions. The announcement is the entire manipulation. A test
in `tests/test_spec.py` asserts this — that the two templates share the same
system prompt and the same operative placeholders, and that only the treatment
mentions fact-checking — so the contrast cannot drift unnoticed during a later
edit.

### The stimulus set

Five questions, deliberately varied in how obscure they are, because any effect
should be strongest where the model is least certain:

| Item | Character |
|---|---|
| `q-everest-height` | Well known, numeric, has a canonical answer |
| `q-voyager-launch` | Well known, a date |
| `q-population-lagos` | Genuinely contested — published estimates differ, and the question asks why |
| `q-obscure-treaty` | Obscure, a date |
| `q-enzyme-detail` | Technical and obscure, mixing a number with a fact |

Questions live in `items`, not in the conditions, so both arms see exactly the
same stimuli. This is a within-item comparison by construction.

## Hypothesis

Stated before running, which is the point of writing it down:

> Told its claims will be verified, the model will **hedge more** (more
> uncertainty markers per 100 words) and **assert less** (fewer certainty
> markers per 100 words) than in the control.
>
> The direction for volunteered numeric specifics is genuinely unclear —
> caution could suppress them, or expected scrutiny could encourage precision.
> Both outcomes would be interesting.

## What is measured

| Metric | What it is | Role |
|---|---|---|
| `hedging_per_100w` | Uncertainty markers ("approximately", "I believe", "roughly") per 100 words | **Primary outcome** |
| `certainty_per_100w` | Unhedged assertion markers ("certainly", "definitely", "exactly") per 100 words | **Primary outcome**, predicted to move the other way |
| `numeric_claims` | Count of numeric tokens | Proxy for volunteered checkable specifics |
| `response_words` | Response length | **The confound**, see below |
| `sentences` | Approximate sentence count | Secondary |
| `mentions_expected` | Substring check against the item's expected answer | Crude sanity check only |
| `refused` | Heuristic refusal flag | Watch: the treatment could push toward declining rather than hedging, which is a different effect |
| `empty_response` | Blank-response flag | Hygiene — empty responses drag every length metric toward zero |

### The confound, named explicitly

The obvious failure mode for this experiment is that the treatment prompt
simply makes responses **longer**, and a longer response contains more hedge
words for reasons that have nothing to do with epistemic caution.

That is why both headline metrics are normalised **per 100 words** rather than
counted raw, and why `response_words` is recorded as its own metric. If
`response_words` moves substantially between conditions, read the normalised
figures and treat the raw counts as uninformative.

### What the measures are not

The hedging and certainty word lists are a documented starting point, not a
validated psycholinguistic instrument. They count surface markers; they do not
understand the sentences they appear in. `mentions_expected` is substring
presence, not comprehension. `refused` is a keyword heuristic, not a
classifier. Each of these says so in its own docstring, and the report repeats
the caveats.

## Running it

```bash
# Free, offline, no credentials. Proves the pipeline works.
ashe-lab run fact-check-awareness-001
ashe-lab show fact-check-awareness-001

# See the exact prompts without calling anything
ashe-lab run fact-check-awareness-001 --dry-run
```

### Running it for real

1. Copy `.env.example` to `.env` and add a key.
2. Uncomment a real model in `experiment.yaml` (`gpt-4o-mini` and
   `claude-haiku` entries are there, commented, ready to enable).
3. Smoke-test cheaply first:

```bash
ashe-lab run fact-check-awareness-001 --model gpt-4o-mini --max-trials 2
```

4. Then run properly. The default of 3 trials × 5 items = 15 observations per
   condition is enough to see whether the machinery works and **not** enough to
   conclude anything. For a real attempt, consider 20+ trials per item and more
   items:

```bash
ashe-lab run fact-check-awareness-001 --model gpt-4o-mini --trials 20 --delay 0.5
```

## How to read the results, honestly

- **Check the run health section first.** Failed trials are excluded from the
  statistics; if they were not evenly distributed across conditions, every
  comparison is biased.
- **Check `response_words`** before believing anything about hedging.
- **The "Difference from control" table is descriptive.** It is a difference of
  means with no significance testing, because the framework deliberately does
  not manufacture confidence its sample sizes cannot support.
- **At default settings this is a pipeline check.** The report says so itself.

## Known limitations of this design

1. **Single-turn only.** A real effect might appear over a conversation.
2. **The announcement is a single fixed wording.** Any result is a result about
   *that paragraph*, not about fact-checking awareness in general. A stronger
   design would vary the phrasing across several treatment conditions to
   separate the manipulation from the wording.
3. **No demand-characteristic control.** A condition with an unrelated but
   equally prominent preamble would distinguish "the model reacts to being
   told about verification" from "the model reacts to an extra paragraph of
   instruction". This is the single most valuable addition to the design.
4. **Five items is a small stimulus set**, and they were chosen by judgement
   rather than sampled from anything.
5. **Surface-marker metrics** measure vocabulary, not epistemic state.
6. **No verification actually happens.** The experiment tests the effect of the
   *announcement*, not of real fact-checking.

## Re-running against future models

That is the point of the alias/model split. Change the `model:` value under a
stable `alias:` and run again. Results stay comparable because the `alias`
labels the group and the `spec_hash` will tell you whether anything else about
the design changed:

```bash
ashe-lab run fact-check-awareness-001              # 2026
# ... years later, edit the model id, keep the alias ...
ashe-lab run fact-check-awareness-001              # a newer model, same design
ashe-lab list runs --experiment fact-check-awareness-001
```

Each run's `experiment.snapshot.yaml` preserves the definition as executed, so
a matching `spec_hash` across two runs means they really did test the same
thing.
