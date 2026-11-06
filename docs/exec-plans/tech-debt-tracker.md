# Technical debt tracker

These open items come from the linked documents. Their owners retain the requirements and evidence;
this tracker records gaps without changing a phase's acceptance or certifying current runtime behavior.
Resolved documentation discrepancies remain identified separately from unfinished product behavior.

| ID | Open item | Evidence and next reconciliation |
| --- | --- | --- |
| TD-01 | CI13 structured delivery labels lag source | [Reconciled plan][ci13-plan] vs [phase][ci13-phase] |
| TD-02 | RESOLVED: standalone block 7 delivery is separated from remaining combined proof | [Toolbelt][toolbelt-7] |
| TD-03 | RESOLVED: both legacy plans follow the common ExecPlan format | [Toolbelt][toolbelt] and [hooks][hooks] |
| TD-04 | Historical phase documents are absent | CI03, CI05, CI06 and CI10 in [CI02][ci02] and [CI08][ci08] |
| TD-05 | CI11 inventory link is unresolved | [Source inventory section][ci11-inventory] points to a missing index |
| TD-06 | CI14 mirror status/gate labels lag source | [Reconciled plan][ci14-plan] vs [phase][ci14-phase] |
| TD-07 | CI15 structured phase labels lag source; remaining behavior stays active | [Current progress][ci15-plan] |
| TD-08 | Mirror partial-ID, timeout and URL matching follow-ups (P2) | [#130](https://github.com/spyroot/ci-skills/issues/130) |
| TD-09 | Pre-existing configured-namespace failure overlap | [#131](https://github.com/spyroot/ci-skills/issues/131) |
| TD-10 | Legacy ownership, numeric chain and absent-scope delete repetition (P2) | [#132](https://github.com/spyroot/ci-skills/issues/132) |
| TD-11 | Legacy `[gitlab]`, `[gitlab.NAME]` and `--gitlab-profile` read for one release | [GitLab targets plan][exec-gitlab-targets] DEC-05 |
| TD-12 | `routing` not yet in `gitlab_job` and `gitlab_pipeline` read and watch results | [GitLab targets plan][exec-gitlab-targets] DEC-15 |

TD-01: prose now credits delivered chain code; the historical structured inventory still labels `R-CI13-007`
`NOT_IMPLEMENTED`. Outstanding interruption and current-chain proof remain separate work. TD-02: standalone
runner-manager and
linked consumer proof are recorded as delivered; the broader combined proof remains an unchecked plan item.
TD-03: both legacy delivery plans now have all required living sections and existing-variable/job/gate tables.
This formatting completion does not deliver native hooks. TD-04: the older phase files were published and later
removed, not lost unpublished documents; see [publication reconciliation](../plans/index.md#publication-reconciliation).
Keep historical references separate from current implementation;
[repository gates](../plans/index.md#repository-gates) maps the checks currently declared in the tree.
TD-05: the source inventory section links to the absent
`docs/plans/2026-10-06/ci11-inventories/index.md`; its intended source remains unresolved.
TD-06/TD-07: the new ExecPlans identify delivered source and missing proof. The active plans now credit completed
bounded proof and approved remaining decisions. Historical structured
phase snapshots still need descriptive status reconciliation; their normative requirements remain unchanged.

[ci13-plan]: active/ci13-image-build-readiness.md#progress
[ci13-phase]: ../phases/CI13-IMAGE-BUILD-READINESS.md#implementation-plan
[toolbelt-7]: active/gitlab-toolbelt-plan.md#7-runner-registration-and-test-job-delivered-proof-and-remaining-chain
[toolbelt]: active/gitlab-toolbelt-plan.md
[hooks]: active/skill-session-hook.md
[ci02]: ../phases/CI02-CLI.md#interface-gate
[ci08]: ../phases/CI08-ROUTING.md#gates
[ci11-inventory]: ../phases/CI11-TOOLS.md#source-rows-openshift-and-kubernetes-inventory-of-2026-10-06
[ci14-plan]: active/ci14-gitlab-connections.md#surprises--discoveries
[ci14-phase]: ../phases/CI14-GITLAB-CONNECTIONS.md#gates
[ci15-plan]: active/ci15-runner-workflows.md#surprises--discoveries
[exec-gitlab-targets]: active/gitlab-private-public-targets.md#decision-log
