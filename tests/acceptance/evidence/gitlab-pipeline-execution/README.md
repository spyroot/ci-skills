# Installed pipeline execution proof

## Latest exact candidate

The `final-30ccd8e` directory records the installed candidate at source
`30ccd8e19f8674c0e8cab2d6bcd79f0ef59b1d58`, package checksum
`b19198a7e05e618d470c090d31baac0511e4fc867e562be0fbbfd14991af03a8`, and
functional checksum
`33dd8b7910f6936f2b513408e92b6748781ea908179f35b33ea8c1014eb53bc4`.

Pipeline 3922 ran the existing protected test ref at exact commit
`a9cd22f5a66395f9514102356ba8dd3e3573af5e`. The confirmed start was APPLIED,
the watch and final get observed success, all five jobs succeeded on existing
runners 74 and 75, and the same confirmed plan returned NO_OP without creating
a second pipeline. [Checksums](final-30ccd8e/sha256.txt) cover the six current
evidence documents. The refreshed 24 ordinary receipts carry the same package
and functional identities; their cleanup report records successful deletion
and absence checks for disposable resources.

This publication does not claim a current-candidate Podman or live-acceptance
gate PASS. The standalone Podman run that would exercise the temporary SCC
RoleBinding remains pending separately.

## Historical pipeline captures

Captured on 2026-10-18 after merging current main into the candidate, from a fresh installation of
source `d8ab14b2d4d408975bc378ce871737bd014bbe44`.

- Package checksum: `0d343fe1d4c8dc6bda5172f572306f6e1f2b50dfdf0d6b0a0d5938a624fab0a8`.
- Functional checksum: `5c8a13b2c648a054c1988092cb72af4dff6b6c6c1394d3de4692ee51709e6b9a`.
- Caller-selected GitLab test project: `hott/test` (ID 123).
- Existing test ref: `neroshige/ci-skills-job-list-smoke-20261007` at
  `a9cd22f5a66395f9514102356ba8dd3e3573af5e`.

| Invocation | Observed result | Captured report |
| --- | --- | --- |
| Confirmed start | APPLIED; pipeline 3918 independently read back | [Start](start-applied.json) |
| Same confirmed plan | NO_OP; same pipeline 3918, no mutation | [Repeat](start-no_op.json) |
| Watch pipeline 3918 | PASS; five successful jobs | [Watch](watch.json) |
| Independent job list | Jobs 60260–60264 all successful | [Read-back](job-readback.json) |
| Different caller run ID | APPLIED; distinct pipeline 3919 | [New start](second-applied.json) |
| Watch pipeline 3919 | PASS; five successful jobs | [New watch](second-watch.json) |
| Independent second job list | Jobs 60265–60269 all successful | [New read-back](second-job-readback.json) |

Both starts supplied the same project, ref, expected SHA and timeout of 180 seconds. The first used
`--run-id pipeline-d8ab14b-20261018`; the distinct build used
`--run-id pipeline-d8ab14b-20261018-second`. Each apply confirmed its own live-plan digest.
Both watches used an interval of 3 seconds and timeout of 180 seconds.

The existing plan contract binds the target-file path and source revision as well as the inputs.
The retry proof uses the same installation and exact confirmed plan. It does not claim retry identity
across installations. Both pipelines used existing runners; this proves pipeline start/retry/watch,
not creation or configuration of a new runner.

The 24 ordinary acceptance receipts were refreshed for these functional bytes. Their disposable
milestone, issue, wiki and runner were deleted and independently verified absent; see
[cleanup](acceptance-cleanup.json). The two successful pipeline records remain as test history.
No temporary cluster permission or storage change was needed for pipeline execution.

The five start/watch reports are unchanged CLI-written portable receipts. The two job-list captures
use the existing portable-receipt transform to tokenize local credential paths.
[Raw hashes](raw-sha256.txt) identify the original captures. Raw stderr and local paths remain private.
GitHub executes the mocked and regression tests separately.
