"""Run bounded native CLIs and keep external output out of gate diagnostics."""

from __future__ import annotations

import os
import re
import selectors
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer|basic)\s+)\S+"),
    re.compile(r"(?i)\b((?:access[_-]?token|api[_-]?key|password|secret)\s*[:=]\s*)\S+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
)


def sanitize(value: str, limit: int = 1000) -> str:
    """Remove common credential forms and bound untrusted report text."""
    result = value
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(lambda match: match.group(1) + "[REDACTED]" if match.lastindex else "[REDACTED]", result)
    return result[:limit]


def run_command(
    argv: Sequence[str],
    *,
    timeout: int = 25,
    env: dict[str, str] | None = None,
) -> CommandResult:
    """Execute an argument vector without a shell or interactive prompts."""
    command = tuple(str(part) for part in argv)
    environment = _environment(env)
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=environment,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return CommandResult(command, 127, "", "command unavailable")
    except subprocess.TimeoutExpired:
        return CommandResult(command, 124, "", "command timed out")
    return CommandResult(command, result.returncode, result.stdout, result.stderr)


def _environment(overrides: dict[str, str] | None) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update({"GH_PROMPT_DISABLED": "1", "GLAB_NO_PROMPT": "1", "KUBECTL_EXTERNAL_DIFF": "false"})
    if overrides:
        environment.update(overrides)
    return environment


def run_command_tail(
    argv: Sequence[str], *, timeout: int = 30, max_bytes: int = 65536,
    max_lines: int = 200, env: dict[str, str] | None = None,
) -> CommandResult:
    """Stream a command and retain only bounded stdout/stderr tail bytes."""
    if max_bytes <= 0 or max_lines <= 0:
        raise ValueError("tail limits must be positive")
    command = tuple(str(part) for part in argv)
    try:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, env=_environment(env),
        )
    except FileNotFoundError:
        return CommandResult(command, 127, "", "command unavailable")
    stdout_tail = bytearray()
    stderr_tail = bytearray()
    deadline = time.monotonic() + timeout
    expired = False
    with selectors.DefaultSelector() as selector:
        assert process.stdout is not None and process.stderr is not None
        selector.register(process.stdout, selectors.EVENT_READ, stdout_tail)
        selector.register(process.stderr, selectors.EVENT_READ, stderr_tail)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                expired = True
                process.kill()
                break
            for key, _ in selector.select(timeout=min(remaining, 0.5)):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                tail = key.data
                tail.extend(chunk)
                cap = max_bytes if tail is stdout_tail else 4096
                if len(tail) > cap:
                    del tail[:-cap]
    process.wait()
    stdout = stdout_tail.decode("utf-8", errors="replace")
    stderr = stderr_tail.decode("utf-8", errors="replace")
    return CommandResult(command, 124 if expired else process.returncode,
                         "\n".join(stdout.splitlines()[-max_lines:]), stderr)


def error_class(result: CommandResult) -> str:
    """Classify an external command without emitting its raw stderr."""
    if result.returncode == 127:
        return "missing_tool"
    if result.returncode == 124:
        return "timeout"
    detail = result.stderr.lower()
    if "unauthorized" in detail or "401" in detail or "not logged in" in detail:
        return "authentication"
    if "forbidden" in detail or "403" in detail or "permission" in detail:
        return "authorization"
    if "could not resolve" in detail or "connection refused" in detail or "no such host" in detail:
        return "transport"
    return "command_failed"
