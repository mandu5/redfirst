"""Git side: what a change touched, which tests it added or edited, and a
context manager that puts the non-test side of a working tree back to the
base revision while the tests stay at the head revision."""

from __future__ import annotations

import os
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .core import (
    NEEDS_REBUILD,
    NO_CODE_CHANGE,
    NO_NEW_TESTS,
    Change,
    Split,
    changed_tests,
    is_test_module,
    split_changes,
)


class GitError(RuntimeError):
    pass


def git(repo: Path, *args: str, binary: bool = False, check: bool = True):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, env=env)
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout if binary else proc.stdout.decode("utf-8", "replace")


def rev_parse(repo: Path, rev: str) -> str:
    return git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}").strip()


def merge_base(repo: Path, base: str, head: str) -> str:
    return git(repo, "merge-base", base, head).strip()


def diff_changes(repo: Path, base: str, head: str) -> list[Change]:
    out = git(repo, "diff", "--name-status", "--no-renames", "-z", base, head)
    parts = [p for p in out.split("\0") if p]
    changes = []
    for status, path in zip(parts[0::2], parts[1::2]):
        changes.append(Change(status[0], path))
    return changes


def show(repo: Path, rev: str, path: str) -> bytes | None:
    proc = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=repo, capture_output=True)
    return proc.stdout if proc.returncode == 0 else None


@dataclass
class Plan:
    base: str  # merge base, the code the change started from
    head: str
    split: Split
    targets: dict[str, str] = field(default_factory=dict)  # nodeid -> added | edited
    files: list[str] = field(default_factory=list)  # test modules to run
    early: str | None = None  # verdict reached without running anything

    @property
    def selection(self) -> list[str]:
        """The target test ids, to run only the tests the change added or edited."""
        return sorted(self.targets)

    def summary(self) -> dict:
        return {
            "base": self.base,
            "head": self.head,
            "test_files_changed": [c.path for c in self.split.tests],
            "code_files_changed": [c.path for c in self.split.code],
            "targets": self.targets,
            "files": self.files,
            "early": self.early,
        }


def make_plan(repo: Path, base: str, head: str) -> Plan:
    head_sha = rev_parse(repo, head)
    base_given = rev_parse(repo, base)
    try:
        base_sha = merge_base(repo, base_given, head_sha)
    except GitError:
        # Shallow checkouts (the default in GitHub Actions) have no history to
        # find a merge base in. On a pull_request event HEAD is the test merge
        # commit and the given base is its first parent, so diffing against it
        # directly is the change.
        base_sha = base_given
    split = split_changes(diff_changes(repo, base_sha, head_sha))
    plan = Plan(base_sha, head_sha, split)
    if not split.code:
        plan.early = NO_CODE_CHANGE
        return plan
    if split.compiled:
        plan.early = NEEDS_REBUILD
        return plan
    for change in split.tests:
        if change.status == "D" or not is_test_module(change.path):
            continue
        head_src = show(repo, head_sha, change.path)
        if head_src is None:
            continue
        base_src = show(repo, base_sha, change.path) if change.status == "M" else None
        added, edited = changed_tests(
            base_src.decode("utf-8", "replace") if base_src is not None else None,
            head_src.decode("utf-8", "replace"),
        )
        for name in added:
            plan.targets[f"{change.path}::{name}"] = "added"
        for name in edited:
            plan.targets[f"{change.path}::{name}"] = "edited"
        if added or edited:
            plan.files.append(change.path)
    if not plan.targets:
        plan.early = NO_NEW_TESTS
    return plan


@contextmanager
def code_at_base(repo: Path, plan: Plan):
    """Temporarily undo the non-test side of the change in the working tree.

    Test files keep their head content. Everything is restored on exit, even
    after an exception, from a copy of the working tree taken on entry.
    """
    saved: dict[str, bytes | None] = {}
    try:
        for change in plan.split.code:
            target = repo / change.path
            saved[change.path] = target.read_bytes() if target.is_file() else None
            if change.status == "A":
                if target.exists():
                    target.unlink()
                continue
            content = show(repo, plan.base, change.path)
            if content is None:
                raise GitError(f"{change.path} missing at {plan.base}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        yield
    finally:
        for path, content in saved.items():
            target = repo / path
            if content is None:
                if target.exists():
                    target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
