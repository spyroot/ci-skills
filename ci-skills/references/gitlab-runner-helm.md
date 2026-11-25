# GitLab Runner Helm chart on OpenShift

The official `gitlab-runner` chart deploys one runner manager that uses the Kubernetes executor: the manager Pod
creates one job Pod per CI/CD job in the job namespace. No ci-skills command installs this chart yet; CI15 PR-004
plans `gitlab_runner_manager.py --executor kubernetes`. Sources: [install][install], [configuration][configure],
[chart values][values] and [runner versions][versions], checked 2026-11-21.

## Version

GitLab documents the rule: keep GitLab Runner on the same major and minor version as the GitLab instance and install
the latest patch. Backward compatibility is guaranteed between minor versions, but some features need the same
minor version. The chart version differs from the runner version; each chart entry's `appVersion` is the runner
version.

1. Read the instance version: `GET /api/v4/version` returns `version`, for example `18.5.2-ee`; keep `18.5`.
2. Read the chart index and select the highest chart `version` whose `appVersion` starts with `18.5.`.
3. When no chart matches, which happens when the instance runs ahead of released runners, select the highest
   `appVersion` below the instance version in the same major, and report the difference.
4. Install that chart version. The chart's default image tag is `alpine-v{{.Chart.AppVersion}}`, so the manager
   image follows the chart. The helper image tag uses the same runner version (`x86_64-v<appVersion>`).

Reads on 2026-11-21:

| GitLab instance | Instance `version` | Chart `version` | Runner `appVersion` |
| --- | --- | --- | --- |
| Self-managed | `18.5.2-ee` | 0.82.0 | 18.5.0 (exact minor) |
| GitLab.com | `19.7.0-pre` | 0.95.0 | 19.6.0 (no 19.7 chart yet; one minor behind) |

```bash
curl -sS https://charts.gitlab.io/index.yaml \
  | yq -r '.entries."gitlab-runner"[] | .version + " " + .appVersion'
```

Charts `0.92.2` and later are also OCI artifacts at
`oci://registry.gitlab.com/charts/charts.gitlab.io/release/gitlab-runner`. Earlier charts come only from the
`https://charts.gitlab.io` repository.

## Values

