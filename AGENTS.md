# AGENTS.md — instructions for coding agents

You are working on **Ashe Agent Lab**, a framework for running reproducible AI
agent experiments. This file is written for you specifically. Read it before
changing anything. It should be enough to work productively here without asking
the repository owner any questions.

If anything in this file is wrong or out of date, fixing it is a legitimate and
welcome change.

---

## 1. Orient yourself in five minutes

Read these, in this order:

| File | What it tells you |
|---|---|
| `VISION.md` | Why the project exists and what it refuses to become |
| `ARCHITECTURE.md` | How the pieces fit, and *why* each decision was made |
| `docs/EXPERIMENT_SPEC.md` | Every key an `experiment.yaml` may contain |
| `docs/RUN_FORMAT.md` | Exactly what a stored run directory contains |
| `experiments/fact-check-awareness-001/experiment.yaml` | A complete, heavily commented worked example |
| `ROADMAP.md` | What is planned, and what is deliberately unscheduled |

Then run this. It takes about ten seconds, needs no credentials, and touches no
network:

```bash
export PYTHONPATH=src           # or: pip install -e ".[dev]"
python -m pytest                # the whole suite must pass before you start
python -m ashe_lab validate --all
python -m ashe_lab run fact-check-awareness-001
python -m ashe_lab show fact-check-awareness-001
```

If the suite does not pass on a clean checkout, **stop and report that** rather
than building on a broken base.

### The mental model, in one paragraph

An *experiment* is a YAML file declaring conditions (the manipulation), items
(the stimuli), and models. A *trial* is one (condition, model, item,
repetition) tuple — the atomic unit of evidence. The runner takes the cross
product, calls each model through a *provider adapter*, scores each response
with *evaluators*, and writes everything into an immutable *run directory*
under `runs/<experiment-id>/<run-id>/`. Reports are derived from those stored
records and can be regenerated at any time. Nothing in the core knows any
vendor's name.

### Repository map

```
src/ashe_lab/
  spec.py           Experiment schema, loader, validator, templating  <- the contract
  runner.py         Execution engine: plan, retry, record
  storage.py        Immutable run directories, sealing, checksums     <- the guarantees
  metrics.py        Aggregation -> results.json, trials.csv
  report.py         Markdown report rendering
  pricing.py        Hand-maintained price table -> cost estimates
  ids.py            Run ids, timestamps, hashing
  errors.py         Exception hierarchy
  cli.py            argparse CLI (run/report/validate/verify/show/list)
  providers/
    base.py         Provider protocol + ModelRequest/ModelResponse    <- the boundary
    __init__.py     Provider registry
    echo.py         Offline deterministic fixture + fault injection
    openai_chat.py  OpenAI-compatible /chat/completions
    anthropic_messages.py  Anthropic /v1/messages
    _http.py        Minimal stdlib JSON-over-HTTPS helper
  evaluators/
    __init__.py     Evaluator registry + run_evaluators
    builtin.py      Deterministic metrics
experiments/<id>/experiment.yaml   Experiment definitions
runs/<id>/<run-id>/                Immutable evidence (gitignored)
tests/                             pytest suite
```

---

## 2. The rules that are not negotiable

Violating any of these is a serious regression even if the tests still pass.
They exist because this project's value is entirely in the trustworthiness of
its stored evidence.

1. **Never make raw run data mutable.** No overwrite flag, no `--force`, no
   "update the run in place". Re-running produces a *new* run directory. The
   single sanctioned exception is `storage.write_report()`, which regenerates
   the *derived* `report.md` and immediately rewrites checksums.
2. **Never drop a failed trial.** Failures are recorded with
   `status: "error"`, the prompt that was attempted, and every attempt's error.
   A missing row silently biases every aggregate — if one condition fails more
   often, dropping those rows makes it look *better*.
3. **Never turn an unknown into a zero.** No usage data means `null`, not `0`.
   An evaluator that cannot measure something returns `None` with a note. A
   model absent from the price table yields `cost_usd: null` and an explanation.
   Zero is a measurement; null is an absence. Conflating them is the most
   damaging bug you can introduce here.
4. **Never let a vendor into the core.** `spec.py`, `runner.py`, `storage.py`,
   `metrics.py`, and `report.py` must not import a provider module or contain a
   vendor name in logic. Add capability behind `providers/base.py`.
5. **Never add a runtime dependency without an explicit decision.** PyYAML is
   the only one, and a test (`test_package_declares_exactly_one_runtime_dependency`)
   enforces it. If you genuinely need another, say so in your summary and
   explain what it buys — do not quietly add it.
