# Harbor Helm OCI inputs

Harbor stores packaged Helm charts as OCI artifacts. The commands below are
native Helm commands; ci-skills image push/pull commands do not implement
chart installation. Sources: [Harbor 2.15][harbor], [Helm OCI][helm] and Helm
4.3.1 [login][login]/[upgrade][upgrade] commands, checked 2026-10-18.
OCI support is generally available from Helm 3.8.

## Values

| Value | Supply |
| --- | --- |
| `HARBOR_HOST` | Registry hostname, optionally port; no `https://` or project. |
| `HARBOR_PROJECT` | Existing writable project, not a proxy-cache project. |
| `HARBOR_USERNAME` | Username or full robot account name. |
| `HARBOR_PASSWORD_FILE` | File containing password, robot secret or OIDC CLI secret. |
| `CHART_DIR` | Caller-owned chart containing `Chart.yaml` and templates. |
| `CHART_PACKAGE` | `.tgz` file returned by `helm package`. |
| `CHART_NAME`, `CHART_VERSION` | `name` and `version` from `Chart.yaml`. |

## Login, package, push, read

These environment variables are caller-assigned example values. Helm receives
the password on standard input, not in its command-line arguments.

```bash
helm registry login "$HARBOR_HOST" \
  --username "$HARBOR_USERNAME" --password-stdin < "$HARBOR_PASSWORD_FILE"
helm package "$CHART_DIR"
helm push "$CHART_PACKAGE" "oci://$HARBOR_HOST/$HARBOR_PROJECT"
helm show chart "oci://$HARBOR_HOST/$HARBOR_PROJECT/$CHART_NAME" \
  --version "$CHART_VERSION"
```

Push names the project only: Helm derives the artifact name/version from the
package. Read/pull/install names the chart and selects `--version`.

## Install on a selected cluster

Supply `KUBECONFIG_PATH`, `CONTEXT`, `NAMESPACE`, `RELEASE` and `VALUES_FILE` for
the caller's intended cluster and chart settings. This command changes that
cluster; publishing or inspecting a chart does not require it. The namespace
must already exist; add `--create-namespace` only when the caller chooses to
create it.

```bash
helm upgrade --install "$RELEASE" \
  "oci://$HARBOR_HOST/$HARBOR_PROJECT/$CHART_NAME" \
  --version "$CHART_VERSION" --values "$VALUES_FILE" \
  --kubeconfig "$KUBECONFIG_PATH" --kube-context "$CONTEXT" \
  --namespace "$NAMESPACE"
helm status "$RELEASE" \
  --kubeconfig "$KUBECONFIG_PATH" --kube-context "$CONTEXT" \
  --namespace "$NAMESPACE"
```

Read back the release with [helm status][status] in that same context and
namespace; chart publication alone does not establish a deployed release.

## Related

- [Harbor authentication](harbor-authentication.md): robot and OIDC credential choices.
- `harbor_push.py`: publish container images; `harbor_pull.py`: verify a toolbox image.
- [Toolbox specification](../schemas/toolbox.schema.json): existing image build inputs.

[harbor]: https://goharbor.io/docs/2.15.0/working-with-projects/working-with-oci/working-with-helm-oci-charts/
[helm]: https://helm.sh/docs/topics/registries/
[login]: https://helm.sh/docs/helm/helm_registry_login/
[upgrade]: https://helm.sh/docs/helm/helm_upgrade/
[status]: https://helm.sh/docs/helm/helm_status/
