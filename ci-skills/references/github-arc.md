# GitHub Actions Runner Controller scale sets

Actions Runner Controller (ARC) runs ephemeral GitHub runners in Kubernetes. Each runner Pod takes one job and is
removed; the controller creates the next Pod. No ci-skills command installs ARC yet; CI15 PR-003 plans
`create_selfhosted_runner.py --runner-mode ephemeral`. Sources: [chart values][values], [release process][release] and
[runner image contract][image], checked 2026-11-21.

## Charts and version

Both charts are OCI artifacts under `oci://ghcr.io/actions/actions-runner-controller-charts/`:

| Chart | Installs |
| --- | --- |
| `gha-runner-scale-set-controller` | One controller and its CRDs |
| `gha-runner-scale-set` | One scale set: listener and runner Pods |

Both charts always carry the same `version` and `appVersion`; install the scale set with the controller's chart
version. Releases are tagged `gha-runner-scale-set-<version>`; the latest on 2026-11-21 is `0.15.1`.

```bash
gh api repos/actions/actions-runner-controller/releases \
  --jq '[.[] | select(.tag_name | startswith("gha-runner-scale-set")) | .tag_name][0]'
```

## Scale set values

| Key | Value | Effect |
| --- | --- | --- |
| `githubConfigUrl` | `https://github.com/owner/repository` | Repository-scoped scale set |
| `githubConfigSecret` | Secret name | Existing Secret in the scale-set namespace with key `github_token` |
| `runnerScaleSetName` | name | Label that workflows select with `runs-on` |
| `minRunners` / `maxRunners` | `0` / `1` | Idle runners kept and maximum runners created |
| `controllerServiceAccount` | `namespace`, `name` | Required when `helm lookup` cannot find the controller |
| `containerMode` | unset | Plain runner Pod; `dind` and `kubernetes` add Docker or job containers |
| `template` runner `image` | digest-pinned image | Default `ghcr.io/actions/actions-runner` |
| `template` runner `command` | path to `run.sh` | Default `/home/runner/run.sh`; a custom image sets its path |

`template` is the runner PodSpec; the runner container must be named `runner`.

A workflow job selects the scale set by name:

```yaml
jobs:
  probe:
    runs-on: example-scale-set
    timeout-minutes: 2
    steps:
      - run: hostname
```

## Runner image

A custom image must contain the runner binary and its `run.sh`. The official image runs as user `runner` (1001) in
`/home/runner`. A root user needs `RUNNER_ALLOW_RUNASROOT=1`. ARC passes registration to the runner Pod, so the
image does not request a registration token itself.

## Lifecycle

| Step | Command shape |
| --- | --- |
| Controller | `helm install RELEASE --namespace CONTROLLER_NS --version VERSION CONTROLLER_CHART` |
| Scale set | `helm install SCALE_SET --namespace RUNNER_NS --version VERSION -f values.yaml SCALE_SET_CHART` |
| Remove | `helm uninstall SCALE_SET --namespace RUNNER_NS`, then the controller only if this run installed it |

Pod read-back is not proof of job execution; read the workflow job's runner name and result from GitHub.

## Related

- [OpenShift SCC access](openshift-scc.md): the constraint that admits runner Pods.

[values]: https://github.com/actions/actions-runner-controller/blob/master/charts/gha-runner-scale-set/values.yaml
[release]: https://github.com/actions/actions-runner-controller/blob/master/CONTRIBUTING.md
[image]: https://github.com/actions/actions-runner-controller/blob/master/docs/adrs/2022-10-17-runner-image.md
