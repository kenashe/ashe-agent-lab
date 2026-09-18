# Roadmap

Proposals for what comes after Phase 0. Nothing here is built. Each phase is
sized to be finishable; if one reads like a quarter of work, it is mis-scoped.

## What Phase 0 delivered

- A YAML spec with a `kind: single_agent.v0` discriminator and a
  `schema_version` integer, parsed by `src/ashe_lab/spec.py` into frozen
  dataclasses (`ExperimentSpec`, `Condition`, `Item`, `ModelSpec`,
  `EvaluatorSpec`, `AnalysisSpec`), under strict load-time validation:
  accumulated error lists, rejected unknown keys, and a check rendering every
  template against every item before a token is spent.
- A provider boundary (`providers/base.py`: `Provider`, `ModelRequest`,
  `ModelResponse`, `Usage`) with an offline `echo` fixture plus `openai_chat`
  and `anthropic_messages` adapters, resolved by string through a registry, and
  a parallel registry of deterministic `builtin.*` evaluators where a raising
  evaluator records a `None` score rather than killing a run.
- A sequential runner recording every attempt, including failures, into
  immutable, SHA-256-checksummed, sealed run directories (`storage.py`):
  `manifest.json`, `experiment.snapshot.yaml`, `trials.jsonl`, `events.jsonl`,
  `results.json`, `trials.csv`, `report.md`, `checksums.sha256`.
- Descriptive aggregation (`metrics.py`), Markdown reports, an `ashe-lab` CLI
  (`run / report / validate / verify / show / list`), a pytest suite, and one
  dependency: PyYAML.

Known gaps: no parallel execution, no resume, no re-scoring of stored runs, and
a hand-maintained price table in `pricing.py`.

## How phases are chosen

Cheapest high-leverage work first. A phase earns its place by multiplying the
value of evidence that already exists, removing a present obstacle, and
unblocking the phase after it. Nothing speculative; between equal candidates,
the one adding no new format and no new dependency goes first.

---

## Phase 1 — Re-scoring stored runs

*Apply new or fixed evaluators to every past run without calling a model again.*

**Why now.** Highest leverage per line in the roadmap. `trials.jsonl` already
preserves response text and prompts, so every measurement is recomputable
offline for free: an evaluator bug that today invalidates a paid run afterwards
costs nothing. Every later phase reads scores.

**Scope.** `src/ashe_lab/rescore.py`, reading a sealed run and writing a *new*
derived artifact at `runs/<experiment-id>/<run-id>.scorings/<scoring-id>/`:
`scoring.json` (evaluator specs, framework commit, `source_trials_sha256`),
`scores.jsonl` keyed by `trial_key`, plus a recomputed `results.json`,
`report.md`, `checksums.sha256`. The source run is untouched, its checksum
verified first. CLI: `ashe-lab rescore <run>`, `ashe-lab list scorings`, and
`--scoring <id>` on `report` and `show`.

**Out of scope.** Network-calling evaluators (Phase 4), merging scorings across
runs (Phase 2), and editing a run in place, ever.

**Design risks / open questions.**
- Where scorings live is the real decision. Inside the sealed run is
  ergonomically nicer and structurally wrong: it forces `unseal_run` on every
  rescore and makes `verify_run` report drift routinely. Prefer a sibling
  directory and teach `resolve_run_path` about it.
- Ground-truth drift. `EvaluationContext` carries `item_expected` from the spec
  *as run*. Scoring against a newer spec silently changes ground truth. Default
  to the snapshot, require a flag otherwise, stamp which was used.

**Done when.** A rescore verifies clean; rescoring with the original evaluators
reproduces `results.json` exactly; tampered evidence makes `rescore` refuse.

**New dependencies.** None.

---

## Phase 2 — Cross-run comparison and dataset export

*Compare runs — model vs model, replication vs original, an old spec against a
newer model — and get the corpus into one analysable table.*

**Why now.** Phase 0 answers questions about one run; almost every real question
is about two or more. It adds no execution machinery, only a reader over
existing artifacts, and Phase 3 cannot start without trials aligned across
runs.

