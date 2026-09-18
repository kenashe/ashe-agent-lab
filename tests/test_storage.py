"""Tests for the evidence layer: immutability, sealing, and integrity.

These assert the guarantees the rest of the project is built on. If a test in
this file fails, stored experimental records can no longer be trusted, which is
a more serious failure than any amount of broken analysis code.
"""

from __future__ import annotations

import json
import os
import stat

import pytest

from ashe_lab.errors import IntegrityError, StorageError
from ashe_lab.ids import is_run_id, new_run_id
from ashe_lab.runner import RunOptions, execute
from ashe_lab.spec import parse_spec
from ashe_lab.storage import (
    RunWriter,
    delete_run,
    latest_run,
    list_runs,
    load_trials,
    read_checksums,
    resolve_run_path,
    seal_run,
    unseal_run,
    verify_run,
    write_checksums,
    write_report,
)


def _run(spec_dict, runs_dir, **kwargs):
    spec = parse_spec(spec_dict, source_text="# test\n")
    return execute(spec, RunOptions(runs_root=runs_dir, **kwargs))


# ---------------------------------------------------------------------------
# Run identifiers
# ---------------------------------------------------------------------------


def test_run_ids_are_unique():
    ids = {new_run_id() for _ in range(500)}
    assert len(ids) == 500


def test_run_ids_sort_chronologically():
    """The whole reason for the timestamp prefix: `sorted()` means "oldest first"."""
    import datetime

    early = new_run_id(datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc))
    late = new_run_id(datetime.datetime(2027, 1, 1, tzinfo=datetime.timezone.utc))
    assert sorted([late, early]) == [early, late]


def test_run_id_format_is_recognisable():
    assert is_run_id(new_run_id())
    assert not is_run_id("arbitrary-string")


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


def test_a_run_directory_is_never_reused(minimal_spec_dict, runs_dir):
    """There is no overwrite path. This is the core storage guarantee."""
    result = _run(minimal_spec_dict, runs_dir)
    run_id = result.run_id

    with pytest.raises(StorageError) as excinfo:
        RunWriter("unit-test-experiment", run_id, runs_dir)
    assert "already exists" in str(excinfo.value)
    assert "immutable" in str(excinfo.value)


def test_finished_run_files_are_read_only(minimal_spec_dict, runs_dir):
    result = _run(minimal_spec_dict, runs_dir)
    for filename in ("trials.jsonl", "manifest.json", "results.json"):
        path = os.path.join(result.run_path, filename)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert not (mode & stat.S_IWUSR), "{0} is still writable".format(filename)


def _filesystem_enforces_read_only(tmp_dir):
    """Probe whether this filesystem honours the read-only bit for the owner.

    Some environments do not: container overlay filesystems, permissive network
    mounts, and any process running as root will happily write to a 0444 file.
    Sealing is documented as a guardrail against accident rather than a
    security control, so the correct behaviour when the platform cannot enforce
    it is to skip the assertion - not to weaken the seal, and not to pretend
    the guarantee is stronger than the operating system allows. Integrity is
    still detectable either way via checksums, which is what
    ``verify_run`` is for.
    """
    probe = os.path.join(tmp_dir, ".permission-probe")
    with open(probe, "w", encoding="utf-8") as handle:
        handle.write("x")
    os.chmod(probe, 0o444)
    try:
        with open(probe, "a", encoding="utf-8") as handle:
            handle.write("y")
        return False
    except OSError:
        return True
    finally:
        os.chmod(probe, 0o644)
        os.remove(probe)


def test_a_sealed_run_cannot_be_appended_to(minimal_spec_dict, runs_dir):
    if not _filesystem_enforces_read_only(runs_dir):
        pytest.skip(
            "this filesystem does not enforce read-only permissions for the "
            "owner; sealing cannot be asserted here (checksum verification "
            "still detects tampering)"
        )
    result = _run(minimal_spec_dict, runs_dir)
    target = os.path.join(result.run_path, "trials.jsonl")
    with pytest.raises((PermissionError, OSError)):
        with open(target, "a", encoding="utf-8") as handle:
            handle.write("tampered\n")


def test_no_seal_option_leaves_the_run_writable(minimal_spec_dict, runs_dir):
    """An escape hatch for debugging, and it is documented as such."""
    result = _run(minimal_spec_dict, runs_dir, seal=False)
    path = os.path.join(result.run_path, "trials.jsonl")
    assert stat.S_IMODE(os.stat(path).st_mode) & stat.S_IWUSR


def test_unseal_then_seal_round_trips(minimal_spec_dict, runs_dir):
    result = _run(minimal_spec_dict, runs_dir)
    unseal_run(result.run_path)
    path = os.path.join(result.run_path, "report.md")
    assert stat.S_IMODE(os.stat(path).st_mode) & stat.S_IWUSR
    seal_run(result.run_path)
    assert not (stat.S_IMODE(os.stat(path).st_mode) & stat.S_IWUSR)


