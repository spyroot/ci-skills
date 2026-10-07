# Documentation

Public specifications and source inventories live here in git. Current specifications are the authority; older
records may describe superseded behavior.

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

`CI-*` is the former name of the `CIxx` phases, renamed in `e6254bd`.

| Date | Kind | Record | What it holds |
| --- | --- | --- | --- |
| 2026-10-02 | brainstorm | [brainstorms](brainstorm/2026-10-02/) | one brainstorm per GAL phase document |
| 2026-10-06 | plan | [plan](plans/2026-10-06/reference-and-docs-readjust.md) | the approved plan behind PR #29 |
| 2026-10-06 | inventory | [inventories](plans/2026-10-06/ci11-inventories/index.md) | source inventories for CI11 |

Inventories use `source` and `$SOURCE_REPO` for the source project and checkout.
