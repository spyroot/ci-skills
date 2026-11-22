"""Shared OCI digest reads preserve the native manifest bytes and error boundary."""

from hashlib import sha256
from unittest.mock import Mock

import pytest
from conftest import import_script_module

OCI = import_script_module("lib.python.core.oci")
RUNTIME = import_script_module("lib.python.core.runtime")
IMAGE = "registry.example.test:5443/consumer/agent:v1"
MANIFEST = '{"schemaVersion":2,"manifests":[]}\n'


def test_public_oci_read_is_anonymous_and_hashes_raw_manifest() -> None:
    transport = Mock(return_value=RUNTIME.CommandResult((), 0, MANIFEST, ""))
    assert OCI.inspect_manifest_digest(IMAGE, timeout=8, command=transport) == (
        "sha256:" + sha256(MANIFEST.encode()).hexdigest()
    )
    transport.assert_called_once_with(
        ["skopeo", "inspect", "--raw", "--no-creds", f"docker://{IMAGE}"],
        timeout=8,
    )


@pytest.mark.parametrize(
    ("returncode", "stdout", "reason"),
    [(0, "", "manifest_missing"), (124, MANIFEST, "timeout")],
)
def test_oci_read_fails_closed(returncode: int, stdout: str, reason: str) -> None:
    transport = Mock(return_value=RUNTIME.CommandResult((), returncode, stdout, ""))
    with pytest.raises(OCI.APIError) as caught:
        OCI.inspect_manifest_digest(IMAGE, timeout=8, command=transport)
    assert caught.value.reason == reason
    assert transport.call_count == 1
