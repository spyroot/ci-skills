"""Read node-local Cilium and Ceph evidence without mutating the node."""

from __future__ import annotations

import json
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from .cilium import valid_health
from .report import report
from .runtime import CommandResult, error_class, run_command, run_command_tail, sanitize
from .status import BLOCKED, PARTIAL, PASS, UNKNOWN

CEPH_PATTERN = re.compile(r"libceph|rbd|ceph")
CEPH_ACTIONS = (
    (
        re.compile(r"blocklist|blacklist", re.IGNORECASE),
        "client_blocklisted",
        "inspect_ceph_client_blocklist",
    ),
    (
        re.compile(
            r"auth[^\n]*(?:fail|error|denied)|permission denied|access denied",
            re.IGNORECASE,
        ),
        "authentication",
        "inspect_ceph_client_auth",
    ),
    (
        re.compile(r"timed? out|unreachable|connection refused", re.IGNORECASE),
        "connectivity",
        "inspect_ceph_monitor_network",
    ),
    (
        re.compile(
            r"i/o error|input/output error|read-only|failed request", re.IGNORECASE
        ),
        "io_error",
        "inspect_rbd_and_ceph_health",
    ),
)
JOURNAL_SINCE = "3 minutes ago"
JOURNAL_MAX_LINES = 200
JOURNAL_MAX_BYTES = 65536


def _valid_daemon_status(value: Any) -> bool:
    """Require the Cilium component state exposed by the status API."""
    if not isinstance(value, dict):
        return False
    cilium = value.get("cilium")
    return (
        isinstance(cilium, dict)
        and isinstance(cilium.get("state"), str)
        and bool(cilium["state"].strip())
    )


def _valid_node_health(value: Any) -> bool:
    if not valid_health(value):
        return False
    local = value.get("local")
    return (
        isinstance(local, dict)
        and isinstance(local.get("name"), str)
        and bool(local["name"].strip())
    )


def _node_report(
    kind: str, records: list[dict[str, Any]], errors: list[dict[str, str]]
) -> dict[str, Any]:
    result = report(kind, socket.getfqdn(), {}, records, errors)
    result["execution_host"] = socket.getfqdn()
    return result


def _json_result(result: CommandResult, *, source: str) -> Any:
    if result.returncode:
        raise ValueError(f"{source}:{error_class(result)}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source}:invalid_json") from exc


def _containers(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("containers"), list):
        raise TypeError("crictl:invalid_container_response")
    containers = payload["containers"]
    if any(not isinstance(item, dict) for item in containers):
        raise TypeError("crictl:invalid_container_response")
    return containers


def _agent_containers(payload: Any) -> list[dict[str, str]]:
    selected = []
    for item in _containers(payload):
        metadata = item.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("name") != "cilium-agent":
            continue
        identifier = item.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(
            r"[0-9a-fA-F]{12,64}", identifier
        ):
            raise ValueError("crictl:invalid_agent_id")
        selected.append({"id": identifier, "state": str(item.get("state", ""))})
    return selected


def collect_cilium_node() -> dict[str, Any]:
    """Read the one local CRI Cilium agent, daemon status, and health JSON."""
    errors: list[dict[str, str]] = []
    records: list[dict[str, Any]] = []
    result = _node_report("cilium_node", records, errors)
    result["commands"] = [
        ["sudo", "-n", "crictl", "ps", "--output", "json"],
        [
            "sudo",
            "-n",
            "crictl",
            "exec",
            "--sync",
            "--timeout",
            "30",
            "<agent-id>",
            "cilium-dbg",
            "status",
            "--verbose",
            "--output",
            "json",
        ],
        [
            "sudo",
            "-n",
            "crictl",
            "exec",
            "--sync",
            "--timeout",
            "30",
            "<agent-id>",
            "cilium-health",
            "status",
            "--verbose",
            "--output",
            "json",
        ],
    ]
    try:
        agents = _agent_containers(
            _json_result(
                run_command(["sudo", "-n", "crictl", "ps", "--output", "json"]),
                source="crictl_ps",
            )
        )
    except (TypeError, ValueError) as exc:
        errors.append({"source": "crictl", "reason": str(exc)})
        result["status"] = BLOCKED
        result["summary"]["error_count"] = len(errors)
        result["safe_next_step"] = (
            "Check noninteractive sudo and the local CRI endpoint."
        )
        return result
    if not agents:
        stopped = run_command(
            ["sudo", "-n", "crictl", "ps", "-a", "--name", "cilium", "--output", "json"]
        )
        try:
            result["cilium_containers"] = [
                {
                    "id": item.get("id"),
                    "name": item["metadata"]["name"],
                    "state": item.get("state"),
                }
                for item in _containers(_json_result(stopped, source="crictl_ps_all"))
                if isinstance(item.get("metadata"), dict)
                and isinstance(item["metadata"].get("name"), str)
                and "cilium" in item["metadata"]["name"]
            ]
        except (TypeError, ValueError) as exc:
            errors.append({"source": "crictl_ps_all", "reason": str(exc)})
        errors.append({"source": "cilium-agent", "reason": "agent_not_running"})
        result["status"] = BLOCKED
        result["summary"]["error_count"] = len(errors)
        result["safe_next_step"] = (
            "Inspect the Cilium agent container and node runtime state."
        )
        return result
    if len(agents) != 1:
        errors.append({"source": "cilium-agent", "reason": "multiple_running_agents"})
        result["status"] = BLOCKED
        result["summary"]["error_count"] = len(errors)
        result["agent_count"] = len(agents)
        result["safe_next_step"] = (
            "Identify the intended local Cilium agent before collecting status."
        )
        return result
    agent = agents[0]
    identifier = agent["id"]
    commands = {
        "daemon": [
            "sudo",
            "-n",
            "crictl",
            "exec",
            "--sync",
            "--timeout",
            "30",
            identifier,
            "cilium-dbg",
            "status",
            "--verbose",
            "--output",
            "json",
        ],
        "health": [
            "sudo",
            "-n",
            "crictl",
            "exec",
            "--sync",
            "--timeout",
            "30",
            identifier,
            "cilium-health",
            "status",
            "--verbose",
            "--output",
            "json",
        ],
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            name: pool.submit(run_command, command, timeout=35)
            for name, command in commands.items()
        }
        outputs = {name: future.result() for name, future in futures.items()}
    row: dict[str, Any] = {
        "name": "cilium-agent",
        "container_id": identifier,
        "status": PASS,
    }
    for name, command_result in outputs.items():
        try:
            value = _json_result(command_result, source=name)
            valid = (
                _valid_daemon_status(value)
                if name == "daemon"
                else _valid_node_health(value)
            )
            if not valid:
                raise ValueError(f"{name}:invalid_response")
            row[name] = value
        except (TypeError, ValueError) as exc:
            row["status"] = UNKNOWN
            errors.append({"source": name, "reason": str(exc)})
    records.append(row)
    result["summary"] = {"record_count": 1, "error_count": len(errors)}
    result["status"] = PARTIAL if errors else PASS
    result["safe_next_step"] = (
        "Inspect the failed Cilium command on this node." if errors else None
    )
    return result


