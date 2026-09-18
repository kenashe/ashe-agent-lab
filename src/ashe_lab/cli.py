"""Command-line interface.

Commands
--------
``run``        Execute an experiment, writing a new immutable run directory.
``report``     Regenerate a Markdown report from a run's preserved records.
``validate``   Check an experiment definition without calling any model.
``verify``     Recompute a run's checksums and report any drift.
``show``       Print a summary of a stored run.
``list``       List experiments, runs, providers, evaluators, or the price table.

Design notes
------------
``argparse`` from the standard library, not Click or Typer. The CLI has six
subcommands and no interactive prompts; a dependency would buy nicer help text
and cost portability.

Exit codes are meaningful, so this composes in shell scripts and CI:

==== ===========================================================
   0 success
   1 a deliberate framework error (bad spec, missing run, ...)
   2 argparse usage error (argparse's own convention)
   3 the run completed but some trials failed
   4 integrity verification found drift
==== ===========================================================
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

from . import __version__
from .errors import AsheLabError
from .evaluators import available_evaluators, validate_evaluator_types
from .pricing import priced_models
from .providers import available_providers
from .spec import discover_experiments, load_spec

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_TRIALS_FAILED = 3
EXIT_INTEGRITY_FAILED = 4


# ---------------------------------------------------------------------------
# .env loading
# ---------------------------------------------------------------------------


def load_dotenv(path: str = ".env") -> List[str]:
    """Load ``KEY=value`` pairs from a ``.env`` file into ``os.environ``.

    Hand-rolled rather than depending on ``python-dotenv``, because it is
    fifteen lines and the framework's dependency budget is one package.

    Existing environment variables always win: an explicitly exported variable
    should never be silently overridden by a stale file. Returns the names (not
    values) of the variables it set, so the CLI can report what it picked up
    without ever printing a secret.
    """
    if not os.path.isfile(path):
        return []
    loaded: List[str] = []
    with open(path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key.startswith("export "):
                key = key[len("export "):].strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value
                loaded.append(key)
    return loaded


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ashe-lab",
        description=(
            "Ashe Agent Lab - run reproducible AI agent experiments and preserve "
            "the evidence."
        ),
        epilog=(
            "Try:  ashe-lab run fact-check-awareness-001 --model echo-fixture\n"
            "Docs: README.md for a tour, AGENTS.md if you are a coding agent."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version="ashe-lab " + __version__)
    parser.add_argument(
        "--no-dotenv",
        action="store_true",
        help="do not read a .env file from the current directory",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    # -- run --------------------------------------------------------------
    run_parser = subparsers.add_parser(
        "run",
        help="execute an experiment",
        description=(
            "Execute an experiment and write a new immutable run directory. "
            "Existing runs are never modified."
        ),
    )
    run_parser.add_argument(
        "experiment",
        help="experiment id (resolved as experiments/<id>/experiment.yaml), "
        "a directory, or a path to a YAML file",
    )
    run_parser.add_argument(
        "--trials", type=int, default=None, help="override trials per cell"
    )
    run_parser.add_argument(
        "--condition",
        action="append",
        default=[],
        metavar="ID",
        help="run only this condition (repeatable)",
    )
    run_parser.add_argument(
        "--model",
        action="append",
        default=[],
        metavar="ALIAS",
        help="run only this model alias (repeatable)",
    )
    run_parser.add_argument(
        "--item",
        action="append",
        default=[],
        metavar="ID",
        help="run only this item (repeatable)",
    )
    run_parser.add_argument(
        "--max-trials",
        type=int,
        default=None,
        help="hard cap on trials executed; a cheap way to smoke-test a paid provider",
    )
    run_parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="sleep between trials, to stay under a rate limit",
    )
    run_parser.add_argument(
        "--runs-dir", default=None, help="where to write run directories (default ./runs)"
    )
    run_parser.add_argument(
        "--label", default=None, help="free-text label recorded in the manifest"
    )
    run_parser.add_argument(
        "--no-seal",
        action="store_true",
        help="leave the run directory writable (not recommended; breaks the "
        "immutability guarantee)",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="render every prompt and print the plan without calling any model "
        "or writing anything",
    )
    run_parser.add_argument(
        "--quiet", action="store_true", help="suppress per-trial progress output"
    )
    run_parser.add_argument(
        "--json", action="store_true", help="print a machine-readable summary"
    )

    # -- report -----------------------------------------------------------
    report_parser = subparsers.add_parser(
        "report",
        help="regenerate a run's Markdown report",
        description=(
            "Rebuild report.md from a run's preserved trials.jsonl. Raw records are "
            "never modified; checksums are refreshed."
        ),
    )
    report_parser.add_argument(
        "target",
        help="a run directory, or an experiment id to use its most recent run",
    )
    report_parser.add_argument(
        "--stdout",
        action="store_true",
        help="print the report instead of writing it into the run directory",
    )
    report_parser.add_argument(
        "--samples",
        type=int,
        default=1,
        help="sample transcripts to include per condition (default 1)",
    )
    report_parser.add_argument("--runs-dir", default=None)

    # -- validate ---------------------------------------------------------
    validate_parser = subparsers.add_parser(
        "validate",
        help="check an experiment definition",
        description=(
            "Parse and validate an experiment, resolve its providers and "
            "evaluators, and render every prompt. Calls no model and costs nothing."
        ),
    )
    validate_parser.add_argument("experiment", nargs="?", default=None)
    validate_parser.add_argument(
        "--all", action="store_true", help="validate every experiment in experiments/"
    )

    # -- verify -----------------------------------------------------------
    verify_parser = subparsers.add_parser(
        "verify",
        help="check a stored run's integrity",
        description="Recompute SHA-256 checksums for a run and report any drift.",
    )
    verify_parser.add_argument("target", help="a run directory, or an experiment id")
    verify_parser.add_argument("--runs-dir", default=None)
    verify_parser.add_argument("--json", action="store_true")

    # -- show -------------------------------------------------------------
    show_parser = subparsers.add_parser(
        "show", help="summarise a stored run", description="Print a summary of one run."
    )
    show_parser.add_argument("target", help="a run directory, or an experiment id")
    show_parser.add_argument("--runs-dir", default=None)
    show_parser.add_argument(
        "--json", action="store_true", help="print results.json verbatim"
    )

    # -- list -------------------------------------------------------------
    list_parser = subparsers.add_parser(
        "list", help="list experiments, runs, providers, evaluators, or pricing"
    )
    list_parser.add_argument(
        "what",
        choices=["experiments", "runs", "providers", "evaluators", "pricing"],
    )
    list_parser.add_argument(
        "--experiment", default=None, help="filter runs by experiment id"
    )
    list_parser.add_argument("--runs-dir", default=None)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return EXIT_OK

    if not args.no_dotenv:
        load_dotenv()

    try:
        handler = {
            "run": _cmd_run,
            "report": _cmd_report,
            "validate": _cmd_validate,
            "verify": _cmd_verify,
            "show": _cmd_show,
            "list": _cmd_list,
        }[args.command]
        return handler(args)
    except AsheLabError as exc:
        # A deliberate framework error. Print it plainly: these messages are
        # written to be actionable, including by a coding agent reading stderr.
        print("error: {0}".format(exc), file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\ninterrupted; any partial run directory has been left in place", file=sys.stderr)
        return EXIT_ERROR


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _cmd_run(args: argparse.Namespace) -> int:
    from .runner import RunOptions, execute, plan_run

    spec = load_spec(args.experiment)
    options = RunOptions(
        trials_override=args.trials,
        only_conditions=args.condition,
        only_models=args.model,
        only_items=args.item,
        max_trials=args.max_trials,
        runs_root=args.runs_dir,
        seal=not args.no_seal,
        delay_seconds=args.delay,
        label=args.label,
    )

    if args.dry_run:
        return _dry_run(spec, options)

    printer = (lambda _m: None) if args.quiet else (lambda m: print(m))
    result = execute(spec, options, on_progress=printer)

    if args.json:
        print(
            json.dumps(
                {
                    "run_id": result.run_id,
                    "run_path": result.run_path,
                    "experiment_id": result.experiment_id,
                    "trials": result.trial_count,
                    "ok": result.ok_count,
                    "errors": result.error_count,
                    "report": result.report_path,
                },
                indent=2,
            )
        )
    else:
        print("")
        print("report:   {0}".format(result.report_path))
        print("evidence: {0}".format(os.path.join(result.run_path, "trials.jsonl")))
        if result.error_count:
            print(
                "\n{0} trial(s) failed. They are preserved with status=error; "
                "see the Failures section of the report.".format(result.error_count),
                file=sys.stderr,
            )

    return EXIT_TRIALS_FAILED if result.error_count else EXIT_OK


def _dry_run(spec: Any, options: Any) -> int:
    from .runner import plan_run

    planned = plan_run(spec, options)
    print("experiment: {0} ({1})".format(spec.id, spec.title))
    print("spec hash:  {0}".format(spec.spec_hash))
    print("planned trials: {0}".format(len(planned)))
    print("")
    seen_cells = set()
    for trial in planned:
        cell = (trial.condition.id, trial.model.alias, trial.item.id)
        if cell in seen_cells:
            continue
        seen_cells.add(cell)
        print("─" * 72)
        print(
            "condition={0}  model={1} ({2}:{3})  item={4}".format(
                trial.condition.id,
                trial.model.alias,
                trial.model.provider,
                trial.model.model,
                trial.item.id,
            )
        )
        if trial.system_prompt:
            print("\n[SYSTEM]\n{0}".format(trial.system_prompt))
        print("\n[USER]\n{0}".format(trial.user_prompt))
        print("")
    print("─" * 72)
    print("dry run: nothing was called and nothing was written.")
    return EXIT_OK


def _cmd_report(args: argparse.Namespace) -> int:
    from . import report as report_module
    from .storage import load_manifest, load_results, load_trials, resolve_run_path, write_report

    run_path = resolve_run_path(args.target, args.runs_dir)
    manifest = load_manifest(run_path)
    results = load_results(run_path)
    trials = load_trials(run_path)

    markdown = report_module.render_report(
        manifest=manifest,
        results=results,
        trials=trials,
        transcript_samples=args.samples,
    )

    if args.stdout:
        print(markdown)
        return EXIT_OK

    path = write_report(run_path, markdown)
    print("wrote {0}".format(path))
    return EXIT_OK


def _cmd_validate(args: argparse.Namespace) -> int:
    targets: List[str]
    if args.all or not args.experiment:
        targets = discover_experiments()
        if not targets:
            print("no experiments found under experiments/", file=sys.stderr)
            return EXIT_ERROR
    else:
        targets = [args.experiment]

    failures = 0
    for target in targets:
        try:
            spec = load_spec(target)
        except AsheLabError as exc:
            failures += 1
            print("FAIL {0}\n{1}\n".format(target, exc), file=sys.stderr)
            continue

        problems = validate_evaluator_types(spec.evaluators)
        for model in spec.models:
            if model.provider not in available_providers():
                problems.append(
                    "model {0!r} names unknown provider {1!r}; registered: {2}".format(
                        model.alias, model.provider, ", ".join(available_providers())
                    )
                )

        if problems:
            failures += 1
            print("FAIL {0}".format(spec.id), file=sys.stderr)
            for problem in problems:
                print("  - {0}".format(problem), file=sys.stderr)
            print("", file=sys.stderr)
            continue

        print(
            "OK   {0}: {1} condition(s) x {2} model(s) x {3} item(s) x {4} trial(s) "
            "= {5} trials, {6} evaluator(s)".format(
                spec.id,
                len(spec.conditions),
                len(spec.models),
                len(spec.items),
                spec.defaults.trials,
                spec.planned_trial_count(),
                len(spec.evaluators),
            )
        )

    return EXIT_ERROR if failures else EXIT_OK


def _cmd_verify(args: argparse.Namespace) -> int:
    from .storage import resolve_run_path, verify_run

    run_path = resolve_run_path(args.target, args.runs_dir)
    report = verify_run(run_path)

    if args.json:
        print(json.dumps(report, indent=2))
    elif report["ok"]:
        print("OK  {0}: {1} file(s) match their recorded checksums".format(
            run_path, len(report["verified"])
        ))
    else:
        print("DRIFT DETECTED in {0}".format(run_path), file=sys.stderr)
        for label in ("modified", "missing", "unexpected"):
            for entry in report[label]:
                print("  {0}: {1}".format(label, entry), file=sys.stderr)
        print(
            "\nThis run's contents no longer match what was recorded when it "
            "finished. Treat its results as untrusted.",
            file=sys.stderr,
        )

    return EXIT_OK if report["ok"] else EXIT_INTEGRITY_FAILED


def _cmd_show(args: argparse.Namespace) -> int:
    from .storage import load_manifest, load_results, resolve_run_path

    run_path = resolve_run_path(args.target, args.runs_dir)
    results = load_results(run_path)

    if args.json:
        print(json.dumps(results, indent=2))
        return EXIT_OK

    manifest = load_manifest(run_path)
    counts = manifest.get("counts", {})
    timing = manifest.get("timing", {})
    cost = results.get("cost", {})

    print("run:        {0}".format(manifest.get("run_id")))
    print("experiment: {0} ({1})".format(manifest.get("experiment_id"), manifest.get("experiment_title")))
    print("started:    {0}".format(timing.get("started_at")))
    print("trials:     {0} ok, {1} error".format(counts.get("ok"), counts.get("error")))
    print("cost:       ~${0} ({1} unpriced)".format(
        cost.get("estimated_usd"), cost.get("trials_unpriced")
    ))
    print("path:       {0}".format(run_path))
    print("")

    group_by = results.get("group_by", [])
    for group in results.get("groups", []):
        labels = group.get("labels", {})
        label_text = " ".join("{0}={1}".format(k, labels.get(k)) for k in group_by)
        print("{0}  (n_ok={1}, n_error={2})".format(label_text, group.get("n_ok"), group.get("n_error")))
        for metric_id, stats in sorted((group.get("metrics") or {}).items()):
            mean = stats.get("mean")
            stdev = stats.get("stdev")
            print(
                "    {0:<28} mean={1} sd={2} n={3}".format(
                    metric_id,
                    "—" if mean is None else round(mean, 3),
                    "—" if stdev is None else round(stdev, 3),
                    stats.get("n"),
                )
            )
    return EXIT_OK


def _cmd_list(args: argparse.Namespace) -> int:
    from .storage import list_runs

    if args.what == "experiments":
        found = discover_experiments()
        if not found:
            print("no experiments found under experiments/")
            return EXIT_OK
        for experiment_id in found:
            try:
                spec = load_spec(experiment_id)
                print("{0:<34} {1}".format(experiment_id, spec.title))
            except AsheLabError as exc:
                print("{0:<34} <invalid: {1}>".format(experiment_id, str(exc).split("\n")[0]))
        return EXIT_OK

    if args.what == "runs":
        runs = list_runs(args.experiment, args.runs_dir)
        if not runs:
            print("no runs found")
            return EXIT_OK
        for run_path in runs:
            print(run_path)
        return EXIT_OK

    if args.what == "providers":
        print("registered providers (use in a model entry's `provider:` field):")
        for name in available_providers():
            print("  " + name)
        return EXIT_OK

    if args.what == "evaluators":
        print("registered evaluators (use in an evaluator's `type:` field):")
        for name in available_evaluators():
            print("  " + name)
        return EXIT_OK

    if args.what == "pricing":
        table = priced_models()
        print("price table ({0}):".format(table["unit"]))
        print("{0:<22} {1:<24} {2:>8} {3:>8}  {4}".format(
            "PROVIDER", "MODEL PREFIX", "INPUT", "OUTPUT", "AS OF"
        ))
        for entry in table["entries"]:
            print("{0:<22} {1:<24} {2:>8} {3:>8}  {4}".format(
                entry["provider"],
                entry["model_prefix"],
                entry["input_rate"],
                entry["output_rate"],
                entry["as_of"],
            ))
        print("\nThese are hand-entered list prices, not billing data. Verify before quoting.")
        return EXIT_OK

    return EXIT_ERROR  # pragma: no cover - argparse restricts choices


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
