"""Portable receipts retain source identity without publishing host paths."""

from __future__ import annotations

from conftest import import_script_module


def test_project_source_and_recorded_query_paths_are_portable() -> None:
    portable = import_script_module("core.portable")
    local = {
        "target_selection": "env:CI_SKILLS_TARGET -> file:/private/project/target.toml",
        "credential_sources": {
            "kubernetes": (
                "binding:/private/project/source.toml#2:"
                "command:/private/project/get-cluster -> file:/private/kube/config"
            )
        },
        "queries": {
            "pods": [
                "oc",
                "--kubeconfig",
                "/private/kube/config",
                "--context",
                "selected-context",
            ]
        },
    }

    rendered = portable.portable(local)

    assert rendered["target_selection"].startswith("env:CI_SKILLS_TARGET -> file:path:")
    assert rendered["credential_sources"]["kubernetes"].startswith("binding:path:")
    assert "command:path:" in rendered["credential_sources"]["kubernetes"]
    assert "file:path:" in rendered["credential_sources"]["kubernetes"]
    assert rendered["queries"]["pods"][2].startswith("path:")
    assert "/private/" not in str(rendered)
