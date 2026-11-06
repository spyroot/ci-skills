"""Shared pytest helpers for the public diagnostics skill.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import stat
import subprocess
import sys
import tomllib
from collections.abc import Iterator, Mapping, Sequence
from importlib import import_module, util
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import TYPE_CHECKING, Final, Optional, TypeAlias, Union

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = REPO_ROOT / "ci-skills"
SCRIPT_ROOT = SKILL_ROOT / "bin"
LIB_ROOT = SKILL_ROOT / "lib" / "python"
CORE_ROOT = LIB_ROOT / "core"
# The shipped target template, read by tests that load the real example.
TARGET_TEMPLATE = REPO_ROOT / "target.toml.template"


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


# --- target files composed from the target contract --------------------------

ENDPOINTS = import_script_module("lib.python.core.endpoints")

# A TOML value a target table holds: a scalar, a list, or an inline table.
TomlValue: TypeAlias = Union[
    str, int, bool, Sequence["TomlValue"], Mapping[str, "TomlValue"]
]
# Top-level target entries in file order: a table (mapping) or a scalar.
TargetTables: TypeAlias = Mapping[str, TomlValue]

# Values of the shared `target_file` fixture. Each test that needs them reads
# them from here or from the file, never from a second copy.
FIXTURE_GITHUB: Final[Mapping[str, TomlValue]] = MappingProxyType(
    {"host": "github.example.test", "repository": "unit/repo"}
)
FIXTURE_GITLAB: Final[Mapping[str, TomlValue]] = MappingProxyType(
    {"url": "https://gitlab.example.test"}
)
FIXTURE_KUBERNETES: Final[Mapping[str, TomlValue]] = MappingProxyType(
    {"context": "unit-context", "server": "https://api.cluster.example.test:6443"}
)
FIXTURE_HARBOR: Final[Mapping[str, TomlValue]] = MappingProxyType(
    {
        "url": "https://harbor.example.test",
        "project": "unit",
        "credential_file": "harbor.credential",
    }
)


def toml_value(value: TomlValue) -> str:
    """Render one target value as TOML; a mapping becomes an inline table.

    :param value: String, integer, boolean, list, or mapping.
    :returns: TOML text for the value.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, str)):
        return json.dumps(value)
    if isinstance(value, Mapping):
        if not value:
            return "{}"
        items = ", ".join(
            f"{ENDPOINTS.TargetContract.quote_key(key)} = {toml_value(item)}"
            for key, item in value.items()
        )
        return f"{{ {items} }}"
    return "[" + ", ".join(toml_value(item) for item in value) + "]"


def render_target(tables: TargetTables) -> str:
    """Render a target file: top-level scalars first, then one header per table.

    A table name is written as given, so a dotted name such as the value of
    ``legacy_profile(name)`` is a nested-table header. A mapping inside a table
    is written as an inline table, as routing rows are.

    :param tables: Top-level entries in file order.
    :returns: TOML text ending with a newline.
    """
    blocks = [
        "\n".join(
            f"{name} = {toml_value(value)}"
            for name, value in tables.items()
            if not isinstance(value, Mapping)
        )
    ]
    for name, fields in tables.items():
        if isinstance(fields, Mapping):
            lines = [f"[{name}]"]
            lines.extend(
                f"{ENDPOINTS.TargetContract.quote_key(key)} = {toml_value(value)}"
                for key, value in fields.items()
            )
            blocks.append("\n".join(lines))
    return "\n\n".join(block for block in blocks if block) + "\n"


def write_target(path: Path, tables: TargetTables) -> Path:
    """Write a rendered target file.

    :param path: Target file path; its parent directory must exist.
    :param tables: Top-level entries in file order.
    :returns: ``path`` after writing.
    """
    path.write_text(render_target(tables), encoding="utf-8")
    return path


def paired(*authorities: str) -> dict[str, TomlValue]:
    """Compose one routing row naming each routed authority's own table.

    :param *authorities: Routed authorities the row pairs, such as Kubernetes.
    :returns: Row mapping each authority to its table; empty pairs nothing.
    """
    return {authority: authority for authority in authorities}


