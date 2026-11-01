# Complete the original record-schema coverage using the current validation owners

This living ExecPlan follows [docs/PLANS.md](../../PLANS.md). Its scope comes from [CI07][phase].
Reconciliation baseline: `ff1fdc8b2fee89486f63f6534be20c784bf119b4`. Phase requirements and status wording remain unchanged.

## Purpose / Big Picture

A reader or command can validate each declared record against its owned schema and receive a useful failure
location. CI07 also specifies schema identity and version rules; the remaining work is coverage and the missing
generic validation capability, not creating a second schema policy.

## Progress

- [x] CI07-R1: source and existing test locations reconciled at the baseline above.
- [x] Root and installed schemas, offline installed-schema validation, pairing and metaschema checks exist.
- [ ] Map actual producers to existing schemas and identify the uncovered original record families.
- [ ] Deliver the remaining generic record inventory and validation behavior through existing shared owners.
- [ ] Prove the phase version/identity rules and full selected-record read-back.

## Surprises & Discoveries

CI07 names a historical `scripts/check.sh schemas` route, while current checks live in the shared static
library and GitHub workflow. The absence of that old script is not evidence that schema checking is absent.
Conversely, the presence of many schemas does not prove all record producers and version transitions are covered.

## Decision Log

DEC-CI07-01: derive remaining work from the unchanged phase and current implementation. Existing code is reused;
open phase decisions remain open. This reconciliation introduces no command, schema, gate or runtime behavior.

## Outcomes & Retrospective

Current tests cover selected schemas; generic inventory, missing families and version-diff enforcement remain open.
The plan remains active. No remaining feature work or live validation was executed during this documentation change.

## Context and Orientation

Schemas already exist under `schemas/` and `ci-skills/schemas/results/`.
`ci-skills/lib/python/core/schema.py` exposes `load_schema` and `validate_specification` for offline validation.
`lib/bash/automation/bless.bash` owns JSON/schema pairing and metaschema checks; the validation workflow also checks
selected result contracts. The proposed `tools/skillkit/schema.py` and `tools/check_schemas.py` are absent.
Missing original families include frontmatter, vendor records, tool operations, reference indexes, OpenAI metadata
and the common command-result envelope. An existing skill-index schema has no generic CI01 producer yet.

## Plan of Work

### CI07-M1: map producer coverage before adding contracts

Start with CI07 Schemas, Identity, Rules every schema follows, Pointers and Versions. Compare each original record
kind with its actual producer and schema, including `tools/render_manifest.py` and installed command results.
Reuse existing schemas for covered kinds. For each uncovered kind, record its exact producer, repository-relative
schema path, fields/types/enums and original invariants before implementing the contract. Preserve CI07 ownership;
this reconciliation does not approve a replacement record shape or a new schema constraint.

### CI07-M2: deliver the missing generic validator

The phase assigns the reusable record walk/validation to `tools/skillkit/schema.py` and a thin
`tools/check_schemas.py --root . --json` entrypoint. Reuse current offline schema loading and registered pairing
facilities rather than copying validation. Resolve the historical CI03 gate-route reference against the existing
workflow before wiring the capability; do not restore an obsolete gate framework. The observable result is the
phase's `schema_check` record naming each validated file/schema/version or each failing file, JSON path and rule.

### CI07-M3: finish versioned coverage with the producing phases

CI01 owns metadata/index producers; CI02 binds result contracts; CI08/CI09 introduce routing and reference records.
Deliver each missing schema with its actual producer. Exercise valid and invalid records, offline references and
version transitions through existing schema tests and checks. Full coverage needs an explicit producer inventory;
passing the currently selected metaschema files alone is insufficient.

## Concrete Steps

Work from the repository root in the existing GitHub validation environment. The following focused commands inspect
current coverage; run them again after each corresponding implementation delta and expect exit 0 with no failures.
They do not themselves establish that unimplemented phase behavior works.

    python -W error -m pytest -q tests/python/test_propositions.py tests/python/test_installed_package.py tests/python/test_ci15_contracts.py

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
| CI07 acceptance | Phase proof | Original scope proven | Original gaps remain | [Phase gates][phase-gates] |

The second row refers to the phase's existing acceptance, not a newly registered automated gate. Its named checks,
including historical or proposed routes, retain the status recorded by their owner.

## Validation and Acceptance

The eventual `schema_check` lists every committed record within CI07's declared scope and its schema/version;
invalid records identify the exact file, JSON path and rule. References resolve offline. Existing contracts stay
compatible, and version changes follow the original Versions section. Run the current schema and pairing checks
through the existing workflow; the proposed generic command is not executable at this snapshot.

## Idempotence and Recovery

The reconciliation is read-only with respect to runtime state. Future implementation uses the existing confirmation,
read-back and recovery behavior of the affected command; a retry first reconciles observed state. Preserve caller-owned
resources and keep evidence tied to the selected candidate. Do not repeat an uncertain mutation solely to obtain proof.

## Artifacts and Notes

The baseline source paths above and the focused test files are the evidence for this reconciliation. Test execution
and new live receipts are not claimed. Link each later delivery and its exact-candidate evidence here when available.
The [plan map](../../plans/index.md) connects this plan to sibling phases and the existing gates.

## Interfaces and Dependencies

Reuse `core/schema.py`, the existing schema directories and shared JSON/schema pairing checks. Exact object
contracts remain the original phase plus their current schema owners. Missing vendor/reference families depend
on their producing phases; do not add schema-only deliverables without the associated capability.

Revision DEC-CI07-01: extracted remaining delivery from the original phase after comparing current code;
kept phase requirements and status wording unchanged so reconciliation can be reviewed separately.

[phase]: ../../phases/CI07-SCHEMA.md
[phase-gates]: ../../phases/CI07-SCHEMA.md#validation
[workflow]: ../../../.github/workflows/validate.yml
[smoke]: ../../../inventory/ci/smoke-tests.yaml
[aggregate]: ../../../tools/ci_required_result.py
