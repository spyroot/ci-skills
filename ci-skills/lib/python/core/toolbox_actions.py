"""Plan and compose toolbox, registry, and runner delivery operations.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, MutableMapping, MutableSequence, Sequence
from dataclasses import asdict, dataclass, field
from functools import partial
from pathlib import Path
from typing import Final, Optional

from lib.python.access.credentials import bind_sources
from lib.python.core.abstract.callback import Callback
from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.abstract.skill_exector import WorkflowExecutor
from lib.python.core.catalog import (
    COMMAND_BY_KIND,
    COMMANDS,
    describe,
    missing_required_options,
)
from lib.python.core.cli import parser
from lib.python.core.cli_api import APIError
from lib.python.core.delivery_state import DeliveryState
from lib.python.core.gitlab.gitlab_runner import RunnerAction, published_image
from lib.python.core.harbor.registry_mirror import TagSnapshot
from lib.python.core.json_types import (
    JSONObject,
    JSONValue,
    is_json_value,
    json_object,
)
from lib.python.core.kubernetes.kubernetes import KubernetesError
from lib.python.core.project_binding import resolve_target
from lib.python.core.report import emit_result, plan_digest, report
from lib.python.core.resources import ResourceSet
from lib.python.core.schema import SCHEMA_DIRECTORY, TOOLBOX_SCHEMA
from lib.python.core.target import Target
from lib.python.core.toolbox import (
    MANAGED_VALUE,
    BuildAction,
    BuildToolbox,
    ToolboxError,
    ToolboxSpec,
)
from lib.python.sys.status import APPLIED, BLOCKED, DRY_RUN, NO_OP, PASS, PLANNED

DEFAULT_OPERATION_TIMEOUT: Final = 1800
MUTATION_OPERATIONS: Final = ("create", "update", "delete")
BUILD_TAG_PREFIX: Final = "ci-skills-build-"
FINGERPRINT_TAG_LENGTH: Final = 24


def workflow_parser(kind: str) -> argparse.ArgumentParser:
    """Build a workflow parser from the existing command catalogue.

    :param kind: Registered workflow report kind.
    :returns: Parser with catalogue-owned options and shared output/target handling.
    :raises KeyError: If the requested workflow is absent from the catalogue.
    """  # noqa: DOC502 - Required catalogue entries are accessed by key.
    entry = COMMANDS[COMMAND_BY_KIND[kind]]
    result = parser(entry["purpose"], kind=kind)
    result.set_defaults(kind=kind)
    if "--spec" in entry["options"]:
        result.epilog = (
            result.epilog or ""
        ) + f" Toolbox input schema: {SCHEMA_DIRECTORY / TOOLBOX_SCHEMA}"
    result.add_argument("action", nargs="?", choices=tuple(entry["subcommands"]))
    for option, help_text in entry["options"].items():
        if option == "--apply":
            result.add_argument(option, help=help_text, action="store_true")
        elif option == "--label":
            result.add_argument(option, help=help_text, action="append", default=[])
        elif option == "--timeout":
            result.add_argument(
                option, help=help_text, type=int, default=DEFAULT_OPERATION_TIMEOUT
            )
        else:
            result.add_argument(option, help=help_text)
    return result


@dataclass
class WorkflowAction(SkillAction):
    """Retain explicit workflow inputs and the planning state they produce.

    :param args: Command arguments parsed by the shared catalogue-owned parser.
    :param spec: Selected toolbox specification, absent for direct push/pull operations.
    :param resolved_base: Immutable build base, absent when no build has been planned.
    :param resolved_source: Immutable direct push source, absent for other operations.
    :param digest: Prepared plan fingerprint, absent until planning succeeds.
    :param context: Typed state shared by the declared ordered callbacks.
    :ivar target: Runtime target assigned by successful preparation.
    :ivar runner: Workload inputs assigned for every operation except direct push.

    .. attribute :: args
        :type: argparse.Namespace

        Command arguments parsed by the shared catalogue-owned parser.

    .. attribute :: target
        :type: Target

        Runtime target assigned by successful preparation.

    .. attribute :: runner
        :type: RunnerAction

        Workload inputs assigned for every operation except direct push.

    .. attribute :: spec
        :type: Optional[ToolboxSpec]

        Selected toolbox specification, absent for direct push/pull operations.

    .. attribute :: resolved_base
        :type: Optional[str]

        Immutable build base, absent when no build has been planned.

    .. attribute :: resolved_source
        :type: Optional[str]

        Immutable direct push source, absent for other operations.

    .. attribute :: digest
        :type: Optional[str]

        Prepared plan fingerprint, absent until planning succeeds.

    .. attribute :: context
        :type: DeliveryState

        Typed state shared by the declared ordered callbacks.
    """

    args: argparse.Namespace
    target: Target = field(init=False)
    runner: RunnerAction = field(init=False)
    spec: Optional[ToolboxSpec] = None
    resolved_base: Optional[str] = None
    resolved_source: Optional[str] = None
    digest: Optional[str] = None
    context: DeliveryState = field(default_factory=DeliveryState)

    def _validate(self) -> None:
        """Reject invalid command modes and missing catalogue-owned arguments.

        :raises ValueError: If required inputs, timeout, or plan/apply flags conflict.
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
        missing = missing_required_options(COMMAND_BY_KIND[args.kind], args)
        if missing:
            raise ValueError("missing_options:" + ",".join(missing))

    def _workload(self) -> None:
        """Select workload identity only for operations that create or read workloads.

        :raises ValueError: If the workload or provider registration inputs are missing.
        """
        args = self.args
        if args.kind == "harbor_push":
            return
        namespace = getattr(args, "namespace", "") or (
            self.spec.namespace if self.spec else ""
        )
        name = getattr(args, "name", "") or (self.spec.app if self.spec else "")
        if not namespace or not name:
            raise ValueError("namespace_and_name_required")
        source_root = getattr(args, "runner_root", "") or ""
        labels = tuple(getattr(args, "label", []))
        if (
            args.kind == "create_selfhosted_runner"
            and args.action != "delete"
            and (not source_root or not labels)
        ):
            raise ValueError("runner_root_and_labels_required")
        self.runner = RunnerAction(namespace, name, source_root, labels, args.timeout)

    def prepare(self) -> None:
        """Validate inputs and bind the shared runtime target before composition.

        :raises ValueError: If command modes or required operation inputs are invalid.
        :raises TargetError: If target or credential source binding is invalid.
        :raises SpecificationError: If the selected toolbox specification is invalid.
        :raises OSError: If a configured specification or credential cannot be read.
        """  # noqa: DOC503 - Specification and credential binding propagate failures.
        self._validate()
        args = self.args
        self.context.args = args
        selected_spec = getattr(args, "spec", "")
        if selected_spec:
            self.spec = ToolboxSpec.from_path(Path(selected_spec).expanduser())
        if args.action in ("create", "update") and self.spec is None:
            raise ValueError("spec_required")
        surfaces = tuple(COMMANDS[COMMAND_BY_KIND[args.kind]]["requires"])
        if args.kind == "create_selfhosted_runner" and args.action == "delete":
            surfaces = ("github", "kubernetes")
        self.target = resolve_target(
            args.target, args.binding, dry_run=args.dry_run, required_surfaces=surfaces
        )
        if not args.dry_run:
            self.target = bind_sources(self.target, revision=args.revision)
        self._workload()
        if self.spec is not None and "@sha256:" in self.spec.base_image:
            self.resolved_base = self.spec.base_image

    def fingerprint(self) -> str:
        """Bind operation inputs to selected endpoints and resolved image identities.

        :returns: Canonical nonsecret plan fingerprint using the established document shape.
        :raises AttributeError: If preparation did not assign the runtime target.
        :raises TypeError: If an operation input cannot be encoded as JSON.
        """  # noqa: DOC502 - State access and plan serialization can fail.
        github = asdict(self.target.github) if self.target.github else None
        if github and github["token_file"] is not None:
            github["token_file"] = str(github["token_file"])
        return plan_digest(
            {
                "kind": self.args.kind,
                "operation": self.args.action,
                "skill": self.target.skill,
                "tested_revision": self.target.tested_revision,
                "target_file": str(self.target.source_file),
                "target_source": self.target.source_kind,
                "github": github,
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
                "runner": asdict(self.runner)
                if self.args.kind != "harbor_push"
                else None,
            }
        )

    @property
    def repository(self) -> str:
        """Select the exact project-relative registry repository for this operation.

        :returns: Specification repository, explicit repository, or pinned pull-image repository.
        :raises ValueError: If the image is outside the configured Harbor target.
        """
        if self.spec is not None:
            return self.spec.image_repository
        selected = getattr(self.args, "repository", "")
        if selected:
            return selected
        image = getattr(self.args, "image", "")
        if self.target.harbor is None:
            raise ValueError("harbor_target_missing")
        prefix = f"{self.target.harbor.host}/{self.target.harbor.project}/"
        if not image.startswith(prefix) or "@sha256:" not in image:
            raise ValueError("harbor_image_target_mismatch")
        return image[len(prefix) :].rsplit("@", 1)[0]

    def _records(self) -> Sequence[JSONObject]:
        """Select JSON evidence from the generic callback result history.

        :returns: Provider records carrying ``verified``, each identified by its
            producing callback; unrelated framework values remain in shared state.
        :raises TypeError: If a report-bound result contains values outside the
            finite JSON data model.
        :raises RecursionError: If report-bound evidence contains a container
            cycle or exceeds the JSON validator's nesting depth.
        """  # noqa: DOC503 - Recursive JSON validation propagates excessive nesting.
        records: MutableSequence[JSONObject] = []
        for callback, value in self.context.results.items():
            if "verified" not in value:
                continue
            if not is_json_value(value):
                raise TypeError("workflow_record_requires_json")
            records.append({"callback": type(callback).__name__, **json_object(value)})
        return records

    def _execute(
        self,
        factory: Callable[[], WorkflowExecutor[DeliveryState]],
        output: MutableMapping[str, JSONValue],
    ) -> bool:
        """Prepare and run the workflow selected by the caller's known mode.

        :param factory: Constructs the already ordered callback collection after preparation.
        :param output: Report whose status and target identity this method updates.
        :returns: Whether a live executor was created rather than an offline plan.
        :raises ValueError: If inputs or operation-specific verification are invalid.
        :raises OSError: If configured files or native commands cannot be accessed.
        :raises APIError: If registry or GitHub operations fail.
        :raises KubernetesError: If Kubernetes operations or cleanup fail.
        :raises ToolboxError: If toolbox validation, build, or read-back fails.
        """  # noqa: DOC502 - Preparation and workflow callbacks propagate failures.
        self.prepare()
        output["target"] = str(self.target.source_file)
        if self.target.skill is not None:
            output["skill"] = self.target.skill
            output["tested_revision"] = self.target.tested_revision
        if self.args.dry_run:
            self.digest = self.fingerprint()
            output["status"] = DRY_RUN
            return False
        executor = factory()
        executor.run()
        output["status"] = PASS if self.args.apply else PLANNED
        return True

    def execute(self, factory: Callable[[], WorkflowExecutor[DeliveryState]]) -> int:
        """Run the declared workflow and emit its existing versioned report envelope.

        :param factory: Thin adapter constructing ordered callbacks after preparation.
        :returns: Canonical report exit code after output and requested receipt writing.
        :raises OSError: If final report output or receipt writing fails.
        :raises ValueError: If the final report cannot be rendered or serialized.
        :raises TypeError: If report-bound callback evidence is not finite JSON.
        :raises RecursionError: If report-bound evidence contains cyclic or
            excessively nested containers.
        """  # noqa: DOC502 - Final report and receipt writing can fail.
        args = self.args
        if args.describe:
            return emit_result(describe(COMMAND_BY_KIND[args.kind]), args)
        output = report(args.kind, "", {}, [], [])
        errors: MutableSequence[JSONObject] = []
        executed = False
        if args.log_level == "debug" and args.log_file:
            output["trace_artifact"] = str(args.log_file)
        try:
            executed = self._execute(factory, output)
        except (ValueError, OSError, APIError, KubernetesError, ToolboxError) as exc:
            output["status"] = BLOCKED
            errors.append({"source": args.kind, "reason": str(exc)})
        records = self._records()
        output.update(
            access=self.context.access_evidence,
            records=records,
            errors=errors,
            operation=getattr(args, "action", None),
            plan_digest=self.digest,
        )
        output["summary"] = {"record_count": len(records), "error_count": len(errors)}
        if executed or self.context.results:
            output["cleanup"] = {
                "status": BLOCKED if self.context.cleanup_failures else PASS,
                "failures": list(self.context.cleanup_failures),
            }
        return emit_result(output, args)


