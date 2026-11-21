"""Dispatch validated Agent intent through its confirmed native workflow."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from lib.python.core.action_modes import ActionMode
from lib.python.core.cli_api import RequestDeadline
from lib.python.core.gitlab.cluster_agent.absence import LogicalAgentAbsence
from lib.python.core.gitlab.cluster_agent.authority import AgentAuthorities
from lib.python.core.gitlab.cluster_agent.ci_access import (
    ExpectedCiAccess,
    ProducerBlob,
    VerifiedCiAccess,
    verify_ci_access,
)
from lib.python.core.gitlab.cluster_agent.connection import AgentConnectivity
from lib.python.core.gitlab.cluster_agent.delete import (
    AgentCleanup,
    delete_tracked_agent,
    plan_tracked_delete,
)
from lib.python.core.gitlab.cluster_agent.fresh import confirm_and_connect_fresh
from lib.python.core.gitlab.cluster_agent.plan import (
    FreshConnectPlan,
    observe_repeat_connect,
    observe_repeat_delete,
    prepare_fresh_connect,
)
from lib.python.core.gitlab.cluster_agent.provider import AgentRegistration
from lib.python.core.gitlab.cluster_agent.recovery import (
    RecoveryRecord,
    locate_agent_run,
)
from lib.python.core.gitlab.cluster_agent.selection import (
    CHART_ARCHIVE,
    CHART_SHA256,
    IMAGE_TAG,
    ConnectSelection,
    select_connect,
)
from lib.python.core.gitlab.cluster_agent.terms import CONNECT, DELETE, VERIFY
from lib.python.core.helm.native import pull_verified_chart
from lib.python.core.json_types import JSONObject, JSONValue, json_integer, json_string
from lib.python.core.kubernetes.kubernetes import KubernetesError
from lib.python.core.oci import inspect_image_identity
from lib.python.sys.status import APPLIED, NO_OP, PLANNED, VERIFIED


@dataclass(frozen=True, slots=True)
class AgentOutcome:
    """Retain native observations for the Agent command's report adapter.

    .. attribute :: action
        :type: str

        Applied, verified, planned, or unchanged operation outcome.

    .. attribute :: selection
        :type: ConnectSelection

        Exact project and run selected from caller intent or recovered ownership.

    .. attribute :: digest
        :type: str | None

        Confirmation for connect and delete, or None for read-only verify.

    .. attribute :: before
        :type: RecoveryRecord | None

        Original journal, when the invocation found an existing installation.

    .. attribute :: observation
        :type: FreshConnectPlan | AgentConnectivity | AgentCleanup | LogicalAgentAbsence | None

        Native observations provided by the operation just executed.

    .. attribute :: ci_access
        :type: VerifiedCiAccess | None

        Independently verified producer and job evidence from verify.
    """

    action: str
    selection: ConnectSelection
    digest: str | None
    before: RecoveryRecord | None
    observation: (
        FreshConnectPlan | AgentConnectivity | AgentCleanup | LogicalAgentAbsence | None
    )
    ci_access: VerifiedCiAccess | None = None


def _optional(value: JSONValue) -> str | None:
    return None if value is None else json_string(value)


def _delete(
    authorities: AgentAuthorities,
    selection: ConnectSelection,
    record: RecoveryRecord | None,
    confirmation: str | None,
    deadline: RequestDeadline,
) -> AgentOutcome:
    gitlab, kubernetes = authorities.gitlab, authorities.kubernetes
    if record is None:
        digest, absence = observe_repeat_delete(
            authorities,
            selection,
            deadline=deadline,
            confirmation=confirmation,
        )
        return AgentOutcome(
            PLANNED if confirmation is None else NO_OP, selection, digest, None, absence
        )
    if confirmation is None:
        digest = plan_tracked_delete(
            gitlab, kubernetes, selection, record, timeout=deadline.remaining()
        )
        return AgentOutcome(PLANNED, selection, digest, record, None)
    cleaned = delete_tracked_agent(
        gitlab,
        kubernetes,
        selection,
        record,
        timeout=deadline.remaining(),
        confirmation=confirmation,
    )
    return AgentOutcome(APPLIED, selection, confirmation, record, cleaned)


def _verify(
    authorities: AgentAuthorities,
    selection: ConnectSelection,
    connected: AgentConnectivity,
    producers: Mapping[str, ProducerBlob],
    job_url: str,
    deadline: RequestDeadline,
) -> VerifiedCiAccess:
    document = connected.record.document
    return deadline.call(
        verify_ci_access,
        authorities.gitlab,
        job_url,
        ExpectedCiAccess(
            selection.project,
            AgentRegistration(
                json_integer(document["agent_id"]),
                selection.agent_name,
                selection.project.id,
            ),
            selection.scope.run_id,
            authorities.kas.proxy_url,
            authorities.cluster_uid,
            selection.scope.namespace,
            json_string(document["namespace_uid"]),
            producers,
        ),
    )


def run_agent_workflow(
    authorities: AgentAuthorities,
    intent: JSONObject,
    mode: ActionMode,
    *,
    deadline: RequestDeadline,
    confirmation: str | None = None,
    producers: Mapping[str, ProducerBlob] | None = None,
) -> AgentOutcome:
    """Run one native operation after intent and provider authentication are validated.

    :param authorities: Selected GitLab and Kubernetes handles and candidate identity.
    :param intent: Already schema-validated and normalized command intent.
    :param mode: Common command execution mode for this operation.
    :param deadline: Shared absolute budget for provider and artifact operations.
    :param confirmation: Exact operation digest provided only for apply.
    :param producers: Trusted producer bytes' identities for read-only verify.
    :returns: Bounded native observations for rendering the Agent result.
    :raises ValueError: Controls or proof inputs are inconsistent before dispatch.
    :raises KubernetesError: Required native ownership cannot be resolved.
    """  # noqa: DOC503 - Artifact readers and individual workflows propagate native errors.
    operation = intent["operation"]
    run_id = _optional(intent["run_id"])
    agent_name = _optional(intent["agent_name"])
    namespace = _optional(intent["namespace"])
    verify = operation == VERIFY
    if (
        intent["project"] != authorities.gitlab.session.target_reference
        or operation not in (CONNECT, DELETE, VERIFY)
        or (mode == ActionMode.APPLY) != bool(confirmation)
        or (verify and mode != ActionMode.READ)
        or (not verify and mode not in (ActionMode.LIVE_PLAN, ActionMode.APPLY))
        or ((mode == ActionMode.APPLY or operation != CONNECT) and not run_id)
        or (operation == DELETE and not agent_name)
        or (verify and (not producers or not intent["job_url"]))
    ):
        raise ValueError("agent_workflow_intent_invalid")
    existing = (
        locate_agent_run(
            authorities,
            run_id,
            agent_name=agent_name,
            namespace=namespace,
            deadline=deadline,
        )
        if run_id is not None
        else None
    )
    if existing is None:
        if verify:
            raise KubernetesError("agent_verify_journal_absent")
        selection = select_connect(
            authorities.project,
            project_name=_optional(intent["project_name"]),
            agent_name=agent_name,
            namespace=namespace,
            run_id=run_id,
        )
        record = None
    else:
        selection, record = existing
    if operation == DELETE:
        return _delete(authorities, selection, record, confirmation, deadline)

    image = inspect_image_identity(IMAGE_TAG, timeout=deadline.remaining())
    with pull_verified_chart(
        CHART_ARCHIVE, CHART_SHA256, timeout=deadline.remaining()
    ) as chart:
        if record is not None:
            digest, connected = observe_repeat_connect(
                authorities,
                selection,
                record,
                chart,
                image,
                deadline=deadline,
                confirmation=confirmation,
            )
            if verify and producers is not None:
                proof = _verify(
                    authorities,
                    selection,
                    connected,
                    producers,
                    json_string(intent["job_url"]),
                    deadline,
                )
                return AgentOutcome(VERIFIED, selection, None, record, connected, proof)
            return AgentOutcome(
                PLANNED if confirmation is None else NO_OP,
                selection,
                digest,
                record,
                connected,
            )
        if confirmation is not None:
            installed = confirm_and_connect_fresh(
                authorities,
                selection,
                chart,
                image,
                confirmation=confirmation,
                deadline=deadline,
            )
            return AgentOutcome(APPLIED, selection, confirmation, None, installed)
        fresh = prepare_fresh_connect(
            authorities, selection, chart, image, deadline=deadline
        )
        return AgentOutcome(PLANNED, selection, fresh.digest, None, fresh)