**Scope.** `src/ashe_lab/corpus.py` to discover runs and scorings under `runs/`
and align trials by `(condition_id, model_alias, item_id, repetition)`.
`ashe-lab compare <run-a> <run-b> ...` prints group means per evaluator plus a
compatibility report: same `spec_hash`? same items and evaluator ids? aliases
resolving to different `model_reported` values? `ashe-lab export` writes one
long table extending `BASE_CSV_COLUMNS` with `run_id`, `spec_hash`,
`scoring_id`, `model_reported`. Plus a documented replication workflow: copy an
experiment, change only `model:` under stable aliases, run, compare.

**Out of scope.** Statistical tests (Phase 3), charts (Phase 5), and any index
file or database — `storage.list_runs` is fast enough and never goes stale.

**Design risks / open questions.**
- Comparability is the hard part, not the table. Two runs with different
  `spec_hash` values may be perfectly comparable or not at all, so the tool must
  classify differences rather than reduce them to same/different, and refuse a
  headline comparison when conditions or items do not align. The export's
  columns also become a public interface the moment anyone scripts against
  them.
- `model_reported` drift is both the point of re-running against newer models
  and the main trap: one alias can cover three snapshots in a year. Comparisons
  key on reported model, not requested model, and say so.

**Done when.** `export` covers every run of an experiment in one CSV; `compare`
flags an altered prompt as incomparable and a whitespace edit as comparable.

**New dependencies.** None.

---

## Phase 3 — Honest inference

*Uncertainty intervals and paired within-item analysis, with sample size stated
loudly enough that nobody mistakes direction for result.*

**Why now.** `metrics.py` stops at means on purpose. Phase 2 makes pooling
possible, which is what makes inference worth attempting: n=3 per cell cannot
support it, n=3 across twenty runs might. It precedes charts, since a chart with
no interval is exactly the false confidence the project refuses.

**Scope.** `src/ashe_lab/inference.py`: bootstrap intervals for group means, a
paired within-item bootstrap or permutation test for condition contrasts (the
natural design, since `Item` is held constant across conditions), and a
minimum-n gate below which an interval reports "not estimable" rather than a
number. `AnalysisSpec` gains optional additive keys, e.g. `inference: { method:
bootstrap, resamples: 10000, ci: 0.95, paired_by: item }`. `results.json` grows
an `inference` section; reports render intervals beside means, print the
contrast count, and carry a `power_note` naming the effect size this design
could plausibly detect.

**Out of scope.** Mixed-effects models, Bayesian posteriors, sequential testing,
stopping rules. No p-value as a headline number.

**Design risks / open questions.**
- The largest risk in the roadmap is that this phase makes the framework better
  at manufacturing false confidence than it is today. Mitigation must be
  structural, not editorial: gate on minimum n, print n beside every interval,
  never emit a bare significance verdict.
- What is the unit of independence? Repetitions of one item on one model are not
  independent observations, so resampling trials yields intervals far too
  narrow. A cluster bootstrap over items is probably right; settle it first.
- Multiple comparisons. Eight evaluators times three conditions is 24 contrasts.
  Pre-registering a primary outcome in the spec and marking the rest exploratory
  is cheaper and more honest than a correction, which correlated evaluators
  would render badly conservative anyway.
**Done when.** Intervals appear with n; a low-n case reports "not estimable";
the seed lands in `results.json` and the bootstrap is reproducible from it; old
specs still validate.

**New dependencies.** None. `random` and `statistics` suffice. NumPy would only
be faster; SciPy would buy analytic tests deliberately declined.

---

## Phase 4 — Judged and reviewed evaluation

*Measurements a regular expression cannot make: model-judged quality and
human-labelled ground truth.*

**Why now.** Deterministic evaluators count hedges; they cannot say whether an
answer was correct. After Phase 1 because a judge pass *is* a rescore pass, and
after Phase 3 because judge quality is itself an empirical question.

**Scope.** An `llm_judge.*` namespace in the evaluator registry, implemented over
the existing `Provider` interface so judges are provider-independent by
construction and their calls are recorded with the same request/response/usage
detail as trials, appending raw evidence and a cost summary to the scoring
directory. Judge prompts live in `EvaluatorSpec.params`, so they are snapshotted
and hashed. A `human.*` namespace plus `ashe-lab review <run>`: emit a blinded
worksheet, ingest a completed one, write it as a scoring with reviewer id and
timestamp. Plus agreement statistics across judges.

**Out of scope.** Any review UI, multi-reviewer adjudication, local or
fine-tuned judges, and pairwise preference judging — a separate design.

