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
from dataclasses import replace
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
    from lib.python.core.abstract.skill_state import SkillState
    from lib.python.core.gitlab.gitlab_session import BoundGitLabSession
    from lib.python.core.json_types import JSONObject, JSONValue
    from lib.python.core.kubernetes.kubernetes import K8SAuth, KubernetesService
    from lib.python.core.runtime import CommandResult
    from lib.python.core.target import Target


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


def authenticated_gitlab_session(
    session: BoundGitLabSession, state: SkillState | None = None
) -> BoundGitLabSession:
    """Initialize native identity for tests focused on later GitLab operations.

    :param session: Synthetic provider session with no real credentials.
    :param state: Existing callback invocation, or None for direct library tests.
    :returns: Session bound to a declaration and genuine initialized runtime state.
    """
    access = import_script_module("lib.python.access.gitlab")
    catalog = import_script_module("lib.python.core.catalog")
    if not any(session.environment.get(name) for name in catalog.GITLAB_VARIABLES):
        session = replace(
            session,
            environment={**session.environment, "GITLAB_TOKEN": "fixture-gitlab-token"},
        )
    contexts = import_script_module("lib.python.core.gitlab.callback")
    specifications = import_script_module("lib.python.core.abstract.spec")
    invocation = contexts.GitLabState() if state is None else state
    if invocation.spec is None:
        invocation.spec = specifications.load_spec()
    bound = (
        session
        if session.invocation is invocation
        else replace(session, invocation=invocation)
    )

    class NativeIdentity:
        """Expose only the native current-user fixture to the real initializer."""

        def get_json(self, selected: BoundGitLabSession, endpoint: str) -> JSONObject:
            """Return a native admin identity for the exact synthetic authority.

            :param selected: Session chosen by the operation under test.
            :param endpoint: Fixed identity endpoint required by initialization.
            :returns: Nonsecret native user fields accepted by the initializer.
            """
            assert endpoint == "user"
            return {
                "id": 17,
                "username": "unit-admin",
                "is_admin": True,
                "web_url": f"https://{selected.host}/unit-admin",
            }

    access.establish_gitlab_identity(invocation, bound, NativeIdentity())
    return bound


def _frozen_kubernetes_target(target: Target) -> Target:
    """Bind a synthetic credential source without establishing runtime identity.

    :param target: Existing test authority and optional frozen sources.
    :returns: Same target when bound, otherwise a copy with fixture sources.
    """
    sources = import_script_module("lib.python.access.sources")
    if target.sources is not None:
        return target
    return replace(
        target,
        sources=sources.Sources(
            github=None,
            gitlab=None,
            kubernetes=sources.CredentialSource("fixture:kubernetes", {}),
            kubeconfig_files=(),
            execution_host="unit-host",
        ),
    )


