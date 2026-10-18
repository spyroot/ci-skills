# Runner image-build chain

`bin/image_build_readiness.py chain` runs the Podman probe, builds and publishes a toolbox, creates a temporary
GitLab runner and manager, starts the caller's pipeline, and compares the selected job's digest artifact with Harbor.
Use [the probe](podman-readiness.md) for Kubernetes-only runtime proof.

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
  `/bin/bash`. Select an image and dependencies compatible
  with the worker's architecture; the chain builds this toolbox itself.
- `--project GROUP/PROJECT`: the project that owns both the runner and pipeline. Alternatively, select
  `--group GROUP --pipeline-project GROUP/PROJECT` for a group runner and its build project.
- `--ref REF`, optionally `--expected-sha SHA`: an existing branch or tag containing the native GitLab job.
- `--job NAME`, `--runner-label LABEL`, `--runner-description TEXT`: the job name, its matching runner tag,
  and a unique description for the temporary runner record. Repeat `--runner-label` for additional tags.
- `--build-repository REPOSITORY --build-tag TAG`: the job's destination within the configured Harbor project.
- `--job-digest-artifact PATH`: the uploaded job artifact containing the digest written by `podman push`.

The job's registry credentials come from its project's CI variables or another caller-configured job credential
source. The chain does not copy local target credentials into pipeline variables. See [runner
management](gitlab-runner-manager.md)
for the shell runner's workload and [SCC access](openshift-scc.md) for its OpenShift authorization.

## Native job example

Put a build job in the selected project's `.gitlab-ci.yml`. This example consumes `RUNNER_LABEL`, supplied as
`--pipeline-variable RUNNER_LABEL=ci-image-smoke`, and `BUILD_IMAGE`, supplied as the complete destination tag.
`HARBOR_HOST`, `HARBOR_USERNAME` and `HARBOR_PASSWORD` are caller-configured CI variables; keep credentials masked
and out of job output. A protected credential is available only on an eligible protected ref.

```yaml
build-image:
  tags: ["$RUNNER_LABEL"]
  script:
    - printf '%s' "$HARBOR_PASSWORD" | podman login --username "$HARBOR_USERNAME" --password-stdin "$HARBOR_HOST"
    - podman build --tag "$BUILD_IMAGE" .
    - mkdir -p reports
    - podman push --digestfile reports/image-digest.txt "$BUILD_IMAGE"
  artifacts:
    paths: [reports/image-digest.txt]
```

The repository supplies its Dockerfile and build context. The digest file contains `sha256:` and 64 lowercase
hexadecimal digits, optionally followed by one newline. ci-skills reads it from the exact successful job ID;
it does not search other jobs or infer the result from a pipeline status alone.

## Plan and apply

`CI_SKILLS_ROOT` is the installed skill directory returned by the installer. The example uses a project runner;
replace `team/project`, `main`, the toolbox file and registry image with your selections. `BUILD_IMAGE` must
match the configured Harbor host/project plus `--build-repository` and `--build-tag`.

```bash
BUILD_IMAGE=registry.example.com/team/smoke-image:smoke-001
chain_args=(
  chain --project team/project --ref main --job build-image
  --toolbox-spec ./runner-toolbox.json
  --runner-description smoke-001 --runner-label ci-image-smoke
  --build-repository smoke-image --build-tag smoke-001
  --job-digest-artifact reports/image-digest.txt
  --pipeline-variable RUNNER_LABEL=ci-image-smoke
  --pipeline-variable "BUILD_IMAGE=$BUILD_IMAGE"
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
from `probe`. Callback records retain the probe's selected worker, manager runtime, exact pipeline/job/runner IDs,
job artifact path and digest, and Harbor image/digest. `PASS` requires the watched pipeline graph to finish
successfully, that job to succeed on the newly created
runner, both digest observations to match, and temporary-resource cleanup to succeed. A failed other job or
downstream pipeline can therefore block the chain even when the selected build job passes.

Plan results contain no completed-cleanup claim. Apply records cleanup of its manager, runner record, private
registration token, temporary SCC binding and owned runtime resources. Existing namespaces, grants and shared
storage remain. Published toolbox and job images, the reusable toolbox BuildConfig, and its build namespace
remain. To remove that toolbox BuildConfig and its two image tags, plan and confirm the existing delete command:

```bash
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/toolbox.py" delete --spec ./runner-toolbox.json --json
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/toolbox.py" delete --spec ./runner-toolbox.json \
  --apply --confirm-plan DELETE_PLAN_DIGEST --json
```

That command preserves the build namespace and the job-produced image. A disposable live test removes its exact
remaining outputs and its own empty namespace after saving proof. A failed or incomplete chain reports
`BLOCKED` with its observed records and cleanup
failures, rather than claiming the missing job or publication succeeded.
