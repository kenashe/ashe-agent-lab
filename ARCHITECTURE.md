# Architecture

This document explains how Ashe Agent Lab is put together and, more
importantly, **why each decision was made and what the alternative was.** If
you are about to change something structural, the reasoning here is what you
need to argue against.

`VISION.md` covers purpose. `AGENTS.md` covers procedure. This covers structure.

---

## 1. The shape of the thing

```
                      experiments/<id>/experiment.yaml
                                   │
                            ┌──────▼──────┐
                            │   spec.py   │  parse + validate + render templates
                            └──────┬──────┘
                                   │  ExperimentSpec (frozen dataclasses)
                            ┌──────▼──────┐
                            │  runner.py  │  plan → execute → retry → record
                            └──┬───────┬──┘
                   ModelRequest│       │EvaluationContext
                   ┌───────────▼─┐   ┌─▼──────────────┐
                   │ providers/  │   │ evaluators/    │
                   │ (adapters)  │   │ (deterministic)│
                   └───────────┬─┘   └─┬──────────────┘
                  ModelResponse│       │EvaluationResult
                            ┌──▼───────▼──┐
                            │  storage.py │  immutable run directory + checksums
                            └──────┬──────┘
                                   │  trials.jsonl  ← the primary artifact
                          ┌────────┴────────┐
                   ┌──────▼─────┐    ┌──────▼─────┐
                   │ metrics.py │    │ report.py  │
                   │ results.json│   │ report.md  │
                   │ trials.csv │    │            │
                   └────────────┘    └────────────┘
```

Data flows one way. `spec.py` knows nothing about storage. `storage.py` knows
nothing about providers. `metrics.py` and `report.py` read only stored records,
which is why a report can be regenerated years later from evidence alone.

### Dependency direction

`errors` and `ids` are leaves — everything may import them, they import
nothing. `spec` depends only on those plus PyYAML. `providers` and
`evaluators` depend on `errors`. `runner` is the only module that depends on
both sides. `metrics` and `report` depend on `spec` for shape and nothing else.

There is no module that everything imports and which imports everything. That
is not an accident; it is the property that keeps the pieces independently
replaceable.

---

## 2. The unit of evidence: a trial

A **trial** is one `(condition, model, item, repetition)` tuple.

This choice determines everything downstream. Because trials are independent
records:

- a run can be interrupted and keep everything it already paid for;
- parallel execution can be added later without changing the storage format;
- re-scoring stored responses with new metrics needs no model calls;
- a failed trial can be recorded as a first-class row rather than a gap.

A trial is written as one JSON object per line in `trials.jsonl`, containing
the rendered prompts, the literal request sent, the response, every attempt
with its timing and error, token usage, cost estimate, and evaluator scores.
Full field reference: `docs/RUN_FORMAT.md`.

### Conditions and items are separate on purpose

The **item** is the stimulus. The **condition** is the manipulation. Keeping
them separate makes a within-item comparison the default: every condition sees
identical stimuli, so the condition is the only thing that varies.

Collapsing them — letting each condition carry its own prompt text wholesale —
would be simpler to implement and would make it trivially easy to author a
confounded experiment without noticing. The structure exists to make the
correct design the path of least resistance.

---

## 3. What "reproducibility" means here

This is the most important thing to be honest about, because the word is
routinely oversold.

**Hosted language models are not bit-reproducible.** Temperature 0 does not
guarantee identical output; `seed` is best-effort where it exists at all;
providers silently reroute an alias to a new snapshot; batching affects
floating-point accumulation. Any framework promising "reproducible LLM
experiments" in the strict sense is either wrong or is not calling hosted
models.

So the framework promises something weaker and actually deliverable:
**everything needed to reconstruct, audit, and re-attempt what happened.**

| Preserved | Why it matters |
|---|---|
| The rendered prompts, verbatim | The manipulation *is* the prompt |
| The literal request sent (`request`, with `request_hash`) | Not a reconstruction — the actual bytes |
| `model_reported` from the provider | Often a dated snapshot behind an alias; the only way to know whether two runs hit the same model |
| `spec_hash` | Identical hash ⇒ identical experiment design |
| `experiment.snapshot.yaml` | The author's bytes, comments included, as executed |
| Framework version, git commit, **and whether the tree was dirty** | A dirty tree means the commit does not fully describe the code that ran; saying so is better than implying otherwise |
| Environment fingerprint | Python version, platform — enough to spot an environmental surprise |
| Every attempt, with errors and timings | Retries are evidence, not noise |

