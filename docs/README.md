# Documentation

This page is the project and capability overview: what `ci-skills` does on `main` today, what the phase
documents will add, and where the dated records are. Every phase document says "Status: proposed": it describes
work still to do, not a capability that exists. Start with [CI10-PHASES](phases/CI10-PHASES.md) for the order.

## Capabilities

Available rows are the 17 commands [`ci-skills/tools.json`](../ci-skills/tools.json) declares, generated from
`ci-skills/lib/core/catalog.py`; run any of them with `--describe` for its contract. Details names the
authorities a command reads; "writes" means it changes state only after a confirmed plan.

| Capability | What the agent can accomplish | Available or planned | Details |
| --- | --- | --- | --- |
| `access_check.py` | Prove access to every selected authority; one receipt | available | github, gitlab, kubernetes |
| `bin/ci-api` | Read one GitHub or GitLab API endpoint, bounded output | available | github or gitlab |
| `bin/ci-binary-build` | Plan an exact-commit OpenShift Binary BuildConfig without applying it | available | local |
| `ceph_cluster.py` | Read Ceph health, OSD hierarchy, inactive PGs and OSD/monitor Pods | available | kubernetes |
| `ceph_kernel.py` | Classify host Ceph/RBD kernel journal lines through an existing Pod | available | kubernetes |
| `cilium_node.py` | Read Cilium status and health from an agent Pod on one node | available | kubernetes |
| `cilium_status.py` | Aggregate Cilium agent, operator and node health by real exec | available | kubernetes |
| `event_trace.py` | Return a time-ordered event trace from both event APIs | available | kubernetes |
| `gitlab_access.py` | Resolve the GitLab credential; read back identity and exact target | available | gitlab |
| `gitlab_issue.py` | Create or reuse an exact bug issue, read back independently | available | gitlab; writes |
| `gitlab_job.py` | Read one CI job with its pipeline, runner and bounded trace | available | gitlab |
| `gitlab_milestone.py` | Create or update an exact milestone, read back independently | available | gitlab; writes |
| `gitlab_pipeline.py` | Read one CI pipeline and bounded job progress by stage | available | gitlab |
| `gitlab_runner.py` | Assign a runner or create one, then verify it | available | gitlab; writes |
| `gitlab_wiki.py` | Create or update an exact wiki page, read back independently | available | gitlab; writes |
| `k8s_verify_mtu_consistency.py` | Compare uplink MTUs across selected nodes | available | kubernetes; writes |
| `storage_report.py` | Correlate claims, volumes, attachments, pods and controllers | available | kubernetes |
| installer | install the skill, digest-verified | available | `tools/install_ci_skills.py`, `install.sh` |
| access references | how access is proved and how a project binds its target | available | `ci-skills/references/` |
| record schemas | one closed schema per record kind | 6 of 14 available | [CI07-SCHEMA](phases/CI07-SCHEMA.md), `schemas/` |
| GitLab tool belt | milestone, issue, wiki and runner actions above | available | [delivery plan](gitlab-toolbelt-plan.md) |
| skill discovery and install | list, get and install every skill, local or vendored, into any Codex or Claude scope | planned | [CI01-CATALOG](phases/CI01-CATALOG.md) |
| one command-line contract | every command answers `--help`, `--describe`, `--json` and `--yaml` with one exit table | planned | [CI02-CLI](phases/CI02-CLI.md) |
| gates | one entrypoint, `scripts/check.sh`, runs every gate; no gate route yet (D-GATE) | planned | [CI03-GATES](phases/CI03-GATES.md) |
| local hooks | advisory pre-commit and pre-push checks | planned | [CI04-HOOKS](phases/CI04-HOOKS.md) |
| vendored `glab` skills | vendor upstream `glab` skills and references under one digest lock | planned | [CI05-VENDOR](phases/CI05-VENDOR.md) |
| tests and live smoke | CI-only test command, coverage, live smoke proven by read-back | planned | [CI06-TESTS](phases/CI06-TESTS.md) |
| routing and selective loading | load only the reference a task needs | planned | [CI08-ROUTING](phases/CI08-ROUTING.md) |
| references and navigator | ask what the skill can do about X; read one upstream keyword chunk | planned | [CI09-REFERENCE](phases/CI09-REFERENCE.md) |
| GitLab jobs and pipelines | get, watch, logs, list by status, interval, keyword, job-name glob; start, retry, cancel | planned | [CI11-TOOLS](phases/CI11-TOOLS.md) |
| pipeline schedules | list, get, play, create, update | planned | [CI11-TOOLS](phases/CI11-TOOLS.md) |
| merge-request checks | one MR's state, head pipeline and failing jobs with log snippets | planned | [CI11-TOOLS](phases/CI11-TOOLS.md) |
| runners | tags, state, online, projects, executor; delete, reset token | planned | [CI11-TOOLS](phases/CI11-TOOLS.md) |
| CI variables | show, set, set from a secret file | planned | [CI11-TOOLS](phases/CI11-TOOLS.md) |
| job failure trace | the last failed job, its interval and the cluster events in it | planned | [CI11-TOOLS](phases/CI11-TOOLS.md) |
| OpenShift views | routes, HA, machines against a node spec | planned | [CI11-TOOLS](phases/CI11-TOOLS.md) |
| OpenShift ISO, CSR, MTU | build and serve ISOs; approve planned CSRs; render and apply the MTU config | planned | [CI11-TOOLS](phases/CI11-TOOLS.md) |
| cluster state and health | one snapshot, one health view: CNI, MTU, controllers, Ceph, storage, events | planned | [CI11-TOOLS](phases/CI11-TOOLS.md) |
| Ceph bench and NFS | benchmarks and NFS export checks | planned | [CI11-TOOLS](phases/CI11-TOOLS.md) |
| toolbox and Harbor | build the toolbox; push by digest; robots, sanity, charts, pull secrets | planned | [CI11-TOOLS](phases/CI11-TOOLS.md) |

Also: [field notes](field-notes.md).

## Records

| Date | Kind | Record | What it holds |
| --- | --- | --- | --- |
| 2026-10-06 | plan | [plan](plans/2026-10-06/reference-and-docs-readjust.md) | the approved plan behind PR #29 |
| 2026-10-06 | inventory | [inventories](plans/2026-10-06/ci11-inventories/index.md) | source inventories for CI11 |

Inventories use `source` and `$SOURCE_REPO` for the source project and checkout.
