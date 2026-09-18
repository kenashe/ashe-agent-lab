# Experiment specification reference

Every key an `experiment.yaml` may contain, as of `schema_version: 0`,
`kind: single_agent.v0`.

Unknown keys are **rejected**, not ignored. A tolerated typo means an
experiment that did not test what its author believed. Validate free of charge
with `ashe-lab validate <experiment>`.

The authoritative implementation is `src/ashe_lab/spec.py`. If this document
and that file disagree, the file is right and this document is a bug.

---

## Top level

```yaml
schema_version: 0                    # optional, default 0
kind: single_agent.v0                # optional, default single_agent.v0
id: my-experiment-001                # REQUIRED
title: A human-readable title        # REQUIRED
research_question: >-                # optional but strongly recommended
  What you are actually asking.
hypothesis: >-                       # optional
  What you expect, and why.
notes: >-                            # optional
  Caveats, design reasoning, anything a future reader needs.
tags: [epistemics, phase-0]          # optional
variables: {}                        # optional, experiment-wide template vars
defaults: {...}                      # optional
models: [...]                        # REQUIRED, >= 1
items: [...]                         # REQUIRED, >= 1
conditions: [...]                    # REQUIRED, >= 1
evaluation: {...}                    # optional
analysis: {...}                      # optional
```

| Key | Type | Notes |
|---|---|---|
| `schema_version` | int | Bumped only for breaking format changes. A spec declaring a version newer than the installed framework is rejected. |
| `kind` | string | Discriminator for the experiment shape. Only `single_agent.v0` exists. Future shapes get new values so old specs never change meaning. |
| `id` | slug | Becomes a directory name. Must match `[a-z0-9][a-z0-9._-]*[a-z0-9]`, 1–64 chars. Convention: `kebab-case` with a trailing `-001`. |
| `title` | string | Shown as the report heading. |
| `research_question` | string | Rendered in the report. Write it as a question. |
| `hypothesis` | string | Rendered in the report. Stating it before the run is what stops you from rationalising afterwards. |
| `variables` | mapping | Template variables available to all conditions. Lowest precedence. |

### Identifier rules

`id`, condition ids, item ids, model aliases, and evaluator ids are all slugs:
lowercase, 1–64 characters, `a-z0-9._-`, no leading or trailing punctuation.
They become directory names, CSV column names, and URL fragments, so the
restriction is deliberate.

---

## `defaults`

Applied to every trial unless a model entry overrides the same key.

```yaml
defaults:
  trials: 3                  # repetitions per (condition, model, item) cell
  temperature: 1.0
  top_p: 0.95
  max_output_tokens: 500
  seed: 12345
  retries:
    max_attempts: 3
    initial_backoff_seconds: 1.0
    backoff_multiplier: 2.0
    max_backoff_seconds: 30.0
```

| Key | Type | Default | Notes |
|---|---|---|---|
| `trials` | int ≥ 1 | `1` | Repetitions per cell. Total trials = conditions × models × items × trials. |
| `temperature` | number ≥ 0 | unset | Omitted from the request when unset, so the provider's default applies. |
| `top_p` | number ≥ 0 | unset | As above. |
| `max_output_tokens` | int ≥ 1 | unset | Maps to `max_tokens` (openai_chat) or the required `max_tokens` (anthropic_messages). |
| `seed` | int | unset | Passed through where supported. **Does not make hosted models deterministic** — see ARCHITECTURE §3. Adapters record when they cannot honour it. |
| `retries.max_attempts` | int ≥ 1 | `3` | Total attempts, not retries-after-the-first. `1` means no retry. |
| `retries.initial_backoff_seconds` | number ≥ 0 | `1.0` | |
| `retries.backoff_multiplier` | number ≥ 1 | `2.0` | Exponential. |
| `retries.max_backoff_seconds` | number ≥ 0 | `30.0` | Ceiling per wait. |

Only *retryable* errors are retried. A missing credential or a malformed
request is not retried — see `ProviderError.retryable`.

---

## `models`

One entry per model under test. At least one required.

