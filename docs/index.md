# Documentation

Public specifications and source inventories live here in git. Current specifications are the authority; older
records may describe superseded behavior.

## Use and composition

Start with [Install and first run](../README.md#install-and-first-run), then the
[agent capability model](capability-model.md) for root-to-command discovery. [Use cases](use-cases.md) state the
requested outcomes; [combos](combos.md) identify the atomic steps, their current status and the evidence needed for a
combined result. The [contribution guide](contribution_guide.md) maps those steps to the existing callback library.
[Field notes](field-notes.md) retain observations from the first deliveries.

## Interface topics

These links organize interface topics; their list order is not an implementation dependency. The
[capability model](capability-model.md#architecture-and-dependency-direction) traces the actual command path.
The [CI11-TOOLS plan](phases/CI11-TOOLS.md) distinguishes installed commands from remaining delivery; the generated
[`tools.json`](../ci-skills/tools.json) is the installed command contract.

| Document | Subject |
| --- | --- |
| [CI01-CATALOG](phases/CI01-CATALOG.md) | discover, list, get, install; Codex metadata and install scopes |
| [CI02-CLI](phases/CI02-CLI.md) | one command-line contract |
| [CI07-SCHEMA](phases/CI07-SCHEMA.md) | record schemas |
| [CI08-ROUTING](phases/CI08-ROUTING.md) | routing, lazy loading and compact representation |
| [CI09-REFERENCE](phases/CI09-REFERENCE.md) | references, tool operations, upstream knowledge and the navigator |
| [CI11-TOOLS](phases/CI11-TOOLS.md) | the tool catalogue and the port of the source repo's scripts |

## Delivery leaves

| Document | Current scope |
| --- | --- |
| [CI12-NODE-ONBOARDING](phases/CI12-NODE-ONBOARDING.md) | commands installed; live ISO evidence; CSR/join proof mocked |
| [Node onboarding ExecPlan](exec-plans/active/ci12-node-onboarding.md) | CI12.0, CI12.1 and CI12.2 delivery and remaining proof |
| [CI13-IMAGE-BUILD-READINESS](phases/CI13-IMAGE-BUILD-READINESS.md) | probe and chain installed; consumer-job proof remains external |
| [Image-build readiness ExecPlan](exec-plans/active/ci13-image-build-readiness.md) | CI13 milestone order and remaining delivery |
| [CI14-GITLAB-CONNECTIONS](phases/CI14-GITLAB-CONNECTIONS.md) | planned: GitLab-to-GitHub synchronization and GitLab Agent installation |
| [CI15-RUNNER-WORKFLOWS](phases/CI15-RUNNER-WORKFLOWS.md) | planned: runner scopes and toolbox delivery |
| [GitLab tool belt](gitlab-toolbelt-plan.md) | delivery plan of the GitLab operations tool belt |
| [CI Skills agent hooks](skill-session-hook.md) | lifecycle integration and delivery lock |