The `echo` fixture provider *is* bit-reproducible, which is how the pipeline
itself gets tested for determinism even though models are not.

---

## 4. Key decisions

### 4.1 YAML in, JSONL/JSON/CSV/Markdown out

**Decision.** Experiments are authored in YAML; evidence is stored as JSONL;
aggregates as JSON; a flat export as CSV; reports as Markdown.

**Why.** YAML is the only widely-used format that supports comments, and an
experiment definition without comments loses the author's reasoning — which is
half of what makes an old experiment interpretable. JSONL is append-only by
nature, survives truncation (you lose one line, not the file), and is readable
by `head`, `jq`, pandas, and a text editor. Markdown renders everywhere and
degrades to plain text.

**Alternative rejected.** A binary or database-backed format would be faster
to query and would make the evidence unreadable without this code. Given that
the evidence is meant to outlive the code, that trade runs the wrong way.

### 4.2 Flat files, not SQLite (yet)

**Decision.** No database in Phase 0.

**Why.** The access pattern is "append during a run, read everything
afterwards". That is exactly what JSONL is good at and exactly where a
database's advantages are irrelevant. A schema also becomes a migration
obligation on data that must never change.

**When to revisit.** When cross-run querying becomes the primary access
pattern — "show me every trial across all runs where the model refused" — a
SQLite index *derived from* the JSONL files becomes worth it. The rule then:
SQLite is a **cache**, rebuildable from the JSONL, never the source of truth.

### 4.3 One runtime dependency

**Decision.** PyYAML, and nothing else. HTTP via `urllib.request`, CLI via
`argparse`, `.env` parsing hand-rolled in fifteen lines.

**Why.** Every dependency is a future breakage and an installation risk. A
framework intended to run unchanged in a decade should depend on things that
will still exist. `urllib` has been in the standard library longer than most
HTTP client libraries have existed.

**Cost, stated honestly.** `requests` is nicer to write against. `click` gives
better help text. `pydantic` would replace ~400 lines of hand-written
validation. Those are real costs, accepted deliberately. A test enforces the
budget so drift requires an explicit decision.

### 4.4 The provider boundary is a Protocol with one method

**Decision.** A provider exposes `name`, `complete(request) -> ModelResponse`,
and `describe()`. `typing.Protocol`, not an abstract base class.

**Why.** Requiring inheritance would couple every third-party adapter to our
import graph permanently. A Protocol means any object with the right shape
works, which also makes test doubles trivial. The neutral `Message(role,
content)` pair is the lowest common denominator across every current chat API,
so adapters translate rather than the core accommodating.

`anthropic_messages.py` exists specifically to prove the boundary is real: the
Messages API hoists the system prompt to a top-level field, requires
`max_tokens`, and returns typed content blocks. Supporting a genuinely
differently-shaped API from day one is what stops the abstraction from being an
OpenAI-shaped hole.

### 4.5 Retries live in the runner, never in adapters

**Decision.** Adapters raise; the runner decides whether to retry.

**Why.** Every attempt must be recorded as evidence. An adapter that retried
internally would hide the first two failures, and "this provider was flaky for
twenty minutes" is exactly the kind of thing you want to discover in a stored
run six months later. `ProviderError.retryable` carries the adapter's judgement
about whether another attempt is worthwhile; the runner owns the policy.

### 4.6 The registry is explicit, not plugin-discovered

**Decision.** Adding a provider or evaluator means adding one line to a dict in
an `__init__.py`.

**Why.** Entry-point scanning and import-time auto-registration make a
repository harder to reason about — you cannot answer "what providers exist?"
by reading a file. The whole list is visible in one place, and the cost is one
line per adapter. For a repository explicitly designed to be understood by a
newcomer with no context, legibility beats cleverness.

### 4.7 Immutable, sealed, checksummed run directories

**Decision.** A run directory is written once, never modified. On completion,
files are chmod'd read-only and every file is SHA-256 hashed into
`checksums.sha256`.

**Why.** This is the project's core guarantee. `RunWriter` refuses to open an
existing directory — there is no overwrite path to accidentally take. Sealing is
a **guardrail against accident**, not a security control: anyone with the
account can `unseal_run()`. It exists to stop a stray script, a careless shell
redirect, or a confused coding agent from clobbering an experimental record.
The checksums are the actual integrity mechanism, and they are written in
`sha256sum` format so a stranger with coreutils can verify a run with no Python
installed.

