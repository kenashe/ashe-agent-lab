"""Immutable run directories: the evidence layer.

This is the part of the framework that matters most in ten years. Code gets
rewritten; the preserved runs are the only thing that cannot be regenerated.
So the storage layer's rules are strict and few.

The four rules
--------------
1.  **A run directory is written once.** :class:`RunWriter` refuses to open a
    directory that already exists. There is no update path, no overwrite flag,
    no ``--force``. Re-running an experiment produces a *new* run.
2.  **Raw records are append-only.** Trials and events are written to JSONL as
    they happen and flushed immediately, so a crash, a killed process, or an
    exhausted API budget leaves a partial run containing every trial that did
    complete - not a corrupt file and not nothing.
3.  **Every artifact is hashed.** ``checksums.sha256`` records the SHA-256 of
    every file in the run. :func:`verify_run` recomputes them, which turns
    "has this evidence been touched?" from a matter of trust into a matter of
    arithmetic.
4.  **Finished runs are sealed.** On completion, files are chmod'd to
    read-only and the directory to non-writable. This is a guardrail against
    accident, not a security control - anyone with the account can
    :func:`unseal_run`. It exists to stop a future script, or a future coding
    agent, from casually clobbering an experimental record.

Layout
------
::

    runs/<experiment-id>/<run-id>/
        manifest.json              run metadata, spec hash, environment
        experiment.snapshot.yaml   the spec exactly as executed
        trials.jsonl               one JSON object per trial (raw evidence)
        events.jsonl               lifecycle log
        results.json               aggregated, machine-readable
        trials.csv                 flat export for spreadsheets and R/pandas
        report.md                  human-readable report
        checksums.sha256           SHA-256 of every file above

Why flat files and not SQLite? Because the access pattern is "append during a
run, read everything afterwards", which is exactly what JSONL is good at, and
because a JSONL file can be read by ``head``, ``jq``, pandas, and a human with
a text editor, forever, with no schema migration. See ARCHITECTURE.md ->
"Storage decision" for when SQLite becomes the right answer instead.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import stat
from typing import Any, Dict, Iterable, Iterator, List, Optional

from .errors import IntegrityError, StorageError
from .ids import canonical_json, iso_utc, sha256_file

MANIFEST_FILENAME = "manifest.json"
SPEC_SNAPSHOT_FILENAME = "experiment.snapshot.yaml"
TRIALS_FILENAME = "trials.jsonl"
EVENTS_FILENAME = "events.jsonl"
RESULTS_FILENAME = "results.json"
TRIALS_CSV_FILENAME = "trials.csv"
REPORT_FILENAME = "report.md"
CHECKSUMS_FILENAME = "checksums.sha256"

#: Files excluded from checksum coverage, because they cannot hash themselves.
_CHECKSUM_EXCLUDED = (CHECKSUMS_FILENAME,)

DEFAULT_RUNS_DIR = "runs"


def runs_root(explicit: Optional[str] = None) -> str:
    """Resolve where run directories live.

    Precedence: explicit argument, then ``ASHE_LAB_RUNS_DIR``, then ``./runs``.
    """
    return explicit or os.environ.get("ASHE_LAB_RUNS_DIR") or DEFAULT_RUNS_DIR


def run_dir_for(experiment_id: str, run_id: str, root: Optional[str] = None) -> str:
    """Path of a run directory, without creating it."""
    return os.path.join(runs_root(root), experiment_id, run_id)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


class RunWriter:
    """Creates and populates exactly one run directory.

    Usage::

        with RunWriter(experiment_id, run_id) as writer:
            writer.write_spec_snapshot(spec_yaml_text)
            writer.append_trial(record)
            writer.finalize(manifest=..., results=..., report_markdown=...)

    Leaving the context manager without calling :meth:`finalize` still leaves a
    readable, unsealed run directory containing every record written so far,
    plus an event noting the abnormal exit. A crashed run is evidence too.
    """

    def __init__(
        self,
        experiment_id: str,
        run_id: str,
        root: Optional[str] = None,
        *,
        seal: bool = True,
    ) -> None:
        self.experiment_id = experiment_id
        self.run_id = run_id
        self.path = run_dir_for(experiment_id, run_id, root)
        self._seal = seal
        self._finalized = False
        self._trial_count = 0
        self._trials_handle = None
        self._events_handle = None

        if os.path.exists(self.path):
            raise StorageError(
                "run directory already exists and will not be overwritten: {0}. "
                "Run directories are immutable by design; start a new run instead.".format(
                    self.path
                )
            )
        try:
            os.makedirs(self.path)
        except OSError as exc:
            raise StorageError("cannot create run directory {0}: {1}".format(self.path, exc))

        self._trials_handle = open(
            os.path.join(self.path, TRIALS_FILENAME), "w", encoding="utf-8"
        )
        self._events_handle = open(
            os.path.join(self.path, EVENTS_FILENAME), "w", encoding="utf-8"
        )

    # -- context manager --------------------------------------------------

    def __enter__(self) -> "RunWriter":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        if exc_type is not None and not self._finalized:
            try:
                self.append_event(
                    "run_aborted",
                    {
                        "exception_type": exc_type.__name__,
                        "exception_message": str(exc_value),
                        "trials_written": self._trial_count,
                    },
                )
            except Exception:  # pragma: no cover - never mask the real error
                pass
        self._close_handles()
        return False  # never swallow the original exception

    def _close_handles(self) -> None:
        for handle in (self._trials_handle, self._events_handle):
            if handle is not None and not handle.closed:
                try:
                    handle.flush()
                    handle.close()
                except Exception:  # pragma: no cover
                    pass

    # -- writing ----------------------------------------------------------

    def write_spec_snapshot(self, yaml_text: str) -> None:
        """Preserve the experiment definition verbatim, as executed.

        A byte copy rather than a re-serialisation of the parsed spec: comments
        and formatting are part of the author's intent, and a round-trip through
        a YAML dumper would silently discard them.
        """
        self._write_text(SPEC_SNAPSHOT_FILENAME, yaml_text)

    def append_trial(self, record: Dict[str, Any]) -> None:
        """Append one trial record to ``trials.jsonl`` and flush it to disk.

        Flushing every record costs a syscall and buys the guarantee that an
        interrupted run keeps everything it paid for.
        """
        if self._trials_handle is None:
            raise StorageError("run writer is closed")
        self._trials_handle.write(canonical_json(record) + "\n")
        self._trials_handle.flush()
        self._trial_count += 1

    def append_event(self, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """Append a lifecycle event: run start, cell start, retry, completion."""
        if self._events_handle is None:
            raise StorageError("run writer is closed")
        record = {"ts": iso_utc(), "event": event_type}
        if payload:
            record["data"] = payload
        self._events_handle.write(canonical_json(record) + "\n")
        self._events_handle.flush()

    def finalize(
        self,
        *,
        manifest: Dict[str, Any],
        results: Dict[str, Any],
        report_markdown: str,
        trial_rows: Iterable[Dict[str, Any]],
        csv_columns: List[str],
    ) -> str:
        """Write derived artifacts, checksum everything, and seal the run.

        Returns the run directory path.
        """
        if self._finalized:
            raise StorageError("run {0} has already been finalized".format(self.run_id))

        self._write_text(MANIFEST_FILENAME, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        self._write_text(RESULTS_FILENAME, json.dumps(results, indent=2, sort_keys=True) + "\n")
        self._write_text(REPORT_FILENAME, report_markdown)
        self._write_csv(TRIALS_CSV_FILENAME, trial_rows, csv_columns)

        self.append_event("run_finalized", {"trials_written": self._trial_count})
        self._close_handles()

        write_checksums(self.path)

        if self._seal:
            seal_run(self.path)

        self._finalized = True
        return self.path

    # -- internals --------------------------------------------------------

    def _write_text(self, filename: str, text: str) -> None:
        target = os.path.join(self.path, filename)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(text)

    def _write_csv(
        self, filename: str, rows: Iterable[Dict[str, Any]], columns: List[str]
    ) -> None:
        target = os.path.join(self.path, filename)
        with open(target, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)


# ---------------------------------------------------------------------------
# Integrity
# ---------------------------------------------------------------------------


def write_checksums(run_path: str) -> str:
    """Write ``checksums.sha256`` covering every file in the run directory.

    Format matches ``sha256sum``'s output, so ``sha256sum -c checksums.sha256``
    verifies a run with no Ashe Agent Lab installed at all. That portability is
    the point: the evidence should be checkable by a stranger with coreutils.
    """
    lines: List[str] = []
    for relative in _iter_run_files(run_path):
        if relative in _CHECKSUM_EXCLUDED:
            continue
        digest = sha256_file(os.path.join(run_path, relative))
        lines.append("{0}  {1}".format(digest, relative))
    lines.sort(key=lambda line: line.split("  ", 1)[1])

    target = os.path.join(run_path, CHECKSUMS_FILENAME)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return target


def read_checksums(run_path: str) -> Dict[str, str]:
    """Parse ``checksums.sha256`` into ``{relative_path: digest}``."""
    target = os.path.join(run_path, CHECKSUMS_FILENAME)
    if not os.path.isfile(target):
        raise IntegrityError("run {0} has no {1}".format(run_path, CHECKSUMS_FILENAME))
    out: Dict[str, str] = {}
    with open(target, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("  ", 1)
            if len(parts) != 2:
                raise IntegrityError("malformed checksum line in {0}: {1!r}".format(target, line))
            out[parts[1]] = parts[0]
    return out


def verify_run(run_path: str) -> Dict[str, Any]:
    """Recompute checksums and report any drift.

    Returns a report dict with ``ok``, ``modified``, ``missing``, ``unexpected``
    and ``verified`` lists. Does not raise on mismatch: the caller decides how
    loud to be, and a report listing what changed is more useful than an
    exception.
    """
    expected = read_checksums(run_path)
    present = {r for r in _iter_run_files(run_path) if r not in _CHECKSUM_EXCLUDED}

    modified: List[str] = []
    missing: List[str] = []
    verified: List[str] = []

    for relative, digest in sorted(expected.items()):
        absolute = os.path.join(run_path, relative)
        if not os.path.isfile(absolute):
            missing.append(relative)
            continue
        if sha256_file(absolute) != digest:
            modified.append(relative)
        else:
            verified.append(relative)

    unexpected = sorted(present - set(expected))

    return {
        "run_path": run_path,
        "ok": not modified and not missing and not unexpected,
        "verified": verified,
        "modified": modified,
        "missing": missing,
        "unexpected": unexpected,
        "checked_at": iso_utc(),
    }


def _iter_run_files(run_path: str) -> Iterator[str]:
    """Yield every file in the run directory, as paths relative to it."""
    for directory, _subdirs, filenames in os.walk(run_path):
        for filename in sorted(filenames):
            absolute = os.path.join(directory, filename)
            yield os.path.relpath(absolute, run_path)


# ---------------------------------------------------------------------------
# Sealing
# ---------------------------------------------------------------------------

_READ_ONLY_FILE = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH  # 0o444
_READ_ONLY_DIR = _READ_ONLY_FILE | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH  # 0o555
_WRITABLE_FILE = _READ_ONLY_FILE | stat.S_IWUSR  # 0o644
_WRITABLE_DIR = _READ_ONLY_DIR | stat.S_IWUSR  # 0o755


def seal_run(run_path: str) -> None:
    """Make a finished run read-only.

    A guardrail, not a security boundary: it stops accidents (a stray script, a
    confused agent, a careless ``>``), and it makes deliberate modification
    require an explicit act that shows up in shell history.
    """
    for directory, subdirs, filenames in os.walk(run_path, topdown=False):
        for filename in filenames:
            _chmod_quietly(os.path.join(directory, filename), _READ_ONLY_FILE)
        for subdir in subdirs:
            _chmod_quietly(os.path.join(directory, subdir), _READ_ONLY_DIR)
    _chmod_quietly(run_path, _READ_ONLY_DIR)


def unseal_run(run_path: str) -> None:
    """Make a sealed run writable again.

    Provided so maintenance (moving, archiving, deleting an obsolete run) does
    not require ``chmod`` incantations. Re-sealing after any content change
    requires rewriting checksums, or :func:`verify_run` will correctly report
    tampering.
    """
    _chmod_quietly(run_path, _WRITABLE_DIR)
    for directory, subdirs, filenames in os.walk(run_path):
        for subdir in subdirs:
            _chmod_quietly(os.path.join(directory, subdir), _WRITABLE_DIR)
        for filename in filenames:
            _chmod_quietly(os.path.join(directory, filename), _WRITABLE_FILE)


def _chmod_quietly(path: str, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        # Filesystems without POSIX permissions (some network mounts, some
        # Windows setups) cannot seal. The checksum file still detects
        # tampering, so degrade rather than fail the run.
        pass


def delete_run(run_path: str) -> None:
    """Unseal and remove a run directory. Only for genuine mistakes."""
    if not os.path.isdir(run_path):
        raise StorageError("no run directory at {0}".format(run_path))
    unseal_run(run_path)
    shutil.rmtree(run_path)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def list_runs(experiment_id: Optional[str] = None, root: Optional[str] = None) -> List[str]:
    """Paths of stored runs, oldest first.

    Sorting by run id sorts chronologically, because run ids lead with a
    compact UTC timestamp. That is the whole reason for the id format.
    """
    base = runs_root(root)
    if not os.path.isdir(base):
        return []

    experiment_ids = [experiment_id] if experiment_id else sorted(os.listdir(base))
    found: List[str] = []
    for exp in experiment_ids:
        exp_dir = os.path.join(base, exp)
        if not os.path.isdir(exp_dir):
            continue
        for run_id in sorted(os.listdir(exp_dir)):
            candidate = os.path.join(exp_dir, run_id)
            if os.path.isdir(candidate):
                found.append(candidate)
    return found


def latest_run(experiment_id: str, root: Optional[str] = None) -> str:
    """Path of the most recent run for an experiment."""
    runs = list_runs(experiment_id, root)
    if not runs:
        raise StorageError(
            "no runs found for experiment {0!r} under {1}".format(
                experiment_id, runs_root(root)
            )
        )
    return runs[-1]


def resolve_run_path(target: str, root: Optional[str] = None) -> str:
    """Accept a run directory path OR an experiment id, return a run path.

    Given an experiment id, resolves to that experiment's latest run - which is
    what "generate the report for what I just ran" means in practice.
    """
    if os.path.isdir(target) and os.path.isfile(os.path.join(target, TRIALS_FILENAME)):
        return os.path.abspath(target)
    if os.path.isdir(target):
        raise StorageError(
            "{0} is a directory but contains no {1}; it is not a run directory".format(
                target, TRIALS_FILENAME
            )
        )
    return latest_run(target, root)


def load_manifest(run_path: str) -> Dict[str, Any]:
    """Read ``manifest.json``."""
    return _load_json(os.path.join(run_path, MANIFEST_FILENAME))


def load_results(run_path: str) -> Dict[str, Any]:
    """Read ``results.json``."""
    return _load_json(os.path.join(run_path, RESULTS_FILENAME))


def load_trials(run_path: str) -> List[Dict[str, Any]]:
    """Read every record from ``trials.jsonl``.

    Tolerates a truncated final line, which is what a process killed mid-write
    leaves behind. A partial run should still be readable.
    """
    target = os.path.join(run_path, TRIALS_FILENAME)
    if not os.path.isfile(target):
        raise StorageError("run {0} has no {1}".format(run_path, TRIALS_FILENAME))
    records: List[Dict[str, Any]] = []
    with open(target, "r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                # Only the last line can legitimately be half-written.
                if handle.read() == "":
                    break
                raise StorageError(
                    "{0} line {1} is not valid JSON".format(target, line_number)
                )
    return records


def load_events(run_path: str) -> List[Dict[str, Any]]:
    """Read every record from ``events.jsonl``."""
    target = os.path.join(run_path, EVENTS_FILENAME)
    if not os.path.isfile(target):
        return []
    records: List[Dict[str, Any]] = []
    with open(target, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                break
    return records


def load_spec_snapshot(run_path: str) -> str:
    """Read the preserved experiment definition as text."""
    target = os.path.join(run_path, SPEC_SNAPSHOT_FILENAME)
    if not os.path.isfile(target):
        raise StorageError("run {0} has no {1}".format(run_path, SPEC_SNAPSHOT_FILENAME))
    with open(target, "r", encoding="utf-8") as handle:
        return handle.read()


def write_report(run_path: str, markdown: str) -> str:
    """Rewrite ``report.md`` for an existing run, then refresh checksums.

    The one sanctioned mutation of a sealed run, and a narrow one: the report is
    *derived* from ``trials.jsonl``, so regenerating it with an improved
    template destroys no evidence. Raw records are never touched, checksums are
    rewritten so integrity verification stays meaningful, and the run is
    re-sealed.
    """
    was_sealed = not os.access(run_path, os.W_OK)
    if was_sealed:
        unseal_run(run_path)
    try:
        target = os.path.join(run_path, REPORT_FILENAME)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(markdown)
        write_checksums(run_path)
    finally:
        if was_sealed:
            seal_run(run_path)
    return os.path.join(run_path, REPORT_FILENAME)


def _load_json(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        raise StorageError("missing file: {0}".format(path))
    with open(path, "r", encoding="utf-8") as handle:
        try:
            return json.load(handle)
        except ValueError as exc:
            raise StorageError("{0} is not valid JSON: {1}".format(path, exc))
