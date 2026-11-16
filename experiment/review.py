"""Write one review packet per pull request whose new tests passed without
the change, for the manual labelling step in PROTOCOL.md.

    python review.py --results ~/.cache/f2p-work/results --out review/

Each packet has the pull request, the target tests with their outcomes, the
code-side diff and the diff of the test modules that hold the targets. Labels
go into review/labels.json by hand: {"<job id>": {"label": "gap", "note": "..."}}.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

WORK = Path(os.environ.get("RF_WORK", "/Users/gosmain/.cache/f2p-work"))
LABELS = ("gap", "unobservable", "environment", "test_side_fix", "other")
MAX_LINES = 400


def git_diff(clone: Path, base: str, head: str, paths: list[str]) -> str:
    if not paths:
        return ""
    proc = subprocess.run(
        ["git", "diff", "--no-renames", "--no-color", base, head, "--", *paths],
        cwd=clone, capture_output=True, text=True, env=dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null"),
    )
    lines = proc.stdout.splitlines()
    if len(lines) > MAX_LINES:
        lines = lines[:MAX_LINES] + [f"... {len(lines) - MAX_LINES} more lines"]
    return "\n".join(lines)


def packet(rec: dict, case: str | None = None) -> str:
    """case: a neutral name for blind labelling; hides the group, the agent
    and the pull request link so the labeller cannot tell agent from human."""
    job, plan = rec["job"], rec["plan"]
    clone = WORK / "repos" / job["repo"].replace("/", "__")
    if case:
        head = [f"# {case}", "", f"- title: {job.get('title')}"]
    else:
        head = [
            f"# {job['id']}",
            "",
            f"- {job['group']} / {job.get('agent')}: {job.get('title')}",
            f"- {job.get('url')} merged {job.get('merged_at')}",
            f"- selection: {rec.get('selection')}, python {rec.get('python')}",
        ]
    out = head + [
        "",
        "| test | kind | head | base |",
        "| --- | --- | --- | --- |",
    ]
    for row in rec.get("rows", []):
        out.append(f"| `{row['test']}` | {row['kind']} | {row['head']} | {row['base']} |")
    target_files = sorted({t.split("::", 1)[0] for t in plan["targets"]})
    out += ["", "## Code side", "", "```diff", git_diff(clone, plan["base"], plan["head"], plan["code_files_changed"]), "```"]
    out += ["", "## Target test modules", "", "```diff", git_diff(clone, plan["base"], plan["head"], target_files), "```", ""]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=str(WORK / "results"))
    ap.add_argument("--out", default="review")
    ap.add_argument("--verdict", default="passes_without_fix")
    ap.add_argument("--blind", help="write neutral case names here instead, with the key in this file")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    recs = [json.loads(f.read_text()) for f in sorted(Path(args.results).glob("*/result.json"))]
    recs = [r for r in recs if r.get("verdict") == args.verdict]
    key = {}
    if args.blind:
        order = sorted(recs, key=lambda r: hashlib.sha256(f"redfirst-blind:{r['job']['id']}".encode()).hexdigest())
        for i, rec in enumerate(order, 1):
            case = f"case-{i:02d}"
            key[case] = rec["job"]["id"]
            (out / f"{case}.md").write_text(packet(rec, case))
        Path(args.blind).write_text(json.dumps(key, indent=1) + "\n")
    else:
        for rec in recs:
            (out / f"{rec['job']['id']}.md").write_text(packet(rec))
    n = len(recs)
    labels = out / "labels.json"
    if not labels.exists():
        labels.write_text("{}\n")
    print(f"{n} packets in {out}; labels: {', '.join(LABELS)}")


if __name__ == "__main__":
    main()