**Design risks / open questions.**
- Judged scorings are not reproducible in the sense the rest of the framework
  means. Honest framing: the judge's raw output is preserved evidence, the score
  a measurement with its own error term — and reports must show that.
- A judge that can see which arm it is scoring can favour one, poisoning
  everything. Blinding is the default and hard to switch off; self-preference,
  where judge and subject share a `model_reported`, warrants a warning.
- Score extraction from prose: a judge asked for 1-5 will sometimes answer
  "4/5, though...", and parse failures must be `None` with raw text kept. Cost
  asymmetry bites too — three judges over 300 trials is 900 calls. And human
  review throughput binds hardest, so sampling must be first-class.

**Done when.** A judged scoring runs end to end against `echo` plus a fixture
judge; blinding is default; unparseable output yields `None` with raw text
kept.

**New dependencies.** None — judges reuse existing provider adapters.

---

## Phase 5 — Charts and static publishing

*A browsable, shareable, self-contained record generated from stored evidence.*

**Why now.** There is finally something worth publishing: multiple runs,
intervals, judged measurements. Earlier would have meant publishing means with
no uncertainty. It is cheap rendering over `results.json` and the Phase 2
loader, and it makes the work legible to anyone else.

**Scope.** `src/ashe_lab/charts.py` emitting hand-written SVG for the two or
three plots that matter: group means with intervals, per-item dot plots,
distribution strips. SVG is text, diffable, durable, dependency-free. `ashe-lab
publish [--out site/]` generates static HTML from existing Markdown and JSON:
an index, a page per experiment and run, embedded SVG, links to raw artifacts.
No JavaScript, no server, works from `file://`.

**Out of scope.** Interactive plots, dashboards, hosting, search, templating
engines, anything needing a running process.

**Design risks / open questions.**
- Hand-written axis and tick layout is fiddlier than it sounds (label collision,
  categorical ordering). Mitigation is scope: three chart types, opinionated
  defaults, no general plotting API. If that fails, the answer is fewer charts,
  not a plotting library.
- Publishing by default will eventually publish a leaked prompt or a private
  note, so opt-in per experiment beats opt-out. Pages get read out of context:
  every chart needs n on its face and every page a provenance line. Generated
  files must never land inside `runs/`.

**Done when.** `publish` renders correctly from disk with no network; every
chart shows n; regeneration is idempotent; no run directory is modified.

**New dependencies.** None. Matplotlib was considered and declined: a large
dependency for three static plots, emitting SVG no more durable than ours.

---

## Phase 6 — Interactive single agents: `agent_loop.v0`

*Multi-turn episodes, tool use, and episode-scoped memory for one agent — plus
the record format multi-agent work will need.*

**Why now.** The first phase that changes the shape of a trial, so it waits
until the single-shot path is rescorable, comparable, defensibly analysed,
judged, and publishable. It is also the right step before multi-agent: an
episode of turns with tool calls is most of the record structure a conversation
needs, at half the complexity.

**Scope.** A `kind: agent_loop.v0` branch in `spec.py` alongside
`single_agent.v0`, which stays unchanged, adding `tools`, `max_turns`,
`stop_conditions`, and an optional episode-scoped `memory`. A tool registry
mirroring the provider and evaluator registries, with offline deterministic
`builtin.*` tools. Trial records gain a `turns` array — request, response, tool
calls, tool results, usage — and bump to `trial.v1`, with `trial.v0` still
readable. Evaluators gain an episode context: transcript, tool-call sequence,
turn count. Plus the robustness long episodes justify: `ashe-lab resume <run>`,
continuing into a *new* run directory that references the partial one, and
bounded parallelism (`--workers N`, default 1).

**Out of scope.** Multiple agents, network-calling tools, cross-episode
persistent memory, third-party agent frameworks — the loop is ours.

**Design risks / open questions.**
- Tool calling is the least standardised part of every vendor API. Keeping
  `providers/base.py` neutral means designing a vendor-independent tool-call
  representation and translating per adapter; getting it wrong makes a vendor
  foundational, which is disqualifying. Fallback: text-protocol tool calls
  parsed by the framework — less capable, fully portable.
- Episode records are unbounded: a 40-turn episode with large tool outputs
  makes `trials.jsonl` enormous. That is the first concrete argument for SQLite,
  to be settled by measurement rather than anticipation.
