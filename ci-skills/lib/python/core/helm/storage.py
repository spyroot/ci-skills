"""Read the native identity of a Helm release's Kubernetes storage object."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from lib.python.core.helm.native import HelmError, HelmRelease
from lib.python.core.kubernetes.kubernetes import KubernetesService
from lib.python.core.kubernetes.run_scope import validate_namespace
from lib.python.core.runtime import error_class, execution_trace

_RELEASE_TYPE: Final = "helm.sh/release.v1"
_STORAGE_PREFIX: Final = "sh.helm.release.v1"
_HELM_OWNER: Final = "helm"
_UID_LIMIT: Final = 128
_STORAGE_FIELDS: Final = (
    "{.metadata.name}",
    "{.metadata.namespace}",
    "{.metadata.uid}",
    "{.type}",
    "{.metadata.labels.owner}",
    "{.metadata.labels.name}",
    "{.metadata.labels.version}",
    "{.metadata.labels.status}",
    "{.metadata.deletionTimestamp}",
)
_STORAGE_PROJECTION: Final = "jsonpath=" + r'{"\t"}'.join(_STORAGE_FIELDS)


@dataclass(frozen=True)
class HelmStorageIdentity:
    """Identify the release Secret observed through its native metadata.

    .. attribute :: name
        :type: str

        Exact Helm Secret name.

    .. attribute :: uid
        :type: str

        Opaque native object UID.
    """

    name: str
    uid: str


def helm_storage_name(release: HelmRelease) -> str:
    """Derive the exact native Helm Secret name for an immutable revision.

    :param release: Selected release and its positive native revision.
    :returns: Helm Secret storage name.
    :raises HelmError: Release selectors or revision are invalid.
    """  # noqa: DOC503 - Invalid selectors normalize to HelmError.
    try:
        validate_namespace(release.name)
        validate_namespace(release.namespace)
        if type(release.revision) is not int or release.revision < 1:
            raise ValueError
        if not isinstance(release.status, str) or not release.status:
            raise ValueError
        return f"{_STORAGE_PREFIX}.{release.name}.v{release.revision}"
    except (TypeError, ValueError):
        raise HelmError("helm_storage_release_identity_invalid") from None


def read_helm_storage(
    kubernetes: KubernetesService, release: HelmRelease
) -> HelmStorageIdentity | None:
    """Read one exact release Secret without emitting its stored release data.

    :param kubernetes: Bound Kubernetes authority and native command limit.
    :param release: Expected release name, namespace, revision, and status.
    :returns: Verified storage name and UID, or None only on exact native absence.
    :raises HelmError: The storage read failed or its metadata differs from the
        expected release identity.
    """
    expected_name = helm_storage_name(release)
    # Native template failure diagnostics can contain the input object.
    with execution_trace(None):
        try:
            result = kubernetes.command(
                "get",
                f"secret/{expected_name}",
                "--namespace",
                release.namespace,
                "--ignore-not-found",
                "-o",
                _STORAGE_PROJECTION,
            )
        except OSError:
            raise HelmError("helm_storage_command_unavailable") from None
    if result.returncode:
        raise HelmError("helm_storage_read_" + error_class(result))
    if result.stdout == "" and result.stderr == "":
        return None
    fields = result.stdout.split("\t")
    if len(fields) != len(_STORAGE_FIELDS):
        raise HelmError("helm_storage_projection_invalid")
    (
        name,
        namespace,
        uid,
        resource_type,
        owner,
        label_name,
        revision,
        status,
        deletion,
    ) = fields
    if (
        name != expected_name
        or namespace != release.namespace
        or resource_type != _RELEASE_TYPE
        or owner != _HELM_OWNER
        or label_name != release.name
        or revision != str(release.revision)
        or status != release.status
        or deletion
        or not uid
        or len(uid) > _UID_LIMIT
        or any(not char.isprintable() or char.isspace() for char in uid)
    ):
        raise HelmError("helm_storage_identity_mismatch")
    return HelmStorageIdentity(name, uid)
