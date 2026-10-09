# CI11-TOOLS: the tool catalogue and the port of the source repo's scripts

Status: proposed. Order and dependencies: CI10-PHASES, Phases. Delivered one
tool per pull request, in the order of the catalogue.

## Motivation

An agent can already perform these operations through `glab`, `oc`, and the other existing clients. ci-skills packages
recurring operations into deterministic, tested combinations: construct the request, call the existing API, parse its
JSON, normalize and reduce the result when useful, and perform the operation's required read-back. This avoids repeated
`jq` construction, missed response keys, and corrective reads caused by parsing mistakes. The same approach applies to
GET, PUT, and POST; normalization preserves the provider's result and error meaning.

The other benefit is less context. Offer the actions available at the current node and small pointers to relevant
references. A broad cluster needs little output; selecting a smaller cluster permits more detail about that narrower
scope. The agent chooses a command, requests its native `--help`, or follows a reference when needed. Do not return the
whole catalogue or provider response and make the agent search it.

This work consumes existing APIs; it does not implement GitLab or repair its concurrency semantics. Two agents updating
the same milestone at the same instant is not, by itself, a reason to add coordination code or another test family.
Additional handling needs an existing requirement or a demonstrated defect in behavior ci-skills owns. Preserve the
existing safeguards while refactoring and keep tests focused on actual parsing, execution, and failure paths.

## Proposition interface

A **proposition** is a compact, machine-readable offering of the tools and references available at one node. The
entrypoint proposes the main clusters; each selected node proposes its own relevant tools and references using the
same node shape. Derive these offerings from `ci-skills/lib/python/core/catalog.py`, the existing command catalogue,
rather than maintaining a second inventory. The proposition interface handles discovery; the callback list handles
execution order.

This is a graph: a node may offer a related pipeline, job, or milestone tool as well as its own combinations. The same
command or reference can be reachable from several nodes without duplicating its implementation or document. Return
the current node's relevant connections, not every descendant. Reference links are optional reading, not prerequisites
or automatically fetched content.

The following payload examples specify the proposed interface, not installed commands. Their short family names and
reference paths are illustrative; existing `gitlab_runner.py`, `gitlab_pipeline.py`, `gitlab_milestone.py`, and
`gitlab_job.py` remain the installed entrypoints until a capability explicitly introduces another name. A live offering
must use its actual installed filename and must not advertise a proposed command as executable.

### Root example

The `glab` part of the root offers command families and one relevant reference:

```json
{
  "glab": {
    "tools": {
      "runner.py": {},
      "pipeline.py": {},
      "milestone.py": {},
      "jobs.py": {}
    },
    "reference": {
      "mcp": {
        "summary": "GitLab MCP configuration and toolsets.",
        "pointer": "references/mcp.md"
      }
    }
  }
}
```

### Jobs example

Selecting `jobs.py` returns its own proposition directly, including a combination and a task-specific reference:

```json
{
  "tools": {
    "watch.py": {},
    "create_tagged_protected_job.py": {}
  },
  "reference": {
    "pipeline variables": {
      "summary": "Predefined CI/CD variables reference",
      "pointer": "references/predefine.md"
    }
  }
}
```

An entrypoint's descriptive filename already tells the agent what it does. Its empty object does not need a
repeated summary, entrypoint, or `args: ["--describe"]` wrapper. The agent can use `--help` for arguments. A node can
include related tools from other command families when useful; narrowing the scope does not require removing them.

### Proposition schema

The existing [reference-next schema](../../schemas/reference-next.schema.json) is the owner to extend for propositions.
Its current 1.x `choices` envelope does not accept the grouped shape above. The proposed response revision is `2.0`;
a proposition reference's `pointer` is a path string, as shown in the root/jobs examples. Preserve the separate 1.x
command-pointer definitions (`action`, `entrypoint`, `args`) still used by the
[reference-section schema](../../schemas/reference-section.schema.json) and existing result schemas. Proposition
reference paths do not reuse that command-pointer object.

Every response carries `schema_version` alongside its payload members. The examples above omit only that response
metadata to show the root and node shapes. The schema describes structure; it does not prescribe which reference an
agent must read or require traversal through `run`, `read`, or `describe` before invoking a command.

| Element | Shape |
| --- | --- |
| Root payload | Mapping of cluster names to proposition nodes. |
| Node payload | `tools` and `reference` mappings, returned directly when that node is selected. |
| `tools` | Mapping of actual entrypoint filenames to `{}` in this proposition version. |
| `reference` | Mapping of reference names to a short `summary` and concrete `pointer`. |
| `schema_version` | Response version, proposed as `2.0` for this shape. |

This proposed schema defines both complete response shapes without an extra payload wrapper. The root carries
`schema_version` beside its named clusters; a selected node carries it beside `tools` and `reference`. Both reuse the
same node definition:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "oneOf": [
    {
      "type": "object",
      "required": ["schema_version"],
      "properties": {
        "schema_version": {"const": "2.0"}
      },
      "additionalProperties": {"$ref": "#/$defs/node"}
    },
    {
      "allOf": [
        {"$ref": "#/$defs/node"},
        {"required": ["schema_version"]}
      ]
    }
  ],
  "$defs": {
    "node": {
      "type": "object",
      "required": ["tools", "reference"],
      "properties": {
        "schema_version": {"const": "2.0"},
        "tools": {
          "type": "object",
          "additionalProperties": {"type": "object", "maxProperties": 0}
        },
        "reference": {
          "type": "object",
          "additionalProperties": {
            "type": "object",
            "required": ["summary", "pointer"],
            "properties": {
              "summary": {"type": "string"},
              "pointer": {"type": "string"}
            },
            "additionalProperties": false
          }
        }
      },
      "additionalProperties": false
    }
  }
}
```

This section revises the discovery shape proposed in CI09-REFERENCE; that phase's older forced `run`/`read` traversal
must be reconciled to this specification when the capability lands. Existing per-command help and result contracts
remain available. The schema files and installed commands are unchanged by this documentation proposal.

## Callback workflow boilerplate

Every workflow main follows this composition pattern. The following names describe the proposed Python interface,
not classes already shipped. `build_parser()` owns this workflow's arguments; the action models map those arguments;
`ExecutionContext` supplies the selected targets and provider services; `Executor` runs the ordered callbacks;
`emit_result()` adapts the existing report emitter and exit-code mapping.

```python
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    build = BuildToolbox(action=BuildAction.from_args(args))
    publish = PublishToolbox(action=PublishAction.from_args(args), build=build)
    runner = configure_runner(action=RunnerAction.from_args(args), image=publish)
    result = Executor(
        context=ExecutionContext.from_args(args),
        callbacks=[
            K8SAuth(), build,
            HarborAuth(), publish,
            GitLabAuth(), runner, VerifyRunner(runner=runner),
        ],
    ).run()
    return emit_result(result, args)
```

This is one combo: build the toolbox on OpenShift, publish its image to Harbor, configure the selected runner/CI to use
it, and verify the result. Dependencies are explicit: build result → published image → runner configuration. The
`configure_runner()` factory selects a callback for the requested runner type; execution stays in that callback.
Different mains expose different arguments and combinations. Adding a combo should mostly change this short list;
introduce a new atom only when an operation is missing. Moving all orchestration into a bespoke workflow function does
not satisfy the pattern.

### Callback ownership and lifecycle

```text
AbstractCallback
├── GitlabCallback
├── K8SCallback
└── HarborCallback

