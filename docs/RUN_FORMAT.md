# Run directory format

A run directory is the durable artifact of this project. Code gets rewritten;
these directories are the only thing that cannot be regenerated. This document
describes exactly what is in one, so that a reader with no framework installed
— a human, a coding agent, a stranger in ten years — can interpret it.

Implementation: `src/ashe_lab/storage.py`. If this document and that file
disagree, the file is right.

---

## Location and naming

```
runs/<experiment-id>/<run-id>/
```

A run id looks like `20260918T134502.318Z-9f3a1c7e`:

- `20260918T134502.318Z` — UTC start time, millisecond precision, fixed width.
- `9f3a1c7e` — 8 random hex characters.

Two properties are load-bearing. **Lexicographic order equals chronological
order**, so `sorted()` on run ids — or an alphabetical file browser — lists
runs oldest first, which is how `ashe-lab show <experiment-id>` resolves "the
latest run". And **no coordination is needed**: the random suffix means two
runs started in the same millisecond still get distinct directories without a
counter, lockfile, or database.

`runs/` is gitignored by default. Run output can be large and may contain model
text you have not reviewed. To publish a specific run as evidence:

```bash
git add -f runs/<experiment-id>/<run-id>
```

---

## Contents

| File | Format | Role |
|---|---|---|
| `trials.jsonl` | JSON Lines | **Primary evidence.** One object per trial. Everything else is derived from this. |
| `events.jsonl` | JSON Lines | Chronological lifecycle log. |
| `manifest.json` | JSON | What was run, from which spec, by which code, on what machine, when. |
| `experiment.snapshot.yaml` | YAML | The experiment definition as executed, byte for byte. |
| `results.json` | JSON | Aggregated statistics. |
| `trials.csv` | CSV | Flat export for spreadsheets, pandas, R. |
| `report.md` | Markdown | Human-readable report. |
| `checksums.sha256` | text | SHA-256 of every file above. |

**Derived vs primary.** `results.json`, `trials.csv`, and `report.md` can all
be rebuilt from `trials.jsonl`. `trials.jsonl`, `events.jsonl`,
`manifest.json`, and `experiment.snapshot.yaml` cannot be rebuilt from
anything. If you ever have to choose what to keep, keep those four.

---

## Immutability

A run directory is written once. `RunWriter` refuses to open a directory that
already exists — there is no overwrite flag and no `--force`. Re-running an
experiment produces a *new* run.

On completion, files are chmod'd `0444` and directories `0555`. This is a
**guardrail against accident, not a security control**: anyone with the account
can call `storage.unseal_run()`. It exists to stop a stray script, a careless
shell redirect, or a confused coding agent from clobbering a record. Some
filesystems (container overlays, some network mounts, anything running as root)
do not enforce the bits at all — which is why checksums, not permissions, are
the actual integrity mechanism.

**The one sanctioned mutation** is `ashe-lab report`, which regenerates the
derived `report.md`, rewrites `checksums.sha256`, and re-seals. Raw records are
never touched. This exists so an improved report template can be applied to
runs recorded years earlier.

---

## Verifying integrity

With the framework:

```bash
ashe-lab verify runs/<experiment-id>/<run-id>
```

Exit code `0` means clean, `4` means drift. The report distinguishes
`modified`, `missing`, and `unexpected` files.

Without the framework — `checksums.sha256` is written in `sha256sum` format
precisely so that a stranger with coreutils can check a run on a machine with
no Python at all:

```bash
cd runs/<experiment-id>/<run-id> && sha256sum -c checksums.sha256
```

The checksum file covers every file in the run except itself.

---

## `trials.jsonl`

One JSON object per line. A **trial** is one `(condition, model, item,
repetition)` tuple — the atomic unit of evidence.

Records are appended and flushed as each trial completes, so an interrupted
run keeps every trial it already paid for. Only the final line of a killed run
can be truncated, and the loader tolerates that.

