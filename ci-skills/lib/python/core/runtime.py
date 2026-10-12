"""Run bounded native CLIs and keep external output out of gate diagnostics.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import os
import re
import selectors
import subprocess
import time
from collections.abc import Callable, Iterator, Mapping, MutableMapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union, overload

from lib.python.core.json_types import JSONObject, JSONValue

# How long a killed child is given to be reaped, so the worst case is the
# caller's timeout plus this, not unbounded.
REAP_SECONDS = 5
OUTPUT_LIMIT_EXIT_CODE = 125


@dataclass(frozen=True)
class CommandResult:
    """Capture one native command's invocation and bounded text output.

    :param argv: Exact command arguments executed by the runtime.
    :param returncode: Native process exit code.
    :param stdout: Captured standard output after runtime decoding.
    :param stderr: Captured standard error after runtime decoding.

    .. attribute :: argv
        :type: tuple[str, ...]

        Exact command arguments executed by the runtime.

    .. attribute :: returncode
        :type: int

        Native process exit code.

    .. attribute :: stdout
        :type: str

        Captured standard output after runtime decoding.

    .. attribute :: stderr
        :type: str

        Captured standard error after runtime decoding.
    """

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

_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@]+:[^\s/@]+@")
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def redact(value: str) -> str:
    """Remove recognized credential forms and terminal escapes from text.

    :param value: Text that may contain credential assignments, tokens, URL
        userinfo, private keys, or ANSI escapes.
    :returns: Full text with recognized sensitive forms replaced or stripped.
    """
    result = _ANSI_ESCAPE.sub("", value).replace("\x1b", "")
    result = _URL_USERINFO.sub(lambda match: match.group(1) + "[REDACTED]@", result)
    for pattern in (*_QUOTED_PATTERNS, *_SECRET_PATTERNS):
        result = pattern.sub(
            lambda match: (
                match.group(1) + "[REDACTED]" if match.lastindex else "[REDACTED]"
            ),
            result,
        )
    return result


def is_secret_name(name: str) -> bool:
    """Identify a field name that declares a credential value.

    :param name: Field name to test against the shared credential-name pattern.
    :returns: ``True`` when the name matches the pattern.
    """
    return bool(_SECRET_NAME.match(name))


@overload
def redact_tree(value: JSONObject) -> MutableMapping[str, JSONValue]:
    """Retain the JSON object contract while redacting nested evidence.

    :param value: JSON report whose string fields and keys may contain credentials.
    :returns: New mutable JSON object with credentials replaced by redaction markers.
    :raises RecursionError: If report containers contain a cycle or excessive nesting.
    """  # noqa: DOC502 - Recursive redaction can exceed container nesting limits.


@overload
def redact_tree(value: Mapping[str, object]) -> MutableMapping[str, object]:
    """Preserve the object shape of a redacted report.

    :param value: Read-only report fields whose nested strings may contain secrets.
    :returns: New mutable mapping containing redacted keys and values.
    :raises RecursionError: If report containers contain a cycle or excessive nesting.
    """  # noqa: DOC502 - Recursive redaction can exceed container nesting limits.


@overload
def redact_tree(value: object) -> object:
    """Accept an untrusted report value before its concrete shape is known.

    :param value: Value to inspect recursively at the report boundary.
    :returns: Redacted strings and containers; other values remain unchanged.
    :raises RecursionError: If report containers contain a cycle or excessive nesting.
    """  # noqa: DOC502 - Recursive redaction can exceed container nesting limits.


def redact_tree(value: object) -> object:
    """Redact strings and credential-named values in a nested report value.

    :param value: String, mapping, non-byte sequence, or scalar report value.
    :returns: A redacted string, transformed mapping or list, or unchanged scalar.
        Unsupported objects remain unchanged so the output serializer can reject them.
    :raises RecursionError: If report containers contain a cycle or excessive nesting.
    """  # noqa: DOC502 - Recursive redaction can exceed container nesting limits.
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, Mapping):
        return {
            redact(str(key)): (
                "[REDACTED]" if is_secret_name(str(key)) else redact_tree(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [redact_tree(item) for item in value]
    return value


def sanitize(value: str, limit: int = 1000) -> str:
    """Redact and truncate text destined for a bounded diagnostic field.

    :param value: Untrusted text to sanitize.
    :param limit: Maximum characters retained after redaction.
    :returns: Redacted text truncated to ``limit`` characters.
    """
    return redact(value)[:limit]


TraceSink = Callable[[str, Mapping[str, object]], None]
_TRACE_SINK: ContextVar[Optional[TraceSink]] = ContextVar(
    "execution_trace", default=None
)
TRACE_TEXT_LIMIT = 4096


@contextmanager
def execution_trace(sink: Optional[TraceSink]) -> Iterator[None]:
    """Bind one workflow's diagnostic writer for the context scope.

    :param sink: Event writer receiving safe event names and redacted details,
        or ``None`` to disable tracing for this scope.
    :yields: Control while the selected writer is bound; the previous writer is
        restored when the scope exits.
    :ytype: None
    """
    token = _TRACE_SINK.set(sink)
    try:
        yield
    finally:
        _TRACE_SINK.reset(token)


def trace_event(event: str, details: Mapping[str, object]) -> None:
    """Send a workflow event through the selected shared diagnostic writer.

    :param event: Stable operation event name.
    :param details: Bounded, noncredential evidence selected by the caller.
    """
    sink = _TRACE_SINK.get()
    if sink is not None:
        sink(event, redact_tree(details))


def trace_enabled() -> bool:
    """Report whether this invocation requested execution diagnostics.

    :returns: Whether a workflow trace writer is bound.
    """
    return _TRACE_SINK.get() is not None


def _traced_result(
    argv: tuple[str, ...], returncode: int, stdout: str, stderr: str
) -> CommandResult:
    """Build a command result and report bounded trace metadata when enabled.

    :param argv: Exact command argument vector.
    :param returncode: Native process exit status.
    :param stdout: Captured standard output.
    :param stderr: Captured standard error.
    :returns: Command result carrying the supplied vector, status, and streams.
    """
    result = CommandResult(argv, returncode, stdout, stderr)
    if trace_enabled():
        safe_argv = []
        secret_next = False
        for item in argv:
            safe_argv.append("[REDACTED]" if secret_next else sanitize(item))
            secret_next = (
                item.startswith("--") and "=" not in item and is_secret_name(item[2:])
            )
        trace_event(
            "command.complete",
            {
                "argv": safe_argv,
                "returncode": returncode,
                "stdout_bytes": len(stdout.encode()),
                "stderr": sanitize(stderr, TRACE_TEXT_LIMIT),
            },
        )
    return result


def run_command(
    argv: Sequence[str],
    *,
    timeout: int = 25,
    env: Optional[Mapping[str, Optional[str]]] = None,
) -> CommandResult:
    """Run a native argument vector without a shell or interactive input.

    :param argv: Executable and arguments; each item is converted to text.
    :param timeout: Maximum subprocess runtime in seconds.
    :param env: Child environment overrides; ``None`` values remove variables.
    :returns: Captured command result; missing tools and timeouts use status 127
        and 124 respectively.
    """
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
        return _traced_result(command, 127, "", "command unavailable")
    except subprocess.TimeoutExpired:
        return _traced_result(command, 124, "", "command timed out")
    return _traced_result(command, result.returncode, result.stdout, result.stderr)


def run_command_bounded(
    argv: Sequence[str],
    *,
    timeout: int = 25,
    env: Optional[Mapping[str, Optional[str]]] = None,
    max_stdout_bytes: int = 8 * 1024 * 1024,
) -> CommandResult:
    """Run a native command with a strict stdout byte limit.

    :param argv: Executable and arguments; each item is converted to text.
    :param timeout: Maximum command runtime in seconds.
    :param env: Child environment overrides; ``None`` values remove variables.
    :param max_stdout_bytes: Maximum stdout bytes before the child is killed.
    :returns: Captured result; status 125 means stdout exceeded its limit,
        124 means timeout, and 127 means the executable is missing.
    :raises ValueError: ``timeout`` or ``max_stdout_bytes`` is nonpositive.
    """
    if timeout <= 0 or max_stdout_bytes <= 0:
        raise ValueError("command limits must be positive")
    command = tuple(str(part) for part in argv)
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=_environment(env),
        )
    except FileNotFoundError:
        return _traced_result(command, 127, "", "command unavailable")
    stdout = bytearray()
    stderr = bytearray()
    deadline = time.monotonic() + timeout
    failure: Optional[int] = None
    try:
        with selectors.DefaultSelector() as selector:
            assert process.stdout is not None and process.stderr is not None
            selector.register(process.stdout, selectors.EVENT_READ, stdout)
            selector.register(process.stderr, selectors.EVENT_READ, stderr)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failure = 124
                    break
                for key, _ in selector.select(timeout=min(remaining, 0.5)):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    captured = key.data
                    captured.extend(chunk)
                    if captured is stdout and len(stdout) > max_stdout_bytes:
                        failure = OUTPUT_LIMIT_EXIT_CODE
                        break
                    if captured is stderr and len(stderr) > 4096:
                        del stderr[:-4096]
                if failure is not None:
                    break
        if failure is None:
            remaining = deadline - time.monotonic()
            try:
                process.wait(timeout=max(remaining, 0))
            except subprocess.TimeoutExpired:
                failure = 124
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=REAP_SECONDS)
        except subprocess.TimeoutExpired:
            failure = 124
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
    return _traced_result(
        command,
        failure if failure is not None else process.returncode,
        stdout.decode("utf-8", errors="replace")
        if failure != OUTPUT_LIMIT_EXIT_CODE
        else "",
        stderr.decode("utf-8", errors="replace"),
    )


def _environment(
    overrides: Optional[Mapping[str, Optional[str]]],
) -> MutableMapping[str, str]:
    """Prepare a noninteractive child environment from process variables.

    :param overrides: Variables to set or unset after prompt-disabling defaults.
    :returns: New environment mapping; a ``None`` override removes its key.
    """
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
    env: Optional[Mapping[str, Optional[str]]] = None,
    cwd: Optional[Union[str, Path]] = None,
) -> CommandResult:
    """Run a native command and retain bounded output tails.

    :param argv: Executable and arguments; each item is converted to text.
    :param timeout: Maximum command runtime in seconds.
    :param max_bytes: Maximum stdout bytes retained from the end of the stream.
    :param max_lines: Maximum stdout lines returned after byte truncation.
    :param env: Child environment overrides; ``None`` values remove variables.
    :param cwd: Optional child working directory.
    :returns: Command result with the final stdout lines and final 4096 stderr
        bytes; status 124 means timeout and 127 means missing executable.
    :raises ValueError: ``max_bytes`` or ``max_lines`` is nonpositive.
    """
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
        return _traced_result(command, 127, "", "command unavailable")
    stdout_tail = bytearray()
    stderr_tail = bytearray()
    deadline = time.monotonic() + timeout
    expired = False
    assert process.stdout is not None and process.stderr is not None
    with selectors.DefaultSelector() as selector, process.stdout, process.stderr:
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
    return _traced_result(
        command,
        124 if expired else process.returncode,
        "\n".join(stdout.splitlines()[-max_lines:]),
        stderr,
    )


def error_class(result: CommandResult) -> str:
    """Classify a native result without exposing raw stderr.

    :param result: Native command status and stderr to classify.
    :returns: Stable reason such as ``missing_tool``, ``timeout``,
        ``response_too_large``, ``authentication``, ``authorization``,
        ``resource_unavailable``, ``transport``, or ``command_failed``.
    """
    if result.returncode == 127:
        return "missing_tool"
    if result.returncode == 124:
        return "timeout"
    if result.returncode == OUTPUT_LIMIT_EXIT_CODE:
        return "response_too_large"
    detail = result.stderr.lower()
    if "unauthorized" in detail or "401" in detail or "not logged in" in detail:
        return "authentication"
    if "forbidden" in detail or "403" in detail or "permission" in detail:
        return "authorization"
    if (
        "the server doesn't have a resource type" in detail
        or "the server does not have a resource type" in detail
        or "the server could not find the requested resource" in detail
    ):
        return "resource_unavailable"
    if (
        "could not resolve" in detail
        or "connection refused" in detail
        or "no such host" in detail
        or "connection reset" in detail
        or "network is unreachable" in detail
        or "can't assign requested address" in detail
        or "timed out" in detail
    ):
        return "transport"
    return "command_failed"