6. **Never commit a secret.** Credentials come from the environment or `.env`
   (gitignored). `describe()` on a provider reports credential *presence* as a
   boolean and never the value. `tests/test_repo_hygiene.py` scans for
   credential-shaped strings.
7. **Never compute a statistic the sample size does not support.** Phase 0 is
   descriptive statistics only, on purpose. Do not add p-values, confidence
   intervals, or effect sizes without the power discussion described in
   ROADMAP Phase 3.
8. **Never silently ignore an unknown key in a spec.** Unknown keys are
   rejected. A typo that was tolerated means an experiment that did not test
   what its author believed.
9. **Do not break old specs or old stored runs.** See §7.

---

## 3. How to add an experiment

This is the most common task you will be asked to do.

1. **Pick an id.** Lowercase, hyphenated, ending in a three-digit sequence:
   `sandbagging-under-observation-001`. It becomes a directory name, so it must
   match `[a-z0-9][a-z0-9._-]*[a-z0-9]`, 1–64 characters.

2. **Create the directory and two files.**

   ```bash
   mkdir -p experiments/my-experiment-001
   # experiment.yaml  - the definition
   # README.md        - what it is, why, and how to read its results
   ```

   Copy `experiments/fact-check-awareness-001/experiment.yaml` as your starting
   point. It is commented specifically to be copied. Full key reference:
   `docs/EXPERIMENT_SPEC.md`.

3. **Design the contrast properly.** This is the part that actually matters,
   and the part a careless agent gets wrong:

   - Put the **stimulus** in `items` and the **manipulation** in `conditions`.
     Then every condition sees identical stimuli and the condition is the only
     thing that varies. If you find yourself writing different questions into
     different conditions, stop — you have designed a confounded experiment.
   - Mark exactly one condition `is_control: true`. That is what enables the
     "Difference from control" comparison.
   - Keep the condition templates byte-identical except for the manipulation.
     Look at how the two conditions in `fact-check-awareness-001` differ only
     by the fact-checking announcement.
   - **Name the obvious confound and measure it.** If your manipulation might
     change response *length*, record length as a metric and normalise your
     headline measure per 100 words (see `builtin.hedging_markers`). An effect
     that is really a verbosity effect is the standard way this kind of
     experiment fools people.

4. **Default to the `echo` provider.** Commit the experiment with
   `provider: echo` active and real models commented out, so anyone can run it
   with no credentials and no spend. Note in the README that echo output is
   hash-derived and meaningless.

5. **Validate and dry-run before spending anything.**

   ```bash
   python -m ashe_lab validate my-experiment-001      # free, calls nothing
   python -m ashe_lab run my-experiment-001 --dry-run # prints every rendered prompt
   ```

   Read the dry-run output. Actually read it. Most design errors are visible
   in the rendered prompts and invisible in the YAML.

6. **Smoke-test a real model cheaply** before a full run:

   ```bash
   python -m ashe_lab run my-experiment-001 --model gpt-4o-mini --max-trials 2
   ```

7. **Add a test** asserting your experiment loads and that its conditions
   differ only by the manipulation. Copy the pattern from
   `test_shipped_experiment_conditions_differ_only_by_the_manipulation` in
   `tests/test_spec.py`.

8. **Tell the owner what you designed and why**, including the confound you
   identified and how you controlled for it. He reviews designs before they run.

---

## 4. How to add a model provider

The boundary is deliberately tiny: one class, one method.

1. **Create `src/ashe_lab/providers/my_provider.py`.** Use
   `openai_chat.py` as the template for an HTTP/JSON API; use
   `anthropic_messages.py` if the request shape differs structurally from chat
   completions.

   ```python
   from ..errors import ProviderError, ProviderNotConfigured
   from . import _http
   from .base import BaseProvider, ModelRequest, ModelResponse, Usage, http_timeout

   class MyProvider(BaseProvider):
       name = "my_provider"          # the string used in a spec's `provider:`

       def __init__(self, api_key=None, base_url=None, timeout=None):
           self._api_key = api_key or os.environ.get("MY_PROVIDER_API_KEY")
           ...

       def describe(self):
           # Goes into manifest.json. Credential PRESENCE only, never the value.
           return {
               "provider": self.name,
               "network": True,
               "credentials_required": True,
               "credentials_present": bool(self._api_key),
               "env_var": "MY_PROVIDER_API_KEY",
           }

       def complete(self, request: ModelRequest) -> ModelResponse:
           if not self._api_key:
               raise ProviderNotConfigured("MY_PROVIDER_API_KEY is not set", provider=self.name)
           body, headers = _http.post_json(url, payload, auth_headers, self._timeout, provider=self.name)
           return ModelResponse(text=..., model_reported=body.get("model"), usage=Usage(...), ...)
   ```

