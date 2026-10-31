"""Compose confirmed mirror operations through the shared command lifecycle.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import hashlib
import time
from collections.abc import MutableMapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Optional

from lib.python.access.gitlab import check_gitlab_operation_access
from lib.python.access.gitlab_credentials import bind_gitlab_session
from lib.python.core.abstract.callback import Callback, LocalCallback
from lib.python.core.abstract.callbacks import AbstractCallbacks, Callbacks
from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.abstract.skill_plugin_type import SkillPluginType
from lib.python.core.action_modes import (
    GITLAB_MODE_POLICY,
    MAX_ACTION_TIMEOUT,
    ActionMode,
    validate_action_mode,
)
from lib.python.core.catalog import COMMANDS, UNIVERSAL_OPTIONS, describe
from lib.python.core.cli import _failure, parser
from lib.python.core.cli_api import APIError, RequestDeadline
from lib.python.core.delivery_state import DeliveryState
from lib.python.core.executor import WorkflowExecutor
from lib.python.core.git.mirroring import MirrorField
from lib.python.core.git.refs import RefSnapshot
from lib.python.core.git.remote import GitRemoteReader
from lib.python.core.github.github import GitHubIdentityAuth
from lib.python.core.github.repository import GitHubRepositoryService
from lib.python.core.gitlab.gitlab_api import GlabAPIClient
from lib.python.core.gitlab.gitlab_session import BoundGitLabSession, GitLabService
from lib.python.core.gitlab.mirror.constants import (
    CLONE_URL_TEMPLATE,
    COMMAND,
    DEFAULT_POLICY,
    KIND,
    OPERATION,
    Failure,
    Field,
    Outcome,
)
from lib.python.core.gitlab.mirror.provider import GitLabPushMirrorService
from lib.python.core.gitlab.mirror.workflow import (
    MirrorEngine,
    MirrorIntent,
    MirrorPlan,
)
from lib.python.core.json_types import (
    JSONObject,
    JSONValue,
    copy_json,
    json_integer,
    json_object,
    json_objects,
    json_string,
)
from lib.python.core.project_binding import resolve_target
from lib.python.core.report import emit_result, plan_digest, report
from lib.python.core.runtime import sanitize
from lib.python.core.target import (
    GitLabOperationTarget,
    Target,
    TargetError,
    select_gitlab_reference,
)
from lib.python.sys.status import BLOCKED, DRY_RUN, PARTIAL, PASS, PLANNED

APPLY_OPTION: Final = "--apply"
CREATE_OPTION: Final = "--create-if-absent"
LIVE_PLAN_OPTION: Final = "--live-plan"
TIMEOUT_OPTION: Final = "--timeout"
BOOLEAN_OPTIONS: Final = frozenset((APPLY_OPTION, CREATE_OPTION, LIVE_PLAN_OPTION))
SURFACES: Final = (SkillPluginType.GITLAB.value, SkillPluginType.GITHUB.value)
EMPTY: Final = ""
ERROR_LIMIT: Final = 240
NO_VERIFIED_REFS: Final = 0
ACTION_REQUIRED: Final = "sync_required"
PROJECT_REQUIRED: Final = "mirror_source_project_required"
TARGET_REQUIRED: Final = "mirror_target_unresolved"
ACCESS_BLOCKED: Final = "mirror_source_access_blocked"
PLAN_REQUIRED: Final = "mirror_live_plan_unavailable"
LIVE_MODE_REQUIRED: Final = "mirror_workflow_requires_live_mode"
ARGUMENTS_SOURCE: Final = "arguments"
ARGUMENT_ACTION: Final = "action"
ARGUMENT_OPTIONAL: Final = "?"
STORE_TRUE_ACTION: Final = "store_true"
RECOVERY: Final = (
    "Inspect the selected source mirror and destination before retrying; "
    "preserve created resources and obtain a new --live-plan confirmation."
)
SELECT_TARGET: Final = (
    "Select the source project with --project or gitlab.project, and configure "
    "the GitHub authority and destination through the existing target protocol."
)


class EnvelopeField(StrEnum):
    """Name shared report fields not owned by the mirror domain model.

    .. attribute :: TARGET

        Native token ``target``.

    .. attribute :: SUMMARY

        Native token ``summary``.

    .. attribute :: RECORD_COUNT

        Native token ``record_count``.

    .. attribute :: ERROR_COUNT

        Native token ``error_count``.

    .. attribute :: SAFE_NEXT_STEP

        Native token ``safe_next_step``.

    .. attribute :: EXECUTION_HOST

        Native token ``execution_host``.
    """

    TARGET = "target"
    SUMMARY = "summary"
    RECORD_COUNT = "record_count"
    ERROR_COUNT = "error_count"
    SAFE_NEXT_STEP = "safe_next_step"
    EXECUTION_HOST = "execution_host"


class CatalogField(StrEnum):
    """Name the existing catalog fields consumed by parser composition.

    .. attribute :: PURPOSE

        Native token ``purpose``.

    .. attribute :: SUBCOMMANDS

        Native token ``subcommands``.

    .. attribute :: OPTIONS

        Native token ``options``.
    """

    PURPOSE = "purpose"
    SUBCOMMANDS = "subcommands"
    OPTIONS = "options"


@dataclass(frozen=True, slots=True)
class MirrorAction(SkillAction):
    """Retain validated command inputs for reusable mirror callbacks.

    :param target: Existing resolver result with both provider authorities.
    :param intent: Normalized source, destination and explicit creation intent.
    :param mode: Shared offline, live-plan or confirmed apply selection.
    :param timeout: Positive operation bound accepted by the shared mode policy.
    :param confirmation: Caller-supplied live plan digest, absent outside apply.
    :param revision: Optional installed skill revision assertion.
    :param deadline: One absolute bound covering resolution, provider calls and read-back.

    .. attribute :: target
        :type: Target

        Existing resolver result with both provider authorities.

    .. attribute :: intent
        :type: MirrorIntent

        Normalized source, destination and explicit creation intent.

    .. attribute :: mode
        :type: ActionMode

        Shared offline, live-plan or confirmed apply selection.

    .. attribute :: timeout
        :type: int

        Positive operation bound accepted by the shared mode policy.

    .. attribute :: confirmation
        :type: Optional[str]

        Caller-supplied live plan digest, absent outside apply.

    .. attribute :: revision
        :type: Optional[str]

        Optional installed skill revision assertion.

    .. attribute :: deadline
        :type: RequestDeadline

        Shared operation bound created before resolving the selected target.
    """

    target: Target
    intent: MirrorIntent
    mode: ActionMode
    timeout: int
    confirmation: Optional[str]
    revision: Optional[str]
    deadline: RequestDeadline


@dataclass
class MirrorState(DeliveryState):
    """Share private provider state and one bounded mirror result.

    DeliveryState documents inherited provider inputs and callback history.

    :param plan: Fresh live confirmation inputs, absent before observation.
    :param record: Single planned or verified result, empty before observation.
    :param session: Private GitLab credential and installed identity, absent before binding.
    :param engine: Provider-independent mutation owner, absent before observation.
    :ivar gitlab: Verified source-project service.

    .. attribute :: plan
        :type: Optional[MirrorPlan]

        Fresh live confirmation inputs, absent before observation.

    .. attribute :: record
        :type: JSONObject

        Single planned or verified result, empty before observation.

    .. attribute :: session
        :type: Optional[BoundGitLabSession]

        Private GitLab credential and installed identity, absent before binding.

    .. attribute :: gitlab
        :type: GitLabService

        Shared gitlab retained for this invocation.

    .. attribute :: engine
        :type: Optional[MirrorEngine]

        Provider-independent mutation owner, absent before observation.
    """

    plan: Optional[MirrorPlan] = None
    record: JSONObject = field(default_factory=dict)
    session: Optional[BoundGitLabSession] = field(default=None, repr=False)
    gitlab: GitLabService = field(init=False, repr=False)
    engine: Optional[MirrorEngine] = field(default=None, repr=False)


class MirrorSourceAuth(Callback[MirrorState, MirrorAction]):
    """Reuse selected-project authentication without runner-specific access."""

    @property
    def plugin_type(self) -> SkillPluginType:
        """Identify the source provider's shared configuration.

        :returns: Existing GitLab plugin identity.
        """
        return SkillPluginType.GITLAB

    def read_config(self) -> JSONObject:
        """Bind the resolved source snapshot and its existing credential provider.

        :returns: Empty evidence; credentials remain in private callback state.
        :raises TargetError: Target selection, credentials or provenance are invalid.
        """
        selected = self.action.target
        if selected.source_file is None:
            raise TargetError(TARGET_REQUIRED)
        target = GitLabOperationTarget(selected.require_gitlab(), selected.source_file)
        self.state.session = self.action.deadline.call(
            bind_gitlab_session,
            target,
            target_kind=Field.PROJECT.value,
            target_reference=self.action.intent.project,
            target_source=selected.source_kind or EnvelopeField.TARGET.value,
            revision=self.action.revision,
        )
        return {}

    def is_configured(self) -> bool:
        """Check that the exact selected credential session has been bound.

        :returns: Whether source configuration completed for this invocation.
        """
        return self.state.session is not None

    def run(self) -> JSONObject:
        """Verify source identity and project using the shared access checker.

        :returns: Empty callback evidence after retaining the verified service.
        :raises APIError: Provider access or selected-project evidence fails.
        :raises TargetError: Source configuration has not bound a credential session.
        :raises OSError: The shared native provider process cannot execute.
        :raises TypeError: The access checker returns malformed evidence.
        """  # noqa: DOC503 - Shared readers and transport propagate the documented failures.
        session = self.state.session
        if session is None:
            raise TargetError(TARGET_REQUIRED)
        client = GlabAPIClient(
            timeout=self.action.timeout, deadline=self.action.deadline
        )
        access = check_gitlab_operation_access(session, api_client=client)
        self.state.access_evidence[SkillPluginType.GITLAB.value] = access
        if access.get(Field.STATUS) != PASS:
            failures = json_objects(access.get(Field.ERRORS, []))
            reason = (
                json_string(failures[0][Field.REASON]) if failures else ACCESS_BLOCKED
            )
            raise APIError(reason)
        target_id = json_integer(json_object(access[EnvelopeField.TARGET])[Field.ID])
        self.state.gitlab = GitLabService(client, session, target_id, access)
        return {}

    def cleanup(self) -> JSONObject:
        """Leave source accounts and repository resources intact.

        :returns: Empty evidence because authentication owns no external resources.
        """
        return {}


def ref_evidence(snapshot: RefSnapshot) -> JSONObject:
    """Summarize complete refs without copying an unbounded provider listing.

    :param snapshot: Validated native branch and tag object observations.
    :returns: Complete ref count and deterministic content fingerprint.
    """
    return {Field.COUNT: len(snapshot.refs), Field.SHA256: snapshot.digest}


class ObserveMirror(LocalCallback[MirrorState, MirrorAction]):
    """Observe both providers through the shared mirror engine without writes."""

    def run(self) -> JSONObject:
        """Bind services, read current refs and retain a confirmable live plan.

        :returns: One bounded record containing actual current observations.
        :raises APIError: Repository, mirror or complete ref observation fails.
        :raises TargetError: Bound destination credentials or provenance are unavailable.
        :raises OSError: A provider or Git observation cannot execute.
        :raises TypeError: Installed skill identity fields are malformed.
        """  # noqa: DOC503 - Shared readers and transport propagate the documented failures.
        session = self.state.session
        if session is None:
            raise TargetError(TARGET_REQUIRED)
        github = self.state.github_identity
        reader = GitRemoteReader(self.action.timeout, deadline=self.action.deadline)
        source = GitLabPushMirrorService(self.state.gitlab, reader)
        destination = GitHubRepositoryService(github, reader)
        url = CLONE_URL_TEMPLATE.format(
            host=github.host, repository=self.action.intent.destination
        )
        engine = MirrorEngine(
            source,
            destination,
            url,
            DEFAULT_POLICY,
            github.identity,
            github.token,
            self.action.timeout,
            deadline=self.action.deadline,
        )
        self.state.engine = engine
        credential_digest = plan_digest(
            {
                SkillPluginType.GITLAB.value: session.credential_digest,
                SkillPluginType.GITHUB.value: hashlib.sha256(
                    github.token.encode()
                ).hexdigest(),
            }
        )
        plan = self.action.deadline.call(
            engine.plan,
            self.action.intent,
            json_string(session.skill[Field.FUNCTIONAL_DIGEST]),
            credential_digest,
        )
        self.state.plan = plan
        observed = plan.observation
        destination_identity = (
            observed.destination.evidence()
            if observed.destination is not None
            else {
                MirrorField.ID: None,
                MirrorField.HOST: github.host,
                MirrorField.PATH: self.action.intent.destination,
            }
        )
        self.state.record = {
            Field.CALLBACK: KIND,
            Field.ACTION: Outcome.PLANNED.value,
            Field.VERIFIED: False,
            Field.SOURCE: observed.source.evidence(),
            Field.DESTINATION: destination_identity,
            Field.MIRROR: (
                observed.mirror.evidence() if observed.mirror is not None else None
            ),
            Field.SOURCE_REFS: ref_evidence(observed.source_refs),
            Field.DESTINATION_REFS: ref_evidence(observed.destination_refs),
            Field.CREATED: {Field.REPOSITORY: False, Field.MIRROR: False},
            Field.REPLACEMENT_REQUIRED: plan.replacement_required,
            Field.REPLACED_MIRROR_ID: plan.replaced_mirror_id,
            Field.VERIFIED_SOURCE_REF_COUNT: observed.destination_refs.matching_count(
                observed.source_refs
            ),
        }
        return self.state.record

    def cleanup(self) -> JSONObject:
        """Preserve independently observed repositories and native mirror state.

        :returns: Empty evidence because observation acquires no persistent resources.
        """
        return {}


class ApplyMirror(LocalCallback[MirrorState, MirrorAction]):
    """Apply the exact fresh live plan through the shared mutation owner."""

    def run(self) -> JSONObject:
        """Require confirmation and retain independently verified replication.

        :returns: Single verified result with actual resource-creation ownership.
        :raises APIError: Plan confirmation, mutation or independent read-back fails.
        :raises OSError: Native provider or Git I/O fails.
        """  # noqa: DOC503 - Shared readers and transport propagate the documented failures.
        plan = self.state.plan
        engine = self.state.engine
        if plan is None or engine is None:
            raise APIError(PLAN_REQUIRED)
        result = self.action.deadline.call(
            engine.apply, plan, self.action.confirmation or EMPTY
        )
        destination = self.action.deadline.call(
            engine.destination.repository, self.action.intent.destination
        )
        if destination is None:
            raise APIError(Failure.DESTINATION_MISSING)
        self.state.record = result.evidence(destination)
        return self.state.record

    def cleanup(self) -> JSONObject:
        """Retain confirmed resources for inspection after success or partial failure.

        :returns: Empty evidence; private transport and Git requests own their cleanup.
        """
        return {}


def workflow(action: MirrorAction, state: MirrorState) -> WorkflowExecutor[MirrorState]:
    """Compose source access, destination identity, planning and confirmed mutation.

    :param action: Validated live-plan or apply inputs.
    :param state: This invocation's one typed shared state.
    :returns: Existing executor with the declared provider-independent lifecycle.
    :raises TargetError: Source or destination configuration cannot be bound.
    :raises TypeError: Callback state or shared configuration is malformed.
    :raises ValueError: Callback configuration or ownership is invalid.
    """  # noqa: DOC503 - Shared readers and transport propagate the documented failures.
    if action.mode not in (ActionMode.LIVE_PLAN, ActionMode.APPLY):
        raise ValueError(LIVE_MODE_REQUIRED)
    callbacks: AbstractCallbacks[MirrorState, SkillAction] = [
        MirrorSourceAuth(action),
        GitHubIdentityAuth(action.target, action.timeout, deadline=action.deadline),
        ObserveMirror(action),
    ]
    if action.mode == ActionMode.APPLY:
        callbacks.append(ApplyMirror(action))
    return WorkflowExecutor(state, Callbacks(callbacks))


def build_parser() -> argparse.ArgumentParser:
    """Build the installed command parser from its one approved catalog entry.

    :returns: Shared discovery/output parser with the mirror operation controls.
    :raises KeyError: The mirror command contract has not been registered.
    """  # noqa: DOC502 - Shared readers and transport propagate the documented failures.
    entry = COMMANDS[COMMAND]
    result = parser(entry[CatalogField.PURPOSE], kind=KIND)
    result.prog = COMMAND
    result.set_defaults(kind=KIND)
    result.add_argument(
        ARGUMENT_ACTION,
        nargs=ARGUMENT_OPTIONAL,
        choices=tuple(entry[CatalogField.SUBCOMMANDS]),
    )
    for option, help_text in entry[CatalogField.OPTIONS].items():
        if option in UNIVERSAL_OPTIONS:
            continue
        if option in BOOLEAN_OPTIONS:
            result.add_argument(option, help=help_text, action=STORE_TRUE_ACTION)
        elif option == TIMEOUT_OPTION:
            result.add_argument(
                option, help=help_text, type=int, default=MAX_ACTION_TIMEOUT
            )
        else:
            result.add_argument(option, help=help_text)
    return result


class MirrorCommand:
    """Validate modes, execute declared callbacks and emit the shared report."""

    def __init__(self, args: argparse.Namespace) -> None:
        """Retain parsed controls without resolving files or credentials.

        :param args: Catalog-backed mirror and shared output arguments.
        """
        self.args = args
        self.state = MirrorState(args=args)

    def prepare(self) -> MirrorAction:
        """Validate once and resolve only explicitly selected provider authorities.

        :returns: Immutable inputs; offline mode does not bind credentials.
        :raises ValueError: Operation, mode, timeout or persistence controls conflict.
        :raises APIError: Apply lacks confirmation or normalized intent is invalid.
        :raises TargetError: Required source/destination selection is missing or invalid.
        """
        args = self.args
        if args.action != OPERATION:
            raise ValueError(ACTION_REQUIRED)
        mode = validate_action_mode(
            apply=args.apply,
            dry_run=args.dry_run,
            live_plan=args.live_plan,
            confirmed=bool(args.confirm_plan),
            receipt_requested=bool(args.receipt_out),
            output_requested=bool(args.output_dir or args.log_file),
            timeout=args.timeout,
            policy=GITLAB_MODE_POLICY,
        )
        if mode == ActionMode.APPLY and not args.confirm_plan:
            raise APIError(Failure.UNCONFIRMED)
        args.dry_run = mode == ActionMode.OFFLINE
        deadline = RequestDeadline(time.monotonic() + args.timeout)
        target = deadline.call(
            resolve_target,
            args.target,
            args.binding,
            dry_run=args.dry_run,
            required_surfaces=SURFACES,
        )
        selected = target.require_gitlab()
        source = args.project if args.project is not None else selected.project
        if source is None or target.source_file is None:
            raise TargetError(PROJECT_REQUIRED)
        _, project = select_gitlab_reference(
            GitLabOperationTarget(selected, target.source_file), project=source
        )
        destination = (
            args.destination
            if args.destination is not None
            else target.require_github().repository
        )
        intent = MirrorIntent.parse(project, destination, args.create_if_absent)
        return MirrorAction(
            target,
            intent,
            mode,
            args.timeout,
            args.confirm_plan,
            args.revision,
            deadline,
        )

    def preserve_partial(self) -> None:
        """Retain known resource identities and ownership after an uncertain write.

        :returns: None; the bounded state record remains explicitly unverified.
        """
        engine = self.state.engine
        if engine is None or not self.state.record or engine.mutated is False:
            return
        record: dict[str, JSONValue] = dict(self.state.record)
        record.update(
            {
                Field.ACTION: Outcome.APPLIED.value,
                Field.VERIFIED: False,
                Field.VERIFIED_SOURCE_REF_COUNT: NO_VERIFIED_REFS,
                Field.CREATED: {
                    Field.REPOSITORY: engine.created_repository,
                    Field.MIRROR: engine.created_mirror,
                },
                Field.REPLACEMENT_REQUIRED: engine.replacement_required,
                Field.REPLACED_MIRROR_ID: engine.replaced_mirror_id,
            }
        )
        if engine.destination_repository is not None:
            record[Field.DESTINATION] = engine.destination_repository.evidence()
        if engine.current_mirror is not None:
            record[Field.MIRROR] = engine.current_mirror.evidence()
        self.state.record = record

    def output(
        self, action: MirrorAction, status: str
    ) -> MutableMapping[str, JSONValue]:
        """Render selected intent and bounded observations through the existing envelope.

        :param action: Prepared source, destination and execution inputs.
        :param status: Result classification from the selected mode or failure.
        :returns: Versioned result with provenance, mutation and cleanup evidence.
        """
        records = [self.state.record] if self.state.record else []
        output = report(
            KIND,
            action.target.require_gitlab().url,
            action.intent.evidence(),
            records,
            [],
        )
        engine = self.state.engine
        output.update(
            {
                Field.STATUS: status,
                Field.OPERATION: OPERATION,
                Field.PLAN_DIGEST: (
                    self.state.plan.digest if self.state.plan is not None else None
                ),
                Field.MUTATED: engine.mutated if engine is not None else False,
                Field.CLEANUP: {
                    Field.STATUS: BLOCKED if self.state.cleanup_failures else PASS,
                    Field.FAILURES: copy_json(self.state.cleanup_failures),
                },
            }
        )
        session = self.state.session
        if session is not None:
            output[Field.SKILL] = copy_json(session.skill)
            output[Field.TESTED_REVISION] = json_string(
                json_object(session.skill[Field.REVISION])[Field.VALUE]
            )
            output[EnvelopeField.EXECUTION_HOST] = session.execution_host
        return output

    def execute(self) -> int:
        """Run one mode and preserve write uncertainty when live execution fails.

        :returns: Shared status exit code after requested report/receipt emission.
        :raises OSError: Final output, receipt or diagnostic persistence fails.
        :raises TypeError: The completed report contains invalid JSON values.
        :raises ValueError: The completed report cannot be serialized.
        """  # noqa: DOC503 - Shared readers and transport propagate the documented failures.
        if self.args.describe:
            return emit_result(describe(COMMAND), self.args)
        try:
            action = self.prepare()
        except (APIError, OSError, RuntimeError, TypeError, ValueError) as exc:
            return _failure(
                self.args,
                KIND,
                ARGUMENTS_SOURCE,
                sanitize(str(exc), ERROR_LIMIT),
                safe_next_step=SELECT_TARGET,
            )
        status = DRY_RUN
        errors: list[JSONObject] = []
        if action.mode != ActionMode.OFFLINE:
            try:
                executor = action.deadline.call(workflow, action, self.state)
                action.deadline.call(executor.run)
                status = PASS if action.mode == ActionMode.APPLY else PLANNED
                verified = (
                    self.state.record.get(Field.VERIFIED) is True
                    if action.mode == ActionMode.APPLY
                    else self.state.record.get(Field.ACTION) == Outcome.PLANNED.value
                )
                if (
                    self.state.plan is None
                    or not verified
                    or self.state.failed
                    or self.state.cleanup_failures
                ):
                    raise APIError(Failure.REF_MISMATCH)
            except (APIError, OSError, RuntimeError, TypeError, ValueError) as exc:
                engine = self.state.engine
                mutated = engine.mutated if engine is not None else False
                status = PARTIAL if mutated is not False else BLOCKED
                self.preserve_partial()
                errors.append(
                    {Field.SOURCE: KIND, Field.REASON: sanitize(str(exc), ERROR_LIMIT)}
                )
        output = self.output(action, status)
        output[Field.ERRORS] = errors
        output[EnvelopeField.SUMMARY] = {
            EnvelopeField.RECORD_COUNT: len(json_objects(output[Field.RECORDS])),
            EnvelopeField.ERROR_COUNT: len(errors),
        }
        if errors:
            output[EnvelopeField.SAFE_NEXT_STEP] = RECOVERY
        return emit_result(output, self.args)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse one installed mirror request and delegate its complete lifecycle.

    :param argv: Explicit arguments, or process arguments when absent.
    :returns: Canonical exit code for the rendered result.
    :raises SystemExit: Shared parser handles help or rejects invalid arguments.
    :raises OSError: Final report or receipt output cannot be written.
    """  # noqa: DOC502 - Shared readers and transport propagate the documented failures.
    return MirrorCommand(build_parser().parse_args(argv)).execute()
