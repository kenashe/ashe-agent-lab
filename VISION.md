# Vision

## The problem

Here is how informal AI experimentation actually goes.

You have a question: *does telling a model that its answers will be fact-checked
make it hedge more?* So you open a chat window, write a prompt, and read the
answer. Interesting. You tweak the prompt — a word or two — and ask again.
Better. You try a harder question. You switch models halfway through because the
first was slow. Somewhere in there a call errored and you retried it, and the
retry is the one you remember. An hour later you have a strong impression and
twelve messages of scrollback.

Three weeks later you want to write that impression down, or defend it, or check
whether a new model release changed anything. Which prompt produced the good
answer — the original or the tweaked one? How many times did you actually ask?
Was temperature the default? Which model version, exactly? The one failed call
you retried: was it failing for a reason that should have made you suspicious of
the whole session?

None of it is answerable, because nothing was preserved. The prompt was edited
in place. The results live in a chat log, unstructured and mixed with your own
commentary. The model behind the endpoint has been silently updated twice. "I
tried this last month and it behaved differently" is a sentence with no way to
resolve it, and the only honest response is to start over.

The problem is not that the experiments were badly designed. Often they are
reasonable. The problem is that the *evidence was never captured*, so the work
is worth nothing an hour after it ends.

This document explains what exists instead, and how to tell whether it is
working.

## What Ashe Agent Lab is

Ashe Agent Lab is a small Python framework for designing, running, preserving,
and reporting experiments on AI agents. An experiment is a single YAML file
declaring a research question, a hypothesis, the models under test, the stimulus
items, the experimental conditions, and the measurements to apply. The framework
validates that file, executes every trial, writes the raw evidence to an
immutable run directory, computes descriptive statistics, and generates a
Markdown report.

It is not a product. It is one researcher's instrument, built to shorten the
distance between "I wonder what an AI would do if…" and a reproducible
experiment with preserved raw data, honest metrics, and a report someone else
can read.

## The core bet

**The durable artifact is the preserved run, not the code.**

Everything in this project follows from that sentence. Code gets rewritten —
this codebase will be unrecognisable in five years, and that is fine. Providers
disappear. Model identifiers rot. The one thing that can never be regenerated
is the record of what a particular model actually did, on a particular day, when
asked a particular thing in a particular way. If that record is lost, the
experiment did not happen.

So the framework treats run directories as the product and the code as the
machinery. A run under `runs/<experiment-id>/<run-id>/` holds the spec exactly
as executed (a byte copy, comments and all), one JSON object per trial, a
lifecycle event log, aggregated results, a flat CSV, a Markdown report, and a
`checksums.sha256` file in `sha256sum` format — so a stranger with coreutils and
no Python can verify the evidence has not been touched. Run directories are
written once and never updated; re-running an experiment makes a new run.
Finished runs are chmod'd read-only: a guardrail, not a security control,
meant to stop a future script or a future coding agent from casually clobbering
a record it did not understand.

## Principles, and why

**Durable formats.** YAML in, JSONL and JSON and CSV and Markdown out. There is
exactly one runtime dependency, PyYAML; everything else is the Python standard
library. A dependency is a bet that someone else will keep maintaining
something. Five years from now the evidence should open in a text editor,
whether or not this framework still installs.

**Reproducibility, honestly defined.** Hosted models are not bit-reproducible.
A fixed seed does not make them so, the endpoint behind a model name changes
without notice, and any framework promising deterministic replay of a hosted
model is lying. So this one does not promise it. It preserves the exact request
sent, the model the provider *reported* serving alongside the one requested, a
content hash of the semantic spec, the framework's git commit, and the
environment. That is a complete record of what happened, which is a different
and more achievable thing than a guarantee of what will happen again. Where the
framework cannot deliver determinism, it delivers documentation.

**Raw evidence is sacred, including the ugly parts.** Failed trials are
recorded, counted, and reported separately — never dropped. Dropping them is the
most tempting and most corrosive thing you can do to a dataset: failures are
rarely random, so every aggregate computed after the drop is biased. Likewise,
trials the evaluators could not measure are counted as unmeasured rather than as
zero, and unpriced trials turn the reported cost into an explicit floor. Trial
records are appended and flushed as they happen, so a crash or an exhausted
budget leaves a partial run containing everything you already paid for.

**Fail loudly, before spending anything.** A spec is fully validated at load
time and every problem is reported at once. Unknown keys are errors, not
warnings — a typo'd key that is silently ignored produces an experiment that did
not test what its author thought it tested, which is the single failure mode
this framework exists to prevent. Templates are rendered against every item
during validation, so a missing placeholder surfaces immediately rather than
three hours and forty dollars into a run.

