"""Write the per pull request table that goes with the summary: one row per
job, agent and human, with the verdict and the manual label where there is
one.

    python export.py --results ~/.cache/f2p-work/results --jobs sample/ \
        --labels review/labels.json --out results/prs.csv
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

COLUMNS = (
    "group", "repo", "number", "url", "agent", "merged_at", "pair",
    "verdict", "targets", "passed_with_change", "passed_without_change",
    "label", "python", "project_dir",
)


def row(job: dict, rec: dict | None, labels: dict) -> dict:
    rec = rec or {}
    rows = rec.get("rows") or []
    passed = [r for r in rows if r.get("head") == "passed"]
    label = labels.get(job["id"], {})
    return {
        "group": job["group"],
        "repo": job["repo"],
        "number": job["number"],
        "url": job["url"],
        "agent": job.get("agent") or "",
        "merged_at": job.get("merged_at", ""),
        "pair": job.get("pair", ""),
        "verdict": rec.get("verdict", "not_run"),
        "targets": len(rows),
        "passed_with_change": len(passed),
        "passed_without_change": sum(r.get("base") == "passed" for r in passed),
        "label": label.get("label", ""),
        "python": rec.get("python") or "",
        "project_dir": rec.get("project_dir") or "",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True, type=Path)
    ap.add_argument("--jobs", required=True, type=Path)
    ap.add_argument("--labels", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    labels = json.loads(args.labels.read_text()) if args.labels and args.labels.exists() else {}
    out_rows = []
    for name in ("jobs_agent.jsonl", "jobs_human.jsonl"):
        f = args.jobs / name
        if not f.exists():
            continue
        for line in f.read_text().splitlines():
            if not line.strip():
                continue
            job = json.loads(line)
            res = args.results / job["id"] / "result.json"
            out_rows.append(row(job, json.loads(res.read_text()) if res.exists() else None, labels))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(out_rows)
    print(f"{len(out_rows)} rows -> {args.out}")


if __name__ == "__main__":
    main()