**The one sanctioned mutation.** `storage.write_report()` regenerates
`report.md`, because the report is *derived* from `trials.jsonl` and an
improved template should be applicable to old runs. It rewrites checksums and
re-seals, so `verify` stays meaningful. Nothing else may write into a finished
run.

### 4.8 Sequential execution

**Decision.** One trial at a time.

**Why.** The event log becomes a true chronological narrative; rate-limit
behaviour is predictable; and concurrency is the single largest source of bugs
that cannot be reproduced. The cost is wall-clock time on large runs, which is
a real cost and the reason parallelism is on the roadmap. The storage format
already tolerates it, since trials are independent.

**Trial ordering is condition-major** (condition → model → item → repetition).
An interrupted run therefore has *complete* coverage of early conditions rather
than *partial* coverage of all of them. Partial data with a known shape is
analysable; partial data with an unknown shape is not.

### 4.9 Null is not zero

**Decision.** Unknown values are `null` everywhere: unreported token counts,
unmeasurable evaluator scores, unpriced models, standard deviation at n=1.

**Why.** This is the subtlest way a measurement framework can lie. If a
provider does not report usage and the framework records `0`, the mean token
count is silently wrong. If an item has no ground truth and
`contains_expected` returns `0.0` instead of `None`, accuracy is understated in
proportion to how many items lack answers. Aggregation excludes nulls and
reports `n_unmeasured` alongside every statistic, so a reader can always see
how much of the group was actually measured.

### 4.10 Descriptive statistics only

**Decision.** n, mean, median, min, max, sample standard deviation, and raw
differences from control. No p-values, no confidence intervals, no effect
sizes.

**Why.** A t-test on three trials per condition produces a number that *looks*
like evidence and is not. The framework's purpose is to avoid manufacturing
false confidence, and the fastest way to betray that purpose would be to ship
inferential statistics before the sample sizes justify them. Reports state
their own n and warn when groups have fewer than five successful trials.

Inferential statistics arrive in a later phase with an explicit power
discussion and a stated multiple-comparison policy — not before.

### 4.11 Unknown spec keys are errors

**Decision.** A key the parser does not recognise fails the load.

**Why.** Consider `temprature: 0.0`. Tolerated, it produces a run at the
provider's default temperature that the author believes was deterministic. That
is a corrupted experiment that looks fine. Every validation problem is
accumulated and reported together, so an author sees all of them in one pass.

### 4.12 Cost estimates are dated and hand-maintained

**Decision.** A price table in `pricing.py`, every entry stamped with an
`as_of` date. Unknown models produce `null` with an explanation.

**Why.** Fetching live prices would make an offline run in 2031 produce
different numbers than the same run today, and would add a network dependency
to a pure calculation. Hand-maintained means occasionally stale, which is why
every estimate carries its rate date and every report calls costs estimates
rather than totals. When any trial is unpriced, the aggregate is labelled a
floor (`≥ $X`), not a total.

### 4.13 An offline fixture provider is load-bearing

**Decision.** `echo` ships as a first-class provider, along with `failing` for
fault injection.

**Why.** It is not a convenience. It means the full test suite runs in CI with
no secrets (and therefore keeps working for forks); a new contributor or agent
can execute a real end-to-end experiment within a minute of cloning; and the
storage, metrics, report, and CLI layers are exercised against byte-stable
output, so a test failure means a regression rather than model variance.

**The risk it introduces** is someone mistaking echo output for a result.
Mitigated by naming (`echo-fixture`), by the provider's `describe()`, by
comments in the example experiment, and by a report caveat stating in bold that
any apparent difference between conditions is an artifact of the prompt bytes
changing a hash.

---

## 5. Extension points for planned capabilities

These are the seams that exist so that roadmap items are additive rather than
rewrites. None are implemented.