AbstractCallbacks = list[AbstractCallback]
```

`AbstractCallback` declares `collect()`, `run()`, `update()`, and `cleanup()` with `abc.abstractmethod`. One shared
optional-hook implementation supplies no-op defaults through the provider bases, so an atomic callback can implement
one, two, or all four hooks. `listify()` accepts one callback or a callback sequence and produces the ordered
`AbstractCallbacks` list. Provider bases own shared client/session access; concrete callbacks own operation state and
behavior. The generic executor must not dispatch on concrete provider names.

For live execution, finish a callback's applicable `collect()`, `run()`, and `update()` hooks before entering the next
callback. Collect reads required inputs; run performs the atomic operation; update performs its declared post-run work.
On completion or failure, invoke cleanup for entered callbacks in reverse order, retaining the primary failure and
cleanup evidence. A failed or incomplete step stops dependent work. Cleanup respects existing resource ownership and
recovery behavior; it does not remove a successfully requested persistent runner, milestone, or image.

Authentication callbacks establish the provider services used by later callbacks. Typed results carry created IDs and
image identities to explicit consumers. `target.toml`, owned by `core/endpoints.py` and resolved by `core/target.py`,
selects endpoints and credential sources. Build settings, tags, requested changes, and other operation inputs come
from that main's arguments or its selected workflow specification. Existing plan/apply confirmation and independent
read-back remain part of action execution; the lifecycle does not authorize a write.

### Ground the refactor in existing code

The following owners are already in `ci-skills/lib/python/core/`; move operation logic into the appropriate methods
and reuse these facilities. Do not copy them into parallel implementations.

| Existing owner | Callback implementation mapping |
| --- | --- |
| `target.py`, `endpoints.py`, `credentials.py` | Reuse target resolution and the existing `GitLabAuthentication` interface. `GitLabAuth` calls the existing binding/access path. |
| `gitlab_session.py`, `access.py`, `gitlab_api.py` | Reuse `BoundGitLabSession`, `check_gitlab_operation_access()`, and `GlabAPIClient` as GitLab callback services. |
| `gitlab_actions.py` | Extract shared plan/apply/report orchestration from `run_action_cli()` into the already-planned `core/action.py`; `Executor` owns sequencing. Keep CLI parsing at the boundary. |
| `gitlab_milestones.py` | Move `prepare()`, `apply()`, and `_create()` behavior into typed milestone actions and callbacks, preserving exact-match checks and create guards. |
| `gitlab_runners.py` | Move `_create()`, `_assign()`, `_tag()`, and `read()` into create, attach, tag, and read/verify callbacks. Preserve token handling, scope checks, and recovery. |
| `runtime.py`, `collect.py`, existing Kubernetes collectors | Reuse bounded subprocess execution and collection logic through Kubernetes callbacks. |
| `report.py`, `status.py`, `provenance.py` | Reuse output, redaction, status/exit mapping, and evidence identities through the final adapter. |

Keep guarded operations intact when moving them. For example, milestone `_create()` holds its `create_guard` across
lookup, mutation, uncertain-write reconciliation, and read-back. It can remain one `run()` implementation rather than
splitting that guard incorrectly across hooks. Existing commands keep their arguments and result behavior during the
refactor. Harbor/toolbox and other missing atoms follow their delivery rows below; their absence is not a reason to
rebuild the transports or collectors already present.

## Goal

Every tool does one task we named, with a machine-readable spec, and nothing
more. No open-ended implementation, no invented behaviour: a tool exists only
when a sentence we recorded or a source script stands behind it. The port
recipe below applies the proposition and callback specification above; the four source-row sections name each source
script by path relative to the source repo root at the inventory commit,
both recorded in the private source inventory. That project's name never
enters a tracked file: the neutrality gate forbids it.

## The catalogue

Commands that ship today are in [docs/README.md](../README.md), Capabilities; this table lists only
what this phase adds or changes.

Tasks we named on 2026-10-06. "exists" means the capability is in `ci-skills`
today as the bare command (`gitlab_job.py --job-url URL` keeps working for the
committed receipt; the verbs arrive with `watch` and `logs`); "port" means it
comes from a source script; "new" means we named it, or README "Proposed
GitLab actions" lists it, and no script exists, so it is built to the
sentence and nothing more. `Pattern` is the README grouping ("Tool grouping"):
visibility, ci (CI Combo), generic (Generic Combo), toolchain (Toolchain
Combination). Authorities are the catalog's (`gitlab`, `kubernetes`, `harbor`);
`podman`, `skopeo`, `butane` and `oc` are `required_tools`, not authorities.

| Task | Tool | Library module | Authority | Mutates | Status | Pattern |
| --- | --- | --- | --- | --- | --- | --- |
| GitLab job get | `gitlab_job.py get` | `core/collect.py` | gitlab | no | exists | visibility |
| GitLab job watch | `gitlab_job.py watch` | `core/gitlab_jobs.py` | gitlab | no | port | ci |
| GitLab job logs | `gitlab_job.py logs` | `core/gitlab_jobs.py` | gitlab | no | port | ci |
| job dump | `gitlab_job.py list` | `core/gitlab_jobs.py` | gitlab | no | new | ci |
| GitLab pipeline get | `gitlab_pipeline.py get` | `core/gitlab_pipelines.py` | gitlab | no | exists | visibility |
| GitLab pipeline watch | `gitlab_pipeline.py watch` | `core/gitlab_pipelines.py` | gitlab | no | port | ci |
| GitLab pipeline logs | `gitlab_pipeline.py logs` | `core/gitlab_jobs.py` | gitlab | no | port | ci |
| pipeline dump, job glob | `gitlab_pipeline.py list` | `core/gitlab_pipelines.py` | gitlab | no | new | ci |
| child pipelines | `gitlab_pipeline.py children` | `core/gitlab_pipelines.py` | gitlab | no | port | ci |
| pipeline start | `gitlab_pipeline.py start` | `core/gitlab_actions.py` | gitlab | yes | port | ci |
| pipeline retry, cancel | `gitlab_pipeline.py retry\|cancel` | `core/gitlab_actions.py` | gitlab | yes | new | ci |
| schedules, read | `gitlab_schedule.py list\|get` | `core/gitlab_schedules.py` | gitlab | no | new | ci |
| schedules, write | `gitlab_schedule.py play\|create\|update` | `core/gitlab_schedules.py` | gitlab | yes | new | ci |
| merge-request checks | `gitlab_mr.py check` | `core/gitlab_merge_requests.py` | gitlab | no | port | ci |
| runner quick view | `gitlab_runner.py list\|get` | `core/gitlab_runners.py` | gitlab | no | new | visibility |
| runner delete | `gitlab_runner.py delete\|reset-token` | `core/gitlab_runners.py` | gitlab | yes | port | generic |
| variables, read | `gitlab_variable.py show` | `core/gitlab_variables.py` | gitlab | no | port | generic |
| variables, write | `gitlab_variable.py set\|set-secret` | `core/gitlab_variables.py` | gitlab | yes | port | generic |
| milestone list | `gitlab_milestone.py list` | `core/gitlab_actions.py` | gitlab | no | port | generic |
| job failure trace | `ci_failure_trace.py` | `core/ci_failure_trace.py` | gitlab, kubernetes | no | new | ci |
| OpenShift routes | `ocp_route.py` | `core/ocp_routes.py` | kubernetes | no | port | visibility |
| OpenShift HA view | `ocp_ha.py` | `core/ocp_ha.py` | kubernetes | no | port | visibility |
| OpenShift machine view | `ocp_machine.py` | `core/ocp_machines.py` | kubernetes | no | port | visibility |
| OpenShift ISO report | `ocp_iso.py rhcos-report` | `core/ocp_iso.py` | kubernetes | no | port | visibility |
| OpenShift ISO actions | `ocp_iso.py` action verbs (bullet) | `core/ocp_iso.py` | kubernetes | yes | port | toolchain |
| CSR approval | `ocp_csr.py` | `core/ocp_csr.py` | kubernetes | yes | port | generic |
| NIC MTU config | `k8s_verify_mtu_consistency.py` | `core/nic_mtu_config.py` | kubernetes | yes | port | generic |
| k8s state snapshot | `k8s_state.py` | `core/k8s_state.py` | kubernetes | no | port | visibility |
| cluster health, one view | `cluster_health.py` | `core/cluster_health.py` | kubernetes | no | new | generic |
| Ceph benchmark | `ceph_bench.py` | `core/ceph_bench.py` | kubernetes | yes | port | generic |
| Ceph NFS check | `ceph_nfs.py` | `core/ceph_nfs.py` | kubernetes | no | port | visibility |
| toolbox build | `toolbox_build.py plan\|apply` | `core/toolbox.py` | kubernetes, harbor | yes | port | toolchain |
| push to Harbor | `harbor_push.py plan\|apply` | `core/registry_mirror.py` | harbor | yes | port | toolchain |
| Harbor robot | `harbor_robot.py create` | `core/harbor_api.py` | harbor | yes | new | toolchain |
| Harbor sanity | `harbor_sanity.py` | `core/harbor_api.py` | harbor | no | port | visibility |
| Harbor charts | `harbor_charts.py` | `core/harbor_charts.py` | harbor | no | port | visibility |
| pull secret | `harbor_pull_secret.py` | `core/harbor_pull_secret.py` | kubernetes, harbor | yes | port | toolchain |

Library modules without a main: `core/registry_mirror.py` (`mirror_one`,
shared by the toolbox push, `harbor_push.py` and the runner images) and
`core/k8s_lifecycle.py` (UID-precondition delete and absence read-back, used
by the ISO `delete` and temporary Pods). `mutates` is declared per verb in the
catalog (CI10-PHASES, Modify), so read and write verbs share one main.

Per-tool interface beyond the universal tier and the query grammar (below),
with config keys and concurrency:

- `gitlab_job.py get|watch|logs`: `--job-url` (`get` also `--project --job-id`);
  `watch` the grammar's `--interval`, `--timeout`; `logs`
  `--lines N`, `--search`, `--failure-window N`; `[gitlab] url`.
- `gitlab_job.py list`: the `list` grammar; `--status` values
  `failed|success|running|pending|canceled|stuck` (`stuck`: pending or created
  longer than `--stuck-after S`); `--pipeline-id`, `--ref`; time on
  `finished_at` else `created_at`; `--search` over name, stage, ref, failure
  reason; fields: id, name, stage, status, failure reason, created, started,
  finished, duration, runner, pipeline id and ref, web URL.
- `gitlab_pipeline.py get|logs|children`: `--project`, `--pipeline-id`
  (or `--ref` for the newest; `watch` as Pipeline watch, exact); `logs` fans out the failed jobs' traces in
  parallel; `children` reads bridges and downstream pipelines; `[gitlab] url,
  project`.
- `gitlab_pipeline.py list`: the `list` grammar; `--ref`; `--name-glob` over
  the jobs of each pipeline; `[gitlab] url, project`; pages in parallel.
- `gitlab_pipeline.py start|retry|cancel`: `start` takes `--ref` (default:
  the project's `default_branch`, read live), repeatable `--variable
  KEY=VALUE`, `--require-protected-ref`, `--expected-sha` and `--live-plan`
  (its digest covers the resolved sha); `retry` and `cancel` take
  `--pipeline-id`; the apply flags; `[gitlab] url, project`.
- `gitlab_schedule.py list|get|play|create|update`: `--project`,
  `--schedule-id`, `--ref`, `--cron`, `--description`; the apply flags on the
  write verbs; `[gitlab] url, project`.
- `gitlab_mr.py check`: `--mr-url` or `--project --mr-iid`, `--lines`,
  `--failure-window`; `[gitlab] url`; trace reads in parallel.
- `gitlab_runner.py list|get`: `--project` or `--group`, `--runner-id` (`get`),
  `--managers` (`get`: the executor through GraphQL `CiRunnerManager.executorName`
  via a new `GlabAPIClient.graphql_json`, else `unknown`); fields: id,
  description, tags, status, online, paused, `runner_type`, projects,
  `contacted_at`, version, platform, architecture; per-project reads in
  parallel; `[gitlab] url`.
- `gitlab_runner.py delete|reset-token`: `--runner-id` or an exact
  `--description` (refused on more than one match); `--token-out PATH`
  (`reset-token`); the apply flags; `[gitlab] url, group`.
- `gitlab_variable.py show|set|set-secret`: `--project` or `--group`, `--key`,
  `--value-file` (never a value on argv), `--protected`, `--raw`,
  `--environment-scope`, `--type`; the apply flags on the write verbs;
  `[gitlab] url`; per-key reads in parallel.
- `gitlab_milestone.py list`: `--project`, `--state`; `[gitlab] url, project`.
- `ci_failure_trace.py`: `--project`, `--pipeline-id`, `--margin S` (default
  60); `[gitlab]`, `[kubernetes]`; the job read, then the event reads over the
  window in parallel.
- `ocp_route.py`: `--namespace`, `--search`, `--probe`; `[kubernetes]`;
  per-route probes in parallel.
- `ocp_ha.py`: `--search`; `[kubernetes]`; nodes, etcd, cluster operators and
  machine config pools read in parallel.
- `ocp_machine.py`: `--node-spec PATH`, `--search`; `[kubernetes]`;
  per-machine reads in parallel.
- `ocp_iso.py rhcos-report|rhcos-download|rhcos-serve|node-build|node-serve|delete`:
  `--node NAME` (repeatable), `--output-dir`; the apply flags on every verb
  but `rhcos-report`; `[kubernetes]`, caller-selected ISO inputs; the plan's reads in parallel, builds
  inside the Pod serial.
- `ocp_csr.py`: `--search` over names and signers; the apply flags (approve
  only the planned names and signers); `[kubernetes]`; one CSR list.
- `k8s_verify_mtu_consistency.py` (extended): the MachineConfig rendering in the plan
  and the fingerprinted apply behind the apply flags; `[kubernetes]`, caller-selected NIC MTU inputs;
  `butane` is a required tool.
- `k8s_state.py`: `--namespace`, `--node`, `--search`, the `time_ranged`
  tier; `[kubernetes]`; every independent list read in parallel.
- `cluster_health.py`: `--ceph-namespace`, `--cilium-namespace`, `--last`
  (event window, default 15m), `--skip COMPONENT` (repeatable);
  `[kubernetes]`; every component collected in parallel through the
  collectors' library functions, never by running the other tools.
- `ceph_bench.py`: the benchmark arguments of `sanity_check_ceph.sh:265,512`,
  named when the row lands; the apply flags (the benchmarks write).
  `ceph_nfs.py`: `--ceph-namespace`; read-only by default, the write test
  behind the apply flags; `[kubernetes]`.
- `toolbox_build.py plan|apply`: `--tag`, `--builder openshift|podman`;
  caller-selected build inputs, `[kubernetes]` and `[harbor]` access; one build.
- `harbor_push.py plan|apply`: `--image`, `--tag`, `--authfile PATH`;
  `[harbor]`; one copy per image, a bounded pool across images.
- `harbor_robot.py create`: `--name` (the robot's exact name), `--permission`
  (repeatable), `--token-out PATH`; the apply flags; `[harbor]`
  `admin_credential_file`; one create.
- `harbor_sanity.py`: `--search`; `[harbor]`; identity, project and
  repository reads in parallel.
- `harbor_charts.py`: `--search`, `--versions CHART`, `--pull CHART:VERSION`,
  `--blocks PATH`, `--stamps PATH`, `--all`; `[harbor]`; `--all` as a bounded
  pool.
- `harbor_pull_secret.py sync`: the Secret's namespace and name as flags; the
  apply flags; `[harbor] credential_file`, `[kubernetes]`; key-by-key
  read-back.

Source paths, functions and lines per tool are in the four source-row
sections below (inventory of 2026-10-06 at the source commit recorded in the
private source inventory); the tests per tool are the
`tests/python/test_<module>.py` files of "Delivery, test and proof".

## The port recipe

The same ten steps for every row; the row repeats them with the real paths.

1. **Point.** Name the source script by path under the source repo root, the
   functions and lines that implement the task, and its tests.

2. **Bound the task.** One tool does one task we named.

3. **Library first.** Move atomic operation logic into the callback methods described in
   [Callback workflow boilerplate](#callback-workflow-boilerplate), under `ci-skills/lib/python/core/`.
   Reuse pure helpers and the existing transports: `core.gitlab_api.GlabAPIClient`
   for GitLab, `core.runtime.run_command_bounded` for `oc`, `kubectl`,
   `podman` and `skopeo`, and one bounded standard-library HTTP helper
   `core/http.py` shared by the Harbor API and the reference fetch. Never a
   second transport, envelope, logger, exit table or redaction.

4. **Declare.** Add the command to `COMMANDS` in `ci-skills/lib/python/core/catalog.py`. Every entry declares `kind`,
   `purpose`, `use_when`, `requires`, `capabilities`, `options` (the command's own options with their help text),
   `required_options` and `returns`. It adds `subcommands`, `mutates`, `required_tools`, `execution_surface` and
   `side_effects` only when they differ from the defaults: none, false, none, "selected authority API" and
   "none". Add the `harbor` authority once (target `[harbor]` with the keys of the config block below;
   credential chain: target file, then the declared variables, then none, reported in `credential_sources` like
   the others). The entry this phase adds for `cluster_health.py`:

   ```python
   "cluster_health.py": {
       "kind": "cluster_health",
       "purpose": "Report CNI, MTU, controller, Ceph, storage and event health in one view.",
       "use_when": "You need one answer to whether the selected cluster is healthy.",
       "requires": ("kubernetes",),
       "capabilities": ("time_ranged",),
       "options": {
           "--ceph-namespace": "namespace of the Ceph cluster to read",
           "--cilium-namespace": "namespace of the Cilium agents",
           "--skip": "component to leave out; repeatable",
       },
       "required_options": (),
       "returns": (
           "One record per component (cni, mtu, controllers, ceph, storage, events), each ok, degraded "
           "or unknown with its evidence and read duration; PASS when all are ok."
       ),
   },
   ```

   `tools/render_manifest.py` turns it into the `tools.json` entry: it adds the universal options, renames
   `requires` to `requires_authorities` and `kind` to `report_kind`, and writes `mutates` as `read_only`,
   inverted. `schemas/skill-manifest.schema.json` must accept the new `tools.json` and
   `schemas/command-contract.schema.json` the new `--describe` output. Extend the catalogue's placement declarations
   for the relevant proposition nodes; the [proposition interface](#proposition-interface) defines their output.

5. **Thin main.** `ci-skills/bin/<name>.py` follows the exact
   [callback boilerplate](#callback-workflow-boilerplate): parse its arguments, construct actions and callbacks,
   connect their results, run `Executor`, and emit through the shared adapter, with the shared locator import.

6. **Strip the project.** Resolve endpoints and credential sources through the existing target contract.
   Pass operation inputs such as namespace, image name, robot name, build paths, and timeouts through the workflow's
   arguments or selected specification. Read observable resource state from the provider.

7. **Mutations.** Plan by default with `plan_digest`, `--apply --confirm-plan
   DIGEST`, read before and after, independent read-back, cleanup on every
   exit, idempotent second run. The plan, apply and read-back skeleton is
   extracted once from `core/gitlab_actions.py` into `core/action.py`, so
   Harbor and toolbox actions reuse it.

8. **Concurrency.** Independent reads run through the executor pattern
   `core/collect.py` already uses, with a declared worker bound and per-call
   timeout, and each read's duration is recorded beside the wall time; the
   row names which serial source steps become parallel. A loop that runs the
   same `kubectl` or `oc` command once per object is a defect, not a tool.
   Threads, never `async`: an `async` contract is added only when a tool must
   hold many open streams at once, as a separate contract (`software-design.md`
   at the pinned standards revision `56a579c`, "Synchronous and Asynchronous
   Contracts"); no such tool exists in this catalogue.

9. **Tests.** Mocked external commands through the `conftest.py` fixtures;
   the pinned mutating matrix for every `apply`; a receipt kind in
   `tests/acceptance/expected.toml` once the live target is declared there.

10. **Docs.** The catalog entry renders `tools.json`; one routing row in
    `SKILL.md`; the row here carries source, destination, adaptation and
    receipt kind. The source repo is not edited.

## Target access and workflow inputs

`core/endpoints.py` owns target keys; new target entries contain endpoint and access information only. The proposed
Harbor entries below use nonsecret values and credential file references, mode 0600, never credential values in a
report. Comments identify the existing source inventory. Values in angle brackets are selected by the caller.

```toml
[harbor]
url = "https://harbor.example.test"      # harbor_manager.sh:19,93; constant.bash:71; toolbox.yaml:10
project = "<project>"                    # harbor_manager.sh:20; toolbox.yaml:10
credential_file = "<0600 file>"          # {username, password} JSON or dockerconfigjson; replaces the
                                         # env pair harbor_manager.sh:64,131 and bindings.bash:63-64