```yaml
models:
  - alias: echo-fixture           # REQUIRED, slug, unique
    provider: echo                # REQUIRED, a registered provider name
    model: echo-deterministic-v1  # REQUIRED, the vendor's identifier
    temperature: 0.7              # optional, overrides defaults
    top_p: 0.9                    # optional
    max_output_tokens: 400        # optional
    seed: 7                       # optional
    notes: Why this model is here # optional
    options:                      # optional, passed to the adapter verbatim
      sentences: 4
```

**`alias` vs `model`.** The alias is your stable local name; it appears in
results, reports, and CSV columns. `model` is the vendor's identifier and is
expected to change. Keeping them separate is what makes "re-run this 2026
experiment against a 2029 model" a one-line edit rather than a reanalysis —
the alias stays, the results stay comparable.

**`options`** is an escape hatch, merged into the provider's request payload.
Anything you put there is by definition not portable across providers. Keys the
framework sets (`model`, `messages`) always win, so options cannot silently
hijack a request.

List available providers with `ashe-lab list providers`. Current:
`echo`, `failing`, `openai_chat`, `anthropic_messages`.

---

## `items`

The stimuli. Presented identically in every condition, which is what makes the
comparison within-item.

```yaml
items:
  - id: q-everest-height          # REQUIRED, slug, unique
    vars:                         # optional, template variables
      question: What is the height of Mount Everest?
    expected: ["8,848", "8848"]   # optional, rough ground truth
    tags: [well-known, numeric]   # optional
```

| Key | Type | Notes |
|---|---|---|
| `id` | slug | Appears in `trial_key` and CSV rows. |
| `vars` | mapping | Keys must be valid Python identifiers. Highest template precedence. |
| `expected` | string, list, mapping, or null | Used by `builtin.contains_expected`. A list means "any of these counts". A mapping may use an `answer` or `value` key. `null` means no ground truth, and that evaluator then returns `null` rather than `0` — so items without answers do not drag accuracy down. |
| `tags` | list of strings | For your own bookkeeping; not used in grouping yet. |

---

## `conditions`

The manipulation. This is the part that determines whether your experiment
means anything.

```yaml
conditions:
  - id: control                   # REQUIRED, slug, unique
    is_control: true              # optional, at most ONE condition may set it
    description: >-               # optional, rendered in the report
      Baseline: the question with no mention of verification.
    system_prompt: >-             # optional, templated
      You are a knowledgeable assistant.
    user_template: |-             # REQUIRED, templated
      {{question}}

      {{answer_instruction}}
    variables: {}                 # optional, condition-scoped template vars
    tags: [baseline]              # optional
```

| Key | Type | Notes |
|---|---|---|
| `id` | slug | Appears as `run_condition` in `trials.csv`. |
| `is_control` | bool | Marks the baseline. Enables the "Difference from control" section. More than one is rejected. |
| `description` | string | Reproduced in the report. Explain the manipulation. |
| `system_prompt` | string | Sent as a `system` message. `anthropic_messages` hoists it to the API's top-level `system` field. |
| `user_template` | string | Sent as the `user` message. |
| `variables` | mapping | Middle template precedence. |

### Designing the contrast

Keep condition templates **byte-identical except for the manipulation**. If the
answer instruction, the question phrasing, or the formatting differs between
conditions, your experiment is confounded at the source and no amount of
analysis fixes it.

Also: **name the obvious confound and measure it.** If your manipulation might
change response length, record length as a metric and normalise your headline
measure per 100 words. A hedging effect that is really a verbosity effect is the
standard way this class of experiment misleads people.

---

## Template syntax

Placeholders use **double braces**: `{{name}}`. Whitespace inside is allowed
(`{{ name }}`).

Single braces pass through untouched, which matters because prompts routinely
contain JSON, code, and format strings:

```yaml
user_template: 'Reply as {"answer": ...} to {{question}}'
```

**A missing placeholder is a hard error at load time**, not a blank
substitution. A prompt with a silently empty slot is corrupted evidence, and
catching it during `validate` costs nothing while catching it mid-run costs
money.

### Variable precedence

Later wins:

1. `variables` at the top level (experiment-wide)
2. `variables` on the condition
3. `vars` on the item

Two variables are always injected and can be overridden: `condition_id` and
`item_id`.

Every combination of condition × item is template-checked at load time, so a
placeholder that works for one item and not another is caught before a run.

---

## `evaluation`

Deterministic measurements applied to every successful response.

```yaml
evaluation:
  evaluators:
    - id: hedging_per_100w        # REQUIRED, slug, unique
      type: builtin.hedging_markers   # REQUIRED, a registered evaluator type
      description: >-             # optional but recommended; shown in the report
        Uncertainty markers per 100 words. Primary outcome measure.
      params:                     # optional, evaluator-specific
        normalise: per_100_words
```

Each evaluator adds a `score_<id>` column to `trials.csv` and a per-group
statistics block to `results.json`.

An evaluator that raises does **not** fail the trial: the score is recorded as
`null` with the error in its note, and the run continues. Expensive evidence
must survive a buggy metric.

List available types with `ashe-lab list evaluators`.

### Built-in evaluators

| Type | Returns | Params |
|---|---|---|
| `builtin.response_length_chars` | Character count | — |
| `builtin.response_length_words` | Whitespace-delimited word count | — |
| `builtin.sentence_count` | Approximate sentence count (punctuation split; abbreviations inflate it) | — |
| `builtin.keyword_count` | Occurrences of any `keywords` | `keywords` (**required**), `case_insensitive` (true), `whole_word` (true), `normalise` (`count` \| `per_100_words`) |
| `builtin.hedging_markers` | Uncertainty markers, per 100 words by default | as `keyword_count`; `keywords` defaults to a built-in list, `whole_word` defaults false (phrases) |
| `builtin.certainty_markers` | Unhedged-assertion markers, per 100 words by default | as above |
| `builtin.numeric_claim_count` | Count of numeric tokens, as a proxy for volunteered checkable specifics | — |
| `builtin.regex_present` | `1.0` / `0.0` | `pattern` (**required**), `case_insensitive` (true) |
| `builtin.contains_expected` | `1.0` / `0.0`, or `null` when the item has no `expected` | `case_insensitive` (true) |
| `builtin.refusal_marker` | `1.0` / `0.0`. Heuristic, not a classifier | `patterns` (list of regexes) |
| `builtin.is_empty_response` | `1.0` if blank | — |

The default hedging and certainty word lists are a documented starting point,
not a validated instrument. An experiment that cares about hedging should state
its own `keywords` in the spec, so the measurement is visible in the experiment
definition rather than buried in framework source.

The `llm_judge.*`, `human.*`, and `script.*` namespaces are reserved and
deliberately unimplemented.

---

## `analysis`

```yaml
analysis:
  group_by: [condition, model]    # optional; any of condition, model, item
  report_template: default        # optional; only "default" exists
```

Grouping produces one row per combination in `results.json` and in the report's
summary table. When `condition` is in `group_by` and a control condition is
declared, a difference-from-control comparison is computed per stratum of the
remaining keys.

Phase 0 computes **descriptive statistics only** — n, mean, median, min, max,
sample standard deviation, and raw differences. No significance testing, on
purpose: see ARCHITECTURE §4.10.

---

## A complete minimal example

```yaml
id: minimal-example-001
title: The smallest useful experiment
research_question: Does asking politely change the answer?

defaults:
  trials: 3

models:
  - alias: fixture
    provider: echo
    model: echo-deterministic-v1

items:
  - id: q-capital
    vars:
      question: What is the capital of Australia?
    expected: Canberra

conditions:
  - id: plain
    is_control: true
    user_template: "{{question}}"
  - id: polite
    user_template: "Could you please tell me: {{question}} Thank you."

evaluation:
  evaluators:
    - id: words
      type: builtin.response_length_words
      description: Response length in words.
    - id: correct
      type: builtin.contains_expected
      description: Substring check against the expected answer.
```

Then:

```bash
ashe-lab validate minimal-example-001
ashe-lab run minimal-example-001 --dry-run   # read the rendered prompts
ashe-lab run minimal-example-001
```
