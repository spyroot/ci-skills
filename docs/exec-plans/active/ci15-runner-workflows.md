# Complete runner qualification and the remaining CI15 delivery slices

This living ExecPlan follows [docs/PLANS.md](../../PLANS.md) and derives from [CI15][phase].
Source baseline: `16255b1c51bb867d7cf4b83ef4ec9d3141211c3a`. [Recorded completed subtasks][baseline] retain the merged
persistent-runner and shell/Podman qualification. The remaining matrix and capability slices keep this plan active.

## Purpose / Big Picture

A user can place and manage a runner using the requested repository, name, namespace, tags and protection,
build the specified toolbox, and qualify the separately requested executor/provider variants. CI15 already
contains identity/scope/interface code; the next work separates its live proof from the remaining capability slices.

## Progress

- [x] CI15-R1: PR-001 identity, scope, aliases and protection code exists, with focused tests.
- [x] PR #127 merged as `ff1fdc8`; installed `bcf7d598` ran one GitHub persistent consumer job and one GitLab
  shell/Podman job, with same-ID create NO_OP and exact-owned cleanup. See [completed evidence][baseline].
- [x] Named profiles and required mirror evidence/portability foundation merged in PR #129.
- [ ] Reconcile runner follow-ups [#132](https://github.com/spyroot/ci-skills/issues/132), then finish uncovered
  PR-001 explicit/default selection, borrowed namespace and protection variants; do not repeat completed cases.
- [ ] Deliver the approved optional `--project-name` override and propagate its consumer-specific names.
- [ ] Deliver PR-002 plain Debian/Ubuntu Conda provisioning and a consuming toolbox job.
- [ ] Deliver PR-003 repository-scoped ARC with `--runner-mode persistent|ephemeral` (default `persistent`).
- [ ] Deliver PR-004 `--executor shell|kubernetes` (default `shell`) through manager and build chain; the
  Kubernetes mode must not require Podman prerequisites.
- [ ] Qualify PR-005 GitLab.com behavior against the selected project/cluster under D-003.

## Surprises & Discoveries

R-002, R-004, R-009 and R-010 implementation labels lag current source. Shared identity and scope handling now
exists, including repository-derived names, namespace ownership and selected runner protection. Tests are not live
proof. The completed baseline supplies bounded historical proof for the two consumer paths above, not the
remaining namespace/protection matrix. `CondaInput._render` in `core/toolbox.py` still requires an existing `conda`; it
does not provision Conda on
plain Debian/Ubuntu. The manager bootstrap still configures the shell executor, not the proposed Kubernetes executor.

## Decision Log

DEC-CI15-01: treat PR-001 as source-delivered; credit only the bounded qualification actually completed. Preserve the
original five
PR/wave slices and D-001–D-003 decisions. Do not infer completion of fleet/executor/Conda behavior from shared runner
foundations, and do not replace caller-selected names, namespace or protection with new defaults.

DEC-CI15-02: publish the already approved remaining scope: optional `--project-name`; repository-scoped ARC
selected by `--runner-mode persistent|ephemeral`, default `persistent`; and GitLab
`--executor shell|kubernetes`, default `shell`. Preserve existing ARC controllers and keep Kubernetes execution
independent of Podman prerequisites. These interfaces are planned, not advertised as available.

DEC-CI15-03: Conda is optional consumer toolbox configuration, not the development-machine environment. Existing
`ToolboxSpec.spec.conda` can build an environment when Conda is present. The undelivered PR-002 installs Conda
when absent on the selected plain Debian/Ubuntu base and verifies the environment in a consuming job.

## Outcomes & Retrospective

The bounded PR-001 baseline is complete. Next: reconcile #132 and the pre-existing shared-namespace failure
limitation [#131](https://github.com/spyroot/ci-skills/issues/131), then execute only the unchecked work above.
CI15 stays active because consumer override, remaining qualification variants, Conda bootstrap, ARC, Kubernetes
executor and GitLab.com qualification are unfinished. Historical DCloud proof does not qualify GitLab.com.

## Context and Orientation

`ci-skills/lib/python/core/runner_identity.py` owns `resolve_repository` and `resolve_name`;
`runner_options.py` owns `parse_protected`, `validate_runner_tags` and `SingleValue`;
`runner_scope.py` owns `scope_record`. Kubernetes scope handling and manager/workload callbacks retain UID-based
ownership. `core/gitlab/gitlab_runners.py` prepares `access_level` and reads selected protection back; the chain
forwards that choice. `tests/python/test_ci15_contracts.py` and `test_runner_scope_lifecycle.py` cover these paths.
The remaining toolbox, fleet and executor work must reuse those owners rather than reimplementing selection.

## Plan of Work

### CI15-M1: qualify the delivered PR-001 behavior

Start from [the completed baseline][baseline] and #132; retain its exact historical scope and do not schedule
another identical smoke merely to resume this plan. Run the existing contract and scope tests, then use the
installed changed candidate for the remaining phase's explicit/default
selection, owned/borrowed namespace, retained identity, protection and cleanup cases. Preserve CI15-NS-001,
CI15-IDENTITY-001, CI15-ARGS-001 and CI15-PROTECTION-001. Independently read namespace UIDs, runner properties and
owned-resource absence. Namespace deletion alone does not establish Podman container/volume/storage cleanup.
Fix only a demonstrated failure in its existing shared owner; do not schedule the whole PR-001 implementation again.

### CI15-M1b: propagate the approved consumer project override

Extend the existing shared resolver in `ci-skills/lib/python/core/runner_identity.py` and the catalog/parser
consumers with optional string `--project-name`. The precedence is explicit object name, then project-name
override, then selected consumer repository. Propagate the resolved consumer identity through runner, namespace,
toolbox/build, support-object and temporary image names. Preserve explicit ToolboxSpec fields, existing
verified identities, ownership metadata and schema identifiers. This override is absent today; do not confuse
repository-derived defaults with its delivery. Name any required result/schema delta before implementation.
Use two different synthetic consumer configurations and immutable-baseline, one-invariant rejection cases.

### CI15-M2: deliver and qualify PR-002 toolbox provisioning

Extend the phase-owned toolbox build path in `core/toolbox.py` so its requested plain Debian/Ubuntu image can
provision the required Conda environment, rather than stopping at `command -v conda`. Reuse current image/build
callbacks and the existing toolbox specification. Before changing any specification fields, show the exact original
field/type/invariant delta; this plan chooses no new fields or image defaults. Read back OS, Conda and a real
consuming job as required by G-003. Select the OS through `baseImage`, not another flavor flag. Qualify the
same toolbox contract in both nested Podman and Kubernetes-hosted private-runner paths; existing Conda support
is not evidence that bootstrap on an image without Conda already works.

### CI15-M3: deliver the separately requested fleet and executor slices

Use repository-scoped ARC for PR-003 and preserve installed controllers. Add the approved runner-mode selection
with persistent compatibility as the default. D-001 still owns the exact chart/version/install delta. For PR-004,
propagate the approved executor selection through manager and chain; shell remains the default, while the
Kubernetes executor needs its own credentials/permission details under D-002 and no Podman prerequisite.
Use the original PR order and existing lifecycle owners. The current shell executor is retained for existing callers;
its bootstrap is not proof of a Kubernetes executor. Prove one separately requested job, replenishment/cleanup for
the fleet, and job Pod/image/owned-resource cleanup for the Kubernetes executor while preserving manager and
borrowed namespace resources. For its bounded qualification, use `minRunners=0`, `maxRunners=1`, exactly two sequential
two-minute jobs and
a 20-minute observation bound. Different runner IDs/Pod UIDs after the installation CLI exits must prove
replenishment. Do not introduce a new fleet mechanism or permission policy through reconciliation.

### CI15-M4: qualify PR-005 against the selected GitLab.com scope

Use the approved project/cluster choice in D-003 and the phase's requested variants. Read back each actual runner,
manager and consuming job, including selected tags and protection. Keep GitLab.com evidence separate from historical
DCloud receipts and retain the exact ownership/cleanup boundary. Reuse implemented `--gitlab-profile com` and the
existing target resolver; do not introduce another provider.
Both shell and Kubernetes variants need their own GitLab.com job proof. Consumer jobs remain external acceptance
actions, not extra callbacks in the production combo. Reuse the locked [asset inventory][assets].

## Concrete Steps

Working directory: repository root in the existing GitHub validation environment. The current focused tests should
exit 0 before and after each implementation delta. They establish regression behavior, not live qualification.

    python -W error -m pytest -q tests/python/test_ci15_contracts.py tests/python/test_runner_scope_lifecycle.py
    python tools/render_manifest.py --check

Use the existing pipeline and receipt route; no new CI job or gate is proposed here. The workflow supplies its
candidate revision, and the [smoke inventory][smoke] owns required steps and evidence paths.

| Variable Name | Type | Default / Required | Source / Scope | Description & Invariants |
| --- | --- | --- | --- | --- |
| `CI_SOURCE_SHA` | string | Required | [Workflow][workflow] | Exact candidate under validation |

| Pipeline / Job Name | Type / Trigger | Stage / Order | File Path | Required Inputs & Artifacts |
| --- | --- | --- | --- | --- |
| `validate` | PR/push/manual/scheduled | Parallel checks | [Workflow][workflow] | Candidate; validate evidence |
| `gate-ci-skills-endpoints` | Same workflow | Parallel checks | [Workflow][workflow] | Candidate; endpoint evidence |
| `test-install` | Same workflow | Parallel checks | [Workflow][workflow] | Candidate; package evidence |
| `required-result` | Same workflow | After required jobs | [Workflow][workflow] | Exact-candidate job evidence |

| Gate ID | Gate Type / Class | Pass Condition | Fail-Closed Condition | Producer / Owner |
| --- | --- | --- | --- | --- |
| `ci-skills/required` | Required CI | All required evidence | Missing/failed evidence | [Aggregator][aggregate] |
| CI15 acceptance | Phase proof | Original scope proven | Original gaps remain | [Phase gates][phase-gates] |

The second row refers to the phase's existing acceptance, not a newly registered automated gate. Its named checks,
including historical or proposed routes, retain the status recorded by their owner.

## Validation and Acceptance

CI15's G-001 covers contract/parser/schema compatibility; G-002 covers placement, protection and cleanup;
G-003 covers toolbox/Conda consuming-job proof; G-004 the fleet; G-005 the Kubernetes executor; G-006 GitLab.com;
G-007 the exact-head required result. These remain the phase's existing declarations and enforcement states.
The completed baseline satisfies its narrow recorded proof, not all seven phase claims. Extend the existing
acceptance inventory/checker as each new capability ships; access-only or mirror receipts must not satisfy
Agent, ARC or executor behavior, and merely enabling aggregate mode is not a coverage fix. Each live result must retain
selected identities and independently establish the phase's before/after and cleanup behavior.

## Idempotence and Recovery

Use the shared scope resolver and UID-based ownership; retain the same selected identity across plan, apply and
cleanup. Preserve borrowed namespaces and successful persistent runners until explicit confirmed deletion.
After uncertainty, read existing state before creating or deleting again. Do not infer ownership from a name.
Existing toolbox/probe resource lifetimes keep their own contracts.

## Artifacts and Notes

[PR #127](https://github.com/spyroot/ci-skills/pull/127) merged as `ff1fdc8` from head `370e23f` after
[workflow 41290533853](https://github.com/spyroot/ci-skills/actions/runs/41290533853), required status and
live-acceptance deployment `7470932025` passed. Independently installed source
`bcf7d598647d1f96f481f6e65265b039d09f1c21` had functional checksum
`17aa9701e833ed382f756d5267c26c76ee8fd2ffe5b17ce0b589accb9e33e2a9`; the evidence-only final head preserved it.

[GitHub job 124801993929](https://github.com/spyroot/test-self-runner/actions/runs/41290977805/job/124801993929),
run `41290977805`, passed on persistent runner `2`. GitLab pipeline `5879`, job `73681`, passed on shell runner
`117` with actual Podman execution. Both used the selected consumer identity; setup repeats kept the same
native runner and workload IDs. Podman read-back showed no containers or volumes, and disposable records,
workloads, Secrets and namespaces were removed with independent absence checks. Reusable fixtures and the
corrected image remain in the [asset inventory][assets]; the GitLab fixture returned to CI-disabled state.
Exactly one job per provider ran; this record is not a request to run another smoke.

The whole GitLab manager delete repeat returned `BLOCKED` after its scope disappeared, tracked in #132. A
separate ownership-primitive repeat proved absence with zero further DELETE; the provider runner delete itself
repeated as NO_OP. Preserve that distinction. The failed, unqualified image remains retained because exclusive
reference proof was incomplete; reuse only the qualified image in the inventory.

These are historical results for the stated source and scope. No Conda, ARC, Kubernetes-executor, GitLab.com or
remaining explicit namespace/protection qualification is recorded as complete. The [plan map](../../plans/index.md)
retains the original phase/gate relationship.

## Interfaces and Dependencies

Reuse `runner_identity.py`, `runner_options.py`, shared `runner_scope.py`, Kubernetes `RunScope`/`ResourceSet`,
manager/workload callbacks and existing toolbox interfaces. Keep selection separate from ownership. D-001–D-003
and the original phase are the authority for the remaining mechanisms and selected execution scopes.

Revision DEC-CI15-01: separated delivered PR-001 code from missing proof and later capability slices while
preserving the unchanged phase and its caller-choice invariants.

[phase]: ../../phases/CI15-RUNNER-WORKFLOWS.md
[phase-gates]: ../../phases/CI15-RUNNER-WORKFLOWS.md#gates
[workflow]: ../../../.github/workflows/validate.yml
[smoke]: ../../../inventory/ci/smoke-tests.yaml
[aggregate]: ../../../tools/ci_required_result.py

Revision DEC-CI15-02/03: credited bounded completed smoke, published the approved remaining interfaces and
clarified existing Conda support versus missing bootstrap; no executable or gate was changed.

[baseline]: #artifacts-and-notes
[assets]: ../../2026-10-31/live-smoke-inventory.md