class ConfirmWorkflow(Callback[DeliveryState, WorkflowAction]):
    """Resolve live plan inputs before any composed mutation callback starts."""

    def run(self) -> JSONObject:
        """Resolve immutable image inputs and validate any requested plan confirmation.

        :returns: Exact plan fingerprint and resolved source image when applicable.
        :raises ValueError: If confirmation differs or a pull image is not immutable.
        :raises APIError: If native registry image identity cannot be read.
        """  # noqa: DOC503 - Image validation and native registry reads can fail.
        workflow = self.action
        if workflow.spec is not None and workflow.resolved_base is None:
            digest = self.state.harbor.inspect(workflow.spec.base_image)
            workflow.resolved_base = (
                workflow.spec.base_image.rsplit("@", 1)[0] + "@" + digest
            )
        source = getattr(workflow.args, "source_image", "")
        if source:
            digest = self.state.harbor.inspect(source)
            workflow.resolved_source = source.rsplit("@", 1)[0] + "@" + digest
            self.state.source_image = workflow.resolved_source
        image = getattr(workflow.args, "image", "")
        if image:
            self.state.published_image = published_image(image)
        workflow.digest = workflow.fingerprint()
        if workflow.args.apply and workflow.args.confirm_plan != workflow.digest:
            raise ValueError("confirm_plan_mismatch_rerun_plan")
        result: MutableMapping[str, JSONValue] = {"plan_digest": workflow.digest}
        if workflow.resolved_source is not None:
            result["image"] = workflow.resolved_source
        return result