def classify_ceph_message(message: str, priority: int) -> tuple[str, str | None]:
    """Classify a kernel message without claiming a root cause."""
    for pattern, category, action in CEPH_ACTIONS:
        if pattern.search(message):
            return category, action
    if priority <= 3:
        return "unclassified_error", "review_ceph_kernel_event"
    return "observation", None


def collect_ceph_kernel() -> dict[str, Any]:
    """Read recent kernel Ceph/RBD journal entries and propose read-only actions."""
    command = [
        "sudo",
        "-n",
        "journalctl",
        "-k",
        "--utc",
        "--since",
        JOURNAL_SINCE,
        "--no-pager",
        "--output=json",
        "--grep=libceph|rbd|ceph",
        f"--lines={JOURNAL_MAX_LINES}",
    ]
    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    result = _node_report("ceph_kernel", records, errors)
    result["filters"] = {"since": JOURNAL_SINCE, "message_regex": CEPH_PATTERN.pattern}
    result["command"] = command
    result["limits"] = {"lines": JOURNAL_MAX_LINES, "bytes": JOURNAL_MAX_BYTES}
    response = run_command_tail(
        command,
        timeout=25,
        max_bytes=JOURNAL_MAX_BYTES,
        max_lines=JOURNAL_MAX_LINES,
    )
    if (
        response.returncode == 1
        and response.stdout.strip() in {"", "-- No entries --"}
        and not response.stderr.strip()
    ):
        result["truncated"] = False
        result["actions"] = []
        return result
    if response.returncode:
        errors.append({"source": "journalctl", "reason": error_class(response)})
        result["status"] = BLOCKED
        result["summary"]["error_count"] = 1
        result["actions"] = []
        result["safe_next_step"] = (
            "Check noninteractive sudo and kernel journal access on this node."
        )
        return result
    else:
        result["truncated"] = (
            len(response.stdout.encode("utf-8")) >= JOURNAL_MAX_BYTES - 4
            or len(response.stdout.splitlines()) >= JOURNAL_MAX_LINES
        )
        for line in response.stdout.splitlines():
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise TypeError("invalid_event")
                message = event.get("MESSAGE")
                stamp = event.get("__REALTIME_TIMESTAMP")
                if (
                    not isinstance(message, str)
                    or not isinstance(stamp, str)
                    or not stamp.isdecimal()
                ):
                    raise ValueError("invalid_event")
                if not CEPH_PATTERN.search(message):
                    continue
                raw_priority = event.get("PRIORITY", "6")
                if not str(raw_priority).isdecimal() or not 0 <= int(raw_priority) <= 7:
                    raise ValueError("invalid_priority")
                priority = int(raw_priority)
                category, action = classify_ceph_message(message, priority)
                records.append(
                    {
                        "timestamp": datetime.fromtimestamp(
                            int(stamp) / 1_000_000, timezone.utc
                        ).isoformat(),
                        "priority": priority,
                        "message": sanitize(message, 1000),
                        "classification": category,
                        "action": action,
                    }
                )
            except (json.JSONDecodeError, OverflowError, TypeError, ValueError) as exc:
                errors.append(
                    {
                        "source": "journalctl",
                        "reason": f"invalid_journal_event:{type(exc).__name__}",
                    }
                )
    result["summary"] = {"record_count": len(records), "error_count": len(errors)}
    result["status"] = PARTIAL if errors or result["truncated"] else PASS
    result["actions"] = sorted({row["action"] for row in records if row["action"]})
    if errors or result["truncated"]:
        result["safe_next_step"] = (
            "Review bounded journal evidence on the selected node."
        )
    return result
