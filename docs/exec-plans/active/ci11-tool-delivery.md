# Deliver the remaining CI11 tool groups using the existing shared callbacks

This living ExecPlan follows [docs/PLANS.md](../../PLANS.md). Its scope comes from [CI11][phase].
Reconciliation baseline: `ff1fdc8b2fee89486f63f6534be20c784bf119b4`. Phase requirements and status wording remain unchanged.

## Purpose / Big Picture

A caller gets the remaining named GitLab, Kubernetes, OpenShift and Harbor operations as discoverable installed
commands. CI11's H1/H2 and C1–C9 map the scope; its source-script inventory remains the authority for each port.

## Progress

- [x] CI11-R1: source and existing test locations reconciled at the baseline above.
- [x] H1/H2 help and persistence guidance, job/pipeline reads and watch, selected mutations, ISO/join and toolbox
  paths exist.
- [ ] Reuse delivered H1/H2 and complete CI09 immediate connections plus C1 descriptive routing.
- [ ] Deliver only missing operations in C2–C9, preserving the original dependency order.
- [ ] Capture affected installed-capability proof for each delivery; retain full-phase proof as a separate closure
  claim.

## Surprises & Discoveries

Several original port rows have since been delivered, while similarly named broader workflows remain absent.
MR create/update does not implement MR check or failure correlation, and pipeline watch does not provide job
watch/logs or a schedule command.

## Decision Log

DEC-CI11-01: derive remaining work from the unchanged phase and current implementation. Existing code is reused;
open phase decisions remain open. This reconciliation introduces no command, schema, gate or runtime behavior.

## Outcomes & Retrospective

Existing tests and historical receipts cover subsets. C1–C9 residual operations and full-phase proof remain open.
The plan remains active. No remaining feature work or live validation was executed during this documentation change.

## Context and Orientation

`core/help.py` and catalog `persistence_options` implement H1/H2. Installed `gitlab_job.py` and
`gitlab_pipeline.py` implement job get/list and pipeline get/start/watch, including bounded child traversal.
`core/gitlab/gitlab_merge_requests.py` supports MR create/update. Runner, milestone and issue commands,
`image_build_readiness.py probe|chain`, native ISO/join, and Harbor/toolbox push/pull already exist.
These shared owners are reused; source presence is not a new live acceptance result.

## Plan of Work

### CI11-M1: finish discovery and descriptive entrypoints

Treat H1/H2 as source-delivered and retain their existing help tests. Follow the phase's Canonical remaining
delivery traceability: CI09 immediate connections first, then C1 descriptive entrypoints over the existing
`image_build_readiness.py` implementation. The phase owns the proposed names; do not introduce new flags or
reimplement the probe/chain callbacks. Keep the original proposition example unchanged.

### CI11-M2: complete the remaining GitLab groups (C2–C6)

C2 still needs job watch/logs/numeric selectors and the remaining pipeline verbs; C3 needs the schedule main.
C4's MR check/failure correlation depends on C2, while MR create/update already exist. C5 retains reset-token,
description-based deletion and manager/executor reads; current delete uses numeric runner ID. C6 retains variables,
milestone list and issue milestone-title resolution, while milestone create/update/adjust-time and issue
create-bug/open-bug exist. Use each original source row and
port recipe to name exact flags, schemas and callback changes before implementing that bounded delivery.

### CI11-M3: complete the remaining platform groups (C7–C9)

C7's route/HA/machine/state/health mains remain absent; reuse installed Kubernetes collectors. C8 already has
native ISO/join, but its standalone RHCOS/CSR/MTU-extension/benchmark/NFS deltas remain. C9 has toolbox and Harbor
push/pull; robot, sanity, charts and pull-secret synchronization remain absent. Deliver independent groups in the
original order/dependencies and retain their source inventory boundaries. Do not treat a similarly named shared
helper as a complete installed command.

### CI11-M4: close proof at the proper scope

For each delivery, show installed discovery, help/describe, the requested observable effect, independent read-back
and owned-resource cleanup through existing receipt owners. Preserve CI11's distinction between affected-capability
merge evidence and an aggregate full-phase session. An unestablished aggregate session is not a new prerequisite
for every ordinary command change.

## Concrete Steps

Work from the repository root in the existing GitHub validation environment. The following focused commands inspect
current coverage; run them again after each corresponding implementation delta and expect exit 0 with no failures.
They do not themselves establish that unimplemented phase behavior works.

    python -W error -m pytest -q tests/python/test_cli_contract.py tests/python/test_gitlab_job_cli.py tests/python/test_gitlab_pipeline_watch.py tests/python/test_gitlab_consumer_actions.py

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
| CI11 acceptance | Phase proof | Original scope proven | Original gaps remain | [Phase gates][phase-gates] |

The second row refers to the phase's existing acceptance, not a newly registered automated gate. Its named checks,
including historical or proposed routes, retain the status recorded by their owner.

## Validation and Acceptance

Each remaining original C1–C9 row maps to one callable entrypoint, reused shared implementation, result contract
and its stated read-back. New tests cover only the delivered delta. Existing H1/H2 and installed operations retain
behavior. Source inspection and mocked tests are separate from live mutation/read-back evidence; keep untested
ports and full-phase closure open.

## Idempotence and Recovery

The reconciliation is read-only with respect to runtime state. Future implementation uses the existing confirmation,
read-back and recovery behavior of the affected command; a retry first reconciles observed state. Preserve caller-owned
resources and keep evidence tied to the selected candidate. Do not repeat an uncertain mutation solely to obtain proof.

## Artifacts and Notes

The baseline source paths above and the focused test files are the evidence for this reconciliation. Test execution
and new live receipts are not claimed. Link each later delivery and its exact-candidate evidence here when available.
The [plan map](../../plans/index.md) connects this plan to sibling phases and the existing gates.

## Interfaces and Dependencies

Use the existing provider callbacks, `WorkflowExecutor`, catalog, help and report layers under
`ci-skills/lib/python/core/`. CI11's port recipe and source rows own each operation; CI09 owns navigation and
CI07 schemas. Preserve C4's dependency on C2 and the existing action-mode/confirmation behavior.

Revision DEC-CI11-01: extracted remaining delivery from the original phase after comparing current code;
kept phase requirements and status wording unchanged so reconciliation can be reviewed separately.

[phase]: ../../phases/CI11-TOOLS.md
[phase-gates]: ../../phases/CI11-TOOLS.md#gates
[workflow]: ../../../.github/workflows/validate.yml
[smoke]: ../../../inventory/ci/smoke-tests.yaml
[aggregate]: ../../../tools/ci_required_result.py
