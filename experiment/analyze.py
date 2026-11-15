"""Summarise replay results: verdict counts per group, the primary share with
Wilson 95% intervals, the paired table, per-agent and per-test shares.

    python analyze.py --results ~/.cache/f2p-work/results --jobs sample/ --out results/summary.json
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

JUDGED = ("proves_fix", "fails_by_error", "passes_without_fix", "inconclusive")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def share(k: int, n: int) -> dict:
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "share": round(k / n, 4) if n else None, "ci95": [round(lo, 4), round(hi, 4)]}


def load(results: Path, jobs: list[dict]) -> list[dict]:
    out = []
    for job in jobs:
        f = results / job["id"] / "result.json"
        rec = json.loads(f.read_text()) if f.exists() else {"job": job, "verdict": "not_run"}
        rec["job"] = job
        out.append(rec)
    return out


def summarise(records: list[dict]) -> dict:
    by_group = defaultdict(list)
    for r in records:
        by_group[r["job"]["group"]].append(r)
    summary: dict = {"groups": {}}
    for group, recs in sorted(by_group.items()):
        verdicts = Counter(r["verdict"] for r in recs)
        judged = [r for r in recs if r["verdict"] in JUDGED]
        pwf = sum(r["verdict"] == "passes_without_fix" for r in judged)
        tests_total = tests_pass = 0
        for r in judged:
            for row in r.get("rows", []):
                if row["head"] == "passed":
                    tests_total += 1
                    tests_pass += row["base"] == "passed"
        g = {
            "jobs": len(recs),
            "verdicts": dict(verdicts.most_common()),
            "judged": len(judged),
            "passes_without_fix": share(pwf, len(judged)),
            "fails_by_error": share(sum(r["verdict"] == "fails_by_error" for r in judged), len(judged)),
            "tests_passing_without_fix": share(tests_pass, tests_total),
        }
        if group == "agent":
            per_agent = defaultdict(lambda: [0, 0])
            for r in judged:
                per_agent[r["job"]["agent"]][1] += 1
                per_agent[r["job"]["agent"]][0] += r["verdict"] == "passes_without_fix"
            g["by_agent"] = {a: share(k, n) for a, (k, n) in sorted(per_agent.items())}
        summary["groups"][group] = g
    agent = {r["job"]["id"]: r for r in by_group.get("agent", [])}
    pairs = Counter()
    for h in by_group.get("human", []):
        a = agent.get(h["job"].get("pair"))
        if a and a["verdict"] in JUDGED and h["verdict"] in JUDGED:
            pairs[(a["verdict"] == "passes_without_fix", h["verdict"] == "passes_without_fix")] += 1
    n_pairs = sum(pairs.values())
    summary["paired"] = {
        "pairs": n_pairs,
        "agent_pwf_human_pwf": pairs[(True, True)],
        "agent_pwf_only": pairs[(True, False)],
        "human_pwf_only": pairs[(False, True)],
        "neither": pairs[(False, False)],
    }
    if n_pairs:
        a_share = (pairs[(True, True)] + pairs[(True, False)]) / n_pairs
        h_share = (pairs[(True, True)] + pairs[(False, True)]) / n_pairs
        summary["paired"]["agent_share"] = round(a_share, 4)
        summary["paired"]["human_share"] = round(h_share, 4)
        b, c = pairs[(True, False)], pairs[(False, True)]
        summary["paired"]["mcnemar_exact_p"] = round(_mcnemar(b, c), 4)
    return summary


def _mcnemar(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True, type=Path)
    ap.add_argument("--jobs", required=True, type=Path, help="folder with jobs_agent.jsonl and jobs_human.jsonl")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    jobs = []
    for name in ("jobs_agent.jsonl", "jobs_human.jsonl"):
        f = args.jobs / name
        if f.exists():
            jobs += [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
    records = load(args.results, jobs)
    summary = summarise(records)
    text = json.dumps(summary, indent=1)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
