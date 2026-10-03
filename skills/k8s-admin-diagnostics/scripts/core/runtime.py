"""Run bounded native CLIs and keep external output out of gate diagnostics."""

from __future__ import annotations

import os
import re
import selectors
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# How long a killed child is given to be reaped, so the worst case is the
# caller's timeout plus this, not unbounded.
REAP_SECONDS = 5


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


# One name CLASS, not a name list: a list leaves the next variable uncovered.
# Covers CI_JOB_TOKEN, GITLAB_TOKEN, GH_TOKEN, DB_PASSWORD, MY_API_KEY and the
# lowercase JSON/YAML spellings alike.
_NAME = (
    r"(?:[A-Za-z0-9_-]*"
    r"(?:token|secret|password|passwd|api[_-]?key|apikey|private[_-]?key)"
    r"[A-Za-z0-9_-]*)"
)

_SECRET_PATTERNS = (
    # A YAML block scalar carries its value on the indented lines that follow,
    # which a \\S+ match cannot reach: the key line was redacted and the value
    # was not.
    re.compile(
        rf"(?im)^(\s*{_NAME}\s*:\s*[|>][-+0-9]*\s*$)(?:\n(?:[ \t]+\S.*|[ \t]*)$)+",
    ),
    re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer|basic)\s+)\S+"),
    # NAME=value and name: value. A quoted value is handled by the next pattern,
    # which must run first, so this one refuses to start on a quote.
    re.compile(rf"(?i)\b({_NAME}\s*[:=]\s*)(?![\s\"'])\S+"),
    # A bare provider token carries no surrounding key at all.
    re.compile(r"\b(?:glpat|glcbt|glsoat|glrt|glptt)-[A-Za-z0-9._-]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{16,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{8,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+"),
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
)

# Quoted structured fields run before the bare-assignment pattern so the closing
# quote is not left dangling: {"password": "v"} and password: "v" alike.
# A structured secret is identified by its KEY, not by its value's shape, so a
# mapping needs the relationship: {"GITLAB_TOKEN": "v"} matches neither half on
# its own.
_SECRET_NAME = re.compile(rf"(?i)\A{_NAME}\Z")

_QUOTED_PATTERNS = (
    re.compile(rf"(?i)(\"{_NAME}\"\s*:\s*)\"[^\"]*\""),
    re.compile(rf"(?i)({_NAME}\s*[:=]\s*)\"[^\"]*\""),
    re.compile(rf"(?i)({_NAME}\s*[:=]\s*)'[^']*'"),
)


def redact(value: str) -> str:
    """Remove credential forms from text, with no length bound.

    Separate from `sanitize` so a whole report can be redacted at its output
    boundary without the 1000-character truncation that bounds one command's
    captured output.
    """
    result = value
    for pattern in (*_QUOTED_PATTERNS, *_SECRET_PATTERNS):
        result = pattern.sub(
            lambda match: (
                match.group(1) + "[REDACTED]" if match.lastindex else "[REDACTED]"
            ),
            result,
        )
    return result


def is_secret_name(name: str) -> bool:
    """Report whether a field name declares its value to be a credential."""
    return bool(_SECRET_NAME.match(name))


def redact_tree(value: Any) -> Any:
    """Redact every string in a nested structure, keys included.

    A mapping whose KEY names a credential has its whole value replaced,
    whatever that value looks like: the key is the evidence, and redacting the
    two independently lets {"GITLAB_TOKEN": "value"} through untouched.
    """
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {
            redact(str(key)): (
                "[REDACTED]" if is_secret_name(str(key)) else redact_tree(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_tree(item) for item in value]
    return value


def sanitize(value: str, limit: int = 1000) -> str:
    """Redact credential forms and bound untrusted report text."""
    return redact(value)[:limit]


def run_command(
    argv: Sequence[str],
    *,
    timeout: int = 25,
    env: dict[str, str | None] | None = None,
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


def _environment(overrides: dict[str, str | None] | None) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "GH_PROMPT_DISABLED": "1",
            "GLAB_NO_PROMPT": "1",
            "KUBECTL_EXTERNAL_DIFF": "false",
        }
    )
    if overrides:
        for name, value in overrides.items():
            if value is None:
                environment.pop(name, None)
            else:
                environment[name] = value
    return environment


def run_command_tail(
    argv: Sequence[str],
    *,
    timeout: int = 30,
    max_bytes: int = 65536,
    max_lines: int = 200,
    env: dict[str, str | None] | None = None,
    cwd: str | Path | None = None,
) -> CommandResult:
    """Stream a command and retain only bounded stdout/stderr tail bytes."""
    if max_bytes <= 0 or max_lines <= 0:
        raise ValueError("tail limits must be positive")
    command = tuple(str(part) for part in argv)
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=_environment(env),
            cwd=cwd,
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
    # A child can close both pipes and stay alive. Waiting without the
    # remaining deadline returned success well past the timeout, so completion
    # is bounded too, then the child is killed and reaped.
    remaining = deadline - time.monotonic()
    try:
        process.wait(timeout=max(remaining, 0))
    except subprocess.TimeoutExpired:
        expired = True
        process.kill()
        try:
            process.wait(timeout=REAP_SECONDS)
        except subprocess.TimeoutExpired:
            pass
    stdout = stdout_tail.decode("utf-8", errors="replace")
    stderr = stderr_tail.decode("utf-8", errors="replace")
    return CommandResult(
        command,
        124 if expired else process.returncode,
        "\n".join(stdout.splitlines()[-max_lines:]),
        stderr,
    )


def error_class(result: CommandResult) -> str:
    """Classify an external command without emitting its raw stderr."""
    if result.returncode == 127:
        return "missing_tool"
    if result.returncode == 124:
        return "timeout"
    detail = result.stderr.lower()
    if (
        "the server doesn't have a resource type" in detail
        or "the server does not have a resource type" in detail
        or "the server could not find the requested resource" in detail
    ):
        return "resource_unavailable"
    if "unauthorized" in detail or "401" in detail or "not logged in" in detail:
        return "authentication"
    if "forbidden" in detail or "403" in detail or "permission" in detail:
        return "authorization"
    if (
        "could not resolve" in detail
        or "connection refused" in detail
        or "no such host" in detail
    ):
        return "transport"
    return "command_failed"
