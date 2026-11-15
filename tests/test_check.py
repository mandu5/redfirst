"""End to end on throwaway git repositories: one per verdict.

Each case starts from the same buggy base and adds one commit. The checker
runs real pytest subprocesses with the interpreter running these tests.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from redfirst.cli import check, main
from redfirst.core import (
    FAILS_BY_ERROR,
    HEAD_NOT_GREEN,
    NO_CODE_CHANGE,
    NO_NEW_TESTS,
    PASSES_WITHOUT_FIX,
    PROVES_FIX,
)

BUGGY = "def add(a, b):\n    return a - b\n\n\ndef neg(a):\n    return -a\n"
FIXED = "def add(a, b):\n    return a + b\n\n\ndef neg(a):\n    return -a\n"
OLD_TESTS = textwrap.dedent(
    """
    import pytest
    from calc import add, neg

    def test_neg():
        assert neg(2) == -2

    @pytest.mark.parametrize("a", [0])
    def test_add_zero(a):
        assert add(a, 0) == a
    """
)


def sh(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def commit(repo, files, message):
    for path, text in files.items():
        target = repo / path
        if text is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    sh(repo, "add", "-A")
    sh(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    sh(root, "init", "-q", "-b", "main")
    commit(root, {"calc.py": BUGGY, "conftest.py": "", "tests/test_calc.py": OLD_TESTS}, "base")
    return root


def verdict_for(repo, files):
    commit(repo, files, "change")
    return check(repo, "HEAD~1", "HEAD", sys.executable, 120, [])


def test_new_test_that_catches_the_bug(repo):
    tests = OLD_TESTS + "\ndef test_add():\n    assert add(2, 3) == 5\n"
    report = verdict_for(repo, {"calc.py": FIXED, "tests/test_calc.py": tests})
    assert report["verdict"] == PROVES_FIX
    row = report["rows"][0]
    assert (row["test"], row["kind"], row["head"], row["base"]) == ("tests/test_calc.py::test_add", "added", "passed", "failed")
    assert "assert" in row["detail"]


def test_new_test_that_never_touches_the_fix(repo):
    tests = OLD_TESTS + "\ndef test_add_identity():\n    assert add(0, 0) == 0\n"
    report = verdict_for(repo, {"calc.py": FIXED, "tests/test_calc.py": tests})
    assert report["verdict"] == PASSES_WITHOUT_FIX


def test_new_parametrize_case_counts_as_an_edit(repo):
    tests = OLD_TESTS.replace("[0]", "[0, 1]")
    report = verdict_for(repo, {"calc.py": FIXED, "tests/test_calc.py": tests})
    assert report["rows"][0]["kind"] == "edited"
    assert report["verdict"] == PASSES_WITHOUT_FIX  # add(1, 0) == 1 holds for the buggy add too


def test_edited_assertion_that_catches_the_bug(repo):
    tests = OLD_TESTS.replace("add(a, 0) == a", "add(a, 1) == a + 1")
    report = verdict_for(repo, {"calc.py": FIXED, "tests/test_calc.py": tests})
    assert report["verdict"] == PROVES_FIX
    assert report["rows"][0]["kind"] == "edited"


def test_test_importing_a_new_function_errors_without_it(repo):
    code = FIXED + "\n\ndef mul(a, b):\n    return a * b\n"
    tests = OLD_TESTS + "\nfrom calc import mul\n\ndef test_mul():\n    assert mul(2, 3) == 6\n"
    report = verdict_for(repo, {"calc.py": code, "tests/test_calc.py": tests})
    assert report["verdict"] == FAILS_BY_ERROR
    assert "mul" in report["rows"][0]["detail"]


def test_new_module_and_new_test_file(repo):
    report = verdict_for(
        repo,
        {"calc2.py": "def sq(a):\n    return a * a\n", "tests/test_calc2.py": "from calc2 import sq\n\ndef test_sq():\n    assert sq(3) == 9\n"},
    )
    assert report["verdict"] == FAILS_BY_ERROR
    assert (repo / "calc2.py").exists()  # restored after the run


def test_failing_new_test(repo):
    tests = OLD_TESTS + "\ndef test_add():\n    assert add(2, 3) == 6\n"
    assert verdict_for(repo, {"calc.py": FIXED, "tests/test_calc.py": tests})["verdict"] == HEAD_NOT_GREEN


def test_code_only_and_tests_only(repo):
    assert verdict_for(repo, {"calc.py": FIXED})["verdict"] == NO_NEW_TESTS
    tests = OLD_TESTS + "\ndef test_more():\n    assert neg(0) == 0\n"
    assert verdict_for(repo, {"tests/test_calc.py": tests})["verdict"] == NO_CODE_CHANGE


def test_working_tree_is_restored(repo):
    tests = OLD_TESTS + "\ndef test_add():\n    assert add(2, 3) == 5\n"
    verdict_for(repo, {"calc.py": FIXED, "tests/test_calc.py": tests})
    assert (repo / "calc.py").read_text() == FIXED
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    assert status == ""


def test_cli_fail_mode_exit_code(repo, capsys):
    tests = OLD_TESTS + "\ndef test_add_identity():\n    assert add(0, 0) == 0\n"
    commit(repo, {"calc.py": FIXED, "tests/test_calc.py": tests}, "change")
    summary = repo.parent / "summary.md"
    code = main(["check", "--repo", str(repo), "--base", "HEAD~1", "--mode", "fail", "--summary", str(summary)])
    assert code == 1
    assert "passes_without_fix" in capsys.readouterr().out
    assert "| `tests/test_calc.py::test_add_identity` | added | passed | passed |" in summary.read_text()


def test_shallow_merge_commit_like_github_actions(repo, tmp_path):
    """actions/checkout gives a depth-1 clone of the test merge commit."""
    sh(repo, "checkout", "-q", "-b", "pr")
    fixed_tests = OLD_TESTS + "\ndef test_add():\n    assert add(2, 3) == 5\n"
    commit(repo, {"calc.py": FIXED, "tests/test_calc.py": fixed_tests}, "fix")
    sh(repo, "checkout", "-q", "main")
    commit(repo, {"README": "unrelated\n"}, "unrelated change on main")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    sh(repo, "-c", "user.name=t", "-c", "user.email=t@t", "merge", "--no-ff", "-q", "-m", "merge pr", "pr")
    clone = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{repo}", str(clone)], check=True)
    sh(clone, "fetch", "-q", "--depth", "1", "origin", base)
    report = check(clone, base, "HEAD", sys.executable, 120, [])
    assert report["verdict"] == PROVES_FIX
    assert report["plan"]["code_files_changed"] == ["calc.py"]


def test_uncollectable_target_falls_back_to_whole_files(repo):
    """pytest runs nothing when one selected id is not collected; fall back to files."""
    tests = OLD_TESTS + textwrap.dedent(
        """

        def test_add():
            assert add(2, 3) == 5

        class TestNotCollected:
            def __init__(self):
                pass

            def test_y(self):
                assert True
        """
    )
    report = verdict_for(repo, {"calc.py": FIXED, "tests/test_calc.py": tests})
    assert report["selection"] == "files"
    assert report["verdict"] == PROVES_FIX
    heads = {r["test"].split("::", 1)[1]: r["head"] for r in report["rows"]}
    assert heads == {"test_add": "passed", "TestNotCollected::test_y": "missing"}


def test_runs_only_the_selected_tests_by_default(repo):
    tests = OLD_TESTS + "\ndef test_add():\n    assert add(2, 3) == 5\n"
    report = verdict_for(repo, {"calc.py": FIXED, "tests/test_calc.py": tests})
    assert report["selection"] == "tests"
    assert "1 passed" in report["head_run"]["tail"]
