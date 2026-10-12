"""Parse Cilium and host-journal evidence read from existing Kubernetes Pods.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import re
import socket
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Optional

from lib.python.core.cli_api import response_decoder
from lib.python.core.json_types import JSONObject, JSONValue
from lib.python.core.kubernetes.cilium import (
    daemon_findings,
    health_detail,
    health_findings,
    valid_daemon_status,
    valid_node_health,
)
from lib.python.core.kubernetes.node_pod import NodePodReader
from lib.python.core.report import report
from lib.python.core.runtime import CommandResult, error_class, sanitize
from lib.python.sys.status import BLOCKED, PARTIAL, PASS, UNKNOWN

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


def _node_report(
    kind: str,
    reader: NodePodReader,
    records: MutableSequence[JSONObject],
    errors: MutableSequence[Mapping[str, str]],
) -> MutableMapping[str, JSONValue]:
    """Start a report tied to an existing selected Pod and host.

    :param kind: Diagnostic report kind.
    :param reader: Verified Pod reader supplying node and Pod identity.
    :param records: Mutable record list shared with the collector.
    :param errors: Mutable error list shared with the collector.
    :returns: Report with execution host and selected Pod evidence attached.
    """
    result = report(kind, reader.evidence()["node"], {}, records, errors)
    result["execution_host"] = socket.getfqdn()
    result["selected_pod"] = reader.evidence()
    return result


def _json_result(result: CommandResult, source: str) -> JSONValue:
    """Decode one successful node command result as JSON.

    :param result: Native command result from the selected Pod.
    :param source: Command name used in a failure reason.
    :returns: Decoded JSON value.
    :raises ValueError: The command failed or stdout is malformed, nonfinite,
        or too deeply nested to decode safely.
    """
    if result.returncode:
        raise ValueError(f"{source}:{error_class(result)}")
    try:
        return response_decoder().decode(result.stdout)
    except (ValueError, RecursionError) as exc:
        raise ValueError(f"{source}:invalid_json") from exc


def collect_cilium_node(reader: NodePodReader) -> MutableMapping[str, JSONValue]:
    """Read daemon status and health inside one verified Cilium Pod.

    :param reader: Reader bound to the exact existing Pod identity.
    :returns: Node report with component states and bounded findings; failures
        appear as errors and ``PARTIAL`` status.
    :raises NodePodError: If the selected Pod identity or container changes.
    :raises TargetError: If bound kubeconfig sources change before a query.
    :raises OSError: If a native read process cannot be started.
    """  # noqa: DOC503 - Local validation is recorded; Pod command setup can fail.
    errors: MutableSequence[Mapping[str, str]] = []
    records: MutableSequence[JSONObject] = []
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
    row_findings: MutableSequence[JSONObject] = []
    row: MutableMapping[str, JSONValue] = {
        "name": reader.name,
        "pod_uid": reader.uid,
        "status": PASS,
        "findings": row_findings,
    }
    for name, command_result in outputs.items():
        try:
            value = _json_result(command_result, source=name)
            valid = (
                valid_daemon_status(value)
                if name == "daemon"
                else valid_node_health(value)
            )
            if not valid or not isinstance(value, Mapping):
                raise ValueError(f"{name}:invalid_response")
            if name == "daemon":
                row["daemon"] = {
                    "components": {
                        component: sanitize(state, 80)
                        for component, data in value.items()
                        if isinstance(data, Mapping)
                        and isinstance(state := data.get("state"), str)
                    }
                }
            else:
                row["health"] = health_detail(value)
            findings = (
                daemon_findings(value) if name == "daemon" else health_findings(value)
            )
            row_findings.extend(findings[:100])
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


def classify_ceph_message(message: str, priority: int) -> tuple[str, Optional[str]]:
    """Classify a Ceph kernel message and suggest a read-only action.

    :param message: Raw journal message to match against known Ceph patterns.
    :param priority: Numeric syslog priority; values at most three classify an
        unmatched message as an error.
    :returns: Classification and optional inspection action, without a root-cause
        claim.
    """
    for pattern, category, action in CEPH_ACTIONS:
        if pattern.search(message):
            return category, action
    if priority <= 3:
        return "unclassified_error", "review_ceph_kernel_event"
    return "observation", None


@dataclass(frozen=True)
class JournalObservation:
    """Hold classified kernel records and independent line-decoding failures.

    :param records: Ordered Ceph journal records with timestamps and read suggestions.
    :param errors: Per-line decode or validation errors from the bounded response.

    .. attribute :: records
        :type: Sequence[JSONObject]

        Ordered Ceph journal records with timestamps and read suggestions.

    .. attribute :: errors
        :type: Sequence[Mapping[str, str]]

        Per-line decode or validation errors from the bounded response.
    """

    records: Sequence[JSONObject]
    errors: Sequence[Mapping[str, str]]

    @staticmethod
    def record(line: str) -> JSONObject:
        """Decode one journal entry and classify a matching Ceph message.

        :param line: One JSON line emitted by journalctl --output=json.
        :returns: Classified record, or an empty object for a non-Ceph message.
        :raises TypeError: If the decoded line is not an object.
        :raises ValueError: If JSON, message, or microsecond timestamp is invalid,
            or priority is not decimal text/an integer in the journal range 0 through 7.
        :raises OverflowError: If the timestamp exceeds the platform datetime range.
        :raises OSError: If the platform cannot represent the supplied timestamp.
        :raises RecursionError: If the JSON input exceeds decoder nesting limits.
        """  # noqa: DOC503 - Timestamp conversion and JSON decoding can fail.
        event = response_decoder().decode(line)
        if not isinstance(event, Mapping):
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
            return {}
        raw_priority = event.get("PRIORITY", "6")
        if not isinstance(raw_priority, (str, int)) or isinstance(raw_priority, bool):
            # Journal reports expose ValueError for every invalid priority value.
            raise ValueError("invalid_priority")  # noqa: TRY004
        if not str(raw_priority).isdecimal() or not 0 <= int(raw_priority) <= 7:
            raise ValueError("invalid_priority")
        priority = int(raw_priority)
        category, action = classify_ceph_message(message, priority)
        return {
            "timestamp": datetime.fromtimestamp(
                int(stamp) / 1_000_000, UTC
            ).isoformat(),
            "priority": priority,
            "message": sanitize(message, 1000),
            "classification": category,
            "action": action,
        }

    @classmethod
    def parse(cls, text: str) -> JournalObservation:
        """Parse bounded journal text while retaining independent malformed lines.

        :param text: Captured journalctl JSON lines after command success.
        :returns: Valid Ceph records and classified errors in response order.
        """
        records: MutableSequence[JSONObject] = []
        errors: MutableSequence[Mapping[str, str]] = []
        for line in text.splitlines():
            try:
                record = cls.record(line)
                if record:
                    records.append(record)
            except (
                OverflowError,
                OSError,
                TypeError,
                ValueError,
                RecursionError,
            ) as exc:
                errors.append(
                    {
                        "source": "journalctl",
                        "reason": f"invalid_journal_event:{type(exc).__name__}",
                    }
                )
        return cls(records, errors)


def collect_ceph_kernel(
    reader: NodePodReader, directory: str
) -> MutableMapping[str, JSONValue]:
    """Read recent Ceph/RBD kernel journal events from the selected Pod.

    :param reader: Verified Pod reader with host journal access.
    :param directory: Journal directory visible inside that Pod.
    :returns: Report with bounded classified records, actions, truncation flag,
        and ``PASS``, ``PARTIAL``, or ``BLOCKED`` status.
    :raises NodePodError: If the selected Pod identity or container changes.
    :raises TargetError: If bound kubeconfig sources change before a query.
    :raises OSError: If a native read process cannot be started.
    """  # noqa: DOC502 - Pod reader and pinned credential checks can fail.
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
    records: MutableSequence[JSONObject] = []
    errors: MutableSequence[Mapping[str, str]] = []
    result = _node_report("ceph_kernel", reader, records, errors)
    result["filters"] = {"since": JOURNAL_SINCE, "message_regex": CEPH_PATTERN.pattern}
    result["command"] = reader.command(*command)
    result["limits"] = {"lines": JOURNAL_MAX_LINES, "bytes": JOURNAL_MAX_BYTES}
    if journal_access.returncode:
        errors.append(
            {"source": "journal_access", "reason": error_class(journal_access)}
        )
        result["status"] = BLOCKED
        result["summary"] = {"record_count": 0, "error_count": 1}
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
        result["summary"] = {"record_count": 0, "error_count": 1}
        result["actions"] = []
        result["safe_next_step"] = (
            "Check journal access inside the selected existing Pod."
        )
        return result
    result["truncated"] = (
        len(response.stdout.encode("utf-8")) >= JOURNAL_MAX_BYTES - 4
        or len(response.stdout.splitlines()) >= JOURNAL_MAX_LINES
    )
    observation = JournalObservation.parse(response.stdout)
    records.extend(observation.records)
    errors.extend(observation.errors)
    result["summary"] = {"record_count": len(records), "error_count": len(errors)}
    result["status"] = PARTIAL if errors or result["truncated"] else PASS
    result["actions"] = sorted(
        {
            action
            for row in records
            if isinstance(action := row.get("action"), str) and action
        }
    )
    if errors or result["truncated"]:
        result["safe_next_step"] = (
            "Review bounded journal evidence on the selected node."
        )
    return result