```json
{
  "schema": "trial.v0",
  "trial_key": "control|echo-fixture|q-everest-height|000",
  "trial_index_in_run": 0,
  "recorded_at": "2026-09-18T13:48:51.601234Z",

  "experiment_id": "fact-check-awareness-001",
  "spec_hash": "011a9aaef26a634b...",

  "condition_id": "control",
  "condition_is_control": true,
  "model_alias": "echo-fixture",
  "provider": "echo",
  "model_requested": "echo-deterministic-v1",
  "model_reported": "echo-deterministic-v1",
  "item_id": "q-everest-height",
  "repetition": 0,

  "prompt": {
    "system": "You are a knowledgeable assistant answering factual questions.",
    "user": "What is the official height of Mount Everest...?"
  },

  "request": {
    "model": "echo-deterministic-v1",
    "messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}],
    "temperature": 1.0,
    "top_p": null,
    "max_output_tokens": 500,
    "seed": null,
    "options": {"sentences": 4}
  },
  "request_hash": "4b1d...",

  "status": "ok",

  "response": {
    "text": "Arguably the evidence suggests...",
    "model_reported": "echo-deterministic-v1",
    "finish_reason": "stop",
    "usage": {
      "input_tokens": 133,
      "output_tokens": 170,
      "total_tokens": 303,
      "extra": {"token_counts_are": "estimated-4-chars-per-token"}
    },
    "provider_request_id": "echo-8f2c...",
    "raw_metadata": {"deterministic": true, "request_digest": "..."}
  },

  "error": null,

  "attempts": [
    {"attempt": 1, "started_at": "2026-09-18T13:48:51.601Z",
     "duration_seconds": 0.000412, "outcome": "ok"}
  ],
  "attempt_count": 1,
  "duration_seconds": 0.000412,

  "cost": {
    "cost_usd": 0.0,
    "cost_note": "estimated from list prices as of 2026-09-18",
    "rate_as_of": "2026-09-18",
    "input_rate_per_mtok": 0.0,
    "output_rate_per_mtok": 0.0
  },

  "scores": {
    "hedging_per_100w": {
      "value": 27.39,
      "note": null,
      "detail": {"matches": {"arguably": 1}, "raw_count": 1,
                 "word_count": 38, "normalise": "per_100_words"},
      "evaluator_type": "builtin.hedging_markers"
    }
  }
}
```

### Field notes

| Field | Notes |
|---|---|
| `schema` | `trial.v0`. Bumped to `.v1` rather than silently changing `.v0`'s shape, so a reader can branch. |
| `trial_key` | `condition\|model_alias\|item_id\|repetition` (zero-padded). Unique within a run. |
| `spec_hash` | Content hash of the semantic spec. Identical hash ⇒ identical design. Comment and whitespace edits do not change it; a changed prompt does. |
| `model_requested` vs `model_reported` | What you asked for vs what the provider says served the request. Often a dated snapshot behind an alias. This is the field that answers "was this the same model?" a year later. `null` on failure. |
| `prompt` | The rendered prompts, for reading. |
| `request` | The literal request object handed to the adapter — not a reconstruction. `request_hash` is its stable content hash. |
| `status` | `"ok"` or `"error"`. |
| `response` | `null` when `status` is `"error"`. |
| `usage` | As reported by the provider. Missing counts are `null`, **never `0`** — an absence and a measurement of zero are different facts. |
| `error` | `null` on success. Otherwise `type`, `message`, `retryable`, `status_code`, `provider`; plus `unexpected: true` and a `traceback` for an unhandled adapter bug. |
| `attempts` | **Every** attempt, including ones that failed before a later success. Each has its own timing and error. |
| `cost` | An estimate from the dated table in `pricing.py`. `cost_usd: null` with a `cost_note` when the model is unpriced or usage is missing. Not billing data. |
| `scores` | `{evaluator_id: {value, note, detail, evaluator_type}}`. A `value` of `null` means not measurable, with `note` saying why. `detail.failed: true` means the evaluator itself raised. |

### Two invariants worth stating explicitly

**Failed trials are recorded, not dropped.** A trial that never succeeded is
written with `status: "error"` and the prompt that was attempted. Omitting it
would silently bias every aggregate: if one condition times out more often,
dropping those rows makes it look *better*, not worse.

**A failed trial has `"scores": {}`, not zeros.** A zero would be
indistinguishable from a genuine measurement of zero.

### Reading it

```bash
RUN=runs/fact-check-awareness-001/<run-id>

head -1 $RUN/trials.jsonl | python -m json.tool          # one full record
jq -r '.trial_key + " " + .status' $RUN/trials.jsonl     # if you have jq
jq 'select(.status=="error") | .error.message' $RUN/trials.jsonl
```

```python
import json
trials = [json.loads(line) for line in open(f"{RUN}/trials.jsonl") if line.strip()]
ok = [t for t in trials if t["status"] == "ok"]
print(len(ok), "of", len(trials), "succeeded")
```

```python
import pandas as pd
df = pd.read_json(f"{RUN}/trials.jsonl", lines=True)   # or pd.read_csv(trials.csv)
```

---

## `events.jsonl`

A chronological narrative of the run. Useful for diagnosing *when* something
went wrong, as opposed to *what*.

