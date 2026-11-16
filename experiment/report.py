"""Render the result tables in RESULTS.md from results/summary.json and
review/labels.json, so the numbers in the write-up come from the data.

    python report.py --summary results/summary.json --labels review/labels.json \
        --jobs sample/ > results/tables.md
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from analyze import share

ORDER = (
    "proves_fix", "fails_by_error", "passes_without_fix", "inconclusive",
    "head_not_green", "env_failed", "no_new_tests", "no_code_change",
    "needs_rebuild", "harness_error", "not_run",
)
LABELS = ("gap", "unobservable", "environment", "test_side_fix", "other")
AGENT_NAMES = {"Claude_Code": "Claude Code", "Copilot": "Copilot", "OpenAI_Codex": "Codex", "Devin": "Devin",
               "Cursor": "Cursor", "Jules": "Jules"}


def pct(s: dict) -> str:
    if not s["n"]:
        return "-"
    lo, hi = s["ci95"]
    return f"{s['k']} of {s['n']} ({s['share'] * 100:.1f}%, 95% CI {lo * 100:.1f} to {hi * 100:.1f}%)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", required=True, type=Path)
    ap.add_argument("--labels", required=True, type=Path)
    ap.add_argument("--jobs", required=True, type=Path)
    args = ap.parse_args()
    summary = json.loads(args.summary.read_text())
    labels = json.loads(args.labels.read_text())
    groups = summary["groups"]
    names = [g for g in ("agent", "human") if g in groups]

    group_of = {}
    for name in ("jobs_agent.jsonl", "jobs_human.jsonl"):
        for line in (args.jobs / name).read_text().splitlines():
            if line.strip():
                job = json.loads(line)
                group_of[job["id"]] = job["group"]
    by_label = {g: Counter() for g in names}
    for job_id, entry in labels.items():
        by_label[group_of[job_id]][entry["label"]] += 1

    out = ["| verdict | " + " | ".join(names) + " |", "| --- |" + " --- |" * len(names)]
    for v in ORDER:
        counts = [groups[g]["verdicts"].get(v, 0) for g in names]
        if any(counts):
            out.append(f"| `{v}` | " + " | ".join(str(c) for c in counts) + " |")
    out.append("| total | " + " | ".join(str(groups[g]["jobs"]) for g in names) + " |")
    out += ["", "| | " + " | ".join(names) + " |", "| --- |" + " --- |" * len(names)]
    out.append("| judged | " + " | ".join(str(groups[g]["judged"]) for g in names) + " |")
    out.append("| all new tests pass without the change | " + " | ".join(pct(groups[g]["passes_without_fix"]) for g in names) + " |")
    gap = {g: share(by_label[g]["gap"], groups[g]["judged"]) for g in names}
    out.append("| of judged, labelled `gap` | " + " | ".join(pct(gap[g]) for g in names) + " |")
    out.append("| new tests that pass without the change, per test | " + " | ".join(pct(groups[g]["tests_passing_without_fix"]) for g in names) + " |")
    out.append("| `fails_by_error` | " + " | ".join(pct(groups[g]["fails_by_error"]) for g in names) + " |")

    out += ["", "| label | " + " | ".join(names) + " |", "| --- |" + " --- |" * len(names)]
    for lab in LABELS:
        out.append(f"| `{lab}` | " + " | ".join(str(by_label[g][lab]) for g in names) + " |")

    p = summary.get("paired", {})
    if p.get("pairs"):
        out += ["", "| both judged, same repository | count |", "| --- | --- |",
                f"| pairs | {p['pairs']} |",
                f"| both pass without the change | {p['agent_pwf_human_pwf']} |",
                f"| only the agent pull request | {p['agent_pwf_only']} |",
                f"| only the human pull request | {p['human_pwf_only']} |",
                f"| neither | {p['neither']} |",
                f"| exact McNemar p | {p['mcnemar_exact_p']} |"]

    by_agent = groups.get("agent", {}).get("by_agent", {})
    if by_agent:
        out += ["", "| agent | judged | pass without the change |", "| --- | --- | --- |"]
        for a, s in sorted(by_agent.items(), key=lambda kv: -kv[1]["n"]):
            out.append(f"| {AGENT_NAMES.get(a, a)} | {s['n']} | {s['k']} |")

    hng = {g: groups[g].get("head_not_green_by_outcome", {}) for g in names}
    out += ["", "| why no new test passed with the change | " + " | ".join(names) + " |", "| --- |" + " --- |" * len(names)]
    for k in ("error", "failed", "skipped", "missing"):
        out.append(f"| {k} | " + " | ".join(str(hng[g].get(k, 0)) for g in names) + " |")
    print("\n".join(out))


if __name__ == "__main__":
    main()
