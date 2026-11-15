"""Guess how to build a repository's test environment and write it as a
bash script that runs inside the runner container.

Heuristics only. The study counts a pull request only when its new tests
pass with the change, so a wrong guess loses a sample but cannot produce a
false verdict.
"""

from __future__ import annotations

import configparser
import re
import shlex
import tomllib
from pathlib import Path

from packaging.specifiers import InvalidSpecifier, SpecifierSet

PYTHONS = ["3.12", "3.11", "3.13", "3.10", "3.9", "3.8"]
TESTISH = ("test", "tests", "testing", "dev", "develop", "development", "pytest")
REQ_FILES = [
    "requirements-dev.txt", "requirements_dev.txt", "requirements-test.txt",
    "requirements_test.txt", "requirements-tests.txt", "requirements_tests.txt",
    "requirements-testing.txt", "test-requirements.txt", "test_requirements.txt",
    "tests-requirements.txt", "dev-requirements.txt", "dev_requirements.txt",
    "requirements/dev.txt", "requirements/test.txt", "requirements/tests.txt",
    "requirements/testing.txt", "requirements/development.txt",
    "tests/requirements.txt", "test/requirements.txt",
]
SIMPLE_REQ = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,._-]+\])?\s*([<>=!~]=?[^;,\s]+(\s*,\s*[<>=!~]=?[^;,\s]+)*)?$")


def _toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (tomllib.TOMLDecodeError, OSError):
        return {}


def _setup_cfg(path: Path) -> configparser.ConfigParser | None:
    if not path.exists():
        return None
    cfg = configparser.ConfigParser(interpolation=None)
    try:
        cfg.read_string(path.read_text(encoding="utf-8", errors="replace"))
    except configparser.Error:
        return None
    return cfg


def requires_python(src: Path, py: dict) -> str | None:
    spec = py.get("project", {}).get("requires-python")
    if spec:
        return spec
    poetry_py = py.get("tool", {}).get("poetry", {}).get("dependencies", {}).get("python")
    if isinstance(poetry_py, str):
        return _poetry_to_pep440(poetry_py)
    cfg = _setup_cfg(src / "setup.cfg")
    if cfg and cfg.has_option("options", "python_requires"):
        return cfg.get("options", "python_requires").strip()
    setup_py = src / "setup.py"
    if setup_py.exists():
        m = re.search(r"python_requires\s*=\s*['\"]([^'\"]+)['\"]", setup_py.read_text(errors="replace"))
        if m:
            return m.group(1)
    return None


def _poetry_to_pep440(spec: str) -> str | None:
    parts = []
    for raw in spec.split(","):
        raw = raw.strip()
        if raw.startswith("^"):
            v = raw[1:].split(".")
            upper = f"{int(v[0]) + 1}" if v[0] != "0" else f"0.{int(v[1]) + 1}"
            parts += [f">={raw[1:]}", f"<{upper}"]
        elif raw.startswith("~") and not raw.startswith("~="):
            v = raw[1:].split(".")
            parts += [f">={raw[1:]}", f"<{v[0]}.{int(v[1]) + 1}" if len(v) > 1 else f"<{int(v[0]) + 1}"]
        elif raw and raw != "*":
            parts.append(raw)
    return ",".join(parts) or None


def pick_pythons(spec: str | None) -> list[str]:
    if not spec:
        return PYTHONS[:2]
    try:
        allowed = SpecifierSet(spec)
    except InvalidSpecifier:
        return PYTHONS[:2]
    ok = [v for v in PYTHONS if allowed.contains(f"{v}.0") or allowed.contains(f"{v}.99")]
    return ok[:2] or PYTHONS[:1]


def extras(src: Path, py: dict) -> list[str]:
    names = set(py.get("project", {}).get("optional-dependencies", {}).keys())
    names |= set(py.get("tool", {}).get("poetry", {}).get("extras", {}).keys())
    cfg = _setup_cfg(src / "setup.cfg")
    if cfg and cfg.has_section("options.extras_require"):
        names |= set(cfg.options("options.extras_require"))
    setup_py = src / "setup.py"
    if setup_py.exists():
        text = setup_py.read_text(errors="replace")
        block = re.search(r"extras_require\s*=\s*\{(.*?)\}", text, re.S)
        if block:
            names |= set(re.findall(r"['\"]([A-Za-z0-9_.-]+)['\"]\s*:", block.group(1)))
    return sorted(n for n in names if n.lower() in TESTISH)


def groups(py: dict) -> list[str]:
    names = set(py.get("dependency-groups", {}).keys())
    return sorted(n for n in names if n.lower() in TESTISH)


