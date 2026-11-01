# Finish the remaining command-line contract without repeating delivered ports

This living ExecPlan follows [docs/PLANS.md](../../PLANS.md). Its scope comes from [CI02][phase].
Reconciliation baseline: `ff1fdc8b2fee89486f63f6534be20c784bf119b4`. Phase requirements and status wording remain
unchanged.

## Purpose / Big Picture

A caller can discover every installed command's options, run its advertised operations and consume its result
without guessing which CLI style it uses. This plan retains CI02's contract while separating delivered Python ports
from unresolved interface decisions.

## Progress

- [x] CI02-R1: source and existing test locations reconciled at the baseline above.
- [x] Python ci-api/ci-binary-build ports, shared parser/help and catalog descriptions are present.
- [ ] Complete only uncovered command descriptions, shared options and result contracts.
- [ ] Resolve D-EXIT and D-CONFIRM before changing exit codes or confirmation spelling.
- [ ] Finish the phase interface acceptance against the catalog and installed package.

## Surprises & Discoveries

The phase still schedules Python ports that already exist. Existing tests also cover shared logging and
contract help. Source presence does not prove every output kind has the complete CI07 envelope or every command
has CI09 operation metadata.

## Decision Log

DEC-CI02-01: derive remaining work from the unchanged phase and current implementation. Existing code is reused;
open phase decisions remain open. This reconciliation introduces no command, schema, gate or runtime behavior.

## Outcomes & Retrospective

Existing interface tests cover delivered behavior; full phase closure still depends on open decisions and output
coverage.
The plan remains active. No remaining feature work or live validation was executed during this documentation change.

## Context and Orientation

`ci-skills/lib/python/core/cli.py` builds the common parser; `core/help.py` owns `CommandHelp`;
`core/catalog.py` owns `options_for` and `describe`; `core/argument_parser.py` tracks parser declarations.
`core/read_cli.py` provides `api_main` and `binary_main`; installed `ci-api` and `ci-binary-build` are Python
entrypoints. Root wrappers forward to them. `ci-skills/lib/python/sys/status.py` and general catalog `EXIT_CODES`
use 0 and 2. The read-tool ports already use `core/read_tools.py`'s `ReadExit` and catalog `READ_EXIT_CODES`
with 0/64/65/66/69. The uniform exit policy, `PARTIAL` mapping and `ci-k8s` consolidation remain unresolved.

## Plan of Work

### CI02-M1: close only demonstrated interface gaps

Use the declared command catalog as the inventory, then compare `describe` output to each parser through the
existing `tests/python/test_catalog.py` and `test_cli_contract.py` patterns. Reuse `CommandHelp` and
`TrackedArgumentParser` for a demonstrated mismatch. The shared parser and the two read-only Python ports remain
the owners; no parallel CLI framework or repeat port is needed. The CI01 generic maintenance CLI stays with CI01.

### CI02-M2: resolve the original behavioral decisions

Record the operator's resolution of D-EXIT, the `PARTIAL` exit and D-CONFIRM before changing the current
per-command exit mappings
or installer confirmation options. The proposed `ci-k8s` names and Cilium namespace distinction remain phase
requirements to reconcile, not approved changes in this document. Any selected transition must list exact affected
catalog constants, parser options and caller compatibility before implementation.

### CI02-M3: bind output and operation contracts through their owners

CI07 supplies the shared result envelope and missing per-kind schemas; CI09 supplies operation declarations.
Bind those approved contracts to existing command emitters and descriptions instead of copying schema or operation
policy here. Extend the existing interface tests to cover the actual selected deltas, then exercise help/describe
and representative output from the installed candidate without credentials for offline commands.

## Concrete Steps

Work from the repository root in the existing GitHub validation environment. The following focused commands inspect
current coverage; run them again after each corresponding implementation delta and expect exit 0 with no failures.
They do not themselves establish that unimplemented phase behavior works.

    python -W error -m pytest -q tests/python/test_catalog.py tests/python/test_cli_contract.py tests/python/test_node_local_cli.py

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
| CI02 acceptance | Phase proof | Original scope proven | Original gaps remain | [Phase gates][phase-gates] |

The second row refers to the phase's existing acceptance, not a newly registered automated gate. Its named checks,
including historical or proposed routes, retain the status recorded by their owner.

## Validation and Acceptance

Every declared command has help and a valid description; parser and catalog options agree. Offline help and
describe do not need credentials or network access. Existing modes and exit behavior remain compatible until the
original decisions are approved. Validate output against its owned schema and distinguish schema completeness from
passing parser tests. The historical `scripts/check.sh cli` route is not present and is not recreated by this plan.

## Idempotence and Recovery

The reconciliation is read-only with respect to runtime state. Future implementation uses the existing confirmation,
read-back and recovery behavior of the affected command; a retry first reconciles observed state. Preserve caller-owned
resources and keep evidence tied to the selected candidate. Do not repeat an uncertain mutation solely to obtain proof.

## Artifacts and Notes

The baseline source paths above and the focused test files are the evidence for this reconciliation. Test execution
and new live receipts are not claimed. Link each later delivery and its exact-candidate evidence here when available.
The [plan map](../../plans/index.md) connects this plan to sibling phases and the existing gates.

## Interfaces and Dependencies

Preserve `core/cli.py`, `core/help.py`, `core/argument_parser.py`, `core/read_cli.py` and catalog declarations.
Use CI07 for record shapes and CI09 for operation connections. Exact new fields or exit enums are not decided by
this reconciliation; the corresponding phase decision remains a prerequisite.

Revision DEC-CI02-01: extracted remaining delivery from the original phase after comparing current code;
kept phase requirements and status wording unchanged so reconciliation can be reviewed separately.

[phase]: ../../phases/CI02-CLI.md
[phase-gates]: ../../phases/CI02-CLI.md#interface-gate
[workflow]: ../../../.github/workflows/validate.yml
[smoke]: ../../../inventory/ci/smoke-tests.yaml
[aggregate]: ../../../tools/ci_required_result.py
