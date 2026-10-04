#!/usr/bin/env python3
"""Report one GitLab pipeline's bounded job and stage progress."""

from __future__ import annotations

import argparse
import json
import sys
import time

from core.access import check_gitlab_operation_access
from core.catalog import describe, missing_required_options
from core.cli import _failure, log_event, output_mode, parser, resolve_gitlab_target
from core.credentials import bind_gitlab_session
from core.gitlab_pipelines import MAX_JOB_PAGES, PAGE_SIZE, read_pipeline
from core.report import emit, report
from core.status import BLOCKED, DRY_RUN, PASS, exit_code
from core.target import TargetError, select_gitlab_reference

KIND = "gitlab_pipeline"
SCRIPT = "gitlab_pipeline.py"


# Summary: Define project and pipeline selectors on the shared CLI.
# Arguments: none; Environment inputs: none; Stdout: none; Stderr: none.
# Exit classes: no process exit; Side effects: constructs a parser in memory.
# Idempotency: same option definitions each call; Cleanup: none.
def build_parser() -> argparse.ArgumentParser:
    """Create the GitLab pipeline CLI parser.

    :returns: Shared options plus exact project and pipeline selectors.
    """
    cli = parser(
        "Read one selected GitLab pipeline and summarize job progress by stage.",
        kind=KIND,
    )
    cli.add_argument(
        "--project",
        metavar="PATH_OR_ID",
        help="exact project path or numeric ID (defaults to gitlab.project in target)",
    )
    cli.add_argument(
        "--pipeline-id",
        metavar="ID",
        type=int,
        help="numeric ID of the pipeline to read",
    )
    return cli


# Summary: Read one bound GitLab pipeline and emit a bounded report.
# Arguments: argv optionally supplies CLI arguments; Environment inputs: target and auth.
# Stdout: pipeline report or command description; Stderr: logs and usage errors.
# Exit classes: success or classified blocked/usage status
# Side effects: GitLab GETs and optional report file.
# Idempotency: read follows live pipeline state; Cleanup: delegated output helper.
def main(argv: list[str] | None = None) -> int:
    """Bind one session and report the selected pipeline's current jobs.

    :param argv: Optional arguments, or process arguments when omitted.
    :returns: Exit status from the pipeline read and report.
    """
    args = build_parser().parse_args(argv)
    if args.describe:
        sys.stdout.write(json.dumps(describe(SCRIPT), indent=2, sort_keys=True) + "\n")
        return 0
    if args.dry_run and (args.output_dir or args.log_file):
        return _failure(args, KIND, "arguments", "dry_run_cannot_write_output")
    missing = missing_required_options(SCRIPT, args)
    if missing:
        return _failure(
            args, KIND, "arguments", f"{', '.join(missing)} is required; see --describe"
        )
    if args.pipeline_id <= 0:
        return _failure(args, KIND, "arguments", "pipeline_id_must_be_positive")

    started = time.monotonic()
    source = "target"
    try:
        target, target_source = resolve_gitlab_target(
            args.target, args.binding, dry_run=args.dry_run
        )
        target_kind, reference = select_gitlab_reference(target, project=args.project)
        if target_kind != "project":
            raise TargetError("pipeline_requires_project_target")
        filters = {"project": reference, "pipeline_id": args.pipeline_id}
        if args.dry_run:
            job_limit = PAGE_SIZE * MAX_JOB_PAGES
            data = report(KIND, target.gitlab.url, filters, [], [])
            data["status"] = DRY_RUN
            data["target_source"] = target_source
            data["access"] = {
                "status": DRY_RUN,
                "target": {"kind": "project", "reference": reference},
                "observed_capability": [],
            }
            data["collection_probes"] = [
                "exact GitLab identity and project reads",
                f"pipeline and up to {job_limit} job reads with one overflow probe",
            ]
        else:
            source = "credential"
            session = bind_gitlab_session(
                target,
                target_kind="project",
                target_reference=reference,
                target_source=(
                    f"{target_source};argv:--project" if args.project else target_source
                ),
                revision=args.revision,
            )
            source = "access"
            gate = check_gitlab_operation_access(session)
            if gate["status"] != PASS:
                data = report(KIND, target.gitlab.url, filters, [], gate["errors"])
                data["status"] = BLOCKED
            else:
                source = "pipeline"
                record, errors = read_pipeline(
                    session, gate["target"]["id"], args.pipeline_id
                )
                data = report(KIND, target.gitlab.url, filters, [record], errors)
            data["access"] = gate
            data["execution_host"] = session.execution_host
            data["tested_revision"] = session.skill["revision"]["value"]
            data["skill"] = session.skill
            data["target_source"] = session.target_source
        rendered = emit(data, output_mode(args), args.output_dir)
        log_event(
            args, KIND, "result", data["status"], elapsed=time.monotonic() - started
        )
        sys.stdout.write(rendered)
        return exit_code(data["status"])
    except (TargetError, RuntimeError, ValueError, OSError, TypeError, KeyError) as exc:
        return _failure(args, KIND, source, str(exc))
    except Exception:  # noqa: BLE001 - keep unexpected failures machine-readable
        return _failure(args, KIND, source, "unexpected_runtime_failure")


if __name__ == "__main__":
    raise SystemExit(main())
