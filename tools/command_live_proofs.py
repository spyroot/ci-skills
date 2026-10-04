"""Validate actual before/action/after output for every installed Python command."""

from __future__ import annotations

import json
import hashlib
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def _time(value: Any) -> datetime | None:
    """Parse one UTC instant; no environment, output, side effect, or cleanup."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _live_body(value: Any) -> bool:
    """Reject flags and digests in place of a live response body; pure/read-only."""
    if not isinstance(value, dict) or not value:
        return False
    assertions = {"verified", "access_proven", "readback_sha256", "status", "hash"}
    if not set(value) - assertions:
        return False
    return bool(
        {
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
            "body",
        }
        & set(value)
    )


def _resource_id(value: Any, key: str | None = None) -> tuple[str, Any] | None:
    """Read a server resource identity, not a caller assertion; pure/read-only."""
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("body"), dict):
        value = value["body"]
    for candidate in (key,) if key else ("id", "iid", "slug"):
        if value.get(candidate) not in (None, ""):
            return candidate, value[candidate]
    return None


def _observed_state(value: Any) -> dict[str, Any] | None:
    """Return the full live GET state, omitting only server update timestamps.

    Args: parsed independent GET output. Environment/output/side effects: none.
    Exit classes: normalized state or None. Cleanup/idempotency: not applicable.
    """
    if not isinstance(value, dict) or type(value.get("http_status")) is not int:
        return None
    body = value.get("body")
    if not isinstance(body, dict) or not body:
        return None
    return {
        "http_status": value["http_status"],
        "body": {
            key: item
            for key, item in body.items()
            if key not in {"updated_at", "last_activity_at"}
        },
    }


def _steps_for(script: str, catalog: Any) -> list[tuple[str | None, str]]:
    """Derive the complete phase sequence from the catalog; pure/read-only."""
    entry = catalog.COMMANDS.get(script) or catalog.NODE_LOCAL_COMMANDS.get(script)
    if not entry.get("mutates"):
        return [(None, phase) for phase in ("before", "action", "after")]
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


def _committed_skill_digest(skill_root: Path, revision: str) -> str | None:
    """Hash exact committed skill bytes; Git reads only, no output or cleanup.

    Args: repository skill path and immutable commit. Environment: Git objects.
    Stdout/stderr: none. Exit classes: digest or None. Side effects: none.
    Idempotency: immutable commit bytes give the same digest.
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
    """Inspect one proof; args are declared evidence, returns failures, no I/O.

    Environment/stdout/stderr: none. Exit classes: list, never process exit.
    Side effects/cleanup: none. Idempotency: same evidence gives same result.
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
    executor = next(
        (item for item in expected["executors"] if item.get("host") == host), None
    )
    if executor is None:
        problems.append(f"{name}:executor_not_declared")
    else:
        for authority in entry["requires"]:
            observed = proof.get("identity", {}).get(authority)
            if observed != executor.get("identities", {}).get(authority):
                problems.append(f"{name}:identity_mismatch:{authority}")
            source = proof.get("credential_sources", {}).get(authority)
            if not source or source != access_sources.get(host, {}).get(authority):
                problems.append(f"{name}:credential_source_mismatch:{authority}")
    skill = proof.get("skill") or {}
    if skill.get("digest") != digest:
        problems.append(f"{name}:skill_digest_mismatch")
    revision = skill.get("revision") or {}
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
            invokes_skill = any(
                item.endswith("/" + script) or item == script for item in argv
            )
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
                and not invokes_skill
            ):
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
        if type(code) is not int or (
            code != 0 and phase not in {"before", "final_read", "safe_repeat_refusal"}
        ):
            problems.append(f"{label}:exit_status_invalid")
        output = step.get("stdout")
        if not _live_body(output):
            problems.append(f"{label}:live_output_missing")
            continue
        if output.get("status") in {"DRY_RUN", "PLANNED", "BLOCKED"} and phase not in {
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
            access = output.get("access") or {}
            if output.get("kind") != entry["kind"]:
                problems.append(f"{label}:output_kind_mismatch")
            if (output.get("skill") or access.get("skill") or {}).get(
                "digest"
            ) != digest:
                problems.append(f"{label}:output_digest_mismatch")
            if output.get("execution_host", access.get("execution_host")) != host:
                problems.append(f"{label}:output_host_mismatch")
            for authority in entry["requires"]:
                selected_source = proof.get("credential_sources", {}).get(authority)
                observed_source = (
                    output.get("credential_sources")
                    or access.get("credential_sources")
                    or {}
                ).get(authority)
                if authority == "gitlab":
                    observed_source = output.get("credential_source", observed_source)
                if observed_source is not None and observed_source != selected_source:
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
                or output.get("cleanup", {}).get("verified") is not True
                or output.get("cleanup", {}).get("remaining_pods") != []
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
        for offset in range(0, len(steps), 8):
            block = steps[offset : offset + 8]
            if any(not isinstance(step, dict) for step in block):
                continue
            before = block[0].get("stdout") or {}
            apply = block[2].get("stdout") or {}
            after = block[3].get("stdout") or {}
            repeat = block[4].get("stdout") or {}
            repeat_get = block[5].get("stdout") or {}
            cleanup = block[6].get("stdout") or {}
            final = block[7].get("stdout") or {}
            operation = block[0].get("operation")
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
            final_state = _observed_state(final)
            if before_state is None or before_state != final_state:
                problems.append(f"{name}:{operation}:cleanup_state_not_restored")
            if not _live_body(cleanup):
                problems.append(f"{name}:{operation}:cleanup_output_missing")
    return problems


def evaluate(
    directory: Path,
    expected: dict[str, Any],
    receipts: dict[str, Any],
    skill_root: Path,
    *,
    now: datetime,
    redact: Callable[[Any], Any],
) -> dict[str, Any]:
    """Check the catalog-closed proof inventory; read-only, deterministic.

    Args: proof directory, expectations, old receipts, skill, clock, redactor.
    Environment/stdout/stderr: none. Exit classes: result status.
    Side effects/cleanup: none. Idempotency: unchanged files give same result.
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