admin_credential_file = "<0600 file>"    # robots and projects only; no source, built to the sentence
```

Retain the following source inputs as workflow arguments or fields in a caller-selected build/operation specification,
not as `target.toml` keys. The source defaults below remain proposed inputs for their delivery rows.

| Workflow inputs | Existing source or proposed value |
| --- | --- |
| Anonymous read selection; proxy exclusions | `harbor_manager.sh:115-134,148`; `anonymous_read = false`, caller-selected `no_proxy`. |
| Timeout, attempts, backoff | `rendered_images.bash:47-49`; 30 seconds, 3 attempts, 2 seconds. |
| Parallel copies; page size | `push_helm.sh:29-30`, `harbor_manager.sh:161,178`; 8 and 100. |
| Build spec, source repository, context | `toolbox_image.bash:22,182-185`; caller-selected paths. |
| Containerfile, destination repository and tag | `toolbox.yaml:10,32,36`; caller-selected build inputs. |
| Build namespace, BuildConfig and push Secret | `toolbox.yaml:34-37`; caller-selected names. |
| Successful and failed build history | `toolbox.yaml:38-39`; 3 each. |
| Completion and cleanup deadlines | `toolbox.yaml:40-41`; 1800 and 600 seconds; `--timeout` maps to the build deadlines. |
| Platform and read-back command | `toolbox.yaml:52`, `toolbox_image.bash:740-743`; selected OS/architecture and image doctor command. |
| Latest alias and source TLS verification | `toolbox_image.bash:639,680`; both true in the proposed port. |
| Builder selection | Explicit `openshift` or `podman`, selected by the workflow's declared `--builder` option. |

## One query grammar for every object class

One grammar for every object class (job, pipeline, runner, merge request,
schedule, route, node, machine, Pod, claim):

- `list`: the bounded dump. Filters are the catalog tiers, reused, never
  re-declared: `--status` (repeatable; object-specific values plus derived
  ones such as `stuck`), the `time_ranged` tier (`--last 2h`, `--from`,
  `--to`), the `filters_records` tier (`--search` substring over name, stage,
  ref, description, tags), `--name-glob GLOB` (shell-style match on the
  object's name, for example `--name-glob 'build-*'` to match each job of the
  last pipeline; `--name` stays the created object's exact name), `--limit N`
  (default 50). Pages are read in parallel up to the limit; `record_count` and
  `truncated` (both fields of `command-result`, CI07-SCHEMA) are always
  reported.
- `get`: one object by id or URL, with its related objects (a job with its
  pipeline and runner; a pipeline with its jobs and bridges; a runner with its
  projects and managers).
- `watch`: poll until every object in scope settles, with `--interval` and
  `--timeout`; for pipelines the Pipeline watch combo (Combos) is the single
  specification.
- `logs`: the bounded text of the object (job trace) with `--lines`,
  `--search` and `--failure-window`.
- actions (`start`, `play`, `retry`, `cancel`, `create`, `update`, `assign`,
  `delete`): step 7 of the recipe.

The requests we recorded, in this grammar: `gitlab_job.py list --project P
--status failed --limit 1` (last failed job); `gitlab_pipeline.py list
--project P --limit 1 --name-glob 'deploy-*'` (last pipeline, each job
matching the glob); `gitlab_runner.py list --project P` (the runner quick
view; fields and the executor read are in its interface bullet).

## Combos

A combo is one workflow main composing reusable atomic callbacks through the
[callback boilerplate](#callback-workflow-boilerplate). It calls shared Python operations rather than subprocesses
of other entrypoints, and each step keeps its own plan, apply and read-back. The `Pattern` column of the
catalogue names each tool's README grouping ("Tool grouping"); the
concurrency rule in step 8 applies to independent reads inside those operations.

### Concrete combinations: proposed delivery entries

These are proposed delivery entries, not claims that the commands already exist. Each result follows
CI09-REFERENCE section 3: a compact summary, the essential fields of each step, and `retrieve` pointers to the
component reports, never the reports inline.

- **Pipeline watch** (`gitlab_pipeline.py watch`; the exact interface, result and schema follow this list).
  - Operations combined: resolve the selected pipeline by ID; discover its jobs,
    bridges and linked downstream pipelines, across projects; watch the declared scope, optionally widened to
    newer pipelines in the same project whose name matches a declared pattern (pipelines a job started through
    the API carry no bridge); report changes and final results. `manual` counts as settled; interval and
    overall wait are bounded.
  - Completion evidence: every pipeline in scope is accounted for; incomplete reads remain explicit; completion
    and success are reported separately.
- **Toolbox build, publish and configure a runner.**
  - Operations combined: authenticate to OpenShift, build the toolbox, authenticate to Harbor and publish the image,
    authenticate to GitLab, configure the selected runner/CI to use the image, and verify; use the callback chain above.
  - Completion evidence: selected source revision, build identity, published image digest, Harbor read-back, and
    runner/CI use of that image agree. Reuse pipeline/job readers and watch when that verification needs them.
- **Milestone create, tag and MR check.**
  - Operations combined: create or resolve the milestone; apply the specified associations and labels to the
    selected work items; inspect the related MRs; verify their expected milestone/label associations.
  - Completion evidence: exact milestone and work-item identities, changes made, and per-MR checks. Here, "tag"
    is treated as a label; Git repository tags remain a separate, explicitly specified operation.

#### Pipeline watch, exact

```text
gitlab_pipeline.py watch --pipeline-id ID [--project PATH_OR_ID] [--related-name REGEX]
                         [--interval SECONDS] [--timeout SECONDS]   plus the universal tier
