# Documentation

Public specifications and source inventories live here in git. Current specifications are the authority; older
records may describe superseded behavior.

## Current specifications

Start with [Install and first run](../README.md#install-and-first-run) to use the skill, or the
[contribution guide](contribution_guide.md) to add a callback or workflow. The existing
[CI11-TOOLS plan](phases/CI11-TOOLS.md) records the tool catalogue and implementation map.

| Document | Subject |
| --- | --- |
| [Agent capability model](capability-model.md) | discovery, focused workflows and effect-to-cause investigation |
| [CI01-CATALOG](phases/CI01-CATALOG.md) | discover, list, get, install; Codex metadata and install scopes |
| [CI02-CLI](phases/CI02-CLI.md) | one command-line contract |
| [CI07-SCHEMA](phases/CI07-SCHEMA.md) | record schemas |
| [CI08-ROUTING](phases/CI08-ROUTING.md) | routing, lazy loading and compact representation |
| [CI09-REFERENCE](phases/CI09-REFERENCE.md) | references, tool operations, upstream knowledge and the navigator |
| [CI11-TOOLS](phases/CI11-TOOLS.md) | the tool catalogue and the port of the source repo's scripts |
| [CI12-NODE-ONBOARDING](phases/CI12-NODE-ONBOARDING.md) | planned: tool Pod, native OpenShift ISO, then node join |
| [Node onboarding ExecPlan](exec-plans/active/ci12-node-onboarding.md) | planned CI12.0, CI12.1 and CI12.2 milestones |
| [CI13-IMAGE-BUILD-READINESS](phases/CI13-IMAGE-BUILD-READINESS.md) | planned: Podman probe, then a GitLab build-and-push chain |
| [Image-build readiness ExecPlan](exec-plans/active/ci13-image-build-readiness.md) | the executable plan for CI13, milestone by milestone |
| [GitLab tool belt](gitlab-toolbelt-plan.md) | delivery plan of the GitLab operations tool belt |
| [Field notes](field-notes.md) | observations from the first deliveries |
| [Use cases](use-cases.md) | the behaviour we want, use case by use case, in our own words |
| [Combos](combos.md) | chains worth packing, with the status of each step and the evidence that proves the chain |
| [CI Skills agent hooks](skill-session-hook.md) | lifecycle integration and delivery lock |
