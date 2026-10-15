"""Strict provider and marker evidence readers for the Podman probe.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Final, Optional

from lib.python.core.json_types import JSONObject, JSONValue
from lib.python.core.kubernetes.kubernetes import KubernetesError
from lib.python.sys.status import BLOCKED, PASS

PROBE_CONTAINER: Final = "podman-probe"
MARKER_PREFIX: Final = "CI_SKILLS_PROBE"
REQUIRED_MARKERS: Final = (
    "kernel",
    "podman_version",
    "storage",
    "backing_filesystem",
    "inner_runtime",
    "bind_readback",
    "expected_exit",
    "remaining_containers",
)


@dataclass(frozen=True)
class PodTermination:
    """Authoritative terminated state from the Job-owned probe Pod.

    .. attribute :: pod_name
        :type: str

        Exact Pod name.

    .. attribute :: pod_uid
        :type: str

        Exact Pod UID.

    .. attribute :: exit_code
        :type: int

        Probe container termination exit code.

    .. attribute :: image_id
        :type: str

        Runtime image identity reported by Kubernetes.
    """

    pod_name: str
    pod_uid: str
    exit_code: int
    image_id: str


def pod_termination(document: JSONValue, job_uid: str) -> Optional[PodTermination]:
    """Extract termination only from the sole Pod owned by the exact Job UID.

    :param document: PodList selected by this Job's label.
    :param job_uid: UID independently read from the applied Job.
    :returns: Termination evidence, or ``None`` while the Pod is still running.
    :raises KubernetesError: If the response, ownership, or container shape is invalid.
    """
    items = document.get("items") if isinstance(document, Mapping) else None
    if not isinstance(items, list):
        raise KubernetesError("probe_pod_list_invalid")
    if not items:
        return None
    if len(items) != 1 or not isinstance(items[0], Mapping):
        raise KubernetesError("probe_pod_ambiguous")
    pod = items[0]
    metadata, status = pod.get("metadata"), pod.get("status")
    if not isinstance(metadata, Mapping) or not isinstance(status, Mapping):
        raise KubernetesError("probe_pod_invalid")
    owners = metadata.get("ownerReferences", [])
    if not isinstance(owners, list) or not any(
        isinstance(owner, Mapping)
        and owner.get("kind") == "Job"
        and owner.get("uid") == job_uid
        and owner.get("controller") is True
        for owner in owners
    ):
        raise KubernetesError("probe_pod_owner_mismatch")
    statuses = status.get("containerStatuses", [])
    if not isinstance(statuses, list) or any(
        not isinstance(item, Mapping) for item in statuses
    ):
        raise KubernetesError("probe_container_status_invalid")
    matching = [item for item in statuses if item.get("name") == PROBE_CONTAINER]
    if not matching:
        return None
    if len(matching) != 1:
        raise KubernetesError("probe_container_status_ambiguous")
    state = matching[0].get("state")
    if not isinstance(state, Mapping):
        raise KubernetesError("probe_container_state_invalid")
    terminated = state.get("terminated")
    if not isinstance(terminated, Mapping):
        return None
    name, uid, code, image_id = (
        metadata.get("name"),
        metadata.get("uid"),
        terminated.get("exitCode"),
        matching[0].get("imageID"),
    )
    if (
        not isinstance(name, str)
        or not name
        or not isinstance(uid, str)
        or not uid
        or not isinstance(code, int)
        or isinstance(code, bool)
        or not isinstance(image_id, str)
        or not image_id
    ):
        raise KubernetesError("probe_pod_termination_invalid")
    return PodTermination(name, uid, code, image_id)


def scc_admission_reason(document: JSONValue, job_uid: str) -> Optional[str]:
    """Classify an OpenShift SCC admission rejection for the exact Job.

    :param document: EventList from the generated namespace.
    :param job_uid: Exact Job UID used to exclude unrelated events.
    :returns: Stable ``scc_required`` classification, or ``None`` when unobserved.
    :raises KubernetesError: If the EventList shape is invalid.
    """
    items = document.get("items") if isinstance(document, Mapping) else None
    if not isinstance(items, list):
        raise KubernetesError("probe_event_list_invalid")
    for event in items:
        if not isinstance(event, Mapping):
            raise KubernetesError("probe_event_invalid")
        involved = event.get("involvedObject")
        reason, message = event.get("reason"), event.get("message")
        if (
            isinstance(involved, Mapping)
            and involved.get("uid") == job_uid
            and reason == "FailedCreate"
            and isinstance(message, str)
            and (
                "security context constraint" in message.casefold()
                or "securitycontextconstraints" in message.casefold()
            )
        ):
            return "scc_required"
    return None


@dataclass(frozen=True)
class MarkerEvidence:
    """Parsed marker values and explicit rows, including missing evidence.

    .. attribute :: values
        :type: Mapping[str, str]

        Unique observed marker values.

    .. attribute :: rows
        :type: Sequence[JSONObject]

        Ordered pass/blocked row for every required marker.

    .. attribute :: verified
        :type: bool

        Whether every marker proves its expected value.
    """

    values: Mapping[str, str]
    rows: Sequence[JSONObject]
    verified: bool


def parse_probe_markers(log: str) -> MarkerEvidence:
    """Parse unique marker lines and expose every missing marker as a row.

    :param log: Bounded log from the exact Job-owned Pod.
    :returns: Required rows and the overall marker verdict.
    :raises ValueError: If a marker is malformed or repeated.
    """
    values: MutableMapping[str, str] = {}
    for line in log.splitlines():
        if not line.startswith(MARKER_PREFIX + "|"):
            continue
        fields = line.split("|", 2)
        if len(fields) != 3 or not fields[1] or not fields[2]:
            raise ValueError("probe_marker_invalid")
        key, value = fields[1], fields[2]
        if key in values:
            raise ValueError("probe_marker_duplicate")
        values[key] = value
    rows = []
    for key in REQUIRED_MARKERS:
        value = values.get(key)
        passed = value is not None and _marker_passed(key, value)
        rows.append(
            {
                "check": key,
                "status": PASS if passed else BLOCKED,
                "observed": value if value is not None else "missing",
            }
        )
    return MarkerEvidence(
        dict(values), rows, all(row["status"] == PASS for row in rows)
    )


def _marker_passed(key: str, value: str) -> bool:
    """Evaluate only markers whose value carries a pass/fail invariant.

    :param key: Required marker key.
    :param value: Nonempty observed marker value.
    :returns: Whether the marker satisfies its declared evidence invariant.
    """
    expected = {
        "bind_readback": "ready",
        "expected_exit": "7",
        "remaining_containers": "none",
    }
    if key in expected:
        return value == expected[key]
    if key == "storage":
        fields = _fields(value)
        return (
            fields.get("driver") == "overlay"
            and fields.get("mount_program") == "none"
            and fields.get("cgroup_manager") == "cgroupfs"
            and bool(fields.get("graph_root"))
            and bool(fields.get("cgroup_version"))
        )
    if key == "inner_runtime":
        fields = _fields(value)
        identifier = fields.get("id", "")
        return (
            len(identifier) == 64
            and all(character in "0123456789abcdef" for character in identifier)
            and fields.get("marker") == "inner-ok"
            and bool(fields.get("cgroup"))
            and bool(fields.get("root"))
        )
    return True


def _fields(value: str) -> Mapping[str, str]:
    """Split semicolon-delimited probe fields without accepting duplicates.

    :param value: Marker value containing ``name=value`` components.
    :returns: Unique component mapping, or an empty mapping for malformed input.
    """
    fields: MutableMapping[str, str] = {}
    for component in value.split(";"):
        name, separator, observed = component.partition("=")
        if not separator or not name or not observed or name in fields:
            return {}
        fields[name] = observed
    return fields
