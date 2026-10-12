"""Plan and compose toolbox, registry, and self-hosted runner operations.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final, cast

from lib.python.core.action import (
    Callback,
    ExecutionContext,
    ExecutionResult,
    Executor,
    emit_result,
    plan_digest,
)
from lib.python.core.catalog import COMMAND_BY_KIND, COMMANDS, describe, missing_required_options
from lib.python.core.cli import parser
from lib.python.core.cli_api import APIError
from lib.python.access.credentials import bind_sources
from lib.python.core.gitlab.github import GitHubService
from lib.python.core.kubernetes.kubernetes import KubernetesError, KubernetesService
from lib.python.core.project_binding import resolve_target
from lib.python.core.harbor.registry_mirror import HarborService, TagSnapshot
from lib.python.core.report import report
from lib.python.core.resources import ResourceSet
from lib.python.core.gitlab.gitlab_runner import RunnerAction
from lib.python.core.schema import SCHEMA_DIRECTORY, TOOLBOX_SCHEMA
from lib.python.sys.status import APPLIED, BLOCKED, DRY_RUN, NO_OP, PASS, PLANNED
from lib.python.core.target import Target
from lib.python.core.toolbox import (
    BuildAction,
    BuildToolbox,
    ToolboxBuildResult,
    ToolboxError,
    ToolboxSpec,
)

DEFAULT_OPERATION_TIMEOUT: Final = 1800
MUTATION_OPERATIONS: Final = ("create", "update", "delete")
BUILD_TAG_PREFIX: Final = "ci-skills-build-"
FINGERPRINT_TAG_LENGTH: Final = 24


def workflow_parser(kind: str) -> argparse.ArgumentParser:
    """Build a declared workflow parser from the shared command catalogue.

    :param kind: Registered workflow report kind.
    :returns: Parser using the existing target and output argument contract.
    """
    entry = COMMANDS[COMMAND_BY_KIND[kind]]
    result = parser(entry["purpose"], kind=kind)
    result.set_defaults(kind=kind)
    if "--spec" in entry["options"]:
        result.epilog = (result.epilog or "") + (
            f" Toolbox input schema: {SCHEMA_DIRECTORY / TOOLBOX_SCHEMA}"
        )
    result.add_argument("action", nargs="?", choices=tuple(entry["subcommands"]))
    for option, help_text in entry["options"].items():
        settings: dict[str, Any] = {"help": help_text}
        if option == "--apply":
            settings["action"] = "store_true"
        elif option == "--label":
            settings.update(action="append", default=[])
        elif option == "--timeout":
            settings.update(type=int, default=DEFAULT_OPERATION_TIMEOUT)
        result.add_argument(option, **settings)
    return result


@dataclass
class WorkflowAction:
    """Workflow inputs and report state using the existing plan/apply protocol."""

    args: argparse.Namespace
    target: Target | None = None
    spec: ToolboxSpec | None = None
    runner: RunnerAction | None = None
    resolved_base: str | None = None
    resolved_source: str | None = None
    digest: str | None = None
    context: ExecutionContext = field(default_factory=ExecutionContext)

    def prepare(self) -> None:
        """Validate caller inputs and bind the shared target selection.

        :raises ValueError: If required operation inputs are missing or inconsistent.
        :raises TargetError: If target or credential binding is invalid.
        :raises SpecificationError: If a selected toolbox spec fails its schema.
        :raises OSError: If a caller-selected input cannot be read.
        """
        args = self.args
        if not args.action:
            raise ValueError("action_required")
        if args.apply and args.dry_run:
            raise ValueError("apply_and_dry_run_conflict")
        if args.confirm_plan and not args.apply:
            raise ValueError("confirm_plan_requires_apply")
        if args.timeout <= 0:
            raise ValueError("timeout_must_be_positive")
        self.context = ExecutionContext.from_args(args)
        if getattr(args, "spec", None):
            self.spec = ToolboxSpec.from_path(Path(args.spec).expanduser())
        if args.action in ("create", "update") and self.spec is None:
            raise ValueError("spec_required")
        entry = COMMANDS[COMMAND_BY_KIND[args.kind]]
        missing = missing_required_options(COMMAND_BY_KIND[args.kind], args)
        if missing:
            raise ValueError("missing_options:" + ",".join(missing))
        surfaces = tuple(entry["requires"])
        if args.kind == "create_selfhosted_runner" and args.action == "delete":
            surfaces = ("github", "kubernetes")
        self.target = resolve_target(
            args.target, args.binding, dry_run=args.dry_run, required_surfaces=surfaces
        )
        if not args.dry_run:
            self.target = bind_sources(self.target, revision=args.revision)
        namespace = getattr(args, "namespace", None) or (
            self.spec.namespace if self.spec else None
        )
        name = getattr(args, "name", None) or (self.spec.app if self.spec else None)
        if args.kind != "harbor_push" and (not namespace or not name):
            raise ValueError("namespace_and_name_required")
        root = getattr(args, "runner_root", None) or ""
        labels = tuple(getattr(args, "label", []))
        if (
            args.kind == "create_selfhosted_runner"
            and args.action != "delete"
            and (not root or not labels)
        ):
            raise ValueError("runner_root_and_labels_required")
        if namespace and name:
            self.runner = RunnerAction(namespace, name, root, labels, args.timeout)
        if self.spec is not None and "@sha256:" in self.spec.base_image:
            self.resolved_base = self.spec.base_image

    def fingerprint(self) -> str:
        """Bind operation inputs to the exact selected endpoints and base image.

        :returns: Canonical operation fingerprint with no credential contents.
        :raises ValueError: If preparation has not selected a target.
        """
        if self.target is None:
            raise ValueError("target_missing")
        data = {
            "kind": self.args.kind,
            "operation": self.args.action,
            "skill": self.target.skill,
            "tested_revision": self.target.tested_revision,
            "target_file": str(self.target.source_file),
            "target_source": self.target.source_kind,
            "github": asdict(self.target.github) if self.target.github else None,
            "kubernetes": {
                "context": self.target.kubernetes.context,
                "server": self.target.kubernetes.server,
            }
            if self.target.kubernetes
            else None,
            "harbor": {
                "url": self.target.harbor.url,
                "project": self.target.harbor.project,
            }
            if self.target.harbor
            else None,
            "registry_inputs": {
                key: getattr(self.args, key, None)
                for key in ("source_image", "repository", "tag", "image")
            },
            "specification": self.spec.fingerprint if self.spec else None,
            "base_image": self.resolved_base,
            "source_image": self.resolved_source,
            "runner": asdict(self.runner) if self.runner else None,
        }
        if data["github"] and data["github"]["token_file"] is not None:
            data["github"]["token_file"] = str(data["github"]["token_file"])
        return plan_digest(data)

    @property
    def repository(self) -> str:
        """Return the exact project-relative registry repository for this action.

        :returns: Repository from the toolbox spec or explicit operation inputs.
        :raises ValueError: If an image points outside the configured Harbor target.
        """
        if self.spec is not None:
            return self.spec.image_repository
        selected = getattr(self.args, "repository", None)
        if selected:
            return selected
        image = getattr(self.args, "image", "")
        if self.target is None or self.target.harbor is None:
            raise ValueError("harbor_target_missing")
        prefix = f"{self.target.harbor.host}/{self.target.harbor.project}/"
        if not image.startswith(prefix) or "@sha256:" not in image:
            raise ValueError("harbor_image_target_mismatch")
        return image[len(prefix) :].rsplit("@", 1)[0]

    def execute(self, factory: Callable[[], Executor]) -> int:
        """Plan or run callbacks and emit the existing versioned report envelope.

        :param factory: Thin command composition created after input preparation.
        :returns: Canonical report exit code.
        """
        args = self.args
        if args.describe:
            return emit_result(
                ExecutionResult(None, describe(COMMAND_BY_KIND[args.kind])), args
            )
        output = report(args.kind, "", {}, [], [])
        if args.log_level == "debug" and args.log_file:
            output["trace_artifact"] = str(args.log_file)
        executor: Executor | None = None
        try:
            self.prepare()
            output["target"] = str(self.target.source_file)
            if self.target.skill is not None:
                output["skill"] = self.target.skill
                output["tested_revision"] = self.target.tested_revision
            if args.dry_run:
                self.digest = self.fingerprint()
                output["status"] = DRY_RUN
            else:
                executor = factory()
                executor.run()
                output["status"] = PASS if args.apply else PLANNED
        except (ValueError, OSError, APIError, KubernetesError, ToolboxError) as exc:
            output["status"] = BLOCKED
            output["errors"].append({"source": args.kind, "reason": str(exc)})
        access: dict[str, Any] = {}
        for name, service in self.context.services.items():
            if isinstance(service, HarborService):
                access[name] = {
                    "origin": service.target.url,
                    "identity": service.credential.username,
                    "credential_source": service.credential.source,
                }
            elif isinstance(service, GitHubService):
                access[name] = {
                    "host": service.host,
                    "identity": service.identity,
                    "repository": service.repository,
                    "repository_id": service.repository_id,
                    "credential_source": service.credential_source,
                }
            elif isinstance(service, KubernetesService):
                access[name] = {
                    "identity": service.identity,
                    "context": service.target.kubernetes.context,
                    "server": service.target.kubernetes.server,
                }
        output["access"] = access
        if executor is not None:
            for callback in executor.callbacks:
                value = callback.result
                if isinstance(value, ToolboxBuildResult):
                    value = asdict(value)
                if isinstance(value, dict) and "verified" in value:
                    output["records"].append(
                        {"callback": type(callback).__name__, **value}
                    )
        output.update(operation=getattr(args, "action", None), plan_digest=self.digest)
        output["summary"] = {
            "record_count": len(output["records"]),
            "error_count": len(output["errors"]),
        }
        if executor is not None:
            output["cleanup"] = {
                "status": PASS if not executor.cleanup_failures else BLOCKED,
                "failures": [asdict(item) for item in executor.cleanup_failures],
            }
        return emit_result(ExecutionResult(None, output), args)


class ConfirmWorkflow(Callback[WorkflowAction, dict[str, Any]]):
    """Resolve live plan inputs before any mutating callback may start."""

    @property
    def complete(self) -> bool:
        """Continue past the plan only for an explicitly confirmed apply.

        :returns: Whether this invocation requested mutation.
        """
        return bool(self.action and self.action.args.apply)

    def run(self) -> dict[str, Any]:
        """Confirm the fingerprint of the current selected operation inputs.

        :returns: The exact fingerprint to supply to a later apply invocation.
        :raises ValueError: If action inputs or explicit confirmation differ.
        :raises APIError: If resolving a mutable base image fails.
        """
        workflow = self.action
        if workflow is None:
            raise ValueError("workflow_action_missing")
        if workflow.spec is not None and workflow.resolved_base is None:
            harbor = cast(HarborService, self.context.services["harbor"])
            digest = harbor.inspect(workflow.spec.base_image)
            image = workflow.spec.base_image.rsplit("@", 1)[0]
            workflow.resolved_base = image + "@" + digest
        source = getattr(workflow.args, "source_image", None)
        if source:
            harbor = cast(HarborService, self.context.services["harbor"])
            digest = harbor.inspect(source)
            workflow.resolved_source = source.rsplit("@", 1)[0] + "@" + digest
        workflow.digest = workflow.fingerprint()
        if workflow.args.apply and workflow.args.confirm_plan != workflow.digest:
            raise ValueError("confirm_plan_mismatch_rerun_plan")
        result = {"plan_digest": workflow.digest}
        if workflow.resolved_source is not None:
            result["image"] = workflow.resolved_source
        return result


class EnsureNamespace(Callback[WorkflowAction, dict[str, Any]]):
    """Create a requested absent namespace without changing an existing one."""

    def collect(self) -> None:
        """Start the list of namespace resources created by this invocation."""
        self._created: list[ResourceSet] = []

    def cleanup(self) -> None:
        """Remove newly created namespaces after dependent cleanup on failure.

        :raises KubernetesError: If owned namespace cleanup cannot complete.
        """
        if self.context.failed:
            for resources in reversed(self._created):
                resources.cleanup_created()

    def run(self) -> dict[str, Any]:
        """Read before creation and record the selected namespace UID.

        :returns: Verified namespace identity and whether it was created.
        :raises ValueError: If namespace inputs are missing.
        :raises KubernetesError: If creation or independent read-back fails.
        """
        workflow = self.action
        if workflow is None or workflow.runner is None:
            raise ValueError("namespace_missing")
        service = cast(KubernetesService, self.context.services["kubernetes"])
        names = {workflow.runner.namespace}
        if workflow.spec is not None:
            names.add(workflow.spec.namespace)
        records = []
        for name in sorted(names):
            observed = service.read("namespace", name, "")
            if observed is None:
                resources = ResourceSet(service, "", workflow.runner.name)
                self._created.append(resources)
                records.append(
                    resources.apply(
                        {
                            "apiVersion": "v1",
                            "kind": "Namespace",
                            "metadata": {"name": name},
                        }
                    )
                )
            else:
                records.append(
                    {
                        "name": name,
                        "uid": observed["metadata"]["uid"],
                        "action": NO_OP,
                    }
                )
        return {"verified": True, "namespaces": records}


class HarborBuild(BuildToolbox):
    """Bind the generic OpenShift build atom to the authenticated registry."""

    def __init__(self, *, workflow: WorkflowAction) -> None:
        """Keep a workflow whose authentication callbacks run before collection.

        :param workflow: Validated inputs and resolved base-image plan.
        """
        super().__init__(action=None)
        self.workflow = workflow
        self._candidate_snapshot: TagSnapshot | None = None

    def collect(self) -> None:
        """Resolve native build inputs from the preceding Harbor callback.

        :raises ValueError: If required plan or authentication inputs are missing.
        :raises ToolboxError: If generic build input validation fails.
        """
        spec = self.workflow.spec
        if spec is None or self.workflow.resolved_base is None:
            raise ValueError("build_plan_missing")
        harbor = cast(HarborService, self.context.services["harbor"])
        prefix = f"{harbor.target.host}/{harbor.target.project}/{spec.image_repository}"
        reference = BUILD_TAG_PREFIX + spec.fingerprint[:FINGERPRINT_TAG_LENGTH]
        self.action = BuildAction(
            spec,
            self.workflow.resolved_base,
            prefix,
            reference,
            harbor.dockerconfig,
            lambda _repository, selected: harbor.digest(
                spec.image_repository, selected
            ),
        )
        super().collect()
        self._candidate_snapshot = TagSnapshot.capture(
            harbor, spec.image_repository, reference
        )

    def cleanup(self) -> None:
        """Clean build helpers and restore the prior candidate tag after failure.

        :raises KubernetesError: If build resource cleanup is incomplete.
        :raises APIError: If candidate tag recovery or read-back fails.
        """
        try:
            super().cleanup()
        finally:
            if self.context.failed and self._candidate_snapshot is not None:
                self._candidate_snapshot.restore()


class DeleteToolbox(Callback[WorkflowAction, dict[str, Any]]):
    """Delete an explicitly selected toolbox build record and registry tags."""

    def run(self) -> dict[str, Any]:
        """Remove selected build and image outputs with independent absence proof.

        :returns: Verified deletion or no-op evidence for the named toolbox.
        :raises ValueError: If specification or bound services are unavailable.
        :raises APIError: If registry delete or absence read-back fails.
        :raises KubernetesError: If build record deletion or read-back fails.
        """
        workflow = self.action
        if workflow is None or workflow.spec is None:
            raise ValueError("toolbox_spec_missing")
        spec = workflow.spec
        harbor = cast(HarborService, self.context.services["harbor"])
        service = cast(KubernetesService, self.context.services["kubernetes"])
        from lib.python.core.toolbox import MANAGED_VALUE

        resources = ResourceSet(service, spec.namespace, MANAGED_VALUE)
        changed = resources.delete("BuildConfig", spec.app)
        for reference in (
            spec.image_tag,
            BUILD_TAG_PREFIX + spec.fingerprint[:FINGERPRINT_TAG_LENGTH],
        ):
            changed = harbor.delete_tag(spec.image_repository, reference) or changed
        return {
            "verified": True,
            "action": APPLIED if changed else NO_OP,
            "repository": spec.image_repository,
            "tag": spec.image_tag,
            "namespace": spec.namespace,
            "name": spec.app,
        }
