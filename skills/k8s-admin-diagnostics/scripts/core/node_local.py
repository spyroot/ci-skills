"""Parse Cilium and host-journal evidence read from existing Kubernetes Pods."""

from __future__ import annotations

import json
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from .cilium import daemon_findings, health_detail, health_findings, valid_health
from .node_pod import NodePodReader
from .report import report
from .runtime import CommandResult, error_class, sanitize
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
CEPH_CLASSIFICATIONS = tuple(
    sorted({category for _pattern, category, _action in CEPH_ACTIONS})
    + ["observation", "unclassified_error"]
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
    kind: str,
    reader: NodePodReader,
    records: list[dict[str, Any]],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    result = report(kind, reader.evidence()["node"], {}, records, errors)
    result["execution_host"] = socket.getfqdn()
    result["selected_pod"] = reader.evidence()
    return result


def _json_result(result: CommandResult, *, source: str) -> Any:
    if result.returncode:
        raise ValueError(f"{source}:{error_class(result)}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source}:invalid_json") from exc


def collect_cilium_node(reader: NodePodReader) -> dict[str, Any]:
    """Execute both Cilium JSON diagnostics in the verified existing Pod."""
    errors: list[dict[str, str]] = []
    records: list[dict[str, Any]] = []
    result = _node_report("cilium_node", reader, records, errors)
    commands = {
        "daemon": ("cilium-dbg", "status", "--verbose", "--output", "json"),
        "health": ("cilium-health", "status", "--verbose", "--output", "json"),
    }
    result["commands"] = [reader.command(*command) for command in commands.values()]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            name: pool.submit(reader.run, *command, timeout=35)
            for name, command in commands.items()
        }
        outputs = {name: future.result() for name, future in futures.items()}
    row: dict[str, Any] = {
        "name": reader.name,
        "pod_uid": reader.uid,
        "status": PASS,
        "findings": [],
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
            if name == "daemon":
                row["daemon"] = {
                    "components": {
                        component: sanitize(data.get("state", ""), 80)
                        for component, data in value.items()
                        if isinstance(data, dict) and isinstance(data.get("state"), str)
                    }
                }
            else:
                row["health"] = health_detail(value)
            findings = (
                daemon_findings(value) if name == "daemon" else health_findings(value)
            )
            row["findings"].extend(findings[:100])
            if len(findings) > 100:
                row["findings_truncated"] = True
                errors.append({"source": name, "reason": "findings_truncated"})
        except (TypeError, ValueError) as exc:
            row["status"] = UNKNOWN
            errors.append({"source": name, "reason": str(exc)})
    records.append(row)
    result["summary"] = {"record_count": 1, "error_count": len(errors)}
    result["status"] = PARTIAL if errors else PASS
    result["safe_next_step"] = (
        "Inspect the failed Cilium command on this node."
        if row["status"] == UNKNOWN
        else "Inspect the reported Cilium component and peer findings."
        if row["findings"]
        else None
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


def collect_ceph_kernel(reader: NodePodReader, directory: str) -> dict[str, Any]:
    """Read recent kernel Ceph/RBD journal entries and propose read-only actions."""
    journal_access = reader.run_tail(
        "journalctl",
        f"--directory={directory}",
        "--list-boots",
        "--no-pager",
        timeout=20,
        max_bytes=4096,
        max_lines=20,
    )
    command = [
        "journalctl",
        "-k",
        f"--directory={directory}",
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
    result = _node_report("ceph_kernel", reader, records, errors)
    result["filters"] = {"since": JOURNAL_SINCE, "message_regex": CEPH_PATTERN.pattern}
    result["command"] = reader.command(*command)
    result["limits"] = {"lines": JOURNAL_MAX_LINES, "bytes": JOURNAL_MAX_BYTES}
    if journal_access.returncode:
        errors.append(
            {"source": "journal_access", "reason": error_class(journal_access)}
        )
        result["status"] = BLOCKED
        result["summary"]["error_count"] = 1
        result["actions"] = []
        result["safe_next_step"] = (
            "Verify the selected Pod can read the mounted host journal directory."
        )
        return result
    response = reader.run_tail(
        *command,
        timeout=25,
        max_bytes=JOURNAL_MAX_BYTES,
        max_lines=JOURNAL_MAX_LINES,
    )
    if (
        response.returncode == 1
        and response.stdout.strip() in {"", "-- No entries --"}
        and response.stderr.strip() in {"", "command terminated with exit code 1"}
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
            "Check journal access inside the selected existing Pod."
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
