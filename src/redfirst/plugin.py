"""pytest plugin: write every test's outcome to the JSON file named by
the REDFIRST_RESULT environment variable.

The command line copies this file into a temporary folder and loads it as
``-p redfirst_plugin``; it can also be loaded as ``-p redfirst.plugin``. It is
standalone on purpose: it runs inside the project's own environment, which
may be an old Python, and it must not import anything that environment lacks.
"""

import json
import os

_tests = {}
_collect_errors = []


def _crash_line(report):
    crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
    message = getattr(crash, "message", None)
    if message:
        lines = str(message).strip().splitlines()
        if lines:
            return lines[0][:300]
    return _last_error_line(report)


def _last_error_line(report):
    text = str(getattr(report, "longrepr", "") or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    marked = [line[1:].strip() for line in lines if line.startswith("E ")]
    if marked:
        return marked[-1][:300]
    return lines[-1][:300] if lines else ""


def pytest_runtest_logreport(report):
    entry = _tests.setdefault(report.nodeid, {"outcome": None})
    wasxfail = hasattr(report, "wasxfail")
    if report.when == "call":
        if wasxfail:
            outcome = "xfailed" if report.skipped else "xpassed"
        else:
            outcome = report.outcome
        if entry["outcome"] in (None, "passed"):
            entry["outcome"] = outcome
            if outcome == "failed":
                entry["message"] = _crash_line(report)
    elif report.failed:
        if entry["outcome"] != "failed":
            entry["outcome"] = "error"
            entry["message"] = _crash_line(report)
    elif report.when == "setup" and report.skipped:
        entry["outcome"] = "xfailed" if wasxfail else "skipped"


def pytest_collectreport(report):
    if report.failed:
        _collect_errors.append({"nodeid": report.nodeid, "message": _last_error_line(report)})


def pytest_sessionfinish(session, exitstatus):
    if hasattr(session.config, "workerinput"):  # pytest-xdist worker
        return
    path = os.environ.get("REDFIRST_RESULT")
    if not path:
        return
    tests = dict((k, v) for k, v in _tests.items() if v.get("outcome") is not None)
    with open(path, "w") as fh:
        json.dump(
            {"tests": tests, "collect_errors": _collect_errors, "exitstatus": int(exitstatus)},
            fh,
        )