```json
{"ts": "2026-09-18T13:48:51.594Z", "event": "run_started", "data": {"run_id": "...", "planned_trials": 30, "spec_hash": "...", "options": {...}}}
{"ts": "2026-09-18T13:48:51.601Z", "event": "trial_started", "data": {"trial_key": "...", "index": 0, "model": "echo-fixture"}}
{"ts": "2026-09-18T13:48:52.110Z", "event": "trial_retry", "data": {"trial_key": "...", "attempt": 1, "backoff_seconds": 1.0, "error": "..."}}
{"ts": "2026-09-18T13:48:51.602Z", "event": "trial_finished", "data": {"trial_key": "...", "status": "ok", "attempts": 1, "duration_seconds": 0.0004}}
{"ts": "2026-09-18T13:48:51.613Z", "event": "run_finalized", "data": {"trials_written": 30}}
```

Event types: `run_started`, `trial_started`, `trial_retry`, `trial_finished`,
`run_finalized`, and `run_aborted`.

**`run_aborted`** appears when a run raised before finishing. It records the
exception type, its message, and how many trials had been written. A crashed
run is evidence too, and the absence of `run_finalized` is how you know a run
is partial.

---

## `manifest.json`

Answers, without reading any other file: what was run, from which spec, by
which code, on what machine, when, with what outcome, and roughly what it cost.

```json
{
  "schema": "manifest.v0",
  "run_id": "20260918T134851.593Z-c8080d09",
  "experiment_id": "fact-check-awareness-001",
  "experiment_title": "Does announced fact-checking change...",
  "kind": "single_agent.v0",
  "schema_version": 0,
  "spec_hash": "011a9aaef26a634b...",
  "spec_source_path": "/abs/path/experiments/.../experiment.yaml",
  "research_question": "...",
  "hypothesis": "...",

  "framework": {
    "name": "ashe-agent-lab",
    "version": "0.1.0",
    "git_commit": "a1b2c3...",
    "git_dirty": false
  },

  "environment": {
    "python_version": "3.11.9",
    "python_implementation": "CPython",
    "platform": "Linux-6.1-x86_64",
    "machine": "x86_64",
    "hostname_hash": "9f2a...",
    "cwd_basename": "ashe-agent-lab"
  },

  "timing": {"started_at": "...Z", "finished_at": "...Z"},
  "counts": {"planned_trials": 30, "recorded_trials": 30, "ok": 30, "error": 0},
  "options": {"trials_override": null, "only_conditions": [], "max_trials": null,
              "delay_seconds": 0.0, "seal": true, "label": null},

  "providers": {
    "echo": {"provider": "echo", "kind": "offline-deterministic",
             "network": false, "credentials_required": false, "note": "..."}
  },

  "spec": { "...the full parsed spec..." },
  "cost_summary": { "...": "..." }
}
```

Three fields deserve attention:

- **`framework.git_dirty`.** `true` means the working tree had uncommitted
  changes, so `git_commit` does not fully describe the code that ran. Recording
  that honestly is more useful than implying otherwise. Reports surface it.
- **`options`.** Distinguishes a full run from a narrowed one (`--condition
  control --trials 1`), which matters when reading counts later.
- **`providers`.** Each adapter's `describe()` output. Credentials appear as a
  `credentials_present` boolean. **A key is never recorded here**, and a test
  enforces that.

`environment.hostname_hash` is a hash rather than the hostname, and no
environment variables are captured at all, so the manifest cannot leak a
machine identity or a secret.

---

## `experiment.snapshot.yaml`

The experiment definition **as executed**, copied byte for byte — comments and
formatting included.

A byte copy rather than a re-serialisation of the parsed spec, because comments
carry the author's reasoning and a YAML dumper would silently discard them.
This is what lets you read, years later, not just what the experiment did but
why its author thought it was the right design.

If someone edits `experiments/<id>/experiment.yaml` next week, this file still
shows what actually ran.

---

## `results.json`

Aggregated statistics. Regenerable from `trials.jsonl`.

