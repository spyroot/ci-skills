"""Models and pure builders for the OLM operator command (CI16).

The module holds no cluster value: channel, catalog source, install mode and
object names are read from the cluster by the resolve callbacks, and the
builders here only turn resolved values into the three enable documents
(DEC-CI16-04, DEC-CI16-09). Every OLM object is addressed by its qualified
resource name (DEC-CI16-13).

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Optional

from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.delivery_state import DeliveryState
from lib.python.core.json_types import JSONObject
from lib.python.core.target import Target

PACKAGEMANIFEST_RESOURCE: str = "packagemanifests.packages.operators.coreos.com"
SUBSCRIPTION_RESOURCE: str = "subscriptions.operators.coreos.com"
OPERATORGROUP_RESOURCE: str = "operatorgroups.operators.coreos.com"
CLUSTERSERVICEVERSION_RESOURCE: str = "clusterserviceversions.operators.coreos.com"
INSTALLPLAN_RESOURCE: str = "installplans.operators.coreos.com"

NAMESPACE_API_VERSION: str = "v1"
OPERATORGROUP_API_VERSION: str = "operators.coreos.com/v1"
SUBSCRIPTION_API_VERSION: str = "operators.coreos.com/v1alpha1"

ALM_EXAMPLES_ANNOTATION: str = "alm-examples"
OLM_PACKAGE_LABEL: str = "operators.coreos.com/{package}.{namespace}"

APPROVAL_AUTOMATIC: str = "Automatic"
APPROVAL_MANUAL: str = "Manual"
INSTALL_MODE_OWN_NAMESPACE: str = "OwnNamespace"
INSTALL_MODE_ALL_NAMESPACES: str = "AllNamespaces"

MANAGED_BY_LABEL: str = "app.kubernetes.io/managed-by"
OPERATION_LABEL: str = "ci-skills.spyroot.dev/operation"
MANAGED_BY_VALUE: str = "ci-skills"


@dataclass(frozen=True, slots=True)
class CatalogSourceRef:
    """Identify one catalog source serving a package.

    :param name: CatalogSource name read from the PackageManifest status.
    :param namespace: Namespace that holds the CatalogSource.

    .. attribute :: name
        :type: str

        CatalogSource name read from the PackageManifest status.

    .. attribute :: namespace
        :type: str

        Namespace that holds the CatalogSource.
    """

    name: str
    namespace: str


@dataclass(frozen=True, slots=True)
class OperatorOverrides:
    """Carry the caller's six optional overrides, each checked against the cluster.

    :param channel: Subscription channel from ``--channel``.
    :param source: CatalogSource name from ``--source``.
    :param source_namespace: CatalogSource namespace from ``--source-namespace``.
    :param install_mode: OperatorGroup install mode from ``--install-mode``.
    :param approval: InstallPlan approval from ``--approval``.
    :param starting_csv: Subscription starting CSV from ``--starting-csv``.

    .. attribute :: channel
        :type: Optional[str]

        Subscription channel from ``--channel``.

    .. attribute :: source
        :type: Optional[str]

        CatalogSource name from ``--source``.

    .. attribute :: source_namespace
        :type: Optional[str]

        CatalogSource namespace from ``--source-namespace``.

    .. attribute :: install_mode
        :type: Optional[str]

        OperatorGroup install mode from ``--install-mode``.

    .. attribute :: approval
        :type: Optional[str]

        InstallPlan approval from ``--approval``.

    .. attribute :: starting_csv
        :type: Optional[str]

        Subscription starting CSV from ``--starting-csv``.
    """

    channel: Optional[str] = None
    source: Optional[str] = None
    source_namespace: Optional[str] = None
    install_mode: Optional[str] = None
    approval: Optional[str] = None
    starting_csv: Optional[str] = None


@dataclass(frozen=True, slots=True)
class OperatorPlan:
    """Hold the resolved enable decision the plan binds.

    :param package: OLM package name.
    :param namespace: Target namespace the package enables into.
    :param channel: Resolved Subscription channel.
    :param catalog: Catalog source that serves the package.
    :param install_mode: Resolved OperatorGroup install mode.
    :param approval: Resolved InstallPlan approval.
    :param starting_csv: Starting CSV, or None for the channel head.
    :param existing_subscription: Subscription already present for the package, or None.
    :param installed_csv: CSV reported by the existing Subscription, or None.
    :param csv_phase: Phase of the installed CSV, or None.

    .. attribute :: package
        :type: str

        OLM package name.

    .. attribute :: namespace
        :type: str

        Target namespace the package enables into.

    .. attribute :: channel
        :type: str

        Resolved Subscription channel.

    .. attribute :: catalog
        :type: CatalogSourceRef

        Catalog source that serves the package.

    .. attribute :: install_mode
        :type: str

        Resolved OperatorGroup install mode.

    .. attribute :: approval
        :type: str

        Resolved InstallPlan approval.

    .. attribute :: starting_csv
        :type: Optional[str]

        Starting CSV, or None for the channel head.

    .. attribute :: existing_subscription
        :type: Optional[JSONObject]

        Subscription already present for the package, or None.

    .. attribute :: installed_csv
        :type: Optional[str]

        CSV reported by the existing Subscription, or None.

    .. attribute :: csv_phase
        :type: Optional[str]

        Phase of the installed CSV, or None.
    """

    package: str
    namespace: str
    channel: str
    catalog: CatalogSourceRef
    install_mode: str
    approval: str
    starting_csv: Optional[str] = None
    existing_subscription: Optional[JSONObject] = None
    installed_csv: Optional[str] = None
    csv_phase: Optional[str] = None

    @property
    def enabled(self) -> bool:
        """Report whether the cluster already enables the package.

        :returns: True when a Subscription for the package exists (DEC-CI16-06).
        """
        return self.existing_subscription is not None


@dataclass(frozen=True, slots=True)
class OperatorNeed:
    """State one caller-suppliable value the plan cannot decide.

    :param need: Need code, for example ``override_conflict``.
    :param evidence: Observed cluster value that produced the need.
    :param supply: Option or action the caller supplies to resolve it.

    .. attribute :: need
        :type: str

        Need code, for example ``override_conflict``.

    .. attribute :: evidence
        :type: str

        Observed cluster value that produced the need.

    .. attribute :: supply
        :type: str

        Option or action the caller supplies to resolve it.
    """

    need: str
    evidence: str
    supply: str


@dataclass(frozen=True, slots=True)
class OLMAction(SkillAction):
    """Prepare stable inputs for one OLM operator operation.

    :param args: Shared command arguments and the verb's selections.
    :param state: One typed state used by every callback.
    :param target: Resolved Kubernetes target with optional registry profiles.
    :param package: OLM package name, for example ``nvidia-network-operator``.
    :param namespace: Target namespace the package enables into.
    :param overrides: Caller overrides, each checked against the cluster.

    .. attribute :: args
        :type: argparse.Namespace

        Shared command arguments and the verb's selections.

    .. attribute :: state
        :type: OLMState

        One typed state used by every callback.

    .. attribute :: target
        :type: Target

        Resolved Kubernetes target with optional registry profiles.

    .. attribute :: package
        :type: str

        OLM package name, for example ``nvidia-network-operator``.

    .. attribute :: namespace
        :type: str

        Target namespace the package enables into.

    .. attribute :: overrides
        :type: OperatorOverrides

        Caller overrides, each checked against the cluster.
    """

    args: argparse.Namespace
    state: OLMState
    target: Target
    package: str
    namespace: str
    overrides: OperatorOverrides


@dataclass
class OLMState(DeliveryState):
    """Mutable state the OLM callbacks hand to each other through named fields.

    :param package: OLM package name.
    :param namespace: Target namespace the package enables into.
    :param overrides: Caller overrides from the command line.
    :param plan: Resolved enable plan, or None before resolution.
    :param needs: Caller-suppliable values the resolution could not decide.
    :param shipped_document: Document the apply step sent, or None.
    :param wait_for: ``(field, value)`` from ``--wait-for``, or None.

    .. attribute :: package
        :type: str

        OLM package name.

    .. attribute :: namespace
        :type: str

        Target namespace the package enables into.

    .. attribute :: overrides
        :type: OperatorOverrides

        Caller overrides from the command line.

    .. attribute :: plan
        :type: Optional[OperatorPlan]

        Resolved enable plan, or None before resolution.

    .. attribute :: needs
        :type: list[OperatorNeed]

        Caller-suppliable values the resolution could not decide.

    .. attribute :: shipped_document
        :type: Optional[JSONObject]

        Document the apply step sent, or None.

    .. attribute :: wait_for
        :type: Optional[tuple[str, str]]

        ``(field, value)`` from ``--wait-for``, or None.
    """

    package: str = ""
    namespace: str = ""
    overrides: OperatorOverrides = field(default_factory=OperatorOverrides)
    plan: Optional[OperatorPlan] = None
    needs: list[OperatorNeed] = field(default_factory=list)
    shipped_document: Optional[JSONObject] = None
    wait_for: Optional[tuple[str, str]] = None


def operation_labels(operation: str) -> JSONObject:
    """Return the two ownership labels ``ResourceSet`` applies on create.

    :param operation: Operation name recorded under the operation label.
    :returns: Label mapping applied only to run-created objects.
    """
    return {MANAGED_BY_LABEL: MANAGED_BY_VALUE, OPERATION_LABEL: operation}


def namespace_document(namespace: str) -> JSONObject:
    """Build the Namespace document for the enable step.

    :param namespace: Target namespace name.
    :returns: Namespace document with ownership labels and no namespace field.
    """
    return {
        "apiVersion": NAMESPACE_API_VERSION,
        "kind": "Namespace",
        "metadata": {"name": namespace, "labels": operation_labels("ocp_operator")},
    }


def operatorgroup_document(namespace: str, install_mode: str) -> JSONObject:
    """Build the OperatorGroup document for the enable step.

    :param namespace: Target namespace name; the group is named after it.
    :param install_mode: Resolved install mode; ``OwnNamespace`` pins the
        target namespace, ``AllNamespaces`` omits it.
    :returns: OperatorGroup document.
    """
    spec: JSONObject = {}
    if install_mode == INSTALL_MODE_OWN_NAMESPACE:
        spec["targetNamespaces"] = [namespace]
    return {
        "apiVersion": OPERATORGROUP_API_VERSION,
        "kind": "OperatorGroup",
        "metadata": {
            "name": namespace,
            "namespace": namespace,
            "labels": operation_labels("ocp_operator"),
        },
        "spec": spec,
    }


def subscription_document(
    package: str,
    namespace: str,
    channel: str,
    catalog: CatalogSourceRef,
    approval: str,
    starting_csv: Optional[str] = None,
) -> JSONObject:
    """Build the Subscription document for the enable step.

    :param package: OLM package name; the Subscription is named after it.
    :param namespace: Target namespace.
    :param channel: Resolved channel.
    :param catalog: Resolved catalog source name and namespace.
    :param approval: ``Automatic`` or ``Manual``.
    :param starting_csv: Optional pinned starting CSV.
    :returns: Subscription document.
    """
    spec: JSONObject = {
        "name": package,
        "channel": channel,
        "source": catalog.name,
        "sourceNamespace": catalog.namespace,
        "installPlanApproval": approval,
    }
    if starting_csv is not None:
        spec["startingCSV"] = starting_csv
    return {
        "apiVersion": SUBSCRIPTION_API_VERSION,
        "kind": "Subscription",
        "metadata": {
            "name": package,
            "namespace": namespace,
            "labels": operation_labels("ocp_operator"),
        },
        "spec": spec,
    }
