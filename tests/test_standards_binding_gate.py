"""The pinned Standards gate checks the actual Git object and manifest."""

from __future__ import annotations

import json
import subprocess
import sys

import yaml
from conftest import REPO_ROOT, load_module

sys.path.insert(0, str(REPO_ROOT / "tools"))
GATE = load_module(
    "check_standards_binding", REPO_ROOT / "tools" / "check_standards_binding.py"
)


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _fixture(tmp_path):
    standards = tmp_path / "standards"
    project = tmp_path / "project"
    standards.mkdir()
    project.mkdir()
    _git(standards, "init", "-q")
    _git(standards, "config", "user.name", "Unit")
    _git(standards, "config", "user.email", "unit@example.test")
    _git(standards, "remote", "add", "origin", "https://github.com/example/standards")
    (standards / "schemas").mkdir()
    (standards / "contracts").mkdir()
    (standards / "contracts" / "ci.md").write_text("# CI\n", encoding="utf-8")
    (standards / "schemas" / "binding.json").write_text(
        json.dumps({"type": "object", "required": ["spec"]}), encoding="utf-8"
    )
    manifest = {
        "spec": {
            "authority": {
                "localPath": "~/dev/standards",
                "revisionPolicy": "exact-commit",
            },
            "readOrder": [{"id": "ci", "path": "contracts/ci.md", "required": True}],
            "schemas": [{"id": "binding", "path": "schemas/binding.json"}],
            "consumer": {
                "bindingFile": "standards-binding.yaml",
                "bindingSchema": "binding",
                "forbiddenTrackedPointers": ["STANDARDS.md"],
            },
        }
    }
    (standards / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    _git(standards, "add", ".")
    _git(standards, "commit", "-qm", "pin fixture")
    revision = _git(standards, "rev-parse", "HEAD")
    _git(project, "init", "-q")
    binding = {
        "spec": {
            "source": {
                "localPath": "~/dev/standards",
                "repository": "https://github.com/example/standards",
                "revision": revision,
            },
            "requiredContracts": ["ci"],
            "providers": [],
            "exceptions": [],
        }
    }
    path = project / "standards-binding.yaml"
    path.write_text(yaml.safe_dump(binding), encoding="utf-8")
    _git(project, "add", "standards-binding.yaml")
    return project, standards, binding


def test_exact_committed_standard_and_required_manifest_pass(tmp_path):
    project, standards, _binding = _fixture(tmp_path)
    assert GATE.check(project, standards) == []


def test_missing_required_contract_is_rejected(tmp_path):
    project, standards, binding = _fixture(tmp_path)
    binding["spec"]["requiredContracts"] = []
    (project / "standards-binding.yaml").write_text(
        yaml.safe_dump(binding), encoding="utf-8"
    )
    assert "required_contracts_mismatch" in GATE.check(project, standards)


def test_wrong_revision_is_rejected(tmp_path):
    project, standards, binding = _fixture(tmp_path)
    binding["spec"]["source"]["revision"] = "f" * 40
    (project / "standards-binding.yaml").write_text(
        yaml.safe_dump(binding), encoding="utf-8"
    )
    assert "standards_revision_mismatch" in GATE.check(project, standards)
