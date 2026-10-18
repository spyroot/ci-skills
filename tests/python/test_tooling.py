"""Prove shared dependency inputs and checksum-before-install behavior.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
from pathlib import Path

import pytest
from conftest import import_script_module, install_executable

TOOLING = import_script_module("lib.python.core.tooling")
TOOLBOX = import_script_module("lib.python.core.toolbox")


def test_dependency_preserves_probe_for_both_consumers() -> None:
    """Toolbox and prepared Pod consumers retain the same caller-supplied probe."""
    dependency = TOOLING.Dependency.from_document(
        {
            "name": "rsync",
            "commands": ["rsync"],
            "probe": ["rsync", "--version"],
            "apt": ["rsync"],
        }
    )
    assert dependency.probe == ("rsync", "--version")
    assert TOOLBOX.Dependency is TOOLING.Dependency
    assert TOOLBOX.LinuxInstall is TOOLING.LinuxInstall


@pytest.mark.parametrize("outcome", ("checksum", "transfer", "success"))
def test_shared_artifact_operations_require_checksum_before_installer(
    tmp_path: Path, fake_bin: Path, outcome: str
) -> None:
    """Run the generated shell to prove a bad download cannot reach its installer."""
    marker = tmp_path / "installed"
    payload = tmp_path / "downloaded-script"
    payload.write_text(
        f"printf installed > {shlex.quote(str(marker))}\n", encoding="utf-8"
    )
    expected = (
        hashlib.sha256(payload.read_bytes()).hexdigest()
        if outcome != "checksum"
        else "0" * 64
    )
    artifact = tmp_path / "staged installer.sh"
    install_executable(
        fake_bin,
        "curl",
        '#!/bin/sh\nset -eu\nwhile [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = --output ]; then shift; output=$1; fi\n'
        "  shift\ndone\n"
        'if [ "$UNIT_OUTCOME" = transfer ]; then printf partial > "$output"; exit 22; fi\n'
        'cp "$UNIT_PAYLOAD" "$output"\n',
    )
    route = TOOLING.LinuxInstall.from_document(
        {
            "method": "installer",
            "url": "https://artifacts.example.test/installer.sh?token=fixture",
            "sha256": expected,
            "interpreter": "/bin/sh",
        }
    )
    operation = f"{route.download_operation(str(artifact))}; {route.install_operation(str(artifact))}"
    result = subprocess.run(
        ["/bin/sh", "-ceu", operation],
        env={
            **os.environ,
            "PATH": str(fake_bin) + os.pathsep + os.defpath,
            "UNIT_PAYLOAD": str(payload),
            "UNIT_OUTCOME": outcome,
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert artifact.read_bytes() == (
        b"partial" if outcome == "transfer" else payload.read_bytes()
    )
    if outcome == "success":
        assert result.returncode == 0, result.stderr
        assert marker.read_text(encoding="utf-8") == "installed"
    else:
        assert result.returncode == (22 if outcome == "transfer" else 1)
        assert not marker.exists()
