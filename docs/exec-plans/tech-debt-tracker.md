# Technical debt tracker

These open items come from the linked documents. Their owners retain the requirements and evidence;
this tracker records gaps without changing a plan's status or certifying current runtime behavior.

| ID | Open item | Evidence and next reconciliation |
| --- | --- | --- |
| TD-01 | CI13 phase still calls the chain unmerged | [Reconciled plan][ci13-plan] vs [phase][ci13-phase] |
| TD-02 | Toolbelt block 7 status conflicts | [Delivered summary][toolbelt-summary] vs [planned block][toolbelt-7] |
| TD-03 | Legacy plans lack ExecPlan living sections | [Toolbelt][toolbelt] and [hooks][hooks] vs [standard][standard] |
| TD-04 | Historical phase documents are absent | CI03, CI05, CI06 and CI10 in [CI02][ci02] and [CI08][ci08] |
| TD-05 | CI11 inventory link is unresolved | [Source inventory section][ci11-inventory] points to a missing index |
| TD-06 | CI14 mirror status/gate labels lag source | [Reconciled plan][ci14-plan] vs [phase][ci14-phase] |
| TD-07 | CI15 identity/scope/protection labels lag source | [Reconciled plan][ci15-plan] vs [phase][ci15-phase] |

TD-01: the ExecPlan now records delivered chain code; the phase still carries old unmerged labels. Outstanding
interruption and current-chain proof remain separate work. TD-02: reconcile the delivered runner-manager evidence
with the still-planned combined proof.
TD-03: the legacy delivery plans lack the required Progress, Surprises & Discoveries, Decision Log, and Outcomes &
Retrospective sections. TD-04: resolve the historical references against their intended authority before linking them;
[repository gates](../plans/index.md#repository-gates) maps the checks currently declared in the tree.
TD-05: the source inventory section links to the absent
`docs/plans/2026-10-06/ci11-inventories/index.md`; its intended source remains unresolved.
TD-06/TD-07: the new ExecPlans identify delivered source and missing proof. Phase status corrections remain a
separate reviewable change; no phase requirement or status wording is rewritten here.

[ci13-plan]: active/ci13-image-build-readiness.md#progress
[ci13-phase]: ../phases/CI13-IMAGE-BUILD-READINESS.md#implementation-plan
[toolbelt-summary]: active/gitlab-toolbelt-plan.md#outcome-and-boundary
[toolbelt-7]: active/gitlab-toolbelt-plan.md#7-runner-registration-and-test-job-planned
[toolbelt]: active/gitlab-toolbelt-plan.md
[hooks]: active/skill-session-hook.md
[standard]: ../PLANS.md#living-plans-and-design-decisions
[ci02]: ../phases/CI02-CLI.md#interface-gate
[ci08]: ../phases/CI08-ROUTING.md#gates
[ci11-inventory]: ../phases/CI11-TOOLS.md#source-rows-openshift-and-kubernetes-inventory-of-2026-10-06
[ci14-plan]: active/ci14-gitlab-connections.md#surprises--discoveries
[ci14-phase]: ../phases/CI14-GITLAB-CONNECTIONS.md#gates
[ci15-plan]: active/ci15-runner-workflows.md#surprises--discoveries
[ci15-phase]: ../phases/CI15-RUNNER-WORKFLOWS.md#implementation-plan
