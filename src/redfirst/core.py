"""Pure logic, no I/O: which files are tests, which test functions a change
adds or edits, and what the two test runs say about the change.

The question redfirst answers for one pull request:

    If you take the pull request, keep its test changes, and undo everything
    else, do the new or edited tests still pass?

If they do, the tests never observed the behaviour the pull request changed.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath

TEST_DIRS = frozenset({"test", "tests", "testing"})

# Source files whose revert would need a rebuild. A change that touches them
# cannot be checked by swapping files in place.
COMPILED_SUFFIXES = frozenset(
    {
        ".c", ".h", ".cc", ".cpp", ".cxx", ".hh", ".hpp", ".pyx", ".pxd",
        ".pxi", ".rs", ".go", ".f", ".f90", ".cu", ".m", ".mm", ".zig",
    }
)

# Verdicts, from the point of view of the pull request.
PROVES_FIX = "proves_fix"  # a new or edited test fails once the code change is undone
FAILS_BY_ERROR = "fails_by_error"  # it errors instead (import, fixture, collection)
PASSES_WITHOUT_FIX = "passes_without_fix"  # every such test still passes
INCONCLUSIVE = "inconclusive"  # skipped without the change, so no evidence either way
HEAD_NOT_GREEN = "head_not_green"  # the tests do not pass even with the change
NO_NEW_TESTS = "no_new_tests"  # no test function was added or edited
NO_CODE_CHANGE = "no_code_change"  # only tests changed, nothing to undo
NEEDS_REBUILD = "needs_rebuild"  # compiled sources changed

COUNTED = (PROVES_FIX, FAILS_BY_ERROR, PASSES_WITHOUT_FIX, INCONCLUSIVE)


@dataclass(frozen=True)
class Change:
    status: str  # "A", "M" or "D" (diff run with --no-renames)
    path: str


def is_test_path(path: str) -> bool:
    """Test side of a change: anything under a test directory, test modules, conftest."""
    p = PurePosixPath(path)
    if any(part.lower() in TEST_DIRS for part in p.parts[:-1]):
        return True
    name = p.name
    return name.endswith(".py") and (
        name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"
    )


def is_test_module(path: str) -> bool:
    """A file pytest collects tests from under its default naming rules."""
    name = PurePosixPath(path).name
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


@dataclass(frozen=True)
class Split:
    tests: tuple[Change, ...]
    code: tuple[Change, ...]  # everything that is not test side
    compiled: tuple[Change, ...]

    @property
    def python_code(self) -> tuple[Change, ...]:
        return tuple(c for c in self.code if c.path.endswith(".py"))


def split_changes(changes: list[Change]) -> Split:
    tests, code, compiled = [], [], []
    for change in changes:
        if is_test_path(change.path):
            tests.append(change)
            continue
        code.append(change)
        if PurePosixPath(change.path).suffix.lower() in COMPILED_SUFFIXES:
            compiled.append(change)
    return Split(tuple(tests), tuple(code), tuple(compiled))


def _is_test_class(node: ast.ClassDef) -> bool:
    if node.name.startswith("Test"):
        return True
    for base in node.bases:
        name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
        if isinstance(name, str) and name.endswith("TestCase"):
            return True
    return False


def _digest(node: ast.AST) -> str:
    dumped = ast.dump(node, annotate_fields=True, include_attributes=False)
    return hashlib.sha1(dumped.encode("utf-8")).hexdigest()[:16]


def scan_tests(source: str) -> dict[str, str]:
    """Map each test (``func`` or ``Class::method``) to a digest of its code.

    Decorators are part of the digest, so a new parametrize case counts as an
    edit. Comments and formatting are not.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return {}
    found: dict[str, str] = {}

    def walk_class(cls: ast.ClassDef, prefix: str) -> None:
        for sub in cls.body:
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name.startswith("test"):
                found[f"{prefix}::{sub.name}"] = _digest(sub)
            elif isinstance(sub, ast.ClassDef) and _is_test_class(sub):
                walk_class(sub, f"{prefix}::{sub.name}")

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            found[node.name] = _digest(node)
        elif isinstance(node, ast.ClassDef) and _is_test_class(node):
            walk_class(node, node.name)
    return found


