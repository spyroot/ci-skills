"""Serialize writes to an exact provider resource on the execution host.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import stat
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Final

from lib.python.core.cli_api import APIError

RESOURCE_LOCK_WAIT_SECONDS: Final = 30


@contextmanager
def locked_resource(root: Path, identity: Sequence[str | int]) -> Iterator[int]:
    """Hold an exclusive local lock for one provider resource identity.

    :param root: Private lock directory selected by the provider.
    :param identity: Provider and resource identifiers, without credentials.
    :yields: Open locked file descriptor until the caller's scope exits.
    :ytype: int
    :raises APIError: Lock directory/file metadata is unsafe or acquisition
        exceeds its deadline.
    :raises OSError: Lock file creation, locking, or cleanup fails.
    """
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = root.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        raise APIError("resource_lock_directory_unsafe")
    encoded = json.dumps(identity, separators=(",", ":"))
    name = hashlib.sha256(encoded.encode("utf-8")).hexdigest() + ".lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(root / name, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise APIError("resource_lock_file_unsafe")
        deadline = time.monotonic() + RESOURCE_LOCK_WAIT_SECONDS
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                if time.monotonic() >= deadline:
                    raise APIError("resource_lock_timeout") from exc
                time.sleep(0.05)
        yield descriptor
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
