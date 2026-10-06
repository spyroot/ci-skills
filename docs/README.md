# Documentation

Every document of this repository lives here, in git. Local workspaces hold only queue and coordination state.
Current specifications are the authority. Records are kept as they were written; where a record and a current
specification disagree, the specification wins.

## Current specifications

Start with [CI10-PHASES](phases/CI10-PHASES.md): the phase order, how a phase lands, the implementation map, the
decisions taken and the open decisions.

| Document | Subject |
| --- | --- |
| [CI01-CATALOG](phases/CI01-CATALOG.md) | discover, list, get, install; Codex metadata and install scopes |
| [CI02-CLI](phases/CI02-CLI.md) | one command-line contract |
| [CI03-GATES](phases/CI03-GATES.md) | verification gates and the gate route (G0) |
| [CI04-HOOKS](phases/CI04-HOOKS.md) | local hooks |
| [CI05-VENDOR](phases/CI05-VENDOR.md) | vendored `glab` skills and upstream references, one lock |
| [CI06-TESTS](phases/CI06-TESTS.md) | testing strategy and the live smoke contract |
| [CI07-SCHEMA](phases/CI07-SCHEMA.md) | record schemas |
| [CI08-ROUTING](phases/CI08-ROUTING.md) | routing, lazy loading and compact representation |
| [CI09-REFERENCE](phases/CI09-REFERENCE.md) | references, tool operations, upstream knowledge and the navigator |
| [CI10-PHASES](phases/CI10-PHASES.md) | plan overview and order |
| [CI11-TOOLS](phases/CI11-TOOLS.md) | the tool catalogue and the port of the source repo's scripts |
| [GitLab tool belt](gitlab-toolbelt-plan.md) | delivery plan of the GitLab operations tool belt |
| [Field notes](field-notes.md) | observations from the first deliveries |

## Records

`GAL-*` is the former name of the `CIxx` phases, renamed in `e6254bd`.

| Date | Kind | Record | What it holds |
| --- | --- | --- | --- |
| 2026-10-02 | brainstorm | [brainstorms](brainstorm/2026-10-02/) | one brainstorm per GAL phase document |
| 2026-10-04 | plan | [GAL phases](plans/2026-10-04/GAL-phases.md) | the GAL phase plan and the questions answered |
| 2026-10-04 | review | [GAL review](reviews/2026-10-04/GAL-phase-docs.review.md) | review of the GAL phase documents |
| 2026-10-06 | plan | [plan](plans/2026-10-06/reference-and-docs-readjust.md) | the approved plan behind PR #29 |
| 2026-10-06 | inventory | [inventories](plans/2026-10-06/ci11-inventories/index.md) | source inventories for CI11 |
| 2026-10-06 | review | [reviews](reviews/2026-10-06/index.md) | reviews of PR #29 and the per-phase editor reports |

Records were copied unchanged except for two mechanical substitutions: the source project's name is written
`source` and its checkout `$SOURCE_REPO`, because the project-neutrality gate forbids the name, and host paths are
written relative to the home directory (`~/`).
