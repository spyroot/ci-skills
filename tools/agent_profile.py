"""Install the agent host and bundled skills declared by the toolchain manifest.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping, MutableMapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Optional

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from lib.python.core.cli_api import response_decoder
from lib.python.core.json_types import (
    JSONObject,
    json_object,
    json_objects,
    json_string,
    json_strings,
)


class AgentProfileReason(StrEnum):
    """Stable installer failure reasons shared by the profile and CLI."""  # noqa: DOC601, DOC603

    PROFILE_INVALID = "agent_profile_invalid"
    DESTINATION_UNSAFE = "agent_destination_unsafe"
    REPOSITORY_UNAVAILABLE = "agent_repository_unavailable"
    SKILL_CONFLICT = "agent_skill_conflict"
    SKILL_INVALID = "agent_skill_invalid"
    COMMAND_FAILED = "agent_command_failed"
    GLAB_UNAVAILABLE = "glab_unavailable"
    GLAB_INCOMPATIBLE = "glab_incompatible"
    GLAB_REPLAN_REQUIRED = "glab_replan_required"
    BREW_UNAVAILABLE = "brew_unavailable"
    PACKAGE_MANAGER_UNAVAILABLE = "package_manager_unavailable"
    DEADLINE_EXPIRED = "install_deadline_expired"
    FINGERPRINT_CHANGED = "installation_fingerprint_changed"


class AgentProfileError(ValueError):
    """Report a safe, classified agent-profile installation failure."""

    def __init__(
        self,
        reason: AgentProfileReason,
        command: Optional[Sequence[str]] = None,
        exit_code: Optional[int] = None,
    ):
        """Store a classified failure without copying host command output.

        :param reason: Stable failure code.
        :param command: Safe failed command, if any.
        :param exit_code: Exit code when the process ran.
        """
        super().__init__(reason)
        self.reason = reason
        self.command = command
        self.exit_code = exit_code


class AgentProfile:
    """Install the source agent profile into the caller's repository."""

    def __init__(self, root: Path, repository_root: Path):
        """Load the source contract and resolve a repository skill destination.

        :param root: Source checkout containing the manifest and schema.
        :param repository_root: Root of the repository invoking the installer.
        :raises AgentProfileError: If the contract or destination is invalid.
        :raises OSError: If the manifest or schema cannot be read.
        """  # noqa: DOC503 - Manifest and schema file reads propagate OSError.
        self.root = root.resolve()
        self.repository_root = repository_root.resolve()
        self.manifest = self.root / "toolchain-dependencies.json"
        raw = self.manifest.read_bytes()
        self.digest = hashlib.sha256(raw).hexdigest()
        data = self._load_json(raw)
        schema = self._load_json(
            (
                self.root / "schemas/configuration/toolchain-dependencies.schema.json"
            ).read_bytes()
        )
        try:
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(data)
        except (SchemaError, ValidationError) as exc:
            raise AgentProfileError(AgentProfileReason.PROFILE_INVALID) from exc
        try:
            profile = json_object(json_object(data["profiles"])["agent"])
            hosts = json_strings(profile["host"])
            skills = json_strings(profile["skills"])
            relative = Path(json_string(profile["skillsDirectory"]))
            dependencies = [
                entry
                for entry in json_objects(data["dependencies"])
                if entry.get("name") == "glab"
            ]
            if len(dependencies) != 1:
                raise AgentProfileError(AgentProfileReason.PROFILE_INVALID)
            dependency = dependencies[0]
            commands = json_strings(dependency.get("commands"))
            packages = {
                manager: json_strings(dependency.get(manager, []))
                for manager in ("brew", "apt", "yum")
            }
            probe = json_strings(dependency.get("probe"))
        except (KeyError, TypeError) as exc:
            raise AgentProfileError(AgentProfileReason.PROFILE_INVALID) from exc
        if tuple(hosts) != ("glab",) or not skills:
            raise AgentProfileError(AgentProfileReason.PROFILE_INVALID)
        if any(not re.fullmatch(r"[a-z][a-z0-9-]*", name) for name in skills):
            raise AgentProfileError(AgentProfileReason.PROFILE_INVALID)
        if relative.is_absolute() or any(
            part in {"", ".", ".."} for part in relative.parts
        ):
            raise AgentProfileError(AgentProfileReason.PROFILE_INVALID)
        if tuple(commands) != ("glab",) or any(
            tuple(names) != ("glab",) for names in packages.values()
        ):
            raise AgentProfileError(AgentProfileReason.PROFILE_INVALID)
        if tuple(probe) != ("glab", "skills", "get", skills[0]):
            raise AgentProfileError(AgentProfileReason.PROFILE_INVALID)
        self.skills: Sequence[str] = skills
        self.packages: Mapping[str, Sequence[str]] = packages
        self.destination = self.repository_root / relative
        self._check_destination()

    @staticmethod
    def _load_json(raw: bytes) -> JSONObject:
        """Read a JSON contract using the shared finite, unique-key decoder.

        :param raw: UTF-8 manifest or schema bytes.
        :returns: Decoded object with finite JSON values and unique keys.
        :raises AgentProfileError: If encoding, nesting, keys, constants, or root shape is invalid.
        """
        try:
            return json_object(response_decoder().decode(raw.decode("utf-8")))
        except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
            raise AgentProfileError(AgentProfileReason.PROFILE_INVALID) from exc

    def _check_destination(self) -> None:
        """Reject symlinks in the caller repository's destination chain.

        :raises AgentProfileError: If a component redirects or is not a directory.
        """
        current = self.repository_root
        for part in self.destination.relative_to(self.repository_root).parts:
            current = current / part
            if current.is_symlink() or (current.exists() and not current.is_dir()):
                raise AgentProfileError(AgentProfileReason.DESTINATION_UNSAFE)

    @staticmethod
    def _run(command: Sequence[str], deadline: float) -> bytes:
        """Run one bounded host command and return its stdout.

        :param command: Program and arguments to execute.
        :param deadline: Monotonic deadline for this installation.
        :returns: Command stdout bytes.
        :raises AgentProfileError: If execution fails or exceeds the deadline.
        """
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AgentProfileError(AgentProfileReason.DEADLINE_EXPIRED)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                check=False,
                timeout=remaining,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AgentProfileError(AgentProfileReason.COMMAND_FAILED, command) from exc
        if completed.returncode:
            raise AgentProfileError(
                AgentProfileReason.COMMAND_FAILED, command, completed.returncode
            )
        return completed.stdout

    def _bundled_skill(self, name: str, deadline: float) -> bytes:
        """Read one skill from the installed glab release.

        :param name: Bundled skill name declared by the agent profile.
        :param deadline: Monotonic deadline for the glab read.
        :returns: Bundled ``SKILL.md`` bytes.
        :raises AgentProfileError: If glab cannot supply the skill.
        """  # noqa: DOC502
        return self._run(["glab", "skills", "get", name], deadline)

    def _host_install_command(self) -> Sequence[str]:
        """Select the manifest's package for the current host.

        :returns: Noninteractive package-manager command for ``glab``.
        :raises AgentProfileError: If this host has no supported manager.
        """
        if platform.system() == "Darwin":
            if not shutil.which("brew"):
                raise AgentProfileError(AgentProfileReason.BREW_UNAVAILABLE)
            return ["brew", "install", *self.packages["brew"]]
        if platform.system() != "Linux":
            raise AgentProfileError(AgentProfileReason.PACKAGE_MANAGER_UNAVAILABLE)
        if shutil.which("apt-get"):
            command = ["apt-get", "install", "-y", *self.packages["apt"]]
        elif shutil.which("dnf"):
            command = ["dnf", "install", "-y", *self.packages["yum"]]
        elif shutil.which("yum"):
            command = ["yum", "install", "-y", *self.packages["yum"]]
        else:
            raise AgentProfileError(AgentProfileReason.PACKAGE_MANAGER_UNAVAILABLE)
        if os.geteuid() != 0:
            if not shutil.which("sudo"):
                raise AgentProfileError(AgentProfileReason.PACKAGE_MANAGER_UNAVAILABLE)
            command = ["sudo", "-n", *command]
        return command

    def plan(self, deadline: Optional[float] = None) -> JSONObject:
        """Describe the host and skill actions without changing either.

        :param deadline: Optional monotonic deadline for bundled skill reads.
        :returns: A machine-readable plan bound to the manifest digest.
        :raises AgentProfileError: If destination paths are unsafe, glab is incompatible,
            a skill conflicts, or the host lacks a supported package manager.
        :raises OSError: If an existing bundled-skill file cannot be read.
        """  # noqa: DOC503 - Existing skill-file reads propagate OSError.
        self._check_destination()
        glab = shutil.which("glab")
        actions: MutableMapping[str, str] = {}
        bundled: MutableMapping[str, str] = {}
        for name in self.skills:
            if glab:
                try:
                    content = self._bundled_skill(
                        name,
                        deadline if deadline is not None else time.monotonic() + 10,
                    )
                except AgentProfileError as exc:
                    raise AgentProfileError(
                        AgentProfileReason.GLAB_INCOMPATIBLE,
                        exc.command,
                        exc.exit_code,
                    ) from exc
                bundled[name] = hashlib.sha256(content).hexdigest()
            target = self.destination / name
            skill_file = target / "SKILL.md"
            if target.is_symlink() or skill_file.is_symlink():
                raise AgentProfileError(AgentProfileReason.DESTINATION_UNSAFE)
            if target.exists():
                if not target.is_dir() or not skill_file.is_file():
                    raise AgentProfileError(AgentProfileReason.SKILL_CONFLICT)
                if glab and skill_file.read_bytes() != content:
                    raise AgentProfileError(AgentProfileReason.SKILL_CONFLICT)
                actions[name] = "pending" if not glab else "ready"
            else:
                actions[name] = "pending" if not glab else "install"
        host_action = (
            "ready" if glab else "install: " + " ".join(self._host_install_command())
        )
        return {
            "manifest_digest": self.digest,
            "destination": str(self.destination),
            "host": host_action,
            "skills": actions,
            "bundled": bundled,
        }

    def apply(self, expected_plan: JSONObject, deadline: float) -> JSONObject:
        """Bootstrap glab or install previously planned bundled skills.

        :param expected_plan: Agent state from the confirmed plan.
        :param deadline: Monotonic deadline for all host commands.
        :returns: Verified host and skill states.
        :raises AgentProfileError: If the plan changed, glab bootstrap requires a new plan,
            or command, path, bundle-content, or deadline validation fails.
        :raises OSError: If a bundle read, staging directory, rename, or final readback fails.
        """  # noqa: DOC503 - Planning and installation filesystem helpers propagate OSError.
        if self.plan(deadline) != expected_plan:
            raise AgentProfileError(AgentProfileReason.FINGERPRINT_CHANGED)
        self._check_destination()
        if not shutil.which("glab"):
            self._run(self._host_install_command(), deadline)
            if not shutil.which("glab"):
                raise AgentProfileError(AgentProfileReason.GLAB_UNAVAILABLE)
            raise AgentProfileError(AgentProfileReason.GLAB_REPLAN_REQUIRED)
        installed = {name: self._install_skill(name, deadline) for name in self.skills}
        return {
            "host": "ready",
            "skills": installed,
            "destination": str(self.destination),
        }

    def _install_skill(self, name: str, deadline: float) -> str:
        """Install one missing declared bundle and independently compare its bytes.

        :param name: Skill name already validated against the source profile.
        :param deadline: Monotonic deadline shared by all native installation commands.
        :returns: ready for an existing matching skill, or installed after verified promotion.
        :raises AgentProfileError: If commands fail, paths conflict, or staged/final bytes differ.
        :raises OSError: If staging, file reads, directory creation, or rename fails.
        """  # noqa: DOC503 - Staging, file reads, and rename propagate OSError.
        expected = self._bundled_skill(name, deadline)
        target = self.destination / name
        skill_file = target / "SKILL.md"
        if target.is_symlink() or skill_file.is_symlink():
            raise AgentProfileError(AgentProfileReason.DESTINATION_UNSAFE)
        if target.exists():
            if not skill_file.is_file() or skill_file.read_bytes() != expected:
                raise AgentProfileError(AgentProfileReason.SKILL_CONFLICT)
            return "ready"
        self.destination.mkdir(parents=True, exist_ok=True)
        self._check_destination()
        with tempfile.TemporaryDirectory(
            prefix=f".ci-skills-{name}-", dir=self.destination
        ) as temporary:
            stage = Path(temporary)
            self._run(
                ["glab", "skills", "install", name, "--path", str(stage)], deadline
            )
            staged = stage / name
            staged_file = staged / "SKILL.md"
            if (
                staged.is_symlink()
                or staged_file.is_symlink()
                or not staged_file.is_file()
            ):
                raise AgentProfileError(AgentProfileReason.SKILL_INVALID)
            if staged_file.read_bytes() != expected:
                raise AgentProfileError(AgentProfileReason.SKILL_INVALID)
            if target.exists() or target.is_symlink():
                raise AgentProfileError(AgentProfileReason.SKILL_CONFLICT)
            os.rename(staged, target)
        if skill_file.read_bytes() != expected:
            raise AgentProfileError(AgentProfileReason.SKILL_INVALID)
        return "installed"
