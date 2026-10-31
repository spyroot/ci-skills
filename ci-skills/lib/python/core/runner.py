"""Shared runner action and runtime values consumed by provider callbacks.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Optional

from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.json_types import JSONObject
from lib.python.core.kubernetes.podman_runtime import PodmanRuntimeSettings

RUNTIME_ROOT: Final = "/ci-skills/runner"


REGISTRATION_SECRET_SUFFIX: Final = "-registration"
REGISTRY_SECRET_SUFFIX: Final = "-registry"
PULL_JOB_SUFFIX: Final = "-pull"


@dataclass(frozen=True)
class RunnerRuntime:
    """Retain provider bootstrap commands and private registration material.

    :param init_command: Native registration command run only by the init container.
    :param command: Runner process command after registration completes.
    :param environment: Registration environment entries and Secret references.
    :param readiness_command: Optional readiness command, empty when unnecessary.
    :param credential: Private key/value data written only to the owned Secret.

    .. attribute :: init_command
        :type: Sequence[str]

        Native registration command run only by the init container.

    .. attribute :: command
        :type: Sequence[str]

        Runner process command after registration completes.

    .. attribute :: environment
        :type: Sequence[JSONObject]

        Registration environment entries and Secret references.

    .. attribute :: readiness_command
        :type: Sequence[str]

        Optional readiness command, empty when unnecessary.

    .. attribute :: credential
        :type: Mapping[str, str]

        Private key/value data written only to the owned Secret.
    """

    init_command: Sequence[str]
    command: Sequence[str]
    environment: Sequence[JSONObject]
    readiness_command: Sequence[str]
    credential: Mapping[str, str] = field(repr=False)


@dataclass(frozen=True)
class RunnerAction(SkillAction):
    """Select one runner workload and its requested registration inputs.

    :param namespace: Runtime-selected workload namespace.
    :param name: Exact workload and provider runner name.
    :param source_root: Runner installation directory inside the supplied image.
    :param labels: Provider labels required during independent read-back.
    :param timeout: Positive maximum seconds for readiness and temporary Jobs.
    :param ownership_id: Optional shared operation identity for run-owned resources.
    :param podman: Optional exact Podman placement and runtime settings.

    .. attribute :: namespace
        :type: str

        Runtime-selected workload namespace.

    .. attribute :: name
        :type: str

        Exact workload and provider runner name.

    .. attribute :: source_root
        :type: str

        Runner installation directory inside the supplied image.

    .. attribute :: labels
        :type: Sequence[str]

        Provider labels required during independent read-back.

    .. attribute :: timeout
        :type: int

        Positive maximum seconds for readiness and temporary Jobs.

    .. attribute :: ownership_id
        :type: Optional[str]

        Explicit run ownership identity; the workload name is the fallback.

    .. attribute :: podman
        :type: Optional[PodmanRuntimeSettings]

        Podman runtime settings for consumers that require them.
    """

    namespace: str
    name: str
    source_root: str
    labels: Sequence[str]
    timeout: int
    ownership_id: Optional[str] = None
    podman: Optional[PodmanRuntimeSettings] = None

    @property
    def operation(self) -> str:
        """Select the ownership label used by shared Kubernetes resources.

        :returns: Explicit run identity, or the existing workload name fallback.
        """
        return self.ownership_id or self.name
