# Toolbox-backed runner consumer chain

`bin/image_build_readiness.py chain` runs the Podman probe, builds and publishes a toolbox, creates a temporary
GitLab runner and manager, starts the caller's pipeline, and verifies that the selected consumer job succeeds on
that exact runner. Use [the probe](podman-readiness.md) for Kubernetes-only runtime proof.

## Inputs

Configure Kubernetes, GitLab and Harbor in your [target file](access.md). The chain uses `kubectl`, `oc`, `glab`
and `skopeo` on the execution host; its toolbox build uses OpenShift. The standalone probe does not require
GitLab or Harbor. Missing chain provider configuration produces a blocked result before mutation.
The selected identities need OpenShift Secret/BuildConfig/Build create/read/delete and namespace permissions,
GitLab runner creation/deletion and pipeline start/read access, and Harbor pull/push access. SCC permissions are
described below; a live plan does not prove every subsequent write is authorized.

Supply these inputs:

- `--toolbox-spec PATH`: the existing [toolbox specification](../schemas/toolbox.schema.json), with
  `gitlab-runner`, `git`, `podman`, `bash`, `install` and `cat` in `requiredCommands`, with Bash at
  `/bin/bash`. Select an image and dependencies compatible with the worker's architecture; the chain builds and
  publishes this toolbox itself.
- `--project GROUP/PROJECT`: the project that owns both the runner and pipeline. Alternatively, select
  `--group GROUP --pipeline-project GROUP/PROJECT` for a group runner and its consumer project.
- `--ref REF`, optionally `--expected-sha SHA`: an existing branch or tag containing the native GitLab job.
- `--job NAME`, `--runner-label LABEL`, `--runner-description TEXT`: the job name, its matching runner tag,
  and a unique description for the temporary runner record. Repeat `--runner-label` for additional tags.

The chain does not add credentials or generated pipeline YAML to the caller's project. See [runner
management](gitlab-runner-manager.md) for the shell runner's workload and [SCC access](openshift-scc.md) for its
OpenShift authorization.

## Native consumer job example

Put one representative consumer job in the selected project's `.gitlab-ci.yml`. This example consumes
`RUNNER_LABEL` and `CONSUMER_IMAGE`, both supplied as caller-selected pipeline variables. Pin the consumer image
by digest so the job result identifies the container it ran.

```yaml
podman-consumer:
  tags: ["$RUNNER_LABEL"]
  script:
    - podman run --rm "$CONSUMER_IMAGE" sh -ec 'printf "PODMAN_CONSUMER_OK\n"'
```

ci-skills reads the exact selected job from the pipeline it started. It requires that job to report `success`
and name the temporary runner record created by this chain. It does not search another pipeline or infer runner
identity from pipeline status alone.

## Plan and apply

`CI_SKILLS_ROOT` is the installed skill directory returned by the installer. The example uses a project runner;
replace `team/project`, `main`, the toolbox file, label and digest-pinned consumer image with your selections.

```bash
chain_args=(
  chain --project team/project --ref main --job podman-consumer
  --toolbox-spec ./runner-toolbox.json
  --runner-description smoke-001 --runner-label ci-image-smoke
  --pipeline-variable RUNNER_LABEL=ci-image-smoke
  --pipeline-variable "CONSUMER_IMAGE=docker.io/library/busybox@sha256:<digest>"
)
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/image_build_readiness.py" \
  "${chain_args[@]}" --live-plan --json
# Use that plan's plan_digest and repeat the same inputs.
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/image_build_readiness.py" \
  "${chain_args[@]}" --apply --confirm-plan DIGEST_FROM_PLAN --json
```

Use `--node NAME` to select a worker and `--namespace NAME` to preserve an existing runtime namespace.
`--watch-interval` and `--watch-timeout` bound pipeline observation; `--help` lists their defaults and the shared
probe image/resource options. `--dry-run` reads no provider API and cannot authorize apply.

## Results and cleanup

The [result schema](../schemas/results/image-build-readiness.schema.json) distinguishes `operation: chain`
from `probe`. Callback records retain the probe's selected worker, toolbox publication result, manager runtime,
and exact pipeline/job/runner IDs. The existing `HarborPush` result is the sole image-publication evidence and
contains the immutable toolbox image and digest. The consumer-job result contains job, pipeline, runner and status
identity only; it does not relabel the toolbox digest as a job-produced image.

`PASS` requires the watched pipeline graph to finish successfully, the selected consumer job to succeed on the
newly created runner, and temporary-resource cleanup to succeed. A failed other job or downstream pipeline can
therefore block the chain even when the selected consumer job passes.

Plan results contain no completed-cleanup claim. Apply records cleanup of its manager, runner record, private
registration token, temporary SCC binding and owned runtime resources. Existing namespaces, grants and shared
storage remain. The published toolbox image, reusable toolbox BuildConfig and build namespace remain. To remove
that toolbox BuildConfig and its image tags, plan and confirm the existing delete command:

```bash
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/toolbox.py" delete --spec ./runner-toolbox.json --json
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/toolbox.py" delete --spec ./runner-toolbox.json \
  --apply --confirm-plan DELETE_PLAN_DIGEST --json
```

A disposable live test removes only its exact remaining outputs and its own empty namespace after saving proof.
A failed or incomplete chain reports `BLOCKED` with its observed records and cleanup failures, rather than
claiming the missing job succeeded.
