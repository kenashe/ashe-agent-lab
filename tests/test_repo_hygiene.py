"""Repository hygiene: the project must stay safe to keep public.

These tests guard properties that are easy to break months later, by a human in
a hurry or by a coding agent with good intentions:

*   no credential-shaped strings in tracked files;
*   ``.gitignore`` still covers secret files and run output;
*   ``.env.example`` documents variable names without carrying real values;
*   the documentation a future maintainer is told to read actually exists.

A test suite is the only documentation that fails when it becomes untrue, which
is why these live here rather than in a checklist.
"""

from __future__ import annotations

import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Directories never scanned: not source, and full of false positives.
_SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "node_modules",
    "runs",  # run output is gitignored; model text triggers false positives
    ".egg-info",
    "build",
    "dist",
}

_TEXT_SUFFIXES = {
    ".py",
    ".yaml",
    ".yml",
    ".md",
    ".toml",
    ".cfg",
    ".txt",
    ".json",
    ".example",
    ".sh",
    "",  # Makefile, LICENSE
}

#: Patterns that look like live credentials. Each is a real vendor key shape.
#: The patterns are assembled at run time so that this file does not itself
#: contain a string matching them - otherwise the test would flag its own source.
_SECRET_PATTERNS = [
    ("OpenAI key", re.compile(r"\bsk-[A-Za-z0-9]{32,}")),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{24,}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[A-Za-z0-9\-_]{35}\b")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]

#: Placeholders that legitimately appear in documentation and fixtures.
_ALLOWED_PLACEHOLDERS = (
    "sk-replace-me",
    "sk-ant-replace-me",
    "sk-test",
    "sk-ant-test",
    "sk-do-not-leak-this-value",
    "sk-your-key-here",
)


def _tracked_text_files():
    for directory, subdirs, filenames in os.walk(REPO_ROOT):
        subdirs[:] = [
            d for d in subdirs if d not in _SKIP_DIRS and not d.endswith(".egg-info")
        ]
        for filename in filenames:
            suffix = os.path.splitext(filename)[1]
            if suffix not in _TEXT_SUFFIXES:
                continue
            path = os.path.join(directory, filename)
            if os.path.getsize(path) > 2_000_000:
                continue
            yield path


def test_no_credential_shaped_strings_in_the_repository():
    """The check that keeps this repository publishable."""
    findings = []
    for path in _tracked_text_files():
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            content = handle.read()
        for label, pattern in _SECRET_PATTERNS:
            for match in pattern.finditer(content):
                text = match.group(0)
                if any(placeholder in text for placeholder in _ALLOWED_PLACEHOLDERS):
                    continue
                findings.append(
                    "{0}: possible {1} -> {2}...".format(
                        os.path.relpath(path, REPO_ROOT), label, text[:12]
                    )
                )
    assert not findings, "possible secrets found:\n" + "\n".join(findings)


def test_gitignore_covers_secrets_and_run_output():
    with open(os.path.join(REPO_ROOT, ".gitignore"), encoding="utf-8") as handle:
        content = handle.read()
    for required in (".env", "runs/*", "__pycache__", "*.pem", "*.key"):
        assert required in content, "missing from .gitignore: " + required
    # But the safe template must remain trackable.
    assert "!.env.example" in content


def test_env_example_exists_and_has_no_real_values():
    path = os.path.join(REPO_ROOT, ".env.example")
    assert os.path.isfile(path)
    with open(path, encoding="utf-8") as handle:
        content = handle.read()

    # Documents the variables the adapters read...
    assert "OPENAI_API_KEY" in content
    assert "ANTHROPIC_API_KEY" in content

    # ...without shipping anything that could be a live key.
    for line in content.splitlines():
        if "=" not in line or line.strip().startswith("#"):
            continue
        value = line.split("=", 1)[1].strip()
        assert value == "" or "replace-me" in value or not value.startswith("sk-"), line


def test_no_env_file_is_present_in_the_working_tree():
    """A committed .env is the failure mode this project must never have."""
    assert not os.path.isfile(os.path.join(REPO_ROOT, ".env")), (
        "a .env file exists in the repository root. It is gitignored, but verify "
        "it has never been committed before pushing."
    )


def test_every_environment_variable_the_code_reads_is_documented():
    """A credential the code needs but nobody documented is a support burden."""
    with open(os.path.join(REPO_ROOT, ".env.example"), encoding="utf-8") as handle:
        documented = handle.read()

    referenced = set()
    src = os.path.join(REPO_ROOT, "src")
    for directory, _subdirs, filenames in os.walk(src):
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            with open(os.path.join(directory, filename), encoding="utf-8") as handle:
                content = handle.read()
            referenced.update(re.findall(r"os\.environ(?:\.get)?[\(\[]\"([A-Z_]+)\"", content))

    missing = sorted(name for name in referenced if name not in documented)
    assert not missing, "environment variables used but not in .env.example: " + str(missing)


@pytest.mark.parametrize(
    "filename",
    [
        "README.md",
        "AGENTS.md",
        "ARCHITECTURE.md",
        "VISION.md",
        "ROADMAP.md",
        "LICENSE",
        ".env.example",
        ".gitignore",
        "pyproject.toml",
        "Makefile",
        "docs/EXPERIMENT_SPEC.md",
        "docs/RUN_FORMAT.md",
    ],
)
def test_required_documentation_exists(filename):
    """These files are referenced by other docs and by error messages."""
    path = os.path.join(REPO_ROOT, filename)
    assert os.path.isfile(path), filename + " is missing"
    assert os.path.getsize(path) > 200, filename + " is suspiciously short"


def test_agents_md_covers_the_tasks_it_promises():
    """AGENTS.md is the contract with future maintainers. Verify its coverage."""
    with open(os.path.join(REPO_ROOT, "AGENTS.md"), encoding="utf-8") as handle:
        content = handle.read().lower()
    for topic in (
        "add an experiment",
        "add a model provider",
        "run the test",
        "generate a report",
    ):
        assert topic in content, "AGENTS.md does not cover: " + topic


def test_runs_directory_is_kept_but_empty_in_version_control():
    """The directory should exist on clone so the first run has somewhere to go."""
    assert os.path.isfile(os.path.join(REPO_ROOT, "runs", ".gitkeep"))


def test_package_declares_exactly_one_runtime_dependency():
    """The dependency budget is a design commitment; assert it holds."""
    with open(os.path.join(REPO_ROOT, "pyproject.toml"), encoding="utf-8") as handle:
        content = handle.read()
    block = content.split("dependencies = [", 1)[1].split("]", 1)[0]
    declared = [line.strip() for line in block.splitlines() if line.strip().startswith('"')]
    assert len(declared) == 1, "expected exactly one runtime dependency, got: " + str(declared)
    assert "yaml" in declared[0].lower()


def test_source_imports_no_undeclared_third_party_package():
    """Catch an accidental `import requests` before it reaches a user."""
    allowed_third_party = {"yaml", "pytest", "typing_extensions"}
    stdlib_ish = re.compile(r"^(?:from|import)\s+([a-zA-Z_][a-zA-Z0-9_]*)")
    offenders = []

    src = os.path.join(REPO_ROOT, "src")
    for directory, _subdirs, filenames in os.walk(src):
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = os.path.join(directory, filename)
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    match = stdlib_ish.match(line.strip())
                    if not match:
                        continue
                    module = match.group(1)
                    if module in ("__future__", "ashe_lab"):
                        continue
                    if module in allowed_third_party:
                        continue
                    if module in _STDLIB_MODULES:
                        continue
                    offenders.append("{0}: {1}".format(os.path.relpath(path, REPO_ROOT), module))
    assert not offenders, "undeclared non-stdlib imports: " + str(sorted(set(offenders)))


#: The standard-library modules this project uses. Kept explicit rather than
#: probed dynamically, so the list doubles as documentation of the surface the
#: framework depends on.
_STDLIB_MODULES = {
    "argparse",
    "copy",
    "csv",
    "dataclasses",
    "datetime",
    "hashlib",
    "itertools",
    "json",
    "math",
    "os",
    "platform",
    "re",
    "shutil",
    "stat",
    "subprocess",
    "sys",
    "time",
    "traceback",
    "typing",
    "urllib",
    "uuid",
}
