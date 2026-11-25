"""Resolve callbacks for the OLM operator enable step (CI16-M1).

``ResolveOperatorPackage`` reads the cluster and either writes an
:class:`~lib.python.core.kubernetes.olm_models.OperatorPlan` into the shared
state or a ``needs`` list the caller can supply (DEC-CI16-15, DEC-CI16-16).
``ConfirmOperatorPlan`` binds the caller's confirmed digest to the resolved
plan before any writer runs. The write callbacks land with CI16-M2; the chain
builders in ``olm_cli`` already place them.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from typing import Final, Optional

from lib.python.core.json_types import JSONObject
from lib.python.core.kubernetes.kubernetes import (
    K8SCallback,
    KubernetesError,
    KubernetesService,
)
from lib.python.core.kubernetes.olm_models import (
    APPROVAL_AUTOMATIC,
    APPROVAL_MANUAL,
    CLUSTERSERVICEVERSION_RESOURCE,
    INSTALL_MODE_OWN_NAMESPACE,
    PACKAGEMANIFEST_RESOURCE,
    SUBSCRIPTION_RESOURCE,
    CatalogSourceRef,
    OLMAction,
    OperatorNeed,
    OperatorPlan,
)
from lib.python.core.report import plan_digest

OLM_GROUP_VERSION: Final = "operators.coreos.com/v1alpha1"
CSV_SUCCEEDED_PHASE: Final = "Succeeded"

APPROVALS: Final = (APPROVAL_AUTOMATIC, APPROVAL_MANUAL)

NEED_OLM_UNAVAILABLE: Final = "olm_unavailable"
NEED_PACKAGE_UNKNOWN: Final = "package_unknown"
NEED_SOURCE_AMBIGUOUS: Final = "source_ambiguous"
NEED_CHANNEL_UNKNOWN: Final = "channel_unknown"
NEED_MODE_UNSUPPORTED: Final = "mode_unsupported"
NEED_CSV_UNKNOWN: Final = "starting_csv_unknown"
NEED_APPROVAL_INVALID: Final = "approval_invalid"
NEED_OVERRIDE_CONFLICT: Final = "override_conflict"


def _olm_available(service: KubernetesService) -> bool:
    """Discover the OLM API without inferring from one missing resource.

    :param service: Verified Kubernetes service retained by the workflow.
    :returns: Whether the selected cluster exposes the OLM alpha API.
    :raises KubernetesError: If discovery fails ambiguously.
    """
    result = service.command("get", f"--raw=/apis/{OLM_GROUP_VERSION}")
    if result.returncode:
        detail = f"{result.stdout}\n{result.stderr}".casefold()
        if "404" in detail or "not found" in detail:
            return False
        raise KubernetesError("olm_api_unavailable")
    return True


def _catalogs_for(
    service: KubernetesService, package: str
) -> dict[CatalogSourceRef, JSONObject]:
    """Group the cluster's PackageManifests for one package by catalog source.

    A failed list call propagates the ``KubernetesError`` raised by ``service.json``.

    :param service: Verified Kubernetes service retained by the workflow.
    :param package: OLM package name.
    :returns: Mapping of catalog source identity to its PackageManifest.
    """
    document = service.json(
        "get",
        PACKAGEMANIFEST_RESOURCE,
        "-A",
        "--field-selector",
        f"metadata.name={package}",
        "-o",
        "json",
    )
    found: dict[CatalogSourceRef, JSONObject] = {}
    items = document.get("items", []) if isinstance(document, dict) else []
    for item in items:
        metadata = item.get("metadata", {})
        status = item.get("status", {})
        if metadata.get("name") != package:
            continue
        key = CatalogSourceRef(
            name=str(status.get("catalogSource", "")),
            namespace=str(status.get("catalogSourceNamespace", "")),
        )
        if key.name:
            found[key] = item
    return found


def _channel_entry(manifest: JSONObject, channel: str) -> Optional[JSONObject]:
    """Return one channel entry of a PackageManifest by name.

    :param manifest: PackageManifest object.
    :param channel: Channel name.
    :returns: The channel entry, or ``None`` when absent.
    """
    status = manifest.get("status", {})
    for entry in status.get("channels", []):
        if entry.get("name") == channel:
            return entry
    return None


def _supported_modes(
    manifest: JSONObject, csv: Optional[JSONObject]
) -> tuple[str, ...]:
    """List install modes the cluster evidences for the resolved channel.

    The installed CSV carries ``spec.installModes`` once the package is
    enabled; some clusters also list modes on the PackageManifest channel
    entry. When neither states any, the result is empty and the caller's
    choice or ``OwnNamespace`` stands unverified.

    :param manifest: Selected PackageManifest.
    :param csv: Installed CSV when present, else ``None``.
    :returns: Supported install-mode names in cluster order.
    """
    candidates = None
    if csv is not None:
        candidates = csv.get("spec", {}).get("installModes")
    if not candidates:
        status = manifest.get("status", {})
        for entry in status.get("channels", []):
            if entry.get("installModes"):
                candidates = entry["installModes"]
                break
    supported: list[str] = []
    for mode in candidates or []:
        if mode.get("supported") is not False:
            supported.append(str(mode.get("type")))
    return tuple(supported)


def _resolve_plan(
    action: OLMAction, service: KubernetesService
) -> tuple[OperatorPlan, list[OperatorNeed]]:
    """Read the cluster and resolve the enable plan or the caller-suppliable needs.

    :param action: Prepared OLM action with caller overrides.
    :param service: Verified Kubernetes service retained by the workflow.
    :returns: The resolved plan and the needs list; needs may be non-empty
        while the plan still carries every resolved evidence field.
    """
    needs: list[OperatorNeed] = []
    placeholder_catalog = CatalogSourceRef("", "")
    if not _olm_available(service):
        return (
            OperatorPlan(
                action.package,
                action.namespace,
                "",
                placeholder_catalog,
                INSTALL_MODE_OWN_NAMESPACE,
                APPROVAL_AUTOMATIC,
            ),
            [
                OperatorNeed(
                    need=NEED_OLM_UNAVAILABLE,
                    evidence=f"GET /apis/{OLM_GROUP_VERSION} returned 404",
                    supply="select a cluster with the Operator Lifecycle Manager",
                )
            ],
        )
    catalogs = _catalogs_for(service, action.package)
    if not catalogs:
        return (
            OperatorPlan(
                action.package,
                action.namespace,
                "",
                placeholder_catalog,
                INSTALL_MODE_OWN_NAMESPACE,
                APPROVAL_AUTOMATIC,
            ),
            [
                OperatorNeed(
                    need=NEED_PACKAGE_UNKNOWN,
                    evidence=f"no PackageManifest named {action.package}",
                    supply="pass --package with a name the cluster catalog serves",
                )
            ],
        )
    override_key = None
    if action.overrides.source is not None:
        override_key = CatalogSourceRef(
            action.overrides.source,
            action.overrides.source_namespace or "openshift-marketplace",
        )
    candidates = [override_key] if override_key is not None else list(catalogs)
    selected = next((key for key in candidates if key in catalogs), None)
    if selected is None or (override_key is None and len(catalogs) > 1):
        return (
            OperatorPlan(
                action.package,
                action.namespace,
                "",
                placeholder_catalog,
                INSTALL_MODE_OWN_NAMESPACE,
                APPROVAL_AUTOMATIC,
            ),
            [
                OperatorNeed(
                    need=NEED_SOURCE_AMBIGUOUS,
                    evidence="catalog sources: "
                    + ", ".join(f"{key.name}/{key.namespace}" for key in catalogs),
                    supply="pass --source and --source-namespace matching one listed source",
                )
            ],
        )
    manifest = catalogs[selected]
    status = manifest.get("status", {})
    existing = service.read(SUBSCRIPTION_RESOURCE, action.package, action.namespace)
    existing_channel = (
        existing.get("spec", {}).get("channel") if existing is not None else None
    )
    channel = (
        action.overrides.channel
        or existing_channel
        or str(status.get("defaultChannel", ""))
    )
    entry = _channel_entry(manifest, channel)
    if entry is None:
        listed = ", ".join(str(item.get("name")) for item in status.get("channels", []))
        return (
            OperatorPlan(
                action.package,
                action.namespace,
                channel,
                selected,
                INSTALL_MODE_OWN_NAMESPACE,
                APPROVAL_AUTOMATIC,
            ),
            [
                OperatorNeed(
                    need=NEED_CHANNEL_UNKNOWN,
                    evidence=f"channel {channel!r} not in PackageManifest channels: {listed}",
                    supply="pass --channel with one of the listed channels",
                )
            ],
        )
    installed_csv: Optional[str] = None
    csv: Optional[JSONObject] = None
    if existing is not None:
        installed_csv = existing.get("status", {}).get("installedCSV") or None
        if installed_csv:
            csv = service.read(
                CLUSTERSERVICEVERSION_RESOURCE, installed_csv, action.namespace
            )
    csv_phase = csv.get("status", {}).get("phase") if csv is not None else None
    install_mode = action.overrides.install_mode or INSTALL_MODE_OWN_NAMESPACE
    supported = _supported_modes(manifest, csv)
    if supported and install_mode not in supported:
        needs.append(
            OperatorNeed(
                need=NEED_MODE_UNSUPPORTED,
                evidence=f"install mode {install_mode!r} not in supported modes: "
                + ", ".join(supported),
                supply="pass --install-mode with one of the listed modes",
            )
        )
    approval = action.overrides.approval or APPROVAL_AUTOMATIC
    if approval not in APPROVALS:
        needs.append(
            OperatorNeed(
                need=NEED_APPROVAL_INVALID,
                evidence=f"approval {approval!r} not one of {', '.join(APPROVALS)}",
                supply="pass --approval Automatic or Manual",
            )
        )
    if action.overrides.starting_csv is not None:
        csvs = {str(item.get("name")) for item in entry.get("entries", [])}
        if action.overrides.starting_csv not in csvs:
            needs.append(
                OperatorNeed(
                    need=NEED_CSV_UNKNOWN,
                    evidence=f"starting CSV {action.overrides.starting_csv!r} not in channel {channel}",
                    supply="pass --starting-csv with a CSV of the chosen channel",
                )
            )
    if (
        existing is not None
        and action.overrides.channel is not None
        and existing_channel != channel
    ):
        needs.append(
            OperatorNeed(
                need=NEED_OVERRIDE_CONFLICT,
                evidence=f"existing Subscription uses channel {existing_channel!r}",
                supply="remove --channel or remove the existing Subscription first",
            )
        )
    return (
        OperatorPlan(
            package=action.package,
            namespace=action.namespace,
            channel=channel,
            catalog=selected,
            install_mode=install_mode,
            approval=approval,
            starting_csv=action.overrides.starting_csv,
            existing_subscription=existing,
            installed_csv=installed_csv,
            csv_phase=csv_phase,
        ),
        needs,
    )


def plan_evidence(plan: OperatorPlan) -> JSONObject:
    """Build the digest-bound evidence mapping of one resolved plan.

    :param plan: Resolved operator plan.
    :returns: JSON-safe mapping bound by the plan digest.
    """
    return {
        "package": plan.package,
        "namespace": plan.namespace,
        "channel": plan.channel,
        "catalog_source": plan.catalog.name,
        "catalog_source_namespace": plan.catalog.namespace,
        "install_mode": plan.install_mode,
        "approval": plan.approval,
        "starting_csv": plan.starting_csv,
        "enabled": plan.enabled,
        "installed_csv": plan.installed_csv,
        "csv_phase": plan.csv_phase,
    }


class ResolveOperatorPackage(K8SCallback[OLMAction]):
    """Read OLM state and resolve the enable plan or the caller-suppliable needs."""

    def run(self) -> JSONObject:
        """Resolve the package plan against the live cluster.

        :returns: Resolution evidence with the plan fields or the needs list.
        """
        plan, needs = _resolve_plan(self.action, self.kubernetes)
        self.state.plan = plan
        self.state.needs = needs
        return {
            "package": plan.package,
            "namespace": plan.namespace,
            "channel": plan.channel,
            "source": plan.catalog.name,
            "source_namespace": plan.catalog.namespace,
            "install_mode": plan.install_mode,
            "approval": plan.approval,
            "enabled": plan.enabled,
            "installed_csv": plan.installed_csv,
            "csv_phase": plan.csv_phase,
            "needs": [
                {"need": item.need, "evidence": item.evidence, "supply": item.supply}
                for item in needs
            ],
        }


class ConfirmOperatorPlan(K8SCallback[OLMAction]):
    """Bind the caller's confirmed digest to the resolved plan before writers run."""

    def run(self) -> JSONObject:
        """Recompute the plan digest and require the confirmed match in apply mode.

        :returns: Verified plan record with the digest.
        :raises ValueError: The caller's digest differs from the resolved plan.
        """
        plan = self.state.plan
        if plan is None:
            raise ValueError("operator_plan_unresolved")
        digest = plan_digest(plan_evidence(plan))
        confirm = self.action.args.confirm_plan
        if confirm is not None and confirm != digest:
            raise ValueError("confirm_plan_mismatch_rerun_live_plan")
        return {"verified": True, "plan_digest": digest, **plan_evidence(plan)}
