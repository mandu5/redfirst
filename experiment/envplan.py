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
REQ_TOKEN = re.compile(r"(^|[._-])(test|tests|testing|dev|develop|development)([._-]|$)")
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
    exact = re.fullmatch(r"\s*===?\s*(\d+\.\d+\.\d+)\s*", spec)
    if exact:  # a pinned patch release (PostHog pins ==3.12.12); uv fetches it
        return [exact.group(1)]
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


def requirement_files(src: Path, is_installable: bool) -> list[str]:
    """Test requirement files: the known names, any requirements*.txt at the
    top level or under requirements/ whose name carries a test or dev token,
    and requirements.txt itself when the repository is not a package (its
    dependencies are declared nowhere else)."""
    found = [f for f in REQ_FILES if (src / f).is_file()]
    candidates = sorted(src.glob("*requirements*.txt")) + sorted(src.glob("requirements/*.txt"))
    for path in candidates:
        rel = path.relative_to(src).as_posix()
        stem = path.name[: -len(".txt")]
        if rel not in found and path.is_file() and REQ_TOKEN.search(stem):
            found.append(rel)
    if not is_installable and (src / "requirements.txt").is_file():
        found.insert(0, "requirements.txt")
    return found


# Flags and ini keys in a project's own pytest configuration that only work
# with a plugin installed. Installing the plugin the project already uses
# makes its configuration load; nothing else is added.
PLUGIN_FLAGS = [
    (re.compile(r"(^|\s)--timeout\b"), "pytest-timeout"),
    (re.compile(r"(^|\s)(-n\s*\S|--numprocesses\b|--dist\b|--maxprocesses\b)"), "pytest-xdist"),
    (re.compile(r"(^|\s)--cov"), "pytest-cov"),
    (re.compile(r"(^|\s)--reruns\b"), "pytest-rerunfailures"),
    (re.compile(r"(^|\s)--benchmark"), "pytest-benchmark"),
    (re.compile(r"(^|\s)--asyncio-mode\b"), "pytest-asyncio"),
    (re.compile(r"(^|\s)--html\b"), "pytest-html"),
    (re.compile(r"(^|\s)--json-report\b"), "pytest-json-report"),
    (re.compile(r"(^|\s)--mypy\b"), "pytest-mypy"),
    (re.compile(r"(^|\s)--ds\b"), "pytest-django"),
]
PLUGIN_INI_KEYS = {
    "asyncio_mode": "pytest-asyncio",
    "asyncio_default_fixture_loop_scope": "pytest-asyncio",
    "timeout": "pytest-timeout",
    "env": "pytest-env",
    "django_settings_module": "pytest-django",
    "mock_use_standalone_module": "pytest-mock",
}


def pytest_config(src: Path, py: dict) -> dict:
    """The project's pytest ini options from the first file pytest would read."""
    for name, section in (("pytest.ini", "pytest"), (".pytest.ini", "pytest")):
        cfg = _setup_cfg(src / name)
        if cfg and cfg.has_section(section):
            return dict(cfg.items(section))
    opts = py.get("tool", {}).get("pytest", {}).get("ini_options")
    if isinstance(opts, dict):
        return {k: " ".join(map(str, v)) if isinstance(v, list) else str(v) for k, v in opts.items()}
    for name, section in (("tox.ini", "pytest"), ("setup.cfg", "tool:pytest")):
        cfg = _setup_cfg(src / name)
        if cfg and cfg.has_section(section):
            return dict(cfg.items(section))
    return {}


def pytest_plugins(src: Path, py: dict) -> list[str]:
    cfg = pytest_config(src, py)
    need = set()
    addopts = cfg.get("addopts", "").replace("\n", " ")
    for pattern, plugin in PLUGIN_FLAGS:
        if pattern.search(addopts):
            need.add(plugin)
    for key, plugin in PLUGIN_INI_KEYS.items():
        if key in cfg:
            need.add(plugin)
    return sorted(need)


def workspace_package(src: Path, root: Path | None) -> str | None:
    """Name of the uv workspace member at src, when the lock lives at root."""
    if root is None or src == root or (src / "uv.lock").exists() or not (root / "uv.lock").exists():
        return None
    if "workspace" not in _toml(root / "pyproject.toml").get("tool", {}).get("uv", {}):
        return None
    name = _toml(src / "pyproject.toml").get("project", {}).get("name")
    return name if isinstance(name, str) else None


def is_project(src: Path) -> bool:
    """A directory that declares a Python package: setup.py, a pyproject with
    project metadata or a build system, or a setup.cfg with a name. A
    setup.cfg or pyproject that only configures tools does not count."""
    if (src / "setup.py").is_file():
        return True
    py = _toml(src / "pyproject.toml")
    if "project" in py or "build-system" in py or "name" in py.get("tool", {}).get("poetry", {}):
        return True
    cfg = _setup_cfg(src / "setup.cfg")
    return bool(cfg and cfg.has_option("metadata", "name"))