```

- `--pipeline-id`: required. `--project`: as `get`, default `gitlab.project` in the target. `--interval`: seconds
  between polls, default 10 (the source's cap, `gitlab_util.sh:62`). `--timeout`: seconds for the whole watch,
  default 3600. `get` keeps today's bare invocation, `gitlab_pipeline.py --pipeline-id ID`, as its default verb.
- Scope: the pipeline, every pipeline its bridges started (any project, depth at most 5), and with `--related-name`
  each newer pipeline in the root's project on the root's ref whose `name` matches REGEX. Settled: `success`,
  `failed`, `canceled`, `skipped`, `manual`.
- Result: the report envelope, with the access evidence every GitLab command emits (`access`, `execution_host`,
  `tested_revision`, `skill`, `target_source`; `collection_probes` under `--dry-run`), plus `complete` (every
  pipeline in scope settled and read), `success` (every one `success`), `polls`, `elapsed_seconds`, `changes`
  (pipeline status changes), `choices` (`retrieve` pointers in the shape of CI09-REFERENCE section 3: failed jobs
  first, then unsuccessful pipelines) and `continuation` (`null`; a watch result has no next page). One record per
  pipeline: ids, `via`, `depth`, `status`, job counts by status; never the jobs themselves.
- Values: `via` is `root`, `related` or `bridge:<bridge job name>`; a choice id is `job:<job id>` or
  `pipeline:<pipeline id>`; a job choice's summary is `failed: <failure reason>, <job name>`, a pipeline choice's
  `every job of pipeline <id> by stage`.
- Limits: 50 pipelines in scope, 50 changes, 12 choices. Past 50 pipelines, `errors` carries `scope_limit_exceeded`
  with its `count` and `complete` is false. Past 50 changes or 12 choices, `errors` carries `changes_limit_exceeded`
  or `choices_limit_exceeded` with its `count`, `complete` keeps its meaning, and the narrowing call for any record
  without a choice is `gitlab_pipeline.py get --project <project_id> --pipeline-id <pipeline_id>`. Each settled
  pipeline or job is one stderr log line while the watch runs.
- Status: `PASS` when complete and successful; `PARTIAL` otherwise; `BLOCKED` when access or the root read fails;
  `DRY_RUN` under `--dry-run`, with `collection_probes` and no records.
- Human view: the status with `complete` and `success`; the target, project, pipeline, filters, polls, elapsed time
  and capture time; a "Pipelines" block, one line per record; a "Changes" block; a "Results" block drawn as
  CI09-REFERENCE section 3 draws choices; an "Errors" block when there are any.

```text
$ gitlab_pipeline.py watch --pipeline-id 4101 --related-name '^component '
PARTIAL  complete: yes  success: no
https://gitlab.example.test  project group/project  pipeline 4101  related '^component '  25 polls  742 s  captured 2026-10-07T09:40:00+00:00

Pipelines
  4101  failed   root           depth 0  project 12  9 jobs: failed 1, success 8
  4102  success  bridge:static  depth 1  project 12  25 jobs: success 25
  4110  success  related        depth 0  project 12  4 jobs: success 4

Changes
   300 s  4102  success
   610 s  4110  success
   742 s  4101  failed

Results
  job:88231      failed: script_failure, deploy:component
                 Retrieve: gitlab_job.py --job-url https://gitlab.example.test/group/project/-/jobs/88231

  pipeline:4101  every job of pipeline 4101 by stage
                 Retrieve: gitlab_pipeline.py get --project 12 --pipeline-id 4101
