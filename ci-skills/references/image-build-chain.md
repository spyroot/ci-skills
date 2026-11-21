# Publish a prepared toolbox and configure its runner

`bin/image_build_readiness.py chain` copies the target's prepared CI toolbox
to a consumer repository in Harbor and configures a GitLab runner with the
published image digest. The runner and its Kubernetes namespace remain
available after the command succeeds. Use the [Podman probe](podman-readiness.md)
for a standalone Kubernetes runtime check.

## Prepare the target once

Use `toolbox.py create` with the [toolbox specification](../schemas/toolbox.schema.json)
to build and publish the shared image. Include the CI tools your jobs need,
the runner's `gitlab-runner`, `git`, `podman`, `install` and `cat` commands,
and Bash at `/bin/bash`. Choose an image compatible with the target workers.

The selected Harbor table in the [target file](access.md) sets
`reference_toolbox` to the published artifact within its configured project,
such as `ci-skills-toolbox:stable` or the same repository pinned by digest.
The chain reads this setting at runtime. Its identity needs pull access to
the reference and pull and push access to the consumer repository.
The chain also needs GitLab runner permissions and Kubernetes workload
permissions, including [SCC access](openshift-scc.md) on OpenShift.

## Name and select the consumer

`<runner_name> = <project_name> = <toolbox_name>`. The caller's
`--project-name` takes precedence over the selected repository's leaf name.
For `team/sample_project`, GitLab's default runner description and scheduling
tag and Harbor's consumer repository and default image tag are all
`sample_project`. Kubernetes uses the DNS-safe base `sample-project`, with
existing ownership suffixes where needed. The GitLab project selection and
the configured Harbor host and project retain their original identities.

`--namespace` selects an existing caller-owned runtime namespace. An optional
`--toolbox-spec` retains explicit publication fields and reports the spec's
build namespace and app without editing those resources. If an explicit
runner description, scheduling tags or toolbox repository conflicts with the
consumer name, the live plan shows the explicit and expected values and
blocks apply. For later changes to a runner record, use the
[runner controls](gitlab-runner.md#manage-a-runner-record).

## Plan and apply

`CI_SKILLS_ROOT` is the installed skill directory reported by the installer.
Run from the consumer repository root. Without an explicit `--binding`, the
[target resolver](access.md#where-the-target-file-comes-from) uses
`CI_SKILLS_TARGET`, then `./.ci-skills/target.toml`, then
`~/.ci-skills/target.toml`. The selected GitLab route identifies the
Kubernetes and Harbor tables. Those tables provide the Kubernetes context,
API server, kubeconfig selection, and prepared Harbor toolbox. Relative
credential paths resolve beside the selected target file.

`image_build_readiness.py --describe --json` gives the chain inputs and
result schema. `reference.py image_build_readiness.py --json` lists the
chain's related tools and references.

A project path is enough to select a project runner. A group runner takes
`--group` and `--project-name`.

```bash
chain=(conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/image_build_readiness.py")
"${chain[@]}" chain --project team/sample_project --dry-run --json
"${chain[@]}" chain --project team/sample_project --live-plan --json
"${chain[@]}" chain --project team/sample_project --apply --confirm-plan DIGEST_FROM_PLAN --json
```

The dry run reads no provider API. The live plan resolves the Harbor source,
the current destination tag and the Ready worker. Apply checks the live plan
again at the publication boundary and uses the exact published digest in the
runner deployment. Keep publishers of the same destination tag serialized.
The registry copy API has no atomic compare-and-set for an external writer
that changes the tag during publication. The
[result schema](../schemas/results/image-build-readiness.schema.json) carries
the plan, publication and runner deployment evidence.

The chain preserves existing runner and namespace identities. If the selected
scope already has a runner manager, reuse that runner through
[runner management](gitlab-runner-manager.md). A failed or incomplete chain
reports `BLOCKED` and any cleanup observations. Consumer job execution and
disposal of test resources belong to live acceptance.
