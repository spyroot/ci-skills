"""Monitor one booted OpenShift node through exact CSR and Ready read-back.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Optional

from lib.python.core.abstract.callbacks import AbstractCallbacks
from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.delivery_state import DeliveryState
from lib.python.core.json_types import JSONObject
from lib.python.core.kubernetes.kubernetes import (
    DEFAULT_COMMAND_TIMEOUT,
    K8SAuth,
    K8SCallback,
    KubernetesError,
    KubernetesService,
)
from lib.python.core.kubernetes.objects import KubernetesObject
from lib.python.core.kubernetes.ocp_artifact_run import (
    OCPArtifactState,
    ResolveNodeArtifactRecord,
)
from lib.python.core.kubernetes.ocp_build_workflow import execute_callbacks
from lib.python.core.kubernetes.ocp_csr import (
    CLIENT_SIGNER,
    SERVING_SIGNER,
    CSRInventory,
    CSRObservation,
    JoinSelector,
    approve_csr,
    canonical_ip,
    read_csr_inventory,
)
from lib.python.core.kubernetes.ocp_invocation import NodeOperation
from lib.python.core.kubernetes.ocp_lifecycle_workflow import artifact_callbacks
from lib.python.core.kubernetes.ocp_recovery import ResolveNodeRecoveryRecord
from lib.python.core.kubernetes.run_scope import ResolveRunScopeAction, ResolveRunScopes
from lib.python.core.runtime import error_class
from lib.python.core.target import Target
from lib.python.sys.status import APPLIED, NO_OP, VERIFIED

JOIN_POLL_SECONDS: Final = 5
NODE_ROLE_PREFIX: Final = "node-role.kubernetes.io/"


@dataclass(frozen=True)
class NodeJoinAction(SkillAction):
    """Bind one confirmed run to the caller-selected joining node.

    .. attribute :: run_id
        :type: str

        Existing ISO build correlation identity.

    .. attribute :: selector
        :type: JoinSelector

        Exact node hostname, addresses and fixed signer sequence.

    .. attribute :: timeout
        :type: int

        Positive total approval-loop bound in seconds.
    """

    run_id: str
    selector: JoinSelector
    timeout: int

    def __post_init__(self) -> None:
        """Validate typed action inputs before callback construction.

        :raises TypeError: Selector has another type.
        :raises ValueError: Run identity or timeout is empty or invalid.
        """
        if not self.run_id:
            raise ValueError("node_join_run_id_missing")
        if not isinstance(self.selector, JoinSelector):
            raise TypeError("node_join_selector_invalid")
        if self.timeout <= 0:
            raise ValueError("node_join_timeout_invalid")


@dataclass(frozen=True)
class NodeObservation:
    """Retain one safe read-back of the selected native Node.

    .. attribute :: name
        :type: str

        Exact caller-selected Node name.

    .. attribute :: uid
        :type: str

        Immutable native Node identity.

    .. attribute :: ready
        :type: bool

        Whether the Node has a true Ready condition.

    .. attribute :: roles
        :type: tuple[str, ...]

        Roles decoded from native Node labels.

    .. attribute :: ip_addresses
        :type: tuple[str, ...]

        Canonical native InternalIP and ExternalIP values.

    .. attribute :: selected_addresses_present
        :type: bool

        Whether all caller-selected monitor addresses were read back.
    """

    name: str
    uid: str
    ready: bool
    roles: tuple[str, ...]
    ip_addresses: tuple[str, ...]
    selected_addresses_present: bool

    @property
    def complete(self) -> bool:
        """Report the provider's terminal Ready condition.

        Monitor addresses remain evidence because a reachable address can differ
        from the Node addresses reported across NAT or multiple networks.

        :returns: True when the exact caller-selected Node is Ready.
        """
        return self.ready

    def as_dict(self) -> JSONObject:
        """Render safe node identity, role, address and readiness evidence.

        :returns: Selected native Node fields.
        """
        return {
            "name": self.name,
            "uid": self.uid,
            "ready": self.ready,
            "roles": list(self.roles),
            "ip_addresses": list(self.ip_addresses),
            "selected_addresses_present": self.selected_addresses_present,
        }


def read_node(
    service: KubernetesService,
    selector: JoinSelector,
    *,
    deadline: Optional[float] = None,
) -> Optional[NodeObservation]:
    """Read and validate one exact native Node without listing other nodes.

    :param service: Credential-bound native Kubernetes service.
    :param selector: Caller-selected node identity and addresses.
    :param deadline: Optional monotonic approval-loop deadline.
    :returns: Safe node observation, or ``None`` before the Node exists.
    :raises KubernetesError: Native node fields are malformed or identity drifts.
    """
    if deadline is not None and time.monotonic() >= deadline:
        raise KubernetesError("node_join_deadline_exceeded")
    document = service.read("node", selector.node_name, "")
    if document is None:
        return None
    try:
        node = KubernetesObject(document)
        metadata = node.metadata
        if node.name != selector.node_name:
            raise KubernetesError("node_join_name_readback_mismatch")
        uid = metadata.required_text("uid")
        labels_value = metadata.document.get("labels", {})
        if not isinstance(labels_value, Mapping) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in labels_value.items()
        ):
            raise KubernetesError("node_join_node_invalid")
        roles = tuple(
            sorted(
                key[len(NODE_ROLE_PREFIX) :]
                for key in labels_value
                if key.startswith(NODE_ROLE_PREFIX) and key[len(NODE_ROLE_PREFIX) :]
            )
        )
        status = node.object("status")
        ready = any(
            condition.text("type") == "Ready" and condition.text("status") == "True"
            for condition in status.objects("conditions")
        )
        try:
            addresses = tuple(
                canonical_ip(address.required_text("address"))
                for address in status.objects("addresses")
                if address.required_text("type") in ("InternalIP", "ExternalIP")
            )
        except ValueError as exc:
            raise KubernetesError("node_join_address_invalid") from exc
    except TypeError as exc:
        raise KubernetesError("node_join_node_invalid") from exc
    selected_present = selector.canonical_ips.issubset(frozenset(addresses))
    return NodeObservation(
        name=selector.node_name,
        uid=uid,
        ready=ready,
        roles=roles,
        ip_addresses=tuple(sorted(set(addresses))),
        selected_addresses_present=selected_present,
    )


@dataclass
class OCPJoinState(OCPArtifactState):
    """Extend artifact lifecycle state with one bounded join operation.

    Fields that require live observations are assigned only after the matching
    provider read; presence booleans avoid nullable domain objects.

    .. attribute :: join_node
        :type: NodeObservation

        Most recent exact Node read-back.

    .. attribute :: join_node_present
        :type: bool

        Whether ``join_node`` has been assigned.

    .. attribute :: join_existing_ready
        :type: bool

        Whether the initial read found a complete Ready Node.

    .. attribute :: join_inventory
        :type: CSRInventory

        Most recent bounded CSR snapshot.

    .. attribute :: join_inventory_present
        :type: bool

        Whether ``join_inventory`` has been assigned.

    .. attribute :: join_client_csr
        :type: CSRObservation

        Selected client request when observed.

    .. attribute :: join_client_present
        :type: bool

        Whether ``join_client_csr`` has been assigned.

    .. attribute :: join_client_issued
        :type: bool

        Whether client certificate issuance was read back directly.

    .. attribute :: join_client_completion_source
        :type: str

        Direct or inferred bootstrap-completion evidence source.

    .. attribute :: join_serving_csr
        :type: CSRObservation

        Selected serving request when observed.

    .. attribute :: join_serving_present
        :type: bool

        Whether ``join_serving_csr`` has been assigned.

    .. attribute :: join_serving_issued
        :type: bool

        Whether serving certificate issuance was read back directly.

    .. attribute :: join_poll_count
        :type: int

        Number of bounded approval snapshots collected.

    .. attribute :: join_elapsed_ms
        :type: int

        Approval-loop elapsed time in milliseconds.

    .. attribute :: join_monitor_exit_code
        :type: int

        Native monitor exit code after explicit approval completes.
    """

    join_node: NodeObservation = field(init=False)
    join_node_present: bool = False
    join_existing_ready: bool = False
    join_inventory: CSRInventory = field(init=False)
    join_inventory_present: bool = False
    join_client_csr: CSRObservation = field(init=False)
    join_client_present: bool = False
    join_client_issued: bool = False
    join_client_completion_source: str = ""
    join_serving_csr: CSRObservation = field(init=False)
    join_serving_present: bool = False
    join_serving_issued: bool = False
    join_poll_count: int = 0
    join_elapsed_ms: int = 0
    join_monitor_exit_code: int = -1

    def join_result(self) -> JSONObject:
        """Render only captured node and CSR lifecycle evidence.

        :returns: Safe join selector, timing, read-back and request identities.
        """
        result: dict[str, object] = {
            "already_ready": self.join_existing_ready,
            "client_issued": self.join_client_issued,
            "client_completion_source": self.join_client_completion_source,
            "serving_issued": self.join_serving_issued,
            "poll_count": self.join_poll_count,
            "elapsed_time_ms": self.join_elapsed_ms,
        }
        if self.join_node_present:
            result["node"] = self.join_node.as_dict()
        if self.join_inventory_present:
            result["csr_inventory"] = self.join_inventory.as_dict()
        if self.join_client_present:
            result["client_csr"] = self.join_client_csr.as_dict()
        if self.join_serving_present:
            result["serving_csr"] = self.join_serving_csr.as_dict()
        if self.join_monitor_exit_code >= 0:
            result["monitor_exit_code"] = self.join_monitor_exit_code
        return result


def _state(callback: K8SCallback[NodeJoinAction]) -> OCPJoinState:
    """Narrow one callback's shared state to CI12.2 join state.

    :param callback: Bound Kubernetes join callback.
    :returns: Exact shared join state.
    :raises TypeError: Another state class was supplied.
    """
    state = callback.state
    if not isinstance(state, OCPJoinState):
        raise TypeError("ocp_join_state_required")
    return state


def _retain_node(state: OCPJoinState, node: Optional[NodeObservation]) -> None:
    """Assign a node observation without nullable state fields.

    :param state: Shared join state.
    :param node: Current provider observation, absent before registration.
    """
    state.join_node_present = node is not None
    if node is not None:
        state.join_node = node


class ReadNodeState(K8SCallback[NodeJoinAction]):
    """Read the selected Node before planning or after the approval loop."""

    def __init__(self, action: NodeJoinAction, *, require_ready: bool = False):
        """Select whether this read is observational or terminal verification.

        :param action: Exact confirmed join inputs.
        :param require_ready: Require the exact selected Node to be Ready.
        """
        super().__init__(action)
        self.require_ready = require_ready

    def run(self) -> JSONObject:
        """Read one exact Node and optionally enforce the terminal condition.

        :returns: Presence plus safe node read-back evidence.
        :raises TypeError: Shared state is not join-aware.
        :raises KubernetesError: Terminal read is absent or incomplete.
        """  # noqa: DOC503 - state narrowing raises the documented TypeError.
        state = _state(self)
        node = read_node(self.kubernetes, self.action.selector)
        _retain_node(state, node)
        if not self.require_ready:
            state.join_existing_ready = node is not None and node.complete
        if self.require_ready and (node is None or not node.complete):
            raise KubernetesError("node_join_ready_readback_incomplete")
        return {
            "action": VERIFIED if node is not None else NO_OP,
            "verified": node is not None and (node.complete or not self.require_ready),
            "present": node is not None,
            **({"node": node.as_dict()} if node is not None else {}),
        }

    def cleanup(self) -> JSONObject:
        """Leave provider Node state unchanged.

        :returns: Empty cleanup evidence.
        """
        return {}


class ObserveJoinRequests(K8SCallback[NodeJoinAction]):
    """Read exact CSR candidates for live planning without approving them."""

    def run(self) -> JSONObject:
        """Collect one bounded request snapshot for the confirmed selector.

        :returns: Matching request states and unrelated signer count.
        :raises TypeError: Shared state is not join-aware.
        :raises KubernetesError: Native request reads are malformed or exceed the bound.
        """  # noqa: DOC502,DOC503 - delegated reads preserve provider failures.
        state = _state(self)
        if state.join_existing_ready:
            return {"action": NO_OP, "verified": True, "already_ready": True}
        inventory = read_csr_inventory(
            self.kubernetes,
            self.action.selector,
            deadline=time.monotonic() + self.action.timeout,
        )
        state.join_inventory = inventory
        state.join_inventory_present = True
        return {"action": VERIFIED, "verified": True, **inventory.as_dict()}

    def cleanup(self) -> JSONObject:
        """Leave provider requests unchanged.

        :returns: Empty cleanup evidence.
        """
        return {}


def _remember_request(
    state: OCPJoinState, signer: str, request: CSRObservation
) -> None:
    """Retain the exact request identity for later report and progress checks.

    :param state: Shared join state.
    :param signer: Fixed client or serving signer.
    :param request: Current same-selector request observation.
    :raises ValueError: Signer is outside the fixed sequence.
    """
    if signer == CLIENT_SIGNER:
        state.join_client_csr = request
        state.join_client_present = True
        state.join_client_issued = request.issued
        if request.issued:
            state.join_client_completion_source = "csr_readback"
        return
    if signer == SERVING_SIGNER:
        state.join_serving_csr = request
        state.join_serving_present = True
        state.join_serving_issued = request.issued
        return
    raise ValueError("node_join_signer_invalid")


def _retained_request_names(state: OCPJoinState) -> frozenset[str]:
    """Return exact request names selected by earlier snapshots.

    :param state: Shared join state.
    :returns: Immutable request-name set for bounded recollection.
    """
    names = set()
    if state.join_client_present:
        names.add(state.join_client_csr.name)
    if state.join_serving_present:
        names.add(state.join_serving_csr.name)
    return frozenset(names)


def _process_requests(
    service: KubernetesService,
    selector: JoinSelector,
    state: OCPJoinState,
    inventory: CSRInventory,
    deadline: float,
) -> int:
    """Approve one snapshot in client-then-serving order.

    :param service: Credential-bound native service.
    :param selector: Exact node identity from the confirmed plan.
    :param state: Shared join state updated with read-back evidence.
    :param inventory: Current bounded provider snapshot.
    :param deadline: Monotonic approval-loop deadline.
    :returns: Number of approval mutations performed.
    :raises KubernetesError: A selected request is denied or approval fails.
    """
    approvals = 0
    client = inventory.selected(CLIENT_SIGNER)
    if client is not None:
        if client.denied:
            raise KubernetesError("node_join_client_csr_denied")
        if client.pending:
            client = approve_csr(service, selector, client, deadline=deadline)
            approvals += 1
        _remember_request(state, CLIENT_SIGNER, client)
    serving = inventory.selected(SERVING_SIGNER)
    if serving is None:
        return approvals
    if serving.denied:
        raise KubernetesError("node_join_serving_csr_denied")
    if not state.join_client_issued:
        state.join_client_completion_source = "serving_request"
    if serving.pending:
        serving = approve_csr(service, selector, serving, deadline=deadline)
        approvals += 1
    _remember_request(state, SERVING_SIGNER, serving)
    return approvals


def _join_complete(state: OCPJoinState) -> bool:
    """Check the approved terminal CSR and Node conditions.

    :param state: Shared join state.
    :returns: True after serving issuance and exact Ready Node read-back.
    """
    return (
        state.join_serving_issued
        and state.join_node_present
        and state.join_node.complete
    )


def _approval_result(
    state: OCPJoinState,
    inventory: CSRInventory,
    approvals: int,
) -> JSONObject:
    """Render final approval-loop evidence from concrete state.

    :param state: Completed shared join state.
    :param inventory: Final provider snapshot.
    :param approvals: Total approval mutations performed.
    :returns: Safe CSR, timing and Node evidence.
    """
    return {
        "action": APPLIED if approvals else NO_OP,
        "verified": True,
        "approvals": approvals,
        "poll_count": state.join_poll_count,
        "elapsed_time_ms": state.join_elapsed_ms,
        "client_completion_source": state.join_client_completion_source,
        "client_csr": (
            state.join_client_csr.as_dict() if state.join_client_present else {}
        ),
        "serving_csr": state.join_serving_csr.as_dict(),
        "node": state.join_node.as_dict(),
        "unrelated_signer_count": inventory.unrelated_signer_count,
    }


class ApproveJoinRequests(K8SCallback[NodeJoinAction]):
    """Approve the exact client-then-serving sequence inside one bounded run."""

    def run(self) -> JSONObject:
        """Recollect, approve and read back until serving is issued and Node Ready.

        :returns: Final CSR and Ready-node evidence with poll timing.
        :raises TypeError: Shared state is not join-aware.
        :raises KubernetesError: Requests drift, are denied or the deadline expires.
        """  # noqa: DOC503 - state narrowing raises the documented TypeError.
        state = _state(self)
        if state.join_existing_ready:
            state.join_client_completion_source = "already_ready"
            return {"action": NO_OP, "verified": True, "already_ready": True}
        started = time.monotonic()
        deadline = started + self.action.timeout
        approvals = 0
        while True:
            if time.monotonic() >= deadline:
                raise KubernetesError("node_join_deadline_exceeded")
            state.join_poll_count += 1
            inventory = read_csr_inventory(
                self.kubernetes,
                self.action.selector,
                deadline=deadline,
                known_names=_retained_request_names(state),
            )
            state.join_inventory = inventory
            state.join_inventory_present = True
            approvals += _process_requests(
                self.kubernetes,
                self.action.selector,
                state,
                inventory,
                deadline,
            )
            node = read_node(
                self.kubernetes,
                self.action.selector,
                deadline=deadline,
            )
            _retain_node(state, node)
            if _join_complete(state):
                state.join_elapsed_ms = round((time.monotonic() - started) * 1000)
                return _approval_result(state, inventory, approvals)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise KubernetesError("node_join_deadline_exceeded")
            time.sleep(min(JOIN_POLL_SECONDS, remaining))

    def cleanup(self) -> JSONObject:
        """Never revoke issued node certificates during workflow cleanup.

        :returns: Empty cleanup evidence.
        """
        return {}


class WaitForJoiner(K8SCallback[NodeJoinAction]):
    """Run the native OpenShift monitor after explicit CSR completion."""

    def run(self) -> JSONObject:
        """Verify native monitor success for the selected addresses.

        Explicit CSR handling runs first because native monitor can skip CSR
        checks when reverse DNS is unavailable. Waiting first would deadlock the
        callback sequence that must approve those requests. The monitor is one
        separately bounded native call using the service command timeout.

        :returns: Native monitor status without unbounded terminal output.
        :raises TypeError: Shared state is not join-aware.
        :raises KubernetesError: Explicit approval is incomplete or monitor fails.
        """  # noqa: DOC503 - state narrowing raises the documented TypeError.
        state = _state(self)
        if state.join_existing_ready:
            return {"action": NO_OP, "verified": True, "already_ready": True}
        if not state.join_serving_issued or not state.join_node_present:
            raise KubernetesError("node_join_approval_incomplete")
        result = self.kubernetes.command(
            "adm",
            "node-image",
            "monitor",
            "--ip-addresses",
            ",".join(self.action.selector.ip_addresses),
            openshift=True,
        )
        state.join_monitor_exit_code = result.returncode
        if result.returncode:
            raise KubernetesError(error_class(result))
        return {
            "action": VERIFIED,
            "verified": True,
            "exit_code": result.returncode,
            "ip_addresses": list(self.action.selector.ip_addresses),
        }

    def cleanup(self) -> JSONObject:
        """Leave the joined host and native Node untouched.

        :returns: Empty cleanup evidence.
        """
        return {}


def observe_join_callbacks(
    action: NodeJoinAction,
) -> AbstractCallbacks[DeliveryState, SkillAction]:
    """Compose mutation-free node and CSR reads for a live plan.

    :param action: Exact confirmed join inputs.
    :returns: Ordered node and request observations.
    """
    return [ReadNodeState(action), ObserveJoinRequests(action)]


def join_callbacks(
    action: NodeJoinAction,
) -> AbstractCallbacks[DeliveryState, SkillAction]:
    """Compose approval, native monitor and final independent node read-back.

    :param action: Exact confirmed join inputs.
    :returns: Ordered atomic join callbacks; artifact cleanup is appended by caller.
    """
    return [
        ApproveJoinRequests(action),
        WaitForJoiner(action),
        ReadNodeState(action, require_ready=True),
    ]


def resolve_join(
    state: OCPJoinState,
    target: Target,
    action: NodeJoinAction,
    *,
    observe_requests: bool,
) -> None:
    """Authenticate and resolve node, run records and optional CSR observations.

    The Node read precedes artifact enforcement so a successfully cleaned prior
    join remains an idempotent ``NO_OP`` even after its build records are gone.

    :param state: Shared join and artifact state.
    :param target: Credential-bound Kubernetes target.
    :param action: Exact confirmed join inputs.
    :param observe_requests: Include one mutation-free CSR snapshot for live plan.
    :raises BaseException: Authentication, provider reads or record decoding fails.
    """  # noqa: DOC502 - shared callbacks preserve provider failures.
    lookup = ResolveRunScopeAction(action.run_id)
    callbacks: AbstractCallbacks[DeliveryState, SkillAction] = [
        K8SAuth(target, min(action.timeout, DEFAULT_COMMAND_TIMEOUT)),
        ReadNodeState(action),
        ResolveRunScopes(lookup),
        ResolveNodeArtifactRecord(lookup),
        ResolveNodeRecoveryRecord(lookup),
    ]
    if observe_requests:
        callbacks.append(ObserveJoinRequests(action))
    execute_callbacks(state, callbacks)


def execute_join(state: OCPJoinState, action: NodeJoinAction) -> None:
    """Run one confirmed join and remove its owned build scope last.

    :param state: State populated by ``resolve_join``.
    :param action: Exact confirmed join inputs.
    :raises KubernetesError: A non-ready node lacks a complete artifact record,
        join read-back is incomplete, or owned cleanup fails.
    :raises BaseException: Native approval, monitor or cleanup operations fail.
    """  # noqa: DOC502,DOC503 - callbacks preserve provider and transport failures.
    if not state.join_existing_ready and not state.artifact_record_present:
        if state.recovery_record_present:
            raise KubernetesError("node_recovery_requires_verify_or_remove")
        if state.found_scopes:
            raise KubernetesError("node_artifact_records_missing")
        raise KubernetesError("node_artifact_record_not_found")
    callbacks = join_callbacks(action)
    callbacks.extend(
        artifact_callbacks(
            state,
            NodeOperation.REMOVE,
            action.run_id,
            action.timeout,
        )
    )
    execute_callbacks(state, callbacks)
    if not state.join_node_present or not state.join_node.complete:
        raise KubernetesError("node_join_ready_readback_incomplete")
    if not state.join_existing_ready and not state.join_serving_issued:
        raise KubernetesError("node_join_serving_readback_incomplete")


__all__ = [
    "ApproveJoinRequests",
    "NodeJoinAction",
    "NodeObservation",
    "OCPJoinState",
    "ObserveJoinRequests",
    "ReadNodeState",
    "WaitForJoiner",
    "execute_join",
    "join_callbacks",
    "observe_join_callbacks",
    "read_node",
    "resolve_join",
]
