"""Build a declared toolbox with a native OpenShift binary build.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import base64
import hashlib
import json
import shlex
import tempfile
from collections.abc import (
    Callable,
    Iterable,
    Mapping,
    MutableMapping,
    MutableSequence,
    Sequence,
)
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Final, Optional, TypeGuard, TypeVar
from urllib.parse import urlsplit

from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.json_types import (
    JSONObject,
    JSONValue,
    is_json_value,
    json_integer,
    json_object,
    json_objects,
    json_string,
    json_strings,
)
from lib.python.core.kubernetes.kubernetes import (
    K8SCallback,
    KubernetesError,
    KubernetesService,
)
from lib.python.core.resources import ResourceSet
from lib.python.core.runtime import error_class
from lib.python.core.schema import (
    TOOLBOX_SCHEMA,
    SpecificationError,
    validate_specification,
)

MANAGED_ANNOTATION: Final = "ci-skills.spyroot.dev/managed-by"
MANAGED_VALUE: Final = "toolbox-build"
FINGERPRINT_ANNOTATION: Final = "ci-skills.spyroot.dev/build-fingerprint"
IMAGE_ANNOTATION: Final = "ci-skills.spyroot.dev/candidate-image"
DIGEST_ANNOTATION: Final = "ci-skills.spyroot.dev/output-digest"
BUILD_API_VERSION: Final = "build.openshift.io/v1"
BUILD_DOCKERFILE: Final = "Containerfile"
DOCTOR_NAME: Final = "toolbox-doctor"
SPEC_NAME: Final = "toolbox-spec.json"
CONDA_CHANNELS_ENV: Final = "CONDA_CHANNELS"
CONDA_DEFAULT_CHANNELS_ENV: Final = "CONDA_DEFAULT_CHANNELS"
CONDA_NAME: Final = "conda-environment.yml"
DOCTOR_PATH: Final = "/usr/local/bin/toolbox-doctor"
SPEC_PATH: Final = "/opt/ci-skills/toolbox-spec.json"
CONDA_PATH: Final = "/opt/ci-skills/conda-environment.yml"
NONROOT_UID: Final = 1001
BUILD_HISTORY_LIMIT: Final = 2
HASH_SEPARATOR: Final = b"\0"

DigestLookup = Callable[[str], Optional[str]]


class ToolboxError(RuntimeError):
    """A toolbox specification or build did not complete safely."""


FieldT = TypeVar("FieldT")


class SpecificationFields:
    """Narrow schema fields while retaining their toolbox specification location.

    :ivar document: Read-only JSON object supplied by the caller.
    :ivar path: Schema location used in value-free validation failures.
    """

    def __init__(self, document: JSONObject, path: str):
        """Bind the object and its known schema location without copying it.

        :param document: Decoded object whose fields are being converted to a model.
        :param path: Schema path naming the object, without caller-supplied values.
        """
        self.document = document
        self.path = path

    def read(self, name: str, reader: Callable[[JSONValue], FieldT]) -> FieldT:
        """Read one required field through a shared concrete JSON shape reader.

        :param name: Declared schema field to read from the bound object.
        :param reader: Shared JSON reader returning the field's concrete type.
        :returns: Field value narrowed by the selected reader.
        :raises SpecificationError: The field is absent or its JSON shape is invalid.
        """
        try:
            return reader(self.document[name])
        except (KeyError, TypeError) as exc:
            raise SpecificationError(
                f"invalid_specification:{self.path}/{name}:type"
            ) from exc

    def optional_text(self, name: str) -> Optional[str]:
        """Read a text field that is absent for unselected installation routes.

        :param name: Declared optional text field.
        :returns: Original text, or None when the field is absent or JSON null.
        :raises SpecificationError: A present nonnull value is not text.
        """  # noqa: DOC502 - field validation propagates from read
        return None if self.document.get(name) is None else self.read(name, json_string)

    def optional_strings(self, name: str) -> Sequence[str]:
        """Read an optional ordered string array with an empty absence default.

        :param name: Declared optional array field.
        :returns: Declared strings, or an empty sequence when the field is absent.
        :raises SpecificationError: A present value is not an array of strings.
        """  # noqa: DOC502 - field validation propagates from read
        return () if name not in self.document else self.read(name, json_strings)


def _installation_text(value: Optional[str], field: str) -> str:
    """Require a route-specific text value before rendering an installation command.

    :param value: Typed route field, absent for methods that do not consume it.
    :param field: Schema field required by the selected installation method.
    :returns: Original required text for shell quoting.
    :raises SpecificationError: The selected method lacks its required field.
    """
    if value is None:
        raise SpecificationError(
            f"invalid_specification:spec/dependencies/linux/{field}:required"
        )
    return value


def _toolbox_document(source: Path) -> JSONObject:
    """Decode finite JSON and validate the installed toolbox schema once.

    :param source: Resolved caller-selected specification path.
    :returns: Read-only specification object after authoritative schema validation.
    :raises OSError: The selected file or an installed schema cannot be read.
    :raises UnicodeError: Selected content is not valid UTF-8.
    :raises SpecificationError: Input JSON syntax, root shape, or schema fields are invalid.
    :raises ValueError: An installed schema contains malformed JSON.
    :raises TypeError: An installed schema document is not an object.
    :raises KeyError: An installed dependency schema lacks its required identifier.
    :raises SchemaError: An installed schema fails its meta-schema.
    :raises RecursionError: A document exceeds supported nesting depth.
    """  # noqa: DOC503 - file and installed-schema validation errors propagate
    try:
        document: object = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SpecificationError("invalid_specification:/:json") from exc
    if not is_json_value(document) or not isinstance(document, Mapping):
        raise SpecificationError("invalid_specification:/:type")
    validate_specification(document, TOOLBOX_SCHEMA)
    return document


@dataclass(frozen=True)
class LinuxInstall:
    """Validated installation route for one Linux dependency.

    :param method: Archive, deb, installer, download, Go, npm, or uv route.
    :param url: Download URL for artifact-backed routes, when selected.
    :param sha256: Expected artifact checksum for downloaded routes.
    :param strip_components: Archive path components removed during extraction.
    :param files: Optional archive members selected for extraction.
    :param module: Go module reference for the Go route.
    :param package: npm or uv package reference for package routes.
    :param destination: Optional installation path or binary directory.
    :param interpreter: Executable used for an installer script.
    :param args: Declared installer-script arguments.

    .. attribute :: method
        :type: str

        Archive, deb, installer, download, Go, npm, or uv route.

    .. attribute :: url
        :type: Optional[str]

        Download URL for artifact-backed routes, when selected.

    .. attribute :: sha256
        :type: Optional[str]

        Expected artifact checksum for downloaded routes.

    .. attribute :: strip_components
        :type: int

        Archive path components removed during extraction.

    .. attribute :: files
        :type: tuple[str, ...]

        Optional archive members selected for extraction.

    .. attribute :: module
        :type: Optional[str]

        Go module reference for the Go route.

    .. attribute :: package
        :type: Optional[str]

        npm or uv package reference for package routes.

    .. attribute :: destination
        :type: Optional[str]

        Optional installation path or binary directory.

    .. attribute :: interpreter
        :type: Optional[str]

        Executable used for an installer script.

    .. attribute :: args
        :type: tuple[str, ...]

        Declared installer-script arguments.
    """

    method: str
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
        """Create an installation route from a schema-validated object.

        :param value: Validated ``linux`` object.
        :returns: Typed installation route.
        :raises SpecificationError: A method omits a required field or a field has the wrong shape.
        """
        fields = SpecificationFields(value, "spec/dependencies/linux")
        route = cls(
            method=fields.read("method", json_string),
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
        required = {
            "archive": (route.url, route.sha256, route.destination),
            "go": (route.module,),
            "npm": (route.package,),
            "uv": (route.package,),
            "deb": (route.url, route.sha256),
            "installer": (route.url, route.sha256, route.interpreter),
            "download": (route.url, route.sha256, route.destination),
        }
        if route.method not in required or any(
            item is None for item in required[route.method]
        ):
            raise SpecificationError(
                "invalid_specification:spec/dependencies/linux:required"
            )
        return route

    def _operation(self, index: int) -> str:
        """Render one declared Linux dependency installation command.

        :param index: Stable dependency index used for a temporary download filename.
        :returns: Shell command for the selected archive, package, installer,
            download, Go, npm, or uv route with declared inputs quoted.
        :raises SpecificationError: A selected route lacks a required text field.
        """  # noqa: DOC502 - route-specific text validation propagates
        if self.method in {"archive", "deb", "installer", "download"}:
            return self._artifact_operation(index)
        if self.method == "go":
            destination = self.destination or "/usr/local/bin"
            return (
                f"mkdir -p {shlex.quote(destination)}; "
                f"GOBIN={shlex.quote(destination)} go install "
                f"{shlex.quote(_installation_text(self.module, 'module'))}"
            )
        if self.method == "npm":
            prefix = (
                f" --prefix {shlex.quote(self.destination)}"
                if self.destination is not None
                else ""
            )
            return (
                f"npm install --global{prefix} "
                f"{shlex.quote(_installation_text(self.package, 'package'))}"
            )
        destination = self.destination or "/usr/local/bin"
        return (
            f"mkdir -p {shlex.quote(destination)}; "
            f"UV_TOOL_BIN_DIR={shlex.quote(destination)} uv tool install "
            f"{shlex.quote(_installation_text(self.package, 'package'))}"
        )

    def _artifact_operation(self, index: int) -> str:
        """Render checksum verification, installation, and temporary artifact cleanup.

        :param index: Stable dependency index used in the temporary artifact name.
        :returns: Quoted command for the selected archive, deb, installer, or download.
        :raises SpecificationError: The selected route lacks a required text field.
        """  # noqa: DOC502 - route-specific text validation propagates
        suffix = ".deb" if self.method == "deb" else ""
        if self.method == "installer":
            suffix = PurePosixPath(
                urlsplit(_installation_text(self.url, "url")).path
            ).suffix
        artifact = f"/tmp/ci-skills-dependency-{index}{suffix}"
        prefix = (
            f"curl --fail --location --silent --show-error "
            f"--output {shlex.quote(artifact)} {shlex.quote(_installation_text(self.url, 'url'))}; "
            f"printf '%s  %s\\n' {shlex.quote(_installation_text(self.sha256, 'sha256'))} "
            f"{shlex.quote(artifact)} | sha256sum -c -; "
        )
        if self.method == "archive":
            members = " ".join(shlex.quote(item) for item in self.files)
            selected = f" {members}" if members else ""
            install = (
                f"mkdir -p {shlex.quote(_installation_text(self.destination, 'destination'))}; "
                f"tar -xf {shlex.quote(artifact)} "
                f"-C {shlex.quote(_installation_text(self.destination, 'destination'))} "
                f"--strip-components={self.strip_components}{selected}"
            )
        elif self.method == "deb":
            install = (
                "command -v apt-get >/dev/null 2>&1; "
                f"apt-get install -y {shlex.quote(artifact)}"
            )
        elif self.method == "installer":
            arguments = " ".join(shlex.quote(item) for item in self.args)
            install = (
                f"{shlex.quote(_installation_text(self.interpreter, 'interpreter'))} "
                f"{shlex.quote(artifact)}{(' ' + arguments) if arguments else ''}"
            )
        else:
            destination = _installation_text(self.destination, "destination")
            install = (
                f"mkdir -p {shlex.quote(str(PurePosixPath(destination).parent))}; "
                f"install -m 0755 {shlex.quote(artifact)} "
                f"{shlex.quote(destination)}"
            )
        return f"{prefix}{install}; rm -f {shlex.quote(artifact)}"

    def _paths(self) -> tuple[str, ...]:
        """Identify installed executable paths to add to image ``PATH``.

        :returns: Route-specific destination paths, or an empty tuple when no
            destination is declared.
        """
        if self.destination is None:
            return ()
        destination = PurePosixPath(self.destination)
        if self.method == "archive":
            return str(destination), str(destination / "bin")
        if self.method == "npm":
            return (str(destination / "bin"),)
        if self.method == "download":
            return (str(destination.parent),)
        return (str(destination),)

    def _writable_roots(self) -> tuple[str, ...]:
        """Identify installed paths to make group-writable in the image.

        :returns: Destination root, its parent for direct downloads, or empty tuple.
        """
        if self.destination is None:
            return ()
        destination = PurePosixPath(self.destination)
        root = destination.parent if self.method == "download" else destination
        return (str(root),)


@dataclass(frozen=True)
class Dependency:
    """One toolbox dependency with command probes and installation fallback.

    :param name: Dependency name from the specification.
    :param commands: Executables used to detect whether it is installed.
    :param apt: Packages selected for an apt-based base image.
    :param yum: Packages selected for a yum-based base image.
    :param linux: Optional explicit Linux installation route.

    .. attribute :: name
        :type: str

        Dependency name from the specification.

    .. attribute :: commands
        :type: tuple[str, ...]

        Executables used to detect whether it is installed.

    .. attribute :: apt
        :type: tuple[str, ...]

        Packages selected for an apt-based base image.

    .. attribute :: yum
        :type: tuple[str, ...]

        Packages selected for a yum-based base image.

    .. attribute :: linux
        :type: Optional[LinuxInstall]

        Optional explicit Linux installation route.
    """

    name: str
    commands: tuple[str, ...]
    apt: tuple[str, ...]
    yum: tuple[str, ...]
    linux: Optional[LinuxInstall]

    @classmethod
    def from_document(cls, value: JSONObject) -> Dependency:
        """Create a dependency from a schema-validated object.

        :param value: Validated dependency object.
        :returns: Typed dependency.
        :raises SpecificationError: A required field is missing or has an invalid shape.
        """  # noqa: DOC502 - field and installation validation errors propagate
        fields = SpecificationFields(value, "spec/dependencies")
        linux = value.get("linux")
        return cls(
            name=fields.read("name", json_string),
            commands=tuple(fields.read("commands", json_strings)),
            apt=tuple(fields.optional_strings("apt")),
            yum=tuple(fields.optional_strings("yum")),
            linux=(
                LinuxInstall.from_document(fields.read("linux", json_object))
                if linux is not None
                else None
            ),
        )

    def _render(self, index: int) -> Optional[str]:
        """Render a conditional Containerfile installation step.

        :param index: Dependency index passed to the Linux installation route.
        :returns: ``RUN`` instruction probing declared commands, or ``None`` when
            the dependency has no Linux fallback.
        :raises SpecificationError: The selected installation route lacks a required field.
        """  # noqa: DOC502 - installation rendering propagates field errors
        if self.linux is None:
            return None
        probes = " && ".join(
            f"command -v {shlex.quote(command)} >/dev/null 2>&1"
            for command in self.commands
        )
        return (
            "RUN set -eu; \\\n"
            f"    if ! ( {probes} ); then \\\n"
            f"        {self.linux._operation(index)}; \\\n"
            "    fi"
        )


@dataclass(frozen=True)
class CondaInput:
    """Selected Conda environment bytes and creation options.

    :param content: Exact environment file bytes included in build identity.
    :param prefix: Optional installation prefix passed to Conda.
    :param channels: Declared channel order used during environment creation.

    .. attribute :: content
        :type: bytes

        Exact environment file bytes included in build identity.

    .. attribute :: prefix
        :type: Optional[str]

        Optional installation prefix passed to Conda.

    .. attribute :: channels
        :type: tuple[str, ...]

        Declared channel order used during environment creation.
    """

    content: bytes
    prefix: Optional[str]
    channels: tuple[str, ...]

    @classmethod
    def from_document(cls, value: JSONObject, base: Path) -> CondaInput:
        """Resolve the caller-selected environment file.

        :param value: Validated Conda object.
        :param base: Directory containing the toolbox specification.
        :returns: Local content and declared Conda settings.
        :raises OSError: If the environment file cannot be read.
        :raises SpecificationError: File, prefix, or channel fields have invalid shapes.
        """  # noqa: DOC502 - field validation and selected file I/O propagate
        fields = SpecificationFields(value, "spec/conda")
        selected = Path(fields.read("environmentFile", json_string)).expanduser()
        source = (
            selected.resolve(strict=True)
            if selected.is_absolute()
            else (base / selected).resolve(strict=True)
        )
        return cls(
            content=source.read_bytes(),
            prefix=fields.optional_text("prefix"),
            channels=tuple(fields.read("channels", json_strings)),
        )

    def _render(self) -> tuple[str, str]:
        """Render the declared Conda file copy and environment creation steps.

        :returns: ``COPY`` and ``RUN`` Containerfile instructions using the declared
            channels and optional prefix.
        """
        channels = shlex.quote(",".join(self.channels))
        options: MutableSequence[str] = []
        if self.prefix is not None:
            options[:0] = ("--prefix", shlex.quote(self.prefix))
        arguments = " ".join(options)
        return (
            f"COPY {CONDA_NAME} {CONDA_PATH}",
            "RUN set -eu; \\\n"
            "    command -v conda >/dev/null 2>&1; \\\n"
            f"    {CONDA_CHANNELS_ENV}={channels} "
            f"{CONDA_DEFAULT_CHANNELS_ENV}={channels} "
            f"conda env create --file {shlex.quote(CONDA_PATH)} {arguments}".rstrip(),
        )


@dataclass(frozen=True)
class ToolboxSpec:
    """Schema-validated toolbox inputs with a deterministic source fingerprint.

    :param source_path: Resolved specification JSON path.
    :param name: Metadata name of the toolbox specification.
    :param base_image: Declared base image before live digest resolution.
    :param image_repository: Requested output repository.
    :param image_tag: Requested output tag.
    :param namespace: Build namespace selected by the specification.
    :param app: Build application name selected by the specification.
    :param dependencies: Validated dependencies in declaration order.
    :param required_commands: Executables checked by the toolbox doctor.
    :param environment: Sorted declared environment name/value pairs.
    :param platform: Optional build platform selector.
    :param conda: Optional local Conda environment input.
    :param canonical_json: Canonical specification bytes written into build context.
    :param fingerprint: SHA-256 identity of specification and local Conda bytes.

    .. attribute :: source_path
        :type: Path

        Resolved specification JSON path.

    .. attribute :: name
        :type: str

        Metadata name of the toolbox specification.

    .. attribute :: base_image
        :type: str

        Declared base image before live digest resolution.

    .. attribute :: image_repository
        :type: str

        Requested output repository.

    .. attribute :: image_tag
        :type: str

        Requested output tag.

    .. attribute :: namespace
        :type: str

        Build namespace selected by the specification.

    .. attribute :: app
        :type: str

        Build application name selected by the specification.

    .. attribute :: dependencies
        :type: tuple[Dependency, ...]

        Validated dependencies in declaration order.

    .. attribute :: required_commands
        :type: tuple[str, ...]

        Executables checked by the toolbox doctor.

    .. attribute :: environment
        :type: tuple[tuple[str, str], ...]

        Sorted declared environment name/value pairs.

    .. attribute :: platform
        :type: Optional[str]

        Optional build platform selector.

    .. attribute :: conda
        :type: Optional[CondaInput]

        Optional local Conda environment input.

    .. attribute :: canonical_json
        :type: bytes

        Canonical specification bytes written into build context.

    .. attribute :: fingerprint
        :type: str

        SHA-256 identity of specification and local Conda bytes.
    """

    source_path: Path
    name: str
    base_image: str
    image_repository: str
    image_tag: str
    namespace: str
    app: str
    dependencies: tuple[Dependency, ...]
    required_commands: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    platform: Optional[str]
    conda: Optional[CondaInput]
    canonical_json: bytes
    fingerprint: str

    @classmethod
    def from_path(cls, path: Path) -> ToolboxSpec:
        """Validate and bind one toolbox specification and local inputs.

        :param path: JSON specification selected by the caller.
        :returns: Typed specification with deterministic content fingerprint.
        :raises OSError: Selected content or an installed schema cannot be read.
        :raises UnicodeError: The selected specification is not valid UTF-8.
        :raises SpecificationError: Input JSON, schema, or local-input validation fails.
        :raises ValueError: An installed schema document is malformed.
        :raises TypeError: An installed schema document is not a JSON object.
        :raises KeyError: An installed dependency schema lacks its required identifier.
        :raises SchemaError: An installed schema fails its meta-schema.
        :raises RecursionError: A document exceeds supported nesting depth.
        """  # noqa: DOC502 - file, field, and installed-schema errors propagate
        source = path.expanduser().resolve(strict=True)
        document = _toolbox_document(source)
        root = SpecificationFields(document, "")
        body = SpecificationFields(root.read("spec", json_object), "spec")
        image = SpecificationFields(body.read("image", json_object), "spec/image")
        build = SpecificationFields(body.read("build", json_object), "spec/build")
        metadata = SpecificationFields(root.read("metadata", json_object), "metadata")
        conda = (
            CondaInput.from_document(body.read("conda", json_object), source.parent)
            if body.document.get("conda") is not None
            else None
        )
        canonical = json.dumps(
            document, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        identity = bytearray(canonical)
        if conda is not None:
            identity.extend(HASH_SEPARATOR)
            identity.extend(conda.content)
        environment = SpecificationFields(
            body.read("environment", json_object)
            if "environment" in body.document
            else {},
            "spec/environment",
        )
        return cls(
            source_path=source,
            name=metadata.read("name", json_string),
            base_image=body.read("baseImage", json_string),
            image_repository=image.read("repository", json_string),
            image_tag=image.read("tag", json_string),
            namespace=build.read("namespace", json_string),
            app=build.read("app", json_string),
            dependencies=tuple(
                Dependency.from_document(item)
                for item in body.read("dependencies", json_objects)
            ),
            required_commands=tuple(body.read("requiredCommands", json_strings)),
            environment=tuple(
                (name, environment.read(name, json_string))
                for name in sorted(environment.document)
            ),
            platform=body.optional_text("platform"),
            conda=conda,
            canonical_json=canonical,
            fingerprint=hashlib.sha256(identity).hexdigest(),
        )

    def render(self, directory: Path, resolved_base_image: str) -> None:
        """Write the deterministic OpenShift binary-build context.

        :param directory: Empty context directory owned by the caller.
        :param resolved_base_image: Immutable base image selected by live planning.
        :raises OSError: Writing build context files or permissions fails.
        :raises SpecificationError: A declared installation or environment field is invalid.
        """  # noqa: DOC502 - rendering and build-context file errors propagate
        (directory / BUILD_DOCKERFILE).write_text(
            self._containerfile(resolved_base_image), encoding="utf-8"
        )
        (directory / SPEC_NAME).write_bytes(self.canonical_json + b"\n")
        doctor = directory / DOCTOR_NAME
        doctor.write_text(self._doctor(), encoding="utf-8")
        doctor.chmod(0o555)
        if self.conda is not None:
            (directory / CONDA_NAME).write_bytes(self.conda.content)

    def _containerfile(self, resolved_base_image: str) -> str:
        """Render a complete deterministic Containerfile for the specification.

        :param resolved_base_image: Immutable base image selected by live planning.
        :returns: Containerfile text installing declared dependencies, doctor, and
            nonroot writable paths.
        :raises SpecificationError: An environment name or required install field is invalid.
        """
        lines = [
            f"FROM {resolved_base_image}",
            "USER 0",
            'SHELL ["/bin/sh", "-ceu"]',
        ]
        apt = _unique(
            item for dependency in self.dependencies for item in dependency.apt
        )
        yum = _unique(
            item for dependency in self.dependencies for item in dependency.yum
        )
        if apt or yum:
            lines.append(_package_instruction(apt, yum))
        environment = dict(self.environment)
        for name, value in self.environment:
            if not name.replace("_", "a").isalnum() or name[0].isdigit():
                raise SpecificationError(
                    "invalid_specification:spec/environment:variable_name"
                )
            lines.append(f"ENV {name}={json.dumps(value)}")
        if "HOME" not in environment:
            lines.append('ENV HOME="/tmp"')
        paths = _unique(
            path
            for dependency in self.dependencies
            if dependency.linux is not None
            for path in dependency.linux._paths()
        )
        if self.conda is not None and self.conda.prefix is not None:
            paths = _unique((*paths, f"{self.conda.prefix}/bin"))
        if paths:
            lines.append(f'ENV PATH="{":".join(paths)}:${{PATH}}"')
        lines.extend(
            rendered
            for index, dependency in enumerate(self.dependencies)
            if (rendered := dependency._render(index)) is not None
        )
        lines.extend(
            (
                f"COPY {SPEC_NAME} {SPEC_PATH}",
                f"COPY {DOCTOR_NAME} {DOCTOR_PATH}",
                f"RUN chmod 0555 {shlex.quote(DOCTOR_PATH)}",
            )
        )
        if self.conda is not None:
            lines.extend(self.conda._render())
        lines.append(self._ownership_instruction())
        lines.extend(
            (
                f"RUN {shlex.quote(DOCTOR_PATH)}",
                f"USER {NONROOT_UID}",
                "",
            )
        )
        return "\n".join(lines)

    def _ownership_instruction(self) -> str:
        """Render group-writable roots selected by the specification's install paths.

        :returns: Containerfile instruction preparing application, Conda, and home roots.
        """
        selected_home = dict(self.environment).get("HOME")
        roots = _unique(
            (
                "/opt/ci-skills",
                *(
                    root
                    for dependency in self.dependencies
                    if dependency.linux is not None
                    for root in dependency.linux._writable_roots()
                ),
                *((self.conda.prefix,) if self.conda and self.conda.prefix else ()),
                *((selected_home,) if selected_home else ()),
            )
        )
        quoted_roots = " ".join(shlex.quote(root) for root in roots)
        return (
            "RUN set -eu; \\\n"
            f"    mkdir -p {quoted_roots}; \\\n"
            f"    chgrp -R 0 {quoted_roots}; \\\n"
            f"    chmod -R g=u {quoted_roots}"
        )

    def _doctor(self) -> str:
        """Render an image health script for declared required commands.

        :returns: Shell script that exits nonzero when a required command is missing.
        """
        checks = "".join(
            f"command -v {shlex.quote(command)} >/dev/null 2>&1 || "
            f"{{ printf 'missing command: %s\\n' {shlex.quote(command)} >&2; exit 1; }}\n"
            for command in self.required_commands
        )
        return f"#!/bin/sh\nset -eu\n{checks}"

    def _node_selector(self) -> Optional[Mapping[str, str]]:
        """Convert an optional platform string into Kubernetes node labels.

        :returns: OS and architecture selector, or ``None`` when no platform is set.
        :raises SpecificationError: Platform is not exactly two nonblank slash-separated
            parts.
        """
        if self.platform is None:
            return None
        parts = self.platform.split("/")
        if len(parts) != 2 or any(
            not part or any(ch.isspace() for ch in part) for part in parts
        ):
            raise SpecificationError("invalid_specification:spec/platform:format")
        return {"kubernetes.io/os": parts[0], "kubernetes.io/arch": parts[1]}


@dataclass(frozen=True)
class BuildAction(SkillAction):
    """Bind a toolbox build to resolved images and private registry access.

    :param spec: Validated toolbox specification and local inputs.
    :param resolved_base_image: Immutable base image reference selected by planning.
    :param candidate_repository: Registry repository for the candidate image.
    :param candidate_reference: Candidate image tag selected for this build.
    :param dockerconfig: Private registry authentication bytes for the build.
    :param digest_lookup: Repository-bound reader accepting one candidate tag
        for independent digest read-back.

    .. attribute :: spec
        :type: ToolboxSpec

        Validated toolbox specification and local inputs.

    .. attribute :: resolved_base_image
        :type: str

        Immutable base image reference selected by planning.

    .. attribute :: candidate_repository
        :type: str

        Registry repository for the candidate image.

    .. attribute :: candidate_reference
        :type: str

        Candidate image tag selected for this build.

    .. attribute :: dockerconfig
        :type: bytes

        Private registry authentication bytes for the build.

    .. attribute :: digest_lookup
        :type: DigestLookup

        Repository-bound reader accepting one candidate tag for independent digest read-back.
    """

    spec: ToolboxSpec
    resolved_base_image: str
    candidate_repository: str
    candidate_reference: str
    dockerconfig: bytes = field(repr=False)
    digest_lookup: DigestLookup = field(repr=False)

    @property
    def image(self) -> str:
        """Return the internal candidate image tag.

        :returns: Candidate repository and reference accepted by OpenShift.
        """
        return f"{self.candidate_repository}:{self.candidate_reference}"

    @property
    def fingerprint(self) -> str:
        """Bind declared content, resolved base, and candidate identity.

        :returns: Deterministic build fingerprint without credentials.
        """
        return hashlib.sha256(
            HASH_SEPARATOR.join(
                (
                    self.spec.fingerprint.encode("ascii"),
                    self.resolved_base_image.encode("utf-8"),
                    self.image.encode("utf-8"),
                )
            )
        ).hexdigest()


@dataclass(frozen=True)
class ToolboxBuildResult:
    """Verified toolbox image identity from a new or reused OpenShift build.

    :param image: Full published candidate image reference.
    :param digest: Independently observed manifest digest.
    :param specification_fingerprint: Fingerprint of declared toolbox inputs.
    :param build_fingerprint: Fingerprint binding inputs to resolved base and image.
    :param namespace: Namespace where the build was verified.
    :param app: Build application name.
    :param build_name: Build resource name, when a new build ran.
    :param reused: Whether an existing verified image satisfied the build.
    :param verified: Whether independent image read-back succeeded.

    .. attribute :: image
        :type: str

        Full published candidate image reference.

    .. attribute :: digest
        :type: str

        Independently observed manifest digest.

    .. attribute :: specification_fingerprint
        :type: str

        Fingerprint of declared toolbox inputs.

    .. attribute :: build_fingerprint
        :type: str

        Fingerprint binding inputs to resolved base and image.

    .. attribute :: namespace
        :type: str

        Namespace where the build was verified.

    .. attribute :: app
        :type: str

        Build application name.

    .. attribute :: build_name
        :type: Optional[str]

        Build resource name, when a new build ran.

    .. attribute :: reused
        :type: bool

        Whether an existing verified image satisfied the build.

    .. attribute :: verified
        :type: bool

        Whether independent image read-back succeeded.
    """

    image: str
    digest: str
    specification_fingerprint: str
    build_fingerprint: str
    namespace: str
    app: str
    build_name: Optional[str]
    reused: bool
    verified: bool

    def public(self) -> JSONObject:
        """Return the verified build's declared JSON evidence.

        :returns: Image, fingerprints, build identity, and verification flags.
        """
        return {
            "image": self.image,
            "digest": self.digest,
            "specification_fingerprint": self.specification_fingerprint,
            "build_fingerprint": self.build_fingerprint,
            "namespace": self.namespace,
            "app": self.app,
            "build_name": self.build_name,
            "reused": self.reused,
            "verified": self.verified,
        }


class BuildToolbox(K8SCallback[BuildAction]):
    """Build or reuse one content-bound toolbox image in OpenShift."""

    def __init__(self, action: BuildAction):
        """Keep workflow-bound inputs without contacting a provider.

        :param action: Validated spec, candidate, credentials, and read-back.
        """
        super().__init__(action=action)
        self._build_name: Optional[str] = None
        self._secret_name: Optional[str] = None
        self._resource_set: Optional[ResourceSet] = None

    def _service(self) -> KubernetesService:
        """Retrieve the Kubernetes service bound by the parent callback.

        :returns: Bound Kubernetes service used for provider operations.
        """
        return self.kubernetes

    def _resources(self) -> ResourceSet:
        """Reuse one resource tracker for this build callback.

        :returns: Tracker scoped to the build namespace and ownership marker.
        """
        if self._resource_set is None:
            action = self.action
            self._resource_set = ResourceSet(
                self._service(), action.spec.namespace, MANAGED_VALUE
            )
        return self._resource_set

    def collect(self) -> JSONObject:
        """Reject invalid live-bound inputs before provider mutation.

        :returns: Empty evidence after validating immutable inputs and private config.
        :raises ToolboxError: If image or credential input is invalid.
        """
        action = self.action
        base_parts = action.resolved_base_image.rsplit("@", maxsplit=1)
        if (
            len(base_parts) != 2
            or not base_parts[0]
            or not _valid_digest(base_parts[1])
            or any(character.isspace() for character in action.resolved_base_image)
        ):
            raise ToolboxError("resolved_base_image_not_immutable")
        if any(
            not value or any(character.isspace() for character in value)
            for value in (action.candidate_repository, action.candidate_reference)
        ):
            raise ToolboxError("candidate_image_invalid")
        try:
            dockerconfig = json.loads(action.dockerconfig)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ToolboxError("dockerconfig_invalid") from exc
        if not isinstance(dockerconfig, dict) or not dockerconfig:
            raise ToolboxError("dockerconfig_invalid")
        return {}

    def _reusable(self) -> JSONObject:
        """Read an existing candidate and verify its ownership and exact content.

        :returns: Verified reuse evidence, or an empty mapping when a build is needed.
        :raises KubernetesError: If native BuildConfig read-back fails.
        :raises ToolboxError: If the selected existing BuildConfig has another owner.
        :raises APIError: If independent registry digest read-back fails.
        """  # noqa: DOC503 - provider and registry readers propagate failures
        action = self.action
        existing = self.kubernetes.read(
            "buildconfig", action.spec.app, action.spec.namespace
        )
        if existing is None:
            return {}
        if not self._resources().owns(existing):
            raise ToolboxError("buildconfig_ownership_conflict")
        annotations = self._annotations(existing)
        digest = annotations.get(DIGEST_ANNOTATION)
        if (
            annotations.get(FINGERPRINT_ANNOTATION) == action.fingerprint
            and annotations.get(IMAGE_ANNOTATION) == action.image
            and _valid_digest(digest)
            and action.digest_lookup(action.candidate_reference) == digest
        ):
            return self._verified_result(digest, None, reused=True)
        return {}

    def _start_build(self) -> str:
        """Submit the rendered source and read the exact created Build identity.

        :returns: Nonempty Build name returned by independent native read-back.
        :raises OSError: If temporary rendered build inputs cannot be written.
        :raises SpecificationError: A declared install or environment field cannot be rendered.
        :raises KubernetesError: If native Build read-back fails.
        :raises ToolboxError: If start-build fails or its result lacks an identity.
        """  # noqa: DOC503 - rendering, native reads, and temporary-file cleanup propagate failures
        action = self.action
        with tempfile.TemporaryDirectory(prefix="ci-skills-toolbox-") as directory:
            action.spec.render(Path(directory), action.resolved_base_image)
            result = self.kubernetes.command(
                "--namespace",
                action.spec.namespace,
                "start-build",
                f"buildconfig/{action.spec.app}",
                f"--from-dir={directory}",
                "-o",
                "name",
                openshift=True,
            )
        if result.returncode:
            raise ToolboxError("build_start_failed:" + error_class(result))
        reference = result.stdout.strip()
        if not reference:
            raise ToolboxError("build_reference_missing")
        started = self.kubernetes.json(
            "--namespace", action.spec.namespace, "get", reference, "-o", "json"
        )
        if not isinstance(started, Mapping):
            raise ToolboxError("build_start_invalid")
        metadata = started.get("metadata")
        name = metadata.get("name") if isinstance(metadata, Mapping) else None
        if not isinstance(name, str) or not name:
            raise ToolboxError("build_name_missing")
        self._build_name = name
        return name

    def run(self) -> JSONObject:
        """Reuse a verified candidate or build and independently verify its digest.

        :returns: Safe candidate identity and exact build/specification fingerprints.
        :raises KubernetesError: If native provider operations or read-back fail.
        :raises ToolboxError: If ownership, build, or output identity verification fails.
        :raises APIError: If independent registry read-back cannot complete.
        :raises OSError: If temporary build inputs cannot be prepared.
        :raises SpecificationError: A declared platform or install field is invalid.
        """  # noqa: DOC503 - owned build orchestration propagates provider and rendering failures
        reused = self._reusable()
        if reused:
            return reused
        secret = self._temporary_name("push")
        self._apply_secret(secret)
        self._resources().apply(self._build_config(secret, None))
        name = self._start_build()
        action = self.action
        completed = self.kubernetes.wait_build(name, action.spec.namespace)
        digest = self._output_digest(completed)
        observed = action.digest_lookup(action.candidate_reference)
        if observed != digest:
            raise ToolboxError("registry_digest_mismatch")
        self._resources().apply(self._build_config(secret, digest))
        return self._verified_result(digest, name, reused=False)

    def cleanup(self) -> JSONObject:
        """Recover durable state and delete temporary build resources.

        :returns: Empty evidence after owned resource recovery and cleanup.
        :raises KubernetesError: If recovery or temporary cleanup is incomplete.
        """
        errors: MutableSequence[BaseException] = []
        action = self.action
        if self._build_name is not None:
            try:
                self._service().delete("build", self._build_name, action.spec.namespace)
            except BaseException as exc:  # noqa: BLE001 - finish owned cleanup
                errors.append(exc)
        if self.state.failed and self._resource_set is not None:
            try:
                self._resource_set.recover()
            except BaseException as exc:  # noqa: BLE001 - retain every recovery result
                errors.append(exc)
        if self._secret_name is not None:
            try:
                self._resources().delete("secret", self._secret_name)
            except BaseException as exc:  # noqa: BLE001 - finish owned cleanup
                errors.append(exc)
        if errors:
            raise KubernetesError("toolbox_cleanup_incomplete") from errors[0]
        return {}

    def _annotations(self, document: JSONObject) -> Mapping[str, str]:
        """Read text annotations from a provider resource.

        :param document: Kubernetes object containing optional metadata annotations.
        :returns: String annotation mapping, or an empty mapping when malformed.
        """
        metadata = document.get("metadata")
        annotations = (
            metadata.get("annotations") if isinstance(metadata, Mapping) else None
        )
        if not isinstance(annotations, Mapping):
            return {}
        return {
            key: value
            for key, value in annotations.items()
            if isinstance(key, str) and isinstance(value, str)
        }

    def _managed_annotations(self, digest: Optional[str] = None) -> Mapping[str, str]:
        """Build ownership and candidate identity annotations.

        :param digest: Optional verified output image digest.
        :returns: Managed-by marker, build fingerprint, candidate image, and digest;
            missing digest is represented by empty text.
        """
        action = self.action
        return {
            MANAGED_ANNOTATION: MANAGED_VALUE,
            FINGERPRINT_ANNOTATION: action.fingerprint,
            IMAGE_ANNOTATION: action.image,
            DIGEST_ANNOTATION: digest or "",
        }

    def _temporary_name(self, purpose: str) -> str:
        """Derive a bounded temporary resource name from the build fingerprint.

        :param purpose: Resource purpose inserted before the digest prefix.
        :returns: Application prefix plus purpose and first twelve fingerprint chars,
            truncated to Kubernetes' 253-character name bound.
        """
        action = self.action
        suffix = f"-{purpose}-{action.fingerprint[:12]}"
        return f"{action.spec.app[: 253 - len(suffix)].rstrip('-')}{suffix}"

    def _apply_secret(self, name: str) -> None:
        """Apply a temporary registry credential Secret owned by this build.

        :param name: Exact temporary Secret name.
        :raises ToolboxError: Existing Secret is not owned by this build fingerprint.
        :raises KubernetesError: Provider read or apply fails.
        """  # noqa: DOC503 - provider read and apply propagate KubernetesError
        action = self.action
        existing = self._service().read("secret", name, action.spec.namespace)
        if existing is not None and (
            not self._resources().owns(existing)
            or self._annotations(existing).get(FINGERPRINT_ANNOTATION)
            != action.fingerprint
        ):
            raise ToolboxError("secret_ownership_conflict")
        secret = {
            "apiVersion": "v1",
            "data": {
                ".dockerconfigjson": base64.b64encode(action.dockerconfig).decode(
                    "ascii"
                )
            },
            "kind": "Secret",
            "metadata": {
                "annotations": self._managed_annotations(),
                "name": name,
                "namespace": action.spec.namespace,
            },
            "type": "kubernetes.io/dockerconfigjson",
        }
        self._resources().apply(secret)
        self._secret_name = name

    def _build_config(self, secret_name: str, digest: Optional[str]) -> JSONObject:
        """Build a Binary/Docker BuildConfig document for this candidate.

        :param secret_name: Push Secret referenced by the BuildConfig output.
        :param digest: Optional verified image digest for annotations.
        :returns: BuildConfig object with selected namespace, image, platform selector,
            binary source, Docker strategy, and managed identity.
        :raises SpecificationError: Declared platform selector is invalid.
        """  # noqa: DOC502 - platform validation propagates from the specification
        action = self.action
        specification: MutableMapping[str, JSONValue] = {
            "failedBuildsHistoryLimit": BUILD_HISTORY_LIMIT,
            "output": {
                "pushSecret": {"name": secret_name},
                "to": {"kind": "DockerImage", "name": action.image},
            },
            "runPolicy": "SerialLatestOnly",
            "source": {"type": "Binary"},
            "strategy": {
                "dockerStrategy": {
                    "dockerfilePath": BUILD_DOCKERFILE,
                    "forcePull": True,
                },
                "type": "Docker",
            },
            "successfulBuildsHistoryLimit": BUILD_HISTORY_LIMIT,
            "triggers": [],
        }
        selector = action.spec._node_selector()
        if selector is not None:
            specification["nodeSelector"] = selector
        return {
            "apiVersion": BUILD_API_VERSION,
            "kind": "BuildConfig",
            "metadata": {
                "annotations": self._managed_annotations(digest),
                "name": action.spec.app,
                "namespace": action.spec.namespace,
            },
            "spec": specification,
        }

    def _output_digest(self, build: JSONObject) -> str:
        """Read the immutable output digest from a completed Build.

        :param build: Kubernetes Build object returned after completion.
        :returns: Lowercase ``sha256:`` image digest with 64 hexadecimal characters.
        :raises ToolboxError: Output digest is absent or malformed.
        """
        status = build.get("status")
        output = status.get("output") if isinstance(status, Mapping) else None
        target = output.get("to") if isinstance(output, Mapping) else None
        digest = target.get("imageDigest") if isinstance(target, Mapping) else None
        if not _valid_digest(digest):
            raise ToolboxError("build_output_digest_missing")
        return digest

    def _verified_result(
        self, digest: str, build_name: Optional[str], reused: bool
    ) -> JSONObject:
        """Record a verified new or reused candidate image.

        :param digest: Verified immutable image digest.
        :param build_name: Completed Build name, or ``None`` for reuse.
        :param reused: Whether an existing candidate satisfied the fingerprint.
        :returns: Image identity, spec/build fingerprints, namespace, application,
            Build name, and verified reuse state.
        """
        action = self.action
        result = ToolboxBuildResult(
            image=action.image,
            digest=digest,
            specification_fingerprint=action.spec.fingerprint,
            build_fingerprint=action.fingerprint,
            namespace=action.spec.namespace,
            app=action.spec.app,
            build_name=build_name,
            reused=reused,
            verified=True,
        )
        self.state.build_result = result
        self.state.source_image = action.candidate_repository + "@" + digest
        return result.public()


def _package_instruction(apt: tuple[str, ...], yum: tuple[str, ...]) -> str:
    """Render package-manager installation branches for the Containerfile.

    :param apt: Apt package names declared by dependencies.
    :param yum: DNF/Yum package names declared by dependencies.
    :returns: ``RUN`` instruction using the available declared package manager;
        it fails inside the image when none is available.
    """
    branches: MutableSequence[str] = []
    if apt:
        packages = " ".join(shlex.quote(item) for item in apt)
        branches.append(
            "if command -v apt-get >/dev/null 2>&1; then \\\n"
            "        apt-get update; \\\n"
            f"        DEBIAN_FRONTEND=noninteractive apt-get install -y "
            f"--no-install-recommends {packages}; \\\n"
            "        rm -rf /var/lib/apt/lists/*"
        )
    if yum:
        packages = " ".join(shlex.quote(item) for item in yum)
        prefix = "elif" if branches else "if"
        branches.append(
            f"{prefix} command -v dnf >/dev/null 2>&1; then \\\n"
            f"        dnf install -y {packages}; \\\n"
            "        dnf clean all; \\\n"
            "    elif command -v yum >/dev/null 2>&1; then \\\n"
            f"        yum install -y {packages}; \\\n"
            "        yum clean all"
        )
    branches.append(
        "else \\\n"
        "        echo 'declared package manager unavailable' >&2; \\\n"
        "        exit 1"
    )
    return "RUN set -eu; \\\n    " + "; \\\n    ".join(branches) + "; \\\n    fi"


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    """Deduplicate ordered text values without changing first occurrence order.

    :param values: Iterable of paths or package names.
    :returns: Tuple of unique values in first-seen order.
    """
    return tuple(dict.fromkeys(values))


def _valid_digest(value: object) -> TypeGuard[str]:
    """Check an immutable image digest's exact lowercase SHA-256 shape.

    :param value: Candidate provider digest.
    :returns: Whether it is ``sha256:`` plus 64 lowercase hexadecimal digits.
    """
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    hexadecimal = value.removeprefix("sha256:")
    return len(hexadecimal) == 64 and all(
        ch in "0123456789abcdef" for ch in hexadecimal
    )
