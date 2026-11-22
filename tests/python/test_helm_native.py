"""Bounded public Helm chart retrieval and checksum enforcement."""

from hashlib import sha256
from pathlib import Path
from types import MappingProxyType

import pytest
from conftest import import_script_module

HELM = import_script_module("lib.python.core.helm.native")
RUNTIME = import_script_module("lib.python.core.runtime")
ARCHIVE = b"exact chart archive bytes"
BASELINE = MappingProxyType(
    {
        "url": "https://charts.example.test/approved-2.20.1.tgz",
        "sha256": sha256(ARCHIVE).hexdigest(),
        "timeout": 8,
    }
)


@pytest.mark.parametrize(
    ("field", "value", "dispatched"),
    [
        (None, None, True),
        ("url", "http://charts.example.test/chart.tgz", False),
        ("sha256", "0" * 64, True),
    ],
)
def test_chart_pull_requires_immutable_archive(monkeypatch, field, value, dispatched):
    args = dict(BASELINE)
    if field is not None:
        args[field] = value
    workspaces = []

    def native_pull(argv, *, timeout, env):
        assert argv[:4] == ("helm", "pull", BASELINE["url"], "--destination")
        assert timeout == BASELINE["timeout"]
        workspace = Path(argv[4])
        workspaces.append(workspace)
        assert Path(env["HELM_REGISTRY_CONFIG"]).is_relative_to(workspace)
        assert Path(env["DOCKER_CONFIG"]).is_relative_to(workspace)
        (workspace / "approved-2.20.1.tgz").write_bytes(ARCHIVE)
        return RUNTIME.CommandResult(argv, 0, "", "")

    monkeypatch.setattr(HELM, "run_command_bounded", native_pull)
    if field is None:
        with HELM.pull_verified_chart(**args) as chart:
            assert chart.path.read_bytes() == ARCHIVE
            assert chart.sha256 == BASELINE["sha256"]
        assert not chart.path.exists()
    else:
        with pytest.raises(HELM.HelmError), HELM.pull_verified_chart(**args):
            pytest.fail("An unverified archive was yielded")
    assert len(workspaces) == int(dispatched)
    assert not any(workspace.exists() for workspace in workspaces)