def changed_tests(base_source: str | None, head_source: str) -> tuple[list[str], list[str]]:
    """Return (added, edited) test names between two versions of one file."""
    head = scan_tests(head_source)
    base = scan_tests(base_source) if base_source is not None else {}
    added = sorted(name for name in head if name not in base)
    edited = sorted(name for name in head if name in base and head[name] != base[name])
    return added, edited


# ---------------------------------------------------------------- results


def _split_node(nodeid: str) -> tuple[str, str]:
    bare = nodeid.split("[", 1)[0]
    path, _, name = bare.partition("::")
    return path, name


def _same_test(target: str, reported: str) -> bool:
    t_path, t_name = _split_node(target)
    r_path, r_name = _split_node(reported)
    if t_name != r_name:
        return False
    return t_path == r_path or t_path.endswith("/" + r_path) or r_path.endswith("/" + t_path)


def needs_file_fallback(run: dict) -> bool:
    """pytest runs nothing when any selected id is not found; then run whole files."""
    return not run.get("tests") and "not found:" in (run.get("tail") or "")


def cases_for(target: str, tests: dict[str, dict]) -> list[dict]:
    """All reported cases (parametrized ids included) that belong to one target."""
    return [dict(case, nodeid=nodeid) for nodeid, case in tests.items() if _same_test(target, nodeid)]


def file_failed_to_collect(target: str, collect_errors: list[dict]) -> bool:
    t_path, _ = _split_node(target)
    for err in collect_errors:
        e_path = err.get("nodeid", "").split("::", 1)[0]
        if not e_path or e_path == ".":
            return True  # session-level failure (conftest, plugin, config)
        if t_path == e_path or t_path.endswith("/" + e_path) or e_path.endswith("/" + t_path):
            return True
    return False


def fold(cases: list[dict], collect_failed: bool) -> str:
    """One outcome per test function: failed > error > passed > skipped > missing."""
    outcomes = {c.get("outcome") for c in cases}
    if "failed" in outcomes:
        return "failed"
    if "error" in outcomes:
        return "error"
    if not cases:
        return "error" if collect_failed else "missing"
    if outcomes & {"passed", "xpassed"}:
        return "passed"
    if outcomes & {"xfailed"}:
        return "xfailed"
    return "skipped"


@dataclass
class Row:
    test: str
    kind: str  # "added" or "edited"
    head: str
    base: str | None
    detail: str | None = None  # first line of the failure without the change


def judge(targets: dict[str, str], head_run: dict, base_run: dict | None) -> tuple[str, list[Row]]:
    """Verdict for one pull request.

    ``targets`` maps nodeid -> "added" | "edited". Runs are the plugin's JSON.
    Only tests that pass with the change are judged; the rest are reported.
    """
    rows: list[Row] = []
    head_tests = head_run.get("tests", {})
    head_errors = head_run.get("collect_errors", [])
    for target, kind in sorted(targets.items()):
        head = fold(cases_for(target, head_tests), file_failed_to_collect(target, head_errors))
        rows.append(Row(target, kind, head, None))
    green = [r for r in rows if r.head == "passed"]
    if not green:
        return HEAD_NOT_GREEN, rows
    if base_run is None:
        raise ValueError("base run missing for green targets")
    base_tests = base_run.get("tests", {})
    base_errors = base_run.get("collect_errors", [])
    for row in green:
        cases = cases_for(row.test, base_tests)
        row.base = fold(cases, file_failed_to_collect(row.test, base_errors))
        for case in cases:
            if case.get("outcome") in ("failed", "error") and case.get("message"):
                row.detail = case["message"]
                break
        if row.base == "error" and row.detail is None:
            for err in base_errors:
                if err.get("message"):
                    row.detail = err["message"]
                    break
    outcomes = [r.base for r in green]
    if "failed" in outcomes:
        return PROVES_FIX, rows
    if "error" in outcomes:
        return FAILS_BY_ERROR, rows
    if all(o == "passed" for o in outcomes):
        return PASSES_WITHOUT_FIX, rows
    # skipped, xfailed, or not reported at all (the run hung or crashed)
    return INCONCLUSIVE, rows
