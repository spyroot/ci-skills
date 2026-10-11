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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final, cast
from urllib.parse import urlsplit

from .action import K8SCallback
from .kubernetes import KubernetesError, KubernetesService
from .resources import ResourceSet
from .runtime import error_class
from .schema import TOOLBOX_SCHEMA, SpecificationError, validate_specification

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

DigestLookup = Callable[[str, str], str | None]


class ToolboxError(RuntimeError):
    """A toolbox specification or build did not complete safely."""


@dataclass(frozen=True)
class LinuxInstall:
    """A schema-validated Linux installation route."""

    method: str
    url: str | None
    sha256: str | None
    strip_components: int
    files: tuple[str, ...]
    module: str | None
    package: str | None
    destination: str | None
    interpreter: str | None
    args: tuple[str, ...]

    @classmethod
    def from_document(cls, value: Mapping[str, Any]) -> LinuxInstall:
        """Create an installation route from a schema-validated object.

        :param value: Validated ``linux`` object.
        :returns: Typed installation route.
        :raises SpecificationError: If a method omits a behavioral input.
        """
        route = cls(
            method=cast(str, value["method"]),
            url=cast(str | None, value.get("url")),
            sha256=cast(str | None, value.get("sha256")),
            strip_components=cast(int, value.get("stripComponents", 0)),
            files=tuple(cast(list[str], value.get("files", []))),
            module=cast(str | None, value.get("module")),
            package=cast(str | None, value.get("package")),
            destination=cast(str | None, value.get("destination")),
            interpreter=cast(str | None, value.get("interpreter")),
            args=tuple(cast(list[str], value.get("args", []))),
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
        if self.method in {"archive", "deb", "installer", "download"}:
            suffix = ".deb" if self.method == "deb" else ""
            if self.method == "installer":
                suffix = PurePosixPath(urlsplit(cast(str, self.url)).path).suffix
            artifact = f"/tmp/ci-skills-dependency-{index}{suffix}"
            prefix = (
                f"curl --fail --location --silent --show-error "
                f"--output {shlex.quote(artifact)} {shlex.quote(cast(str, self.url))}; "
                f"printf '%s  %s\\n' {shlex.quote(cast(str, self.sha256))} "
                f"{shlex.quote(artifact)} | sha256sum -c -; "
            )
            if self.method == "archive":
                members = " ".join(shlex.quote(item) for item in self.files)
                selected = f" {members}" if members else ""
                install = (
                    f"mkdir -p {shlex.quote(cast(str, self.destination))}; "
                    f"tar -xf {shlex.quote(artifact)} "
                    f"-C {shlex.quote(cast(str, self.destination))} "
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
                    f"{shlex.quote(cast(str, self.interpreter))} "
                    f"{shlex.quote(artifact)}{(' ' + arguments) if arguments else ''}"
                )
            else:
                destination = cast(str, self.destination)
                install = (
                    f"mkdir -p {shlex.quote(str(PurePosixPath(destination).parent))}; "
                    f"install -m 0755 {shlex.quote(artifact)} "
                    f"{shlex.quote(destination)}"
                )
            return f"{prefix}{install}; rm -f {shlex.quote(artifact)}"
        if self.method == "go":
            destination = self.destination or "/usr/local/bin"
            return (
                f"mkdir -p {shlex.quote(destination)}; "
                f"GOBIN={shlex.quote(destination)} go install "
                f"{shlex.quote(cast(str, self.module))}"
            )
        if self.method == "npm":
            prefix = (
                f" --prefix {shlex.quote(self.destination)}"
                if self.destination is not None
                else ""
            )
            return (
                f"npm install --global{prefix} {shlex.quote(cast(str, self.package))}"
            )
        destination = self.destination or "/usr/local/bin"
        return (
            f"mkdir -p {shlex.quote(destination)}; "
            f"UV_TOOL_BIN_DIR={shlex.quote(destination)} uv tool install "
            f"{shlex.quote(cast(str, self.package))}"
        )

    def _paths(self) -> tuple[str, ...]:
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
        if self.destination is None:
            return ()
        destination = PurePosixPath(self.destination)
        root = destination.parent if self.method == "download" else destination
        return (str(root),)


@dataclass(frozen=True)
class Dependency:
    """One declared dependency and its Linux fallback."""

    name: str
    commands: tuple[str, ...]
    apt: tuple[str, ...]
    yum: tuple[str, ...]
    linux: LinuxInstall | None

    @classmethod
    def from_document(cls, value: Mapping[str, Any]) -> Dependency:
        """Create a dependency from a schema-validated object.

        :param value: Validated dependency object.
        :returns: Typed dependency.
        """
        linux = cast(Mapping[str, Any] | None, value.get("linux"))
        return cls(
            name=cast(str, value["name"]),
            commands=tuple(cast(list[str], value["commands"])),
            apt=tuple(cast(list[str], value.get("apt", []))),
            yum=tuple(cast(list[str], value.get("yum", []))),
            linux=LinuxInstall.from_document(linux) if linux is not None else None,
        )

    def _render(self, index: int) -> str | None:
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
    """A local Conda environment file included in build identity."""

    content: bytes
    prefix: str | None
    channels: tuple[str, ...]

    @classmethod
    def from_document(cls, value: Mapping[str, Any], base: Path) -> CondaInput:
        """Resolve the caller-selected environment file.

        :param value: Validated Conda object.
        :param base: Directory containing the toolbox specification.
        :returns: Local content and declared Conda settings.
        :raises OSError: If the environment file cannot be read.
        """
        selected = Path(cast(str, value["environmentFile"])).expanduser()
        source = (
            selected.resolve(strict=True)
            if selected.is_absolute()
            else (base / selected).resolve(strict=True)
        )
        return cls(
            content=source.read_bytes(),
            prefix=cast(str | None, value.get("prefix")),
            channels=tuple(cast(list[str], value["channels"])),
        )

    def _render(self) -> tuple[str, str]:
        channels = shlex.quote(",".join(self.channels))
        options: list[str] = []
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
    """Typed, schema-validated toolbox build input."""

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
    platform: str | None
    conda: CondaInput | None
    canonical_json: bytes
    fingerprint: str

    @classmethod
    def from_path(cls, path: Path) -> ToolboxSpec:
        """Validate and bind one toolbox specification and local inputs.

        :param path: JSON specification selected by the caller.
        :returns: Typed specification with deterministic content fingerprint.
        :raises OSError: If selected content cannot be read.
        :raises SpecificationError: If schema or local-input validation fails.
        """
        source = path.expanduser().resolve(strict=True)
        try:
            document = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SpecificationError("invalid_specification:/:json") from exc
        if not isinstance(document, dict):
            raise SpecificationError("invalid_specification:/:type")
        validate_specification(cast(Mapping[str, object], document), TOOLBOX_SCHEMA)
        body = cast(dict[str, Any], document["spec"])
        image = cast(dict[str, Any], body["image"])
        build = cast(dict[str, Any], body["build"])
        conda_value = cast(dict[str, Any] | None, body.get("conda"))
        conda = (
            CondaInput.from_document(conda_value, source.parent)
            if conda_value is not None
            else None
        )
        canonical = json.dumps(
            document, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        identity = bytearray(canonical)
        if conda is not None:
            identity.extend(HASH_SEPARATOR)
            identity.extend(conda.content)
        environment = cast(dict[str, str], body.get("environment", {}))
        return cls(
            source_path=source,
            name=cast(dict[str, str], document["metadata"])["name"],
            base_image=cast(str, body["baseImage"]),
            image_repository=image["repository"],
            image_tag=image["tag"],
            namespace=build["namespace"],
            app=build["app"],
            dependencies=tuple(
                Dependency.from_document(item)
                for item in cast(list[dict[str, Any]], body["dependencies"])
            ),
            required_commands=tuple(cast(list[str], body["requiredCommands"])),
            environment=tuple(sorted(environment.items())),
            platform=cast(str | None, body.get("platform")),
            conda=conda,
            canonical_json=canonical,
            fingerprint=hashlib.sha256(identity).hexdigest(),
        )

    def render(self, directory: Path, resolved_base_image: str) -> None:
        """Write the deterministic OpenShift binary-build context.

        :param directory: Empty context directory owned by the caller.
        :param resolved_base_image: Immutable base image selected by live planning.
        """
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
        selected_home = environment.get("HOME")
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
        lines.append(
            "RUN set -eu; \\\n"
            f"    mkdir -p {quoted_roots}; \\\n"
            f"    chgrp -R 0 {quoted_roots}; \\\n"
            f"    chmod -R g=u {quoted_roots}"
        )
        lines.extend(
            (
                f"RUN {shlex.quote(DOCTOR_PATH)}",
                f"USER {NONROOT_UID}",
                "",
            )
        )
        return "\n".join(lines)

    def _doctor(self) -> str:
        checks = "".join(
            f"command -v {shlex.quote(command)} >/dev/null 2>&1 || "
            f"{{ printf 'missing command: %s\\n' {shlex.quote(command)} >&2; exit 1; }}\n"
            for command in self.required_commands
        )
        return f"#!/bin/sh\nset -eu\n{checks}"

    def _node_selector(self) -> dict[str, str] | None:
        if self.platform is None:
            return None
        parts = self.platform.split("/")
        if len(parts) != 2 or any(
            not part or any(ch.isspace() for ch in part) for part in parts
        ):
            raise SpecificationError("invalid_specification:spec/platform:format")
        return {"kubernetes.io/os": parts[0], "kubernetes.io/arch": parts[1]}


@dataclass(frozen=True)
class BuildAction:
    """Inputs bound by the build workflow after live image resolution."""

    spec: ToolboxSpec
    resolved_base_image: str
    candidate_repository: str
    candidate_reference: str
    dockerconfig: bytes
    digest_lookup: DigestLookup

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
    """Verified image identity from a completed or reused build."""

    image: str
    digest: str
    specification_fingerprint: str
    build_fingerprint: str
    namespace: str
    app: str
    build_name: str | None
    reused: bool
    verified: bool


class BuildToolbox(K8SCallback[BuildAction, ToolboxBuildResult]):
    """Build or reuse one content-bound toolbox image in OpenShift."""

    def __init__(self, *, action: BuildAction) -> None:
        """Keep workflow-bound inputs without contacting a provider.

        :param action: Validated spec, candidate, credentials, and read-back.
        """
        super().__init__(action=action)
        self._build_name: str | None = None
        self._secret_name: str | None = None
        self._resource_set: ResourceSet | None = None

    def _service(self) -> KubernetesService:
        return cast(KubernetesService, self.kubernetes)

    def _resources(self) -> ResourceSet:
        if self._resource_set is None:
            action = self._action()
            self._resource_set = ResourceSet(
                self._service(), action.spec.namespace, MANAGED_VALUE
            )
        return self._resource_set

    def collect(self) -> None:
        """Reject invalid live-bound inputs before provider mutation.

        :raises ToolboxError: If image or credential input is invalid.
        """
        action = self._action()
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

    def run(self) -> ToolboxBuildResult:
        """Reuse a verified candidate or execute one binary build.

        :returns: Candidate image and digest verified through registry read-back.
        :raises KubernetesError: If a native provider operation fails.
        :raises ToolboxError: If ownership or identity verification fails.
        """  # noqa: DOC502,DOC503 - service errors intentionally propagate
        action = self._action()
        existing = self._service().read(
            "buildconfig", action.spec.app, action.spec.namespace
        )
        if existing is not None:
            if not self._resources().owns(existing):
                raise ToolboxError("buildconfig_ownership_conflict")
            annotations = self._annotations(existing)
            digest = annotations.get(DIGEST_ANNOTATION)
            if (
                annotations.get(FINGERPRINT_ANNOTATION) == action.fingerprint
                and annotations.get(IMAGE_ANNOTATION) == action.image
                and _valid_digest(digest)
                and action.digest_lookup(
                    action.candidate_repository, action.candidate_reference
                )
                == digest
            ):
                return self._result(cast(str, digest), None, reused=True)

        secret_name = self._temporary_name("push")
        self._apply_secret(secret_name)
        self._resources().apply(self._build_config(secret_name, None))
        with tempfile.TemporaryDirectory(prefix="ci-skills-toolbox-") as directory:
            action.spec.render(Path(directory), action.resolved_base_image)
            result = self._service().command(
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
        started = self._service().json(
            "--namespace", action.spec.namespace, "get", reference, "-o", "json"
        )
        if not isinstance(started, dict):
            raise ToolboxError("build_start_invalid")
        metadata = started.get("metadata")
        name = metadata.get("name") if isinstance(metadata, dict) else None
        if not isinstance(name, str) or not name:
            raise ToolboxError("build_name_missing")
        self._build_name = name
        completed = self._service().wait_build(name, action.spec.namespace)
        digest = self._output_digest(completed)
        observed = action.digest_lookup(
            action.candidate_repository, action.candidate_reference
        )
        if observed != digest:
            raise ToolboxError("registry_digest_mismatch")
        self._resources().apply(self._build_config(secret_name, digest))
        return self._result(digest, name, reused=False)

    def cleanup(self) -> None:
        """Delete the generated Build and owned temporary push Secret.

        :raises KubernetesError: If an owned resource remains present.
        """
        errors: list[BaseException] = []
        action = self._action()
        if self._build_name is not None:
            try:
                self._service().delete("build", self._build_name, action.spec.namespace)
            except BaseException as exc:  # noqa: BLE001 - finish owned cleanup
                errors.append(exc)
        if self._secret_name is not None:
            try:
                self._resources().delete("secret", self._secret_name)
            except BaseException as exc:  # noqa: BLE001 - finish owned cleanup
                errors.append(exc)
        if self.context.failed and self._resource_set is not None:
            try:
                self._resource_set.cleanup_created()
            except BaseException as exc:  # noqa: BLE001 - retain every cleanup result
                errors.append(exc)
        if errors:
            raise KubernetesError("toolbox_cleanup_incomplete") from errors[0]

    def _action(self) -> BuildAction:
        if not isinstance(self.action, BuildAction):
            raise ToolboxError("build_action_missing")
        return self.action

    def _annotations(self, document: Mapping[str, Any]) -> Mapping[str, str]:
        metadata = document.get("metadata")
        annotations = (
            metadata.get("annotations") if isinstance(metadata, dict) else None
        )
        if not isinstance(annotations, dict):
            return {}
        return {
            key: value
            for key, value in annotations.items()
            if isinstance(key, str) and isinstance(value, str)
        }

    def _managed_annotations(self, digest: str | None = None) -> dict[str, str]:
        action = self._action()
        return {
            MANAGED_ANNOTATION: MANAGED_VALUE,
            FINGERPRINT_ANNOTATION: action.fingerprint,
            IMAGE_ANNOTATION: action.image,
            DIGEST_ANNOTATION: digest or "",
        }

    def _temporary_name(self, purpose: str) -> str:
        action = self._action()
        suffix = f"-{purpose}-{action.fingerprint[:12]}"
        return f"{action.spec.app[: 253 - len(suffix)].rstrip('-')}{suffix}"

    def _apply_secret(self, name: str) -> None:
        action = self._action()
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

    def _build_config(self, secret_name: str, digest: str | None) -> dict[str, Any]:
        action = self._action()
        specification: dict[str, Any] = {
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

    def _output_digest(self, build: Mapping[str, Any]) -> str:
        status = build.get("status")
        output = status.get("output") if isinstance(status, dict) else None
        target = output.get("to") if isinstance(output, dict) else None
        digest = target.get("imageDigest") if isinstance(target, dict) else None
        if not _valid_digest(digest):
            raise ToolboxError("build_output_digest_missing")
        return cast(str, digest)

    def _result(
        self, digest: str, build_name: str | None, *, reused: bool
    ) -> ToolboxBuildResult:
        action = self._action()
        return ToolboxBuildResult(
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


def _package_instruction(apt: tuple[str, ...], yum: tuple[str, ...]) -> str:
    branches: list[str] = []
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
    return tuple(dict.fromkeys(values))


def _valid_digest(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    hexadecimal = value.removeprefix("sha256:")
    return len(hexadecimal) == 64 and all(
        ch in "0123456789abcdef" for ch in hexadecimal
    )
