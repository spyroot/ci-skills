"""Shared pytest helpers for the public diagnostics skill.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from importlib import import_module, util
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Optional, Union

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = REPO_ROOT / "ci-skills"
SCRIPT_ROOT = SKILL_ROOT / "bin"
LIB_ROOT = SKILL_ROOT / "lib" / "python"
CORE_ROOT = LIB_ROOT / "core"


if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject, JSONValue


def load_module(module_name: str, path: Path) -> ModuleType:
    """Import a source file by path for tests that bypass installation.

    :param module_name: Import name registered in ``sys.modules`` before execution.
    :param path: Python file under the repository to load.
    :returns: Executed module object.
    """
    if not path.exists():
        pytest.fail(f"missing module under test: {path.relative_to(REPO_ROOT)}")
    spec = util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        pytest.fail(f"cannot import module under test: {path.relative_to(REPO_ROOT)}")
    module = util.module_from_spec(spec)
    # Registered before it runs, as importlib documents: dataclasses resolve
    # their string annotations through sys.modules[cls.__module__].
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def import_script_module(module_name: str) -> ModuleType:
    """Import a diagnostics module with repository script paths available.

    :param module_name: Dotted module name to import.
    :returns: Imported module object from the script or library tree.
    """
    if str(SCRIPT_ROOT) not in sys.path:
        sys.path.insert(0, str(SCRIPT_ROOT))
    import_module("_bootstrap")
    return import_module(module_name)


def pytest_selectors(command: str) -> tuple[str, ...]:
    """Return positional ``tests/python`` selectors from one shell command.

    :param command: Workflow ``run`` script containing a pytest invocation.
    :returns: Test directory or paths selected for execution, excluding ignores.
    """
    tokens = shlex.split(command)
    return tuple(
        token
        for index, token in enumerate(tokens)
        if (token == "tests/python" or token.startswith("tests/python/"))
        and (index == 0 or tokens[index - 1] != "--ignore")
    )


@pytest.fixture
def target_file(tmp_path: Path) -> Path:
    """Provide a nonsecret target declaring GitHub, GitLab, and Kubernetes.

    :param tmp_path: Pytest temporary directory for this test.
    :returns: Path to a TOML target with test-only host, repository, and context.
    """
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
    """Create a directory for fake native executables used by CLI tests.

    :param tmp_path: Pytest temporary directory for this test.
    :returns: Empty ``bin`` path to prepend to a child process PATH.
    """
    path = tmp_path / "bin"
    path.mkdir()
    return path


@pytest.fixture
def call_journal(tmp_path: Path) -> Path:
    """Reserve a JSON Lines path for fake native-command calls.

    :param tmp_path: Pytest temporary directory for this test.
    :returns: Path where test executables can append call records.
    """
    return tmp_path / "calls.jsonl"


def git(root: Path, *args: str) -> str:
    """Run Git in a fixture repository and capture its output.

    :param root: Repository checkout to query or mutate.
    :param *args: Positional arguments forwarded to the helper under test.
    :returns: Standard output with surrounding whitespace removed.

    """
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=True, text=True
    )
    return result.stdout.strip()


def install_executable(directory: Path, name: str, body: str) -> Path:
    """Create an executable fake command for a child CLI test.

    :param directory: Fake-bin directory owned by the test.
    :param name: Command filename to create.
    :param body: Complete script content written as UTF-8.
    :returns: Executable file path.
    """
    path = directory / name
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def run_script(
    script_name: str,
    *args: Union[str, Path],
    fake_bin: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    """Run a packaged Python entrypoint with isolated child settings.

    :param script_name: Filename under the skill's ``bin`` directory.
    :param *args: Command-line values passed to the selected script.
    :param fake_bin: Optional fake-command directory prepended to child PATH.
    :param env: Additional child environment variables, including injected target.
    :returns: Completed process with captured text stdout, stderr, and exit code.

    """
    script = SCRIPT_ROOT / script_name
    if not script.exists():
        pytest.fail(f"missing script under test: {script.relative_to(REPO_ROOT)}")
    environment = os.environ.copy()
    python_path = os.pathsep.join((str(SCRIPT_ROOT), str(SKILL_ROOT)))
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


def parse_json_output(result: subprocess.CompletedProcess[str]) -> JSONValue:
    """Decode CLI stdout and expose captured process details on failure.

    :param result: Completed child process whose stdout should contain JSON.
    :returns: Decoded JSON value.
        and captured streams for diagnosis.
    """
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.fail(
            "stdout is not valid JSON\n"
            f"returncode={result.returncode}\n"
            f"stdout={result.stdout!r}\n"
            f"stderr={result.stderr!r}"
        )


def read_journal(path: Path) -> Sequence[JSONObject]:
    """Read fake native-command calls from a JSON Lines journal.

    :param path: Journal path supplied to fake executables.
    :returns: Decoded call mappings, or an empty list when no file was written.
    """
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def forbidden_project_term() -> str:
    """Construct the public-repository prohibited term without a source literal.

    :returns: Decoded marker text used by the neutrality tests.
    """
    return bytes((103, 97, 108, 105, 108, 101, 111)).decode("ascii")
