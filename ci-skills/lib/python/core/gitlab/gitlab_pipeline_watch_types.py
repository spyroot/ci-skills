"""Typed values and fixed bounds for GitLab pipeline watching.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional

from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.json_types import JSONObject

MAX_PIPELINES = 50
MAX_CHANGES = 50
MAX_CHOICES = 12
MAX_GRAPH_DEPTH = 5
PAGE_SIZE = 100
MAX_PAGES = 5
PIPELINE_WATCH_KIND = "gitlab_pipeline_watch"
DEFAULT_WATCH_INTERVAL = 10.0
DEFAULT_WATCH_TIMEOUT = 3600.0
SETTLED_PIPELINE_STATUSES = frozenset(
    {"success", "failed", "canceled", "skipped", "manual"}
)
SETTLED_JOB_STATUSES = frozenset({"success", "failed", "canceled", "skipped", "manual"})


@dataclass(frozen=True, order=True)
class PipelineKey:
    """Identify one pipeline independently of its discovery route.

    .. attribute :: project_id
        :type: int

        Positive GitLab project ID.

    .. attribute :: pipeline_id
        :type: int

        Positive GitLab pipeline ID.
    """

    project_id: int
    pipeline_id: int


@dataclass(frozen=True)
class PipelineRoute:
    """Retain how the root, bridge, or related scan found one pipeline.

    .. attribute :: key
        :type: PipelineKey

        Provider project and pipeline identity.

    .. attribute :: via
        :type: str

        Root, related, or bridge job discovery label.

    .. attribute :: depth
        :type: int

        Bridge depth from the root, with related pipelines at zero.
    """

    key: PipelineKey
    via: str
    depth: int


@dataclass(frozen=True)
class WatchJob:
    """Keep only job evidence used by pipeline watch and result choices.

    .. attribute :: id
        :type: int

        Positive GitLab job ID.

    .. attribute :: name
        :type: str

        Provider job name.

    .. attribute :: stage
        :type: str

        Provider stage name.

    .. attribute :: status
        :type: str

        Provider job status.

    .. attribute :: failure_reason
        :type: Optional[str]

        Optional provider failure classification.

    .. attribute :: web_url
        :type: Optional[str]

        Optional same-provider job URL used by a retrieve choice.
    """

    id: int
    name: str
    stage: str
    status: str
    failure_reason: Optional[str]
    web_url: Optional[str]


@dataclass(frozen=True)
class PipelineObservation:
    """Describe one pipeline, its bounded jobs, and discovered child routes.

    .. attribute :: route
        :type: PipelineRoute

        Discovery identity for this pipeline.

    .. attribute :: status
        :type: str

        Current pipeline status.

    .. attribute :: ref
        :type: str

        Exact provider ref.

    .. attribute :: sha
        :type: str

        Exact provider commit SHA.

    .. attribute :: jobs
        :type: Sequence[WatchJob]

        Bounded jobs used for counts and failed-job choices.

    .. attribute :: children
        :type: Sequence[PipelineRoute]

        Downstream pipelines discovered from bridge jobs.

    .. attribute :: errors
        :type: Sequence[JSONObject]

        Bounded collection errors for this pipeline.
    """

    route: PipelineRoute
    status: str
    ref: str
    sha: str
    jobs: Sequence[WatchJob]
    children: Sequence[PipelineRoute]
    errors: Sequence[JSONObject]

    def record(self) -> JSONObject:
        """Render the compact pipeline record required by the watch contract.

        :returns: Identity, route, status, and job counts without inline jobs.
        """
        counts = Counter(job.status for job in self.jobs)
        return {
            "project_id": self.route.key.project_id,
            "pipeline_id": self.route.key.pipeline_id,
            "via": self.route.via,
            "depth": self.route.depth,
            "status": self.status,
            "jobs": {
                "total": len(self.jobs),
                "by_status": dict(sorted(counts.items())),
            },
        }


@dataclass(frozen=True)
class PipelineWatchAction(SkillAction):
    """Select the root, optional related graph, and bounded wait controls.

    .. attribute :: pipeline_id
        :type: int

        Positive root pipeline ID.

    .. attribute :: related_name
        :type: Optional[str]

        Optional regular expression selecting newer root-project pipelines.

    .. attribute :: interval
        :type: float

        Positive seconds between polls.

    .. attribute :: timeout
        :type: float

        Positive total watch deadline in seconds.
    """

    pipeline_id: int
    related_name: Optional[str]
    interval: float
    timeout: float
