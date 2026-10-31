"""Adapt native read commands to the shared catalogue and callback executor.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NoReturn, Optional

from lib.python.core.abstract.callbacks import Callbacks
from lib.python.core.api_read import (
    ApiCheckAction,
    ApiOutput,
    ApiReadAction,
    ApiReadState,
    BindAPI,
    CheckAPI,
    GitProvider,
    PlanAPI,
    PlanAPICheck,
    ReadAPI,
    SelectAPI,
)
from lib.python.core.catalog import describe
from lib.python.core.cli import MachineArgumentParser, add_gitlab_profile_argument
from lib.python.core.cli_api import APIError
from lib.python.core.executor import WorkflowExecutor
from lib.python.core.json_types import JSONValue, json_string
from lib.python.core.logging import add_log_arguments, log_event
from lib.python.core.read_tools import ReadExit, ReadToolError, read_failure_message
from lib.python.core.report import render_value
from lib.python.core.runtime import sanitize
from lib.python.core.target import TargetError
from lib.python.sys.status import BLOCKED, DRY_RUN, PASS, PLANNED


class ReadArgumentParser(MachineArgumentParser):
    """Preserve native read exit codes without echoing rejected input values."""

    def error(self, message: str) -> NoReturn:
        """Report invalid arguments without exposing a supplied credential.

        :param message: Argparse diagnostic, intentionally not reproduced.
        :raises SystemExit: With the native invalid-argument exit code.
        """  # noqa: DOC502 - argparse.exit raises SystemExit.
        self.print_usage(sys.stderr)
        self.exit(ReadExit.USAGE, f"{self.prog}: invalid arguments; see --help\n")


def _parser(script: str) -> ReadArgumentParser:
    """Create a read-command parser with catalogue help and shared controls.

    :param script: Command path registered in the command catalogue.
    :returns: Parser accepting discovery, dry-run, and logging options.
    :raises KeyError: If the command path is not registered.
    """  # noqa: DOC502 - Required catalogue metadata is accessed by key.
    contract = describe(script)
    result = ReadArgumentParser(
        prog=Path(script).name,
        report_kind=json_string(contract["report_kind"]),
        help_contract=contract,
    )
    result.add_argument("--describe", action="store_true")
    result.add_argument("--dry-run", action="store_true")
    add_log_arguments(result)
    return result


def build_api_parser() -> ReadArgumentParser:
    """Create the argument parser for authenticated API reads.

    :returns: Unparsed ``ci-api`` parser accepting ``check`` or ``get``,
        provider selection, response transformations, and JSON, YAML, or text
        output. JSON is the default output format.
    :raises KeyError: If the installed API command is missing from the catalogue.
    """  # noqa: DOC502 - Required catalogue metadata is accessed by key.
    result = _parser("bin/ci-api")
    result.add_argument("action", nargs="?", choices=("check", "get"))
    result.add_argument("--provider", choices=tuple(GitProvider))
    result.add_argument("--host")
    add_gitlab_profile_argument(result)
    result.add_argument("--token-file", type=Path)
    result.add_argument("--endpoint")
    result.add_argument("--field")
    result.add_argument("--decode-base64", action="store_true")
    result.add_argument("--lines")
    result.add_argument("--paginate", action="store_true")
    output = result.add_mutually_exclusive_group()
    output.add_argument(
        "--output",
        type=ApiOutput,
        choices=(ApiOutput.JSON, ApiOutput.TEXT),
        default=ApiOutput.JSON,
    )
    output.add_argument("--json", action="store_true")
    output.add_argument("--yaml", action="store_true")
    return result


def build_binary_parser() -> ReadArgumentParser:
    """Create the argument parser for an exact-commit binary build plan.

    :returns: Unparsed ``ci-binary-build`` parser accepting the BuildConfig
        path, source repository, commit, commit-label key, optional context,
        and JSON or YAML output. Defaults to the ``plan`` action and JSON.
    :raises KeyError: If the installed build command is missing from the catalogue.
    """  # noqa: DOC502 - Required catalogue metadata is accessed by key.
    result = _parser("bin/ci-binary-build")
    result.add_argument("--spec", type=Path)
    result.add_argument("--source-repo", type=Path)
    result.add_argument("--source-commit", default="")
    result.add_argument("--commit-label", default="")
    result.add_argument("--context")
    output = result.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true")
    output.add_argument("--yaml", action="store_true")
    result.set_defaults(action="plan", live_plan=True)
    return result


def _output(
    args: argparse.Namespace,
    script: str,
    operation: Callable[[], JSONValue],
    mode: ApiOutput,
) -> int:
    """Write a command result and translate known failures to native exit codes.

    :param args: Parsed discovery, dry-run, and logging options.
    :param script: Registered command path used for metadata and diagnostics.
    :param operation: Zero-argument callback, skipped for ``--describe``.
    :param mode: Named JSON, YAML, or text output representation.
    :returns: Zero on success; 64 for invalid arguments, 65 for invalid data,
        66 for missing input, or 69 for a blocked operation.
    :raises KeyError: If the command path is not registered.
    :raises OSError: If failure diagnostics cannot be written to standard error.
    """  # noqa: DOC502 - Catalogue indexing and final stderr writes can fail.
    contract = describe(script)
    persist = not (args.describe or args.dry_run)
    try:
        value = contract if args.describe else operation()
        rendered = render_value(value, mode, compact=not args.describe)
        if not args.describe:
            status = (
                DRY_RUN
                if args.dry_run
                else (PLANNED if getattr(args, "live_plan", False) else PASS)
            )
            log_event(
                args,
                json_string(contract["report_kind"]),
                "result",
                status,
                persist=persist,
            )
        sys.stdout.write(rendered)
        return ReadExit.SUCCESS
    except ReadToolError as exc:
        code, reason = exc.code, str(exc)
        diagnostic = reason
    except APIError as exc:
        reason = exc.reason
        code = ReadExit.DATA if reason == "response_too_large" else ReadExit.BLOCKED
        diagnostic = read_failure_message(reason)
    except TargetError as exc:
        reason = str(exc)
        diagnostic = reason
        code = {
            "credential_file_unavailable": ReadExit.MISSING,
            "credential_file_invalid": ReadExit.DATA,
        }.get(reason, ReadExit.BLOCKED)
    except (ImportError, OSError, RuntimeError) as exc:
        code, reason = ReadExit.BLOCKED, type(exc).__name__
        diagnostic = reason
    except (TypeError, ValueError) as exc:
        code, reason = ReadExit.DATA, type(exc).__name__
        diagnostic = reason
    try:
        log_event(
            args,
            json_string(contract["report_kind"]),
            "result",
            BLOCKED,
            error_class=sanitize(reason, 160),
            persist=persist,
        )
    except OSError:
        pass
    print(f"{Path(script).name}: {sanitize(diagnostic, 240)}", file=sys.stderr)
    return code


def _api_callbacks(
    args: argparse.Namespace, output: ApiOutput
) -> Callbacks[ApiReadState]:
    """Construct the declared linear workflow for one concrete API operation.

    :param args: Parsed action, provider, target overrides, and response options.
    :param output: Named representation retained by the concrete action.
    :returns: Ordered selection and planning callbacks, or selection, binding,
        and the concrete identity-check or endpoint-read callback.
    :raises ReadToolError: If an identity check includes an endpoint or response
        transforms, a GET omits its endpoint, or no supported operation is selected.
    :raises ValueError: If called with a provider outside the parser's choices.
    """  # noqa: DOC503 - GitProvider rejects unsupported caller-supplied values.
    provider = GitProvider(args.provider)
    if args.action == "check":
        if (
            args.endpoint is not None
            or args.field is not None
            or args.lines is not None
            or args.decode_base64
            or args.paginate
        ):
            raise ReadToolError(
                "check does not accept response transforms", ReadExit.USAGE
            )
        check = ApiCheckAction(
            provider,
            args.host,
            args.token_file,
            output,
            gitlab_profile=args.gitlab_profile,
        )
        return Callbacks(
            (SelectAPI(check), PlanAPICheck(check))
            if args.dry_run
            else (SelectAPI(check), BindAPI(check), CheckAPI(check))
        )
    if args.action != "get" or args.endpoint is None:
        raise ReadToolError("a relative GET endpoint is required", ReadExit.USAGE)
    read = ApiReadAction(
        provider=provider,
        gitlab_profile=args.gitlab_profile,
        endpoint=args.endpoint,
        host=args.host,
        token_file=args.token_file,
        field=args.field,
        decode_base64=args.decode_base64,
        lines=args.lines,
        paginate=args.paginate,
        output=output,
    )
    return Callbacks(
        (SelectAPI(read), PlanAPI(read))
        if args.dry_run
        else (SelectAPI(read), BindAPI(read), ReadAPI(read))
    )


def api_main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the bounded read callback or emit its discovery/request plan.

    :param argv: Argument sequence; ``None`` reads process arguments.
    :returns: Zero after writing the selected response, discovery metadata,
        or request plan; otherwise the read-command failure exit code.
    :raises SystemExit: With zero for help or 64 for invalid CLI arguments.
    :raises KeyError: If required API catalogue metadata is absent.
    :raises OSError: If a final failure diagnostic cannot be written to stderr.
    """  # noqa: DOC502 - Argument parsing and final output can exit or fail.
    parser = build_api_parser()
    args = parser.parse_args(None if argv is None else list(argv))
    mode = ApiOutput.YAML if args.yaml else args.output
    action_mode = mode
    if not args.describe and (not args.action or not args.provider):
        parser.error("action and provider are required")

    def operation() -> JSONValue:
        """Execute the parsed read action or describe its request.

        :returns: Selected provider value or a nonexecuted request plan.
        :raises ReadToolError: If the action or returned data is invalid.
        :raises TargetError: If configuration or credential resolution fails.
        :raises APIError: If the provider request fails.
        :raises OSError: If a native provider command cannot be started.
        """  # noqa: DOC502 - Ordered callbacks propagate input, provider, and I/O errors.
        context = ApiReadState.from_args(args)
        WorkflowExecutor(context, _api_callbacks(args, action_mode)).run()
        return context.payload

    if args.describe or args.dry_run:
        mode = ApiOutput.YAML if args.yaml else ApiOutput.JSON
    return _output(args, "bin/ci-api", operation, mode)


