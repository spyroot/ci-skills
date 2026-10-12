"""Typed state passed through declared image and runner delivery callbacks.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lib.python.core.abstract.context import ExecutionContext
from lib.python.core.abstract.skill_action import AuthAction
from lib.python.core.json_types import JSONObject

if TYPE_CHECKING:
    from lib.python.core.gitlab.github import GitHubService
    from lib.python.core.gitlab.gitlab_runner import RunnerRuntime
    from lib.python.core.harbor.registry_mirror import HarborService
    from lib.python.core.kubernetes.kubernetes import KubernetesService
    from lib.python.core.toolbox import ToolboxBuildResult


@dataclass(frozen=True)
class ProviderAuthAction(AuthAction):
    """Select existing target credentials and bound provider operations.

    :param target: Runtime target already resolved by the calling workflow.
    :param timeout: Positive maximum seconds for each native provider request.

    AuthAction defines the inherited target field.

    .. attribute :: timeout
        :type: int

        Positive maximum seconds for each native provider request.
    """

    timeout: int


@dataclass
class DeliveryState(ExecutionContext):
    """Retain private services and typed outputs between ordered callbacks.

    Authentication fills service fields before provider operations run. Producers
    assign image, runtime, and deployment fields before their declared consumers.
    ExecutionContext documents inherited argument/report fields; SkillState
    documents shared invocation settings, results, and cleanup evidence.

    :ivar kubernetes: Verified Kubernetes service assigned by K8SAuth.
    :ivar harbor: Private registry service assigned by HarborAuth.
    :ivar github: Verified GitHub service assigned by GitHubAuth.
    :ivar build_result: Image identity assigned by BuildToolbox.
    :ivar source_image: Immutable publication input assigned by plan or build.
    :ivar published_image: Verified image assigned by plan or HarborPush.
    :ivar runner_runtime: Private registration inputs assigned by provider setup.
    :ivar deployment_result: Safe workload read-back assigned by DeployRunner.
    :param access_evidence: Safe provider identity records, populated by authentication.

    .. attribute :: kubernetes
        :type: KubernetesService

        Verified Kubernetes service assigned by K8SAuth.

    .. attribute :: harbor
        :type: HarborService

        Private registry service assigned by HarborAuth.

    .. attribute :: github
        :type: GitHubService

        Verified GitHub service assigned by GitHubAuth.

    .. attribute :: build_result
        :type: ToolboxBuildResult

        Image identity assigned by BuildToolbox.

    .. attribute :: source_image
        :type: str

        Immutable publication input assigned by plan or build.

    .. attribute :: published_image
        :type: str

        Verified image assigned by plan or HarborPush.

    .. attribute :: runner_runtime
        :type: RunnerRuntime

        Private registration inputs assigned by provider setup.

    .. attribute :: deployment_result
        :type: JSONObject

        Safe workload read-back assigned by DeployRunner.

    .. attribute :: access_evidence
        :type: MutableMapping[str, JSONObject]

        Safe provider identity records, populated by authentication.
    """

    kubernetes: KubernetesService = field(init=False, repr=False)
    harbor: HarborService = field(init=False, repr=False)
    github: GitHubService = field(init=False, repr=False)
    build_result: ToolboxBuildResult = field(init=False, repr=False)
    source_image: str = field(init=False, repr=False)
    published_image: str = field(init=False, repr=False)
    runner_runtime: RunnerRuntime = field(init=False, repr=False)
    deployment_result: JSONObject = field(init=False, repr=False)
    access_evidence: MutableMapping[str, JSONObject] = field(default_factory=dict)
