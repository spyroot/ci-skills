"""Focused unit contracts for the kubernetes-executor chart values.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import pytest
from conftest import import_script_module

VALUES = import_script_module("lib.python.core.gitlab.runner_manager.helm_values")


def test_values_bind_secret_and_never_a_token() -> None:
    built = VALUES.runner_chart_values(
        gitlab_url="https://gitlab.example.com/",
        secret_name="ci-manager-token",
        app_version="18.5.0",
    )
    assert built["runners"]["secret"] == "ci-manager-token"
    encoded = repr(built)
    assert "runner-token" not in encoded  # key name stays out of values/storage
    assert "runnerRegistrationToken" not in encoded


def test_bounded_concurrency_and_namespaced_rbac() -> None:
    built = VALUES.runner_chart_values(
        gitlab_url="https://gitlab.example.com/",
        secret_name="token",
        app_version="18.5.0",
    )
    assert built["concurrent"] == 1
    rbac = built["rbac"]
    assert rbac["create"] is True
    assert rbac["clusterWideAccess"] is False
    assert rbac["rules"] == [dict(rule) for rule in VALUES.EXECUTOR_RBAC_RULES]
    assert all("resourceNames" not in rule for rule in rbac["rules"])
    assert all("nonResourceURLs" not in rule for rule in rbac["rules"])


def test_ubi_images_follow_app_version() -> None:
    built = VALUES.runner_chart_values(
        gitlab_url="https://gitlab.example.com/",
        secret_name="token",
        app_version="18.5.0",
    )
    assert built["image"]["tag"] == "v18.5.0"
    assert built["useTini"] is True
    config = built["runners"]["config"]
    assert "gitlab-runner-helper-ocp:x86_64-v18.5.0" in config
    assert "run_as_non_root = true" in config


def test_fixed_user_ids_are_removed() -> None:
    built = VALUES.runner_chart_values(
        gitlab_url="https://gitlab.example.com/",
        secret_name="token",
        app_version="18.5.0",
    )
    assert built["podSecurityContext"] == {"runAsUser": None, "fsGroup": None}
    assert built["securityContext"] == {"runAsNonRoot": True}


def test_pull_secrets_render_into_config() -> None:
    built = VALUES.runner_chart_values(
        gitlab_url="https://gitlab.example.com/",
        secret_name="token",
        app_version="18.5.0",
        pull_secrets=("harbor-pull",),
    )
    assert 'image_pull_secrets = ["harbor-pull"]' in built["runners"]["config"]


def test_certs_secret_is_optional() -> None:
    without = VALUES.runner_chart_values(
        gitlab_url="https://gitlab.example.com/",
        secret_name="token",
        app_version="18.5.0",
    )
    assert "certsSecretName" not in without
    with_certs = VALUES.runner_chart_values(
        gitlab_url="https://gitlab.example.com/",
        secret_name="token",
        app_version="18.5.0",
        certs_secret_name="gitlab-example-com-crt",
    )
    assert with_certs["certsSecretName"] == "gitlab-example-com-crt"


@pytest.mark.parametrize("app_version", ["17.8.3", "18.5.0", "19.1.7"])
def test_values_serialize_as_json(app_version: str) -> None:
    """Values must pass the HelmClient JSON encoding on every version."""
    import json

    built = VALUES.runner_chart_values(
        gitlab_url="https://gitlab.example.com/",
        secret_name="token",
        app_version=app_version,
        pull_secrets=("harbor-pull",),
    )
    json.dumps(built, allow_nan=False)
