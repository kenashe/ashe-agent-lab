"""Tests for the command-line interface.

These exercise the exact commands the README and AGENTS.md tell a reader to
run. A documented command that does not work is worse than an undocumented
one, so the documentation's promises are asserted here.
"""

from __future__ import annotations

import json
import os

import pytest

from ashe_lab.cli import EXIT_INTEGRITY_FAILED, EXIT_OK, EXIT_TRIALS_FAILED, load_dotenv, main
from ashe_lab.storage import unseal_run

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def in_repo(monkeypatch, tmp_path):
    """Run the CLI from the repository root, writing runs to a temp directory."""
    monkeypatch.chdir(REPO_ROOT)
    runs = tmp_path / "runs"
    runs.mkdir()
    yield str(runs)
    for directory, subdirs, filenames in os.walk(str(runs), topdown=True):
        for name in [directory] + [os.path.join(directory, s) for s in subdirs]:
            try:
                os.chmod(name, 0o755)
            except OSError:
                pass
        for filename in filenames:
            try:
                os.chmod(os.path.join(directory, filename), 0o644)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Basics
# ---------------------------------------------------------------------------


def test_no_arguments_prints_help_and_succeeds(capsys):
    assert main([]) == EXIT_OK
    assert "ashe-lab" in capsys.readouterr().out


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "ashe-lab" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_validate_accepts_the_shipped_experiment(in_repo, capsys):
    assert main(["validate", "fact-check-awareness-001"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "OK" in out
    assert "fact-check-awareness-001" in out


def test_validate_all_accepts_every_shipped_experiment(in_repo):
    """The repository must never contain an experiment that does not load."""
    assert main(["validate", "--all"]) == EXIT_OK


def test_validate_reports_a_bad_spec(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(str(tmp_path))
    path = tmp_path / "broken.yaml"
    path.write_text("id: broken\ntitle: Broken\nmodels: []\n", encoding="utf-8")
    assert main(["validate", str(path)]) != EXIT_OK
    assert "FAIL" in capsys.readouterr().err


def test_validate_catches_an_unknown_provider(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(str(tmp_path))
    path = tmp_path / "e.yaml"
    path.write_text(
        "id: bad-provider\n"
        "title: Bad provider\n"
        "models:\n"
        "  - alias: m\n"
        "    provider: telepathy\n"
        "    model: mind-reader\n"
        "items:\n"
        "  - id: i\n"
        "    vars: {q: hi}\n"
        "conditions:\n"
        "  - id: c\n"
        "    user_template: '{{q}}'\n",
        encoding="utf-8",
    )
    assert main(["validate", str(path)]) != EXIT_OK
    assert "telepathy" in capsys.readouterr().err


def test_validate_catches_an_unknown_evaluator(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(str(tmp_path))
    path = tmp_path / "e.yaml"
    path.write_text(
        "id: bad-evaluator\n"
        "title: Bad evaluator\n"
        "models:\n"
        "  - alias: m\n    provider: echo\n    model: echo-deterministic-v1\n"
        "items:\n  - id: i\n    vars: {q: hi}\n"
        "conditions:\n  - id: c\n    user_template: '{{q}}'\n"
        "evaluation:\n  evaluators:\n    - id: vibes\n      type: builtin.vibes\n",
        encoding="utf-8",
    )
    assert main(["validate", str(path)]) != EXIT_OK
    assert "builtin.vibes" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# dry run
# ---------------------------------------------------------------------------


def test_dry_run_prints_rendered_prompts_and_writes_nothing(in_repo, capsys):
    before = os.listdir(in_repo)
    assert main(["run", "fact-check-awareness-001", "--dry-run", "--runs-dir", in_repo]) == EXIT_OK
    out = capsys.readouterr().out
    assert "[USER]" in out
    assert "Mount Everest" in out
    assert "{{" not in out  # every placeholder was resolved
    assert "nothing was called and nothing was written" in out
    assert os.listdir(in_repo) == before


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def test_run_executes_the_example_experiment(in_repo, capsys):
    """This is the command the README promises works after a clone."""
    code = main(
        [
            "run",
            "fact-check-awareness-001",
            "--trials",
            "1",
            "--runs-dir",
            in_repo,
            "--quiet",
        ]
    )
    assert code == EXIT_OK

    runs = os.listdir(os.path.join(in_repo, "fact-check-awareness-001"))
    assert len(runs) == 1
    run_path = os.path.join(in_repo, "fact-check-awareness-001", runs[0])
    for filename in ("trials.jsonl", "report.md", "results.json", "checksums.sha256"):
        assert os.path.isfile(os.path.join(run_path, filename))


def test_run_json_output_is_machine_readable(in_repo, capsys):
    main(
        [
            "run",
            "fact-check-awareness-001",
            "--trials",
            "1",
            "--condition",
            "control",
            "--item",
            "q-everest-height",
            "--runs-dir",
            in_repo,
            "--quiet",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] == 1
    assert payload["errors"] == 0
    assert payload["run_id"]
    assert os.path.isfile(payload["report"])


def test_run_filters_narrow_execution(in_repo, capsys):
    main(
        [
            "run",
            "fact-check-awareness-001",
            "--condition",
            "control",
            "--item",
            "q-everest-height",
            "--trials",
            "2",
            "--runs-dir",
            in_repo,
            "--quiet",
            "--json",
        ]
    )
    assert json.loads(capsys.readouterr().out)["trials"] == 2


def test_max_trials_caps_a_run(in_repo, capsys):
    main(
        [
            "run",
            "fact-check-awareness-001",
            "--max-trials",
            "3",
            "--runs-dir",
            in_repo,
            "--quiet",
            "--json",
        ]
    )
    assert json.loads(capsys.readouterr().out)["trials"] == 3


def test_run_exit_code_signals_failed_trials(tmp_path, monkeypatch, capsys):
    """So CI and shell scripts can tell a clean run from a degraded one."""
    monkeypatch.chdir(str(tmp_path))
    path = tmp_path / "failing.yaml"
    path.write_text(
        "id: all-fail\n"
        "title: Everything fails\n"
        "defaults:\n  trials: 1\n  retries:\n    max_attempts: 1\n    initial_backoff_seconds: 0\n"
        "models:\n  - alias: m\n    provider: failing\n    model: x\n"
        "    options: {retryable: false}\n"
        "items:\n  - id: i\n    vars: {q: hi}\n"
        "conditions:\n  - id: c\n    user_template: '{{q}}'\n",
        encoding="utf-8",
    )
    code = main(["run", str(path), "--runs-dir", str(tmp_path / "runs"), "--quiet"])
    assert code == EXIT_TRIALS_FAILED


def test_run_reports_an_unknown_experiment_clearly(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(str(tmp_path))
    assert main(["run", "no-such-experiment"]) != EXIT_OK
    assert "no-such-experiment" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# report / show / verify
# ---------------------------------------------------------------------------


def _one_run(runs_dir):
    main(
        [
            "run",
            "fact-check-awareness-001",
            "--trials",
            "1",
            "--condition",
            "control",
            "--item",
            "q-everest-height",
            "--runs-dir",
            runs_dir,
            "--quiet",
        ]
    )
    runs = os.listdir(os.path.join(runs_dir, "fact-check-awareness-001"))
    return os.path.join(runs_dir, "fact-check-awareness-001", runs[0])


def test_report_regenerates_from_preserved_records(in_repo, capsys):
    run_path = _one_run(in_repo)
    capsys.readouterr()
    assert main(["report", run_path]) == EXIT_OK
    assert "wrote" in capsys.readouterr().out


def test_report_resolves_an_experiment_id_to_its_latest_run(in_repo, capsys):
    _one_run(in_repo)
    capsys.readouterr()
    assert main(["report", "fact-check-awareness-001", "--runs-dir", in_repo]) == EXIT_OK


def test_report_stdout_prints_markdown_without_writing(in_repo, capsys):
    run_path = _one_run(in_repo)
    capsys.readouterr()
    assert main(["report", run_path, "--stdout"]) == EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith("# ")
    assert "## Caveats" in out


def test_show_summarises_a_run(in_repo, capsys):
    run_path = _one_run(in_repo)
    capsys.readouterr()
    assert main(["show", run_path]) == EXIT_OK
    out = capsys.readouterr().out
    assert "fact-check-awareness-001" in out
    assert "trials:" in out


def test_show_json_emits_results_verbatim(in_repo, capsys):
    run_path = _one_run(in_repo)
    capsys.readouterr()
    main(["show", run_path, "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "results.v0"


def test_verify_passes_on_a_fresh_run(in_repo, capsys):
    run_path = _one_run(in_repo)
    capsys.readouterr()
    assert main(["verify", run_path]) == EXIT_OK
    assert "OK" in capsys.readouterr().out


def test_verify_fails_with_a_distinct_exit_code_after_tampering(in_repo, capsys):
    """A tampered run must be detectable from a shell script's exit code."""
    run_path = _one_run(in_repo)
    unseal_run(run_path)
    with open(os.path.join(run_path, "trials.jsonl"), "a", encoding="utf-8") as handle:
        handle.write('{"forged": true}\n')
    capsys.readouterr()

    assert main(["verify", run_path]) == EXIT_INTEGRITY_FAILED
    err = capsys.readouterr().err
    assert "DRIFT DETECTED" in err
    assert "untrusted" in err


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_experiments(in_repo, capsys):
    assert main(["list", "experiments"]) == EXIT_OK
    assert "fact-check-awareness-001" in capsys.readouterr().out


def test_list_providers_names_the_registered_adapters(capsys):
    assert main(["list", "providers"]) == EXIT_OK
    out = capsys.readouterr().out
    for name in ("echo", "openai_chat", "anthropic_messages"):
        assert name in out


def test_list_evaluators_names_the_registered_metrics(capsys):
    assert main(["list", "evaluators"]) == EXIT_OK
    assert "builtin.hedging_markers" in capsys.readouterr().out


def test_list_pricing_shows_dates_and_a_disclaimer(capsys):
    assert main(["list", "pricing"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "AS OF" in out
    assert "not billing data" in out


def test_list_runs(in_repo, capsys):
    run_path = _one_run(in_repo)
    capsys.readouterr()
    assert main(["list", "runs", "--runs-dir", in_repo]) == EXIT_OK
    assert run_path in capsys.readouterr().out


# ---------------------------------------------------------------------------
# .env handling
# ---------------------------------------------------------------------------


def test_dotenv_loads_variables(tmp_path, monkeypatch):
    monkeypatch.chdir(str(tmp_path))
    monkeypatch.delenv("UNIT_TEST_TOKEN", raising=False)
    (tmp_path / ".env").write_text(
        "# a comment\n\nUNIT_TEST_TOKEN=abc123\nQUOTED=\"with spaces\"\n", encoding="utf-8"
    )
    loaded = load_dotenv()
    assert "UNIT_TEST_TOKEN" in loaded
    assert os.environ["UNIT_TEST_TOKEN"] == "abc123"
    assert os.environ["QUOTED"] == "with spaces"


def test_dotenv_never_overrides_an_exported_variable(tmp_path, monkeypatch):
    """An explicit export must win over a stale file."""
    monkeypatch.chdir(str(tmp_path))
    monkeypatch.setenv("UNIT_TEST_TOKEN", "from-shell")
    (tmp_path / ".env").write_text("UNIT_TEST_TOKEN=from-file\n", encoding="utf-8")
    load_dotenv()
    assert os.environ["UNIT_TEST_TOKEN"] == "from-shell"


def test_dotenv_returns_names_only_never_values(tmp_path, monkeypatch):
    """The return value may be printed; it must not carry a secret."""
    monkeypatch.chdir(str(tmp_path))
    monkeypatch.delenv("UNIT_TEST_SECRET", raising=False)
    (tmp_path / ".env").write_text("UNIT_TEST_SECRET=super-secret\n", encoding="utf-8")
    loaded = load_dotenv()
    assert loaded == ["UNIT_TEST_SECRET"]
    assert "super-secret" not in repr(loaded)


def test_missing_dotenv_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.chdir(str(tmp_path))
    assert load_dotenv() == []