```

```json
{
  "schema_version": "1.0",
  "kind": "gitlab_pipeline_watch",
  "captured_at": "2026-10-07T09:40:00+00:00",
  "target": "https://gitlab.example.test",
  "filters": {"project": "group/project", "pipeline_id": 4101, "related_name": "^component "},
  "status": "PARTIAL",
  "complete": true,
  "success": false,
  "polls": 25,
  "elapsed_seconds": 742,
  "records": [
    {"project_id": 12, "pipeline_id": 4101, "via": "root", "depth": 0, "status": "failed", "jobs": {"total": 9, "by_status": {"failed": 1, "success": 8}}},
    {"project_id": 12, "pipeline_id": 4102, "via": "bridge:static", "depth": 1, "status": "success", "jobs": {"total": 25, "by_status": {"success": 25}}},
    {"project_id": 12, "pipeline_id": 4110, "via": "related", "depth": 0, "status": "success", "jobs": {"total": 4, "by_status": {"success": 4}}}
  ],
  "changes": [
    {"at_seconds": 300, "pipeline_id": 4102, "status": "success"},
    {"at_seconds": 610, "pipeline_id": 4110, "status": "success"},
    {"at_seconds": 742, "pipeline_id": 4101, "status": "failed"}
  ],
  "choices": [
    {"id": "job:88231", "kind": "retrieve", "summary": "failed: script_failure, deploy:component",
     "next": {"action": "retrieve", "entrypoint": "bin/gitlab_job.py", "args": ["--job-url", "https://gitlab.example.test/group/project/-/jobs/88231", "--json"]}},
    {"id": "pipeline:4101", "kind": "retrieve", "summary": "every job of pipeline 4101 by stage",
     "next": {"action": "retrieve", "entrypoint": "bin/gitlab_pipeline.py", "args": ["get", "--project", "12", "--pipeline-id", "4101", "--json"]}}
  ],
  "continuation": null,
  "errors": [],
  "summary": {"record_count": 3, "error_count": 0}
}
```

`schemas/gitlab-pipeline-watch.schema.json`, which reuses the choice and pointer of
`schemas/reference-next.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://github.com/spyroot/ci-skills/schemas/gitlab-pipeline-watch.schema.json",
  "title": "gitlab_pipeline_watch",
  "description": "Result of ci-skills/bin/gitlab_pipeline.py watch (CI11-TOOLS, Combos, Pipeline watch): one record per pipeline in scope, completion and success reported separately, retrieve pointers to details.",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "kind",
    "captured_at",
    "target",
    "filters",
    "status",
    "complete",
    "success",
    "polls",
    "elapsed_seconds",
    "records",
    "changes",
    "choices",
    "continuation",
    "errors",
    "summary"
  ],
  "properties": {
    "schema_version": {
      "type": "string",
      "pattern": "^1\\.[0-9]+$"
    },
    "kind": {
      "const": "gitlab_pipeline_watch"
    },
    "captured_at": {
      "type": "string",
      "minLength": 1
    },
    "target": {
      "type": "string",
      "minLength": 1
    },
    "filters": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "project",
        "pipeline_id"
      ],
      "properties": {
        "project": {
          "type": [
            "string",
            "integer"
          ]
        },
        "pipeline_id": {
          "type": "integer",
          "minimum": 1
        },
        "related_name": {
          "type": "string",
          "minLength": 1
        }
      }
    },
    "status": {
      "enum": [
        "PASS",
        "PARTIAL",
        "BLOCKED",
        "DRY_RUN"
      ]
    },
    "complete": {
      "type": "boolean"
    },
    "success": {
      "type": "boolean"
    },
    "polls": {
      "type": "integer",
      "minimum": 0
    },
    "elapsed_seconds": {
      "type": "number",
      "minimum": 0
    },
    "records": {
      "type": "array",
      "maxItems": 50,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "project_id",
          "pipeline_id",
          "via",
          "depth",
          "status",
          "jobs"
        ],
        "properties": {
          "project_id": {
            "type": "integer",
            "minimum": 1
          },
          "pipeline_id": {
            "type": "integer",
            "minimum": 1
          },
          "via": {
            "type": "string",
            "pattern": "^(root|related|bridge:.+)$"
          },
          "depth": {
            "type": "integer",
            "minimum": 0,
            "maximum": 5
          },
          "status": {
            "type": "string",
            "minLength": 1
          },
          "jobs": {
            "type": "object",
            "additionalProperties": false,
            "required": [
              "total",
              "by_status"
            ],
            "properties": {
              "total": {
                "type": "integer",
                "minimum": 0
              },
              "by_status": {
                "type": "object",
                "propertyNames": {
                  "pattern": "^[a-z_]+$"
                },
                "additionalProperties": {
                  "type": "integer",
                  "minimum": 0
                }
              }
            }
          }
        }
      }
    },
    "changes": {
      "type": "array",
      "maxItems": 50,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "at_seconds",
          "pipeline_id",
          "status"
        ],
        "properties": {
          "at_seconds": {
            "type": "number",
            "minimum": 0
          },
          "pipeline_id": {
            "type": "integer",
            "minimum": 1
          },
          "status": {
            "type": "string",
            "minLength": 1
          }
        }
      }
    },
    "choices": {
      "type": "array",
      "maxItems": 12,
      "items": {
        "$ref": "reference-next.schema.json#/$defs/choice"
      }
    },
    "continuation": {
      "oneOf": [
        {
          "type": "null"
        },
        {
          "$ref": "reference-next.schema.json#/$defs/pointer"
        }
      ]
    },
    "errors": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "source",
          "reason"
        ],
        "properties": {
          "source": {
            "type": "string",
            "minLength": 1
          },
          "reason": {
            "type": "string",
            "minLength": 1
          },
          "count": {
            "type": "integer",
            "minimum": 1
          }
        }
      }
    },
    "summary": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "record_count",
        "error_count"
      ],
      "properties": {
        "record_count": {
          "type": "integer",
          "minimum": 0
        },
        "error_count": {
          "type": "integer",
          "minimum": 0
        }
      }
    },
    "access": {
      "type": "object"
    },
    "execution_host": {
      "type": "string",
      "minLength": 1
    },
    "tested_revision": {
      "type": [
        "string",
        "null"
      ]
    },
    "skill": {
      "type": "object"
    },
    "target_source": {
      "type": "string",
      "minLength": 1
    },
    "collection_probes": {
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1
      }
    }
  }
}
```

### Two combos we named, concretely

- **`cluster_health.py`, "is the cluster ok" in one go.** One record per
  component, each `ok`, `degraded` or `unknown` with the evidence that says
  so, collected concurrently by calling the library functions the existing
  tools already use (`core/cilium.py`, `core/mtu_consistency.py`,
  `core/ceph_cluster.py`, `core/collect.py`), never by running twenty
  commands and parsing them:

  | Component | Evidence read | `ok` when |
  | --- | --- | --- |
  | cni | Cilium agents ready, non-TTY `cilium-health` per agent | every agent answers with `local` and `nodes` |
  | mtu | PCI uplink MTUs read through the existing agent Pod exec (host network), no debug Pod | one MTU across nodes |
  | controllers | degraded cluster operators; deployments with unavailable replicas | both lists empty |
  | ceph | `ceph status` health and inactive PGs through the operator Pod | `HEALTH_OK`, no inactive PG |
  | storage | pending or lost claims, unattached volumes | none |
  | events | `Warning` events in the last window (`--last`) | count below the declared bound |

  `status` is `PASS` when every component is `ok`, `PARTIAL` with
  `access_proven: true` otherwise; a component that could not be read is
  `unknown`, never coerced. The MTU read through the agent Pod is proved by
  the smoke on the declared cluster before it replaces the debug-Pod path;
  until then the component reports `unknown` with the `--apply` route named.
- **`ci_failure_trace.py`, "the last failed job, then its interval".** Read
  the last failed job of the project or pipeline (`gitlab_job.py list --status failed --limit 1`),
  take its `started_at` and `finished_at`, widen by `--margin`, read the
  cluster events in that window (`event_trace` with `--from` and `--to`, the
  pair `SKILL.md` section 2 prefers over an RFC3339 window computed by hand), and correlate by
  the job's runner node and Pod. One record: the job (id, name, stage,
  status, interval, runner), the window, the events in it, and the
  correlated subset. This is the README's "CI Combo" in one command.

## Smoke cases: fixed arguments and the read-back that proves each tool

One `[[smoke_cases]]` entry per tool in `tests/acceptance/expected.toml`
(CI06-TESTS, "Live smoke"); `<declared ...>` values are written there before
the smoke, never chosen by a tool; each case runs with
`--receipt-out tests/acceptance/receipts/<tool>-<case>.json` (CI02-CLI, item 7)
and every receipt is committed.

- `gitlab_job.py get --job-url <declared job>`: read-back `records[0].id`
  equals the declared id, plus `pipeline.id`, `runner.id`, the trace tail.
- `gitlab_job.py logs --job-url <declared job> --lines 200`: the trace
  contains the declared marker line; `lines <= 200`.
- `gitlab_job.py list --project <declared project> --status failed --last 7d --limit 5`:
  the declared failing job's id, name, stage, `started_at` and `finished_at`
  among the records; `--status stuck --stuck-after 600` returns the empty
  bounded dump with `record_count: 0`.
- `gitlab_pipeline.py start --project <declared project> --ref <declared ref>`:
  plan, apply, read-back of the new pipeline id and `sha`; this pipeline runs
  one job that prints the marker line and one that fails on purpose.
- `gitlab_pipeline.py watch --project <declared project> --pipeline-id <from start>`:
  `complete: true`, `success: false` (the job that fails on purpose), the root
  record's `status`, and `changes` ending with the root's settled status.
- `gitlab_pipeline.py get`, `logs` and `children` on that pipeline: stage
  counts; the marker line in the job's trace; the bridge list (empty).
- `gitlab_pipeline.py list --project <declared project> --limit 1 --name-glob <declared glob>`:
  the newest pipeline with the jobs matching the glob.
- `gitlab_pipeline.py retry` then `cancel` on the declared pipeline: plan,
  apply, read-back of the status change.
- `gitlab_schedule.py create --project <declared project> --description <declared>
  --ref <declared> --cron <declared>`: plan, apply, GET read-back equal to the
  request, second apply `NO_OP`; `update`, `play`, `list` and `get` the same
  way.
- `gitlab_mr.py check --mr-url <declared merge request>`: read-back of
  `detailed_merge_status`, the head pipeline id and each failing job's name.
- `gitlab_runner.py list --project <declared project>` and
  `get --runner-id <declared> --managers`: the declared runner's tags,
  status, online and `runner_type`; the executor value or `unknown`.
- `gitlab_runner.py reset-token --runner-id <declared> --token-out <path>`:
  `sink_persisted` true, the token absent from the report; `delete` on a
  runner created for the smoke: 404 read-back.
- `gitlab_variable.py set --project <declared project> --key <declared>
  --value-file <path>` then `show --key <declared>`: the masked value reported
  present, protected and scope fields equal to the request, second apply
  `NO_OP`.
- `gitlab_milestone.py list --project <declared project> --state active`: the
  milestone the existing receipt created is listed by title.
- `ci_failure_trace.py --project <declared project>`: that job, its window,
  the events in it and the correlated subset.
- `ocp_route.py --namespace <declared namespace>`: the declared route host
  appears with its TLS and backend fields; probes recorded per route with
  durations.
- `ocp_ha.py`: control-plane node names and readiness, etcd member health,
  the list of degraded cluster operators (empty or named), machine config
  pool status; one duration per concurrent read.
- `ocp_machine.py --node-spec <declared path>`: each machine's match or
  mismatch against the spec, by field.
- `ocp_iso.py rhcos-report`: the bootimage stream's ISO URL and sha256;
  `node-build --node <declared>` then `node-serve`: the ISO file's sha256
  recorded, `HEAD` on the served URL returns 200, `delete` reads back absence;
  a second build with the same inputs `NO_OP`.
- `ocp_csr.py`: the pending CSR list as `PLANNED`; apply on a CSR created for
  the smoke reads back `Approved`.
- `k8s_verify_mtu_consistency.py` extended: the rendered MachineConfig's
  digest in the plan. Its apply needs a node pool declared for it in
  `tests/acceptance/expected.toml`; until one is declared, this row is not
  delivered (rule below).
- `k8s_state.py`: node and pod counts per declared namespace; per-read
  durations and wall time.
- `cluster_health.py --ceph-namespace <declared namespace>`: one line per
  component with its evidence (agent count, MTU value, degraded operator
  list, Ceph health string, pending claim count, warning event count) and a
  duration per component; `status` matches the live cluster on the day.
- `ceph_nfs.py`: the export list read back. `ceph_bench.py`: the plan; its
  apply writes and needs a pool declared for it in `expected.toml`, and until
  one is declared, this row is not delivered (rule below).
- `toolbox_build.py apply --tag <declared>`: the image digest reported by the
  build equals the digest read back from Harbor; second apply `NO_OP`.
- `harbor_push.py apply --image <declared> --tag <declared>`: artifact digest
  read back equals the pushed digest.
- `harbor_robot.py create --name <declared> --token-out <declared path>`:
  GET robot by name equals the request, `sink_persisted` true, the token is
  not in the report, second apply `NO_OP`.
- `harbor_sanity.py`: the declared project and repository are listed.
- `harbor_charts.py --versions <declared chart>`: the declared version listed.
- `harbor_pull_secret.py sync` on the declared namespace: the Secret's keys
  read back equal to the authfile; second apply `NO_OP`.

A tool without a committed receipt for its case is not delivered.

## Source rows: OpenShift and Kubernetes (inventory of 2026-10-06)

Inventory of 2026-10-06 at the source commit recorded with the source repo
root in the private source inventory; paths are relative to that
root and line numbers are from that commit; the full inventories are
[ci11-inventories](../plans/2026-10-06/ci11-inventories/index.md). "new" marks a read we asked for
that no source script performs; it is built to the sentence and nothing more.

### Cross-cutting adaptations for every port

- Transport and emission: step 3 of the recipe (`GlabAPIClient` for GitLab,
  `run_command_bounded` for `oc`, `kubectl`, `podman` and `skopeo`,
  `core/http.py` for Harbor; `report.emit` for every result).
- `--dry-run` changes meaning: the source's plan or `--dry-run` reads the
  cluster live (`scripts/ocp/nic-mtu.sh:19-23`, `csr-approve.sh:20-21`,
  `node-status.sh:16`, `serve-iso.sh:25-26`, `node-image.sh:119`,
  `get-rhcos-iso.sh:67`; `gitlab_util.sh:1586-1607` even POSTs a preview
  pipeline); here the offline default run is `DRY_RUN` (no API call) and the
  source's live read-only plan becomes `--live-plan` with status `PLANNED`
  (`docs/gitlab-toolbelt-plan.md`, "One result contract").
- Status and exit codes collapse to the one table: the source's `READY` maps
  to `PASS`, `WOULD_CHANGE` to `DRY_RUN` or `PLANNED`, source 1 to `PARTIAL`
  or `BLOCKED`, every failure class to `BLOCKED` with the class kept as
  `reason`, source 64 to 67 to an unusable input, a lost write to `PARTIAL`
  through `unverified_write`; signals reuse `core/mtu_consistency.py`
  `Interrupted` and `_signal_interrupt`.
- Removed on port: the source's environment-variable prefix, function prefix,
  label and receipt `apiVersion` namespace, `inventory/clusters/...` paths,
  `schemas/*-v1.schema.json` paths, its conda environment name, and every
  site value in `scripts/ocp/README.md` and `scripts/ocp/reference-node-spec.yaml`;
  the two-timer bound at `automation/lib/core/k8s_state_observer.bash:104,224-302`
  is replaced by `run_command_bounded`.

### `ocp_route.py`

- Source: `automation/lib/deploy/public_routes.bash:584` (Admitted plus ready
  backend per Route), `:690` (host under the wildcard, TLS Secret, trusted
  handshake), `:819` (public reachability and the served certificate),
  `:293-389` (one Route list bound to the run), `automation/lib/core/route_readiness.bash:29,45`
  (Admitted; ready EndpointSlice addresses), driver `scripts/deploy/public-routes.sh:158-233`.
- Port the two pure functions from `route_readiness.bash` first.
- Adapt: the source checks only Routes declared in a repo manifest
  (`public_routes.bash:47,58-93`); the view lists the cluster's Routes live
  (new read), joins EndpointSlices by service name (`:629-630`), reports
  Admitted per router (`route_readiness.bash:31-32`), TLS and
  `externalCertificate` (`:720,727-743`); the handshake uses Python `ssl`,
  not the conda wrapper at `:165`. The `apply`, `delete` and `absent` steps
  (`:264,451,424`) are not part of a view and stay out.
- Inputs: namespace, search and probe arguments above; route state is read live through the selected Kubernetes target.
- Concurrency: one `get routes -A` and one `get endpointslices -A` instead of
  the per-Route loop (`:600-654`); Secret reads, handshakes (`:710-801`) and
  public probes (`:827-853`) in parallel with a bounded pool.

### `ocp_ha.py`

- Source: `scripts/ocp/node-status.sh:32-42` (version, nodes, CSRs, pools in
  one run), `scripts/ocp/ocp_lib.bash:62` (cluster version), `:92` (node
  summary), `:248` (node to pool, IP, Ready), `:384` (pool counts and
  Updated), `automation/lib/core/cilium_health.bash:42,109,148,211,244`
  (agent rollout, identity drift, mesh unreachable, port-9962 counters,
  routing-device drift), `scripts/storage/sanity_check_ceph.sh:177` (health,
  OSD up and in, PGs, failure domains).
- Adapt: read pools with `-o json`, not the positional parse at
  `node-status.sh:41-42`; reuse `core/collect.py` `collect_cilium`,
  `core/cilium.py` `ready_agent_pods` and `core/ceph_cluster.py`
  `collect_ceph_cluster` as sub-sections instead of porting the Bash
  readers; cluster operators, etcd members and control-plane quorum are
  **new** reads (no source function; grep finds only comments at
  `nic_mtu.bash:138`, `ocp_lib.bash:242`).
- Concurrency: clusterversion, infrastructure, nodes, CSRs, MCPs, Cilium
  and Ceph reads in parallel; the cilium node and ciliumnode lists
  (`cilium_health.bash:112-113`) and the per-pod execs (`:255-270`) too.

### `ocp_machine.py`

- Source: `scripts/ocp/ocp_lib.bash:92,231,248,267` (nodes; the source lists
  them three times), `:362` (network type and cluster MTU), `:373,384,403`
  (MachineConfig, pool status, pools selecting a label set);
  `scripts/ocp/reference-node-spec.yaml:8-81` is site data, read by nothing.
- Adapt: list nodes once, pools once, MachineConfigs once; reuse
  `core/mtu_consistency.py` `physical_uplinks` (do not port `ocp_lib.bash:294`);
  node-local facts through the existing-Pod route (`core/node_pod.py`
  `select_node_pod`) so the view stays read-only; MachineSets, Machines and
  BareMetalHosts are **new** reads (the cluster is bare metal,
  `scripts/ocp/README.md:4-5`); the live-versus-reference comparator is
  **new**; the reference spec is supplied by the consuming project as
  `--node-spec PATH`, never shipped.
- Concurrency: per-node Pod reads in parallel.

### `ocp_iso.py`

- Source: `scripts/ocp/get-rhcos-iso.sh:46,64` (bootimage stream; ISO URL,
  sha256, download and verify), `scripts/ocp/serve-iso.sh:53,77` with
  `scripts/ocp/manifests/rhcos-iso-server.yaml:10-102` (serve the RHCOS ISO on
  a NodePort), `scripts/ocp/node-image.sh:87` (node ISO from the
  workstation through `oc adm node-image create`), `scripts/ocp/serve-node-iso.sh:208,229,403,442,477`
  with `scripts/ocp/node_iso.bash:60,108,135,187`,
  `scripts/ocp/templates/node-iso/{build.sh,nodes-config.jq,server.yq}` and
  `scripts/ocp/manifests/node-iso-server.yaml:16-136` (build and serve node
  ISOs in the cluster, delete, joiner read-back), `scripts/ocp/node_iso_proof.py:34`
  (proof; its library was not inventoried), `automation/lib/deploy/node_iso_smoke.bash:312`
  (the acceptance shape: plan, build, fetch, verify, cleanup).
- `core/ocp_iso.py` renders nodes-config and the server manifest in Python
  (replacing `nodes-config.jq:12-31`, `server.yq:15-19`).
- Adapt: every `--confirm-plan` names the namespace, the cluster-admin
  ClusterRoleBinding (`node-iso-server.yaml:36-40`), the pull-secret copy
  (`serve-node-iso.sh:208-213`), the 0600 pull-secret files on disk
  (`node-image.sh:99-111`, `ocp_lib.bash:194-207`, `serve-node-iso.sh:241-249`),
  the NodePort, the images and the rollout timeout; delete ports the
  UID-precondition delete and absence read-back
  (`automation/lib/core/k8s_lifecycle.bash:25,280`) plus the joiner read-back
  (`node_iso.bash:187`), and drops `serve-iso.sh:80-90`'s requirement that
  delete needs a Ready node.
- Inputs: caller-selected ISO workflow arguments or specification; cluster endpoint and access come from the target.
- Concurrency: in the plan, the serving node, the API URL (read three times
  in the source, `serve-node-iso.sh:506-531`), the version, the bootimage
  ConfigMap (read twice, `serve-iso.sh:80-81`), the release image and the
  pull secret in parallel; builds inside the pod stay serial
  (`build.sh:21-35`); one Pod per ISO is not added (no source).

### `k8s_state.py`

- Source: `automation/lib/core/k8s_state_observer.bash:224` (one bounded
  read), `:342,404,494` (classify Pod, workload, Job into ready, transient,
  terminal, unobservable with the terminal waiting reasons `:144-151`),
  `:542-604` (observe and decide), `:635,660` (ready pods by selector;
  workload selector), `automation/lib/core/k8s_lifecycle.bash:563` (namespace
  snapshot of Jobs, Pods, Builds, PVCs, BuildConfigs, CronJobs), `:1029,1211`
  (per-UID status, events and log digests), `automation/lib/deploy/storage_readback.bash:33`.
- The classifiers are ported as pure Python.
- Adapt: reuse `core/collect.py` `collect_storage` (covers
  `storage_readback.bash`) and `collect_events`; capabilities `namespaced`,
  `filters_records`, `time_ranged`.
- Concurrency: the namespace lists (`k8s_lifecycle.bash:568-618`, serial in
  the source), per-UID evidence (`:1062-1199`), claims and volumes
  (`storage_readback.bash:42,52`) in parallel.

### `cluster_health.py`

- Source: the readers above (`cilium_health.bash`, `sanity_check_ceph.sh:177,210,224`,
  `storage_readback.bash:33`, `k8s_state_observer.bash`), composed.
- `core/cluster_health.py` calls the collectors' library functions.
- Adapt: `sanity_check_ceph.sh` runs `ceph osd stat`, `ceph pg stat` and
  `ceph df` twice each (`:182-187,213-218`) and repeats them for its JSON
  (`:560-563`); take each read once; add `ceph df` to `core/ceph_cluster.py`.

### The remaining OpenShift and storage rows

- `scripts/ocp/csr-approve.sh:56,79-90` with `ocp_lib.bash:69,77,84`:
  `ocp_csr.py` (list pending as `PLANNED`; `--apply --confirm-plan` approves
  only the planned names and signers; one CSR list instead of a read per CSR).
- `scripts/ocp/nic-mtu.sh:157`, `scripts/ocp/nic_mtu.bash:494,749` and
  `scripts/ocp/templates/nic-mtu/*`: extend `core/mtu_consistency.py`; the
  MachineConfig rendering and the fingerprinted `oc create -f` apply become
  `core/nic_mtu_config.py`; a caller-selected NIC MTU specification replaces
  `inventory/clusters/<id>/nic-mtu.yaml` (`nic_mtu.bash:48`); `butane` is a
  required tool. Node links are already parallel (`nic_mtu.bash:260-291`).
- `scripts/storage/sanity_check_ceph.sh:265,512` (RADOS and fio benchmarks,
  which write by default, `:40,268-294`): `ceph_bench.py` behind
  `--apply --confirm-plan`; `scripts/storage/sanity_check_nfs.sh:149,181,314`:
  `ceph_nfs.py`, read-only by default (`:487-489`), write test behind apply.
- `automation/lib/core/k8s_lifecycle.bash` delete and cleanup (`:25,280,1279-1592`):
  an internal `core/k8s_lifecycle.py` used by ISO delete and temporary Pods,
  generalizing `core/mtu_consistency.py` `_marked_pods` and `_cleanup`.
- Not ported: `automation/lib/access/openshift_context.bash`,
  `in_cluster_kubeconfig.bash`, `scripts/ci/cluster-access.sh` (covered by
  the access protocol, `--binding` and `access_check.py`), `k8s_test_job.bash`,
  `scripts/ci/k8s-test.sh`, `node_iso_smoke.bash` (the source repo's CI
  tooling), `scripts/ceph_tool.py`'s SSH path (the node model here is an
  existing Pod) and its Intersight lookup (outside the authorities).

## Source rows: GitLab (inventory of 2026-10-06)

The source has three GitLab transports (the shared `glab api` wrapper at
`automation/lib/access/gitlab_auth.bash:195`, `curl` with the token on argv at
`automation/lib/ci/pipeline.bash:62-76`, and `curl` to the MCP endpoint at
`automation/lib/access/gitlab_mcp.bash:127`); every port uses the one
`core.gitlab_api.GlabAPIClient` (JSON bodies in a 0600 file, never argv). The
status mapping is in the cross-cutting adaptations above.

### `gitlab_job.py get`, `list`, `watch`, `logs`

- Source: job by name and id `automation/lib/ci/pipeline.bash:113-117`, job list
  (20 pages of 100, silently capped) `:94-108`, `GET jobs/<id>` `:140`; poll
  until terminal `:136-157` (incomplete status set; see Adapt); the driver
  `scripts/ci/pipeline.sh:129-139`. No source function reads a job trace: the
  failed job ids are printed and never fetched
  (`scripts/utility/gitlab_util.sh:2350-2352`).
- Already in `ci-skills`: `core/collect.py:783` `collect_gitlab_job` (job URL only;
  last 200 trace lines sanitized, `:853-860`), `TERMINAL_JOB_STATUSES` at
  `core/gitlab_pipelines.py:13`.
- Adapt: `list` and `get` by project and id or by pipeline and name glob;
  `watch` with one terminal set and `manual` ending the watch explicitly;
  `logs` reads the trace through a new bounded `GlabAPIClient.get_text`
  (today only `*_json`, `core/gitlab_api.py:283-294`), and `collect.py:853`'s
  trace read moves onto it; paginate everything (`pipeline.bash:98` caps silently).
- Concurrency: the trace read in `collect_gitlab_job` (`collect.py:853`) joins
  the existing pool (`:820`); list pages in parallel.

### `gitlab_pipeline.py get`, `list`, `watch`, `logs`, `children`, `start`

- Source: pipeline GET with id, ref and sha checks
  `scripts/utility/gitlab_util.sh:1674-1680`; bridges to the child pipeline
  `:1701-1717`; poll until terminal `:1666-1694` (interval at most 10 s,
  `:62`; incomplete status set, see Adapt);
  newest pipeline on a ref `scripts/ci/pipeline.sh:118-125`; newest pipeline at
  an exact sha `automation/lib/access/gitlab_merge_requests.bash:82-91`; the
  report `automation/lib/ci/pipeline.bash:162-185` (repo-specific component
  matrix, not ported); job artifact fetch `gitlab_util.sh:1731-1734`; pipeline
  create with variables `automation/lib/access/gitlab_pipeline.bash:27-64`
  (the POST is wrapped in `timeout` and a lost write is not detected) and the
  drivers `gitlab_util.sh:1796-1821,1468-1630,1832-1933`; protected-branch
  exact head `gitlab_util.sh:1637-1650`.
- Already in `ci-skills`: `core/gitlab_pipelines.py:66` `read_pipeline` (up to 500
  jobs, PARTIAL when truncated).
- Adapt: `list` with the grammar above and `--name-glob GLOB` over jobs;
  `children` reads bridges and downstream pipelines; `logs` fans out the
  failed jobs' traces; `start` is a new mutating kind in `core/gitlab_actions.py`
  with the flags of its interface bullet (the source POSTs a preview pipeline
  even under `--dry-run`, `gitlab_util.sh:1586-1607`, which this skill's
  `--dry-run` contract forbids); `retry` and `cancel` have no source and are
  built to the README sentence only; no default ref in config: read the
  project's `default_branch` when `--ref` is omitted (the source bakes in
  `main` at `pipeline.sh:119`, `gitlab_util.sh:1470,1833,1898`).
- Concurrency: jobs and bridges reads (`gitlab_util.sh:1695,1701`); the
  pre-reads before a start (`:1573,1579,1591`); failed-job traces.

### `gitlab_schedule.py list`, `get`, `play`, `create`, `update`

- Source: none. The only match for "schedul" in the source is a job-status
  enum (`automation/lib/access/gitlab_merge_requests.bash:126`). Built to the
  sentence we recorded: reads through `GlabAPIClient.get_json`, paginated;
  mutations as a `gitlab_actions` kind with an offline plan digest and GET
  read-back (`GET …/:sid/pipelines` after `play`). Endpoints are the GitLab
  REST pipeline-schedule endpoints, verified by the smoke on the declared
  host before the row is marked delivered.

### `gitlab_mr.py check`

- Source: `automation/lib/access/gitlab_merge_requests.bash:34-152` (MR, newest
  pipeline at the exact head, all current jobs, failed jobs, draft and merge
  state; sanitized JSON) and its driver `scripts/utility/gitlab_util.sh:2276-2388`;
  tests `tests/gitlab_merge_requests.bats:63-188`.
- Keep: newest-at-exact-head selection (`:82-87,134-135`), the job-status
  validation (`:124-128`), failed or canceled jobs blocking (`:142-144`).
  Drop the scratch directory and trap (`:47-57`). Add the failing jobs' bounded
  traces (the source never fetches them).
- Concurrency: the MR read and the MR pipelines read (`:58-68`); traces.

### `gitlab_runner.py list`, `get`, `delete`, `reset-token` (`assign` and `create` exist)

- Source: runner GET with 3 attempts and Retry-After
  `automation/lib/access/gitlab_runners.bash:340-371` (the transport already
  retries, `core/gitlab_api.py:25-26`; not ported); runners by description
  `:156-165,389-401` (at most one match); DELETE then 404 read-back
  `:182-198` and its driver `scripts/utility/gitlab_util.sh:939-1034`;
  register `:217-258` and reset token `:498-500`; group assign `:96-136` and
  `scripts/utility/gitlab_runner_assign.sh:42-62` (already `gitlab_runner.py
  assign`); executor deployment into the cluster `:424-567` and
  `gitlab_util.sh:1067-1202` (not ported: the source repo's runner).
- Adapt: `list` and `get` are new reads built to the sentence we recorded
  (fields and the GraphQL executor read: the interface bullet);
  `delete` selects by `--runner-id` or an exact `--description` refused on
  more than one match, reads back 404; `reset-token` with
  `--token-out`; replace `glab repo list` and `glab runner assign` text
  matching (`gitlab_runners.bash:48-51,66-72`) with REST through the client;
  `[gitlab] group` in the target replaces the hardcoded group default.
- Concurrency: the group list and the runner's projects (`:104,127`); per-project
  assigns as a bounded fan-out (`:106-127`).

### `gitlab_variable.py show`, `set`, `set-secret` (project and group CI variables)

- Source: `automation/lib/access/gitlab_project_variables.bash:19-135` (PUT if
  the variable exists else POST, masked retry only on a mask-eligibility
  error `:77-100`, metadata contract `:115-123`, sha256 compare `:129-135`)
  and `scripts/utility/gitlab_util.sh:505-595,1243-1437`.
- Adapt: values only from `--value-file` (rule at `gitlab_util.sh:1374-1379`);
  masked values reported as present or absent; `protected`, `raw`,
  `environment_scope` and type become flags (`:62-70`); the cluster-admin
  check (`:694-709`) belongs to the Kubernetes authority; `get-cluster`
  (`:807-885`, marked temporary, writes a secret copy) is not ported.
- Concurrency: the per-name reads of `show` (`gitlab_util.sh:1265-1310`).

### `gitlab_milestone.py list` and `gitlab_issue.py` (exist; additions)

- Source: milestone list `automation/lib/access/gitlab_issues.bash:481-515`
  (one page), create `:531-600`, issue open `:397-467`, exact-title lookups
  `:263-310` (one page each), the drivers `gitlab_util.sh:2076-2269`.
- Adapt: add `list --state` (read-only; `mutates` is declared per verb,
  CI10-PHASES Modify); resolve `--milestone TITLE`
  (`gitlab_util.sh:2032-2065`); paginate the lookups.

### Not ported from the GitLab utilities, with the reason

- `scripts/utility/gitlab_auth.sh` `login` and `exec` (keyring write;
  arbitrary command escape hatch), `check` is `gitlab_access.py check`.
- `scripts/utility/gitlab_mcp.sh` and `automation/lib/access/gitlab_mcp.bash`
  (a third transport; the source README says `glab` covers everything).
- `scripts/ci/render-pipeline.sh`, `automation/lib/ci/render_pipeline.bash`,
  `automation/lib/access/ci_inputs.bash`, `automation/lib/ci/parent_static.bash`,
  `automation/lib/debugger/gitlab_env_state.bash`, `scripts/ci/{cluster-access,deployment-gate,k8s-test,static-gate}.sh`
  (the source repo's CI contracts and adapters).
- The source README's policies (secrets never on argv, exact-title match,
  read-back as the evidence) are already this skill's: `docs/gitlab-toolbelt-plan.md`.

## Source rows: toolbox and Harbor (inventory of 2026-10-06)

The source has no Harbor REST mutation at all: in the scripts and libraries
read there is no POST, PUT, PATCH or DELETE on `/api/v2.0`, no robot, retention, replication,
quota or webhook call. Reads: `GET /api/v2.0/projects/{p}/repositories`,
`GET …/repositories/{r}/artifacts?with_tag=true` (`scripts/harbor_manager.sh:161,177-178`,
no paging past 100), `GET /api/v2.0/users/current` and `GET /api/v2.0/projects`
(`automation/lib/access/registry_sanity.bash:194-200`, password on `curl -u`
argv). Pushes go through `skopeo` and `helm` over the OCI transport.

### `toolbox_build.py plan`, `apply`

- Source: `automation/lib/access/toolbox_image.bash` (1,126 lines): config from
  the spec with env overrides `:63-159`; build context copied from the working
  tree, not a commit `:179-186`; BuildConfig create or patch `:195-270` (Binary
  source, Docker strategy, output ImageStreamTag `<app>:latest`, no read-back
  of the patched fields); ImageStream heal that deletes and recreates
  persistent objects `:272-326`; `oc start-build --from-dir` with a run-id env
  `:562-634`; Build resolved by run-id, labelled, read by UID, finalized
  (cancel, logs, evidence, delete by UID) `:336-553`; push through an
  in-cluster `skopeo copy` Job with `--src-tls-verify=false` and a `:latest`
  alias `:636-706,838-859` (Job exit only, no digest comparison); read-back by
  running the image's doctor `:708-745,868-891`; orchestration `:958-1126`
  (`--apply` mutates with no confirm). Thin wrapper `scripts/toolbox/toolbox-image.sh`,
  `Makefile:177-184`, spec `platforms/component/toolbox/toolbox.yaml`,
  recipe `platforms/component/toolbox/Containerfile`, in-image read-back
  `platforms/component/toolbox/bin/toolbox-doctor:94-198`; tests
  `tests/toolbox_image.bats` (12), `tests/toolbox_contract.bats` (13).
- Already in `ci-skills`: `lib/bash/automation/binary_build.bash:72-145`
  (`ci-binary-build`: validates a BuildConfig against one exact Git tree,
  requires output kind `DockerImage` and a commit label, never contacts
  OpenShift, "apply is not implemented"). It is superseded by
  `core/binary_build.py` (CI10-PHASES, Refactor); `toolbox_build.py` builds on
  that port.
- Adapt: build from `git archive` of the commit (`binary_build.bash:124-141`),
  not the working tree; output `DockerImage` with `pushSecret` so the heal
  step, the mirror Job and the disabled TLS check disappear; push by digest
  and read Harbor's digest back (the pattern at
  `automation/lib/access/bootstrap_runner_images.bash:46-57`); the ImageStream heal is dropped
  (a `DockerImage` output needs none); `podman` builder: no source,
  built to the sentence; `--timeout` maps to `completionDeadlineSeconds` and
  `cleanupTimeoutSeconds` (`toolbox.yaml:40-41`).
- Inputs: the toolbox workflow inputs under [Target access and workflow inputs](#target-access-and-workflow-inputs),
  each with its source; endpoint and credential resolution stays in the target contract.
- Concurrency: the two preflight reads (`:1077-1080`) and the context copy
  beside the BuildConfig calls (`:1083-1090`); build, push, read-back stay
  serial (`:1091-1106`); version then `:latest` stays serial (`:685-693`).

### `harbor_push.py plan`, `apply` and the generic mirror

- Source: digest-pinned plan, copy and read-back for one image
  `automation/lib/access/bootstrap_runner_images.bash:22-66` (`skopeo inspect`
  digest, `skopeo copy --all --preserve-digests --authfile`, re-read must
  equal the source digest, plan file with `source_commit`); the GCR to Harbor
  mirror loop `automation/lib/access/registry_sanity.bash:416-476` (digest
  compared per image); the adapters `scripts/supply-chain/push_images.sh:16-77`
  and `push_helm.sh:16-74` (plan by default, `--apply --confirm-*-mirror`,
  parallel 8, per-read 300 s, per-copy 1800 s; their libraries were not read,
  so the port is built from `bootstrap_runner_images.bash:22-66` and the
  adapters' flags; see Open decisions).
- Adapt: one `core/registry_mirror.py` with `mirror_one` (`:22-66`) used by
  `harbor_push.py`, the toolbox push and the runner images; authfiles 0600
  passed by path, never `--creds`; two path conventions in the source
  (`registry_sanity.bash:434-436` versus the nested form) become one
  declared mapping.
- Concurrency: already parallel in the source (`push_*` parallel 8;
  `bootstrap_runner_images.bash:136-148`); the mirror loop at
  `registry_sanity.bash:427-472` becomes a bounded pool.

### `harbor_robot.py create` (no source)

- Source: none. The admin credential exists only as a declared CI secret
  (`automation/lib/access/constant.bash:17`) and a reader with no caller
  (`automation/lib/access/bindings.bash:65`).
- Built to the sentence we recorded: `POST /api/v2.0/robots` with declared
  permissions, the one-time secret written straight to the 0600 `--token-out`
  file (the runner-create pattern already in `ci-skills`), `GET /robots/{id}`
  read-back, second apply `NO_OP`; the admin credential only from
  `[harbor] admin_credential_file`; reports carry name, id, permissions,
  expiry and a sha256 stamp, never the value (`scripts/harbor_manager.sh:377-381`
  stamp-not-value practice).

### `harbor_sanity.py`

- Source: `automation/lib/access/registry_sanity.bash:178-232` (identity
  read-back, project count, `helm registry login` and `skopeo login` with
  `--password-stdin`) and `scripts/harbor_manager.sh:92-151` (Basic auth through
  a 0600 header file, anonymous fallback, curl 22 mapped to blocked);
  registry answer classification `automation/lib/core/registry_answer.bash:23-70`;
  bounded `skopeo` read `automation/lib/core/rendered_images.bash:332-364`.
- Adapt: Harbor part only (the GCR and vendor-Helm checks and the GitLab
  issue posting at `:234-414,478-605` stay out); credentials from
  `[harbor] credential_file`, the header built in process by `core/http.py`,
  no header file on disk; page every list; `--max-time` on every call (the source has
  none).
- Concurrency: the three probes (`:194,210,222`) and the index downloads
  (`:136,146`).

### `harbor_charts.py` (read-only chart inspection, from `scripts/harbor_manager.sh`)

- Source: `scripts/harbor_manager.sh:159-636` (search, versions, pull,
  extract, grep, secret fields, compare; `helm pull oci://`); tests
  `tests/harbor_manager.bats` (11). Adapt: paging, timeouts, explicit
  `--blocks PATH` (the chart block list) and `--stamps PATH` (the sha256 stamp
  file) instead of the source repo's files (`:452-453`), `--all` as a bounded
  pool (`:579-589`).

### `[harbor]` keys

The endpoint and credential-source keys and their source lines are under
[Target access and workflow inputs](#target-access-and-workflow-inputs). Operation settings remain workflow inputs;
credential values stay out of the target, as with `token_file` today.

### Not ported from the toolbox and Harbor side, with the reason

- `automation/lib/access/toolbox_auth_smoke.bash` (vendor Helm chart and GCR
  checks; only the Harbor `skopeo inspect` at `:214-218` is generic).
- `automation/lib/access/bootstrap_readiness.bash`, `bootstrap_access.bash`,
  `runtime_secret_sync.bash` beyond the Harbor pull-secret part (`:219-234,609-734`
  become `core/harbor_pull_secret.py`: authfile, Secret create or apply,
  key-by-key read-back), `secret_util.bash`, `bindings.bash`, `constant.bash`
  (the source repo's CI bootstrap).
- `scripts/refresh_inventory.sh` and the lab inventory Python tools (hardware
  inventory, not a Harbor or toolbox task).

## Gates

- Static: per CI10-PHASES, "How a phase lands", plus the catalog contract
  test for each new command (`tests/python/test_catalog.py`).
- Tests: the files named in "Delivery, test and proof"; run on the gate route
  (D-GATE).
- Live: the smoke case of each row; a tool without a committed receipt for
  its case is not delivered.

## Read-back

After each capability lands: its relevant proposition lists the installed tool and scoped reference pointers;
the tool's native `--help` exposes its arguments and `<tool> --describe` validates against `command-contract`;
`tools/render_manifest.py --check` reports CURRENT; the neutrality checker
reports PASS.

## Delivery, test and proof

1. *Delivery*, per tool: `ci-skills/bin/<tool>.py`, the Library module cell
   under `ci-skills/lib/python/core/`, the catalog entry and the regenerated
   `tools.json`, any endpoint/access additions to `target.toml.template` and its workflow inputs, its
   `[[smoke_cases]]` entry; the command that runs is the tool's smoke line.

2. *Tests*: `tests/python/test_<module>.py` per Library module (mocked `gh`,
   `glab`, `kubectl`, `oc`, `skopeo` and `podman` through the `conftest.py`
   fixtures; the pinned mutating matrix for every `apply`; the worker bound
   and per-call timeout asserted on a fake executor); written with the block;
   run status UNVERIFIED (CI03-GATES, G0).
3. *Smoke*: the row's line under "Smoke cases", on the D-SMOKE executor.

4. *Evidence*: `tests/acceptance/receipts/<tool>-<case>.json` with `kind`,
   `status`, `records`, `plan_digest` and `readback` (mutations),
   `result_action` (`APPLIED` then `NO_OP`), `execution_host`, `captured_at`.

5. *Verification*: `tools/check_live_acceptance.py --root . --expected
   tests/acceptance/expected.toml --receipts tests/acceptance/receipts
   --skill ci-skills --json`, with the row's `[[smoke_cases]]` entry
   declared, prints `"status": "PASS"` and exits 0.

## Open decisions

- The `scripts/supply-chain/lib` sources behind `push_images.sh` and
  `push_helm.sh` were outside the authorized read set; `harbor_push.py` is
  built from `bootstrap_runner_images.bash:22-66` and the adapters' flags
  until they are read.
- Module layout: flat `core/<name>.py` until CI10-PHASES' Modularity decision.
