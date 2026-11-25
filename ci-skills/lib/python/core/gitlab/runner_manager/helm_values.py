"""Build official gitlab-runner chart values for the kubernetes executor.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from typing import Final, Optional

RUNNER_CHART_REPOSITORY: Final = "https://charts.gitlab.io"
RUNNER_TOKEN_SECRET_KEY: Final = "runner-token"
MANAGER_CONCURRENCY: Final = 1

UBI_IMAGE_REGISTRY: Final = "registry.gitlab.com"
UBI_MANAGER_IMAGE: Final = "gitlab-org/ci-cd/gitlab-runner-ubi-images/gitlab-runner-ocp"
UBI_HELPER_IMAGE: Final = (
    "registry.gitlab.com/gitlab-org/ci-cd/gitlab-runner-ubi-images/"
    "gitlab-runner-helper-ocp"
)

# GitLab's rule list for the kubernetes executor with default settings and
# feature flags. Chart 0.94 and earlier ignore resourceNames/nonResourceURLs
# and grant more; chart 0.95 rejects them, so neither is used (see
# references/gitlab-runner-helm.md).
EXECUTOR_RBAC_RULES: Final = (
    {
        "apiGroups": [""],
        "resources": ["events"],
        "verbs": ["list"],
    },
    {
        "apiGroups": [""],
        "resources": ["pods"],
        "verbs": ["create", "delete", "get", "list", "watch"],
    },
    {
        "apiGroups": [""],
        "resources": ["pods/attach", "pods/exec"],
        "verbs": ["create", "delete", "get", "patch"],
    },
    {
        "apiGroups": [""],
        "resources": ["pods/log"],
        "verbs": ["get", "list"],
    },
    {
        "apiGroups": [""],
        "resources": ["secrets"],
        "verbs": ["create", "delete", "get", "update"],
    },
    {
        "apiGroups": [""],
        "resources": ["serviceaccounts"],
        "verbs": ["get"],
    },
    {
        "apiGroups": [""],
        "resources": ["services"],
        "verbs": ["create", "get"],
    },
)


def runner_chart_config(app_version: str, pull_secrets: tuple[str, ...]) -> str:
    """Render the runner TOML config selecting the kubernetes executor.

    :param app_version: Selected chart ``appVersion`` (runner version).
    :param pull_secrets: Existing job-namespace pull Secret names.
    :returns: TOML config value for ``runners.config``.
    """
    secrets = ", ".join(f'"{name}"' for name in pull_secrets)
    return (
        "[[runners]]\n"
        "  [runners.kubernetes]\n"
        f'    helper_image = "{UBI_HELPER_IMAGE}:x86_64-v{app_version}"\n'
        f"    image_pull_secrets = [{secrets}]\n"
        "    [runners.kubernetes.pod_security_context]\n"
        "      run_as_non_root = true\n"
    )


def runner_chart_values(
    *,
    gitlab_url: str,
    secret_name: str,
    app_version: str,
    pull_secrets: tuple[str, ...] = (),
    certs_secret_name: Optional[str] = None,
) -> dict[str, object]:
    """Build nonsecret chart values for one kubernetes-executor manager.

    The runner token never appears in values or Helm storage; the chart reads
    it from the existing Secret named by ``runners.secret``.

    :param gitlab_url: GitLab server the runner authenticates to.
    :param secret_name: Existing Secret holding key ``runner-token``.
    :param app_version: Selected chart ``appVersion`` (runner version).
    :param pull_secrets: Existing job-namespace pull Secret names.
    :param certs_secret_name: Optional server certificate Secret.
    :returns: Values accepted by HelmClient render and install paths.
    """
    values: dict[str, object] = {
        "gitlabUrl": gitlab_url,
        "concurrent": MANAGER_CONCURRENCY,
        "image": {
            "registry": UBI_IMAGE_REGISTRY,
            "image": UBI_MANAGER_IMAGE,
            "tag": f"v{app_version}",
        },
        "useTini": True,
        "podSecurityContext": {"runAsUser": None, "fsGroup": None},
        "securityContext": {"runAsNonRoot": True},
        "rbac": {
            "create": True,
            "clusterWideAccess": False,
            "rules": [dict(rule) for rule in EXECUTOR_RBAC_RULES],
        },
        "runners": {
            "secret": secret_name,
            "config": runner_chart_config(app_version, pull_secrets),
        },
    }
    if certs_secret_name is not None:
        values["certsSecretName"] = certs_secret_name
    return values
