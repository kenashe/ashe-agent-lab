# Ashe Agent Lab

A small, durable framework for running **reproducible AI agent experiments**
and preserving the evidence.

You describe an experiment in a YAML file. It runs the cross product of your
conditions, models, and stimuli; records every prompt, response, retry, token
count, and cost estimate into an immutable, checksummed run directory; scores
the responses with deterministic metrics; and writes a Markdown report that is
honest about what its sample size can support.

One runtime dependency (PyYAML). Everything else is the Python standard
library. No vendor SDKs, no agent framework, no database, no web service.

> **Status: Phase 0.** The core loop works end to end and is tested. See
> [ROADMAP.md](ROADMAP.md) for what comes next and what is deliberately
> unscheduled.

---

## Try it in thirty seconds

No API key, no network, no spend — the built-in `echo` fixture provider is
offline and deterministic.

```bash
git clone https://github.com/kenashe/ashe-agent-lab.git
cd ashe-agent-lab
pip install -e ".[dev]"        # or just: export PYTHONPATH=src

ashe-lab run fact-check-awareness-001
ashe-lab show fact-check-awareness-001
python -m pytest
```

That executes a real 45-trial experiment, writes a complete run directory, and
generates a report. Then look at what it preserved:

```bash
ls runs/fact-check-awareness-001/*/
# checksums.sha256  events.jsonl  experiment.snapshot.yaml  manifest.json
# report.md  results.json  trials.csv  trials.jsonl
```

