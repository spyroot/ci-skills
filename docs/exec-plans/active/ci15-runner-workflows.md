# Complete runner qualification and the remaining CI15 delivery slices

This living ExecPlan follows [docs/PLANS.md](../../PLANS.md) and derives from [CI15][phase].
Reconciliation baseline: `ff1fdc8b2fee89486f63f6534be20c784bf119b4`; phase requirements and status wording remain unchanged.

## Purpose / Big Picture

A user can place and manage a runner using the requested repository, name, namespace, tags and protection,
build the specified toolbox, and qualify the separately requested executor/provider variants. CI15 already
contains identity/scope/interface code; the next work separates its live proof from the remaining capability slices.

## Progress

- [x] CI15-R1: PR-001 identity, scope, aliases and protection code exists, with focused tests.
- [ ] Prove the original PR-001 live selection, ownership, protection and cleanup cases.
- [ ] Deliver PR-002 plain Debian/Ubuntu Conda provisioning and a consuming toolbox job.
- [ ] Resolve D-001/D-002 and deliver the requested PR-003 fleet and PR-004 Kubernetes-executor slices.
- [ ] Qualify PR-005 GitLab.com behavior against the selected project/cluster under D-003.

## Surprises & Discoveries

R-002, R-004, R-009 and R-010 implementation labels lag current source. Shared identity and scope handling now
exists, including repository-derived names, namespace ownership and selected runner protection. Tests are not live
proof. `CondaInput._render` in `core/toolbox.py` still requires an existing `conda`; it does not provision Conda on
plain Debian/Ubuntu. The manager bootstrap still configures the shell executor, not the proposed Kubernetes executor.

## Decision Log

DEC-CI15-01: treat PR-001 as source-delivered while keeping its live acceptance open. Preserve the original five
PR/wave slices and D-001–D-003 decisions. Do not infer completion of fleet/executor/Conda behavior from shared runner
foundations, and do not replace caller-selected names, namespace or protection with new defaults.

## Outcomes & Retrospective

The next action is PR-001 qualification plus the independently scoped remaining capability work. CI15 stays active;
source/test inspection is not a new live result, and older DCloud evidence does not qualify GitLab.com behavior.

## Context and Orientation

`ci-skills/lib/python/core/runner_identity.py` owns `resolve_repository` and `resolve_name`;
`runner_options.py` owns `parse_protected`, `validate_runner_tags` and `SingleValue`;
`runner_scope.py` owns `scope_record`. Kubernetes scope handling and manager/workload callbacks retain UID-based
ownership. `core/gitlab/gitlab_runners.py` prepares `access_level` and reads selected protection back; the chain
forwards that choice. `tests/python/test_ci15_contracts.py` and `test_runner_scope_lifecycle.py` cover these paths.
The remaining toolbox, fleet and executor work must reuse those owners rather than reimplementing selection.

## Plan of Work

### CI15-M1: qualify the delivered PR-001 behavior

Run the existing contract and scope tests, then use the installed candidate for the phase's explicit/default
selection, owned/borrowed namespace, retained identity, protection and cleanup cases. Preserve CI15-NS-001,
CI15-IDENTITY-001, CI15-ARGS-001 and CI15-PROTECTION-001. Independently read namespace UIDs, runner properties and
owned-resource absence. Namespace deletion alone does not establish Podman container/volume/storage cleanup.
Fix only a demonstrated failure in its existing shared owner; do not schedule the whole PR-001 implementation again.

### CI15-M2: deliver and qualify PR-002 toolbox provisioning

Extend the phase-owned toolbox build path in `core/toolbox.py` so its requested plain Debian/Ubuntu image can
provision the required Conda environment, rather than stopping at `command -v conda`. Reuse current image/build
callbacks and the existing toolbox specification. Before changing any specification fields, show the exact original
field/type/invariant delta; this plan chooses no new fields or image defaults. Read back OS, Conda and a real
consuming job as required by G-003.

### CI15-M3: deliver the separately requested fleet and executor slices

Resolve D-001 for PR-003's one-job GitHub fleet and D-002 for PR-004's Kubernetes-executor credentials/permissions.
Use the original PR order and approved interfaces. The current shell executor is retained for existing callers;
its bootstrap is not proof of a Kubernetes executor. Prove one separately requested job, replenishment/cleanup for
the fleet, and job Pod/image/owned-resource cleanup for the Kubernetes executor while preserving manager and
borrowed namespace resources. Do not introduce a new fleet mechanism or permission policy through reconciliation.

### CI15-M4: qualify PR-005 against the selected GitLab.com scope

Use the approved project/cluster choice in D-003 and the phase's requested variants. Read back each actual runner,
manager and consuming job, including selected tags and protection. Keep GitLab.com evidence separate from historical
DCloud receipts and retain the exact ownership/cleanup boundary. Consumer jobs remain external acceptance actions,
not extra callbacks in the production combo.

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
The current source satisfies parts of the implementation, not all seven proof claims. Each live result must retain
selected identities and independently establish the phase's before/after and cleanup behavior.

## Idempotence and Recovery

Use the shared scope resolver and UID-based ownership; retain the same selected identity across plan, apply and
cleanup. Preserve borrowed namespaces and successful persistent runners until explicit confirmed deletion.
After uncertainty, read existing state before creating or deleting again. Do not infer ownership from a name.
Existing toolbox/probe resource lifetimes keep their own contracts.

## Artifacts and Notes

Current tests are the evidence for source implementation. No new PR-001 live receipt or fleet/executor/GitLab.com
qualification was produced in this reconciliation. Link each slice's installed-candidate receipt and independent
read-back here when available. The [plan map](../../plans/index.md) retains the original phase/gate relationship.

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
