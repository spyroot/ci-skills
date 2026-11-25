"""Use Helm against a bound Kubernetes target with a verified chart archive."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final
from urllib.parse import urlsplit

from lib.python.access.kubernetes import kubectl_argv, kubernetes_env
from lib.python.access.sources import Sources
from lib.python.core.cli_api import response_decoder
from lib.python.core.json_types import JSONObject, copy_json_object, is_json_value
from lib.python.core.kubernetes.kubernetes import KubernetesService
from lib.python.core.runtime import (
    CommandResult,
    error_class,
    execution_trace,
    run_command_bounded,
)

_MAX_ARCHIVE_BYTES: Final = 64 * 1024 * 1024
_RELEASE_NAME: Final = re.compile(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?")
_DIGEST: Final = re.compile(r"[a-f0-9]{64}")
_ARCHIVE_NAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.tgz")
_RELEASE_NOT_FOUND: Final = "Error: release: not found"
# These Helm defaults must not redirect the selected context, impersonate a
# different principal, or hide release history in another storage backend.
_ENVIRONMENT_OVERRIDES: Final = {
    "HELM_DEBUG": None,
    "HELM_DRIVER": "secret",
    "HELM_KUBEAPISERVER": None,
    "HELM_KUBEASGROUPS": None,
    "HELM_KUBEASUSER": None,
    "HELM_KUBECAFILE": None,
    "HELM_KUBECONTEXT": None,
    "HELM_KUBEINSECURE_SKIP_TLS_VERIFY": None,
    "HELM_KUBETLS_SERVER_NAME": None,
    "HELM_KUBETOKEN": None,
}


class HelmError(RuntimeError):
    """A stable native Helm failure without the raw output."""


@dataclass(frozen=True)
class VerifiedChart:
    """Use a local chart archive identified by its approved SHA-256 digest.

    .. attribute :: path
        :type: Path

        Caller-selected archive path.

    .. attribute :: sha256
        :type: str

        Expected SHA-256 digest as lowercase hexadecimal.
    """

    path: Path
    sha256: str

    @contextmanager
    def snapshot(self) -> Iterator[Path]:
        """Keep the archive bytes passed to Helm identical to the checked bytes.

        :yields: Private temporary path for the verified chart archive.
        :ytype: Path
        :raises HelmError: The source cannot be read, pinned, or copied.
        """
        _require_chart_digest(self.sha256)
        try:
            with self.path.open("rb") as handle:
                archive = handle.read(_MAX_ARCHIVE_BYTES + 1)
        except OSError as exc:
            raise HelmError("helm_chart_archive_unavailable") from exc
        if len(archive) > _MAX_ARCHIVE_BYTES:
            raise HelmError("helm_chart_archive_too_large")
        if hashlib.sha256(archive).hexdigest() != self.sha256:
            raise HelmError("helm_chart_digest_mismatch")
        with TemporaryDirectory(prefix="ci-skills-helm-") as directory:
            snapshot = Path(directory) / "chart.tgz"
            try:
                snapshot.write_bytes(archive)
            except OSError as exc:
                raise HelmError("helm_chart_snapshot_unavailable") from exc
            yield snapshot


def _require_chart_digest(sha256: str) -> None:
    if not _DIGEST.fullmatch(sha256):
        raise HelmError("helm_chart_digest_invalid")


def _public_archive_name(url: str) -> str:
    if any(not character.isprintable() or character.isspace() for character in url):
        raise HelmError("helm_chart_url_invalid")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise HelmError("helm_chart_url_invalid") from None
    name = parsed.path.rsplit("/", 1)[-1]
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or port == 0
        or parsed.username is not None
        or "?" in url
        or "#" in url
        or not _ARCHIVE_NAME.fullmatch(name)
    ):
        raise HelmError("helm_chart_url_invalid")
    return name


def _public_pull_environment(workspace: Path) -> dict[str, str | None]:
    cache = workspace / "cache"
    config = workspace / "config"
    data = workspace / "data"
    return {
        **_ENVIRONMENT_OVERRIDES,
        "HELM_CACHE_HOME": str(cache),
        "HELM_CONFIG_HOME": str(config),
        "HELM_CONTENT_CACHE": str(cache / "content"),
        "HELM_DATA_HOME": str(data),
        "HELM_PLUGINS": str(data / "plugins"),
        "HELM_REGISTRY_CONFIG": str(config / "registry.json"),
        "HELM_REPOSITORY_CACHE": str(cache / "repository"),
        "HELM_REPOSITORY_CONFIG": str(config / "repositories.yaml"),
        "DOCKER_CONFIG": str(config / "docker"),
        "REGISTRY_AUTH_FILE": None,
        "KUBECONFIG": str(config / "kubeconfig"),
    }


@contextmanager
def pull_verified_chart(
    url: str, sha256: str, *, timeout: float
) -> Iterator[VerifiedChart]:
    """Retrieve a public pinned chart for the duration of a caller operation.

    :param url: Approved HTTPS URL of an exact chart archive.
    :param sha256: Approved digest as lowercase hexadecimal.
    :param timeout: Maximum native pull runtime in seconds.
    :yields: Verified chart using private temporary storage.
    :ytype: VerifiedChart
    :raises HelmError: The URL, digest, timeout, download or archive is invalid.
    """
    filename = _public_archive_name(url)
    _require_chart_digest(sha256)
    if not math.isfinite(timeout) or timeout <= 0:
        raise HelmError("helm_timeout_invalid")
    with TemporaryDirectory(prefix="ci-skills-helm-pull-") as directory:
        workspace = Path(directory)
        try:
            result = run_command_bounded(
                ("helm", "pull", url, "--destination", str(workspace)),
                timeout=timeout,
                env=_public_pull_environment(workspace),
            )
        except OSError as exc:
            raise HelmError("helm_command_unavailable") from exc
        if result.returncode:
            raise HelmError("helm_pull_" + error_class(result))
        archive = workspace / filename
        if archive.is_symlink() or not archive.is_file():
            raise HelmError("helm_pulled_chart_missing")
        with VerifiedChart(archive, sha256).snapshot() as verified:
            yield VerifiedChart(verified, sha256)


@dataclass(frozen=True)
class HelmRelease:
    """Report the exact release selected and its latest native revision.

    .. attribute :: name
        :type: str

        Exact release name.

    .. attribute :: namespace
        :type: str

        Namespace containing the release history.

    .. attribute :: revision
        :type: int

        Latest numeric revision reported by Helm.

    .. attribute :: status
        :type: str

        Native status of the selected revision.
    """

    name: str
    namespace: str
    revision: int
    status: str


class HelmClient:
    """Read, render and install releases through the shared native runtime."""

    def __init__(self, kubernetes: KubernetesService) -> None:
        """Capture a service whose Kubernetes credentials have been bound.

        :param kubernetes: Selected Kubernetes authority and per-command timeout.
        :raises HelmError: No digest-bound kubeconfig was supplied.
        """
        sources = kubernetes.target.sources
        if (
            not isinstance(sources, Sources)
            or not sources.kubeconfig_files
            or len(sources.kubeconfig_digests) != len(sources.kubeconfig_files)
        ):
            raise HelmError("helm_kubeconfig_binding_required")
        self._kubernetes = kubernetes

    def _deadline(self, timeout: float | None) -> float:
        limit = self._kubernetes.timeout
        if timeout is not None:
            if not math.isfinite(timeout) or timeout <= 0:
                raise HelmError("helm_timeout_invalid")
            limit = min(limit, timeout)
        if not math.isfinite(limit) or limit <= 0:
            raise HelmError("helm_timeout_invalid")
        return time.monotonic() + limit

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HelmError("helm_timeout")
        return remaining

    @staticmethod
    def _validate_selection(release: str, namespace: str) -> None:
        if not _RELEASE_NAME.fullmatch(release) or len(release) > 53:
            raise HelmError("helm_release_name_invalid")
        if not _RELEASE_NAME.fullmatch(namespace) or len(namespace) > 63:
            raise HelmError("helm_namespace_invalid")

    def _run(
        self,
        namespace: str,
        *args: str,
        deadline: float,
        values: bytes | None = None,
        native_timeout: bool = False,
    ) -> CommandResult:
        target = self._kubernetes.target
        # Reuse kubectl's kubeconfig source precedence and freshness assertion.
        target_args = kubectl_argv(target)[1:]
        target_args[-2] = "--kube-context"
        environment = {
            **(kubernetes_env(target) or {}),
            **_ENVIRONMENT_OVERRIDES,
        }
        timeout = self._remaining(deadline)
        command = ["helm", *target_args, "--namespace", namespace, *args]
        if native_timeout:
            command.extend(("--timeout", f"{max(1, int(timeout * 1000))}ms"))
        try:
            return run_command_bounded(
                command, timeout=timeout, env=environment, input_data=values
            )
        except OSError as exc:
            raise HelmError("helm_command_unavailable") from exc

    def _verify_target(self, deadline: float) -> None:
        # Reserve at most half of the remaining budget for each of the two
        # native authority queries. Recheck the kubeconfig before each Helm call.
        remaining = int(self._remaining(deadline) / 2)
        if remaining < 1:
            raise HelmError("helm_timeout")
        KubernetesService(self._kubernetes.target, timeout=remaining).verify()

    def _status(
        self, release: str, namespace: str, deadline: float
    ) -> HelmRelease | None:
        result = self._run(
            namespace,
            "history",
            release,
            "--max",
            "1",
            "-o",
            "json",
            deadline=deadline,
        )
        if result.returncode == 1 and (
            result.stderr.strip() == _RELEASE_NOT_FOUND and not result.stdout.strip()
        ):
            return None
        if result.returncode:
            raise HelmError("helm_history_" + error_class(result))
        try:
            history = json.loads(result.stdout)
            if not isinstance(history, list) or len(history) != 1:
                raise ValueError
            row = history[0]
            revision = row["revision"]
            status = row["status"]
            if (
                type(revision) is not int
                or revision < 1
                or not isinstance(status, str)
                or not status
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise HelmError("helm_history_invalid_response") from None
        return HelmRelease(release, namespace, revision, status)

    def status(
        self, release: str, namespace: str, *, timeout: float | None = None
    ) -> HelmRelease | None:
        """Read a release's latest revision, including failed and pending installs.

        :param release: Exact release name.
        :param namespace: Exact release storage namespace.
        :param timeout: Optional remaining workflow seconds.
        :returns: Latest revision and status, or None on native release absence.
        :raises HelmError: Invalid selection, timeout, or ambiguous Helm response.
        """  # noqa: DOC502 - Selection checks and native history propagate errors.
        self._validate_selection(release, namespace)
        deadline = self._deadline(timeout)
        self._verify_target(deadline)
        return self._status(release, namespace, deadline)

    def values_match(
        self,
        release: HelmRelease,
        values: JSONObject,
        *,
        timeout: float | None = None,
    ) -> bool:
        """Compare one release revision's explicit values without reporting them.

        :param release: Exact release name, namespace and native revision.
        :param values: Expected nonsecret values supplied at installation.
        :param timeout: Optional remaining workflow seconds.
        :returns: Whether the explicit values match as a JSON object.
        :raises HelmError: Invalid inputs, failed native read or malformed response.
        """
        self._validate_selection(release.name, release.namespace)
        if type(release.revision) is not int or release.revision < 1:
            raise HelmError("helm_values_revision_invalid")
        deadline = self._deadline(timeout)
        try:
            if not isinstance(values, Mapping) or not is_json_value(values):
                raise HelmError("helm_values_invalid")
            expected = json.dumps(
                copy_json_object(values), sort_keys=True, allow_nan=False
            )
        except (TypeError, ValueError, RecursionError):
            raise HelmError("helm_values_invalid") from None
        # An unowned or replaced release may contain private values.
        with execution_trace(None):
            self._verify_target(deadline)
            result = self._run(
                release.namespace,
                "get",
                "values",
                release.name,
                "--revision",
                str(release.revision),
                "-o",
                "json",
                deadline=deadline,
            )
        if result.returncode:
            raise HelmError("helm_values_" + error_class(result))
        try:
            observed = response_decoder().decode(result.stdout)
            if not isinstance(observed, dict):
                raise HelmError("helm_values_invalid_response")
            return json.dumps(observed, sort_keys=True, allow_nan=False) == expected
        except (TypeError, ValueError, RecursionError):
            raise HelmError("helm_values_invalid_response") from None

    @staticmethod
    def _encode_values(values: JSONObject) -> bytes:
        try:
            return json.dumps(values, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise HelmError("helm_values_invalid") from exc

    def _render(
        self, release: str, namespace: str, chart: Path, values: bytes, deadline: float
    ) -> str:
        result = self._run(
            namespace,
            "template",
            release,
            str(chart),
            "--values",
            "-",
            "--dry-run=server",
            "--include-crds",
            deadline=deadline,
            values=values,
            native_timeout=True,
        )
        if result.returncode:
            raise HelmError("helm_render_" + error_class(result))
        if not result.stdout.strip():
            raise HelmError("helm_render_empty")
        return result.stdout

    def render(
        self,
        release: str,
        namespace: str,
        chart: VerifiedChart,
        values: JSONObject,
        *,
        timeout: float | None = None,
    ) -> str:
        """Produce chart manifests for local reconciliation without creating objects.

        :param release: Exact proposed release name.
        :param namespace: Exact proposed release namespace.
        :param chart: Caller-selected local archive and approved digest.
        :param values: Nonsecret overrides. Passed on stdin, outside native argv.
        :param timeout: Optional remaining workflow seconds.
        :returns: Raw rendered manifest for internal use, never a report field.
        :raises HelmError: Invalid input or a failed Helm render.
        """  # noqa: DOC502 - Archive checks and native rendering propagate errors.
        self._validate_selection(release, namespace)
        deadline = self._deadline(timeout)
        self._verify_target(deadline)
        with chart.snapshot() as path:
            return self._render(
                release, namespace, path, self._encode_values(values), deadline
            )

    def install_fresh(
        self,
        release: str,
        namespace: str,
        chart: VerifiedChart,
        values: JSONObject,
        *,
        verify_absent: Callable[[str], None],
        timeout: float | None = None,
    ) -> HelmRelease:
        """Install a new release after checking its names and chart objects.

        :param release: Exact proposed release name.
        :param namespace: Exact selected namespace, which must already exist.
        :param chart: Caller-selected local archive and approved digest.
        :param values: Nonsecret overrides, which Helm retains with the release.
        :param verify_absent: Caller checks every rendered object by native identity
            and raises before install if any cannot be proved absent.
        :param timeout: Optional remaining workflow seconds.
        :returns: Independently read deployed first release revision.
        :raises HelmError: Invalid input, an existing release, or an unverified install.
        """
        self._validate_selection(release, namespace)
        deadline = self._deadline(timeout)
        self._verify_target(deadline)
        payload = self._encode_values(values)
        with chart.snapshot() as path:
            verify_absent(self._render(release, namespace, path, payload, deadline))
            if self._status(release, namespace, deadline) is not None:
                raise HelmError("helm_release_already_exists")
            result = self._run(
                namespace,
                "install",
                release,
                str(path),
                "--values",
                "-",
                deadline=deadline,
                values=payload,
                native_timeout=True,
            )
        if result.returncode:
            # A failed response can follow a partial install. Do not retry or
            # let Helm automatically delete objects under reused names.
            raise HelmError("helm_install_" + error_class(result))
        observed = self._status(release, namespace, deadline)
        if observed is None or observed.revision != 1 or observed.status != "deployed":
            raise HelmError("helm_install_unverified")
        return observed

    def uninstall(
        self,
        release: str,
        namespace: str,
        *,
        timeout: float | None = None,
    ) -> bool:
        """Remove one installed release and independently verify its absence.

        :param release: Exact release name.
        :param namespace: Exact release storage namespace.
        :param timeout: Optional remaining workflow seconds.
        :returns: Whether a release was removed; verified absence needs no change.
        :raises HelmError: Invalid selection, a failed native uninstall, or a
            release that remains present afterwards.
        """  # noqa: DOC502 - Selection checks and native history propagate errors.
        self._validate_selection(release, namespace)
        deadline = self._deadline(timeout)
        self._verify_target(deadline)
        if self._status(release, namespace, deadline) is None:
            return False
        result = self._run(
            namespace,
            "uninstall",
            release,
            deadline=deadline,
            native_timeout=True,
        )
        if result.returncode:
            raise HelmError("helm_uninstall_" + error_class(result))
        if self._status(release, namespace, deadline) is not None:
            raise HelmError("helm_uninstall_unverified")
        return True
