"""Select and approve only one OpenShift joiner's certificate requests.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import ipaddress
import time
from dataclasses import dataclass
from typing import Final, Optional

from lib.python.core.json_types import (
    JSONObject,
    json_object,
    json_objects,
    json_strings,
)
from lib.python.core.kubernetes.kubernetes import KubernetesError, KubernetesService
from lib.python.core.kubernetes.objects import KubernetesObject
from lib.python.core.runtime import error_class

CLIENT_SIGNER: Final = "kubernetes.io/kube-apiserver-client-kubelet"
SERVING_SIGNER: Final = "kubernetes.io/kubelet-serving"
JOIN_SIGNERS: Final = (CLIENT_SIGNER, SERVING_SIGNER)
NODE_COMMON_NAME_PREFIX: Final = "system:node:"
_DESCRIBE_FIELDS: Final = {
    "Common Name": "common_name",
    "Organization": "organizations",
    "DNS Names": "dns_names",
    "Email Addresses": "email_addresses",
    "URIs": "uris",
    "IP Addresses": "ip_addresses",
}


def canonical_ip(value: str) -> str:
    """Normalize one caller or provider IP for exact semantic comparison.

    :param value: Textual IPv4 or IPv6 address.
    :returns: Compressed canonical address.
    :raises ValueError: The value is not an IP address.
    """  # noqa: DOC502 - ipaddress raises the documented ValueError.
    return ipaddress.ip_address(value).compressed


def _before_deadline(deadline: Optional[float]) -> None:
    """Reject a new provider call after the approval-loop deadline.

    Each native call retains the Kubernetes service's command timeout; this
    guard prevents the next call from starting once the loop's total bound has
    expired.

    :param deadline: Optional monotonic approval-loop deadline.
    :raises KubernetesError: The deadline has already expired.
    """
    if deadline is not None and time.monotonic() >= deadline:
        raise KubernetesError("node_join_deadline_exceeded")


@dataclass(frozen=True)
class JoinSelector:
    """Bind CSR selection to one exact node identity and its addresses.

    .. attribute :: node_name
        :type: str

        Caller-selected Kubernetes Node name.

    .. attribute :: ip_addresses
        :type: tuple[str, ...]

        Caller-selected addresses used by native monitor and Node read-back.
    """

    node_name: str
    ip_addresses: tuple[str, ...]

    def __post_init__(self) -> None:
        """Reject an empty or malformed selector before provider access.

        :raises ValueError: Node identity or one address is invalid.
        """
        if (
            not self.node_name
            or self.node_name != self.node_name.strip()
            or any(character in self.node_name for character in ("\x00", "\n", "\r"))
        ):
            raise ValueError("node_join_name_invalid")
        if not self.ip_addresses:
            raise ValueError("node_join_ip_addresses_missing")
        canonical = tuple(canonical_ip(value) for value in self.ip_addresses)
        if len(set(canonical)) != len(canonical):
            raise ValueError("node_join_ip_addresses_duplicate")

    @property
    def common_name(self) -> str:
        """Return the native kubelet client identity for this node.

        :returns: Exact ``system:node:`` common name.
        """
        return NODE_COMMON_NAME_PREFIX + self.node_name

    @property
    def canonical_ips(self) -> frozenset[str]:
        """Return addresses normalized for provider read-back comparison.

        :returns: Unique canonical IPv4 and IPv6 addresses.
        """
        return frozenset(canonical_ip(value) for value in self.ip_addresses)

    def as_dict(self) -> JSONObject:
        """Render nonsecret selector evidence without provider-generated names.

        :returns: Exact caller identity, addresses and fixed signer sequence.
        """
        return {
            "node_name": self.node_name,
            "ip_addresses": list(self.ip_addresses),
            "signers": list(JOIN_SIGNERS),
        }


@dataclass(frozen=True)
class CSRSubject:
    """Decoded PKCS#10 identity rendered by the native OpenShift client.

    .. attribute :: common_name
        :type: str

        Native request common name used by the selector.

    .. attribute :: organizations
        :type: tuple[str, ...]

        Subject organizations retained as evidence.

    .. attribute :: dns_names
        :type: tuple[str, ...]

        DNS alternative names retained as evidence.

    .. attribute :: email_addresses
        :type: tuple[str, ...]

        Email alternative names retained as evidence.

    .. attribute :: uris
        :type: tuple[str, ...]

        URI alternative names retained as evidence.

    .. attribute :: ip_addresses
        :type: tuple[str, ...]

        Canonical IP alternative names retained as evidence.
    """

    common_name: str
    organizations: tuple[str, ...]
    dns_names: tuple[str, ...]
    email_addresses: tuple[str, ...]
    uris: tuple[str, ...]
    ip_addresses: tuple[str, ...]

    def as_dict(self) -> JSONObject:
        """Render bounded identity fields used by the selector.

        :returns: Subject and SAN values without the encoded request bytes.
        """
        return {
            "common_name": self.common_name,
            "organizations": list(self.organizations),
            "dns_names": list(self.dns_names),
            "ip_addresses": list(self.ip_addresses),
        }


def decode_described_subject(output: str) -> CSRSubject:
    """Decode fixed identity labels from ``oc describe csr`` output.

    The native client performs the X.509 decoding. This parser reads only the
    stable fields printed by Kubernetes instead of implementing ASN.1 locally.

    :param output: Native describe output for one exact CSR.
    :returns: Decoded subject and alternative names.
    :raises KubernetesError: Required identity fields are absent or malformed.
    """
    values: dict[str, list[str]] = {field: [] for field in _DESCRIBE_FIELDS.values()}
    active = ""
    active_indent = -1
    for raw_line in output.splitlines():
        line = raw_line.expandtabs(8)
        stripped = line.strip()
        if not stripped:
            active = ""
            continue
        indent = len(line) - len(line.lstrip())
        matched = False
        for label, field in _DESCRIBE_FIELDS.items():
            prefix = label + ":"
            if stripped.startswith(prefix):
                initial = stripped[len(prefix) :].strip()
                if initial and initial != "<none>":
                    values[field].append(initial)
                active = field
                active_indent = indent
                matched = True
                break
        if matched:
            continue
        if active and indent > active_indent and stripped != "<none>":
            values[active].append(stripped)
        else:
            active = ""
    common_names = values["common_name"]
    if len(common_names) != 1:
        raise KubernetesError("node_join_csr_subject_invalid")
    try:
        canonical_ips = tuple(canonical_ip(value) for value in values["ip_addresses"])
    except ValueError as exc:
        raise KubernetesError("node_join_csr_subject_invalid") from exc
    return CSRSubject(
        common_name=common_names[0],
        organizations=tuple(values["organizations"]),
        dns_names=tuple(values["dns_names"]),
        email_addresses=tuple(values["email_addresses"]),
        uris=tuple(values["uris"]),
        ip_addresses=canonical_ips,
    )


@dataclass(frozen=True)
class CSRObservation:
    """Retain one exact provider CSR and its decoded node identity.

    .. attribute :: name
        :type: str

        Provider-generated request name.

    .. attribute :: uid
        :type: str

        Immutable request identity used for approval read-back.

    .. attribute :: signer
        :type: str

        Native Kubernetes signer name.

    .. attribute :: usages
        :type: frozenset[str]

        Requested certificate usages retained as evidence.

    .. attribute :: subject
        :type: CSRSubject

        Native decoded request identity.

    .. attribute :: approved
        :type: bool

        Whether the request has an Approved condition.

    .. attribute :: denied
        :type: bool

        Whether the request has a Denied condition.

    .. attribute :: issued
        :type: bool

        Whether the API returned certificate bytes.

    .. attribute :: created_at
        :type: str

        Native creation timestamp used for deterministic selection.
    """

    name: str
    uid: str
    signer: str
    usages: frozenset[str]
    subject: CSRSubject
    approved: bool
    denied: bool
    issued: bool
    created_at: str

    @property
    def pending(self) -> bool:
        """Report whether this request can still be approved.

        :returns: True only before approval, denial and certificate issuance.
        """
        return not self.approved and not self.denied and not self.issued

    def as_dict(self) -> JSONObject:
        """Render safe request identity and lifecycle evidence.

        :returns: CSR metadata without request or certificate bytes.
        """
        return {
            "name": self.name,
            "uid": self.uid,
            "signer": self.signer,
            "usages": sorted(self.usages),
            "approved": self.approved,
            "denied": self.denied,
            "issued": self.issued,
            "subject": self.subject.as_dict(),
        }


def _true_conditions(document: JSONObject) -> frozenset[str]:
    """Read condition types whose native status is exactly true.

    :param document: Native CSR JSON document.
    :returns: Condition type names with status ``True``.
    :raises KubernetesError: Condition fields are malformed.
    """
    try:
        status = KubernetesObject(document).object("status")
        result = {
            condition.required_text("type")
            for condition in status.objects("conditions")
            if condition.required_text("status") == "True"
        }
    except TypeError as exc:
        raise KubernetesError("node_join_csr_conditions_invalid") from exc
    return frozenset(result)


def _describe_subject(
    service: KubernetesService,
    name: str,
    *,
    deadline: Optional[float] = None,
) -> CSRSubject:
    """Ask the installed OpenShift client to decode one exact CSR.

    :param service: Credential-bound native Kubernetes service.
    :param name: Exact CSR resource name.
    :param deadline: Optional monotonic approval-loop deadline.
    :returns: Decoded request identity.
    :raises KubernetesError: Native describe fails or its identity is malformed.
    """
    _before_deadline(deadline)
    result = service.command("describe", "csr", name, openshift=True)
    if result.returncode:
        raise KubernetesError(error_class(result))
    return decode_described_subject(result.stdout)


def observe_csr(
    service: KubernetesService,
    document: JSONObject,
    *,
    deadline: Optional[float] = None,
) -> CSRObservation:
    """Decode one native CSR while excluding secret certificate material.

    :param service: Credential-bound service used for native X.509 decoding.
    :param document: Exact CSR JSON returned by the API.
    :param deadline: Optional monotonic approval-loop deadline.
    :returns: Typed safe observation.
    :raises KubernetesError: Required metadata, spec or status is malformed.
    """
    try:
        resource = KubernetesObject(document)
        metadata = resource.metadata
        spec = resource.object("spec")
        status = resource.object("status")
        name = resource.name
        uid = metadata.required_text("uid")
        signer = spec.required_text("signerName")
        usages = frozenset(json_strings(spec.document.get("usages", [])))
        certificate = status.text("certificate")
        created_at = metadata.text("creationTimestamp") or ""
        conditions = _true_conditions(document)
    except (TypeError, ValueError) as exc:
        raise KubernetesError("node_join_csr_invalid") from exc
    return CSRObservation(
        name=name,
        uid=uid,
        signer=signer,
        usages=usages,
        subject=_describe_subject(service, name, deadline=deadline),
        approved="Approved" in conditions,
        denied="Denied" in conditions,
        issued=bool(certificate),
        created_at=created_at,
    )


def _matches(observation: CSRObservation, selector: JoinSelector) -> bool:
    """Match the approved hostname and two-signer selector.

    :param observation: Decoded request.
    :param selector: Caller-selected node identity.
    :returns: Whether the request has the exact node common name and allowed signer.
    """
    return (
        observation.signer in JOIN_SIGNERS
        and observation.subject.common_name == selector.common_name
    )


@dataclass(frozen=True)
class CSRInventory:
    """Bind one API snapshot to the exact join selector.

    .. attribute :: selector
        :type: JoinSelector

        Caller-selected node identity.

    .. attribute :: requests
        :type: tuple[CSRObservation, ...]

        Candidate requests decoded from this snapshot.

    .. attribute :: unrelated_signer_count
        :type: int

        Pending requests outside the two fixed signer families.
    """

    selector: JoinSelector
    requests: tuple[CSRObservation, ...]
    unrelated_signer_count: int

    def selected(self, signer: str) -> Optional[CSRObservation]:
        """Select the one current request for a signer without guessing.

        Pending requests take precedence over older issued evidence. Multiple
        pending requests for the same exact selector are ambiguous and block.

        :param signer: One of the fixed client or serving signers.
        :returns: Current exact request, or ``None`` before it appears.
        :raises ValueError: Signer is unsupported.
        :raises KubernetesError: Pending selection is ambiguous.
        """
        if signer not in JOIN_SIGNERS:
            raise ValueError("node_join_signer_invalid")
        matches = [
            request
            for request in self.requests
            if request.signer == signer and _matches(request, self.selector)
        ]
        pending = [request for request in matches if request.pending]
        if len(pending) > 1:
            raise KubernetesError("node_join_csr_ambiguous")
        if pending:
            return pending[0]
        if not matches:
            return None
        return max(matches, key=lambda item: (item.created_at, item.name))

    def as_dict(self) -> JSONObject:
        """Render bounded selector and matching request evidence.

        :returns: Selected request states and unrelated signer count.
        """
        selected = []
        for signer in JOIN_SIGNERS:
            request = self.selected(signer)
            if request is not None:
                selected.append(request.as_dict())
        return {
            "selector": self.selector.as_dict(),
            "requests": selected,
            "unrelated_signer_count": self.unrelated_signer_count,
        }


def read_csr_inventory(
    service: KubernetesService,
    selector: JoinSelector,
    *,
    deadline: float,
    known_names: frozenset[str] = frozenset(),
) -> CSRInventory:
    """Read and natively decode CSR candidates within the join deadline.

    Only the two supported signer families are decoded. Other pending signers
    are counted for evidence and never become approval candidates.

    :param service: Credential-bound native service.
    :param selector: Exact node identity selected by the caller.
    :param deadline: Monotonic deadline shared by the approval loop.
    :param known_names: Exact requests retained after prior selection or approval.
    :returns: One immutable provider snapshot.
    :raises KubernetesError: The list is malformed or the deadline expires.
    """
    _before_deadline(deadline)
    value = service.json("get", "certificatesigningrequests", "-o", "json")
    try:
        document = json_object(value)
        items = json_objects(document.get("items", []))
    except TypeError as exc:
        raise KubernetesError("node_join_csr_list_invalid") from exc
    candidates: list[JSONObject] = []
    unrelated = 0
    for item in items:
        try:
            resource = KubernetesObject(item)
            spec = resource.object("spec")
            signer = spec.required_text("signerName")
            conditions = _true_conditions(item)
            issued = bool(resource.object("status").text("certificate"))
            name = resource.name
            username = spec.text("username") or ""
        except TypeError as exc:
            raise KubernetesError("node_join_csr_invalid") from exc
        if signer in JOIN_SIGNERS:
            pending = not issued and not ({"Approved", "Denied"} & conditions)
            if (
                pending
                or name in known_names
                or (signer == SERVING_SIGNER and username == selector.common_name)
            ):
                candidates.append(item)
        elif not issued and not ({"Approved", "Denied"} & conditions):
            unrelated += 1
    observations = []
    for item in candidates:
        _before_deadline(deadline)
        observations.append(observe_csr(service, item, deadline=deadline))
    return CSRInventory(selector, tuple(observations), unrelated)


def approve_csr(
    service: KubernetesService,
    selector: JoinSelector,
    request: CSRObservation,
    *,
    deadline: Optional[float] = None,
) -> CSRObservation:
    """Approve one exact still-current request and verify native read-back.

    :param service: Credential-bound native service.
    :param selector: Exact node identity allowed by the confirmed plan.
    :param request: Previously observed pending request.
    :param deadline: Optional monotonic approval-loop deadline.
    :returns: Same-UID request with an Approved condition.
    :raises KubernetesError: Identity drift, denial, mutation failure or read-back mismatch.
    """
    _before_deadline(deadline)
    observed = service.read("certificatesigningrequest", request.name, "")
    if observed is None:
        raise KubernetesError("node_join_csr_disappeared")
    current = observe_csr(service, observed, deadline=deadline)
    if current.uid != request.uid or not _matches(current, selector):
        raise KubernetesError("node_join_csr_identity_changed")
    if current.denied:
        raise KubernetesError("node_join_csr_denied")
    if current.approved or current.issued:
        return current
    _before_deadline(deadline)
    result = service.command(
        "adm", "certificate", "approve", current.name, openshift=True
    )
    if result.returncode:
        raise KubernetesError(error_class(result))
    _before_deadline(deadline)
    readback = service.read("certificatesigningrequest", current.name, "")
    if readback is None:
        raise KubernetesError("node_join_csr_readback_missing")
    approved = observe_csr(service, readback, deadline=deadline)
    if (
        approved.uid != current.uid
        or not _matches(approved, selector)
        or not approved.approved
        or approved.denied
    ):
        raise KubernetesError("node_join_csr_approval_readback_mismatch")
    return approved


__all__ = [
    "CLIENT_SIGNER",
    "SERVING_SIGNER",
    "CSRInventory",
    "CSRObservation",
    "CSRSubject",
    "JoinSelector",
    "approve_csr",
    "canonical_ip",
    "decode_described_subject",
    "observe_csr",
    "read_csr_inventory",
]
