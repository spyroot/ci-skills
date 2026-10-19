# Installed runner-manager proof

Captured on 2026-10-19 from installed source
`d2a16beab957f5b5ef34c2ae0e22b36f638e53e3`.

- Package checksum: `ac13183ffd71563eabf7676f6b2da8cb17042667ea951fa9adbab20b513b7e60`.
- Functional checksum (`bin/`, `lib/`, `schemas/`):
  `0336a2806847e9e9cb75e962c72ea568864e1df50e0039efaad4f84c33d4cced`.
- Execution model: GitLab **shell executor hosted in Kubernetes**, using the selected toolbox image.

## Observed execution

The installed commands [built the toolbox](toolbox-create.json),
[created runner 99](runner-create.json), and deployed its manager.

| Operation | Observed result | Report |
| --- | --- | --- |
| Confirmed manager deploy | APPLIED; runner 99 became online | [Deploy](manager-deploy-applied.json) |
| Identical confirmed deploy | NO_OP; same namespace, workload and runner | [Retry](manager-deploy-noop.json) |
| Start and watch consumer pipeline | Pipeline 3956 succeeded | [Start][start], [watch][watch] |
| Read consumer job | Job 60694 succeeded on runner 99 | [Job](job-get.json), [list](job-list.json) |
| Delete manager | Owned workload, binding and namespace absent; runner preserved | [Delete](manager-delete.json) |

The disposable [consumer fixture](consumer-fixture-create.json) used commit
`e34a95dccadd57288bd7c75db029855ae27b7cc0` in `hott/test`.
[Job 60694](https://gitlab.dcloud.run/hott/test/-/jobs/60694) executed direct Podman commands;
its [timestamped output](consumer-trace-proof.json) confirms native overlay, a successful inner
container, bind-mount read-back, propagated exit code 7, and removal of the owned inner containers.
No warning or command-not-found line was observed. This proves the runner executed that Podman
consumer. Docker API/Compose compatibility and the combined CI13 command are not established here.

## Cleanup and retained artifact

Manager deletion retained the GitLab runner record by design. The separate
[runner deletion](runner-delete.json) and [fixture deletion](consumer-fixture-delete.json)
removed that record and the temporary branch.
[Final read-back](final-cleanup.json) confirms their absence, the manager namespace's absence,
and removal of the superseded toolbox namespace. The
[old toolbox deletion](old-toolbox-delete.json) records its resource cleanup.

The corrected toolbox namespace, BuildConfig and image were deliberately retained for reuse;
its temporary Build was removed. The retained image digest is
`sha256:beeaf60ce4194020a00960a0af97fce7e88b433eeab7a388f9d8796797e6493b`.
This run used disposable container storage, not the shared ISO/model PVC.

## Capture boundaries

The reports are copied unchanged from the publication bundle prepared with the existing redaction
and portable-path helpers. [Checksums](SHA256SUMS.json) identify those published bytes.
The shared redactor masks token-named fields, including token-sink cleanup booleans;
those private observations are not exposed by the published report.

Installed v3 discovery captures are preserved for the [root](v3-discovery/root.json),
[GitLab cluster](v3-discovery/glab.json), and
[manager command](v3-discovery/gitlab-runner-manager.json).
GitHub validation is recorded separately; the complete CI13 workflow still requires its own proof.

[start]: pipeline-start.json
[watch]: pipeline-watch.json
