# Complete reference routing on top of the installed navigator

This living ExecPlan follows [docs/PLANS.md](../../PLANS.md). Its scope comes from [CI08][phase].
Reconciliation baseline: `ff1fdc8b2fee89486f63f6534be20c784bf119b4`. Phase requirements and status wording remain unchanged.

## Purpose / Big Picture

A caller reaches the relevant tool or bounded reference without loading the whole skill. The installed navigator
already supports compact discovery; CI08's remaining work is its original routing metadata, conditional references
and dependency handoff.

## Progress

- [x] CI08-R1: source and existing test locations reconciled at the baseline above.
- [x] Compact catalog propositions, bounded local reference reading and generated manifest routing exist.
- [ ] Add the original routing metadata through the CI07/CI09 owners.
- [ ] Extract the specified report guidance and add the conditional GitLab reference.
- [ ] Prove routing completeness and dependency handoff without repeating navigator implementation.

## Surprises & Discoveries

The phase's broad navigation goal is partly delivered by newer proposition and local-reading code. Historical
progressive-discovery receipts concern those paths, not the missing routing declarations or vendor dependencies.

## Decision Log

DEC-CI08-01: derive remaining work from the unchanged phase and current implementation. Existing code is reused;
open phase decisions remain open. This reconciliation introduces no command, schema, gate or runtime behavior.

## Outcomes & Retrospective

Existing source/tests establish local discovery; routing metadata, extraction and dependency acceptance are still
missing.
The plan remains active. No remaining feature work or live validation was executed during this documentation change.

## Context and Orientation

`ci-skills/lib/python/core/catalog.py` provides `proposition` and `PROPOSITION_REFERENCES`;
`ci-skills/bin/reference.py` is the installed navigator. `core/navigate_references.py` implements
`reference_document` and `section_document`. `tools/render_manifest.py` generates the installed manifest.
The planned `tags`, `load_when`, `points_to` and `uses` declarations are absent. So are
`ci-skills/references/reading-reports.md` and `references/conditional/gitlab-writes.md`.
Sections 4–6 still reside in `ci-skills/SKILL.md`.

## Plan of Work

### CI08-M1: extend the existing routing declarations

Follow CI08 Changes and Steps in `ci-skills/lib/python/core/catalog.py`: add the phase-defined derived tags,
`load_when`, reference connections and `uses` through the CI07 schema and CI09 operation/reference owners.
Preserve the existing `proposition` interface. The original phase defines the routing fields and dependency
meaning; record their exact schema changes before implementation rather than introducing new routing policy.
Regenerate `ci-skills/tools.json` using the existing renderer and extend its catalog equality/routing coverage.

### CI08-M2: move the specified reference text and add the handoff

Move SKILL sections 4–6 unchanged to `ci-skills/references/reading-reports.md`, then retain the phase's router
sentence, safety list and access pointer. Add `references/conditional/gitlab-writes.md` with the original handoff
for GitLab work no installed command declares. The `points_to: glab` dependency belongs to the original phase;
its generic install/discovery path depends on CI01 and the missing vendor authority. Do not claim that path works
until its provider is resolved.

### CI08-M3: prove the installed route graph

Use the existing proposition tests and manifest renderer to show every registered command remains reachable,
references stay bounded and declared connections resolve. Exercise the installed navigator after installation.
Capture affected capability evidence only where the change affects a live operation; the phase explicitly does
not require full-publication access for a routing change.

## Concrete Steps

Work from the repository root in the existing GitHub validation environment. The following focused commands inspect
current coverage; run them again after each corresponding implementation delta and expect exit 0 with no failures.
They do not themselves establish that unimplemented phase behavior works.

    python tools/render_manifest.py --check
    python -W error -m pytest -q tests/python/test_propositions.py tests/python/test_catalog.py

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
| CI08 acceptance | Phase proof | Original scope proven | Original gaps remain | [Phase gates][phase-gates] |

The second row refers to the phase's existing acceptance, not a newly registered automated gate. Its named checks,
including historical or proposed routes, retain the status recorded by their owner.

## Validation and Acceptance

Manifest output equals its catalog source, every declared route resolves, and no undeclared command becomes
callable. The moved guidance is byte-identical apart from required links. Conditional handoff and dependency
installation satisfy the original pointer rules; existing local discovery alone does not prove those additions.

## Idempotence and Recovery

The reconciliation is read-only with respect to runtime state. Future implementation uses the existing confirmation,
read-back and recovery behavior of the affected command; a retry first reconciles observed state. Preserve caller-owned
resources and keep evidence tied to the selected candidate. Do not repeat an uncertain mutation solely to obtain proof.

## Artifacts and Notes

The baseline source paths above and the focused test files are the evidence for this reconciliation. Test execution
and new live receipts are not claimed. Link each later delivery and its exact-candidate evidence here when available.
The [plan map](../../plans/index.md) connects this plan to sibling phases and the existing gates.

## Interfaces and Dependencies

Preserve `proposition`, `reference_document`, `section_document` and the current manifest renderer. CI09 owns
reference/operation declarations, CI07 their shapes and CI01 dependency installation. No new provider target or
parallel reference registry is introduced.

Revision DEC-CI08-01: extracted remaining delivery from the original phase after comparing current code;
kept phase requirements and status wording unchanged so reconciliation can be reviewed separately.

[phase]: ../../phases/CI08-ROUTING.md
[phase-gates]: ../../phases/CI08-ROUTING.md#gates
[workflow]: ../../../.github/workflows/validate.yml
[smoke]: ../../../inventory/ci/smoke-tests.yaml
[aggregate]: ../../../tools/ci_required_result.py
