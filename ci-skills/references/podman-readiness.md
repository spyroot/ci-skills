# Nested Podman readiness

`bin/image_build_readiness.py probe` composes worker selection, a temporary privileged Job, runtime checks, and cleanup.
It uses only the Kubernetes configuration in your [target file](access.md), `kubectl`, and OpenShift `oc`
authorization reads when API discovery exposes the SCC API. Podman runs in the Job.

## Plan and apply

No JSON specification is required. The default mode is a live plan; apply requires the returned `plan_digest`.
`CI_SKILLS_ROOT` is the installed skill directory reported by the installer. `DIGEST_FROM_PLAN` below is that
live plan's `plan_digest` value.

```bash
# Read target identity and select a Ready worker without creating resources.
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/image_build_readiness.py" --live-plan --json
# Apply the same inputs using the returned live-plan digest.
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/image_build_readiness.py" \
  --apply --confirm-plan DIGEST_FROM_PLAN --json
```

Repeat the same input options on apply; the plan also binds the installed probe scripts.
`--dry-run` makes no API calls and its plan cannot authorize apply. The optional positional action is `probe`.

## Choose inputs

- `--node NAME` selects a Ready worker; otherwise the command discovers one.
- `--namespace NAME` uses and preserves an existing namespace; otherwise the run creates and cleans its namespace.
- `--probe-image` and `--inner-image` accept digest-pinned images. Defaults target Linux amd64; select a compatible
  worker on a mixed cluster or supply compatible digests.
- `--receipt-out PATH` saves a sanitized apply receipt. `--output-dir PATH` saves shared reports, the probe log and
  successfully parsed marker rows. Without an output path, results go to stdout.

Use `--help` for the full option list and defaults; `--describe` provides the command contract.

## Access

The selected identity must read Nodes, Pods, Jobs, Events and Pod logs, and create/delete ConfigMaps and Jobs.
A generated namespace also needs namespace create/delete permission. On OpenShift, the probe selects `privileged`
and the namespace's `default` service account; [SCC access](openshift-scc.md) describes the required permission,
its preparation and cleanup, and the reusable callback's inputs.

## Runtime evidence

The result reports the exact Job-owned Pod exit, marker rows, and cleanup read-back. It proves nested container
execution, bind-mount read-back, exit propagation, no remaining run-labeled containers, and owned
Job/ConfigMap/Namespace absence. A caller-supplied namespace remains. This probe does not prove a GitLab runner,
an image build, or a Harbor push; the separate [chain operation](image-build-chain.md) links those observations.
The [result schema](../schemas/results/image-build-readiness.schema.json),
also linked by `--describe`, defines the report and its `$defs/markers` shape.

The probe selects native `overlay` storage and `cgroupfs`; see the current [Podman global options][podman].
Its inner containers use bind mounts and `--security-opt label=disable`; see [Podman run options][podman-run].
The result records the actual Podman version.

[podman]: https://docs.podman.io/en/latest/markdown/podman.1.html
[podman-run]: https://docs.podman.io/en/latest/markdown/podman-run.1.html
