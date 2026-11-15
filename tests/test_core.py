from redfirst.core import (
    FAILS_BY_ERROR,
    HEAD_NOT_GREEN,
    INCONCLUSIVE,
    PASSES_WITHOUT_FIX,
    PROVES_FIX,
    Change,
    changed_tests,
    fold,
    is_test_path,
    judge,
    split_changes,
    scan_tests,
)


def test_test_paths():
    assert is_test_path("tests/test_a.py")
    assert is_test_path("pkg/tests/data/input.json")
    assert is_test_path("test_top.py")
    assert is_test_path("pkg/thing_test.py")
    assert is_test_path("conftest.py")
    assert not is_test_path("pkg/testing_utils.py")
    assert not is_test_path("pkg/core.py")
    assert not is_test_path("docs/testing.md")


def test_split_marks_compiled_sources():
    split = split_changes([Change("M", "src/a.py"), Change("M", "src/fast.pyx"), Change("A", "tests/test_a.py")])
    assert [c.path for c in split.tests] == ["tests/test_a.py"]
    assert [c.path for c in split.compiled] == ["src/fast.pyx"]
    assert [c.path for c in split.python_code] == ["src/a.py"]


SRC = '''
import unittest
import pytest

def helper():
    return 1

def test_plain():
    assert helper() == 1

@pytest.mark.parametrize("x", [1, 2])
def test_param(x):
    assert x

class TestGroup:
    def test_inside(self):
        assert True

    class TestNested:
        def test_deep(self):
            assert True

class Legacy(unittest.TestCase):
    def test_old(self):
        self.assertTrue(True)

class NotATest:
    def test_ignored(self):
        pass
'''


def test_scan_finds_pytest_names():
    names = set(scan_tests(SRC))
    assert names == {"test_plain", "test_param", "TestGroup::test_inside", "TestGroup::TestNested::test_deep", "Legacy::test_old"}


def test_changed_tests_sees_new_param_case_but_not_comments():
    edited_param = SRC.replace("[1, 2]", "[1, 2, 3]")
    comment_only = SRC.replace("def test_plain():", "def test_plain():  # a comment")
    added = SRC + "\ndef test_new():\n    assert True\n"
    assert changed_tests(SRC, edited_param) == ([], ["test_param"])
    assert changed_tests(SRC, comment_only) == ([], [])
    assert changed_tests(SRC, added) == (["test_new"], [])
    assert changed_tests(None, "def test_a():\n    pass\n") == (["test_a"], [])


def run(**tests):
    return {"tests": {k: {"outcome": v} for k, v in tests.items()}, "collect_errors": []}


T = {"tests/test_a.py::test_new": "added"}


def test_fold_order():
    assert fold([{"outcome": "passed"}, {"outcome": "failed"}], False) == "failed"
    assert fold([{"outcome": "passed"}, {"outcome": "skipped"}], False) == "passed"
    assert fold([], True) == "error"
    assert fold([], False) == "missing"


def test_judge_proves_fix_with_parametrized_case():
    head = run(**{"tests/test_a.py::test_new[1]": "passed", "tests/test_a.py::test_new[2]": "passed"})
    base = run(**{"tests/test_a.py::test_new[1]": "passed", "tests/test_a.py::test_new[2]": "failed"})
    verdict, rows = judge(T, head, base)
    assert verdict == PROVES_FIX
    assert rows[0].base == "failed"


def test_judge_passes_without_fix():
    verdict, _ = judge(T, run(**{"tests/test_a.py::test_new": "passed"}), run(**{"tests/test_a.py::test_new": "passed"}))
    assert verdict == PASSES_WITHOUT_FIX


def test_judge_collection_error_without_fix():
    base = {"tests": {}, "collect_errors": [{"nodeid": "tests/test_a.py", "message": "ImportError: cannot import name 'x'"}]}
    verdict, rows = judge(T, run(**{"tests/test_a.py::test_new": "passed"}), base)
    assert verdict == FAILS_BY_ERROR
    assert "ImportError" in rows[0].detail


def test_judge_head_not_green_and_skips():
    assert judge(T, run(**{"tests/test_a.py::test_new": "failed"}), None)[0] == HEAD_NOT_GREEN
    verdict, _ = judge(T, run(**{"tests/test_a.py::test_new": "passed"}), run(**{"tests/test_a.py::test_new": "skipped"}))
    assert verdict == INCONCLUSIVE


def test_judge_matches_nodeids_relative_to_a_subdirectory_rootdir():
    targets = {"python/tests/test_a.py::TestX::test_y": "added"}
    head = run(**{"tests/test_a.py::TestX::test_y": "passed"})
    base = run(**{"tests/test_a.py::TestX::test_y": "failed"})
    assert judge(targets, head, base)[0] == PROVES_FIX
