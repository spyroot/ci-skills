# Finish reference connections and pinned lifecycle beyond the working navigator

This living ExecPlan follows [docs/PLANS.md](../../PLANS.md). Its scope comes from [CI09][phase].
Reconciliation baseline: `ff1fdc8b2fee89486f63f6534be20c784bf119b4`. Phase requirements and status wording remain unchanged.

## Purpose / Big Picture

A caller can move from one reference to its immediate references or tools, read only the needed source section
and refresh pinned upstream knowledge with traceable changes. CI09's R-CI09-001–005 and delivery slices 1a–1d/2
own those outcomes; compact local navigation already works.

## Progress

- [x] CI09-R1: source and existing test locations reconciled at the baseline above.
- [x] Local discovery, section reads and paging exist, with focused knowledge references and proposition tests.
- [ ] Finish 1a immediate reference/tool connections and reachability.
- [ ] Close only the remaining 1b knowledge gaps, then 1c/1d pinned update lifecycle.
- [ ] Deliver slice 2 operation declarations and its original acceptance.

## Surprises & Discoveries

Copied local knowledge and a working navigator do not close the machine-readable graph or pinned upstream
lifecycle. `ocp-node-image.md` is not in the source reference registry even though catalog help names it; a test
over only registered nodes can miss such an orphan.

## Decision Log

DEC-CI09-01: derive remaining work from the unchanged phase and current implementation. Existing code is reused;
open phase decisions remain open. This reconciliation introduces no command, schema, gate or runtime behavior.

## Outcomes & Retrospective

Historical local-reading proof does not establish outgoing connections, pinned updates or operation declarations.
The plan remains active. No remaining feature work or live validation was executed during this documentation change.

## Context and Orientation

`ci-skills/lib/python/core/catalog.py` owns `PROPOSITION_REFERENCES` and `proposition`;
`core/navigate_references.py` owns local document/section reads. Reference documents currently construct nodes
without outgoing reference/tool connections, and sections expose child references only.
`ci-skills/bin/reference.py` reports unavailable `card`/`part` tiers rather than pretending they exist.
The phase's `update_reference.py`, `reference_update.py`, `tool_operations.py` and pinned index/lock lifecycle are
absent. Existing references already cover runner configuration, Harbor and OpenShift topics.

## Plan of Work

### CI09-M1: complete immediate connections and reachability (1a)

Use R-CI09-001–005 and Concrete remaining deliveries as the requirement map. Extend the existing catalog and
`core/navigate_references.py` nodes with the original immediate reference/tool relationships, preserving the
locked CI11 proposition example. Make all intended local references reachable, including reconciling the
`ocp-node-image.md` registration gap. Test the expected source set as well as traversal of already-registered nodes;
reuse `tests/python/test_propositions.py` rather than implementing another navigator.

### CI09-M2: close knowledge gaps and deliver pinned lifecycle (1b–1d)

Compare existing local references with the original knowledge list and add only uncovered material. Preserve
CI09's declared upstream revisions, content identity, bounded reads and update semantics. Its tool-operations,
index and lock objects remain owned by the phase's Schemas and Declarations sections; enumerate their exact fields
and producer paths before implementation. Introduce the originally named update modules only after resolving
any still-open lifecycle decisions, and keep runtime reads separate from maintenance updates.

### CI09-M3: deliver operation declarations (2)

Bind the original named tool operations to installed commands and reference `uses` pointers through the existing
catalog. CI07 owns schema validation and CI08 consumes routing metadata. Verify declared exact argv, method, flags and
`mutates` values against installed-binary path/version/digest identity without invoking mutations, as slice 2
requires. Exercise the original positive/negative
navigator cases and pinned-update read-back, including unavailable tiers and unsupported segments. Do not close
this milestone using the historical own-text discovery receipt alone.

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
| CI09 acceptance | Phase proof | Original scope proven | Original gaps remain | [Phase gates][phase-gates] |

The second row refers to the phase's existing acceptance, not a newly registered automated gate. Its named checks,
including historical or proposed routes, retain the status recorded by their owner.

## Validation and Acceptance

Each intended reference is reachable from a declared root, and each returned connection identifies its immediate
reference or installed tool without dumping the full graph. Section reads stay bounded and unsupported operations
remain explicit. Pinned update proof identifies the source revision and changed content. All original R-CI09
requirements and gate traceability remain in the phase; this plan does not replace them.

## Idempotence and Recovery

The reconciliation is read-only with respect to runtime state. Future implementation uses the existing confirmation,
read-back and recovery behavior of the affected command; a retry first reconciles observed state. Preserve caller-owned
resources and keep evidence tied to the selected candidate. Do not repeat an uncertain mutation solely to obtain proof.

## Artifacts and Notes

The baseline source paths above and the focused test files are the evidence for this reconciliation. Test execution
and new live receipts are not claimed. Link each later delivery and its exact-candidate evidence here when available.
The [plan map](../../plans/index.md) connects this plan to sibling phases and the existing gates.

## Interfaces and Dependencies

Extend the existing catalog, `core/navigate_references.py` and `bin/reference.py`; reuse the existing local
reference texts. Preserve the CI11 proposition interface. Operation/index/lock fields and upstream maintenance
contracts come from CI09, with CI07 validation and CI08 routing as consumers.

Revision DEC-CI09-01: extracted remaining delivery from the original phase after comparing current code;
kept phase requirements and status wording unchanged so reconciliation can be reviewed separately.

[phase]: ../../phases/CI09-REFERENCE.md
[phase-gates]: ../../phases/CI09-REFERENCE.md#gates
[workflow]: ../../../.github/workflows/validate.yml
[smoke]: ../../../inventory/ci/smoke-tests.yaml
[aggregate]: ../../../tools/ci_required_result.py
