# Labelling rubric

The exact instructions given to the model that labelled the blind packets. {DIR}, {N} and {OUT} were filled with the packet folder, the number of cases and the output file.

```text
You are labelling cases for a software testing study. Work alone, read only the files named here, and do not run code, open URLs or search the web.

Folder: {DIR}
It holds files case-01.md ... case-{N}.md. Each case is one merged bug-fix pull request in a Python project. The pull request added or edited tests. A harness ran those tests twice in a container: once on the pull request ("head"), and once with every non-test file put back the way it was before the pull request ("base"), keeping the new tests. In every case here, all new tests that passed at head also passed at base. Each file shows the pull request title, the per-test outcomes, the diff of the non-test files ("Code side") and the diff of the test modules that hold the new tests. Long diffs are cut at 400 lines.

For each case, decide why the new tests did not notice the change. Pick exactly one label:

- gap: the changed behaviour could be checked by a unit test, but the new tests do not check it. This includes tests that test something else, tests that run the changed code but assert nothing that differs between before and after (for example a test that only returns True or only checks that no exception was raised where the old code raised none either), and tests that were edited so they accept both the old and the new behaviour.
- unobservable: the change cannot be seen by a unit test: log or message wording, comments or docstrings, type hints or annotations, performance, formatting or lint fixes, a pure refactor under a fix title.
- environment: the bug only shows on another platform, Python version, dependency version or external service, so a test in a plain Linux container would pass either way.
- test_side_fix: the fix itself is in a file the harness counts as test side (under a test, tests or testing directory, a test_*.py or *_test.py module, or conftest.py), so undoing the non-test files does not undo the fix.
- other: none of the above, for example the only code change is a dependency pin or a configuration file the tests never read. Say what it is.

Be strict about gap: use it only when you can point to the behaviour the code change altered and say why the new tests would pass on the old code. If the code diff is cut and you cannot tell, say so and pick the label the visible evidence supports.

Also record accommodating_edit: true when an existing test was edited so that it now holds for both the old and the new code (for example replacing a literal with the value it is compared to), else false.

Write your answer as one JSON object to {OUT}, keyed by case name:

{"case-01": {"label": "gap", "accommodating_edit": false, "note": "one or two sentences: what the code change altered, and why the new tests pass without it"}, ...}

Then reply with only the label counts, one line per label.
```