2. **Register it** — one line in `src/ashe_lab/providers/__init__.py`:

   ```python
   _REGISTRY = {
       ...
       MyProvider.name: MyProvider,
   }
   ```

3. **Obey the contract.** These are not stylistic preferences; the runner and
   the evidence format depend on them:

   - **Do not retry inside the adapter.** The runner owns retry policy so that
     every attempt is recorded as evidence. An adapter that retries internally
     hides failures.
   - **Set `retryable` correctly** on `ProviderError`. Rate limits, timeouts,
     transport errors and 5xx are retryable. A bad key or malformed request is
     not — retrying it is just a slower failure.
   - **Always populate `model_reported`** from the response when the API
     provides it. It is often a dated snapshot behind an alias, and it is the
     field that answers "was this the same model?" a year later.
   - **Report usage honestly.** Absent counts are `None`, never `0`.
   - **Never crash on an empty or unusual response.** An empty completion is
     data. Return `text=""` and record the oddity in `raw_metadata`.
   - **Never log or return a credential.**

4. **Document the credential** in `.env.example`. A test
   (`test_every_environment_variable_the_code_reads_is_documented`) enforces
   this.

5. **Add prices** to `PRICE_TABLE` in `src/ashe_lab/pricing.py`, with an
   `as_of` date. If you do not know them, leave them out — a null cost with an
   explanation is correct; a guessed price is not.

6. **Test it offline** by stubbing `ashe_lab.providers._http.post_json`. See
   `test_openai_adapter_translates_a_response` in `tests/test_providers.py`.
   **Never add a test that requires a real API key** — CI has no secrets and
   must stay that way.

---

## 5. How to add an evaluator

An evaluator is a pure function of a stored response. Purity is the
requirement: the same text must score identically in 2026 and 2036, because
stored evidence gets re-scored later.

1. Add a function to `src/ashe_lab/evaluators/builtin.py`:

   ```python
   def my_metric(context: EvaluationContext, params: Dict[str, Any]) -> EvaluationResult:
       """One line on what it measures, and one on what it does NOT measure."""
       if not_measurable_here:
           return EvaluationResult(value=None, note="why it could not be measured")
       return EvaluationResult(value=float(...), detail={"evidence": ...})
   ```

2. Register it in `BUILTIN_EVALUATORS` as `builtin.my_metric`.

3. Rules:
   - Return `None` with a `note` rather than a fabricated number.
   - Put the supporting evidence in `detail` (which keywords matched, what the
     regex caught) so a reader can audit the score instead of trusting it.
   - If your metric could be confounded by response length, offer
     `normalise: per_100_words` the way `keyword_count` does.
   - Be honest in the docstring and in `note` about what a crude heuristic is.
     `builtin.refusal_marker` says it is "not a trained refusal classifier",
     because it is not.
   - No network calls, no randomness, no clock reads.

4. Add tests, including a determinism check and the null case.

The `llm_judge.*`, `human.*`, and `script.*` namespaces are reserved and
intentionally unused — see ROADMAP.

---

## 6. Commands you will need

```bash
# Setup (either works)
export PYTHONPATH=src
pip install -e ".[dev]"

# Run the tests
python -m pytest                    # full suite, offline, no credentials
python -m pytest -q tests/test_spec.py
python -m pytest -k "immutab or checksum"
make test

# Validate definitions without spending anything
python -m ashe_lab validate --all
python -m ashe_lab validate my-experiment-001

# See exactly what would be sent, call nothing, write nothing
python -m ashe_lab run my-experiment-001 --dry-run

# Execute
python -m ashe_lab run fact-check-awareness-001
python -m ashe_lab run my-experiment-001 --trials 5 --model gpt-4o-mini
python -m ashe_lab run my-experiment-001 --condition control --item q-everest-height
python -m ashe_lab run my-experiment-001 --max-trials 2       # cheap smoke test
python -m ashe_lab run my-experiment-001 --delay 1.0          # stay under a rate limit
python -m ashe_lab run my-experiment-001 --json               # machine-readable summary

# Inspect stored evidence
python -m ashe_lab list runs
python -m ashe_lab show my-experiment-001                     # latest run
python -m ashe_lab show runs/my-experiment-001/<run-id> --json
python -m ashe_lab verify my-experiment-001                   # checksum integrity

# Generate a report (regenerates from raw records; safe on old runs)
python -m ashe_lab report my-experiment-001
python -m ashe_lab report runs/my-experiment-001/<run-id> --stdout

# Discover what is available
python -m ashe_lab list providers
python -m ashe_lab list evaluators
python -m ashe_lab list pricing

# Make targets
make help          # all targets
make check         # validate + test
make demo          # run the example experiment and show results
```

