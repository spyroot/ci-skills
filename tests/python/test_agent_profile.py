"""Check the agent profile used by ``install.sh``.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path

import pytest
from conftest import REPO_ROOT, load_module


def _profile(tmp_path: Path):
    """Create a valid manifest with one declared host and bundled skill.

    :param tmp_path: Isolated checkout root.
    :returns: Imported profile module and profile instance.
    """
    manifest = {
        "$schema": "./schemas/configuration/toolchain-dependencies.schema.json",
        "kind": "toolchain_dependencies",
        "schema_version": "1.0",
        "dependencies": [
            {
                "name": "glab",
                "commands": ["glab"],
                "probe": ["glab", "skills", "get", "glab-stack"],
                "brew": ["glab"],
                "apt": ["glab"],
                "yum": ["glab"],
            }
        ],
        "profiles": {
            "agent": {
                "host": ["glab"],
                "skills": ["glab-stack"],
                "skillsDirectory": ".agents/skills",
            }
        },
        "conda": {"environmentFile": "environment.yml", "channels": ["conda-forge"]},
    }
    (tmp_path / "toolchain-dependencies.json").write_text(json.dumps(manifest))
    schema = tmp_path / "schemas" / "configuration"
    schema.mkdir(parents=True)
    shutil.copy2(
        REPO_ROOT / "schemas/configuration/toolchain-dependencies.schema.json",
        schema / "toolchain-dependencies.schema.json",
    )
    module = load_module("agent_profile", REPO_ROOT / "tools" / "agent_profile.py")
    repository_root = tmp_path / "consumer"
    repository_root.mkdir()
    return module, module.AgentProfile(tmp_path, repository_root)


def test_missing_glab_uses_brew_then_verifies_installed_skill(tmp_path, monkeypatch):
    """Install glab, then require a plan that binds its bundled skill bytes.

    :param tmp_path: Isolated checkout root.
    :param monkeypatch: Host command fixture controller.
    """
    module, profile = _profile(tmp_path)
    assert profile.destination == tmp_path / "consumer" / ".agents" / "skills"
    state = {"glab": False, "bundle": b"bundled skill\n"}
    commands = []
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        module.shutil,
        "which",
        lambda name: (
            "/usr/local/bin/" + name
            if name == "brew" or (name == "glab" and state["glab"])
            else None
        ),
    )

    def run(command, _deadline):
        commands.append(command[:4])
        if command[:2] == ["brew", "install"]:
            state["glab"] = True
        elif command[:3] == ["glab", "skills", "install"]:
            skill = Path(command[5]) / "glab-stack"
            skill.mkdir()
            (skill / "SKILL.md").write_bytes(state["bundle"])
        elif command[:3] == ["glab", "skills", "get"]:
            return state["bundle"]
        return b""

    monkeypatch.setattr(profile, "_run", run)
    first_plan = profile.plan()
    with pytest.raises(module.AgentProfileError, match="glab_replan_required"):
        profile.apply(first_plan, time.monotonic() + 30)
    assert commands[0] == ["brew", "install", "glab"]
    assert not (profile.destination / "glab-stack").exists()

    second_plan = profile.plan()
    assert (
        second_plan["bundled"]["glab-stack"]
        == hashlib.sha256(state["bundle"]).hexdigest()
    )
    state["bundle"] = b"changed skill\n"
    with pytest.raises(
        module.AgentProfileError, match="installation_fingerprint_changed"
    ):
        profile.apply(second_plan, time.monotonic() + 30)
    assert not (profile.destination / "glab-stack").exists()
    state["bundle"] = b"bundled skill\n"
    result = profile.apply(second_plan, time.monotonic() + 30)

    assert result["skills"] == {"glab-stack": "installed"}
    assert (
        profile.destination / "glab-stack" / "SKILL.md"
    ).read_bytes() == b"bundled skill\n"


def test_missing_homebrew_blocks_and_preserves_existing_skill(tmp_path, monkeypatch):
    """A missing package manager never changes an existing skill.

    :param tmp_path: Isolated checkout root.
    :param monkeypatch: Host command fixture controller.
    """
    module, profile = _profile(tmp_path)
    skill = profile.destination / "glab-stack" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("operator copy\n")
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)

    with pytest.raises(module.AgentProfileError, match="brew_unavailable"):
        profile.plan()
    with pytest.raises(module.AgentProfileError, match="brew_unavailable"):
        profile.apply({"manifest_digest": profile.digest}, time.monotonic() + 30)

    assert skill.read_text() == "operator copy\n"


def test_linux_apt_uses_declared_glab_package(tmp_path, monkeypatch):
    """Linux chooses apt when the host exposes that package manager.

    :param tmp_path: Isolated checkout root.
    :param monkeypatch: Host command fixture controller.
    """
    module, profile = _profile(tmp_path)
    monkeypatch.setattr(module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        module.shutil,
        "which",
        lambda name: "/usr/bin/apt-get" if name == "apt-get" else None,
    )

    assert profile._host_install_command() == ["apt-get", "install", "-y", "glab"]


def test_existing_glab_without_bundled_skill_blocks_plan(tmp_path, monkeypatch):
    """Detect an incompatible installed glab before asking for confirmation.

    :param tmp_path: Isolated checkout root.
    :param monkeypatch: Host command fixture controller.
    """
    module, profile = _profile(tmp_path)
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/glab")

    def incompatible(_command, _deadline):
        raise module.AgentProfileError(module.AgentProfileReason.COMMAND_FAILED)

    monkeypatch.setattr(profile, "_run", incompatible)
    with pytest.raises(module.AgentProfileError, match="glab_incompatible"):
        profile.plan()


@pytest.mark.parametrize(
    "invalid",
    ("unknown_field", "missing_host", "wrong_skills", "wrong_version"),
)
def test_manifest_schema_rejects_invalid_contracts(tmp_path, invalid):
    """Reject unknown, missing, mistyped, and incompatible manifest fields.

    :param tmp_path: Isolated checkout root.
    :param invalid: Invalid manifest variant to construct.
    """
    module, _ = _profile(tmp_path)
    path = tmp_path / "toolchain-dependencies.json"
    data = json.loads(path.read_text())
    if invalid == "unknown_field":
        data["extra"] = True
    elif invalid == "missing_host":
        del data["profiles"]["agent"]["host"]
    elif invalid == "wrong_skills":
        data["profiles"]["agent"]["skills"] = 42
    else:
        data["schema_version"] = "2.0"
    path.write_text(json.dumps(data))

    with pytest.raises(module.AgentProfileError, match="agent_profile_invalid"):
        module.AgentProfile(tmp_path, tmp_path / "consumer")


@pytest.mark.parametrize("raw", (b'{"a":1,"a":2}', b'{"a":NaN}'))
def test_strict_json_rejects_duplicate_and_nonfinite_values(tmp_path, raw):
    """Reject JSON accepted by Python's permissive default decoder.

    :param tmp_path: Isolated checkout root.
    :param raw: Invalid JSON contract bytes.
    """
    module, _ = _profile(tmp_path)
    with pytest.raises(module.AgentProfileError, match="agent_profile_invalid"):
        module.AgentProfile._load_json(raw)