def routing_table(
    rows: Mapping[str, TomlValue], default: Optional[str] = None
) -> dict[str, TomlValue]:
    """Compose the GitLab routing table: optional default, then one row each.

    :param rows: Instance table name mapped to its row; a row is normally a
        mapping from ``paired``, or any value for a malformed row.
    :param default: Instance named by the routing default key, or ``None`` to
        omit the key.
    :returns: Routing table entries in file order.
    """
    table: dict[str, TomlValue] = (
        {} if default is None else {ENDPOINTS.GITLAB_ROUTING_DEFAULT_KEY: default}
    )
    table.update(rows)
    return table


def legacy_profile(name: str) -> str:
    """Name the header of one legacy named GitLab profile table.

    :param name: Profile key nested under the legacy GitLab table.
    :returns: Dotted header path, such as the issue #133 public profile.
    """
    return ENDPOINTS.TARGET_CONTRACT.dotted_path((ENDPOINTS.GITLAB_AUTHORITY, name))


def fixture_tables() -> dict[str, TomlValue]:
    """Compose the shared fixture target as new mappings the caller may change.

    GitHub, the private GitLab instance whose routing row pairs Kubernetes, and
    Kubernetes. ``load_target`` requires GitHub, GitLab and Kubernetes by
    default, so the row pairs Kubernetes; the file declares no Harbor table.

    :returns: Top-level entries in file order, Kubernetes last so a test can
        append nested Kubernetes tables to the file.
    """
    return {
        ENDPOINTS.GITHUB_AUTHORITY: dict(FIXTURE_GITHUB),
        ENDPOINTS.GITLAB_ROUTING_TABLE: routing_table(
            {ENDPOINTS.GITLAB_PRIVATE_TABLE: paired(ENDPOINTS.KUBERNETES_AUTHORITY)},
            default=ENDPOINTS.GITLAB_PRIVATE_TABLE,
        ),
        ENDPOINTS.GITLAB_PRIVATE_TABLE: dict(FIXTURE_GITLAB),
        ENDPOINTS.KUBERNETES_AUTHORITY: dict(FIXTURE_KUBERNETES),
    }


def copy_template_target(directory: Path) -> Path:
    """Copy the shipped target template and create the files it references.

    Every relative path held by a key whose contract role is access (a token,
    credential or kubeconfig file) is created beside the copy with the content
    ``unit-<file name>``, so a test can compare what a provider received with
    the file the template names.

    :param directory: Existing directory receiving ``target.toml``.
    :returns: Path of the copied target.
    """
    target = directory / "target.toml"
    text = TARGET_TEMPLATE.read_text(encoding="utf-8")
    target.write_text(text, encoding="utf-8")
    for table, fields in tomllib.loads(text).items():
        if not isinstance(fields, Mapping):
            continue
        for key, value in fields.items():
            path = ENDPOINTS.TARGET_CONTRACT.dotted_path((table, key))
            if (
                isinstance(value, str)
                and ENDPOINTS.TARGET_CONTRACT.role(path) is ENDPOINTS.Role.ACCESS
                and not Path(value).expanduser().is_absolute()
            ):
                created = directory / value
                created.parent.mkdir(parents=True, exist_ok=True)
                created.write_text(f"unit-{created.name}", encoding="utf-8")
    return target


def selected_test_paths(command: str) -> tuple[str, ...]:
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


@pytest.fixture(autouse=True)
def isolated_log_settings() -> Iterator[None]:
    """Give each test an empty diagnostic binding and restore it afterwards.

    Command parsers bind their log options for code below the adapter
    (``core.logging.bind_log_settings``). In one test process that binding
    would otherwise carry one test's ``--log-file`` into the next test.

    :yields: None while the test runs with no bound log options.
    :ytype: None
    """
    logging_module = import_script_module("lib.python.core.logging")
    token = logging_module.bind_log_settings(argparse.Namespace())
    yield
    logging_module.unbind_log_settings(token)


@pytest.fixture
def target_file(tmp_path: Path) -> Path:
    """Provide a nonsecret target declaring GitHub, GitLab, and Kubernetes.

    The file is ``render_target(fixture_tables())``: the private GitLab instance
    and its routing row pairing Kubernetes.

    :param tmp_path: Pytest temporary directory for this test.
    :returns: Path to a TOML target with test-only host, repository, and context.
    """
    return write_target(tmp_path / "target.toml", fixture_tables())


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
