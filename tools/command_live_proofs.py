"""Check submitted before/action/after evidence for every installed command."""

from __future__ import annotations

import json
import hashlib
import re
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Summary: recognize approved project Python invocation; Arguments: argv
# Environment inputs: none; Stdout: none; Stderr: none; Exit classes: boolean
# Side effects: none; Idempotency: stable argv; Cleanup: none
def _project_python(argv: Any) -> bool:
    """Require the declared ci-skills conda interpreter in public command proof.

    :param argv: Sanitized recorded command argument vector.
    :returns: Whether it starts with the approved conda environment invocation.
    """
    return isinstance(argv, list) and argv[:5] == [
        "conda", "run", "-n", "ci-skills", "python"
    ]


# Summary: parse a UTC timestamp; Arguments: untrusted JSON value
# Environment inputs: none; Stdout: none; Stderr: none; Exit classes: instant or None
# Side effects: none; Idempotency: stable value; Cleanup: none
def _time(value: Any) -> datetime | None:
    """Parse a timezone-aware timestamp for evidence ordering.

    :param value: JSON value expected to contain an ISO 8601 timestamp.
    :returns: UTC instant, or None for an invalid or naive timestamp.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


# Summary: require substantive live output; Arguments: output and body allowance
# Environment inputs: none; Stdout: none; Stderr: none; Exit classes: boolean
# Side effects: none; Idempotency: stable output; Cleanup: none
def _live_body(value: Any, *, allow_body: bool = False) -> bool:
    """Reject flags and digests used in place of a live response body.

    :param value: Parsed command or API output.
    :param allow_body: Whether an independent GET body is an accepted shape.
    :returns: Whether output contains a recognized substantive payload field.
    """
    if not isinstance(value, dict) or not value:
        return False
    assertions = {"verified", "access_proven", "readback_sha256", "status", "hash"}
    if not set(value) - assertions:
        return False
    evidence_keys = {
        "records",
        "surfaces",
        "identity",
        "id",
        "iid",
        "slug",
        "items",
        "metadata",
        "message",
        "nodes",
        "planned_nodes",
        "plan",
    }
    if allow_body:
        evidence_keys.add("body")
    return bool(evidence_keys & set(value))


# Summary: extract server resource identity; Arguments: response and optional key
# Environment inputs: none; Stdout: none; Stderr: none; Exit classes: pair or None
# Side effects: none; Idempotency: stable response; Cleanup: none
def _resource_id(value: Any, key: str | None = None) -> tuple[str, Any] | None:
    """Extract an ID from the resource returned by a command or GET.

    :param value: Parsed response or nested response body.
    :param key: Required identity field when matching an earlier response.
    :returns: Identity field and value, or None when absent.
    """
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("body"), dict):
        value = value["body"]
    for candidate in (key,) if key else ("id", "iid", "slug"):
        if value.get(candidate) not in (None, ""):
            return candidate, value[candidate]
    return None


# Summary: normalize live GET state; Arguments: parsed independent GET output
# Environment inputs: none; Stdout: none; Stderr: none; Exit classes: state or None
# Side effects: none; Idempotency: stable response; Cleanup: none
def _observed_state(value: Any) -> dict[str, Any] | None:
    """Return a GET state suitable for comparing before and cleanup reads.

    :param value: Parsed independent GET output with status and body.
    :returns: State without server update timestamps, or None if unreadable.
    """
    if not isinstance(value, dict) or value.get("http_status") not in (200, 404):
        return None
    body = value.get("body")
    if isinstance(body, list):
        endpoint = value.get("endpoint")
        if (
            value["http_status"] != 200
            or not isinstance(endpoint, str)
            or not endpoint
        ):
            return None
        return {"http_status": 200, "endpoint": endpoint, "body": body}
    if not isinstance(body, dict) or not body:
        return None
    if value["http_status"] == 200 and (
        _resource_id(body) is None or len(body) < 2
    ):
        return None
    if value["http_status"] == 404 and not any(
        marker in str(body.get("message", "")).lower()
        for marker in ("404", "not found")
    ):
        return None
    return {
        "http_status": value["http_status"],
        "body": {
            key: item
            for key, item in body.items()
            if key not in {"updated_at", "last_activity_at"}
        },
    }


# Summary: derive required evidence phases; Arguments: command and catalog
# Environment inputs: none; Stdout: none; Stderr: none; Exit classes: phase list
# Side effects: none; Idempotency: fixed catalog; Cleanup: none
def _steps_for(script: str, catalog: Any) -> list[tuple[str | None, str]]:
    """List every required phase for one catalog command.

    :param script: Catalog command filename.
    :param catalog: Imported command catalog module.
    :returns: Ordered operation and phase pairs.
    """
    entry = catalog.COMMANDS.get(script) or catalog.NODE_LOCAL_COMMANDS.get(script)
    if not entry.get("mutates"):
        return [(None, phase) for phase in ("before", "action", "after")]
    if script == "gitlab_runner.py":
        return [
            ("create", phase)
            for phase in (
                "before", "plan", "apply", "after", "safe_repeat_refusal",
                "repeat_get",
            )
        ] + [
            ("assign", phase)
            for phase in (
                "before", "plan", "apply", "after", "no_op_repeat",
                "repeat_get", "cleanup", "final_read",
            )
        ] + [("create", "cleanup"), ("create", "final_read")]
    operations = sorted(entry.get("subcommands") or {"apply": {}})
    return [
        (operation, phase)
        for operation in operations
        for phase in (
            "before",
            "plan",
            "apply",
            "after",
            "safe_repeat_refusal"
            if script == "gitlab_runner.py" and operation == "create"
            else "repeat_apply"
            if script == "k8s_verify_mtu_consistency.py"
            else "no_op_repeat",
            "repeat_get",
            "cleanup",
            "final_read",
        )
    ]


# Summary: hash committed skill bytes; Arguments: skill root and exact revision
# Environment inputs: local Git objects; Stdout: none; Stderr: none
# Exit classes: SHA-256 or None; Side effects: read-only Git calls
# Idempotency: immutable commit; Cleanup: Git child processes exit
def _committed_skill_digest(skill_root: Path, revision: str) -> str | None:
    """Compute the package digest from an immutable commit's skill tree.

    :param skill_root: Current checkout's skill package directory.
    :param revision: Full Git commit SHA to inspect.
    :returns: Package digest, or None for invalid/unreadable Git content.
    """
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        return None
    root = skill_root.resolve().parents[1]
    prefix = skill_root.resolve().relative_to(root).as_posix() + "/"
    listing = subprocess.run(
        ["git", "-C", str(root), "ls-tree", "-r", "-z", revision, "--", prefix],
        capture_output=True,
        check=False,
    )
    if listing.returncode or not listing.stdout:
        return None
    overall = hashlib.sha256()
    count = 0
    for row in listing.stdout.split(b"\0"):
        if not row:
            continue
        metadata, raw_path = row.split(b"\t", 1)
        mode, kind, oid = metadata.decode("ascii").split(" ")
        if kind != "blob" or mode == "120000":
            return None
        relative = raw_path.decode("utf-8").removeprefix(prefix)
        path = Path(relative)
        if (
            "__pycache__" in path.parts
            or path.suffix in {".pyc", ".pyo"}
            or path.name == ".DS_Store"
        ):
            continue
        blob = subprocess.run(
            ["git", "-C", str(root), "cat-file", "blob", oid],
            capture_output=True,
            check=False,
        )
        if blob.returncode:
            return None
        content = hashlib.sha256(blob.stdout).hexdigest()
        executable = "1" if mode == "100755" else "0"
        overall.update(f"{relative}\0{executable}\0{content}\0".encode())
        count += 1
    return overall.hexdigest() if count else None


# Summary: check candidate installation; Arguments: proof identity and skill digest
# Environment inputs: recorded installer output; Stdout: none; Stderr: none
# Exit classes: problems, entrypoint token, timestamp; Side effects: none
# Idempotency: stable proof; Cleanup: none
def _installed_candidate(
    name: str, proof: dict[str, Any], script: str, digest: str
) -> tuple[list[str], str | None, datetime | None]:
    """Check installer outputs and derive the installed command's public token.

    :param name: Proof file name for precise failures.
    :param proof: Parsed untrusted command evidence.
    :param script: Catalog command executable name.
    :param digest: Current committed skill tree digest.
    :returns: Problems, sanitized installed entrypoint, last installer time.
    """
    problems: list[str] = []
    installation = proof.get("installation")
    if not isinstance(installation, dict):
        return [f"{name}:installation_missing"], None, None
    destination = installation.get("destination")
    if not isinstance(destination, str) or not re.fullmatch(
        r"path:[0-9a-f]{12}", destination
    ):
        problems.append(f"{name}:installed_destination_invalid")
        destination = None
    entrypoint = (
        f"installed:{destination}/scripts/{script}" if destination else None
    )
    source: str | None = None
    fingerprint: str | None = None
    action: str | None = None
    previous: datetime | None = None
    for phase in ("plan", "apply", "readback"):
        step = installation.get(phase)
        label = f"{name}:installation:{phase}"
        if not isinstance(step, dict):
            problems.append(f"{label}:missing")
            continue
        captured = _time(step.get("captured_at"))
        if captured is None or (previous is not None and captured <= previous):
            problems.append(f"{label}:time_order_invalid")
        if captured is not None:
            previous = captured
        argv = step.get("command")
        if (
            not isinstance(argv, list)
            or not all(isinstance(item, str) and item for item in argv)
            or not any(item.endswith("tools/install_ci_skills.py") for item in argv)
        ):
            problems.append(f"{label}:installer_invocation_missing")
            argv = []
        if not _project_python(argv):
            problems.append(f"{label}:project_python_missing")
        if "--json" not in argv or (
            phase == "readback" and "--upgrade" not in argv
        ):
            problems.append(f"{label}:installer_flags_missing")
        if phase == "apply":
            if "--apply" not in argv or "--timeout" not in argv:
                problems.append(f"{label}:apply_flags_missing")
            if fingerprint is None or not any(
                option in argv
                and argv.index(option) + 1 < len(argv)
                and argv[argv.index(option) + 1] == fingerprint
                for option in ("--confirm-install", "--confirm-upgrade")
            ):
                problems.append(f"{label}:confirmation_mismatch")
        elif "--apply" in argv:
            problems.append(f"{label}:read_only_mode_invalid")
        if type(step.get("exit_code")) is not int or step["exit_code"] != 0:
            problems.append(f"{label}:exit_status_invalid")
        output = step.get("stdout")
        if not isinstance(output, dict) or output.get("kind") != "skill_install":
            problems.append(f"{label}:installer_output_missing")
            continue
        revision = output.get("revision")
        if not isinstance(revision, dict):
            revision = {}
        expected_status = "PASS" if phase == "apply" else "DRY_RUN"
        if output.get("status") != expected_status:
            problems.append(f"{label}:status_invalid")
        if (
            output.get("digest") != digest
            or output.get("source_revision") != proof.get("tested_revision")
            or revision.get("value") != proof.get("tested_revision")
            or revision.get("source") != "git_head"
            or revision.get("verified") is not True
            or output.get("destination") != destination
            or output.get("mutable_link") is not False
        ):
            problems.append(f"{label}:candidate_identity_mismatch")
        if (
            type(output.get("file_count")) is not int
            or output["file_count"] < 1
        ):
            problems.append(f"{label}:file_inventory_missing")
        if source is None:
            source = output.get("source")
            if not isinstance(source, str) or not re.fullmatch(
                r"path:[0-9a-f]{12}", source
            ):
                problems.append(f"{label}:source_token_invalid")
        elif output.get("source") != source:
            problems.append(f"{label}:source_mismatch")
        if action is None:
            action = output.get("action")
            if action not in ("install", "upgrade"):
                problems.append(f"{label}:action_invalid")
        elif phase != "readback" and output.get("action") != action:
            problems.append(f"{label}:action_mismatch")
        if phase != "readback" and ("--upgrade" in argv) != (action == "upgrade"):
            problems.append(f"{label}:mode_mismatch")
        if phase == "plan":
            fingerprint = output.get("fingerprint")
            if not isinstance(fingerprint, str) or re.fullmatch(
                r"[0-9a-f]{64}", fingerprint
            ) is None:
                problems.append(f"{label}:fingerprint_invalid")
                fingerprint = None
        elif phase == "apply" and output.get("fingerprint") != fingerprint:
            problems.append(f"{label}:fingerprint_mismatch")
        elif phase == "readback" and not isinstance(
            output.get("fingerprint"), str
        ):
            problems.append(f"{label}:fingerprint_missing")
        if phase == "readback" and output.get("already_installed") is not True:
            problems.append(f"{label}:installed_readback_missing")
    return problems, entrypoint, previous


# Summary: narrow one submitted step output; Arguments: step mapping
# Environment inputs: none; Stdout: none; Stderr: none; Exit classes: mapping
# Side effects: none; Idempotency: stable step; Cleanup: none
def _step_output(step: dict[str, Any]) -> dict[str, Any]:
    """Return a parsed output mapping or an empty invalid sentinel.

    :param step: Parsed submitted evidence step.
    :returns: Output mapping, or empty mapping for a malformed payload.
    """
    output = step.get("stdout")
    return output if isinstance(output, dict) else {}


# Summary: validate one command proof; Arguments: proof, catalog and authority facts
# Environment inputs: supplied clock and redactor; Stdout: none; Stderr: none
# Exit classes: problem list; Side effects: none; Idempotency: stable inputs
# Cleanup: none
def _check_one(
    name: str,
    proof: dict[str, Any],
    script: str,
    catalog: Any,
    digest: str,
    expected: dict[str, Any],
    access_sources: dict[str, dict[str, str]],
    now: datetime,
    redact: Callable[[Any], Any],
) -> list[str]:
    """Compare one submitted command proof with the catalog and expected target.

    :param name: Proof filename used in precise failure messages.
    :param proof: Untrusted parsed command proof.
    :param script: Catalog command filename.
    :param catalog: Imported command catalog module.
    :param digest: Current skill package digest.
    :param expected: Operator-selected live acceptance expectations.
    :param access_sources: Credential source tokens from accepted access receipts.
    :param now: UTC time used to judge receipt age.
    :param redact: Sanitizer used to detect raw secret-bearing proof values.
    :returns: Exact contract failures for this command proof.
    """
    problems: list[str] = []
    entry = catalog.COMMANDS.get(script) or catalog.NODE_LOCAL_COMMANDS.get(script)
    if (
        proof.get("schema_version") != "1.0"
        or proof.get("kind") != "command_live_proof"
    ):
        problems.append(f"{name}:schema_invalid")
    if proof.get("command") != script or proof.get("status") != "PASS":
        problems.append(f"{name}:command_or_status_mismatch")
    host = proof.get("execution_host")
    identity = proof.get("identity")
    if not isinstance(identity, dict):
        identity = {}
    credential_sources = proof.get("credential_sources")
    if not isinstance(credential_sources, dict):
        credential_sources = {}
    executor = next(
        (item for item in expected["executors"] if item.get("host") == host), None
    )
    if executor is None:
        problems.append(f"{name}:executor_not_declared")
    else:
        for authority in entry["requires"]:
            observed = identity.get(authority)
            if observed != executor.get("identities", {}).get(authority):
                problems.append(f"{name}:identity_mismatch:{authority}")
            source = credential_sources.get(authority)
            if not source or source != access_sources.get(host, {}).get(authority):
                problems.append(f"{name}:credential_source_mismatch:{authority}")
    skill = proof.get("skill")
    if not isinstance(skill, dict):
        skill = {}
    if skill.get("digest") != digest:
        problems.append(f"{name}:skill_digest_mismatch")
    revision = skill.get("revision")
    if not isinstance(revision, dict):
        revision = {}
    if proof.get("tested_revision") != revision.get("value") or not revision.get(
        "source"
    ):
        problems.append(f"{name}:revision_unverified")
    target = proof.get("target")
    if not isinstance(target, dict):
        problems.append(f"{name}:target_missing")
        target = {}
    for authority in entry["requires"]:
        selected = target.get(authority)
        required_target = expected.get("targets", {}).get(authority)
        if authority == "gitlab":
            if (
                not isinstance(selected, str)
                or not isinstance(required_target, str)
                or not selected.startswith(required_target + "/")
            ):
                problems.append(f"{name}:target_mismatch:gitlab")
        elif selected != required_target:
            problems.append(f"{name}:target_mismatch:{authority}")
    captured = _time(proof.get("captured_at"))
    if (
        captured is None
        or captured > now
        or (now - captured).days > expected["max_receipt_age_days"]
    ):
        problems.append(f"{name}:capture_time_invalid")
    if redact(proof) != proof:
        problems.append(f"{name}:not_sanitized")
    if re.search(
        r"(?<![A-Za-z0-9])/(?:Users|home|private|var|etc)/",
        json.dumps(proof, sort_keys=True),
    ):
        problems.append(f"{name}:private_host_path_exposed")
    install_problems, installed_entrypoint, installed_at = _installed_candidate(
        name, proof, script, digest
    )
    problems.extend(install_problems)
    steps = proof.get("steps")
    expected_steps = _steps_for(script, catalog)
    if not isinstance(steps, list) or len(steps) != len(expected_steps):
        problems.append(f"{name}:step_inventory_mismatch")
        return problems
    previous: datetime | None = None
    for index, ((operation, phase), step) in enumerate(zip(expected_steps, steps)):
        label = f"{name}:step_{index}:{operation or 'read'}:{phase}"
        if not isinstance(step, dict) or (step.get("operation"), step.get("phase")) != (
            operation,
            phase,
        ):
            problems.append(f"{label}:phase_mismatch")
            continue
        timestamp = _time(step.get("captured_at"))
        if timestamp is None or (previous is not None and timestamp <= previous):
            problems.append(f"{label}:time_order_invalid")
        elif installed_at is None or timestamp <= installed_at:
            problems.append(f"{label}:before_install_readback")
        elif captured is not None and timestamp > captured:
            problems.append(f"{label}:after_proof_capture")
        if timestamp is not None:
            previous = timestamp
        argv = step.get("command")
        invokes_skill = False
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(arg, str) or not arg for arg in argv)
        ):
            problems.append(f"{label}:invocation_missing")
        else:
            invokes_skill = installed_entrypoint in argv
            if invokes_skill and not _project_python(argv):
                problems.append(f"{label}:project_python_missing")
            if (
                phase
                in {
                    "action",
                    "plan",
                    "apply",
                    "no_op_repeat",
                    "repeat_apply",
                    "safe_repeat_refusal",
                }
                or (not entry.get("mutates") and phase in {"before", "after"})
            ) and not invokes_skill:
                problems.append(f"{label}:real_entrypoint_missing")
            if invokes_skill and (
                "--revision" not in argv
                or argv.index("--revision") + 1 >= len(argv)
                or argv[argv.index("--revision") + 1] != proof.get("tested_revision")
            ):
                problems.append(f"{label}:candidate_revision_arg_missing")
        if (
            phase
            in {
                "action",
                "apply",
                "no_op_repeat",
                "repeat_apply",
                "safe_repeat_refusal",
                "after",
                "repeat_get",
                "final_read",
            }
            and isinstance(argv, list)
            and "--dry-run" in argv
        ):
            problems.append(f"{label}:dry_run_not_live")
        if phase in {
            "apply",
            "no_op_repeat",
            "repeat_apply",
            "safe_repeat_refusal",
        } and not {
            "--apply",
            "--confirm-plan",
        } <= set(argv or []):
            problems.append(f"{label}:apply_flags_missing")
        if (
            entry.get("mutates")
            and phase in {"after", "repeat_get", "final_read"}
            and invokes_skill
        ):
            problems.append(f"{label}:readback_not_independent")
        code = step.get("exit_code")
        expected_code = 2 if phase == "safe_repeat_refusal" else 0
        if type(code) is not int or code != expected_code:
            problems.append(f"{label}:exit_status_invalid")
        output = step.get("stdout")
        allow_body = bool(entry.get("mutates")) and phase in {
            "before",
            "after",
            "repeat_get",
            "cleanup",
            "final_read",
        }
        if not _live_body(output, allow_body=allow_body):
            problems.append(f"{label}:live_output_missing")
            continue
        if output.get("status") in ("DRY_RUN", "PLANNED", "BLOCKED") and phase not in {
            "plan",
            "before",
            "final_read",
            "safe_repeat_refusal",
        }:
            problems.append(f"{label}:not_live_success")
        if not entry.get("mutates") or phase in {
            "apply",
            "no_op_repeat",
            "repeat_apply",
            "safe_repeat_refusal",
        }:
            access = output.get("access")
            if not isinstance(access, dict):
                access = {}
            if output.get("kind") != entry["kind"]:
                problems.append(f"{label}:output_kind_mismatch")
            observed_skill = output.get("skill") or access.get("skill")
            if not isinstance(observed_skill, dict):
                observed_skill = {}
            if observed_skill.get("digest") != digest:
                problems.append(f"{label}:output_digest_mismatch")
            if output.get("execution_host", access.get("execution_host")) != host:
                problems.append(f"{label}:output_host_mismatch")
            for authority in entry["requires"]:
                selected_source = credential_sources.get(authority)
                observed_source = (
                    output.get("credential_sources")
                    or access.get("credential_sources")
                    or {}
                )
                if not isinstance(observed_source, dict):
                    observed_source = {}
                observed_source = observed_source.get(authority)
                if authority == "gitlab":
                    observed_source = output.get(
                        "credential_source",
                        access.get("credential_source", observed_source),
                    )
                if observed_source != selected_source:
                    problems.append(f"{label}:output_auth_source_mismatch:{authority}")
            if "gitlab" in entry["requires"] and output.get("origin") not in (
                None,
                expected["targets"]["gitlab"],
            ):
                problems.append(f"{label}:output_origin_mismatch")
            if (
                script == "access_check.py"
                and output.get("targets") != expected["targets"]
            ):
                problems.append(f"{label}:output_targets_mismatch")
            output_time = _time(output.get("captured_at"))
            if output_time and timestamp and output_time > timestamp:
                problems.append(f"{label}:output_time_mismatch")
        if entry.get("mutates") and phase in {
            "plan", "apply", "no_op_repeat", "repeat_apply", "safe_repeat_refusal"
        }:
            declared_targets = [
                item
                for item in expected.get("gitlab_receipts", [])
                if item.get("kind") == entry["kind"]
                and item.get("operation") == operation
            ]
            if declared_targets:
                selected_target = output.get("target")
                if not isinstance(selected_target, dict):
                    selected_target = {}
                if not any(
                    selected_target.get("kind") == item["target_kind"]
                    and selected_target.get("reference") == item["target_path"]
                    for item in declared_targets
                ):
                    problems.append(f"{label}:selected_target_mismatch")
                if phase in {"apply", "no_op_repeat", "repeat_apply"}:
                    verified_target = output.get("verified_target")
                    if not isinstance(verified_target, dict):
                        verified_target = {}
                    if not any(
                        verified_target.get("id") == item["target_id"]
                        and verified_target.get("full_path") == item["target_path"]
                        for item in declared_targets
                    ):
                        problems.append(f"{label}:verified_target_mismatch")
        if (
            phase == "apply"
            and script != "k8s_verify_mtu_consistency.py"
            and (
                output.get("result_action") != "APPLIED"
                or output.get("mutated") is not True
            )
        ):
            problems.append(f"{label}:apply_not_proven")
        if (
            script == "k8s_verify_mtu_consistency.py"
            and phase
            in {
                "apply",
                "repeat_apply",
            }
            and (
                output.get("status") != "PASS"
                or not output.get("records")
                or not isinstance(output.get("cleanup"), dict)
                or output["cleanup"].get("verified") is not True
                or output["cleanup"].get("remaining_pods") != []
            )
        ):
            problems.append(f"{label}:mtu_read_or_cleanup_not_proven")
        if phase == "no_op_repeat" and (
            output.get("result_action") != "NO_OP" or output.get("mutated") is not False
        ):
            problems.append(f"{label}:repeat_not_no_op")
        if phase == "safe_repeat_refusal" and (
            code != 2
            or output.get("status") != "BLOCKED"
            or not any(
                item.get("reason")
                == "runner_description_exists_verify_and_remove_record_before_retry"
                for item in output.get("errors", [])
                if isinstance(item, dict)
            )
        ):
            problems.append(f"{label}:unsafe_repeat_refusal")
        if (
            phase in {"repeat_get", "after", "final_read"}
            and output.get("verified") is True
            and len(output) <= 3
        ):
            problems.append(f"{label}:self_certified_readback")
    if entry.get("mutates"):
        by_operation_phase = {
            (step.get("operation"), step.get("phase")): step
            for step in steps
            if isinstance(step, dict)
        }
        operations = sorted(entry.get("subcommands") or {"apply": {}})
        for operation in operations:
            phases = (
                "before",
                "plan",
                "apply",
                "after",
                "safe_repeat_refusal"
                if script == "gitlab_runner.py" and operation == "create"
                else "repeat_apply"
                if script == "k8s_verify_mtu_consistency.py"
                else "no_op_repeat",
                "repeat_get",
                "cleanup",
                "final_read",
            )
            block = [by_operation_phase.get((operation, phase)) for phase in phases]
            if any(not isinstance(step, dict) for step in block):
                continue
            before = _step_output(block[0])
            apply = _step_output(block[2])
            after = _step_output(block[3])
            repeat = _step_output(block[4])
            repeat_get = _step_output(block[5])
            cleanup = _step_output(block[6])
            final = _step_output(block[7])
            if script == "k8s_verify_mtu_consistency.py":
                marker = apply.get("debug_marker")
                if (
                    not marker
                    or repeat.get("plan_digest") != apply.get("plan_digest")
                    or repeat.get("planned_nodes") != apply.get("planned_nodes")
                    or cleanup.get("remaining_pods") != []
                    or final.get("items") != []
                    or final.get("debug_marker") != marker
                ):
                    problems.append(f"{name}:{operation}:temporary_pods_not_absent")
                continue
            applied_id = _resource_id(apply.get("readback"))
            id_key = applied_id[0] if applied_id else None
            after_id = _resource_id(after, id_key)
            refused = script == "gitlab_runner.py" and operation == "create"
            repeated_id = (
                applied_id if refused else _resource_id(repeat.get("readback"), id_key)
            )
            repeat_get_id = _resource_id(repeat_get, id_key)
            if script != "k8s_verify_mtu_consistency.py" and (
                not applied_id
                or applied_id != after_id
                or applied_id != repeated_id
                or applied_id != repeat_get_id
            ):
                problems.append(f"{name}:{operation}:independent_resource_mismatch")
            if not apply.get("plan_digest") or (
                not refused and apply.get("plan_digest") != repeat.get("plan_digest")
            ):
                problems.append(f"{name}:{operation}:repeat_plan_mismatch")
            if block[6].get("exit_code") != 0 or not _live_body(final):
                problems.append(f"{name}:{operation}:cleanup_or_final_read_missing")
            before_state = _observed_state(before)
            after_state = _observed_state(after)
            repeat_state = _observed_state(repeat_get)
            final_state = _observed_state(final)
            if before_state is None or before_state != final_state:
                problems.append(f"{name}:{operation}:cleanup_state_not_restored")
            if (
                after_state is None
                or after_state["http_status"] != 200
                or repeat_state is None
                or repeat_state["http_status"] != 200
            ):
                problems.append(f"{name}:{operation}:independent_get_not_successful")
            if not _live_body(cleanup):
                problems.append(f"{name}:{operation}:cleanup_output_missing")
    return problems


# Summary: validate the closed command proof inventory; Arguments: paths and facts
# Environment inputs: catalog, Git objects, submitted JSON; Stdout: none
# Stderr: none; Exit classes: result map; Side effects: read-only
# Idempotency: stable files and clock; Cleanup: file handles close
def evaluate(
    directory: Path,
    expected: dict[str, Any],
    receipts: dict[str, Any],
    skill_root: Path,
    *,
    now: datetime,
    redact: Callable[[Any], Any],
) -> dict[str, Any]:
    """Check all required command proofs and the executed skill tree identity.

    :param directory: Directory holding sanitized per-command JSON proofs.
    :param expected: Operator-selected live acceptance expectations.
    :param receipts: Existing live access receipts for shared credential sources.
    :param skill_root: Current skill package in the checkout.
    :param now: UTC time used to judge receipt age.
    :param redact: Sanitizer used to reject raw sensitive values.
    :returns: PASS or BLOCKED result with accepted names and exact problems.
    """
    from core import catalog
    from core.provenance import tree_digest

    required = set(catalog.COMMANDS) | set(catalog.NODE_LOCAL_COMMANDS)
    proofs: dict[str, Any] = {}
    if directory.is_dir():
        for path in directory.glob("*.json"):
            try:
                proofs[path.name] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                proofs[path.name] = None
    expected_names = {script.removesuffix(".py") + ".json" for script in required}
    problems = [
        f"command_proof_missing:{name}" for name in sorted(expected_names - set(proofs))
    ]
    problems.extend(
        f"command_proof_undeclared:{name}"
        for name in sorted(set(proofs) - expected_names)
    )
    if proofs and not any(
        isinstance(proof, dict)
        and isinstance(proof.get("installation"), dict)
        and isinstance(proof["installation"].get("apply"), dict)
        and isinstance(proof["installation"]["apply"].get("stdout"), dict)
        and proof["installation"]["apply"]["stdout"].get("status") == "PASS"
        and proof["installation"]["apply"]["stdout"].get("already_installed")
        is not True
        for proof in proofs.values()
    ):
        problems.append("candidate_install_not_observed")
    access_sources = {
        receipt["execution_host"]: receipt.get("credential_sources", {})
        for receipt in receipts.values()
        if isinstance(receipt, dict) and receipt.get("kind") == "access_check"
    }
    digest = tree_digest(skill_root)["digest"]
    revisions: dict[str, str | None] = {}
    accepted: list[str] = []
    for script in sorted(required):
        name = script.removesuffix(".py") + ".json"
        if name not in proofs:
            continue
        proof = proofs[name]
        if not isinstance(proof, dict):
            problems.append(f"{name}:proof_invalid")
            continue
        found = _check_one(
            name, proof, script, catalog, digest, expected, access_sources, now, redact
        )
        revision = proof.get("tested_revision")
        if isinstance(revision, str):
            if revision not in revisions:
                revisions[revision] = _committed_skill_digest(skill_root, revision)
            if revisions[revision] != digest:
                found.append(f"{name}:tested_commit_skill_tree_mismatch")
        else:
            found.append(f"{name}:tested_commit_missing")
        problems.extend(found)
        if not found:
            accepted.append(name)
    return {
        "status": "PASS" if not problems else "BLOCKED",
        "accepted": accepted,
        "problems": sorted(problems),
    }
