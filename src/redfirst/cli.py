"""Command line: ``redfirst check --base origin/main``.

Runs the tests a change added or edited twice in the current environment:
once as checked out, once with the non-test side of the change undone.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .core import (
    FAILS_BY_ERROR,
    HEAD_NOT_GREEN,
    INCONCLUSIVE,
    NEEDS_REBUILD,
    NO_CODE_CHANGE,
    NO_NEW_TESTS,
    PASSES_WITHOUT_FIX,
    PROVES_FIX,
    judge,
)
from .repo import GitError, code_at_base, git, make_plan

SRC_DIR = str(Path(__file__).resolve().parent.parent)

HEADLINES = {
    PROVES_FIX: "The new tests fail without the code change. They test the change.",
    FAILS_BY_ERROR: "The new tests error without the code change (import, fixture or collection error), so they depend on it, but no assertion caught the old behaviour.",
    PASSES_WITHOUT_FIX: "The new tests pass even with the code change undone. They do not test what the change changed.",
    INCONCLUSIVE: "The new tests were skipped or did not report without the code change. No evidence either way.",
    HEAD_NOT_GREEN: "The new tests do not pass with the change, so there is nothing to compare.",
    NO_NEW_TESTS: "The change adds or edits no test functions.",
    NO_CODE_CHANGE: "Only tests changed. There is no code change to undo.",
    NEEDS_REBUILD: "Compiled sources changed. Undoing them needs a rebuild, which redfirst does not do.",
}


def run_pytest(python: str, files: list[str], cwd: Path, timeout: int, extra: list[str]) -> dict:
    with tempfile.TemporaryDirectory(prefix="redfirst-") as tmp:
        result = Path(tmp) / "result.json"
        env = dict(os.environ)
        env["REDFIRST_RESULT"] = str(result)
        env["PYTHONPATH"] = SRC_DIR + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
        cmd = [python, "-m", "pytest", "-p", "redfirst.plugin", "-p", "no:cacheprovider", "-q", "-rN", *extra, *files]
        started = time.monotonic()
        try:
            proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
            tail, code = (proc.stdout + proc.stderr)[-3000:], proc.returncode
        except subprocess.TimeoutExpired as exc:
            out = (exc.stdout or b"") + (exc.stderr or b"")
            tail = (out.decode("utf-8", "replace") if isinstance(out, bytes) else str(out))[-3000:]
            code = "timeout"
        data = json.loads(result.read_text()) if result.exists() else None
        if data is None:
            data = {"tests": {}, "collect_errors": [{"nodeid": "", "message": f"pytest did not finish ({code})"}]}
        data["returncode"] = code
        data["seconds"] = round(time.monotonic() - started, 1)
        data["tail"] = tail
        return data


def check(repo: Path, base: str, head: str, python: str, timeout: int, extra: list[str]) -> dict:
    plan = make_plan(repo, base, head)
    report: dict = {"tool": f"redfirst {__version__}", "plan": plan.summary(), "rows": []}
    if plan.early:
        report["verdict"] = plan.early
        return report
    head_run = run_pytest(python, plan.files, repo, timeout, extra)
    report["head_run"] = {k: head_run[k] for k in ("returncode", "seconds", "tail")}
    verdict, rows = judge(plan.targets, head_run, None) if _nothing_green(plan, head_run) else (None, None)
    base_run = None
    if verdict is None:
        with code_at_base(repo, plan):
            base_run = run_pytest(python, plan.files, repo, timeout, extra)
        report["base_run"] = {k: base_run[k] for k in ("returncode", "seconds", "tail")}
        verdict, rows = judge(plan.targets, head_run, base_run)
    report["verdict"] = verdict
    report["rows"] = [asdict(r) for r in rows]
    return report


def _nothing_green(plan, head_run) -> bool:
    verdict, _ = judge(plan.targets, head_run, {"tests": {}, "collect_errors": []})
    return verdict == HEAD_NOT_GREEN


def render_text(report: dict) -> str:
    verdict = report["verdict"]
    lines = [f"redfirst: {verdict}", HEADLINES.get(verdict, "")]
    rows = report.get("rows") or []
    if rows:
        lines.append("")
        width = max(len(r["test"]) for r in rows)
        for r in rows:
            base = r["base"] or "-"
            lines.append(f"  {r['test']:<{width}}  {r['kind']:<6}  with change: {r['head']:<8}  without: {base}")
            if r.get("detail") and r["base"] in ("failed", "error"):
                lines.append(f"  {'':<{width}}  {r['detail']}")
    return "\n".join(lines)


def render_markdown(report: dict) -> str:
    verdict = report["verdict"]
    out = [f"### redfirst: `{verdict}`", "", HEADLINES.get(verdict, "")]
    rows = report.get("rows") or []
    if rows:
        out += ["", "| test | kind | with the change | without it |", "| --- | --- | --- | --- |"]
        for r in rows:
            out.append(f"| `{r['test']}` | {r['kind']} | {r['head']} | {r['base'] or '-'} |")
    out += ["", f"<sub>{report['tool']} compared {report['plan']['base'][:10]}..{report['plan']['head'][:10]}</sub>"]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="redfirst", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="check the change between --base and --head")
    c.add_argument("--base", required=True, help="branch or commit the change started from")
    c.add_argument("--head", default="HEAD")
    c.add_argument("--repo", default=".")
    c.add_argument("--python", default=sys.executable, help="interpreter of the project's environment")
    c.add_argument("--timeout", type=int, default=900, help="seconds per test run")
    c.add_argument("--mode", choices=("warn", "fail"), default="warn",
                   help="fail: exit 1 when the new tests pass without the change")
    c.add_argument("--json", dest="json_out", help="write the full report here")
    c.add_argument("--summary", help="append a markdown report (e.g. $GITHUB_STEP_SUMMARY)")
    c.add_argument("--allow-dirty", action="store_true", help="run with uncommitted changes")
    c.add_argument("pytest_args", nargs="*", help="extra pytest arguments after --")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    try:
        if not args.allow_dirty and git(repo, "status", "--porcelain", "--untracked-files=no").strip():
            print("redfirst: the working tree has uncommitted changes; commit them or pass --allow-dirty", file=sys.stderr)
            return 2
        report = check(repo, args.base, args.head, args.python, args.timeout, args.pytest_args)
    except GitError as exc:
        print(f"redfirst: {exc}", file=sys.stderr)
        return 2
    print(render_text(report))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2) + "\n")
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write(render_markdown(report) + "\n")
    if args.mode == "fail" and report["verdict"] == PASSES_WITHOUT_FIX:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
