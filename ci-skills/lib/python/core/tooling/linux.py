"""Render shared Linux download and installation operations without transport.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from typing import Final, Optional
from urllib.parse import urlsplit

from lib.python.core.json_types import JSONObject, json_integer, json_string
from lib.python.core.schema import SpecificationError
from lib.python.core.tooling.fields import SpecificationFields

DEFAULT_BINARY_DIRECTORY: Final = "/usr/local/bin"
DEFAULT_ARTIFACT_DIRECTORY: Final = "/tmp"
ARTIFACT_NAME_PREFIX: Final = "ci-skills-dependency-"


class LinuxMethod(str, Enum):
    """Installation routes declared by the dependency schema.

    .. attribute :: ARCHIVE

        Extract selected archive members.

    .. attribute :: GO

        Install a Go module.

    .. attribute :: NPM

        Install an npm package.

    .. attribute :: UV

        Install a uv tool.

    .. attribute :: DEB

        Install a Debian package artifact.

    .. attribute :: INSTALLER

        Execute a verified installer script.

    .. attribute :: DOWNLOAD

        Install a verified executable artifact.
    """

    ARCHIVE = "archive"
    GO = "go"
    NPM = "npm"
    UV = "uv"
    DEB = "deb"
    INSTALLER = "installer"
    DOWNLOAD = "download"


ARTIFACT_METHODS: Final = frozenset(
    (LinuxMethod.ARCHIVE, LinuxMethod.DEB, LinuxMethod.INSTALLER, LinuxMethod.DOWNLOAD)
)


def _installation_text(value: Optional[str], field: str) -> str:
    """Require one field consumed by the selected installation route.

    :param value: Typed field, absent for routes that do not consume it.
    :param field: Schema field required by the selected installation method.
    :returns: Original text for shell quoting.
    :raises SpecificationError: The selected method lacks its required field.
    """
    if value is None:
        raise SpecificationError(
            f"invalid_specification:spec/dependencies/linux/{field}:required"
        )
    return value


@dataclass(frozen=True)
class LinuxInstall:
    """Validated Linux dependency route, shared by build and Pod consumers.

    :param method: Installation route selected by the dependency specification.
    :param url: Artifact URL; absent for native language package routes.
    :param sha256: Required checksum for artifact-backed routes.
    :param strip_components: Archive path components removed during extraction.
    :param files: Archive members selected for extraction; empty extracts all.
    :param module: Go module reference; absent for other routes.
    :param package: npm or uv package reference; absent for other routes.
    :param destination: Selected file or directory, when the route accepts one.
    :param interpreter: Executable used for an installer script.
    :param args: Ordered installer-script arguments.

    .. attribute :: method
        :type: LinuxMethod

        Selected installation route.

    .. attribute :: url
        :type: Optional[str]

        Artifact URL for download routes.

    .. attribute :: sha256
        :type: Optional[str]

        Expected artifact SHA-256.

    .. attribute :: strip_components
        :type: int

        Leading archive path components removed.

    .. attribute :: files
        :type: tuple[str, ...]

        Selected archive members; empty extracts all.

    .. attribute :: module
        :type: Optional[str]

        Go module reference.

    .. attribute :: package
        :type: Optional[str]

        npm or uv package reference.

    .. attribute :: destination
        :type: Optional[str]

        Caller-selected installation file or directory.

    .. attribute :: interpreter
        :type: Optional[str]

        Executable for installer scripts.

    .. attribute :: args
        :type: tuple[str, ...]

        Ordered installer-script arguments.
    """

    method: LinuxMethod
    url: Optional[str]
    sha256: Optional[str]
    strip_components: int
    files: tuple[str, ...]
    module: Optional[str]
    package: Optional[str]
    destination: Optional[str]
    interpreter: Optional[str]
    args: tuple[str, ...]

    @classmethod
    def from_document(cls, value: JSONObject) -> LinuxInstall:
        """Convert a schema-validated Linux installation object.

        :param value: Validated ``linux`` dependency object.
        :returns: Typed route retaining the original URL and installation arguments.
        :raises SpecificationError: A field is invalid or a route requirement is absent.
        """
        fields = SpecificationFields(value, "spec/dependencies/linux")
        try:
            method = LinuxMethod(fields.read("method", json_string))
        except ValueError as exc:
            raise SpecificationError(
                "invalid_specification:spec/dependencies/linux/method:enum"
            ) from exc
        route = cls(
            method=method,
            url=fields.optional_text("url"),
            sha256=fields.optional_text("sha256"),
            strip_components=(
                fields.read("stripComponents", json_integer)
                if "stripComponents" in value
                else 0
            ),
            files=tuple(fields.optional_strings("files")),
            module=fields.optional_text("module"),
            package=fields.optional_text("package"),
            destination=fields.optional_text("destination"),
            interpreter=fields.optional_text("interpreter"),
            args=tuple(fields.optional_strings("args")),
        )
        route._require_fields()
        return route

    def _require_fields(self) -> None:
        """Check route-specific fields required by existing dependency behavior.

        :raises SpecificationError: The selected method lacks a required field.
        """
        required = {
            LinuxMethod.ARCHIVE: (self.url, self.sha256, self.destination),
            LinuxMethod.GO: (self.module,),
            LinuxMethod.NPM: (self.package,),
            LinuxMethod.UV: (self.package,),
            LinuxMethod.DEB: (self.url, self.sha256),
            LinuxMethod.INSTALLER: (self.url, self.sha256, self.interpreter),
            LinuxMethod.DOWNLOAD: (self.url, self.sha256, self.destination),
        }
        if any(item is None for item in required[self.method]):
            raise SpecificationError(
                "invalid_specification:spec/dependencies/linux:required"
            )

    @property
    def artifact_backed(self) -> bool:
        """Identify routes with a distinct checksum-verified download step.

        :returns: Whether this route downloads an artifact before installation.
        """
        return self.method in ARTIFACT_METHODS

    def artifact_path(
        self, index: int, directory: str = DEFAULT_ARTIFACT_DIRECTORY
    ) -> str:
        """Name a staged artifact while preserving installer URL suffixes.

        :param index: Stable dependency index within the current composition.
        :param directory: Caller-selected writable staging directory inside its workload.
        :returns: Artifact path; query parameters never become part of its filename.
        :raises SpecificationError: An installer route has no URL.
        """  # noqa: DOC502 - required URL validation propagates
        suffix = ".deb" if self.method == LinuxMethod.DEB else ""
        if self.method == LinuxMethod.INSTALLER:
            suffix = PurePosixPath(
                urlsplit(_installation_text(self.url, "url")).path
            ).suffix
        return str(PurePosixPath(directory) / f"{ARTIFACT_NAME_PREFIX}{index}{suffix}")

    def download_operation(self, artifact: str) -> str:
        """Render transfer and checksum verification without installation.

        :param artifact: Exact staging path owned by the execution adapter.
        :returns: Shell command downloading the original URL and checking its SHA-256.
        :raises SpecificationError: This route has no artifact URL or checksum.
        """  # noqa: DOC502 - required artifact fields are validated by the shared reader
        return (
            "curl --fail --location --silent --show-error "
            f"--output {shlex.quote(artifact)} {shlex.quote(_installation_text(self.url, 'url'))}; "
            f"printf '%s  %s\\n' {shlex.quote(_installation_text(self.sha256, 'sha256'))} "
            f"{shlex.quote(artifact)} | sha256sum -c -"
        )

    def install_operation(self, artifact: str = "") -> str:
        """Render installation from a verified artifact or a language package route.

        :param artifact: Verified staging path; omitted for Go, npm and uv routes.
        :returns: Shell command performing installation without another artifact download.
        :raises SpecificationError: A required route field or artifact path is absent.
        """
        if self.artifact_backed:
            if not artifact:
                raise SpecificationError("invalid_specification:artifact:required")
            return self._artifact_installation(artifact)
        return self._package_installation()

    def _artifact_installation(self, artifact: str) -> str:
        """Render installation only for one already-downloaded artifact.

        :param artifact: Verified path belonging to this execution.
        :returns: Archive, Debian package, script, or binary installation command.
        :raises SpecificationError: A required route-specific field is absent.
        """  # noqa: DOC502 - required field validation propagates
        if self.method == LinuxMethod.ARCHIVE:
            return self._archive_installation(artifact)
        if self.method == LinuxMethod.DEB:
            return (
                "command -v apt-get >/dev/null 2>&1; "
                f"apt-get install -y {shlex.quote(artifact)}"
            )
        if self.method == LinuxMethod.INSTALLER:
            arguments = shlex.join(self.args)
            return (
                f"{shlex.quote(_installation_text(self.interpreter, 'interpreter'))} "
                f"{shlex.quote(artifact)}{(' ' + arguments) if arguments else ''}"
            )
        destination = _installation_text(self.destination, "destination")
        return (
            f"mkdir -p {shlex.quote(str(PurePosixPath(destination).parent))}; "
            f"install -m 0755 {shlex.quote(artifact)} {shlex.quote(destination)}"
        )

    def _archive_installation(self, artifact: str) -> str:
        """Render extraction with the caller's member selection and destination.

        :param artifact: Verified archive path.
        :returns: Shell command creating the destination and extracting selected members.
        :raises SpecificationError: The destination is absent.
        """  # noqa: DOC502 - required destination validation propagates
        destination = shlex.quote(_installation_text(self.destination, "destination"))
        members = shlex.join(self.files)
        return (
            f"mkdir -p {destination}; tar -xf {shlex.quote(artifact)} -C {destination} "
            f"--strip-components={self.strip_components}{(' ' + members) if members else ''}"
        )

    def _package_installation(self) -> str:
        """Render an existing Go, npm or uv installation route.

        :returns: Quoted package installation command preserving route defaults.
        :raises SpecificationError: The package or module reference is absent.
        """  # noqa: DOC502 - required package/module validation propagates
        destination = shlex.quote(self.destination or DEFAULT_BINARY_DIRECTORY)
        if self.method == LinuxMethod.GO:
            return (
                f"mkdir -p {destination}; GOBIN={destination} go install "
                f"{shlex.quote(_installation_text(self.module, 'module'))}"
            )
        if self.method == LinuxMethod.NPM:
            prefix = (
                f" --prefix {shlex.quote(self.destination)}"
                if self.destination is not None
                else ""
            )
            return (
                f"npm install --global{prefix} "
                f"{shlex.quote(_installation_text(self.package, 'package'))}"
            )
        return (
            f"mkdir -p {destination}; UV_TOOL_BIN_DIR={destination} uv tool install "
            f"{shlex.quote(_installation_text(self.package, 'package'))}"
        )

    def prerequisite_commands(self) -> tuple[str, ...]:
        """List native executables required by the selected installation route.

        :returns: Ordered executables to inspect before download or installation.
        :raises SpecificationError: An installer route omits its interpreter.
        """  # noqa: DOC502 - required interpreter validation propagates
        routes = {
            LinuxMethod.ARCHIVE: ("mkdir", "tar"),
            LinuxMethod.DEB: ("apt-get",),
            LinuxMethod.DOWNLOAD: ("mkdir", "install"),
            LinuxMethod.GO: ("mkdir", "go"),
            LinuxMethod.NPM: ("npm",),
            LinuxMethod.UV: ("mkdir", "uv"),
        }
        install = (
            (_installation_text(self.interpreter, "interpreter"),)
            if self.method == LinuxMethod.INSTALLER
            else routes[self.method]
        )
        return (("curl", "sha256sum") if self.artifact_backed else ()) + install

    def installation_paths(self) -> tuple[str, ...]:
        """Identify executable search directories for this installation route.

        :returns: Derived PATH additions, including established Go/uv defaults.
        """
        if self.destination is None:
            return (
                (DEFAULT_BINARY_DIRECTORY,)
                if self.method in (LinuxMethod.GO, LinuxMethod.UV)
                else ()
            )
        destination = PurePosixPath(self.destination)
        if self.method == LinuxMethod.ARCHIVE:
            return str(destination), str(destination / "bin")
        if self.method == LinuxMethod.NPM:
            return (str(destination / "bin"),)
        if self.method == LinuxMethod.DOWNLOAD:
            return (str(destination.parent),)
        return (str(destination),)

    def installation_roots(self) -> tuple[str, ...]:
        """Identify declared destination roots for workload write checks.

        :returns: Roots receiving installation writes; empty when the route owns selection.
        """
        if self.destination is None:
            return (
                (DEFAULT_BINARY_DIRECTORY,)
                if self.method in (LinuxMethod.GO, LinuxMethod.UV)
                else ()
            )
        destination = PurePosixPath(self.destination)
        root = (
            destination.parent if self.method == LinuxMethod.DOWNLOAD else destination
        )
        return (str(root),)

    def _operation(self, index: int) -> str:
        """Compose the shared operations for the existing toolbox renderer.

        :param index: Stable dependency index used for the staging filename.
        :returns: Download/install/cleanup command or native language package command.
        :raises SpecificationError: A required installation field is absent.
        """  # noqa: DOC502 - shared operation validation propagates
        return (
            self._artifact_operation(index)
            if self.artifact_backed
            else self.install_operation()
        )

    def _artifact_operation(self, index: int) -> str:
        """Compose separated artifact operations for Containerfile compatibility.

        :param index: Stable dependency index used for the staging filename.
        :returns: Existing quoted transfer, install and cleanup command sequence.
        :raises SpecificationError: A required installation field is absent.
        """  # noqa: DOC502 - shared operation validation propagates
        artifact = self.artifact_path(index)
        return (
            f"{self.download_operation(artifact)}; "
            f"{self.install_operation(artifact)}; rm -f {shlex.quote(artifact)}"
        )

    def _paths(self) -> tuple[str, ...]:
        """Retain toolbox PATH rendering only for explicitly declared destinations.

        :returns: Declared installation paths, or an empty tuple.
        """
        return () if self.destination is None else self.installation_paths()

    def _writable_roots(self) -> tuple[str, ...]:
        """Retain toolbox ownership changes for explicitly declared destinations.

        :returns: Declared write roots, or an empty tuple.
        """
        return () if self.destination is None else self.installation_roots()
