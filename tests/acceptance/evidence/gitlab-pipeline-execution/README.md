# Installed pipeline execution proof

Captured on 2026-10-18 from a fresh installation of source
`6ca1d7778d07c417815618452b3dbee96170e8d5`.

- Package checksum: `86eeff94d94240f04cad123064ac6cb233cc47182f66a2f685c4f010cd9cfda1`.
- Functional checksum: `6e940688a3c787ca63728040f5445ec95376b6b6b240a456d420ecad406da080`.
- Caller-selected GitLab test project: `hott/test` (ID 123).
- Existing test ref: `neroshige/ci-skills-job-list-smoke-20261007` at
  `a9cd22f5a66395f9514102356ba8dd3e3573af5e`.

| Invocation | Observed result | Captured report |
| --- | --- | --- |
| Confirmed start | APPLIED; pipeline 3916 independently read back | [Start](start-applied.json) |
| Same confirmed plan | NO_OP; same pipeline 3916, no mutation | [Repeat](start-no_op.json) |
| Watch pipeline 3916 | PASS; five successful jobs | [Watch](watch.json) |
| Independent job list | Jobs 60250–60254 all successful | [Read-back](job-readback.json) |
| Different caller run ID | APPLIED; distinct pipeline 3917 | [New start](second-applied.json) |
| Watch pipeline 3917 | PASS; five successful jobs | [New watch](second-watch.json) |

The first invocation used `--run-id pipeline-6ca1d77-20261018`; the distinct build used
`--run-id pipeline-6ca1d77-20261018-second`. Each apply confirmed its own live-plan digest.
Both ran the same existing test configuration on existing runners. This proves pipeline
start/retry/watch; it does not claim creation or configuration of a new runner.

The 24 ordinary acceptance receipts were refreshed for these functional bytes. Their disposable
milestone, issue, wiki and runner were deleted and independently verified absent; see
[cleanup](acceptance-cleanup.json). The two successful pipeline records remain as test history.
No temporary cluster permission or storage change was needed for pipeline execution.

The five start/watch reports are unchanged CLI-written portable receipts. The job-list capture
uses the existing portable-receipt transform to tokenize local credential paths.
[Raw hashes](raw-sha256.txt) identify the original captures. Raw stderr and local paths remain private.

An initial planning invocation used `--receipt-out`, which the command correctly rejected with
`receipt_out_requires_live_run`; the corrected live-plan used stdout. No pipeline was created by
that rejected invocation. GitHub executes the mocked and regression tests separately.