The `echo` provider's responses are hash-derived text, not model behaviour.
The run proves the pipeline works; it tells you nothing about language models.
To get real data, add a key and enable a real model — see
[Using a real model](#using-a-real-model).

---

## Why this exists

Informal AI experimentation is unreproducible almost by default. Prompts get
edited in place. Results live in chat scrollback. Six months later, "I tried
this last year and it behaved differently" is unanswerable, because nothing
that would answer it was kept.

The bet this project makes is that **the durable artifact is the preserved run,
not the code**. Everything else follows from that: flat text formats you can
read without this software, immutable run directories, checksums, failed
trials recorded rather than dropped, and a deliberate refusal to compute
statistics the data cannot support.

[VISION.md](VISION.md) makes the full argument.

---

## The model

An **experiment** is a YAML file declaring:

- **conditions** — the manipulation (what varies)
- **items** — the stimuli (what stays the same across conditions)
- **models** — what is under test
- **evaluators** — deterministic measurements applied to every response

A **trial** is one `(condition, model, item, repetition)` tuple — the atomic
unit of evidence. The runner executes the cross product and records each trial
as one line of JSON.

Keeping the stimulus in `items` and the manipulation in `conditions` is what
makes a within-item comparison the default, so the condition is the only thing
that varies. It is a small structural choice that makes the correct design the
path of least resistance.

Here is a complete experiment:

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

Every key is documented in [docs/EXPERIMENT_SPEC.md](docs/EXPERIMENT_SPEC.md).

---

## What a run preserves

```
runs/<experiment-id>/<run-id>/
├── trials.jsonl              ← primary evidence, one JSON object per trial
├── events.jsonl                 chronological lifecycle log
├── manifest.json                spec hash, framework commit, environment, counts
├── experiment.snapshot.yaml     the definition as executed, comments and all
├── results.json                 aggregated statistics
├── trials.csv                   flat export for pandas / R / spreadsheets
├── report.md                    human-readable report
└── checksums.sha256             SHA-256 of every file above
```

Each trial record holds the rendered prompts, the literal request sent, the
response, **every attempt including failed ones**, token usage, a dated cost
estimate, and every evaluator's score with its supporting detail.

Run directories are written once and then sealed read-only. There is no
overwrite flag: re-running an experiment produces a *new* run. Integrity is
verifiable with or without this framework:

```bash
ashe-lab verify runs/fact-check-awareness-001/<run-id>
cd runs/fact-check-awareness-001/<run-id> && sha256sum -c checksums.sha256
```

Full field reference: [docs/RUN_FORMAT.md](docs/RUN_FORMAT.md).

---

## Design commitments

These are the decisions that shape everything else. The reasoning behind each
is in [ARCHITECTURE.md](ARCHITECTURE.md).

**Durable formats.** YAML in; JSONL, JSON, CSV, and Markdown out. Readable by
`head`, `jq`, pandas, a text editor, and a human in 2040.

**Provider independence.** No vendor sits at the foundation. An adapter is one
class with one method, registered with one line. Ships with `openai_chat`
(which also serves Azure, OpenRouter, Together, vLLM, llama.cpp — anything
speaking chat completions), `anthropic_messages`, and the offline `echo`
fixture.

**Honest reproducibility.** Hosted models are not bit-reproducible, and this
project does not pretend otherwise. What it guarantees is that everything
needed to reconstruct and audit a run is preserved: the exact request, the
model the provider *reported* serving, the spec hash, the framework commit
*and whether the working tree was dirty*, and the environment.

**Null is never zero.** Unreported token counts, unmeasurable scores, unpriced
models, and standard deviation at n=1 are all `null`. An absence and a
measurement of zero are different facts, and conflating them is the subtlest
way a measurement framework can lie.

**Failures are evidence.** A trial that failed is recorded with `status:
"error"`, the prompt attempted, and every attempt's error. Dropping it would
bias every aggregate — if one condition fails more often, omitting those rows
makes it look *better*.

**No manufactured confidence.** Phase 0 computes descriptive statistics only.
No p-values, no confidence intervals. A t-test on three trials produces a
number that looks like evidence and is not. Reports state their own n and warn
when groups are too small to interpret.

**Simplicity as a feature.** No containers, queues, databases, dashboards, or
web services. Flat files until there is a concrete reason otherwise.

---

## Using a real model

```bash
cp .env.example .env     # then add your key; .env is gitignored
```

Enable a model in the experiment's `models:` list:

```yaml
models:
  - alias: gpt-4o-mini           # stable local name, appears in results
    provider: openai_chat
    model: gpt-4o-mini           # the vendor's id, expected to change over time
```

Then validate, preview, smoke-test, and run:

```bash
ashe-lab validate my-experiment-001                       # free, calls nothing
ashe-lab run my-experiment-001 --dry-run                  # prints every rendered prompt
ashe-lab run my-experiment-001 --model gpt-4o-mini --max-trials 2   # cheap smoke test
ashe-lab run my-experiment-001 --model gpt-4o-mini --trials 20 --delay 0.5
```

The `alias` / `model` split is what makes **re-running a 2026 experiment
against a 2029 model a one-line edit**: the alias labels the results group, so
the two runs stay directly comparable, and the `spec_hash` tells you whether
anything else about the design moved.

Credentials are read from the environment (or `.env`), never from a spec file,
and are never written into a run directory — a provider's manifest entry
records credential *presence* as a boolean and nothing more.

---

## Commands

```bash
ashe-lab run <experiment>        # execute; writes a new immutable run
ashe-lab report <run|experiment> # regenerate the Markdown report from raw records
ashe-lab validate [--all]        # check definitions; calls nothing, costs nothing
ashe-lab verify <run|experiment> # recompute checksums, report drift
ashe-lab show <run|experiment>   # summarise a stored run
ashe-lab list experiments|runs|providers|evaluators|pricing
```

Useful flags on `run`: `--trials N`, `--condition ID`, `--model ALIAS`,
`--item ID`, `--max-trials N`, `--delay SECONDS`, `--dry-run`, `--json`,
`--quiet`, `--runs-dir PATH`, `--label TEXT`.

Exit codes: `0` success · `1` framework error · `2` usage error · `3` run
completed but some trials failed · `4` integrity check found drift.

`python -m ashe_lab` works identically if you have not installed the package.
`make help` lists the Make targets.

---

## Adding your own experiment

```bash
mkdir -p experiments/my-experiment-001
cp experiments/fact-check-awareness-001/experiment.yaml experiments/my-experiment-001/
# edit it, then:
ashe-lab validate my-experiment-001
ashe-lab run my-experiment-001 --dry-run    # read the rendered prompts
ashe-lab run my-experiment-001
```

The shipped example is commented specifically to be copied. Read the dry-run
output before spending anything — most design errors are visible in the
rendered prompts and invisible in the YAML.

---

## Repository layout

```
src/ashe_lab/
  spec.py         experiment schema, validation, templating   ← the contract
  runner.py       execution: plan, retry, record
  storage.py      immutable run directories, checksums         ← the guarantees
  metrics.py      aggregation → results.json, trials.csv
  report.py       Markdown report rendering
  pricing.py      dated price table → cost estimates
  cli.py          the ashe-lab command
  providers/      adapter boundary + echo, openai_chat, anthropic_messages
  evaluators/     deterministic metrics
experiments/      experiment definitions
runs/             immutable evidence (gitignored by default)
tests/            pytest suite — offline, no credentials
```

---

## Documentation

| Document | Read it when |
|---|---|
| [VISION.md](VISION.md) | You want to know why this exists and what it refuses to be |
| [ARCHITECTURE.md](ARCHITECTURE.md) | You are about to change something structural |
| [AGENTS.md](AGENTS.md) | **You are a coding agent working on this repository** |
| [docs/EXPERIMENT_SPEC.md](docs/EXPERIMENT_SPEC.md) | You are writing an `experiment.yaml` |
| [docs/RUN_FORMAT.md](docs/RUN_FORMAT.md) | You are reading stored evidence |
| [ROADMAP.md](ROADMAP.md) | You want to know what is planned |

`AGENTS.md` is deliberately thorough. A design goal of this project is that a
coding agent can enter the repository cold, read the documentation, and add an
experiment or a provider correctly without further explanation.

---

## Development

```bash
pip install -e ".[dev]"
make check      # validate every experiment + run the full test suite
make test
make demo
```

The suite runs fully offline with no credentials, and that is permanent: CI
holds no secrets, so it keeps working for forks. Tested on Python 3.9 and 3.13.

---

## Security

- Credentials come from environment variables or a gitignored `.env`.
  `.env.example` documents the variable names with placeholder values only.
- Nothing in the framework logs, stores, or returns a credential. Provider
  descriptions in a run manifest record presence as a boolean.
- A test scans the repository for credential-shaped strings, and another
  asserts that every environment variable the code reads is documented.
- Run directories are gitignored by default, since model output may not have
  been reviewed. Publish a specific run deliberately with
  `git add -f runs/<experiment-id>/<run-id>`.

The repository is intended to be safe to keep public.

---

## Known limitations

Stated plainly, because discovering them by surprise is worse:

1. No parallel execution — runs are sequential by design.
2. No resume; an interrupted run leaves valid partial evidence, but finishing
   it means a new run.
3. No command yet to re-score stored runs with new evaluators (the storage
   format supports it; see ROADMAP Phase 1).
4. The price table is hand-maintained and will go stale; every estimate carries
   its rate date.
5. Sealing depends on POSIX permissions, which some filesystems ignore.
   Checksums still detect tampering.
6. Several evaluators are deliberately crude heuristics and say so.

ARCHITECTURE.md §7 has the full list.

---

## License

MIT. See [LICENSE](LICENSE).
