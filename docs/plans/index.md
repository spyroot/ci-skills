# Plans and delivery map

Follow the requested outcome to its execution plan, phase or design, then the gates and evidence owned there.
Statuses below describe the recorded delivery; they do not certify a new candidate or live environment.
[PLANS.md](../PLANS.md) defines the ExecPlan standard and skeleton.

## Phase map

Each row maps a phase to its ExecPlan, source reconciliation and original gate authority.
Code was inspected at `ff1fdc8b2fee89486f63f6534be20c784bf119b4`; source presence is distinct from execution proof.
These links organize interface topics; their list order is not an implementation dependency. The
[capability model](../capability-model.md#architecture-and-dependency-direction) traces the actual command path.
The [CI11-TOOLS plan](../phases/CI11-TOOLS.md) distinguishes installed commands from remaining delivery; the generated
[`tools.json`](../../ci-skills/tools.json) is the installed command contract.

| Source phase | ExecPlan | Code present → remaining work | Gates |
| --- | --- | --- | --- |
| [CI01][ci01] | [Active][exec-ci01] | Installer → generic catalog/metadata | [Catalog][ci01-gates] |
| [CI02][ci02] | [Active][exec-ci02] | Python ports/help → contract gaps/decisions | [Interface][ci02-gates] |
| [CI07][ci07] | [Active][exec-ci07] | Selected schemas → full record coverage | [Validation][ci07-gates] |
| [CI08][ci08] | [Active][exec-ci08] | Local navigation → routing metadata | [Routing][ci08-gates] |
| [CI09][ci09] | [Active][exec-ci09] | Local reading → connections/pinned lifecycle | [References][ci09-gates] |
| [CI11][ci11] | [Active][exec-ci11] | H1/H2 and ports → remaining C1–C9 | [Tools][ci11-gates] |
| [CI12][ci12] | [Completed][exec-ci12] | Three deliveries; CSR/join proof mocked | [Onboarding][ci12-gates] |
| [CI13][ci13] | [Active][exec-ci13] | Probe/chain code → interruption/chain proof | [Readiness][ci13-gates] |
| [CI14][ci14] | [Active][exec-ci14] | Mirror → Agent decisions/delivery | [Connections][ci14-gates] |
| [CI15][ci15] | [Active][exec-ci15] | PR-001 code → proof and PR-002–005 | [Runners][ci15-gates] |

The ExecPlans retain each phase's existing decisions and requirement boundaries; phase edits only repair moved paths.
Historical phase labels can lag
code; the [debt tracker](../exec-plans/tech-debt-tracker.md) points to those discrepancies without changing the phases.
[CI12 live evidence][ci12-evidence] retains its accepted mocked-join boundary;
[CI13 evidence][ci13-evidence] retains outstanding interruption and chain proof.

Completed records live in [completed/](../exec-plans/completed/); unfinished plans live in
[active/](../exec-plans/active/).

## Other delivery plans

| Outcome | Active plan | Gate authority |
| --- | --- | --- |
| GitLab operations | [Toolbelt][exec-toolbelt]; block 7 still marked planned | [Proof per block][toolbelt-gates] |
| Lifecycle integration | [Hooks][exec-hooks]; implementation/proof pending | [G1–G4 and R1][hooks-gates] |

The [architecture index](../architecture/index.md) points to the existing design;
[use cases](../use-cases.md) and [combos](../combos.md) map user outcomes to operations.
Gate identifiers are scoped to their owning document. A planned gate listed here is not an enforced pass.

## Repository gates

The [validation workflow][workflow] defines jobs; the [smoke inventory][smoke] declares required steps and evidence
paths.
[README validation][validation] explains the execution surfaces and the required `ci-skills/required` result.

| From: required job | To: checks and evidence producer |
| --- | --- |
| `validate` | [Static checks][static], API style, schemas, Python/Bats, [live receipts][live-check] |
| `gate-ci-skills-endpoints` | [Endpoint gate][endpoints], schema and endpoint unit checks |
| `test-install` | [Installed-package checks][install-tests], toolchain schema and API documentation |
| `required-result` | [Aggregate evidence and publish/read back required status][aggregate] |

Both [bless.sh](../../bless.sh) and [the CI static entrypoint][static] use the
[shared static check list][static-library]: `whitespace`, `json`, `schemas`, `yaml`, `markdown`, `bash_syntax`,
`shell`, `shfmt`, `actionlint`, `gitleaks`, `python`, `endpoints`, `portability`, `source_cycle` and `runtime_sync`.
The [portability gate][portability] and [endpoint gate][endpoints] retain their own implementations.

[Live-acceptance policy][live-policy] selects the receipt requirements; [receipt evaluation][live-check] checks them.
[Installed-behavior verification][verification] explains command-level proof, and
[acceptance evidence](../../tests/acceptance/) holds candidate and historical records.
[References](../references/index.md) links supporting material without replacing these authorities.

[exec-ci01]: ../exec-plans/active/ci01-skill-catalog.md
[exec-ci02]: ../exec-plans/active/ci02-command-line-contract.md
[exec-ci07]: ../exec-plans/active/ci07-record-schemas.md
[exec-ci08]: ../exec-plans/active/ci08-routing.md
[exec-ci09]: ../exec-plans/active/ci09-reference-navigation.md
[exec-ci11]: ../exec-plans/active/ci11-tool-delivery.md
[exec-ci14]: ../exec-plans/active/ci14-gitlab-connections.md
[exec-ci15]: ../exec-plans/active/ci15-runner-workflows.md
[exec-ci12]: ../exec-plans/completed/ci12-node-onboarding.md
[exec-ci13]: ../exec-plans/active/ci13-image-build-readiness.md
[exec-toolbelt]: ../exec-plans/active/gitlab-toolbelt-plan.md
[exec-hooks]: ../exec-plans/active/skill-session-hook.md
[ci01]: ../phases/CI01-CATALOG.md
[ci01-gates]: ../phases/CI01-CATALOG.md#gates
[ci02]: ../phases/CI02-CLI.md
[ci02-gates]: ../phases/CI02-CLI.md#interface-gate
[ci07]: ../phases/CI07-SCHEMA.md
[ci07-gates]: ../phases/CI07-SCHEMA.md#validation
[ci08]: ../phases/CI08-ROUTING.md
[ci08-gates]: ../phases/CI08-ROUTING.md#gates
[ci09]: ../phases/CI09-REFERENCE.md
[ci09-gates]: ../phases/CI09-REFERENCE.md#gates
[ci11]: ../phases/CI11-TOOLS.md
[ci11-gates]: ../phases/CI11-TOOLS.md#gates
[ci12]: ../phases/CI12-NODE-ONBOARDING.md
[ci12-gates]: ../phases/CI12-NODE-ONBOARDING.md#gates
[ci12-evidence]: ../phases/CI12-NODE-ONBOARDING.md#live-smoke-evidence
[ci13]: ../phases/CI13-IMAGE-BUILD-READINESS.md
[ci13-gates]: ../phases/CI13-IMAGE-BUILD-READINESS.md#gates
[ci13-evidence]: ../phases/CI13-IMAGE-BUILD-READINESS.md#live-smoke-evidence
[ci14]: ../phases/CI14-GITLAB-CONNECTIONS.md
[ci14-gates]: ../phases/CI14-GITLAB-CONNECTIONS.md#gates
[ci15]: ../phases/CI15-RUNNER-WORKFLOWS.md
[ci15-gates]: ../phases/CI15-RUNNER-WORKFLOWS.md#gates
[toolbelt-gates]: ../exec-plans/active/gitlab-toolbelt-plan.md#gates-and-proof-per-block
[hooks-gates]: ../exec-plans/active/skill-session-hook.md#gates
[workflow]: ../../.github/workflows/validate.yml
[smoke]: ../../inventory/ci/smoke-tests.yaml
[validation]: ../../README.md#validation
[static]: ../../scripts/ci/static.sh
[static-library]: ../../lib/bash/automation/bless.bash
[endpoints]: ../../gates/gate-ci-skills-endpoints.py
[portability]: ../../gates/gate-ci-skills-portability.py
[install-tests]: ../../tests/python/test_installed_package.py
[aggregate]: ../../tools/ci_required_result.py
[live-policy]: ../../inventory/ci/live-acceptance.json
[live-check]: ../../tools/check_live_acceptance.py
[verification]: ../contribution_guide.md#5-verify-the-installed-behavior