def uv_required(src: Path, root: Path | None) -> str | None:
    """The uv version range a project pins (tool.uv.required-version)."""
    for d in [src] + ([root] if root is not None and root != src else []):
        spec = _toml(d / "uv.toml").get("required-version") or \
            _toml(d / "pyproject.toml").get("tool", {}).get("uv", {}).get("required-version")
        if isinstance(spec, str) and spec.strip():
            return spec.strip()
    return None


def installable(src: Path, py: dict) -> bool:
    return (src / "setup.py").exists() or "build-system" in py or "project" in py or (src / "setup.cfg").exists()


def plan(src: Path, exclude_newer: str, root: Path | None = None) -> dict:
    """src is the project to install: the repository root, or the subproject
    that holds the target tests in a monorepo (root is then the checkout)."""
    py = _toml(src / "pyproject.toml")
    is_installable = installable(src, py)
    info = {
        "requires_python": requires_python(src, py),
        "extras": extras(src, py),
        "groups": groups(py),
        "req_files": requirement_files(src, is_installable),
        "loose": loose_requirements(py, src),
        "plugins": pytest_plugins(src, py),
        "uv_lock": (src / "uv.lock").exists(),
        "workspace_package": workspace_package(src, root),
        "uv_required": uv_required(src, root),
        "installable": is_installable,
        "exclude_newer": exclude_newer,
    }
    info["pythons"] = pick_pythons(info["requires_python"])
    return info


def script(info: dict, src: str, venv: str, root: str | None = None) -> str:
    """Bash that builds the environment; exit 0 only when pytest imports."""
    q = shlex.quote
    excl = f"--exclude-newer {q(info['exclude_newer'])}"
    uv = '"${UV[@]}"'
    lines = ["set -u", f"cd {q(src)}"]
    spec = info.get("uv_required")
    if spec:
        # The project pins the uv version it works with; run that one.
        spec = spec.strip()
        if spec[:1].isdigit():
            spec = "==" + spec
        lines.append(f"UV=(uvx -q --from {q('uv' + spec)} uv)")
    else:
        lines.append("UV=(uv)")
    lines += [
        "build() {",
        "  py=$1",
        f"  rm -rf {q(venv)}",
        f"  {uv} venv -q {q(venv)} --python \"$py\" || return 1",
        f"  export VIRTUAL_ENV={q(venv)}",
        "  ok=1",
    ]
    flags = " ".join(f"--group {q(g)}" for g in info["groups"]) + " " + " ".join(f"--extra {q(e)}" for e in info["extras"])
    if info["uv_lock"]:
        lines += [
            f"  if UV_PROJECT_ENVIRONMENT={q(venv)} {uv} sync -q --frozen --python \"$py\" {flags}; then ok=0; fi",
        ]
    elif info.get("workspace_package") and root:
        pkg = q(info["workspace_package"])
        lines += [
            f"  if (cd {q(root)} && UV_PROJECT_ENVIRONMENT={q(venv)} {uv} sync -q --frozen --python \"$py\" --package {pkg} {flags}); then ok=0; fi",
        ]
    if info["installable"]:
        ex = ",".join(info["extras"])
        target = f".[{ex}]" if ex else "."
        lines += [
            "  if [ $ok -ne 0 ]; then",
            f"    {uv} pip install -q {excl} -e {q(target)} && ok=0",
            f"    if [ $ok -ne 0 ] && [ {q(target)} != . ]; then {uv} pip install -q {excl} -e . && ok=0; fi",
            "  fi",
        ]
    else:
        lines += ["  ok=0"]
    lines += ["  [ $ok -eq 0 ] || return 1"]
    for f in info["req_files"]:
        lines.append(f"  {uv} pip install -q {excl} -r {q(f)} || echo 'redfirst: {f} failed' >&2")
    if info["groups"] and not info["uv_lock"]:
        for g in info["groups"]:
            lines.append(f"  {uv} pip install -q {excl} --group {q(g)} || echo 'redfirst: group {g} failed' >&2")
    if info["loose"]:
        lines.append(f"  {uv} pip install -q {excl} {' '.join(q(r) for r in info['loose'])} || echo 'redfirst: loose deps failed' >&2")
    if info.get("plugins"):
        # uv leaves an installed plugin as it is; only missing ones are added.
        plugins = " ".join(q(p) for p in info["plugins"])
        lines.append(f"  {uv} pip install -q {excl} {plugins} || echo 'redfirst: pytest plugins failed' >&2")
    lines += [
        f"  {q(venv)}/bin/python -c 'import pytest' 2>/dev/null || {uv} pip install -q {excl} pytest || return 1",
        f"  {q(venv)}/bin/python -c 'import pytest' || return 1",
        "  return 0",
        "}",
    ]
    tries = " ".join(q(p) for p in info["pythons"])
    lines += [
        f"for py in {tries}; do",
        "  echo \"redfirst: building with python $py\" >&2",
        f"  if build \"$py\"; then echo \"$py\" > {q(str(Path(venv).parent / 'python.txt'))}; exit 0; fi",
        "done",
        "exit 1",
    ]
    return "\n".join(lines) + "\n"
