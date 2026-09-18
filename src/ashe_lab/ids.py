"""Identifiers, timestamps, and hashing.

Three small rules live here, and the whole reproducibility story leans on them:

1. Timestamps are always UTC, always ISO 8601 with a ``Z`` suffix.
2. A run identifier is sortable by time and unique without coordination.
3. Content hashes are SHA-256 hex digests of UTF-8 bytes.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import uuid
from typing import Any

#: Allowed shape for experiment ids, condition ids, item ids, model aliases.
#: Deliberately restrictive: these strings become directory names, CSV columns,
#: and URL fragments in a future static publisher. Lowercase only, 1-64
#: characters, no leading or trailing punctuation.
SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")

#: e.g. ``20260918T134502.318Z-9f3a1c7e``
RUN_ID_PATTERN = re.compile(r"^\d{8}T\d{6}\.\d{3}Z-[0-9a-f]{8}$")


def is_slug(value: Any) -> bool:
    """Return True if ``value`` is a safe identifier for filesystem + tabular use."""
    return isinstance(value, str) and bool(SLUG_PATTERN.match(value))


def utc_now() -> _dt.datetime:
    """Current time as a timezone-aware UTC datetime."""
    return _dt.datetime.now(_dt.timezone.utc)


def iso_utc(moment: "_dt.datetime | None" = None) -> str:
    """Format a datetime as ``2026-09-18T13:45:02.123456Z``.

    Always UTC. A naive datetime is assumed to already be UTC.
    """
    moment = moment or utc_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_dt.timezone.utc)
    return moment.astimezone(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def new_run_id(moment: "_dt.datetime | None" = None) -> str:
    """Mint a fresh run identifier, e.g. ``20260918T134502.318Z-9f3a1c7e``.

    Two properties, both load-bearing:

    *   **Lexicographic order is chronological order.** Every component is
        fixed-width, so ``sorted()`` on run ids - or an alphabetical file
        browser - lists runs oldest first. ``latest_run()`` depends on this.
    *   **No coordination needed.** The random suffix means two runs started in
        the same millisecond still get distinct directories, with no counter,
        lockfile, or database.

    Millisecond precision rather than seconds specifically so that two runs
    launched back to back in a script still order correctly.
    """
    moment = moment or utc_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_dt.timezone.utc)
    moment = moment.astimezone(_dt.timezone.utc)
    stamp = "{0}.{1:03d}Z".format(
        moment.strftime("%Y%m%dT%H%M%S"), moment.microsecond // 1000
    )
    return "{0}-{1}".format(stamp, uuid.uuid4().hex[:8])


def is_run_id(value: Any) -> bool:
    """Return True if ``value`` looks like a run identifier minted by this module."""
    return isinstance(value, str) and bool(RUN_ID_PATTERN.match(value))


def sha256_text(text: str) -> str:
    """SHA-256 hex digest of ``text`` encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str) -> str:
    """SHA-256 hex digest of a file's bytes, read in chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    """Serialise ``value`` deterministically, so its hash is stable.

    Sorted keys, no insignificant whitespace. Used for content-addressing specs
    and trial inputs.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(value: Any) -> str:
    """Stable SHA-256 of any JSON-serialisable structure."""
    return sha256_text(canonical_json(value))


def trial_key(
    condition_id: str, model_alias: str, item_id: str, trial_index: int
) -> str:
    """Human-readable unique key for one trial within a run.

    A trial is the atomic unit of evidence: one condition, one model, one
    stimulus item, one repetition.
    """
    return "{0}|{1}|{2}|{3:03d}".format(
        condition_id, model_alias, item_id, trial_index
    )


def env_fingerprint() -> "dict":
    """Non-secret description of the machine that produced a run.

    Recorded in ``manifest.json`` so a future reader can tell whether a
    surprising result might be environmental. Deliberately excludes anything
    that could carry a credential: no environment variables are captured.
    """
    import platform
    import sys

    return {
        "python_version": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "hostname_hash": sha256_text(platform.node())[:16],
        "cwd_basename": os.path.basename(os.getcwd()),
    }
