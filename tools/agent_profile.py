"""Install the agent host and bundled skills declared by the toolchain manifest.

Author Mustafa Bayramov
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError


class AgentProfileError(ValueError):
    """Report a safe, classified agent-profile installation failure."""

    def __init__(
        self,
        reason: str,
        command: list[str] | None = None,
        exit_code: int | None = None,
    ):
        """Store a classified failure without copying host command output.

        :param reason: Stable failure code.
        :param command: Safe failed command, if any.
        :param exit_code: Exit code when the process ran.
        """
        super().__init__(reason)
        self.command = command
        self.exit_code = exit_code


class AgentProfile:
    """Resolve and install the manifest's agent profile for one checkout."""

    def __init__(self, root: Path):
        """Load and validate the checkout's agent toolchain contract.

        :param root: Checkout containing the manifest and its schema.
        :raises AgentProfileError: If the contract or destination is invalid.
        :raises OSError: If the manifest or schema cannot be read.
        """
        self.root = root.resolve()
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
            raise AgentProfileError("agent_profile_invalid") from exc
        profile = data["profiles"]["agent"]
        hosts = profile["host"]
        skills = profile["skills"]
        relative = Path(profile["skillsDirectory"])
        if hosts != ["glab"] or not isinstance(skills, list) or not skills:
            raise AgentProfileError("agent_profile_invalid")
        if any(
            not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", name)
            for name in skills
        ):
            raise AgentProfileError("agent_profile_invalid")
        if relative.is_absolute() or any(
            part in {"", ".", ".."} for part in relative.parts
        ):
            raise AgentProfileError("agent_profile_invalid")
        dependencies = [
            entry for entry in data["dependencies"] if entry.get("name") == "glab"
        ]
        if len(dependencies) != 1 or dependencies[0].get("commands") != ["glab"]:
            raise AgentProfileError("agent_profile_invalid")
        self.packages = {
            manager: dependencies[0].get(manager, [])
            for manager in ("brew", "apt", "yum")
        }
        if any(packages != ["glab"] for packages in self.packages.values()):
            raise AgentProfileError("agent_profile_invalid")
        if dependencies[0].get("probe") != ["glab", "skills", "get", skills[0]]:
            raise AgentProfileError("agent_profile_invalid")
        self.skills = skills
        self.destination = self.root / relative
        self._check_destination()

    @staticmethod
    def _load_json(raw: bytes) -> dict[str, Any]:
        """Parse one JSON contract without duplicate keys or nonfinite values.

        :param raw: UTF-8 JSON bytes.
        :returns: Decoded JSON object.
        :raises AgentProfileError: If the contract is malformed.
        """

        def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            """Reject duplicate object keys during JSON decoding.

            :param pairs: Ordered object members from the decoder.
            :returns: Object with unique keys.
            :raises AgentProfileError: If a key occurs twice.
            """
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise AgentProfileError("agent_profile_invalid")
                result[key] = value
            return result

        def nonfinite(_value: str) -> None:
            """Reject a nonfinite JSON constant.

            :param _value: Nonfinite token supplied by the decoder.
            :raises AgentProfileError: Every invocation rejects the token.
            """
            raise AgentProfileError("agent_profile_invalid")

        try:
            parsed = json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)
        except (UnicodeError, ValueError) as exc:
            raise AgentProfileError("agent_profile_invalid") from exc
        if not isinstance(parsed, dict):
            raise AgentProfileError("agent_profile_invalid")
        return parsed

    def _check_destination(self) -> None:
        """Reject symlinks in the checkout-local destination chain.

        :raises AgentProfileError: If a component redirects or is not a directory.
        """
        current = self.root
        for part in self.destination.relative_to(self.root).parts:
            current = current / part
            if current.is_symlink() or (current.exists() and not current.is_dir()):
                raise AgentProfileError("agent_destination_unsafe")

    @staticmethod
    def _run(command: list[str], deadline: float) -> bytes:
        """Run one bounded host command and return its stdout.

        :param command: Program and arguments to execute.
        :param deadline: Monotonic deadline for this installation.
        :returns: Command stdout bytes.
        :raises AgentProfileError: If execution fails or exceeds the deadline.
        """
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AgentProfileError("install_deadline_expired")
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                check=False,
                timeout=remaining,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AgentProfileError("agent_command_failed", command) from exc
        if completed.returncode:
            raise AgentProfileError(
                "agent_command_failed", command, completed.returncode
            )
        return completed.stdout

    def _bundled_skill(self, name: str, deadline: float) -> bytes:
        """Read one skill from the installed glab release.

        :param name: Bundled skill name declared by the agent profile.
        :param deadline: Monotonic deadline for the glab read.
        :returns: Bundled ``SKILL.md`` bytes.
        :raises AgentProfileError: If glab cannot supply the skill.
        """
        return self._run(["glab", "skills", "get", name], deadline)

    def _host_install_command(self) -> list[str]:
        """Select the manifest's package for the current host.

        :returns: Noninteractive package-manager command for ``glab``.
        :raises AgentProfileError: If this host has no supported manager.
        """
        if platform.system() == "Darwin":
            if not shutil.which("brew"):
                raise AgentProfileError("brew_unavailable")
            return ["brew", "install", *self.packages["brew"]]
        if platform.system() != "Linux":
            raise AgentProfileError("package_manager_unavailable")
        if shutil.which("apt-get"):
            command = ["apt-get", "install", "-y", *self.packages["apt"]]
        elif shutil.which("dnf"):
            command = ["dnf", "install", "-y", *self.packages["yum"]]
        elif shutil.which("yum"):
            command = ["yum", "install", "-y", *self.packages["yum"]]
        else:
            raise AgentProfileError("package_manager_unavailable")
        if os.geteuid() != 0:
            if not shutil.which("sudo"):
                raise AgentProfileError("package_manager_unavailable")
            command = ["sudo", "-n", *command]
        return command

    def plan(self, deadline: float | None = None) -> dict[str, Any]:
        """Describe the host and skill actions without changing either.

        :param deadline: Optional monotonic deadline for bundled skill reads.
        :returns: A machine-readable plan bound to the manifest digest.
        :raises AgentProfileError: If a current skill conflicts with the bundle.
        """
        self._check_destination()
        glab = shutil.which("glab")
        actions: dict[str, str] = {}
        bundled: dict[str, str] = {}
        for name in self.skills:
            if glab:
                try:
                    content = self._bundled_skill(
                        name, deadline or time.monotonic() + 10
                    )
                except AgentProfileError as exc:
                    raise AgentProfileError(
                        "glab_incompatible", exc.command, exc.exit_code
                    ) from exc
                bundled[name] = hashlib.sha256(content).hexdigest()
            target = self.destination / name
            skill_file = target / "SKILL.md"
            if target.is_symlink() or skill_file.is_symlink():
                raise AgentProfileError("agent_destination_unsafe")
            if target.exists():
                if not target.is_dir() or not skill_file.is_file():
                    raise AgentProfileError("agent_skill_conflict")
                if glab and skill_file.read_bytes() != content:
                    raise AgentProfileError("agent_skill_conflict")
                actions[name] = "verify" if not glab else "ready"
            else:
                actions[name] = "install"
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

    def apply(self, expected_plan: dict[str, Any], deadline: float) -> dict[str, Any]:
        """Install glab and its bundled skills, preserving existing content.

        :param expected_plan: Agent state from the confirmed plan.
        :param deadline: Monotonic deadline for all host commands.
        :returns: Verified host and skill states.
        :raises AgentProfileError: If the plan changed or an install fails.
        """
        if self.plan(deadline) != expected_plan:
            raise AgentProfileError("installation_fingerprint_changed")
        self._check_destination()
        if not shutil.which("glab"):
            self._run(self._host_install_command(), deadline)
            if not shutil.which("glab"):
                raise AgentProfileError("glab_unavailable")
        installed: dict[str, str] = {}
        for name in self.skills:
            expected = self._bundled_skill(name, deadline)
            target = self.destination / name
            skill_file = target / "SKILL.md"
            if target.is_symlink() or skill_file.is_symlink():
                raise AgentProfileError("agent_destination_unsafe")
            if target.exists():
                if not skill_file.is_file() or skill_file.read_bytes() != expected:
                    raise AgentProfileError("agent_skill_conflict")
                installed[name] = "ready"
                continue
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
                    raise AgentProfileError("agent_skill_invalid")
                if staged_file.read_bytes() != expected:
                    raise AgentProfileError("agent_skill_invalid")
                if target.exists() or target.is_symlink():
                    raise AgentProfileError("agent_skill_conflict")
                os.rename(staged, target)
            if skill_file.read_bytes() != expected:
                raise AgentProfileError("agent_skill_invalid")
            installed[name] = "installed"
        return {
            "host": "ready",
            "skills": installed,
            "destination": str(self.destination),
        }
