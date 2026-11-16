# redfirst

A test that passes before the fix does not test the fix.

redfirst takes the tests a pull request adds or edits, runs them on the
pull request, then puts every non-test file back the way it was before the
change and runs them again. If they still pass, they would not have caught
the bug, and the pull request ships a fix that nothing checks.

A pull request makes retry backoff exponential and adds one test:

```python
def test_backoff_is_capped():
    assert backoff(100) == 30.0
```

The old code capped the wait too, so the test passes either way:

```
$ redfirst check --base main
redfirst: passes_without_fix
The new tests pass even with the code change undone. They do not test what the change changed.

  tests/test_retry.py::test_backoff_is_capped  added   with change: passed    without: passed
```

Add a test of the growth and it goes red without the change:

```
$ redfirst check --base main
redfirst: proves_fix
The new tests fail without the code change. They test the change.

  tests/test_retry.py::test_backoff_doubles    added   with change: passed    without: failed
                                               assert [0.5, 1.0, 1.5] == [0.5, 1.0, 2.0]
  tests/test_retry.py::test_backoff_is_capped  added   with change: passed    without: passed
```

<!-- RESULTS -->

## Use it in GitHub Actions

Add a step after the one that installs your test environment:

```yaml
on: pull_request

jobs:
  redfirst:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[test]"        # however you install your tests
      - uses: mandu5/redfirst@v0.1.0
        with:
          mode: warn                         # or fail
```

The step writes a table to the job summary, sets the `verdict` output, and
adds a warning to the pull request when every new test passes without the
fix. With `mode: fail` it fails the job instead.

Inputs: `python` (the interpreter that has your test dependencies, default
`python`), `mode` (`warn` or `fail`), `pytest-args` (extra arguments), and
`base` (defaults to the pull request base).

## Use it locally

```
pipx install git+https://github.com/mandu5/redfirst
cd your-repo
redfirst check --base origin/main --python .venv/bin/python
```

It refuses to run on a dirty working tree, and puts every file back when it
is done, also after a crash.

## What it does

1. Diffs the merge base of `--base` and `--head` against `--head`, and
   splits the changed files into the test side (files under `test`, `tests`
   or `testing`, `test_*.py`, `*_test.py`, `conftest.py`) and the code side
   (everything else).
2. Finds the test functions the change added or edited, by comparing syntax
   trees, decorators included: a new `parametrize` case counts, a comment
   does not.
3. Runs those tests with the change. Then restores the code side to the
   merge base, deletes files the change added, keeps the test side as it is,
   and runs the same tests again.
4. Reports one verdict:

| verdict | meaning |
| --- | --- |
| `proves_fix` | at least one new test fails without the change |
| `fails_by_error` | none fails, at least one errors without the change (import, fixture, collection) |
| `passes_without_fix` | every new test that passes with the change also passes without it |
| `inconclusive` | without the change the tests were skipped or did not report |
| `head_not_green` | no new test passes with the change, so there is nothing to compare |
| `no_new_tests` | the change adds or edits no test functions |
| `no_code_change` | only tests changed |
| `needs_rebuild` | compiled sources changed (C, C++, Cython, Rust and others) |

## When passing without the fix is fine

Some changes cannot be seen by a unit test: a log message, a type hint, a
performance change, a refactor under a fix title. Some bugs only show on
another platform or Python version. redfirst tells you the new tests do
not exercise the change; whether that matters is your call.

## Limits

- Python and pytest only.
- Dependency changes are not undone: the environment stays the one you
  installed for the pull request.
- Changes to compiled sources need a rebuild, which redfirst does not do.
- It runs the new tests twice, so it costs that much test time. Only the
  added or edited tests run, not your whole suite.

<!-- STUDY -->

## A hosted version

A GitHub App that runs this on every pull request, with no CI changes, is
being considered. If you would use it, react or comment on
[issue #1](https://github.com/mandu5/redfirst/issues/1).

## License

MIT
