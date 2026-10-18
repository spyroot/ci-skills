"""Exchange bounded child output and optional stdin without blocking a pipe.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import os
import selectors
import subprocess
import time
from dataclasses import dataclass, field
from typing import BinaryIO, Final, Optional

OUTPUT_LIMIT_EXIT_CODE: Final = 125
STREAM_CHUNK_BYTES: Final = 8192
STDERR_TAIL_BYTES: Final = 4096
SELECT_INTERVAL_SECONDS: Final = 0.5


@dataclass
class CapturedStreams:
    """Retain bounded output and a transport failure independently of child status.

    :param stdout: Standard output collected up to the configured limit.
    :param stderr: Most recent standard-error bytes.
    :param failure: Runtime failure code, or ``None`` if transport completed.

    .. attribute :: stdout
        :type: bytearray

        Captured standard output.

    .. attribute :: stderr
        :type: bytearray

        Bounded standard-error tail.

    .. attribute :: failure
        :type: Optional[int]

        Transport failure, absent after normal completion.
    """

    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)
    failure: Optional[int] = None


@dataclass
class _InputWriter:
    """Track one child's exact stdin payload without copying its remaining bytes.

    :param stream: Open binary child stdin pipe.
    :param payload: Read-only view of the caller's bytes.
    :param offset: Number of bytes already accepted by the pipe.

    .. attribute :: stream
        :type: BinaryIO

        Child stdin pipe.

    .. attribute :: payload
        :type: memoryview

        View of the exact input bytes.

    .. attribute :: offset
        :type: int

        Number of bytes already written.
    """

    stream: BinaryIO = field(repr=False)
    payload: memoryview = field(repr=False)
    offset: int = 0

    def write_ready(self, selector: selectors.BaseSelector) -> None:
        """Advance a writable nonblocking pipe and close it at EOF or broken pipe.

        :param selector: Selector currently watching this input stream.
        :raises OSError: If a write fails for a reason other than a full or closed pipe.
        """  # noqa: DOC502,DOC503 - Nonrecoverable pipe failures propagate from os.write.
        try:
            self.offset += os.write(
                self.stream.fileno(),
                self.payload[self.offset : self.offset + STREAM_CHUNK_BYTES],
            )
        except BlockingIOError:
            return
        except BrokenPipeError:
            self.offset = len(self.payload)
        if self.offset == len(self.payload):
            selector.unregister(self.stream)
            self.stream.close()


def _register_input(
    selector: selectors.BaseSelector,
    stream: Optional[BinaryIO],
    input_data: Optional[bytes],
) -> None:
    """Watch supplied input for writes, or immediately signal EOF for empty bytes.

    :param selector: Selector already watching the child's output streams.
    :param stream: Child stdin pipe, absent when stdin uses the null device.
    :param input_data: Exact supplied payload, or ``None`` for no input pipe.
    :raises AssertionError: If supplied input has no child stdin pipe.
    :raises OSError: If the stdin descriptor cannot be made nonblocking.
    """  # noqa: DOC502,DOC503 - Descriptor failures propagate from os.set_blocking.
    if input_data is None:
        return
    assert stream is not None
    if not input_data:
        stream.close()
        return
    os.set_blocking(stream.fileno(), False)
    selector.register(
        stream, selectors.EVENT_WRITE, _InputWriter(stream, memoryview(input_data))
    )


def capture_command_streams(
    process: subprocess.Popen[bytes],
    *,
    input_data: Optional[bytes],
    deadline: float,
    max_stdout_bytes: int,
) -> CapturedStreams:
    """Drain child output while feeding stdin under one monotonic deadline.

    Child reaping and final descriptor closure belong to the calling runtime.
    Input payloads are never emitted or included in command metadata.

    :param process: Running child with stdout and stderr pipes.
    :param input_data: Optional exact bytes to send to its stdin pipe.
    :param deadline: Absolute monotonic timestamp when command runtime expires.
    :param max_stdout_bytes: Maximum stdout bytes before returning status 125.
    :returns: Captured streams and an optional timeout or output-limit failure.
    :raises AssertionError: If output pipes are absent or selector data is invalid.
    :raises OSError: If native pipe or selector operations fail.
    """  # noqa: DOC502,DOC503 - Native descriptor and selector failures propagate.
    captured = CapturedStreams()
    assert process.stdout is not None and process.stderr is not None
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ, captured.stdout)
        selector.register(process.stderr, selectors.EVENT_READ, captured.stderr)
        _register_input(selector, process.stdin, input_data)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                captured.failure = 124
                break
            for key, _ in selector.select(
                timeout=min(remaining, SELECT_INTERVAL_SECONDS)
            ):
                if isinstance(key.data, _InputWriter):
                    key.data.write_ready(selector)
                    continue
                chunk = os.read(key.fd, STREAM_CHUNK_BYTES)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                output = key.data
                assert isinstance(output, bytearray)
                output.extend(chunk)
                if output is captured.stdout and len(output) > max_stdout_bytes:
                    captured.failure = OUTPUT_LIMIT_EXIT_CODE
                    break
                if output is captured.stderr and len(output) > STDERR_TAIL_BYTES:
                    del output[:-STDERR_TAIL_BYTES]
            if captured.failure is not None:
                break
    return captured
