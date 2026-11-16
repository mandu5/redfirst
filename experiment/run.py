"""Replay one sampled pull request: build its test environment in a
container, run its new tests with the change and with the code side undone,
and write the verdict.

    python run.py jobs.jsonl --workers 2

Each job line: {"id", "group", "agent", "repo", "number", "base_sha",
"head_sha", "merged_at"}. Finished jobs are skipped, so the run resumes.

Untrusted code only runs inside containers in the dedicated VM: the setup
step has network access, the two test runs have none. Git runs on the host.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

from redfirst.core import HEAD_NOT_GREEN, judge, needs_file_fallback  # noqa: E402
from redfirst.repo import code_at_base, make_plan  # noqa: E402

import envplan  # noqa: E402

WORK = Path(os.environ.get("RF_WORK", Path.home() / ".cache" / "f2p-work"))
IMAGE = os.environ.get("RF_IMAGE", "rf-runner:2")
HARNESS = "v2"
SRC_MOUNT = WORK / "redfirst-src"
UID = f"{os.getuid()}:{os.getgid()}"
GIT_ENV = dict(
    os.environ,
    GIT_TERMINAL_PROMPT="0",
    GIT_CONFIG_GLOBAL="/dev/null",
    GIT_CONFIG_NOSYSTEM="1",
    GIT_LFS_SKIP_SMUDGE="1",
    LC_ALL="C",
)
SETUP_TIMEOUT = 1200
TEST_TIMEOUT = 600


def sh(args, cwd=None, timeout=600, check=True):
    proc = subprocess.run(args, cwd=cwd, env=GIT_ENV, capture_output=True, text=True, timeout=timeout)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, args))}: {proc.stderr.strip()[-800:]}")
    return proc


def ensure_clone(repo: str) -> Path:
    path = WORK / "repos" / repo.replace("/", "__")
    if not (path / ".git").exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".partial")
        shutil.rmtree(tmp, ignore_errors=True)
        sh(["git", "clone", "--quiet", "--filter=blob:none", "--no-checkout",
            f"https://github.com/{repo}.git", str(tmp)], timeout=1200)
        tmp.rename(path)
    return path


def fetch_pr(clone: Path, number: int, base_sha: str, head_sha: str) -> None:
    have = sh(["git", "cat-file", "-e", f"{head_sha}^{{commit}}"], cwd=clone, check=False).returncode == 0
    if not have:
        sh(["git", "fetch", "--quiet", "origin", f"+refs/pull/{number}/head:refs/rf/pr-{number}"], cwd=clone, timeout=900)
    have_base = sh(["git", "cat-file", "-e", f"{base_sha}^{{commit}}"], cwd=clone, check=False).returncode == 0
    if not have_base:
        sh(["git", "fetch", "--quiet", "origin", base_sha], cwd=clone, timeout=900)


IMAGE_PATH = "/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"


def write_identity(run_dir: Path) -> None:
    """passwd and group files that know the host uid the containers run as.
    Without them getpass.getuser() and pwd.getpwuid() raise KeyError."""
    uid, gid = os.getuid(), os.getgid()
    etc = run_dir / "etc"
    etc.mkdir(exist_ok=True)
    (etc / "passwd").write_text(
        "root:x:0:0:root:/root:/bin/bash\n"
        f"rf:x:{uid}:{gid}:rf:{run_dir}/home:/bin/bash\n"
        "nobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin\n"
    )
    (etc / "group").write_text(f"root:x:0:\nrf:x:{gid}:\nnogroup:x:65534:\n")


def docker(job_id: str, run_dir: Path, clone: Path, command: list[str], network: bool, timeout: int,
           env: dict | None = None, workdir: Path | None = None):
    name = f"rf-{job_id}"[:120]
    args = [
        "docker", "run", "--rm", "--name", name,
        "--cpus", "2", "--memory", "2200m", "--pids-limit", "4096",
        "--user", UID,
        "-v", f"{run_dir}/etc/passwd:/etc/passwd:ro",
        "-v", f"{run_dir}/etc/group:/etc/group:ro",
        "-e", "USER=rf",
        "-e", "LOGNAME=rf",
        # Console scripts of the project and its test tools (uvicorn, alembic,
        # the project's own CLI) resolve from the environment, as in CI.
        "-e", f"PATH={run_dir}/venv/bin:{IMAGE_PATH}",
        "-e", f"VIRTUAL_ENV={run_dir}/venv",
        "-e", f"HOME={run_dir}/home",
        "-e", "TMPDIR=/tmp",  # not the shared folder: virtiofs cannot read unlinked temp files
        "-e", f"UV_CACHE_DIR={WORK}/uv-cache",
        "-e", f"UV_PYTHON_INSTALL_DIR={WORK}/uv-python",
        "-e", "UV_LINK_MODE=copy",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-e", "PIP_DISABLE_PIP_VERSION_CHECK=1",
        "-v", f"{run_dir}:{run_dir}",
        "-v", f"{clone}:{clone}",
        "-v", f"{WORK}/uv-python:{WORK}/uv-python" + ("" if network else ":ro"),
        "-v", f"{SRC_MOUNT}:{SRC_MOUNT}:ro",
        # OpenAI tokenizer files, fetched once on the host: tiktoken downloads
        # them on first use, which fails with the network off.
        "-v", f"{WORK}/tiktoken-cache:{WORK}/tiktoken-cache:ro",
        "-e", f"TIKTOKEN_CACHE_DIR={WORK}/tiktoken-cache",
        "-w", str(workdir or run_dir / "src"),
    ]
    if network:
        args += ["-v", f"{WORK}/uv-cache:{WORK}/uv-cache"]
    else:
        args += ["--network", "none"]
    for k, v in (env or {}).items():
        args += ["-e", f"{k}={v}"]
    args += [IMAGE, *command]
    started = time.monotonic()
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        code, out = proc.returncode, proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as exc:
        subprocess.run(["docker", "kill", name], capture_output=True)
        out = ((exc.stdout or b"") + (exc.stderr or b"")).decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
        code = "timeout"
    return code, out, round(time.monotonic() - started, 1)


def project_dir(src: Path, target_files: list[str]) -> str:
    """The project the target tests belong to, relative to the checkout: the
    nearest directory above them that is an installable Python project. ""
    is the repository root. In a monorepo the subproject is installed and
    pytest runs from it, as its own CI does."""
    votes: dict[str, int] = {}
    for f in target_files:
        found = ""
        parent = Path(f).parent
        while parent != Path("."):
            if envplan.is_project(src / parent):
                found = parent.as_posix()
                break
            parent = parent.parent
        votes[found] = votes.get(found, 0) + 1
    return max(votes, key=lambda k: (votes[k], k == "")) if votes else ""


def relative_ids(ids: list[str], proj: str) -> list[str]:
    """Test ids relative to the project directory pytest runs from."""
    if not proj:
        return list(ids)
    out = []
    for nodeid in ids:
        path, sep, rest = nodeid.partition("::")
        out.append(os.path.relpath(path, proj) + sep + rest)
    return out


def pytest_phase(job_id, run_dir, clone, files, tag, proj=""):
    result = run_dir / f"result_{tag}.json"
    result.unlink(missing_ok=True)
    cmd = [str(run_dir / "venv/bin/python"), "-m", "pytest", "-p", "redfirst.plugin",
           "-p", "no:cacheprovider", "-q", "-rN", *relative_ids(files, proj)]
    code, out, secs = docker(job_id, run_dir, clone, cmd, network=False, timeout=TEST_TIMEOUT,
                             env={"REDFIRST_RESULT": str(result), "PYTHONPATH": str(SRC_MOUNT)},
                             workdir=run_dir / "src" / proj)
    (run_dir / f"log_{tag}.txt").write_text(out[-200_000:])
    data = json.loads(result.read_text()) if result.exists() else None
    if data is None:
        data = {"tests": {}, "collect_errors": [{"nodeid": "", "message": f"pytest did not finish ({code})"}]}
    data["returncode"], data["seconds"], data["tail"] = code, secs, out[-2500:]
    return data


def run_job(job: dict) -> dict:
    out_dir = WORK / "results" / job["id"]
    final = out_dir / "result.json"
    if final.exists():
        return json.loads(final.read_text())
    out_dir.mkdir(parents=True, exist_ok=True)
    record = {"job": job, "harness": HARNESS, "image": IMAGE,
              "started": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    run_dir = WORK / "runs" / job["id"]
    clone = None
    try:
        clone = ensure_clone(job["repo"])
        fetch_pr(clone, job["number"], job["base_sha"], job["head_sha"])
        plan = make_plan(clone, job["base_sha"], job["head_sha"])
        record["plan"] = plan.summary()
        if plan.early:
            record["verdict"] = plan.early
            return record
        shutil.rmtree(run_dir, ignore_errors=True)
        (run_dir / "home").mkdir(parents=True)
        (run_dir / "tmp").mkdir()
        write_identity(run_dir)
        sh(["git", "worktree", "add", "--quiet", "--detach", str(run_dir / "src"), plan.head], cwd=clone, timeout=900)
        merged = datetime.fromisoformat(job["merged_at"].replace("Z", "+00:00"))
        proj = project_dir(run_dir / "src", plan.files)
        record["project_dir"] = proj
        src = run_dir / "src" / proj
        info = envplan.plan(src, (merged + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z"), root=run_dir / "src")
        record["env"] = info
        (run_dir / "setup.sh").write_text(envplan.script(info, str(src), str(run_dir / "venv"), root=str(run_dir / "src")))
        code, out, secs = docker(job["id"], run_dir, clone, ["bash", str(run_dir / "setup.sh")], network=True, timeout=SETUP_TIMEOUT)
        (out_dir / "log_setup.txt").write_text(out[-200_000:])
        record["setup"] = {"returncode": code, "seconds": secs, "tail": out[-2500:]}
        if code != 0:
            record["verdict"] = "env_failed"
            return record
        record["python"] = (run_dir / "python.txt").read_text().strip() if (run_dir / "python.txt").exists() else None
        selection = plan.selection
        head_run = pytest_phase(job["id"], run_dir, clone, selection, "head", proj)
        if needs_file_fallback(head_run):
            selection = plan.files
            head_run = pytest_phase(job["id"], run_dir, clone, selection, "head", proj)
        record["selection"] = "files" if selection is plan.files else "tests"
        record["head_run"] = {k: head_run[k] for k in ("returncode", "seconds", "tail")}
        verdict, rows = judge(plan.targets, head_run, {"tests": {}, "collect_errors": []})
        if verdict != HEAD_NOT_GREEN:
            with code_at_base(run_dir / "src", plan):
                base_run = pytest_phase(job["id"], run_dir, clone, selection, "base", proj)
            record["base_run"] = {k: base_run[k] for k in ("returncode", "seconds", "tail")}
            verdict, rows = judge(plan.targets, head_run, base_run)
            (out_dir / "raw_base.json").write_text(json.dumps(base_run))
        (out_dir / "raw_head.json").write_text(json.dumps(head_run))
        record["verdict"] = verdict
        record["rows"] = [asdict(r) for r in rows]
        return record
    except Exception as exc:  # recorded, not fatal for the batch
        record["verdict"] = "harness_error"
        record["error"] = f"{type(exc).__name__}: {exc}"[-1500:]
        record["traceback"] = traceback.format_exc()[-3000:]
        return record
    finally:
        record["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        final.write_text(json.dumps(record, indent=1))
        for tag in ("head", "base"):
            log = run_dir / f"log_{tag}.txt"
            if log.exists():
                shutil.copy(log, out_dir / log.name)
        if clone is not None and run_dir.exists():
            sh(["git", "worktree", "remove", "--force", str(run_dir / "src")], cwd=clone, check=False)
            shutil.rmtree(run_dir, ignore_errors=True)


def sync_source():
    """The plugin is mounted read-only into the containers from a copy."""
    shutil.rmtree(SRC_MOUNT, ignore_errors=True)
    shutil.copytree(HERE.parent / "src", SRC_MOUNT)
    for d in ("uv-cache", "uv-python", "results", "runs", "repos"):
        (WORK / d).mkdir(parents=True, exist_ok=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jobs")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", help="comma-separated job ids")
    args = ap.parse_args()
    jobs = [json.loads(line) for line in Path(args.jobs).read_text().splitlines() if line.strip()]
    if args.only:
        keep = set(args.only.split(","))
        jobs = [j for j in jobs if j["id"] in keep]
    if args.limit:
        jobs = jobs[: args.limit]
    sync_source()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_job, j): j for j in jobs}
        for fut in as_completed(futures):
            rec = fut.result()
            j = futures[fut]
            print(f"{datetime.now():%H:%M:%S} {j['id']}: {rec.get('verdict')} {rec.get('error', '')[:160]}", flush=True)


if __name__ == "__main__":
    main()
