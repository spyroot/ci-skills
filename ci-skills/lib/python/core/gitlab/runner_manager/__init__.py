"""Public GitLab runner-manager action and callback surface.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from .action import (
    GitLabRunnerManagerAction,
    GitLabRunnerManagerState,
    ManagerWorkloadAction,
)
from .callbacks import (
    GitLabManagerAuth,
    GitLabRunnerRuntime,
    GuardGitLabRunnerManager,
    ObserveGitLabRunner,
    VerifyGitLabRunnerOnline,
    VerifyGitLabRunnerPreserved,
    retain_manager_resources,
)
from .token import (
    BoundRunnerToken,
    DeferredRunnerToken,
    NoRunnerToken,
    RunnerTokenSelection,
    read_runner_token,
)
from .workload import (
    DeleteManagerRunner,
    DeployManagerRunner,
    OpenManagerRunScope,
    PullManagerImage,
    SelectManagerPlanScope,
    SelectResolvedManagerScope,
    workload_action,
)

__all__ = (
    "BoundRunnerToken",
    "DeferredRunnerToken",
    "DeleteManagerRunner",
    "DeployManagerRunner",
    "GitLabManagerAuth",
    "GitLabRunnerManagerAction",
    "GitLabRunnerManagerState",
    "GitLabRunnerRuntime",
    "GuardGitLabRunnerManager",
    "ManagerWorkloadAction",
    "NoRunnerToken",
    "ObserveGitLabRunner",
    "OpenManagerRunScope",
    "PullManagerImage",
    "RunnerTokenSelection",
    "SelectManagerPlanScope",
    "SelectResolvedManagerScope",
    "VerifyGitLabRunnerOnline",
    "VerifyGitLabRunnerPreserved",
    "read_runner_token",
    "retain_manager_resources",
    "workload_action",
)
