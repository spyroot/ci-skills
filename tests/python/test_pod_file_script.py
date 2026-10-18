"""Exercise native private-file creation, reuse and checked cleanup in CI.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import stat
import subprocess
from pathlib import Path

import pytest
from conftest import SKILL_ROOT

POD_FILE_SCRIPT = SKILL_ROOT / "lib/bash/kubernetes/pod-file.sh"


def _file_operation(
    path: Path, operation: str, content: bytes
) -> subprocess.CompletedProcess[bytes]:
    """Run the installed Linux shell file operation in a test-owned directory.

    :param path: Exact destination below this test's temporary directory.
    :param operation: Native write, stage or remove operation.
    :param content: Private stdin bytes or expected cleanup digest and size.
    :returns: Bounded child result with exact stdout, stderr and native exit status.
    """
    return subprocess.run(
        ["/bin/sh", str(POD_FILE_SCRIPT), str(path), operation],
        input=content,
        capture_output=True,
        check=False,
        timeout=5,
    )


def _cleanup_input(content: bytes) -> bytes:
    """Describe the original owned bytes for native checked deletion.

    :param content: Exact byte sequence proven when this invocation created the file.
    :returns: SHA-256 and byte count in the native cleanup stdin format.
    """
    return f"{hashlib.sha256(content).hexdigest()} {len(content)}\n".encode("ascii")


@pytest.mark.parametrize("content", (b"", b"native\x00\xff\r\nbytes"))
def test_write_creates_exact_private_bytes_and_releases_staging_file(
    content: bytes, tmp_path: Path
) -> None:
    """A new file contains exact input, has mode 0600 and leaves no temporary copy.

    :param content: Empty input or binary bytes requiring lossless transport.
    :param tmp_path: Test-owned destination directory.
    """
    path = tmp_path / "caller destination"

    result = _file_operation(path, "write", content)

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [
        hashlib.sha256(content).hexdigest().encode(),
        str(len(content)).encode(),
        b"created",
    ]
    assert path.read_bytes() == content
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("operation", ("write", "stage"))
def test_existing_different_data_survives_failed_staging(
    operation: str, tmp_path: Path
) -> None:
    """Exclusive creation and reusable staging both preserve conflicting caller data.

    :param operation: New-file-only write or identical-byte-reuse staging.
    :param tmp_path: Test-owned directory containing existing caller data.
    """
    path = tmp_path / "caller-file"
    original = b"existing caller data"
    path.write_bytes(original)
    path.chmod(0o640)
    before = path.stat()

    result = _file_operation(path, operation, b"different requested bytes")

    assert result.returncode == 31
    assert path.read_bytes() == original
    assert path.stat().st_ino == before.st_ino
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert list(tmp_path.iterdir()) == [path]


def test_identical_reuse_does_not_claim_ownership_or_change_caller_file(
    tmp_path: Path,
) -> None:
    """An identical caller file is marked reused while strict write still refuses it.

    :param tmp_path: Test-owned directory containing an identical caller file.
    """
    path = tmp_path / "caller-file"
    content = b"identical native bytes\x00\xff\n"
    path.write_bytes(content)
    path.chmod(0o640)
    before = path.stat()

    reused = _file_operation(path, "stage", content)
    refused = _file_operation(path, "write", content)

    assert reused.returncode == 0, reused.stderr
    assert reused.stdout.split() == [
        hashlib.sha256(content).hexdigest().encode(),
        str(len(content)).encode(),
        b"reused",
    ]
    assert refused.returncode == 31
    assert path.read_bytes() == content
    assert path.stat().st_ino == before.st_ino
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("mismatch", ("contents", "size"))
def test_cleanup_refuses_changed_contents_or_incorrect_expected_size(
    mismatch: str, tmp_path: Path
) -> None:
    """Cleanup independently checks both the original checksum and byte count.

    :param mismatch: Changed file contents or an incorrect expected size.
    :param tmp_path: Test-owned directory containing a newly staged file.
    """
    path = tmp_path / "owned-file"
    original = b"original"
    created = _file_operation(path, "stage", original)
    assert created.returncode == 0, created.stderr
    assert created.stdout.split()[-1] == b"created"
    expected_input = _cleanup_input(original)
    retained = original
    if mismatch == "contents":
        retained = b"modified"
        path.write_bytes(retained)
    else:
        expected_input = (
            f"{hashlib.sha256(original).hexdigest()} {len(original) + 1}\n".encode(
                "ascii"
            )
        )

    result = _file_operation(path, "remove", expected_input)

    assert result.returncode == 32
    assert b"owned file contents changed" in result.stderr
    assert path.read_bytes() == retained
    assert list(tmp_path.iterdir()) == [path]


def test_cleanup_removes_only_matching_owned_file_and_repeats_as_absent(
    tmp_path: Path,
) -> None:
    """Verified deletion is idempotent and preserves adjacent caller-owned data.

    :param tmp_path: Test-owned directory containing the owned file and unrelated data.
    """
    path = tmp_path / "owned-file"
    unrelated = tmp_path / "caller-file"
    unrelated.write_bytes(b"preserve me")
    content = b"owned native bytes"
    created = _file_operation(path, "write", content)
    assert created.returncode == 0, created.stderr

    removed = _file_operation(path, "remove", _cleanup_input(content))
    repeated = _file_operation(path, "remove", _cleanup_input(content))

    assert removed.returncode == 0, removed.stderr
    assert removed.stdout == b"removed\n"
    assert repeated.returncode == 0, repeated.stderr
    assert repeated.stdout == b"absent\n"
    assert not path.exists()
    assert unrelated.read_bytes() == b"preserve me"
    assert list(tmp_path.iterdir()) == [unrelated]