def test_finalize_cannot_be_called_twice(minimal_spec_dict, runs_dir):
    writer = RunWriter("twice", new_run_id(), runs_dir, seal=False)
    writer.write_spec_snapshot("# x\n")
    kwargs = dict(
        manifest={"run_id": "x"}, results={}, report_markdown="# r\n", trial_rows=[], csv_columns=["a"]
    )
    writer.finalize(**kwargs)
    with pytest.raises(StorageError) as excinfo:
        writer.finalize(**kwargs)
    assert "already been finalized" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Partial runs: a crash must not destroy what was already paid for
# ---------------------------------------------------------------------------


def test_an_aborted_run_keeps_its_completed_trials(runs_dir):
    """The expensive failure mode. Records written so far must survive."""
    run_id = new_run_id()
    try:
        with RunWriter("aborted-experiment", run_id, runs_dir, seal=False) as writer:
            writer.write_spec_snapshot("# spec\n")
            writer.append_trial({"trial_key": "a", "status": "ok"})
            writer.append_trial({"trial_key": "b", "status": "ok"})
            raise RuntimeError("simulated crash mid-run")
    except RuntimeError:
        pass

    run_path = os.path.join(runs_dir, "aborted-experiment", run_id)
    trials = load_trials(run_path)
    assert [t["trial_key"] for t in trials] == ["a", "b"]

    # And the abort is recorded rather than left as a mystery.
    with open(os.path.join(run_path, "events.jsonl"), encoding="utf-8") as handle:
        events = [json.loads(line) for line in handle if line.strip()]
    aborted = [e for e in events if e["event"] == "run_aborted"]
    assert aborted
    assert aborted[0]["data"]["exception_type"] == "RuntimeError"
    assert aborted[0]["data"]["trials_written"] == 2


def test_the_original_exception_is_not_swallowed(runs_dir):
    with pytest.raises(ValueError):
        with RunWriter("propagates", new_run_id(), runs_dir, seal=False) as writer:
            writer.append_trial({"trial_key": "a"})
            raise ValueError("must propagate")


def test_a_truncated_final_line_is_tolerated(runs_dir):
    """A process killed mid-write leaves half a line. Read what survived."""
    run_id = new_run_id()
    with RunWriter("truncated", run_id, runs_dir, seal=False) as writer:
        writer.append_trial({"trial_key": "a", "status": "ok"})

    run_path = os.path.join(runs_dir, "truncated", run_id)
    with open(os.path.join(run_path, "trials.jsonl"), "a", encoding="utf-8") as handle:
        handle.write('{"trial_key": "b", "stat')

    trials = load_trials(run_path)
    assert len(trials) == 1
    assert trials[0]["trial_key"] == "a"


# ---------------------------------------------------------------------------
# Checksums and verification
# ---------------------------------------------------------------------------


def test_a_fresh_run_verifies_clean(minimal_spec_dict, runs_dir):
    result = _run(minimal_spec_dict, runs_dir)
    report = verify_run(result.run_path)
    assert report["ok"] is True
    assert not report["modified"]
    assert not report["missing"]
    assert not report["unexpected"]
    assert len(report["verified"]) >= 7


def test_checksums_cover_every_artifact_except_the_checksum_file(minimal_spec_dict, runs_dir):
    result = _run(minimal_spec_dict, runs_dir)
    recorded = read_checksums(result.run_path)
    for filename in (
        "manifest.json",
        "experiment.snapshot.yaml",
        "trials.jsonl",
        "events.jsonl",
        "results.json",
        "trials.csv",
        "report.md",
    ):
        assert filename in recorded
    assert "checksums.sha256" not in recorded


def test_verification_detects_a_modified_file(minimal_spec_dict, runs_dir):
    """Integrity is arithmetic, not trust."""
    result = _run(minimal_spec_dict, runs_dir)
    unseal_run(result.run_path)
    target = os.path.join(result.run_path, "trials.jsonl")
    with open(target, "a", encoding="utf-8") as handle:
        handle.write('{"trial_key":"forged","status":"ok"}\n')

    report = verify_run(result.run_path)
    assert report["ok"] is False
    assert "trials.jsonl" in report["modified"]


def test_verification_detects_a_deleted_file(minimal_spec_dict, runs_dir):
    result = _run(minimal_spec_dict, runs_dir)
    unseal_run(result.run_path)
    os.remove(os.path.join(result.run_path, "results.json"))
    report = verify_run(result.run_path)
    assert report["ok"] is False
    assert "results.json" in report["missing"]