def binary_main(argv: Optional[Sequence[str]] = None) -> int:
    """Validate the supplied manifest and Git identity through one callback.

    :param argv: Argument sequence; ``None`` reads process arguments.
    :returns: Zero after writing a fingerprinted build plan or discovery
        metadata; otherwise the read-command failure exit code.
    :raises SystemExit: With zero for help or 64 for invalid CLI arguments.
    :raises KeyError: If required binary-build catalogue metadata is absent.
    :raises OSError: If a final failure diagnostic cannot be written to stderr.
    """  # noqa: DOC502 - Argument parsing and final output can exit or fail.
    parser = build_binary_parser()
    args = parser.parse_args(None if argv is None else list(argv))
    if not args.describe and not all(
        (args.spec, args.source_repo, args.source_commit, args.commit_label)
    ):
        parser.error("build inputs are required")

    def operation() -> JSONValue:
        """Validate the parsed build inputs and serialize the plan result.

        :returns: Mapping containing mode, fingerprint, and validated plan.
        :raises ImportError: If the YAML dependency is unavailable.
        :raises ReadToolError: If the manifest or source identity is invalid.
        :raises OSError: If a native Git command cannot be started.
        """  # noqa: DOC502 - Ordered callbacks propagate input, provider, and I/O errors.
        from lib.python.core.binary_build import (
            BinaryBuildAction,
            BinaryBuildState,
            PlanBinaryBuild,
        )

        callback = PlanBinaryBuild(
            action=BinaryBuildAction(
                spec=args.spec,
                source_repo=args.source_repo,
                source_commit=args.source_commit,
                commit_label=args.commit_label,
                context=args.context,
            )
        )
        context = BinaryBuildState.from_args(args)
        WorkflowExecutor(context, Callbacks((callback,))).run()
        return context.build_plan.document()

    return _output(
        args,
        "bin/ci-binary-build",
        operation,
        ApiOutput.YAML if args.yaml else ApiOutput.JSON,
    )
