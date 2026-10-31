"""Command and result regressions for the CI13 image-build chain.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from conftest import SKILL_ROOT, import_script_module
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from test_image_build_chain import (
    ABSTRACT_CALLBACKS,
    CALLBACKS,
    CALLER_NAMESPACE,
    CALLER_NAMESPACE_UID,
    CHAIN,
    EXECUTOR,
    NODE,
    PODMAN,
    READINESS,
    TOOLBOX_IMAGE,
    ChainCluster,
    JournalCallback,
    _args,
    _lifecycle_executor,
    _prepared_action,
    _target,
)

COMMAND = import_script_module("lib.python.core.kubernetes.image_build_chain.command")
MODEL = import_script_module("lib.python.core.kubernetes.image_build_chain.model")

if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject
    from lib.python.core.kubernetes.image_build_chain import ImageBuildChainState


def _validator() -> Draft202012Validator:
    """Use the shipped schema and its local provenance and scope references.

    :returns: Result validator without any network resolution.
    """
    root = SKILL_ROOT / "schemas" / "results"
    schema = json.loads((root / "image-build-readiness.schema.json").read_text())
    tag_schema = json.loads((root / "gitlab-runner-tag.schema.json").read_text())
    scope_schema = json.loads((root / "runner-scope.schema.json").read_text())
    registry = Registry().with_resources(
        (
            (tag_schema["$id"], Resource.from_contents(tag_schema)),
            (scope_schema["$id"], Resource.from_contents(scope_schema)),
        )
    )
    return Draft202012Validator(schema, registry=registry)


def _apply_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    outcome: str = "success",
    cleanup_failure: bool = False,
) -> tuple[int, JSONObject]:
    """Execute the real command, verifier, cleanup and JSON emitter offline.

    :param tmp_path: Isolated private token directory.
    :param monkeypatch: Fixture replacing provider and target configuration boundaries.
    :param capsys: Capture the actual final JSON emitted by the command.
    :param outcome: Success, provider mismatch, or timeout selected by the fixture.
    :param cleanup_failure: Whether provider manager deletion fails.
    :returns: Command exit code and its decoded final result.
    """
    action, executor, _stages, cleanup, kubernetes = _lifecycle_executor(
        tmp_path, monkeypatch, outcome, cleanup_failure
    )
    command = COMMAND.ImageBuildChain(action.args)
    command.action = action
    monkeypatch.setattr(action, "prepare", lambda mode: mode)
    monkeypatch.setattr(COMMAND, "workflow", lambda selected: executor)
    status = command.execute()
    output = json.loads(capsys.readouterr().out)
    assert cleanup == ([] if outcome == "success" else ["manager", "scc", "runner"])
    assert kubernetes.read("Namespace", CALLER_NAMESPACE, "") is not None
    assert not action.state.token_path.exists()
    assert not action.state.token_directory.exists()
    _validator().validate(output)
    return status, output


@pytest.mark.parametrize(
    "outcome", ("success", "failed-manager", "timeout", "cleanup-failure")
)
def test_chain_emits_schema_valid_runner_delivery_or_classified_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    outcome: str,
) -> None:
    """Report durable runner delivery or a classified failed attempt.

    :param tmp_path: Isolated token directory.
    :param monkeypatch: Fixture replacing external provider operations.
    :param capsys: Captured command JSON.
    :param outcome: Normal completion or one classified execution/cleanup failure.
    :returns: None after checking status, schema, redaction and cleanup side effects.
    """
    status, output = _apply_report(
        tmp_path,
        monkeypatch,
        capsys,
        outcome="failed-manager" if outcome == "cleanup-failure" else outcome,
        cleanup_failure=outcome == "cleanup-failure",
    )
    assert status == (0 if outcome == "success" else 2)
    assert output["status"] == ("PASS" if outcome == "success" else "BLOCKED")
    assert output["cleanup"]["status"] == (
        "BLOCKED" if outcome == "cleanup-failure" else "PASS"
    )
    if outcome == "success":
        proof = next(
            record
            for record in output["records"]
            if record["callback"] == "DeployManagerRunner"
        )
        assert proof["verified"] is True
        assert proof["namespace"] == CALLER_NAMESPACE
        assert proof["image"] == TOOLBOX_IMAGE
        assert proof["pods"]
        assert proof["resources"]
        assert "pipeline_id" not in proof
        assert "job_id" not in proof
        without_cleanup = deepcopy(output)
        without_cleanup.pop("cleanup", None)
        _validator().validate(without_cleanup)
    if outcome != "success":
        assert output["errors"]
    if outcome in ("timeout", "cleanup-failure"):
        assert "private-unit-value" not in json.dumps(output)
        assert "[REDACTED]" in json.dumps(output)


@pytest.mark.parametrize(
    "defect",
    (
        "minimal",
        "missing-proof",
        "missing-plan",
        "missing-digest",
        "unverified-deployment",
        "missing-image",
        "missing-namespace",
    ),
)
def test_chain_schema_rejects_pass_without_complete_runner_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    defect: str,
) -> None:
    """Reject corrupted PASS claims derived from a complete real command report.

    :param tmp_path: Isolated token directory.
    :param monkeypatch: Fixture replacing external provider operations.
    :param capsys: Captured command JSON.
    :param defect: Required identity or runner delivery evidence removed/corrupted.
    :returns: None; every incomplete PASS report fails the shipped schema.
    """
    _status, complete = _apply_report(tmp_path, monkeypatch, capsys)
    broken = deepcopy(complete)
    if defect == "minimal":
        broken = {
            key: complete[key]
            for key in (
                "schema_version",
                "kind",
                "operation",
                "status",
                "captured_at",
                "errors",
                "summary",
            )
        }
    elif defect == "missing-proof":
        broken["records"] = []
    elif defect == "missing-plan":
        del broken["plan"]
    elif defect == "missing-digest":
        del broken["plan_digest"]
    elif defect == "unverified-deployment":
        for record in broken["records"]:
            if record["callback"] == "DeployManagerRunner":
                record["verified"] = False
    else:
        for record in broken["records"]:
            if record["callback"] == "DeployManagerRunner":
                if defect == "missing-image":
                    del record["image"]
                else:
                    del record["namespace"]
    assert list(_validator().iter_errors(broken))


@pytest.mark.parametrize(
    "explicit", (False, True), ids=("default-live-plan", "explicit-dry-run")
)
def test_chain_default_plans_live_and_explicit_dry_run_stays_offline(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    explicit: bool,
) -> None:
    """Plan live by default and keep explicit dry-run outside provider setup.

    :param monkeypatch: Fixture replacing configuration and forbidden live boundaries.
    :param capsys: Actual JSON emitter capture.
    :param explicit: Whether the caller supplies the explicit dry-run flag.
    :returns: None after checking mode-specific planning and absence of mutation.
    """
    args = _args()
    args.live_plan = False
    args.dry_run = explicit
    template = _prepared_action()
    resolutions = []
    bindings = []
    reads = []

    def resolve(_target, _binding, *, dry_run, required_surfaces):
        resolutions.append((dry_run, required_surfaces))
        return _target_fixture

    _target_fixture = _target()
    monkeypatch.setattr(MODEL, "resolve_target", resolve)

    def bind(target, **_kwargs):
        assert not explicit, "dry-run bound credentials"
        bindings.append(target)
        return target

    monkeypatch.setattr(MODEL, "bind_sources", bind)
    monkeypatch.setattr(
        MODEL,
        "WorkflowAction",
        lambda *_a, **_k: SimpleNamespace(
            spec=template.toolbox.spec,
            runner=template.toolbox.runner,
            resolved_base=None,
            prepare=lambda: None,
        ),
    )
    monkeypatch.setattr(
        MODEL,
        "prepare_start_plan",
        lambda *_a, **_k: pytest.fail("runner delivery prepared a consumer pipeline"),
        raising=False,
    )

    def preflight(state: ImageBuildChainState) -> None:
        """Return the worker selected at the mocked provider-read boundary.

        :param state: Shared chain state receiving the observed worker identity.
        """
        state.selected_node = NODE
        state.kubernetes = ChainCluster()

    def workflow(action):
        assert not explicit, "dry-run entered provider callbacks"
        assert action.mode == PODMAN.ProbeMode.LIVE_PLAN
        return EXECUTOR.WorkflowExecutor(
            context=action.state,
            callbacks=ABSTRACT_CALLBACKS.Callbacks(
                [
                    JournalCallback(action, reads, "live-preflight", preflight),
                    CALLBACKS.ResolveChainPlan(action),
                ]
            ),
        )

    monkeypatch.setattr(COMMAND, "workflow", workflow)
    command = COMMAND.ImageBuildChain(args)
    assert command.execute() == 0
    output = json.loads(capsys.readouterr().out)
    _validator().validate(output)
    assert output["status"] == ("DRY_RUN" if explicit else "PLANNED")
    assert output["confirmable"] is not explicit
    assert output["errors"] == []
    assert "cleanup" not in output
    assert resolutions == [(explicit, ("gitlab", "kubernetes", "harbor"))]
    assert bindings == ([] if explicit else [_target_fixture])
    assert reads == ([] if explicit else ["live-preflight"])
    if explicit:
        assert output["records"] == []
    else:
        assert output["selected_node"] == NODE
        assert [record["callback"] for record in output["records"]] == [
            "JournalCallback",
            "ResolveChainPlan",
            "RunnerScopes",
        ]
        assert output["plan_digest"] == output["records"][1]["plan_digest"]
        scope_record = output["records"][-1]
        assert scope_record["verified"] is True
        assert [
            (
                scope["purpose"],
                scope["namespace"],
                scope["uid"],
                scope["ownership"],
                scope["state"],
                scope["retention"],
            )
            for scope in scope_record["scopes"]
        ] == [
            (
                purpose,
                CALLER_NAMESPACE,
                CALLER_NAMESPACE_UID,
                "borrowed",
                "present",
                "preserved",
            )
            for purpose in ("manager", "build")
        ]
    assert "run_id" not in output
    assert not hasattr(command.action.state, "run_scope")
    assert not hasattr(command.action.state, "created_runner_id")
    assert command.action.state.chain_cleanup == {}


def test_chain_describe_never_constructs_execution_or_reads_target(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Resolve chain discovery before any target or provider configuration.

    :param monkeypatch: Fixture making execution setup fail immediately if entered.
    :param capsys: Actual discovery JSON capture.
    :returns: None; description succeeds without selecting a target or chain inputs.
    """
    monkeypatch.setattr(
        CHAIN,
        "ImageBuildChain",
        lambda *_a: pytest.fail("discovery constructed chain"),
    )
    monkeypatch.setattr(
        MODEL, "resolve_target", lambda *_a, **_k: pytest.fail("discovery read target")
    )
    assert READINESS.main(["chain", "--describe", "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["command"] == "image_build_readiness.py"


@pytest.mark.parametrize(
    ("apply", "dry_run", "live_plan", "confirm", "expected"),
    (
        (False, False, False, False, "LIVE_PLAN"),
        (False, True, False, False, "DRY_RUN"),
        (False, False, True, False, "LIVE_PLAN"),
        (True, False, False, True, "APPLY"),
    ),
)
def test_shared_probe_modes_preserve_existing_probe_and_chain_policy(
    apply: bool,
    dry_run: bool,
    live_plan: bool,
    confirm: bool,
    expected: str,
) -> None:
    """Map valid modes through the one policy shared by probe and chain.

    :param apply: Mutating mode selection.
    :param dry_run: Explicit offline mode selection.
    :param live_plan: Read-only provider plan selection.
    :param confirm: Whether a confirmation digest is supplied.
    :param expected: Closed ProbeMode member expected from policy validation.
    :returns: None after checking the mode returned by the public shared resolver.
    """
    args = _args()
    args.apply, args.dry_run, args.live_plan = apply, dry_run, live_plan
    args.confirm_plan = "a" * 64 if confirm else None
    assert PODMAN.resolve_probe_mode(args) == PODMAN.ProbeMode[expected]


@pytest.mark.parametrize("command_type", ("probe", "chain"))
@pytest.mark.parametrize(
    "invalid", ("conflict", "confirmation-without-apply", "offline-receipt")
)
def test_both_commands_reject_invalid_mode_before_target_setup(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command_type: str,
    invalid: str,
) -> None:
    """Enforce shared flag conflicts, confirmation placement and receipt policy once.

    :param monkeypatch: Fixture rejecting any execution setup before valid controls.
    :param capsys: Captured classified failure report.
    :param command_type: Existing probe adapter or new chain command.
    :param invalid: Specific mode precondition violated by the caller.
    :returns: None; both adapters emit BLOCKED without target setup.
    """
    args = _args()
    args.apply = invalid == "conflict"
    args.live_plan = False
    args.dry_run = invalid in ("conflict", "offline-receipt")
    args.confirm_plan = (
        "a" * 64 if invalid in ("conflict", "confirmation-without-apply") else None
    )
    args.receipt_out = "forbidden.json" if invalid == "offline-receipt" else None
    command = (
        COMMAND.ImageBuildChain(args)
        if command_type == "chain"
        else READINESS.ImageBuildReadiness(args)
    )
    if command_type == "chain":
        monkeypatch.setattr(
            command.action,
            "prepare",
            lambda *_a: pytest.fail("invalid mode prepared target"),
        )
    else:
        monkeypatch.setattr(
            command, "_prepare", lambda *_a: pytest.fail("invalid mode prepared target")
        )
    monkeypatch.setattr(
        COMMAND, "emit_result", lambda data, _args: print(json.dumps(data)) or 2
    )
    monkeypatch.setattr(
        READINESS, "emit_result", lambda data, _args: print(json.dumps(data)) or 2
    )
    assert command.execute() == 2
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "BLOCKED"
    assert output["errors"]
