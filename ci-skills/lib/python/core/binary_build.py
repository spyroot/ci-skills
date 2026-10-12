"""Validate caller-owned OpenShift binary build plans at an exact Git commit.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from lib.python.core.abstract.callback import Callback
from lib.python.core.abstract.context import ExecutionContext
from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.cli_api import CommandRunner
from lib.python.core.json_types import JSONObject, is_json_value
from lib.python.core.read_tools import MAX_READ_BYTES, ReadExit, ReadToolError
from lib.python.core.report import plan_digest
from lib.python.core.runtime import run_command_bounded


class BuildConfigLoader(yaml.SafeLoader):
    """Keep YAML names and timestamps as strings, as the former yq reader did."""


# PyYAML's default YAML 1.1 resolver treats names such as "on" and "off" as
# booleans and dates as Python objects. Kubernetes manifests use YAML 1.2
# scalar spelling and must remain JSON-compatible before fingerprinting.
BuildConfigLoader.yaml_implicit_resolvers = {
    first: [
        (tag, pattern)
        for tag, pattern in resolvers
        if tag not in {"tag:yaml.org,2002:bool", "tag:yaml.org,2002:timestamp"}
    ]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
BuildConfigLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)
BuildConfigLoader.add_constructor(
    "tag:yaml.org,2002:timestamp", yaml.SafeLoader.construct_scalar
)


@dataclass(frozen=True)
class BinaryBuildAction(SkillAction):
    """Caller-supplied BuildConfig and source identity for plan validation.

    :param spec: Path to the YAML BuildConfig manifest.
    :param source_repo: Local Git repository containing the selected commit.
    :param source_commit: Full lowercase 40-character commit identifier.
    :param commit_label: Manifest metadata label whose value must equal the commit.
    :param context: Cluster context to record, or ``None`` when not supplied.
        Plan validation does not contact a cluster.

    .. attribute :: spec
        :type: Path

        Path to the YAML BuildConfig manifest.

    .. attribute :: source_repo
        :type: Path

        Local Git repository containing the selected commit.

    .. attribute :: source_commit
        :type: str

        Full lowercase 40-character commit identifier.

    .. attribute :: commit_label
        :type: str

        Manifest metadata label whose value must equal the commit.

    .. attribute :: context
        :type: Optional[str]

        Cluster context to record, or ``None`` when not supplied.
    """

    spec: Path
    source_repo: Path
    source_commit: str
    commit_label: str
    context: Optional[str] = None


@dataclass(frozen=True)
class BinaryBuildPlan:
    """Validated build inputs and their content fingerprint.

    :param plan: Manifest, repository, commit, tree, context, and Dockerfile path.
    :param fingerprint: SHA-256 digest of the plan's canonical UTF-8 JSON.

    .. attribute :: plan
        :type: JSONObject

        Manifest, repository, commit, tree, context, and Dockerfile path.

    .. attribute :: fingerprint
        :type: str

        SHA-256 digest of the plan's canonical UTF-8 JSON.
    """

    plan: JSONObject
    fingerprint: str

    def document(self) -> JSONObject:
        """Return the existing public plan without implying a build ran.

        :returns: Plan-only result with its canonical SHA-256 fingerprint.
        """
        return {"mode": "dry-run", "fingerprint": self.fingerprint, "plan": self.plan}


class BinaryBuildPlanner:
    """Validate a BuildConfig against Git without contacting a cluster."""

    def __init__(
        self, action: BinaryBuildAction, command: CommandRunner = run_command_bounded
    ):
        """Retain one source plan and an injectable bounded native Git runner.

        :param action: Caller-owned BuildConfig and exact source commit inputs.
        :param command: Shared bounded process runner or an equivalent fixture.
        """
        self.action = action
        self.command = command

    def plan(self) -> BinaryBuildPlan:
        """Check manifest identity, source tree and Dockerfile before fingerprinting.

        :returns: Validated plan result consumable by another callback.
        :raises ReadToolError: If input, manifest, Git identity or Dockerfile is invalid.
        :raises OSError: If the Git process cannot be started or read.
        """  # noqa: DOC503 - The injected native runner may propagate OSError.
        action = self.action
        if (
            not re.fullmatch(r"[0-9a-f]{40}", action.source_commit)
            or not action.commit_label
        ):
            raise ReadToolError(
                "exact source commit and commit label are required", ReadExit.USAGE
            )
        if not action.spec.is_file() or not action.source_repo.is_dir():
            raise ReadToolError(
                "specification or source repository is missing", ReadExit.MISSING
            )
        manifest = self.load_manifest(action.spec)
        source_path = self.validate_manifest(manifest)
        root = self.git(action.source_repo, "rev-parse", "--show-toplevel")
        repository = Path(root)
        self.git(repository, "cat-file", "-e", f"{action.source_commit}^{{commit}}")
        kind = self.git(
            repository, "cat-file", "-t", f"{action.source_commit}:{source_path}"
        )
        if kind != "blob":
            raise ReadToolError("Dockerfile is not a file at the selected commit")
        tree = self.git(repository, "rev-parse", f"{action.source_commit}^{{tree}}")
        plan: JSONObject = {
            "manifest": manifest,
            "sourceRepo": root,
            "sourceCommit": action.source_commit,
            "sourceTree": tree,
            "context": action.context if action.context is not None else "",
            "sourceDockerfilePath": source_path,
        }
        return BinaryBuildPlan(plan, self.fingerprint(plan))

    @staticmethod
    def load_manifest(path: Path) -> JSONObject:
        """Read one bounded BuildConfig document without losing its field types.

        :param path: Caller-selected YAML file.
        :returns: String-keyed manifest whose nested values satisfy the JSON data model.
        :raises ReadToolError: If the file is unreadable, exceeds the byte bound,
            is malformed, or contains unsupported or recursive YAML values.
        """
        try:
            if path.stat().st_size > MAX_READ_BYTES:
                raise ReadToolError("BuildConfig exceeds 1 MiB")
            value: object = yaml.load(
                path.read_text(encoding="utf-8"), Loader=BuildConfigLoader
            )
            BinaryBuildPlanner.check_json_size(value)
            if not is_json_value(value) or not isinstance(value, Mapping):
                raise ReadToolError("BuildConfig must contain JSON-compatible data")
            return value
        except (OSError, UnicodeError, yaml.YAMLError, RecursionError) as exc:
            raise ReadToolError("BuildConfig could not be read") from exc

    @staticmethod
    def check_json_size(value: object) -> None:
        """Bound JSON expansion before recursive validation of decoded YAML.

        :param value: Untrusted YAML value or a validated plan awaiting serialization.
        :raises ReadToolError: If encoding exceeds one MiB, exceeds the same bounded
            encoder-step budget, contains nonfinite or non-JSON values, or reaches
            circular/deep recursion. Finite aliases remain valid within these bounds.
        """
        try:
            size = 0
            for steps, chunk in enumerate(
                json.JSONEncoder(allow_nan=False).iterencode(value), 1
            ):
                size += len(chunk.encode("utf-8"))
                if size > MAX_READ_BYTES or steps > MAX_READ_BYTES:
                    raise ReadToolError("expanded BuildConfig plan exceeds 1 MiB")
        except ReadToolError:
            raise
        except (TypeError, ValueError, RecursionError) as exc:
            raise ReadToolError(
                "BuildConfig must contain JSON-compatible data"
            ) from exc

    @staticmethod
    def fingerprint(plan: JSONObject) -> str:
        """Bound the expanded plan before computing its stable UTF-8 identity.

        :param plan: Validated manifest and exact source fields.
        :returns: SHA-256 digest using shared canonical plan serialization.
        :raises ReadToolError: If expansion exceeds the byte bound or the plan
            cannot be represented as finite, acyclic UTF-8 JSON.
        """
        BinaryBuildPlanner.check_json_size(plan)
        try:
            return plan_digest(plan, ensure_ascii=False)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ReadToolError(
                "BuildConfig must contain JSON-compatible data"
            ) from exc

    def git(self, repository: Path, *arguments: str) -> str:
        """Read source metadata with a bounded Git command.

        :param repository: Local repository used as Git's working directory.
        :param *arguments: Git subcommand and arguments to execute.
        :returns: Command stdout with surrounding whitespace removed.
        :raises ReadToolError: If Git fails, is unavailable, exceeds 25 seconds,
            or its output exceeds one MiB.
        :raises OSError: If the Git process cannot be started or read.
        """  # noqa: DOC503 - The injected native runner may propagate OSError.
        result = self.command(
            ["git", "-C", str(repository), *arguments],
            timeout=25,
            env={},
            max_stdout_bytes=MAX_READ_BYTES,
        )
        if result.returncode:
            code = (
                ReadExit.BLOCKED if result.returncode in (124, 127) else ReadExit.DATA
            )
            raise ReadToolError("source Git tree or Dockerfile is unavailable", code)
        return result.stdout.strip()

    @staticmethod
    def required_object(document: JSONObject, key: str) -> JSONObject:
        """Read one required BuildConfig object field.

        :param document: Validated parent JSON object.
        :param key: Required child field name.
        :returns: Child object with string keys and validated JSON values.
        :raises ReadToolError: If the field is missing or is not an object.
        """
        value = document.get(key)
        if not isinstance(value, Mapping):
            raise ReadToolError("BuildConfig fields or commit label are invalid")
        return value

    @staticmethod
    def required_text(document: JSONObject, key: str) -> str:
        """Read one required nonempty BuildConfig string field.

        :param document: Validated parent JSON object.
        :param key: Required child field name.
        :returns: Nonempty string stored in the selected field.
        :raises ReadToolError: If the field is missing, empty, or not a string.
        """
        value = document.get(key)
        if not isinstance(value, str) or not value:
            raise ReadToolError("BuildConfig fields or commit label are invalid")
        return value

    def validate_manifest(self, manifest: JSONObject) -> str:
        """Require a Binary/Docker BuildConfig tied to the selected commit.

        :param manifest: Parsed JSON-compatible BuildConfig mapping.
        :returns: Dockerfile path within the selected source tree, including its context directory.
        :raises ReadToolError: If kind, source, strategy, image output, names,
            context directory, Dockerfile path, or exact commit label is invalid.
        """
        action = self.action
        metadata = self.required_object(manifest, "metadata")
        spec = self.required_object(manifest, "spec")
        source = self.required_object(spec, "source")
        strategy = self.required_object(spec, "strategy")
        output = self.required_object(self.required_object(spec, "output"), "to")
        docker = self.required_object(strategy, "dockerStrategy")
        labels = self.required_object(metadata, "labels")
        if (
            manifest.get("apiVersion") != "build.openshift.io/v1"
            or manifest.get("kind") != "BuildConfig"
            or source.get("type") != "Binary"
            or strategy.get("type") != "Docker"
            or output.get("kind") != "DockerImage"
            or labels.get(action.commit_label) != action.source_commit
        ):
            raise ReadToolError("BuildConfig fields or commit label are invalid")
        self.required_text(metadata, "name")
        self.required_text(metadata, "namespace")
        self.required_text(output, "name")
        dockerfile = self.required_text(docker, "dockerfilePath")
        context = source.get("contextDir", "")
        if context is None:
            context = ""
        if not isinstance(context, str):
            raise ReadToolError("BuildConfig fields or commit label are invalid")
        return (
            f"{context.rstrip('/')}/{dockerfile}"
            if context and context != "."
            else dockerfile
        )


@dataclass
class BinaryBuildState(ExecutionContext):
    """Retain the validated source plan for a later explicitly ordered callback.

    ExecutionContext and SkillState document inherited invocation and lifecycle fields.

    :ivar build_plan: Validated manifest and Git identity assigned by PlanBinaryBuild.

    .. attribute :: build_plan
        :type: BinaryBuildPlan

        Validated manifest and Git identity assigned by PlanBinaryBuild.
    """

    build_plan: BinaryBuildPlan = field(init=False)


class PlanBinaryBuild(Callback[BinaryBuildState, BinaryBuildAction]):
    """Publish one validated build plan to shared state without executing a build."""

    def __init__(
        self, action: BinaryBuildAction, command: CommandRunner = run_command_bounded
    ):
        """Bind concrete build inputs to the reusable plan validator.

        :param action: Caller-owned manifest and exact source inputs.
        :param command: Shared bounded process runner or an equivalent fixture.
        :raises TypeError: If action does not implement SkillAction.
        """  # noqa: DOC502 - Callback construction validates the concrete action.
        super().__init__(action)
        self.planner = BinaryBuildPlanner(action, command)

    def run(self) -> JSONObject:
        """Store the validated plan and return its reportable document.

        :returns: Mapping containing dry-run mode, fingerprint, and exact plan inputs.
        :raises ReadToolError: If manifest, source identity, or Dockerfile is invalid.
        :raises OSError: If the Git process cannot be started or read.
        :raises AttributeError: If the executor has not bound shared state.
        """  # noqa: DOC502 - Planner validation and native reads can fail.
        self.state.build_plan = self.planner.plan()
        return self.state.build_plan.document()
