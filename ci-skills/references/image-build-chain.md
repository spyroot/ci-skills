# Build, publish and configure a toolbox-backed runner

`bin/image_build_readiness.py chain` builds a toolbox, publishes it to Harbor, and creates/configures a GitLab
runner and manager using that image. The runner remains available to the caller after the command succeeds.
Use [the separate probe](podman-readiness.md) for Kubernetes-only runtime proof.

## Inputs

Configure Kubernetes, GitLab and Harbor in your [target file](access.md). The chain uses `kubectl`, `oc`, `glab`
and `skopeo` on the execution host; its toolbox build uses OpenShift. The standalone probe does not require
GitLab or Harbor. Missing chain provider configuration produces a blocked result before mutation.
The selected identities need OpenShift Secret/BuildConfig/Build create/read/delete and namespace permissions,
GitLab runner creation/read access, and Harbor pull/push access. SCC permissions are
described below; a live plan does not prove every subsequent write is authorized.

Supply these inputs:

- `--toolbox-spec PATH`: the existing [toolbox specification](../schemas/toolbox.schema.json), with
  `gitlab-runner`, `git`, `podman`, `bash`, `install` and `cat` in `requiredCommands`, with Bash at
  `/bin/bash`. Select an image and dependencies compatible with the worker's architecture; the chain builds and
  publishes this toolbox itself.
- `--project GROUP/PROJECT` or `--group GROUP`: the runner's owning project or group.
- `--runner-label LABEL`, `--runner-description TEXT`: the runner tag and description.
  Repeat `--runner-label` for additional tags.

See [runner management](gitlab-runner-manager.md) for the shell runner's workload and explicit deletion, and
[SCC access](openshift-scc.md) for its OpenShift authorization. The chain does not run a probe or consumer CI job.

## Plan and apply

`CI_SKILLS_ROOT` is the installed skill directory returned by the installer. The example uses a project runner;
replace `team/project`, the toolbox file, description and label with your selections.

```bash
chain_args=(
  chain --project team/project
  --toolbox-spec ./runner-toolbox.json
  --runner-description toolbox-runner --runner-label container-builds
)
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/image_build_readiness.py" \
  "${chain_args[@]}" --live-plan --json
# Use that plan's plan_digest and repeat the same inputs.
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/image_build_readiness.py" \
  "${chain_args[@]}" --apply --confirm-plan DIGEST_FROM_PLAN --json
```

Use `--node NAME` to select a worker and `--namespace NAME` to use an existing runtime namespace.
Without `--namespace`, the command creates the runner's namespace and returns its name. `--help` lists the
runtime resource options. `--dry-run` reads no provider API and cannot authorize apply.

## Results and lifetime

The [result schema](../schemas/results/image-build-readiness.schema.json) distinguishes `operation: chain`
from `probe`. The chain returns the existing build, `HarborPush` publication and runner deployment records.
It ends at `DeployManagerRunner`, using the published toolbox image; there is no separate verification stage.

The successful chain retains the runner, manager, required SCC binding and runtime namespace. The published
image, reusable toolbox BuildConfig and build namespace also remain. Removal uses the existing explicit runner
and toolbox lifecycle commands; success does not trigger teardown. A failed or incomplete chain reports
`BLOCKED` with the observed records.

Online/runtime proof, consumer-job execution and disposal of live-test resources belong to external acceptance
through installed-skill invocations by the consumer agent, not this combo.