class EnsureNamespace(Callback[DeliveryState, WorkflowAction]):
    """Create absent selected namespaces while preserving existing namespaces."""

    def __init__(self, action: WorkflowAction):
        """Retain the required workflow and own created namespace tracking.

        :param action: Prepared workload and optional build namespace selection.
        """
        super().__init__(action)
        self._created: MutableSequence[ResourceSet] = []

    def cleanup(self) -> JSONObject:
        """Remove only namespaces created here after dependent failure cleanup.

        :returns: Empty evidence after owned namespace cleanup verification.
        :raises KubernetesError: If a newly created namespace cannot be removed safely.
        """  # noqa: DOC502 - ResourceSet applies and verifies namespace lifecycle.
        if self.state.failed:
            for resources in reversed(self._created):
                resources.cleanup_created()
        return {}

    def run(self) -> JSONObject:
        """Read before creation and retain each selected namespace identity.

        :returns: Verified namespace records with created/no-op status and native UID.
        :raises KubernetesError: If creation or independent namespace read-back fails.
        """  # noqa: DOC502 - ResourceSet applies and verifies namespace lifecycle.
        workflow = self.action
        names = {workflow.runner.namespace}
        if workflow.spec is not None:
            names.add(workflow.spec.namespace)
        records = []
        for name in sorted(names):
            observed = self.state.kubernetes.read("namespace", name, "")
            if observed is None:
                resources = ResourceSet(self.state.kubernetes, "", workflow.runner.name)
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
                    {"name": name, "uid": observed["metadata"]["uid"], "action": NO_OP}
                )
        return {"verified": True, "namespaces": records}


