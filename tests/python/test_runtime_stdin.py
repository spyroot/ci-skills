"""Verify bounded native stdin transport without external services.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections.abc import Mapping, MutableSequence, Sequence
from typing import Optional

import pytest
from conftest import authenticated_kubernetes_service, import_script_module

RUNTIME = import_script_module("lib.python.core.runtime")
KUBERNETES = import_script_module("lib.python.core.kubernetes.kubernetes")
TARGET = import_script_module("lib.python.core.target")


@pytest.fixture
def native_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> Sequence[tuple[int, subprocess.Popen[bytes]]]:
    """Observe real process construction without replacing its pipe behavior.

    :param monkeypatch: Replace Popen with a recording delegate.
    :returns: Process stdin selections and handles retained for final read-back.
    """
    observed: MutableSequence[tuple[int, subprocess.Popen[bytes]]] = []
    native_popen = subprocess.Popen

    def capture_popen(
        argv: Sequence[str],
        *,
        stdout: int,
        stderr: int,
        stdin: int,
        env: Mapping[str, str],
    ) -> subprocess.Popen[bytes]:
        """Create a real child and retain its input mode and process handle.

        :param argv: Native executable and argument vector.
        :param stdout: Native stdout pipe selection.
        :param stderr: Native stderr pipe selection.
        :param stdin: Native input pipe or null-device selection.
        :param env: Child process environment selected by the runtime.
        :returns: Real process whose streams remain under test.
        """
        child = native_popen(argv, stdout=stdout, stderr=stderr, stdin=stdin, env=env)
        observed.append((stdin, child))
        return child

    monkeypatch.setattr(RUNTIME.subprocess, "Popen", capture_popen)
    return observed


def test_stdin_drains_both_outputs_before_child_reads_large_payload() -> None:
    """Feed exact bytes even when each output pipe fills before stdin is read."""
    payload = bytes(range(256)) * 2048
    output_size = 131072
    program = (
        "import hashlib, sys\n"
        f"sys.stdout.buffer.write(b'o' * {output_size}); sys.stdout.flush()\n"
        f"sys.stderr.buffer.write(b'e' * {output_size}); sys.stderr.flush()\n"
        "data = sys.stdin.buffer.read()\n"
        "sys.stdout.write(hashlib.sha256(data).hexdigest())\n"
    )

    result = RUNTIME.run_command_bounded(
        [sys.executable, "-c", program], input_data=payload, timeout=5
    )

    assert result.returncode == 0
    assert result.stdout == "o" * output_size + hashlib.sha256(payload).hexdigest()
    assert result.stderr == "e" * 4096


@pytest.mark.parametrize("payload", (None, b""))
def test_absent_and_empty_stdin_both_reach_eof_with_distinct_pipe_selection(
    payload: Optional[bytes],
    native_processes: Sequence[tuple[int, subprocess.Popen[bytes]]],
) -> None:
    """Absent stdin uses DEVNULL; explicit empty input opens and closes a pipe.

    :param payload: Unspecified input or an explicitly empty byte sequence.
    :param native_processes: Recording delegate retaining real child behavior.
    """
    result = RUNTIME.run_command_bounded(
        [sys.executable, "-c", "import sys; print(len(sys.stdin.buffer.read()))"],
        input_data=payload,
        timeout=5,
    )

    assert result.returncode == 0
    assert result.stdout == "0\n"
    assert [mode for mode, _child in native_processes] == [
        subprocess.DEVNULL if payload is None else subprocess.PIPE
    ]


@pytest.mark.parametrize(
    ("program", "limit", "expected_status"),
    (
        ("import signal; signal.pause()", 1024, 124),
        ("import sys; sys.stdout.write('x' * 1000000)", 1024, 125),
    ),
)
def test_stalled_or_overflowing_child_is_reaped_while_input_remains(
    program: str,
    limit: int,
    expected_status: int,
    native_processes: Sequence[tuple[int, subprocess.Popen[bytes]]],
) -> None:
    """Timeout and output limits still terminate a child with pending stdin.

    :param program: Child that stalls without reading or exceeds its output cap.
    :param limit: Maximum captured stdout byte count.
    :param expected_status: Runtime failure code for the selected boundary.
    :param native_processes: Real process handles retained for final read-back.
    """
    result = RUNTIME.run_command_bounded(
        [sys.executable, "-c", program],
        input_data=b"pending" * 131072,
        max_stdout_bytes=limit,
        timeout=1,
    )

    assert result.returncode == expected_status
    assert result.stdout == ""
    assert len(native_processes) == 1
    assert native_processes[0][1].poll() is not None


def test_broken_stdin_pipe_preserves_native_nonzero_exit() -> None:
    """An early child refusal remains its native exit instead of a transport error."""
    result = RUNTIME.run_command_bounded(
        [sys.executable, "-c", "import os; os.close(0); os._exit(17)"],
        input_data=b"not-consumed" * 131072,
        timeout=5,
    )

    assert result.returncode == 17
    assert result.stdout == ""


def test_stdin_payload_is_not_exposed_in_arguments_or_trace() -> None:
    """Command evidence contains input size effects, never the input contents."""
    payload = b"private-native-input-unique-marker\x00\xff\r\n"
    events: MutableSequence[tuple[str, Mapping[str, object]]] = []
    argv = [
        sys.executable,
        "-c",
        "import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())",
    ]

    with RUNTIME.execution_trace(
        lambda event, details: events.append((event, details))
    ):
        result = RUNTIME.run_command_bounded(argv, input_data=payload, timeout=5)

    assert result.returncode == 0
    assert result.stdout.strip() == hashlib.sha256(payload).hexdigest()
    assert result.argv == tuple(argv)
    assert [event for event, _details in events] == ["command.complete"]
    assert "private-native-input-unique-marker" not in json.dumps(events)
    assert events[0][1]["argv"] == argv


@pytest.mark.parametrize("payload", (None, b"", b"native\x00\xff\r\n"))
def test_pod_exec_only_enables_interactive_input_for_supplied_bytes(
    payload: Optional[bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forward exact stdin and enable -i for empty or populated input only.

    :param payload: Optional native command input, including explicit empty bytes.
    :param monkeypatch: Replace the process boundary, retaining the service adapter.
    """
    observed: MutableSequence[tuple[tuple[str, ...], Optional[bytes]]] = []

    def capture_transport(
        argv: Sequence[str],
        *,
        timeout: int,
        env: Optional[Mapping[str, Optional[str]]],
        input_data: Optional[bytes] = None,
    ) -> RUNTIME.CommandResult:
        """Record the adapter's native vector and exact input without a cluster.

        :param argv: Bound kubectl vector supplied by the service.
        :param timeout: Service-selected command deadline.
        :param env: Selected target environment overrides.
        :param input_data: Exact stdin value selected by the caller.
        :returns: Successful native command result.
        """
        assert timeout == 30
        assert env is None
        observed.append((tuple(argv), input_data))
        return RUNTIME.CommandResult(tuple(argv), 0, "staged", "")

    monkeypatch.setattr(KUBERNETES, "run_command_bounded", capture_transport)
    target = TARGET.Target(
        github=None,
        gitlab=None,
        kubernetes=TARGET.KubernetesTarget(
            "unit-context", "https://api.example.test:6443", None
        ),
        active_surfaces=("kubernetes",),
    )
    service = authenticated_kubernetes_service(target)

    result = service.pod_exec(
        "unit-namespace", "unit-pod", "unit-container", "cat", input_data=payload
    )

    expected = (
        "kubectl",
        "--context",
        "unit-context",
        "--namespace",
        "unit-namespace",
        "exec",
        *(("-i",) if payload is not None else ()),
        "pod/unit-pod",
        "-c",
        "unit-container",
        "--",
        "cat",
    )
    assert result.stdout == "staged"
    assert observed == [(expected, payload)]