| Planned capability | Where it attaches |
|---|---|
| Multi-agent conversations | New `kind: multi_agent.v0` in `spec.py` + a parser branch and a new record schema `trial.v1`. `single_agent.v0` keeps its exact meaning forever. |
| Turn-based environments, games | Same: a new `kind`, plus an environment abstraction alongside `providers/`. The runner's trial loop becomes one strategy among several. |
| Private vs shared information | A per-agent message-construction step before `ModelRequest` is built. `Message` already carries roles; visibility is a spec-level concern. |
| Tool-using agents | `ModelRequest.options` already passes tool definitions through. A tool-execution loop belongs in a new runner strategy, with each tool call recorded as an attempt-like record. |
| Persistent memory | A store keyed by `(experiment, condition, agent)`, injected as messages. Must be snapshotted per trial or the evidence is incomplete. |
| Incentives and penalties | Spec-level scoring rules plus an evaluator namespace. No framework change needed. |
| Model-vs-model comparison | Already supported: list multiple models; they appear as groups. Pairwise reporting is a `report.py` addition. |
| LLM-based evaluators | Reserved `llm_judge.*` namespace in the evaluator registry. Needs a caching story so re-scoring is not re-paying, and the judge model must be recorded as evidence. |
| Human review | Reserved `human.*` namespace. Reviews are new records, never edits to existing ones. |
| Statistical analysis, charts | Read `trials.jsonl` / `trials.csv`. Would be the first justified new dependency. |
| Dataset export | `metrics.trials_to_csv_rows` is the template; add formats there. |
| Static web publishing | A generator reading stored runs. Run ids and slugs are already URL-safe, which is why the slug pattern is restrictive. |
| Re-running against newer models | Already supported: change `model:` under a stable `alias:` and run. Matching `spec_hash` values make runs comparable; a changed hash tells you the design moved too. |
| Re-scoring without re-calling | The stored `response.text` makes this nearly free. Needs a new run kind that records its parent run id. |
| Parallel execution | Trials are independent; `trials.jsonl` tolerates any write order. Needs an ordering guarantee for `events.jsonl` or an explicit acceptance that it becomes interleaved. |

---

## 6. Deliberately absent

No web application. No user accounts. No billing. No cloud infrastructure. No
containers. No queues. No distributed execution. No dashboard. No agent
orchestration framework. No vendor SDKs.

Each of these would be justified by scale or by multi-user requirements. This is
a single-researcher tool operating on hundreds to thousands of trials, run from
a laptop. Adding infrastructure for a scale that does not exist would cost
exactly the durability and understandability the project is for.

---

## 7. Known limitations

Stated plainly so nobody has to discover them by surprise:

1. **No parallelism.** A thousand-trial run against a slow model takes as long
   as a thousand sequential calls.
2. **No resume.** An interrupted run leaves valid partial evidence, but
   finishing the remaining trials means a new run.
3. **No re-scoring command.** The stored evidence makes it possible; no CLI
   verb does it yet.
4. **Token counts for `echo` are estimates** (~4 chars/token), labelled as
   such. Real providers report real counts.
5. **`sentence_count` and `refusal_marker` are crude heuristics**, and say so
   in their notes.
6. **The price table is hand-maintained** and will go stale. Every estimate
   carries its rate date.
7. **Sealing depends on POSIX permissions.** Filesystems that ignore them
   (some container overlays, some network mounts, anything running as root)
   cannot enforce it. Checksums still detect tampering.
8. **`contains_expected` is a substring test**, not a comprehension check.
9. **No structured-output or tool-calling support** beyond passing options
   through to a provider.
10. **Secrets are only as safe as the environment.** The framework never logs
    or stores credentials, but it cannot protect a key pasted into a spec file
    — so do not do that.

---

## 8. Testing strategy

The suite runs fully offline with no credentials, and that constraint is
permanent: a CI job requiring an API key stops working for forks.

| File | What it protects |
|---|---|
| `test_spec.py` | Validation rejections — every way a spec could silently measure the wrong thing |
| `test_providers.py` | The adapter contract; HTTP translation with the network stubbed; credentials never leaking into `describe()` |
| `test_evaluators.py` | Determinism, honest nulls, failure isolation |
| `test_runner.py` | End-to-end runs; failed trials recorded not dropped; every retry preserved |
| `test_storage.py` | Immutability, sealing, checksum drift detection, partial-run survival |
| `test_metrics_and_report.py` | Null-not-zero, cost floors, reports stating their own limits |
| `test_cli.py` | Every command the documentation tells a reader to run |
| `test_repo_hygiene.py` | No credential-shaped strings; `.gitignore` coverage; required docs exist; dependency budget holds |

The tests that matter most are the ones asserting things that would otherwise
fail *silently*: a dropped failure, a null becoming a zero, a report that
overstates its evidence. Those are the bugs that corrupt conclusions rather
than crashing.
