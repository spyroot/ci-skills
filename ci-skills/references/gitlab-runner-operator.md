# GitLab Runner Operator on OpenShift

Native Operator resources; ci-skills does not install this Operator.
Prerequisites: [installed Operator][install], its `Runner` CRD, chosen namespace,
and `gitlab-runner-secret`, a caller-created Secret whose `runner-token` value
is read from the token file produced by `gitlab_runner.py create --token-out`.
Do not also supply `runner-registration-token`.

## Properties

[Configuration source][source], checked 2026-10-18:

| `spec` key | Meaning | Operator support |
| --- | --- | --- |
| `env` | ConfigMap keys become manager Pod environment variables. | All |
| `config` | ConfigMap `config.toml` provides a Runner configuration template. | All |
| `concurrent` | Job concurrency; default `10`; `0` is not unlimited. | All |
| `podSpec` | Manager Pod template patches. | 1.23+ |
| `logLevel`, `logFormat`, `shutdownTimeout` | Logging and shutdown controls. | 1.34+ |
| `deploymentSpec` | Manager Deployment template patches. | 1.40+ |

Use `env` for `RUNNER_REQUEST_CONCURRENCY`, `RUNNER_OUTPUT_LIMIT`,
`KUBERNETES_POLL_TIMEOUT` and `KUBERNETES_NAMESPACE`: upstream documents a
configuration-template limitation for those settings.

## Example resources

Save as caller-selected `runner.yaml`; replace the GitLab URL. Both ConfigMaps
are defined here and referenced by the Runner in the same namespace:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: runner-env
data:
  RUNNER_REQUEST_CONCURRENCY: "2"
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: runner-template
data:
  config.toml: |
    [[runners]]
      [runners.kubernetes]
        cpu_limit = "500m"
---
apiVersion: apps.gitlab.com/v1beta2
kind: Runner
metadata:
  name: build-linux
spec:
  gitlabUrl: https://gitlab.example.com
  token: gitlab-runner-secret
  env: runner-env
  config: runner-template
  concurrent: 2
```

`KUBECONFIG_PATH`, `CONTEXT` and `NAMESPACE` select the caller's destination:

```bash
oc --kubeconfig "$KUBECONFIG_PATH" --context "$CONTEXT" \
  --namespace "$NAMESPACE" apply -f runner.yaml
oc --kubeconfig "$KUBECONFIG_PATH" --context "$CONTEXT" \
  --namespace "$NAMESPACE" get runner build-linux -o yaml
```

Resource read-back is not proof of job execution; verify an actual job's runner
identity and result through `gitlab_job.py`.

## Related

- [Runner inputs](gitlab-runner-config.md): record/token and native manager route.
- [Feature flags](gitlab-runner-feature-flags.md): scope and defaults.
- [Cache tuning](gitlab-runner-cache.md): backend-dependent transfer settings.

[source]: https://docs.gitlab.com/runner/configuration/configuring_runner_operator/
[install]: https://docs.gitlab.com/runner/install/operator/
