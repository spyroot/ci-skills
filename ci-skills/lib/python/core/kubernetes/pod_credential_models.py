"""Typed inputs and shared state for private tool Pod credentials.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import MutableSequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Final

from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.json_types import JSONObject
from lib.python.core.kubernetes.tool_pod_models import ArtifactHandle, ToolPodState

KUBECONFIG_NAME: Final = "kubeconfig"
PULL_SECRET_NAME: Final = "pull-secret.json"


def _required_token(value: str, field_name: str) -> str:
    """Require one nonempty native argument without changing its value.

    :param value: Caller-selected Kubernetes or OpenShift argument.
    :param field_name: Stable field name used in an actionable error.
    :returns: Exact caller value.
    :raises ValueError: If the value is empty or contains a control character.
    """
    if (
        not isinstance(value, str)
        or not value
        or any(character in value for character in ("\x00", "\n", "\r"))
    ):
        raise ValueError(f"pod_credential_{field_name}_invalid")
    return value


def _private_directory(value: str) -> str:
    """Require an absolute non-root Pod path without normalizing caller text.

    :param value: Run-owned directory selected by the composing workflow.
    :returns: Exact absolute path.
    :raises ValueError: If the path is root, relative, traversing, or unsafe.
    """
    _required_token(value, "private_directory")
    candidate = PurePosixPath(value)
    if not candidate.is_absolute() or candidate == PurePosixPath("/"):
        raise ValueError("pod_credential_private_directory_invalid")
    if ".." in candidate.parts:
        raise ValueError("pod_credential_private_directory_invalid")
    return value


@dataclass(frozen=True)
class NativePermission:
    """One exact native authorization read-back required by a consumer.

    :param verb: Kubernetes authorization verb accepted by ``oc auth can-i``.
    :param resource: Native resource token, including a group or subresource.
    :param namespace: Exact namespace, or empty text for cluster scope.
    :param all_namespaces: Check the operation across every namespace.

    .. attribute :: verb
        :type: str

        Kubernetes authorization verb accepted by the native client.

    .. attribute :: resource
        :type: str

        Native resource token, including a group or subresource.

    .. attribute :: namespace
        :type: str

        Exact namespace, or empty text for cluster scope.

    .. attribute :: all_namespaces
        :type: bool

        Whether the native authorization check spans every namespace.
    """

    verb: str
    resource: str
    namespace: str = ""
    all_namespaces: bool = False

    def __post_init__(self) -> None:
        """Reject empty or control-bearing native authorization arguments.

        :raises ValueError: If a native argument is empty or contains controls.
        :raises TypeError: If the all-namespace selector is not a boolean.
        """  # noqa: DOC502 - _required_token raises the declared ValueError.
        _required_token(self.verb, "permission_verb")
        _required_token(self.resource, "permission_resource")
        if self.namespace:
            _required_token(self.namespace, "permission_namespace")
        if not isinstance(self.all_namespaces, bool):
            raise TypeError("pod_credential_permission_all_namespaces_invalid")
        if self.namespace and self.all_namespaces:
            raise ValueError("pod_credential_permission_scope_conflict")

    def arguments(self) -> tuple[str, ...]:
        """Build exact ``oc auth can-i`` arguments without shell interpolation.

        :returns: Native authorization arguments for this permission.
        """
        scope = (
            ("--all-namespaces",)
            if self.all_namespaces
            else (("--namespace", self.namespace) if self.namespace else ())
        )
        return ("auth", "can-i", self.verb, self.resource, *scope, "--quiet")

    def as_dict(self) -> JSONObject:
        """Render non-secret permission evidence.

        :returns: Exact verb, resource, namespace scope, and positive read-back.
        """
        return {
            "verb": self.verb,
            "resource": self.resource,
            "namespace": self.namespace,
            "all_namespaces": self.all_namespaces,
            "allowed": True,
        }


@dataclass(frozen=True)
class PullSecretReference:
    """Select one existing Kubernetes Secret data field.

    :param namespace: Namespace containing the existing Secret.
    :param name: Exact Secret name.
    :param key: Data key containing native registry configuration bytes.

    .. attribute :: namespace
        :type: str

        Namespace containing the existing Secret.

    .. attribute :: name
        :type: str

        Exact Secret name.

    .. attribute :: key
        :type: str

        Data key containing native registry configuration bytes.
    """

    namespace: str
    name: str
    key: str

    def __post_init__(self) -> None:
        """Reject missing or control-bearing Secret selectors.

        :raises ValueError: If namespace, name, or key is invalid.
        """  # noqa: DOC502 - _required_token raises the declared ValueError.
        _required_token(self.namespace, "pull_secret_namespace")
        _required_token(self.name, "pull_secret_name")
        _required_token(self.key, "pull_secret_key")

    def as_dict(self) -> JSONObject:
        """Render only the non-secret provider reference.

        :returns: Namespace, Secret name, and selected data-key name.
        """
        return {
            "namespace": self.namespace,
            "name": self.name,
            "key": self.key,
        }


@dataclass(frozen=True)
class CredentialAction(SkillAction):
    """Bind private Pod paths and provider reads for one native consumer.

    :param private_directory: Run-owned private directory inside the prepared Pod.
    :param required_permissions: Native operations the workload must be able to run.
    :param pull_secret: Existing Secret field selected by the OCP consumer.

    .. attribute :: private_directory
        :type: str

        Run-owned private directory inside the prepared Pod.

    .. attribute :: required_permissions
        :type: tuple[NativePermission, ...]

        Native operations the workload must be able to run.

    .. attribute :: pull_secret
        :type: PullSecretReference

        Existing Secret field selected by the OCP consumer.
    """

    private_directory: str
    required_permissions: tuple[NativePermission, ...]
    pull_secret: PullSecretReference

    def __post_init__(self) -> None:
        """Validate only ci-skills path ownership and typed wrapper values.

        :raises ValueError: If the private path or a nested native value is invalid.
        :raises TypeError: If a permission or pull-secret reference has another type.
        """
        _private_directory(self.private_directory)
        if not isinstance(self.pull_secret, PullSecretReference):
            raise TypeError("pod_credential_pull_secret_reference_invalid")
        if any(
            not isinstance(permission, NativePermission)
            for permission in self.required_permissions
        ):
            raise TypeError("pod_credential_permission_invalid")
        if len(set(self.required_permissions)) != len(self.required_permissions):
            raise ValueError("pod_credential_permission_duplicate")

    @property
    def kubeconfig_path(self) -> PurePosixPath:
        """Return the exact run-private workload kubeconfig path.

        :returns: Private directory joined with the fixed kubeconfig filename.
        """
        return PurePosixPath(self.private_directory) / KUBECONFIG_NAME

    @property
    def pull_secret_path(self) -> PurePosixPath:
        """Return the exact run-private registry configuration path.

        :returns: Private directory joined with the fixed pull-secret filename.
        """
        return PurePosixPath(self.private_directory) / PULL_SECRET_NAME


@dataclass
class PodCredentialState(ToolPodState):
    """Retain private files and safe read-back for one prepared tool Pod.

    Secret-bearing file handles are private state and are never selected by the
    inherited report boundary. The composing state may inherit this class with
    another ``ToolPodState`` specialization.

    .. attribute :: caller_identity
        :type: str

        Identity verified by the caller-side Kubernetes service.

    .. attribute :: workload_identity
        :type: str

        Same identity read back by the native client inside the prepared Pod.

    .. attribute :: kubeconfig_file
        :type: ArtifactHandle

        Private staged kubeconfig identity, excluded from reports.

    .. attribute :: pull_secret_file
        :type: ArtifactHandle

        Private staged registry configuration identity, excluded from reports.

    .. attribute :: kubeconfig_exec_plugin
        :type: bool

        Whether the selected flattened kubeconfig invokes an external executable.

    .. attribute :: credential_permissions
        :type: MutableSequence[JSONObject]

        Positive native authorization read-backs in requested order.
    """

    caller_identity: str = field(init=False)
    workload_identity: str = field(init=False)
    kubeconfig_file: ArtifactHandle = field(init=False, repr=False)
    pull_secret_file: ArtifactHandle = field(init=False, repr=False)
    kubeconfig_exec_plugin: bool = False
    credential_permissions: MutableSequence[JSONObject] = field(default_factory=list)
