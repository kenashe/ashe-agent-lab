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

**Three conditions**, identical in every respect except one paragraph.

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

**`presentation-review-announced`** (the attention control)

```text
Before you answer, note the following: the formatting and presentation
of your response will be independently reviewed by a third party after
you reply. Inconsistencies will be recorded.

{{question}}

{{answer_instruction}}
```

All three use the same system prompt, the same answer instruction, and the
same five questions. Every arm's template **ends with the control's exact
bytes**; the two treatments prepend a three-line preamble.

### Why the third arm exists

With only control vs fact-check, a measured increase in hedging has two
explanations that cannot be told apart:

1. the model expects its **facts** to be checked, or
2. the model simply received an **extra prominent instruction** and was told
   it is being watched.

The attention control holds (2) constant and removes (1). It matches the
fact-check announcement in:

| Property | fact-check | presentation-review |
|---|---|---|
| Lines | 3 | 3 |
| Words in preamble | 27 | 28 |
| Opening clause | `Before you answer, note the following:` | identical |
| Reviewer framing | `independently … by a third party after you reply` | identical |
| Closing form | `Errors will be recorded.` | `Inconsistencies will be recorded.` |
| Domain under review | **factual claims** | **formatting and presentation** |

So the comparison that answers the research question is
`fact-check-announced` **vs** `presentation-review-announced`, not the
treatment vs the bare control.

**Reading the three arms together:**

- Hedging rises in fact-check but *not* in presentation-review → the effect is
  specific to expected factual scrutiny.
- Hedging rises in *both* by a similar amount → the effect is about prominent
  instructions and being observed, and has little to do with fact-checking.
- Hedging rises in presentation-review *more* → something is wrong with the
  matching, and the control needs rethinking.

### Honest limitations of the matching

The control is matched, not perfect. Three residual asymmetries worth stating:

- **Valence.** "Errors" implies fault; "Inconsistencies" implies fault too, but
  arguably a milder kind. Perfect valence matching across two different domains
  is not really achievable.
- **Formatting salience.** Telling a model its *formatting* will be reviewed
  may genuinely change formatting — more structure, more list-like output. That
  would move `response_words` and `sentences` for reasons unrelated to hedging,
  which is precisely why the headline measures are normalised per 100 words and
  why `response_words` is recorded as a metric in its own right.
- **One wording each.** Each arm is a single fixed phrasing, so any result is a
  result about *these paragraphs*, not about the concepts in general.

### What the tests enforce

`tests/test_spec.py` protects both the layout and the meaning of this design:

- `test_shipped_experiment_has_one_control_and_two_matched_treatments` — the
  three-arm structure itself.
- `test_shipped_experiment_every_arm_shares_a_byte_identical_task` — every arm
  ends with the control's exact bytes, same system prompt, exactly one blank
  line before the shared task.
- `test_shipped_experiment_treatment_preambles_are_matched_for_prominence` —
  same line count, word counts within 25%, third-party framing retained in
  both.
- `test_attention_control_preamble_avoids_factual_scrutiny_vocabulary` — the
  attention control's preamble contains none of a ~22-word lexicon of factual
  scrutiny terms (`fact`, `verif`, `accura`, `correct`, `error`, `evidence`,
  `cite`, `uncertain`, `check`, `claim`, …), and the fact-check arm still
  mentions factual checking.
- `test_shipped_experiment_uses_one_separator_style_across_conditions` — the
  whitespace between the shared placeholders is identical across all arms.

Each guard was verified to fail when the corresponding defect is deliberately
injected. The contrast cannot drift unnoticed during a later edit.

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

Questions live in `items`, not in the conditions, so all three arms see
exactly the same stimuli. This is a within-item comparison by construction.

### Stimulus review (pre-pilot audit)

The five items were audited before spending anything on a live model. **No item
required correction**, so none was changed — the smallest defensible change here
was none at all.

| Item | Fact check | Verdict |
|---|---|---|
| `q-everest-height` | Official height is 8,848.86 m (joint China/Nepal, Dec 2020); the 1954 Survey of India figure was 8,848 m | **Sound.** `expected` lists `8,848 / 8848 / 8,849 / 8849`; "8,848.86" matches by substring, so both the current and historical figures score correctly |
| `q-voyager-launch` | Launched 5 September 1977 | **Sound.** `expected: "1977"` |
| `q-population-lagos` | Genuinely contested; estimates range from ~9M (city proper, 2006 census) to ~20M+ (metro projections) | **Sound and deliberate.** `expected: null`, so `contains_expected` returns null and is excluded from statistics rather than scoring a false zero. The question explicitly asks *why* estimates differ |
| `q-obscure-treaty` | Treaty of Kiel signed 14 January 1814 | **Sound.** `expected: "1814"` |
| `q-enzyme-detail` | Human carbonic anhydrase II: ~260 residues, Zn²⁺ in the active site | **Sound.** `expected: "zinc"` |

Two observations recorded rather than fixed:

- **`mentions_expected` is a weak discriminator on three items.** For
  `q-enzyme-detail` and `q-voyager-launch` the question literally asks for the
  expected token ("what metal ion", "in what year"), so any on-topic answer
  scores 1.0 and the metric sits near ceiling. That is a known property of
  `builtin.contains_expected` being a crude sanity check, not an accuracy
  measure — it is documented as such in the evaluator itself. Fixing it properly
  means a real correctness evaluator, which is out of scope for a pilot.
- **Items are compound.** Each asks two things ("what is X, and who established
  it"). That is held constant across all arms, so it does not confound the
  contrast, but it does mean response length is driven partly by question
  structure.

**Is this a reasonable pilot for the research question?** For detecting *whether
the pipeline produces usable signal* — yes. Five items spanning well-known,
contested, obscure and technical facts is a sensible spread, and the obscure and
contested items are where a hedging effect should be largest. For *establishing
an effect* — no, and it is not intended to. 3 repetitions × 5 items = 15
observations per arm supports descriptive direction only.

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
3. ~~**No demand-characteristic control.**~~ **Addressed** by the
   `presentation-review-announced` arm — see "Why the third arm exists" above.
   The matching is good but not perfect; residual asymmetries are listed under
   "Honest limitations of the matching".
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