class HarborBuild(Callback[DeliveryState, WorkflowAction]):
    """Construct the generic build action from preceding plan and registry state."""

    def __init__(self, workflow: WorkflowAction):
        """Retain required workflow inputs and initialize child resource ownership.

        :param workflow: Prepared inputs whose authentication and plan callbacks precede this one.
        """
        super().__init__(workflow)
        self._builder: Optional[BuildToolbox] = None
        self._candidate_snapshot: Optional[TagSnapshot] = None

    def collect(self) -> JSONObject:
        """Construct one concrete build action after its required producers finish.

        :returns: Empty evidence after generic build input validation.
        :raises ValueError: If the confirmed build specification or base is missing.
        :raises ToolboxError: If generic immutable-image or credential inputs are invalid.
        :raises APIError: If the prior candidate tag cannot be read for recovery.
        """  # noqa: DOC503 - Build and registry helpers propagate lifecycle failures.
        workflow, harbor = self.action, self.state.harbor
        spec = workflow.spec
        if spec is None or workflow.resolved_base is None:
            raise ValueError("build_plan_missing")
        prefix = f"{harbor.target.host}/{harbor.target.project}/{spec.image_repository}"
        reference = BUILD_TAG_PREFIX + spec.fingerprint[:FINGERPRINT_TAG_LENGTH]
        build = BuildAction(
            spec,
            workflow.resolved_base,
            prefix,
            reference,
            harbor.dockerconfig,
            partial(harbor.digest, spec.image_repository),
        )
        builder = BuildToolbox(build)
        builder.bind(self.state)
        self._builder = builder
        builder.collect()
        self._candidate_snapshot = TagSnapshot.capture(
            harbor, spec.image_repository, reference
        )
        return {}

    def run(self) -> JSONObject:
        """Run the constructed generic build and retain its typed state output.

        :returns: Safe candidate identity and independent build/registry evidence.
        :raises ValueError: If lifecycle collection did not construct the build action.
        :raises ToolboxError: If build execution or exact output verification fails.
        :raises KubernetesError: If native operations or read-back fail.
        :raises APIError: If independent registry read-back fails.
        :raises OSError: If temporary build inputs cannot be prepared.
        """  # noqa: DOC503 - Build and registry helpers propagate lifecycle failures.
        if self._builder is None:
            raise ValueError("build_collection_required")
        return self._builder.run()

    def cleanup(self) -> JSONObject:
        """Clean child resources and restore the captured candidate after failure.

        :returns: Empty report evidence after owned cleanup and recovery verification.
        :raises KubernetesError: If build resource cleanup fails.
        :raises APIError: If prior candidate state cannot be restored and read back.
        """  # noqa: DOC502 - Build cleanup and registry restoration can fail.
        try:
            if self._builder is not None:
                self._builder.cleanup()
        finally:
            if self.state.failed and self._candidate_snapshot is not None:
                self._candidate_snapshot.restore()
        return {}


class DeleteToolbox(Callback[DeliveryState, WorkflowAction]):
    """Delete explicitly selected owned build configuration and registry tags."""

    def run(self) -> JSONObject:
        """Delete named build and image outputs with independent absence proof.

        :returns: Applied/no-op deletion evidence for the selected toolbox.
        :raises ValueError: If no toolbox specification was selected.
        :raises APIError: If tag deletion or independent absence read-back fails.
        :raises KubernetesError: If owned BuildConfig deletion or read-back fails.
        """  # noqa: DOC503 - Registry and Kubernetes deletion helpers can fail.
        spec = self.action.spec
        if spec is None:
            raise ValueError("toolbox_spec_missing")
        resources = ResourceSet(self.state.kubernetes, spec.namespace, MANAGED_VALUE)
        changed = resources.delete("BuildConfig", spec.app)
        references = (
            spec.image_tag,
            BUILD_TAG_PREFIX + spec.fingerprint[:FINGERPRINT_TAG_LENGTH],
        )
        for reference in references:
            changed = (
                self.state.harbor.delete_tag(spec.image_repository, reference)
                or changed
            )
        return {
            "action": APPLIED if changed else NO_OP,
            "verified": True,
            "namespace": spec.namespace,
            "name": spec.app,
        }