```json
{
  "schema": "results.v0",
  "run_id": "...", "experiment_id": "...", "spec_hash": "...",
  "group_by": ["condition", "model"],
  "evaluators": [ {"id": "...", "type": "...", "description": "..."} ],

  "totals": {"trials": 30, "ok": 30, "error": 0, "attempts": 30, "retried_trials": 0},
  "duration": {"n": 30, "mean": 0.0004, "median": ..., "min": ..., "max": ..., "stdev": ..., "sum": ...},
  "tokens": {"input_tokens_total": 1995, "output_tokens_total": 2559,
             "trials_with_input_usage": 30, "trials_missing_usage": 0, "note": "..."},
  "cost": {"estimated_usd": 0.0, "is_floor": false, "trials_priced": 30,
           "trials_unpriced": 0, "rate_dates": ["2026-09-18"], "note": "..."},

  "groups": [
    {
      "labels": {"condition": "control", "model": "echo-fixture"},
      "key": "condition=control|model=echo-fixture",
      "n_trials": 15, "n_ok": 15, "n_error": 0,
      "metrics": {
        "hedging_per_100w": {
          "n": 15, "mean": 27.39, "median": 27.03, "min": 18.4, "max": 38.5,
          "stdev": 5.97, "sum": 410.9,
          "n_unmeasured": 0, "n_evaluator_errors": 0
        }
      },
      "tokens": {...}, "cost": {...}, "duration_seconds": {...}
    }
  ],

  "comparisons": [
    {
      "stratum": {"model": "echo-fixture"},
      "control_condition": "control",
      "treatment_condition": "fact-check-announced",
      "n_control_ok": 15, "n_treatment_ok": 15,
      "metrics": {
        "hedging_per_100w": {
          "control_mean": 27.39, "treatment_mean": 27.80,
          "absolute_change": 0.411, "percent_change": 1.5, "note": null
        }
      },
      "interpretation": "Descriptive difference of means only. With n=15 and n=15 this is a direction, not a result."
    }
  ],

  "errors": {"by_type": {}, "examples": []},
  "caveats": ["Descriptive statistics only...", "Costs are estimates...", "..."]
}
```

### How to read the statistics honestly

- **`n` vs `n_trials` vs `n_ok`.** `n` inside a metric is how many trials that
  metric was actually computed on. It can be lower than `n_ok` when some
  responses were unmeasurable.
- **`n_unmeasured`** counts successful trials where the evaluator returned
  `null` — for example `contains_expected` on an item with no ground truth.
  Those are excluded from the mean rather than counted as zero.
- **`n_evaluator_errors`** counts trials where the evaluator itself raised. A
  non-zero value here is a bug to fix, against data you still have.
- **`stdev` is `null` for n < 2.** The spread of one observation is unknown,
  not zero. Sample formula (n−1).
- **Every statistic is `null` for an empty group**, never `0`.
- **`cost.is_floor: true`** means some trials could not be priced, so
  `estimated_usd` is a lower bound. Reports render it as `≥ $X`.
- **`percent_change: null`** means the control mean was zero and the percentage
  is undefined — reported as undefined rather than fabricated.
- **No p-values, confidence intervals, or effect sizes.** Deliberate. A t-test
  on three trials per condition produces a number that looks like evidence and
  is not. See ARCHITECTURE §4.10 and ROADMAP Phase 3.

---

## `trials.csv`

One row per trial, for spreadsheets and statistics packages.

Fixed leading columns: `trial_key`, `experiment_id`, `run_condition`,
`model_alias`, `provider`, `model_requested`, `model_reported`, `item_id`,
`repetition`, `status`, `attempt_count`, `duration_seconds`, `input_tokens`,
`output_tokens`, `cost_usd`, `finish_reason`, `response_chars`, `error_type`,
`error_message` — then one `score_<evaluator_id>` column per evaluator.

Two deliberate choices:

- **The condition column is named `run_condition`**, not `condition`, because
  `condition` collides with reserved or awkward names in R and some statistics
  packages.
- **Response text is not included.** It contains newlines and commas, it can be
  very long, and `trials.jsonl` already holds it losslessly. The CSV is for
  statistics; the JSONL is for evidence.

Failed trials appear as rows with `status=error` and `null` in the measurement
columns.

---

## `report.md`

The human-readable summary: run metadata, research question, a run-health
section that warns when failures or small samples undermine the numbers,
results by group, difference from control, cost and token accounting, the exact
prompts as executed, sample transcripts, failures, caveats, and reproduction
commands.

Derived, and regenerable with `ashe-lab report <run>`. Two things it always
does: it states its own sample size and warns when groups have fewer than five
successful trials, and it warns in bold when a run used the offline `echo`
fixture — whose output is hash-derived text that carries no information about
model behaviour.

---

## Schema versioning

Every record type carries a `schema` field: `trial.v0`, `manifest.v0`,
`results.v0`. When a format changes incompatibly, the version is bumped to
`.v1` rather than the shape of `.v0` silently changing. A reader can branch on
it, and a stored run from 2026 keeps meaning exactly what it meant in 2026.

The same discipline applies to the spec: see `docs/EXPERIMENT_SPEC.md` and
AGENTS.md §7.