**Provider independence.** No vendor sits at the foundation. A model entry
names a provider adapter by string and a stable local alias; the alias is what
appears in results, while the vendor's identifier is expected to change. That
split is what makes "re-run this against next year's model" a one-line edit
rather than a reanalysis.

**Refusing false confidence.** Phase 0 computes descriptive statistics only —
n, mean, median, min, max, sample standard deviation — plus raw differences from
the control condition, labelled as directions rather than results. No p-values,
no effect sizes, no confidence intervals. A significance test on three trials
produces a number that looks like evidence and is not, and manufacturing it
would defeat the point of building the thing. Inferential statistics can arrive
later, with explicit power considerations and a stated multiple-comparison
policy.

**Simplicity as a feature.** Flat files before databases: the access pattern is
"append during a run, read everything afterwards", and JSONL is exactly right
for that — readable by `head`, `jq`, pandas, and a human, with no schema
migration. No containers, queues, web services, or dashboards without a concrete
present reason, because each one is a thing that can break between now and the
next time someone opens the repo.

**Written for AI maintainers.** Future coding agents are first-class readers
here. Module docstrings explain *why* a decision was made, not just what the
code does; the example experiment's YAML carries its reasoning in comments. An
agent should be able to enter this repository cold, read the docs, understand
the architecture, add an experiment, run the tests, and maintain the framework
without asking the owner anything.

## Non-goals

- **Not a SaaS.** No hosting, no accounts, no service to run. It is a library
  and a CLI on one machine.
- **Not a benchmark harness.** It is for asking specific behavioural questions,
  not for scoring models against a leaderboard.
- **Not an agent framework.** It does not compete with LangChain, CrewAI, or
  AutoGen. It does not orchestrate agents to do work; it studies what they do.
- **Not a statistics package.** It computes the minimum honest summary and hands
  you a CSV. Serious analysis happens in R or pandas, on data it preserved.
- **Not a web app.** There is no UI beyond the `ashe-lab` CLI and Markdown
  reports.

## Who this is for

Primarily one researcher, running his own experiments on his own machine. The
design does not compromise for hypothetical users.

Secondarily, the AI coding agents he will use to extend it. The intended
workflow: describe an experiment idea to an agent; the agent adds it using the
existing framework; the owner reviews the design before anything runs; the
experiment executes; the lab preserves the raw evidence; structured results and
a readable report come out the other end; and the same experiment can be re-run
against a future model. Every step is a reason the repo must be legible without
a conversation attached.

Incidentally, anyone with the same problem. The repository is open source
because the cost of that is zero and the alternative is a private directory that
rots — not because it is chasing an audience.

## What success looks like

**In one year.** Several real experiments exist beyond the framework-validation
example, `fact-check-awareness-001`, each with preserved runs against real
hosted models. The owner has gone from idea to running experiment, via a coding
agent, without writing framework code — the spec format was expressive enough.
At least one result has been written up for an outside reader who could check it
against the stored evidence. Checksum verification passes on every run ever
recorded, and no run has been silently overwritten or lost.

**In five years.** A run recorded in 2026 is still readable with no Ashe Agent
Lab installed: the JSONL opens, the checksums verify with `sha256sum -c`, and
the snapshotted spec says exactly what was asked. A 2026 experiment re-runs
unmodified against a 2031 model — changing only a model identifier — and the two
runs are directly comparable, making genuine longitudinal claims about model
behaviour possible. The codebase has been maintained largely by coding agents
that were never briefed by the owner, and it survived the disappearance of at
least one provider without any stored evidence becoming unreadable.

The falsifiable version: if in 2031 an old run cannot be read, or an old
experiment cannot be re-run, or a stranger cannot tell what was actually asked,
the project failed — no matter how good the code looks.

## On AI tooling

This framework was built with the help of an AI coding agent, and it is intended
to be extended the same way. That is a deliberate part of the design.

It is also, deliberately, not a dependency. No agent platform, no vendor SDK,
and no proprietary tool sits anywhere in the runtime path. The framework
installs with Python and PyYAML, runs offline against a built-in fixture model,
and produces artifacts in formats that outlive every tool involved in making
them. If every AI product used to build this disappeared tomorrow, the
repository would still install, still run, and still be maintainable by whoever
opened it next.

Built with agents. Dependent on none.

---

See `ARCHITECTURE.md` for how the pieces fit together, `ROADMAP.md` for what
comes after Phase 0, and `experiments/fact-check-awareness-001/` for a worked
example.
