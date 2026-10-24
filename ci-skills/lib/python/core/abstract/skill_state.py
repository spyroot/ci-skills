"""Mutable invocation state shared by every callback in one executor.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
import pickle
from collections.abc import Mapping, MutableMapping, MutableSequence
from dataclasses import dataclass, field
from typing import ClassVar, Self, TypedDict, TypeVar

from lib.python.core.abstract.provider_runtime import (
    KubernetesRuntime,
    ProviderRoleError,
    ProviderRoleMismatch,
    ProviderRuntime,
)
from lib.python.core.abstract.skill_plugin_type import SkillPluginType
from lib.python.core.abstract.skill_result import SkillResult
from lib.python.core.abstract.spec import Spec
from lib.python.core.json_types import JSONObject


class CleanupFailure(TypedDict):
    """Report one callback cleanup failure without retaining exception objects.

    :param callback: Concrete callback class whose cleanup hook failed.
    :param reason: Redacted failure text safe for the workflow report.

    .. attribute :: callback
        :type: str

        Concrete callback class whose cleanup hook failed.

    .. attribute :: reason
        :type: str

        Redacted failure text safe for the workflow report.
    """

    callback: str
    reason: str


@dataclass
class SkillState:
    """Own shared invocation data, hook history, and per-callback results.

    Concrete states add domain fields. Every callback retains this same mutable
    instance, so later callbacks observe assignments made by earlier callbacks.

    :param result: Last successful hook result; empty before execution.
    :param records: Successful hook results in execution order, including cleanup.
    :param results: Each bound callback's most recent run result, initially empty.
    :param cleanup_failures: Callback names and sanitized reasons from failed cleanup hooks.
    :param cleanup_results: Successful nonempty cleanup-hook results with identities.
    :param failed: Whether the current run failed or an operation remained incomplete.
    :param name: Caller-supplied workflow name for reporting.
    :param debug: Caller-selected diagnostic detail setting.
    :param verbose: Caller-selected verbose output setting.
    :param environment: Caller-supplied invocation environment values.
    :param plugin_ready: Successful configuration checks for this invocation only.
    :param spec: Declared provider requirements, loaded before callback binding.
    :param gitlab_runtime: GitLab identity and access observed during this invocation.
    :param kubernetes_runtime: Kubernetes identity and access observed during this invocation.
    :param github_runtime: GitHub identity and access observed during this invocation.
    :param harbor_runtime: Harbor identity and access observed during this invocation.

    .. attribute :: result
        :type: SkillResult

        Last successful hook result; empty before execution.

    .. attribute :: records
        :type: MutableSequence[SkillResult]

        Successful hook results in execution order, including cleanup.

    .. attribute :: results
        :type: MutableMapping[object, SkillResult]

        Each bound callback's most recent run result, initially empty.

    .. attribute :: cleanup_failures
        :type: MutableSequence[CleanupFailure]

        Callback names and sanitized reasons from failed cleanup hooks.

    .. attribute :: cleanup_results
        :type: MutableSequence[SkillResult]

        Successful nonempty cleanup-hook results with callback and hook identities.

    .. attribute :: failed
        :type: bool

        Whether the current run failed or an operation remained incomplete.

    .. attribute :: name
        :type: str

        Caller-supplied workflow name for reporting.

    .. attribute :: debug
        :type: bool

        Caller-selected diagnostic detail setting.

    .. attribute :: verbose
        :type: bool

        Caller-selected verbose output setting.

    .. attribute :: environment
        :type: MutableMapping[str, str]

        Caller-supplied invocation environment values.

    .. attribute :: plugin_ready
        :type: MutableMapping[SkillPluginType, bool]

        Configuration readiness keyed by plugin identity, initially empty.

    .. attribute :: spec
        :type: Spec | None

        Provider requirements supplied by the caller or the installed specification.

    .. attribute :: gitlab_runtime
        :type: ProviderRuntime

        Write-once GitLab observation; its role is initially None.

    .. attribute :: kubernetes_runtime
        :type: KubernetesRuntime

        Write-once Kubernetes observation; its cluster_role is initially None.

    .. attribute :: github_runtime
        :type: ProviderRuntime

        Write-once GitHub observation; its role is initially None.

    .. attribute :: harbor_runtime
        :type: ProviderRuntime

        Write-once Harbor observation; its role is initially None.

    .. attribute :: json_encoder
        :type: ClassVar[type[json.JSONEncoder]]

        Encoder used by to_json for the reportable state values.
    """

    result: SkillResult = field(default_factory=dict)
    records: MutableSequence[SkillResult] = field(default_factory=list)
    results: MutableMapping[object, SkillResult] = field(
        default_factory=dict, repr=False
    )
    cleanup_failures: MutableSequence[CleanupFailure] = field(default_factory=list)
    cleanup_results: MutableSequence[SkillResult] = field(default_factory=list)
    failed: bool = False
    name: str = ""
    debug: bool = False
    verbose: bool = False
    environment: MutableMapping[str, str] = field(default_factory=dict)
    plugin_ready: MutableMapping[SkillPluginType, bool] = field(
        default_factory=dict, repr=False
    )
    spec: Spec | None = field(default=None, repr=False)
    gitlab_runtime: ProviderRuntime = field(default_factory=ProviderRuntime, repr=False)
    kubernetes_runtime: KubernetesRuntime = field(
        default_factory=KubernetesRuntime, repr=False
    )
    github_runtime: ProviderRuntime = field(default_factory=ProviderRuntime, repr=False)
    harbor_runtime: ProviderRuntime = field(default_factory=ProviderRuntime, repr=False)
    json_encoder: ClassVar[type[json.JSONEncoder]] = json.JSONEncoder

    def provider_spec(self, plugin_type: SkillPluginType) -> JSONObject:
        """Select one provider's declaration without interpreting its requirements.

        :param plugin_type: Provider key declared by the callback base.
        :returns: That provider's specification object.
        :raises ValueError: No specification or provider section has been bound.
        """
        if self.spec is None or plugin_type.value not in self.spec:
            raise ValueError(f"{plugin_type.value}_specification_missing")
        return self.spec[plugin_type.value]

    def provider_runtime(self, plugin_type: SkillPluginType) -> ProviderRuntime:
        """Read the typed runtime observation selected by the callback's provider.

        :param plugin_type: Provider key declared by the callback base.
        :returns: Immutable observation, including its initial null role.
        :raises ValueError: The plugin has no external provider runtime.
        """
        runtimes = {
            SkillPluginType.GITLAB: self.gitlab_runtime,
            SkillPluginType.KUBERNETES: self.kubernetes_runtime,
            SkillPluginType.GITHUB: self.github_runtime,
            SkillPluginType.HARBOR: self.harbor_runtime,
        }
        if plugin_type not in runtimes:
            raise ValueError(f"{plugin_type.value}_runtime_unavailable")
        return runtimes[plugin_type]

    def establish_runtime(
        self, plugin_type: SkillPluginType, observation: ProviderRuntime
    ) -> None:
        """Write a provider's actual observation once, after authentication.

        :param plugin_type: Provider key declared by its authentication callback.
        :param observation: Observed identity, role, and authentication binding.
        :raises TypeError: Kubernetes receives a non-Kubernetes runtime observation.
        :raises ProviderRoleError: The observation has no runtime role.
        :raises ValueError: Runtime is already established or the plugin has no runtime.
        """  # noqa: DOC502 - provider_runtime rejects plugins without runtime state.
        if self.provider_runtime(plugin_type).role is not None:
            raise ValueError(f"{plugin_type.value}_runtime_already_established")
        if observation.role is None:
            raise ProviderRoleError(f"{plugin_type.value}_runtime_role_missing")
        if plugin_type is SkillPluginType.KUBERNETES:
            if not isinstance(observation, KubernetesRuntime):
                raise TypeError("kubernetes runtime must be KubernetesRuntime")
            self.kubernetes_runtime = observation
        elif plugin_type is SkillPluginType.GITLAB:
            self.gitlab_runtime = observation
        elif plugin_type is SkillPluginType.GITHUB:
            self.github_runtime = observation
        elif plugin_type is SkillPluginType.HARBOR:
            self.harbor_runtime = observation

    def require_runtime(
        self,
        plugin_type: SkillPluginType,
        expected_role: str,
        *,
        authority: str | None = None,
        credential_identity: str | None = None,
    ) -> ProviderRuntime:
        """Validate retained access locally without authenticating or populating state.

        :param plugin_type: Provider key declared by the callback base.
        :param expected_role: Required role interpreted from that provider's declaration.
        :param authority: Provider authority the pending operation will use, when supplied.
        :param credential_identity: Bound authentication identifier, when supplied.
        :returns: The existing observation after all requested comparisons succeed.
        :raises ProviderRoleError: The runtime role has not been observed.
        :raises ProviderRoleMismatch: The observed role differs from the declared role.
        :raises ValueError: The plugin has no runtime or its binding differs.
        """  # noqa: DOC502 - provider_runtime rejects plugins without runtime state.
        runtime = self.provider_runtime(plugin_type)
        if runtime.role is None:
            raise ProviderRoleError(f"{plugin_type.value}_identity_preflight_missing")
        if runtime.role != expected_role:
            raise ProviderRoleMismatch(
                f"{plugin_type.value}_required_role_missing:{expected_role}"
            )
        if authority is not None and runtime.authority != authority:
            raise ValueError(f"{plugin_type.value}_runtime_authority_mismatch")
        if (
            credential_identity is not None
            and runtime.credential_identity != credential_identity
        ):
            raise ValueError(f"{plugin_type.value}_runtime_credential_mismatch")
        return runtime

    def trace(self, event: str, details: Mapping[str, object]) -> None:
        """Accept an execution event; concrete states may send it to a logger.

        :param event: Lifecycle or transport event emitted by the executor.
        :param details: Bounded event metadata supplied by the event producer.
        """

    def is_config_checked(self, plugin_type: SkillPluginType) -> bool:
        """Read whether this invocation validated a plugin's configuration.

        :param plugin_type: Enum identity declared by the callback's plugin.
        :returns: True only after successful configuration validation.
        :raises TypeError: If the caller supplies a string instead of the enum.
        """
        if not isinstance(plugin_type, SkillPluginType):
            raise TypeError("plugin_type must be SkillPluginType")
        if not hasattr(self, "plugin_ready"):
            self.plugin_ready = {}
        return self.plugin_ready.get(plugin_type, False)

    def state_dict(self) -> SkillResult:
        """Select the common reportable values without serializing provider services.

        Concrete state subclasses override this method to expose additional fields.
        Per-callback keys, failure objects, and cleanup failures are not included here.

        :returns: Mapping containing name, debug/verbose flags, environment, current
            result, and ordered hook records.
        """
        return {
            "name": self.name,
            "debug": self.debug,
            "verbose": self.verbose,
            "environment": dict(self.environment),
            "result": dict(self.result),
            "records": [dict(record) for record in self.records],
        }

    def __str__(self) -> str:
        """Render the common invocation settings.

        :returns: A human-readable summary of this state.
        """
        return f"name: {self.name}\ndebug: {self.debug}\nverbose: {self.verbose}"

    def to_json(self) -> str:
        """Encode the reportable state selected by state_dict.

        :returns: Indented JSON text containing the selected state values.
        :raises TypeError: A selected value is unsupported by json_encoder.
        :raises ValueError: Selected values contain a circular reference.
        """  # noqa: DOC502 - json.dumps reports unsupported or circular values.
        return json.dumps(self.state_dict(), cls=self.json_encoder, indent=4)

    @classmethod
    def load_state_from_file(cls, file_path: str) -> Self:
        """Restore a concrete state from a caller-selected trusted pickle file.

        :param file_path: Path previously written by save_state_to_file.
        :returns: Deserialized concrete state with provider authentication observations reset.
        :raises OSError: The file cannot be opened or read.
        :raises EOFError: The file ends before a pickle value can be read.
        :raises pickle.UnpicklingError: The file contains invalid pickle data.
        :raises ImportError: A class required by the stored value cannot be imported.
        :raises AttributeError: A class or attribute referenced by the pickle is unavailable.
        :raises TypeError: The stored object has a different state type.
        """  # noqa: DOC503 - file access and pickle.load propagate decode failures.
        with open(file_path, "rb") as handle:
            state = pickle.load(handle)
        if not isinstance(state, cls):
            raise TypeError(f"Expected {cls.__name__}, got {type(state).__name__}.")
        state.gitlab_runtime = ProviderRuntime()
        state.kubernetes_runtime = KubernetesRuntime()
        state.github_runtime = ProviderRuntime()
        state.harbor_runtime = ProviderRuntime()
        state.plugin_ready = {}
        return state

    def save_state_to_file(self, file_path: str) -> None:
        """Serialize this state to the caller's explicit file.

        :param file_path: Destination for the serialized concrete state.
        :raises OSError: If the destination cannot be written.
        :raises pickle.PicklingError: If this state cannot be serialized.
        """  # noqa: DOC502 - file access and pickle.dump propagate write failures.
        with open(file_path, "wb") as handle:
            pickle.dump(self, handle)


StateT = TypeVar("StateT", bound=SkillState)