def loose_requirements(py: dict, src: Path) -> list[str]:
    """Test dependencies declared by poetry, pdm, hatch or tox, as plain names."""
    reqs: list[str] = []
    tool = py.get("tool", {})
    poetry = tool.get("poetry", {})
    tables = [poetry.get("dev-dependencies", {})]
    for name, group in poetry.get("group", {}).items():
        if name.lower() in TESTISH:
            tables.append(group.get("dependencies", {}))
    for table in tables:
        for name in table:
            if name.lower() != "python":
                reqs.append(name)
    for name, deps in tool.get("pdm", {}).get("dev-dependencies", {}).items():
        if name.lower() in TESTISH:
            reqs += [d for d in deps if isinstance(d, str)]
    envs = tool.get("hatch", {}).get("envs", {})
    for name in ("default", "test", "tests", "dev", "hatch-test"):
        reqs += [d for d in envs.get(name, {}).get("dependencies", []) if isinstance(d, str)]
        reqs += [d for d in envs.get(name, {}).get("extra-dependencies", []) if isinstance(d, str)]
    tox = src / "tox.ini"
    if tox.exists():
        cfg = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            cfg.read_string(tox.read_text(errors="replace"))
            if cfg.has_option("testenv", "deps"):
                reqs += cfg.get("testenv", "deps").splitlines()
        except configparser.Error:
            pass
    clean = []
    for r in reqs:
        r = r.split("#", 1)[0].strip()
        if r.startswith("{") or "{" in r:
            continue
        if r.startswith("-"):
            continue
        r = r.split(";", 1)[0].strip()
        if r and SIMPLE_REQ.match(r):
            clean.append(r)
    return sorted(set(clean))


def installable(src: Path, py: dict) -> bool:
    return (src / "setup.py").exists() or "build-system" in py or "project" in py or (src / "setup.cfg").exists()


def plan(src: Path, exclude_newer: str) -> dict:
    py = _toml(src / "pyproject.toml")
    info = {
        "requires_python": requires_python(src, py),
        "extras": extras(src, py),
        "groups": groups(py),
        "req_files": [f for f in REQ_FILES if (src / f).is_file()],
        "loose": loose_requirements(py, src),
        "uv_lock": (src / "uv.lock").exists(),
        "installable": installable(src, py),
        "exclude_newer": exclude_newer,
    }
    info["pythons"] = pick_pythons(info["requires_python"])
    return info


def script(info: dict, src: str, venv: str) -> str:
    """Bash that builds the environment; exit 0 only when pytest imports."""
    q = shlex.quote
    excl = f"--exclude-newer {q(info['exclude_newer'])}"
    lines = [
        "set -u",
        f"cd {q(src)}",
        "build() {",
        "  py=$1",
        f"  rm -rf {q(venv)}",
        f"  uv venv -q {q(venv)} --python \"$py\" || return 1",
        f"  export VIRTUAL_ENV={q(venv)}",
        "  ok=1",
    ]
    if info["uv_lock"]:
        flags = " ".join(f"--group {q(g)}" for g in info["groups"]) + " " + " ".join(f"--extra {q(e)}" for e in info["extras"])
        lines += [
            f"  if UV_PROJECT_ENVIRONMENT={q(venv)} uv sync -q --frozen --python \"$py\" {flags}; then ok=0; fi",
        ]
    if info["installable"]:
        ex = ",".join(info["extras"])
        target = f".[{ex}]" if ex else "."
        lines += [
            "  if [ $ok -ne 0 ]; then",
            f"    uv pip install -q {excl} -e {q(target)} && ok=0",
            f"    if [ $ok -ne 0 ] && [ {q(target)} != . ]; then uv pip install -q {excl} -e . && ok=0; fi",
            "  fi",
        ]
    else:
        lines += ["  ok=0"]
    lines += ["  [ $ok -eq 0 ] || return 1"]
    for f in info["req_files"]:
        lines.append(f"  uv pip install -q {excl} -r {q(f)} || echo 'redfirst: {f} failed' >&2")
    if info["groups"] and not info["uv_lock"]:
        for g in info["groups"]:
            lines.append(f"  uv pip install -q {excl} --group {q(g)} || echo 'redfirst: group {g} failed' >&2")
    if info["loose"]:
        lines.append(f"  uv pip install -q {excl} {' '.join(q(r) for r in info['loose'])} || echo 'redfirst: loose deps failed' >&2")
    lines += [
        f"  {q(venv)}/bin/python -c 'import pytest' 2>/dev/null || uv pip install -q {excl} pytest || return 1",
        f"  {q(venv)}/bin/python -c 'import pytest' || return 1",
        "  return 0",
        "}",
    ]
    tries = " ".join(q(p) for p in info["pythons"])
    lines += [
        f"for py in {tries}; do",
        "  echo \"redfirst: building with python $py\" >&2",
        "  if build \"$py\"; then echo \"$py\" > ../python.txt; exit 0; fi",
        "done",
        "exit 1",
    ]
    return "\n".join(lines) + "\n"