- Resume is a correctness trap: never silently re-run succeeded trials nor skip
  failed ones, and it splits one experiment across two directories that Phase
  2's logic must then understand. Parallelism separately destroys the event log
  as a chronological narrative — default stays 1, manifest records the count.

**Done when.** An `agent_loop.v0` experiment runs multi-turn with an offline
tool against `echo`; `single_agent.v0` specs and stored `trial.v0` runs still
load, report, rescore, and export unchanged; resume finishes an interrupted run
without duplicating work.

**New dependencies.** None.

---

## Phase 7 — Multi-agent settings: `multi_agent.v0`

*Conversations between agents with private and shared information, turn-based
environments, games, and declared incentives.*

**Why now.** Last: the largest spec-format change, and it depends on everything
before it — a turn and tool record format (6), transcript scoring by judge and
human (4), cross-run comparison (1-2), and inference that will not overclaim
from the small n expensive multi-agent runs force (3).

**Scope.** `kind: multi_agent.v0`: an `agents` list (each with its own model
alias, system prompt, and private `information` block), a `shared_context`
block, and a `protocol` block declaring turn order, termination, and what each
agent observes. Built-in protocols as explicit turn-order functions —
round-robin, fixed-script, simultaneous-reveal — plus a declared payoff table
for the incentives case, shown to agents as text and scored deterministically.
Episode records extend `turns` with speaker and per-agent visibility, so "what
did agent B see when it spoke" stays recoverable. Transcript evaluators:
agreement reached, defection, leaked private information, turns to
termination.

**Out of scope.** A general environment API, more than a handful of agents,
concurrent speaking, cross-episode learning, a game engine. Three settings —
negotiation, hidden-information QA, one simple repeated game — are the target.

**Design risks / open questions.**
- The spec format is the risk. Multi-agent designs invite an expressive-config
  spiral ending in a programming language written in YAML. Discipline: a setting
  the protocol vocabulary cannot express gets a named protocol in Python, not a
  new configuration primitive.
- Information leakage is a correctness property, not a feature. A private block
  reaching the wrong context voids the experiment while producing perfectly
  plausible results. It needs a hard invariant, tests asserting on recorded
  per-turn visibility, and ideally a validation-time check.
- Combinatorics: agents x conditions x items x repetitions, each episode many
  calls long, so pre-run cost estimation becomes mandatory. Very small n is the
  norm, and the inference layer must keep refusing to oblige.
- Attribution. When a two-agent outcome shifts, which agent changed? Holding one
  fixed should be the default and the easy path. And a scripted-agent fixture is
  required, or none of this is testable without spending money.

**Done when.** A two-agent negotiation with asymmetric private information runs
offline against that fixture; a test proves private information never appears in
another agent's recorded context; earlier kinds and stored runs are unaffected.

**New dependencies.** None.

---

## Spec-format evolution and the compatibility promise

`kind` names the *shape* of an experiment. New shapes are new values with new
parser branches in `spec.py`; `SUPPORTED_KINDS` grows and nothing is removed, so
`single_agent.v0` parses identically in every future version, and a spec
declaring an unknown kind is rejected explicitly rather than misread.

`schema_version` bumps only for a breaking change *within* a kind. Additive
changes — a new optional key, a new evaluator type, a new `analysis` sub-block —
do not bump it, which is why most phases above extend the spec without touching
it. Stored runs follow the same discipline: record schemas are tagged
(`trial.v0`, `manifest.v0`, `results.v0`) and readers dispatch on the tag.

The promise: **any run this framework has ever written can be read, verified,
re-scored, exported, and reported by every future version.** Evidence outlives
code; where the promise and a cleaner design conflict, the promise wins.

## Deliberately unscheduled

May never be built. Each needs something specific to change first.

- **Web app or hosted service.** Only if several people need concurrent write
  access to a shared corpus — a different project, not a feature of this one.
- **Real-time dashboards.** Only if runs last long enough that tailing
  `events.jsonl` stops being sufficient. It currently is.
- **Distributed execution.** Only if one experiment cannot finish overnight on
  one machine even with bounded parallelism.
- **GUI or desktop app.** Only if human review proves unsustainable with
  worksheets — and then a static local page writing a CSV.
- **A database as primary store.** Only when real JSONL becomes measurably
  painful to load, and then SQLite, with JSONL still the archival format.
