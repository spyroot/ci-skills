# Complete skill discovery and generalize the existing installer

This living ExecPlan follows [docs/PLANS.md](../../PLANS.md). Its scope comes from [CI01][phase].
Reconciliation baseline: `ff1fdc8b2fee89486f63f6534be20c784bf119b4`. Phase requirements and status wording remain unchanged.

## Purpose / Big Picture

A user can list local and vendored skills, read a named skill file, and install a selected skill through one
repository maintenance interface. CI01 owns that generic catalog; the working ci-skills copy installer already exists.

## Progress

- [x] CI01-R1: source and existing test locations reconciled at the baseline above.
- [x] Transactional copy, source identity, fingerprint confirmation, recovery and installed-digest read-back exist.
- [ ] Add the generic discovery map and bounded list/get interface from CI01 Discovery and Interface.
- [ ] Generalize the existing installer and deliver the originally specified metadata and scope work.
- [ ] Resolve the original vendor, default-scope and confirmation dependencies before claiming full CI01 acceptance.

## Surprises & Discoveries

The phase describes a stale `skills/ci-skills` source and an installer migration that has partly happened.
Preserve the existing root layout and transaction rather than repeating the old layout repair.

## Decision Log

DEC-CI01-01: derive remaining work from the unchanged phase and current implementation. Existing code is reused;
open phase decisions remain open. This reconciliation introduces no command, schema, gate or runtime behavior.

## Outcomes & Retrospective

Existing installer tests are source evidence; generic discovery and metadata acceptance remain absent.
The plan remains active. No remaining feature work or live validation was executed during this documentation change.

## Context and Orientation

`tools/install_ci_skills.py` already resolves `SOURCE` to the root `ci-skills/` package. Its `install`,
`_install_unlocked` and `recover_install` functions implement the transaction. `InstallScope` and
`scope_skills_directory` support `codex-home`, `claude-user` and `claude-project`; these are not evidence for the
phase's separate `repo`, `user` and `admin` proposal. `tools/render_manifest.py` currently renders `tools.json`.
The proposed `tools/skillkit/discover.py`, generic `tools/skillkit/install.py`, `bin/ci-skills`, vendor tree,
`ci-skills/agents/openai.yaml` and icon assets are absent.

## Plan of Work

### CI01-M1: validated discovery and bounded reads

Derive `tools/skillkit/discover.py` from the phase's Discovery rules: one map keyed by validated skill name, local
`ci-skills/` plus direct vendored or explicitly selected children, no network or writes. Reuse
`schemas/skill-index.schema.json`. Its current object contract, and the phase's Index record and Errors, own the
fields and failure names. Connect `list` and `get` through the maintenance interface only after its missing
CI05 dependency is resolved. A successful read returns the requested file inside the selected skill; invalid
names, escaped paths and unverified records produce the already-specified errors.

### CI01-M2: reusable installation and metadata

REFACTOR the existing transaction from `tools/install_ci_skills.py` into the phase-owned `tools/skillkit/install.py`
with skill name as input; preserve the wrapper's `SKILL_NAME`, `install`, `package_files`, `skills_directory` and
`tree_digest` callers. Keep locking, confirmation, journal recovery, previous-copy retention and digest checks.
Before and after extraction, run the existing installer/package tests below. Extend metadata generation in
`tools/render_manifest.py` and the phase's `ci-skills/agents/openai.yaml` and assets only to the fields and paths
in Codex metadata and install scopes; do not invent additional metadata or defaults.

### CI01-M3: resolve retained dependencies and prove the selected scope

D-SCOPE and CI02's D-CONFIRM remain original decisions. Preserve the current default while they are unresolved.
The vendor verification and dependency-install steps require the missing CI05 authority; record that gap instead
of creating a vendor policy here. Once resolved, exercise the phase's list/get/install acceptance with selected
fixtures and independently compare the installed digest. Repeat installation and recovery through the existing
transaction semantics.

## Concrete Steps

Work from the repository root in the existing GitHub validation environment. The following focused commands inspect
current coverage; run them again after each corresponding implementation delta and expect exit 0 with no failures.
They do not themselves establish that unimplemented phase behavior works.

    python -W error -m pytest -q tests/python/test_installer.py tests/python/test_installed_package.py

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
| CI01 acceptance | Phase proof | Original scope proven | Original gaps remain | [Phase gates][phase-gates] |

The second row refers to the phase's existing acceptance, not a newly registered automated gate. Its named checks,
including historical or proposed routes, retain the status recorded by their owner.

## Validation and Acceptance

Discovery returns one record per valid direct skill and reports invalid records with the file, field and rule.
`get` stays within the selected skill. Install requires the phase-approved confirmation, verifies source and
destination identity, and preserves recovery/no-op behavior. Generic CLI examples in the phase are future commands;
they are not runnable at this snapshot. Existing package tests prove only the current installer surface.

## Idempotence and Recovery

The reconciliation is read-only with respect to runtime state. Future implementation uses the existing confirmation,
read-back and recovery behavior of the affected command; a retry first reconciles observed state. Preserve caller-owned
resources and keep evidence tied to the selected candidate. Do not repeat an uncertain mutation solely to obtain proof.

## Artifacts and Notes

The baseline source paths above and the focused test files are the evidence for this reconciliation. Test execution
and new live receipts are not claimed. Link each later delivery and its exact-candidate evidence here when available.
The [plan map](../../plans/index.md) connects this plan to sibling phases and the existing gates.

## Interfaces and Dependencies

Reuse `tools/install_ci_skills.py`, `core/provenance.py` under `ci-skills/lib/python/`, and the existing
`schemas/skill-index.schema.json`. CI07 owns missing schema families; CI08 owns routing/dependency declarations;
CI02 owns unresolved CLI behavior. The absent CI05 document is a dependency to reconcile, not permission to invent it.

Revision DEC-CI01-01: extracted remaining delivery from the original phase after comparing current code;
kept phase requirements and status wording unchanged so reconciliation can be reviewed separately.

[phase]: ../../phases/CI01-CATALOG.md
[phase-gates]: ../../phases/CI01-CATALOG.md#gates
[workflow]: ../../../.github/workflows/validate.yml
[smoke]: ../../../inventory/ci/smoke-tests.yaml
[aggregate]: ../../../tools/ci_required_result.py
