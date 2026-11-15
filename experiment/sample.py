"""Draw the study sample: merged bug-fix pull requests by coding agents in
popular Python repositories (AIDev v5, AIDev-pop), one per repository, and
for each one the human bug-fix pull request in the same repository merged
closest in time.

    python sample.py --aidev DIR --out sample/ --agents 120

Deterministic for a given AIDev snapshot and GitHub state. Every inspected
candidate is logged with the reason it was kept or skipped.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
from redfirst.core import COMPILED_SUFFIXES, is_test_module, is_test_path  # noqa: E402

SEED = 20261116
WINDOW = ("2025-01-01", "2026-03-31T23:59:59Z")
MAX_FILES = 50
HUMAN_DAYS = 180
FIX = re.compile(r"\bfix(es|ed|ing)?\b|\bbug(fix)?\b|\bhotfix\b", re.I)
NOT_FIX = re.compile(r"^\s*(\[[^\]]*\]\s*)?(feat|docs?|test|tests|chore|refactor|ci|build|style|perf|revert)(\([^)]*\))?!?\s*:", re.I)
AGENT_BRANCH = re.compile(r"^(codex|copilot|cursor|claude|devin|jules|sweep|openhands|aider)[/-]", re.I)
AGENT_BODY = re.compile(
    r"generated with \[?claude code|co-authored-by: claude|chatgpt\.com/codex|devin\.ai|cursor\.com/agents|"
    r"co-authored-by: copilot|openhands|jules\.google|sweep\.dev|aider",
    re.I,
)


def is_fix_title(title: str) -> bool:
    return bool(FIX.search(title or "")) and not NOT_FIX.search(title or "")


def gh(path: str, retries: int = 4):
    for attempt in range(retries):
        proc = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github+json", path], capture_output=True, text=True)
        if proc.returncode == 0:
            return json.loads(proc.stdout)
        err = proc.stderr
        if "rate limit" in err.lower() or "secondary" in err.lower():
            time.sleep(30 * (attempt + 1))
            continue
        if "Not Found" in err or "404" in err:
            return None
        time.sleep(3)
    return None


def files_of(repo: str, number: int) -> list[dict] | None:
    out, page = [], 1
    while True:
        batch = gh(f"repos/{repo}/pulls/{number}/files?per_page=100&page={page}")
        if batch is None:
            return None
        out += batch
        if len(batch) < 100 or page >= 5:
            return out
        page += 1


def eligibility(files: list[dict]) -> str | None:
    """None when eligible, else the reason."""
    if len(files) > MAX_FILES:
        return "too_many_files"
    tests = [f for f in files if is_test_path(f["filename"])]
    code = [f for f in files if not is_test_path(f["filename"])]
    if any(Path(f["filename"]).suffix.lower() in COMPILED_SUFFIXES for f in code):
        return "compiled_sources"
    if not any(f["filename"].endswith(".py") and f["status"] in ("added", "modified") for f in code):
        return "no_python_code_change"
    if not any(is_test_module(f["filename"]) and f["status"] in ("added", "modified") for f in tests):
        return "no_test_module_change"
    return None


def job_from(pr: dict, repo: str, group: str, agent: str | None) -> dict:
    owner_name = repo.replace("/", "__")
    return {
        "id": f"{group}-{owner_name}-{pr['number']}",
        "group": group,
        "agent": agent,
        "repo": repo,
        "number": pr["number"],
        "title": pr["title"],
        "url": pr["html_url"],
        "author": pr["user"]["login"],
        "base_sha": pr["base"]["sha"],
        "head_sha": pr["head"]["sha"],
        "head_ref": pr["head"]["ref"],
        "merged_at": pr["merged_at"],
    }


def draw_agents(aidev: Path, n: int, log) -> list[dict]:
    pr = pd.read_parquet(aidev / "pull_request.parquet", columns=["id", "number", "title", "agent", "state", "created_at", "merged_at", "repo_id"])
    repo = pd.read_parquet(aidev / "repository.parquet")
    py = repo[(repo.language == "Python") & (~repo.is_forked.astype(bool))][["id", "full_name", "stars"]]
    frame = pr.merge(py.rename(columns={"id": "repo_id"}), on="repo_id")
    frame = frame[frame.merged_at.notna() & (frame.created_at >= WINDOW[0]) & (frame.created_at <= WINDOW[1])]
    frame = frame[frame.title.fillna("").map(is_fix_title)].sort_values("id").reset_index(drop=True)
    order = np.random.default_rng(SEED).permutation(len(frame))
    log({"event": "frame", "agent_fix_prs": len(frame), "repos": int(frame.repo_id.nunique()),
         "by_agent": frame.agent.value_counts().to_dict()})
    jobs, used = [], set()
    for idx in order:
        row = frame.iloc[idx]
        if row.full_name in used:
            continue
        meta = gh(f"repos/{row.full_name}/pulls/{int(row.number)}")
        if meta is None or not meta.get("merged_at"):
            log({"event": "skip", "repo": row.full_name, "number": int(row.number), "reason": "unavailable"})
            continue
        files = files_of(row.full_name, int(row.number))
        reason = "unavailable" if files is None else eligibility(files)
        log({"event": "skip" if reason else "keep", "group": "agent", "repo": row.full_name,
             "number": int(row.number), "agent": row.agent, "reason": reason})
        if reason:
            continue
        used.add(row.full_name)
        jobs.append(job_from(meta, row.full_name, "agent", row.agent))
        if len(jobs) >= n:
            break
    return jobs


def agent_numbers(aidev: Path) -> dict[str, set[int]]:
    pr = pd.read_parquet(aidev / "pull_request.parquet", columns=["number", "repo_id"])
    repo = pd.read_parquet(aidev / "repository.parquet")[["id", "full_name"]]
    merged = pr.merge(repo.rename(columns={"id": "repo_id"}), on="repo_id")
    out: dict[str, set[int]] = {}
    for name, number in zip(merged.full_name, merged.number):
        out.setdefault(name, set()).add(int(number))
    return out


def looks_human(pr: dict, known_agent: set[int]) -> str | None:
    if pr["number"] in known_agent:
        return "aidev_agent_pr"
    user = pr.get("user") or {}
    if user.get("type") != "User" or user.get("login", "").endswith("[bot]"):
        return "bot_author"
    if AGENT_BRANCH.match(pr.get("head", {}).get("ref", "") or ""):
        return "agent_branch"
    if AGENT_BODY.search(pr.get("body") or ""):
        return "agent_marker_in_body"
    return None


def draw_human(agent_job: dict, known_agent: set[int], log) -> dict | None:
    repo = agent_job["repo"]
    t = datetime.fromisoformat(agent_job["merged_at"].replace("Z", "+00:00"))
    lo, hi = (t - timedelta(days=HUMAN_DAYS)).date(), (t + timedelta(days=HUMAN_DAYS)).date()
    seen: dict[int, dict] = {}
    for word in ("fix", "bug"):
        q = f"repo:{repo} is:pr is:merged merged:{lo}..{hi} {word} in:title"
        res = gh("search/issues?" + urllib.parse.urlencode({"q": q, "per_page": 100}))
        time.sleep(2.2)  # search API: 30 requests per minute
        for item in (res or {}).get("items", []):
            seen.setdefault(item["number"], item)
    cands = []
    for number, item in seen.items():
        if number == agent_job["number"] or not is_fix_title(item["title"]):
            continue
        merged_at = (item.get("pull_request") or {}).get("merged_at") or item.get("closed_at")
        cands.append((abs(datetime.fromisoformat(merged_at.replace("Z", "+00:00")) - t), number))
    for _, number in sorted(cands)[:10]:
        meta = gh(f"repos/{repo}/pulls/{number}")
        if meta is None or not meta.get("merged_at"):
            continue
        reason = looks_human(meta, known_agent)
        if reason is None:
            files = files_of(repo, number)
            reason = "unavailable" if files is None else eligibility(files)
        log({"event": "skip" if reason else "keep", "group": "human", "repo": repo, "number": number, "reason": reason,
             "pair": agent_job["id"]})
        if reason is None:
            job = job_from(meta, repo, "human", None)
            job["pair"] = agent_job["id"]
            return job
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aidev", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--agents", type=int, default=120)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    log_fh = (args.out / "sample_log.jsonl").open("w")

    def log(entry):
        log_fh.write(json.dumps(entry) + "\n")
        log_fh.flush()

    agents = draw_agents(args.aidev, args.agents, log)
    (args.out / "jobs_agent.jsonl").write_text("".join(json.dumps(j) + "\n" for j in agents))
    print(f"agent jobs: {len(agents)}", flush=True)
    known = agent_numbers(args.aidev)
    humans = []
    for job in agents:
        h = draw_human(job, known.get(job["repo"], set()), log)
        if h:
            humans.append(h)
    (args.out / "jobs_human.jsonl").write_text("".join(json.dumps(j) + "\n" for j in humans))
    print(f"human jobs: {len(humans)} of {len(agents)} repos", flush=True)


if __name__ == "__main__":
    main()
