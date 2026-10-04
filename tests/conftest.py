"""Shared pytest helpers for the public diagnostics skill."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from importlib import import_module, util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = REPO_ROOT / "skills" / "ci-skills" / "scripts"
CORE_ROOT = SCRIPT_ROOT / "core"


def load_module(module_name: str, path: Path) -> ModuleType:
    """Import one file by path without requiring package installation."""
    if not path.exists():
        pytest.fail(f"missing module under test: {path.relative_to(REPO_ROOT)}")
    spec = util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        pytest.fail(f"cannot import module under test: {path.relative_to(REPO_ROOT)}")
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def import_script_module(module_name: str) -> ModuleType:
    """Import a module from the diagnostics script tree."""
    script_root = str(SCRIPT_ROOT)
    if script_root not in sys.path:
        sys.path.insert(0, script_root)
    return import_module(module_name)


@pytest.fixture
def target_file(tmp_path: Path) -> Path:
    """Provide one explicit nonsecret target contract for CLI tests."""
    path = tmp_path / "target.toml"
    path.write_text(
        (
            "[github]\n"
            'host = "github.example.test"\n'
            'repository = "unit/repo"\n'
            "\n"
            "[gitlab]\n"
            'url = "https://gitlab.example.test"\n'
            "\n"
            "[kubernetes]\n"
            'context = "unit-context"\n'
            'server = "https://api.cluster.example.test:6443"\n'
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def fake_bin(tmp_path: Path) -> Path:
    """Create a temporary executable directory placed ahead of real tools."""
    path = tmp_path / "bin"
    path.mkdir()
    return path


@pytest.fixture
def call_journal(tmp_path: Path) -> Path:
    """Capture fake external command calls in JSON Lines form."""
    return tmp_path / "calls.jsonl"


def install_executable(directory: Path, name: str, body: str) -> Path:
    """Install one fake command executable for an isolated CLI test."""
    path = directory / name
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def run_script(
    script_name: str,
    *args: str | Path,
    fake_bin: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one planned script through Python with optional fake command path."""
    script = SCRIPT_ROOT / script_name
    if not script.exists():
        pytest.fail(f"missing script under test: {script.relative_to(REPO_ROOT)}")
    environment = os.environ.copy()
    python_path = str(SCRIPT_ROOT)
    if environment.get("PYTHONPATH"):
        python_path = python_path + os.pathsep + environment["PYTHONPATH"]
    environment.update(
        {
            "HOME": str(Path.cwd()),
            "KUBECONFIG": "",
            "LC_ALL": "C",
            "PYTHONPATH": python_path,
        }
    )
    if fake_bin is not None:
        environment["PATH"] = str(fake_bin) + os.pathsep + environment.get("PATH", "")
    if env:
        environment.update(env)
    return subprocess.run(
        [sys.executable, str(script), *(str(arg) for arg in args)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
    )


def parse_json_output(result: subprocess.CompletedProcess[str]) -> Any:
    """Parse JSON stdout and show stderr when the command emitted invalid JSON."""
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.fail(
            "stdout is not valid JSON\n"
            f"returncode={result.returncode}\n"
            f"stdout={result.stdout!r}\n"
            f"stderr={result.stderr!r}"
        )


def read_journal(path: Path) -> list[dict[str, Any]]:
    """Read fake command calls recorded by test executables."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def forbidden_project_term() -> str:
    """Construct the prohibited public-repo term without storing it literally."""
    return bytes((103, 97, 108, 105, 108, 101, 111)).decode("ascii")
