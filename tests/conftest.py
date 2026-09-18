"""Shared pytest fixtures.

Two jobs:

1.  Put ``src/`` on ``sys.path`` so the suite runs in a bare clone without
    ``pip install -e .`` first. A test suite that requires installation before
    it can tell you the installation is broken is not much use.
2.  Unseal run directories created during a test, so temp-directory cleanup can
    remove them. Sealed runs are chmod'd read-only on purpose; that is correct
    behaviour in production and inconvenient in a fixture, so the fixture deals
    with it rather than the production code weakening its guarantee.
"""

from __future__ import annotations

import os
import stat
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_REPO_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


@pytest.fixture
def runs_dir(tmp_path):
    """A temporary runs root, made writable again after the test."""
    target = tmp_path / "runs"
    target.mkdir()
    yield str(target)
    _make_writable(str(target))


def _make_writable(root: str) -> None:
    """Recursively restore write permission so cleanup can delete the tree."""
    for directory, subdirs, filenames in os.walk(root, topdown=True):
        for name in [directory] + [os.path.join(directory, s) for s in subdirs]:
            _chmod(name, 0o755)
        for filename in filenames:
            _chmod(os.path.join(directory, filename), 0o644)
    _chmod(root, 0o755)


def _chmod(path: str, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


@pytest.fixture
def minimal_spec_dict():
    """The smallest valid experiment: one condition, one model, one item.

    Kept deliberately tiny so that a test asserting on validation behaviour
    fails for the reason it is testing rather than because of unrelated spec
    complexity.
    """
    return {
        "id": "unit-test-experiment",
        "title": "Unit test experiment",
        "kind": "single_agent.v0",
        "schema_version": 0,
        "defaults": {"trials": 1},
        "models": [
            {"alias": "fixture", "provider": "echo", "model": "echo-deterministic-v1"}
        ],
        "items": [{"id": "item-one", "vars": {"question": "What is 2 + 2?"}}],
        "conditions": [
            {
                "id": "control",
                "is_control": True,
                "user_template": "{{question}}",
            }
        ],
        "evaluation": {
            "evaluators": [
                {"id": "words", "type": "builtin.response_length_words"},
            ]
        },
    }


@pytest.fixture
def two_condition_spec_dict(minimal_spec_dict):
    """A control/treatment spec, which is the shape real experiments take."""
    spec = dict(minimal_spec_dict)
    spec["id"] = "unit-test-two-condition"
    spec["title"] = "Unit test two-condition experiment"
    spec["research_question"] = "Does the treatment prompt change the response?"
    spec["hypothesis"] = "It does not, because the fixture provider is deterministic."
    spec["defaults"] = {"trials": 2}
    spec["conditions"] = [
        {"id": "control", "is_control": True, "user_template": "{{question}}"},
        {
            "id": "treatment",
            "user_template": "This will be checked. {{question}}",
            "system_prompt": "You are careful.",
        },
    ]
    spec["items"] = [
        {"id": "item-one", "vars": {"question": "What is 2 + 2?"}, "expected": "4"},
        {"id": "item-two", "vars": {"question": "Name the tallest mountain."}},
    ]
    return spec


@pytest.fixture
def stat_helpers():
    """Exposes the read-only mode constants, for permission assertions."""
    return {
        "read_only_file": stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH,
    }