**Exit codes**, so you can branch on them in scripts:
`0` success · `1` framework error · `2` usage error · `3` run completed but
some trials failed · `4` integrity verification found drift.

### Reading raw evidence directly

```bash
RUN=runs/fact-check-awareness-001/<run-id>

cat $RUN/manifest.json | python -m json.tool | head -40
head -1 $RUN/trials.jsonl | python -m json.tool     # one full trial record
python -c "import json,sys; [print(json.loads(l)['trial_key'], json.loads(l)['status']) for l in open('$RUN/trials.jsonl')]"
sha256sum -c $RUN/checksums.sha256                  # verify without this framework
```

`trials.jsonl` is the primary evidence. `results.json`, `trials.csv`, and
`report.md` are all derived from it and can be rebuilt.

---

## 7. Changing the spec format without breaking history

Stored runs outlive the code that made them. Follow this:

- **Adding an optional key**: add it to the relevant dataclass and its
  `ALLOWED` tuple, give it a default, document it in
  `docs/EXPERIMENT_SPEC.md`. No version bump. Old specs keep working.
- **Adding a new experiment shape** (multi-agent, turn-based): add a new
  `kind` value such as `multi_agent.v0` to `SUPPORTED_KINDS` in `spec.py` and
  branch the parser on it. Do not widen `single_agent.v0` to mean something
  else — that would retroactively change what old stored runs claim to be.
- **Breaking a key's meaning**: bump `CURRENT_SCHEMA_VERSION` and keep the
  old path working. Every spec declares `schema_version`, and every stored run
  records the version it ran under.
- **Changing a record format**: the `schema` field (`trial.v0`,
  `manifest.v0`, `results.v0`) exists so readers can branch. Bump to `.v1`
  rather than silently changing the shape of `.v0`.
- **Never change what an evaluator computes under the same id.** Add a new
  evaluator instead. Otherwise two runs' numbers are silently incomparable.

---

## 8. Style and conventions

- Python 3.9+ (CI tests 3.9 and 3.13). No `X | Y` unions in evaluated
  annotations, no `match`, no `:=` where clarity suffers. `from __future__
  import annotations` at the top of every module.
- Standard library first. `urllib.request` over an HTTP client library.
- Comments explain **why**, not what. The code says what. Existing comments are
  written to teach the next reader the reasoning behind a decision — match that
  register, and do not strip them.
- Every public function gets a docstring. Where a design choice is
  non-obvious, say what the alternative was and why it lost.
- Errors are actionable: name the bad value, name the valid options, name the
  file to edit. Error messages are read by agents as often as by humans.
- `dataclass(frozen=True)` for spec objects. They are a parsed contract, not
  mutable state.
- Tests are documentation. Give them descriptive names and a docstring when
  the test encodes a decision rather than a mechanism.

---

## 9. Before you hand work back

```bash
python -m pytest                        # must be green
python -m ashe_lab validate --all       # must be green
python -m ashe_lab run fact-check-awareness-001 --trials 1   # must work offline
python -m ashe_lab verify fact-check-awareness-001           # must verify clean
git status                              # no .env, no credentials, no runs/ output
```

Then in your summary, state plainly:

- what you changed and why;
- any of §2's rules you came close to bending, and how you avoided it;
- any new dependency (and its justification), or "none";
- what you did **not** do, and what you would do next;
- anything you found broken or questionable that you did not fix.

Do not claim a test passes without having run it. Do not report a
scientific finding from a run that used the `echo` provider — its output is
hash-derived text and carries no information about model behaviour.

---

## 10. Things that look like bugs and are not

- **`echo` responses are nonsense.** Intended. It is a deterministic fixture,
  not a model.
- **No parallel execution.** Deliberate: sequential runs make the event log a
  true chronology and remove the largest source of irreproducible bugs.
  ROADMAP has parallelism as a later phase.
- **Run directories are read-only after a run.** Deliberate. Use
  `storage.unseal_run()` if you genuinely must, and understand that
  `verify` will then correctly report drift if you change anything.
- **`stdev` is `null` for n=1.** Correct. The spread of one observation is
  unknown, not zero.
- **Costs appear as `≥ $X`.** That is a floor, shown when some trials could
  not be priced.
- **Percent change is `—` when the control mean is 0.** Division by zero is
  reported as undefined rather than fabricated.
- **`runs/` is gitignored.** By design. To publish a run as evidence:
  `git add -f runs/<experiment-id>/<run-id>`.
- **The sealed-file test skips on some filesystems.** Container overlay
  filesystems do not enforce the read-only bit for the owner. Checksums still
  detect tampering.