def test_verification_detects_an_added_file(minimal_spec_dict, runs_dir):
    result = _run(minimal_spec_dict, runs_dir)
    unseal_run(result.run_path)
    with open(os.path.join(result.run_path, "smuggled.json"), "w", encoding="utf-8") as handle:
        handle.write("{}")
    report = verify_run(result.run_path)
    assert report["ok"] is False
    assert "smuggled.json" in report["unexpected"]


def test_checksum_file_format_is_sha256sum_compatible(minimal_spec_dict, runs_dir):
    """A stranger with coreutils must be able to check a run with no Python."""
    result = _run(minimal_spec_dict, runs_dir)
    with open(os.path.join(result.run_path, "checksums.sha256"), encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            digest, _, name = line.rstrip("\n").partition("  ")
            assert len(digest) == 64
            assert all(c in "0123456789abcdef" for c in digest)
            assert name


def test_missing_checksum_file_raises_integrity_error(runs_dir):
    empty = os.path.join(runs_dir, "no-checksums")
    os.makedirs(empty)
    with pytest.raises(IntegrityError):
        read_checksums(empty)


# ---------------------------------------------------------------------------
# Report regeneration: the one sanctioned mutation
# ---------------------------------------------------------------------------


def test_report_can_be_regenerated_without_disturbing_raw_evidence(
    minimal_spec_dict, runs_dir
):
    """A better template must be applicable to old runs, safely."""
    result = _run(minimal_spec_dict, runs_dir)
    trials_path = os.path.join(result.run_path, "trials.jsonl")
    with open(trials_path, encoding="utf-8") as handle:
        original_evidence = handle.read()

    write_report(result.run_path, "# regenerated report\n")

    with open(os.path.join(result.run_path, "report.md"), encoding="utf-8") as handle:
        assert handle.read() == "# regenerated report\n"
    with open(trials_path, encoding="utf-8") as handle:
        assert handle.read() == original_evidence

    # Checksums were refreshed, so verification stays meaningful...
    assert verify_run(result.run_path)["ok"] is True
    # ...and the run was re-sealed.
    mode = stat.S_IMODE(os.stat(os.path.join(result.run_path, "report.md")).st_mode)
    assert not (mode & stat.S_IWUSR)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_list_runs_returns_oldest_first(minimal_spec_dict, runs_dir):
    first = _run(minimal_spec_dict, runs_dir)
    second = _run(minimal_spec_dict, runs_dir)
    found = list_runs("unit-test-experiment", runs_dir)
    assert found == [first.run_path, second.run_path]


def test_latest_run_returns_the_newest(minimal_spec_dict, runs_dir):
    _run(minimal_spec_dict, runs_dir)
    second = _run(minimal_spec_dict, runs_dir)
    assert latest_run("unit-test-experiment", runs_dir) == second.run_path


def test_latest_run_errors_helpfully_when_there_are_none(runs_dir):
    with pytest.raises(StorageError) as excinfo:
        latest_run("never-run", runs_dir)
    assert "never-run" in str(excinfo.value)


def test_resolve_run_path_accepts_an_experiment_id_or_a_directory(
    minimal_spec_dict, runs_dir
):
    result = _run(minimal_spec_dict, runs_dir)
    assert resolve_run_path(result.run_path) == os.path.abspath(result.run_path)
    assert resolve_run_path("unit-test-experiment", runs_dir) == result.run_path


def test_resolve_run_path_rejects_a_directory_that_is_not_a_run(runs_dir):
    with pytest.raises(StorageError) as excinfo:
        resolve_run_path(runs_dir)
    assert "not a run directory" in str(excinfo.value)


def test_runs_dir_can_be_set_by_environment(minimal_spec_dict, tmp_path, monkeypatch):
    target = tmp_path / "custom-runs"
    monkeypatch.setenv("ASHE_LAB_RUNS_DIR", str(target))
    spec = parse_spec(minimal_spec_dict, source_text="# x\n")
    result = execute(spec, RunOptions(seal=False))
    assert str(target) in result.run_path


def test_delete_run_removes_a_sealed_run(minimal_spec_dict, runs_dir):
    """Deliberate deletion must be possible; accidental deletion must not be."""
    result = _run(minimal_spec_dict, runs_dir)
    delete_run(result.run_path)
    assert not os.path.exists(result.run_path)


def test_write_checksums_is_idempotent(minimal_spec_dict, runs_dir):
    result = _run(minimal_spec_dict, runs_dir, seal=False)
    with open(os.path.join(result.run_path, "checksums.sha256"), encoding="utf-8") as handle:
        first = handle.read()
    write_checksums(result.run_path)
    with open(os.path.join(result.run_path, "checksums.sha256"), encoding="utf-8") as handle:
        assert handle.read() == first