def configure_kubernetes_auth(
    monkeypatch: pytest.MonkeyPatch, selected: object
) -> Target:
    """Exercise real native identity initialization before swapping an operation double.

    :param monkeypatch: Fixture replacing only native responses and post-auth transport.
    :param selected: Target for a real service, or operation double with a target field.
    :returns: Frozen target used by the actual authentication callback.
    """
    kubernetes = import_script_module("lib.python.core.kubernetes.kubernetes")
    runtime = import_script_module("lib.python.core.runtime")
    targets = import_script_module("lib.python.core.target")
    specifications = import_script_module("lib.python.core.abstract.spec")
    target = selected if isinstance(selected, targets.Target) else selected.target
    if not isinstance(target, targets.Target):
        raise TypeError("Kubernetes auth fixture requires a Target")
    target = _frozen_kubernetes_target(target)
    swap = not isinstance(selected, targets.Target)
    if swap:
        selected.target = target
    original_run = kubernetes.K8SAuth.run

    def configure(callback: K8SAuth) -> JSONObject:
        """Retain frozen credentials while leaving observed runtime identity null.

        :param callback: Authentication callback whose genuine whoami will run next.
        :returns: Empty local configuration evidence.
        """
        if callback.state.spec is None:
            callback.state.spec = specifications.load_spec()
        callback.state.kubernetes_target = target
        return {}

    def native(
        service: KubernetesService,
        *args: str,
        openshift: bool = False,
        input_data: bytes | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        """Serve only fixed native bootstrap responses and reject real operations.

        :param service: Real credential-bound service created by K8SAuth.whoami.
        :param args: Native configuration, identity, or administration probe arguments.
        :param openshift: Native executable selector; identity uses kubectl.
        :param input_data: Native stdin, absent during identity initialization.
        :param timeout: Optional per-request deadline accepted by the real transport.
        :returns: Native response consumed by the actual initializer.
        """
        assert not openshift and input_data is None
        assert timeout is None or timeout > 0
        configuration = service.configuration
        if args == ("config", "view", "--minify", "-o", "json"):
            document = {
                "contexts": [
                    {"name": configuration.context, "context": {"cluster": "unit"}}
                ],
                "clusters": [
                    {"name": "unit", "cluster": {"server": configuration.server}}
                ],
            }
            output = json.dumps(document)
        elif args == ("auth", "whoami", "-o", "json"):
            output = json.dumps({"status": {"userInfo": {"username": "unit-user"}}})
        else:
            assert args[:2] == ("auth", "can-i"), f"unexpected native operation: {args}"
            output = "yes\n"
        return runtime.CommandResult(("kubectl", *args), 0, output, "")

    def authenticated(callback: K8SAuth) -> JSONObject:
        """Keep the genuine auth result and guard while replacing later operations.

        :param callback: Callback whose whoami has populated shared runtime state.
        :returns: Actual provider authentication evidence.
        """
        evidence = original_run(callback)
        if swap:
            service = callback.state.kubernetes
            selected.state = callback.state
            selected.configuration = service.configuration
            selected.require_access = service.require_access
            callback.state.kubernetes = selected
        return evidence

    monkeypatch.setattr(kubernetes.K8SAuth, "read_config", configure)
    monkeypatch.setattr(kubernetes.KubernetesService, "_command", native)
    monkeypatch.setattr(kubernetes.K8SAuth, "run", authenticated)
    return target


def authenticated_kubernetes_service(
    target: Target, timeout: int = 30, *, state: SkillState | None = None
) -> KubernetesService:
    """Provide retained cluster access to tests of later Kubernetes service behavior.

    :param target: Synthetic cluster and optional frozen credential sources.
    :param timeout: Native command bound used by the service under test.
    :param state: Existing invocation for callback composition, or a fresh test state.
    :returns: Real service guarded by explicit declared and observed test access.
    """
    contexts = import_script_module("lib.python.core.delivery_state")
    kubernetes = import_script_module("lib.python.core.kubernetes.kubernetes")
    observations = import_script_module("lib.python.core.abstract.provider_runtime")
    plugins = import_script_module("lib.python.core.abstract.skill_plugin_type")
    specifications = import_script_module("lib.python.core.abstract.spec")
    invocation = contexts.DeliveryState() if state is None else state
    if invocation.spec is None:
        invocation.spec = specifications.load_spec()
    selected = _frozen_kubernetes_target(target)
    service = kubernetes.KubernetesService(selected, timeout=timeout, state=invocation)
    invocation.establish_runtime(
        plugins.SkillPluginType.KUBERNETES,
        observations.KubernetesRuntime(
            role="cluster-admin",
            identity="unit-admin",
            authority=service.configuration.server,
            credential_identity=service.credential_identity,
        ),
    )
    return service


def bind_kubernetes_fixture(state: SkillState, service: object) -> None:
    """Give an operation-only Kubernetes double the real retained access guard.

    :param state: Invocation used by the callback under test.
    :param service: Existing operation double whose transport behavior stays unchanged.
    :returns: None; the shared state retains the double with a real access prerequisite.
    """
    targets = import_script_module("lib.python.core.target")
    target = getattr(service, "target", None)
    if not isinstance(target, targets.Target):
        target = targets.Target(
            github=None,
            gitlab=None,
            kubernetes=targets.KubernetesTarget(
                "unit-context", "https://api.cluster.example.test:6443", None
            ),
            active_surfaces=("kubernetes",),
        )
    verified = authenticated_kubernetes_service(target, state=state)
    service.state = state
    service.target = verified.target
    service.configuration = verified.configuration
    service.require_access = verified.require_access
    state.kubernetes = service


def bind_harbor_fixture(state: SkillState, service: object) -> None:
    """Supply explicit native admin evidence to an operation-only Harbor double.

    :param state: Invocation used by the callback under test.
    :param service: Existing repository or tag-operation double.
    :returns: None; provider guards consume the retained declaration and observation.
    """
    registry = import_script_module("lib.python.core.harbor.registry_mirror")
    targets = import_script_module("lib.python.core.target")
    observations = import_script_module("lib.python.core.abstract.provider_runtime")
    plugins = import_script_module("lib.python.core.abstract.skill_plugin_type")
    specifications = import_script_module("lib.python.core.abstract.spec")
    if state.spec is None:
        state.spec = specifications.load_spec()
    original = getattr(service, "target", None)
    host = getattr(original, "host", "registry.example.test")
    project = getattr(original, "project", "unit")
    target = targets.HarborTarget(f"https://{host}", host, project)
    credential = getattr(service, "credential", None)
    if not isinstance(credential, registry.RegistryCredential):
        credential = registry.RegistryCredential(
            "unit-admin", "fixture-secret", "fixture"
        )
    if isinstance(service, registry.HarborService):
        assert service.invocation is state
        target = service.target
    else:
        service.target = target
        service.credential = credential
    state.harbor = service
    state.harbor_target = target
    state.harbor_credential = credential
    state.establish_runtime(
        plugins.SkillPluginType.HARBOR,
        observations.ProviderRuntime(
            role="admin",
            identity=credential.username,
            authority=target.url,
            credential_identity=registry._harbor_credential_identity(credential),
        ),
    )


def bind_github_fixture(state: SkillState, service: object) -> None:
    """Supply explicit repository identity to an operation-only GitHub double.

    :param state: Invocation used by the callback under test.
    :param service: Existing runner-operation double.
    :returns: None; provider callbacks retain genuine local authorization checks.
    """
    github = import_script_module("lib.python.core.github.github")
    targets = import_script_module("lib.python.core.target")
    plugins = import_script_module("lib.python.core.abstract.skill_plugin_type")
    specifications = import_script_module("lib.python.core.abstract.spec")
    if state.spec is None:
        state.spec = specifications.load_spec()
    target = targets.GitHubTarget("github.example.test", "unit/repo")
    session = github.ReadSession(
        target.host, {"GH_TOKEN": "fixture-token"}, "fixture", state, target.repository
    )
    state.github = service
    state.github_target = target
    state.github_session = session
    state.establish_runtime(
        plugins.SkillPluginType.GITHUB,
        github.GitHubRuntime(
            role="admin",
            identity="unit-admin",
            authority=f"https://{target.host}/repos/{target.repository}",
            credential_identity=github._github_credential_identity(session),
            repository_id=42,
            user_id=17,
        ),
    )


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