| Key | Value | Effect |
| --- | --- | --- |
| `gitlabUrl` | `https://gitlab.example.com/` | GitLab server the runner authenticates to |
| `runners.secret` | `gitlab-runner` | Existing Secret with key `runner-token`; no token in values or Helm storage |
| `runners.jobNamespace` | unset | Job Pods run in the release namespace; a set value moves the job Role there |
| `rbac.create` | `true` | Chart creates ServiceAccount, Role and RoleBinding |
| `rbac.clusterWideAccess` | `false` | `true` creates a ClusterRole; needed only for cluster-scoped resources |
| `rbac.rules` | rules below | Replaces the default rule (all verbs on all core resources) |
| `concurrent` | `10` | Maximum parallel jobs for this manager |
| `runners.config` | TOML template | [Kubernetes executor keys](gitlab-runner.md#kubernetes-executor) |
| `certsSecretName` | Secret name | Key `<gitlab.hostname>.crt`; set when the server certificate is not public |

The runner token comes from `gitlab_runner.py create --token-out`; create the Secret with key `runner-token` before
installation. Do not set `runnerToken` or `runnerRegistrationToken` in values.

### Rules for the default executor settings

GitLab lists these rules for the executor with its default settings and feature flags. Do not use `resourceNames` or
`nonResourceURLs`; chart `0.94` and earlier ignore them and grant more, and `0.95` rejects them. Recheck the
[runner API permissions][permissions] on every runner upgrade.

```yaml
rbac:
  create: true
  rules:
    - apiGroups: [""]
      resources: ["events"]
      verbs: ["list"]
    - apiGroups: [""]
      resources: ["pods"]
      verbs: ["create", "delete", "get", "list", "watch"]
    - apiGroups: [""]
      resources: ["pods/attach", "pods/exec"]
      verbs: ["create", "delete", "get", "patch"]
    - apiGroups: [""]
      resources: ["pods/log"]
      verbs: ["get", "list"]
    - apiGroups: [""]
      resources: ["secrets"]
      verbs: ["create", "delete", "get", "update"]
    - apiGroups: [""]
      resources: ["serviceaccounts"]
      verbs: ["get"]
    - apiGroups: [""]
      resources: ["services"]
      verbs: ["create", "get"]
```

## OpenShift images

The default Alpine images do not run as a non-root user. Use the UBI images built for that case; they run with any
user ID that belongs to the root group. The UBI images lack `dumb-init`, so set `useTini: true`.

```yaml
image:
  registry: registry.gitlab.com
  image: gitlab-org/ci-cd/gitlab-runner-ubi-images/gitlab-runner-ocp
  tag: v18.5.0
useTini: true
podSecurityContext:
  runAsUser: null
  fsGroup: null
securityContext:
  runAsNonRoot: true
runners:
  config: |
    [[runners]]
      [runners.kubernetes]
        helper_image = "registry.gitlab.com/gitlab-org/ci-cd/gitlab-runner-ubi-images/gitlab-runner-helper-ocp:x86_64-v18.5.0"
        image_pull_secrets = ["harbor-pull"]
        [runners.kubernetes.pod_security_context]
          run_as_non_root = true
```

Chart 0.82.0 reads the image path from `image.image`; later charts add `image.repository` and still honor
`image.image`, so `image.image` works for both. Chart 0.82.0 also defaults `podSecurityContext` to `runAsUser: 100`
and `fsGroup: 65533`, and the GitLab example fixes `runAsUser: 999` and `run_as_user = 59417`. Under the
`restricted-v2` SCC a fixed user ID outside the namespace range is rejected, so the values above remove the chart
defaults with `null` and set no user ID ([OpenShift SCC access](openshift-scc.md)). `image_pull_secrets` names an
existing pull Secret in the job namespace for job images from Harbor.

## Lifecycle

| Step | Command shape | Precondition |
| --- | --- | --- |
| Install | `helm install --namespace NS RELEASE CHART --version CHART_VERSION -f values.yaml` | Token Secret exists |
| Upgrade | `helm upgrade --namespace NS -f values.yaml RELEASE CHART --version CHART_VERSION` | Runner paused |
| Uninstall | `helm delete --namespace NS RELEASE` | Runner paused |

Pause the runner and let running jobs finish first; this prevents authorization errors when jobs complete.
Resource read-back is not proof of job execution; verify an actual job's runner identity and result through
`gitlab_job.py`.

Kubernetes 1.19 rejects the chart's metrics port without `protocol: TCP`; later versions accept it.

## Installing through the GitLab agent

GitLab also documents [installing the chart through the agent][agent-install]: render the chart with
`helm template`, commit the manifest and list it under the agent's `gitops.manifest_projects`. GitLab deprecated
that pull-based agent feature in 16.2, announced its removal for 17.0, and names Flux as the replacement
([deprecation][deprecation]). The rendered manifest also contains the token Secret unless Sealed Secrets or SOPS
encrypts it.

## Related

- [Kubernetes executor keys](gitlab-runner.md#kubernetes-executor): `config.toml` settings for `runners.config`.
- [Runner inputs](gitlab-runner-config.md): runner record and token file.
- [OpenShift Operator](gitlab-runner-operator.md): the Operator route to the same manager.

[install]: https://docs.gitlab.com/runner/install/kubernetes/
[configure]: https://docs.gitlab.com/runner/install/kubernetes_helm_chart_configuration/
[values]: https://gitlab.com/gitlab-org/charts/gitlab-runner/blob/main/values.yaml
[versions]: https://docs.gitlab.com/runner/#gitlab-runner-versions
[permissions]: https://docs.gitlab.com/runner/executors/kubernetes/#configure-runner-api-permissions
[agent-install]: https://docs.gitlab.com/runner/install/kubernetes-agent/
[deprecation]: https://docs.gitlab.com/update/deprecations/
