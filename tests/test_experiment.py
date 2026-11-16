"""The replay study's environment guesses (experiment/envplan.py) and the
runner helpers that pick where pytest runs (experiment/run.py)."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

EXPERIMENT = Path(__file__).resolve().parent.parent / "experiment"
sys.path.insert(0, str(EXPERIMENT))

envplan = pytest.importorskip("envplan")
run = pytest.importorskip("run")


def write(root: Path, files: dict) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
    return root


def test_requirement_files_by_name_and_plain_requirements_for_non_packages(tmp_path):
    write(tmp_path, {
        "requirements.txt": "voluptuous\n",
        "requirements.test.txt": "pytest\n",
        "requirements-docs.txt": "sphinx\n",
        "requirements/dev.txt": "ruff\n",
        "requirements_dev.txt": "mypy\n",
    })
    assert envplan.requirement_files(tmp_path, is_installable=False) == [
        "requirements.txt", "requirements_dev.txt", "requirements/dev.txt", "requirements.test.txt",
    ]
    assert "requirements.txt" not in envplan.requirement_files(tmp_path, is_installable=True)


def test_plugins_named_by_the_projects_own_pytest_config(tmp_path):
    write(tmp_path, {"pyproject.toml": """
        [tool.pytest.ini_options]
        addopts = "--timeout=60 -n auto --cov=pkg -ra"
        asyncio_mode = "auto"
    """})
    py = envplan._toml(tmp_path / "pyproject.toml")
    assert envplan.pytest_plugins(tmp_path, py) == ["pytest-asyncio", "pytest-cov", "pytest-timeout", "pytest-xdist"]


def test_pytest_ini_wins_and_plain_flags_need_nothing(tmp_path):
    write(tmp_path, {
        "pytest.ini": "[pytest]\naddopts = --no-header -ra --strict-markers\n",
        "pyproject.toml": '[tool.pytest.ini_options]\naddopts = ["--timeout=5"]\n',
    })
    assert envplan.pytest_plugins(tmp_path, envplan._toml(tmp_path / "pyproject.toml")) == []


def test_addopts_as_a_list(tmp_path):
    write(tmp_path, {"pyproject.toml": '[tool.pytest.ini_options]\naddopts = ["--reruns", "2"]\n'})
    assert envplan.pytest_plugins(tmp_path, envplan._toml(tmp_path / "pyproject.toml")) == ["pytest-rerunfailures"]


def test_uv_workspace_member_and_pinned_uv(tmp_path):
    write(tmp_path, {
        "uv.lock": "",
        "pyproject.toml": '[tool.uv]\nrequired-version = "~=0.10.2"\n[tool.uv.workspace]\nmembers = ["libs/*"]\n',
        "libs/core/pyproject.toml": '[project]\nname = "acme-core"\nversion = "1"\n',
    })
    member = tmp_path / "libs/core"
    info = envplan.plan(member, "2026-01-02T00:00:00Z", root=tmp_path)
    assert info["workspace_package"] == "acme-core"
    assert info["uv_required"] == "~=0.10.2"
    text = envplan.script(info, str(member), str(tmp_path / "venv"), root=str(tmp_path))
    assert "UV=(uvx -q --from 'uv~=0.10.2' uv)" in text
    assert "--package acme-core" in text
    assert f"> {tmp_path / 'python.txt'};" in text
    subprocess.run(["bash", "-n"], input=text, text=True, check=True)


def test_member_with_its_own_lock_is_not_a_workspace_member(tmp_path):
    write(tmp_path, {
        "uv.lock": "",
        "pyproject.toml": '[tool.uv.workspace]\nmembers = ["libs/*"]\n',
        "libs/core/pyproject.toml": '[project]\nname = "acme-core"\n',
        "libs/core/uv.lock": "",
    })
    assert envplan.workspace_package(tmp_path / "libs/core", tmp_path) is None


def test_plain_project_script_uses_the_image_uv(tmp_path):
    write(tmp_path, {"pyproject.toml": '[project]\nname = "x"\nversion = "1"\n[project.optional-dependencies]\ntest = ["pytest"]\n'})
    info = envplan.plan(tmp_path, "2026-01-02T00:00:00Z")
    text = envplan.script(info, str(tmp_path), str(tmp_path / "venv"))
    assert "UV=(uv)" in text and "-e '.[test]'" in text
    subprocess.run(["bash", "-n"], input=text, text=True, check=True)


def test_tool_config_alone_is_not_a_project(tmp_path):
    write(tmp_path, {
        "a/setup.cfg": "[flake8]\nmax-line-length = 100\n",
        "b/pyproject.toml": "[tool.ruff]\nline-length = 100\n",
        "c/setup.cfg": "[metadata]\nname = c\n",
        "d/pyproject.toml": '[tool.poetry]\nname = "d"\n',
    })
    assert [envplan.is_project(tmp_path / d) for d in "abcd"] == [False, False, True, True]


def test_project_dir_finds_the_subproject_holding_the_tests(tmp_path):
    write(tmp_path, {
        "pyproject.toml": '[project]\nname = "root"\n',
        "connectors/source-x/pyproject.toml": '[tool.poetry]\nname = "source-x"\n',
        "connectors/source-x/unit_tests/test_a.py": "",
        "tests/test_b.py": "",
    })
    assert run.project_dir(tmp_path, ["connectors/source-x/unit_tests/test_a.py"]) == "connectors/source-x"
    assert run.project_dir(tmp_path, ["tests/test_b.py"]) == ""
    assert run.project_dir(tmp_path, ["tests/test_b.py", "connectors/source-x/unit_tests/test_a.py"]) == ""


def test_relative_ids():
    ids = ["connectors/source-x/unit_tests/test_a.py::TestA::test_x", "connectors/source-x/unit_tests/test_a.py"]
    assert run.relative_ids(ids, "connectors/source-x") == ["unit_tests/test_a.py::TestA::test_x", "unit_tests/test_a.py"]
    assert run.relative_ids(ids, "") == ids


def test_identity_files_know_the_host_uid(tmp_path):
    run.write_identity(tmp_path)
    passwd = (tmp_path / "etc/passwd").read_text()
    assert f"rf:x:{os.getuid()}:{os.getgid()}:" in passwd
    assert f"rf:x:{os.getgid()}:" in (tmp_path / "etc/group").read_text()


def test_python_choice():
    assert envplan.pick_pythons("==3.12.12") == ["3.12.12"]
    assert envplan.pick_pythons(">=3.10,<3.12") == ["3.11", "3.10"]
    assert envplan.pick_pythons(None) == ["3.12", "3.11"]
    assert envplan.pick_pythons("~=3.13") == ["3.13"]
