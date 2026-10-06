# CI11-TOOLS: the tool catalogue and the port of the source repo's scripts

Status: proposed. Depends on: CI02-CLI (one contract), CI07-SCHEMA (one
schema per result), CI09-REFERENCE (the navigator advertises every tool).
Delivered one tool per pull request, in the order of the catalogue.

## Goal

Every tool does one task the operator named, with a machine-readable spec,
and nothing more. No open-ended implementation, no invented behaviour: a tool
exists only when an operator sentence or a source script stands behind it.
The source repo's Bash scripts are ported to Python so every tool has the one
interface of this skill, and collection steps that are independent run
concurrently instead of one after the other.

## Rules

- **One tool, one task.** A source script that does several things becomes
  several thin mains over one library module. Nothing a source script does not
  do is added.
- **One spec per tool.** `--describe` prints the command contract
  (CI02-CLI); the result kind has a schema under `schemas/` (CI07-SCHEMA);
  `--help` lists every argument and output mode; `--json` and `--yaml` are
  the machine outputs. The navigator (CI09-REFERENCE, section 3) lists the
  tool as soon as the catalog declares it.
- **Point to the source.** Each row below names the source script by path
  under the source repo root, the function and lines that implement the task,
  where it is copied to in `ci-skills`, and how it is adapted. The source repo
  root is the operator's local checkout recorded in the ignored
  `.internal/plans/` pointer; its name is not written into tracked files,
  because the project-neutrality gate forbids it.
- **No second implementation.** Transport, envelope, logging, exit table,
  redaction, digest, plan and apply skeleton and concurrency helper each have
  one home in `ci-skills/lib/core/`; a port reuses them or extends them.
- **Config, not constants.** Every host, project, group, namespace, image,
  robot name, path and timeout comes from `target.toml` or a flag; the
  neutrality gate enforces the absence of the source project's name.

## The catalogue

Tasks named by the operator on 2026-10-06. "exists" means the capability is
in `ci-skills` today; "port" means it comes from a source script; "new" means
the operator named it and no script exists, so it is built to the sentence
and nothing more.

| Task | Tool | Library module | Authority | Mutates | Status |
| --- | --- | --- | --- | --- | --- |
| GitLab job fetch | `gitlab_job.py fetch` | `core/collect.py` | gitlab | no | exists |
| GitLab job track | `gitlab_job.py track` | `core/gitlab_jobs.py` | gitlab | no | port |
| GitLab job logs | `gitlab_job.py logs` | `core/gitlab_jobs.py` | gitlab | no | port |
| GitLab pipeline fetch | `gitlab_pipeline.py fetch` | `core/gitlab_pipelines.py` | gitlab | no | exists |
| GitLab pipeline track | `gitlab_pipeline.py track` | `core/gitlab_pipelines.py` | gitlab | no | port |
| GitLab pipeline logs | `gitlab_pipeline.py logs` | `core/gitlab_jobs.py` | gitlab | no | port |
| pipeline start, retry, cancel | `gitlab_pipeline.py start` | `core/gitlab_pipelines.py` | gitlab | yes | README |
| GitLab pipeline schedules | `gitlab_schedule.py` | `core/gitlab_schedules.py` | gitlab | play, create, update | port |
| Merge-request checks | `gitlab_mr.py check` | `core/gitlab_merge_requests.py` | gitlab | no | port |
| OpenShift routes, full view | `ocp_route.py` | `core/ocp_routes.py` | kubernetes | no | port |
| OpenShift HA view | `ocp_ha.py` | `core/ocp_ha.py` | kubernetes | no | port |
| OpenShift machine view | `ocp_machine.py` | `core/ocp_machines.py` | kubernetes | no | port |
| OpenShift build ISO | `ocp_iso.py plan\|build\|serve` | `core/ocp_iso.py` | kubernetes, local | build, serve | port |
| k8s state snapshot | `k8s_state.py` | `core/collect.py` | kubernetes | no | port |
| cluster health, one view | `cluster_health.py` | `core/cluster_health.py` | kubernetes | no | operator |
| job dump by status, interval, keyword | `gitlab_job.py list` | `core/gitlab_jobs.py` | gitlab | no | operator |
| job failure trace | `ci_failure_trace.py` | `core/ci_failure_trace.py` | gitlab, k8s | no | operator |
| pipeline dump, job glob | `gitlab_pipeline.py list` | `core/gitlab_pipelines.py` | gitlab | no | operator |
| runner quick view | `gitlab_runner.py list\|get` | `core/gitlab_runners.py` | gitlab | no | operator |
| Toolbox build | `toolbox_build.py plan\|apply` | `core/toolbox.py` | kubernetes or podman, harbor | apply | port |
| Push to Harbor | `harbor_push.py plan\|apply` | `core/harbor_api.py`, `core/toolbox.py` | harbor | apply | port |
| Create Harbor robot | `harbor_robot.py create` | `core/harbor_api.py` | harbor | yes | port |
| Harbor sanity | `harbor_sanity.py` | `core/harbor_api.py` | harbor | no | port |

Per-tool interface (beyond the universal tier), config keys and concurrency:

- `gitlab_job.py track`: `--job-url`, `--interval S`, `--timeout S`, `--until terminal`;
  `[gitlab] url`; one poll per interval.
- `gitlab_job.py logs`: `--job-url`, `--lines N`, `--search`, `--failure-window N`;
  `[gitlab] url`; no concurrency.
- `gitlab_pipeline.py track`: `--project`, `--pipeline-id`, `--interval`, `--timeout`;
  `[gitlab] url, project`; one poll per interval.
- `gitlab_pipeline.py logs`: `--project`, `--pipeline-id`, `--lines`, `--search`;
  `[gitlab] url, project`; one trace read per failed job, in parallel.
- `gitlab_schedule.py`: `--project`, `--schedule-id`, `--ref`, `--cron`, `--description`,
  the apply flags; `[gitlab] url, project`; no concurrency.
- `gitlab_mr.py check`: `--mr-url` or `--project --mr-iid`, `--lines`, `--failure-window`;
  `[gitlab] url`; trace reads in parallel.
- `ocp_route.py`: `--namespace`, `--search`, `--probe`; `[kubernetes]`; per-route probes in
  parallel.
- `ocp_ha.py`: `--search`; `[kubernetes]`; nodes, etcd, cluster operators and machine config
  pools read in parallel.
- `ocp_machine.py`: `--node-spec PATH`, `--search`; `[kubernetes]`, `[openshift] node_spec`;
  per-machine reads in parallel.
- `ocp_iso.py`: `--node NAME`, `--output-dir`, the apply flags, `--serve-port`;
  `[openshift.iso]`; per-node renders in parallel.
- `k8s_state.py`: `--namespace`, `--node`, `--search`; `[kubernetes]`; every independent
  list read in parallel.
- `cluster_health.py`: `--ceph-namespace`, `--cilium-namespace`, `--last` (event window,
  default 15m), `--skip COMPONENT` (repeatable); `[kubernetes]`; every component collected
  in parallel through the collectors' library functions, never by running the other tools.
- `gitlab_job.py list`: `--project`, `--pipeline-id` (optional), `--ref` (optional),
  `--status failed|success|running|pending|canceled|stuck` (repeatable; `stuck` is pending or
  created longer than `--stuck-after S`), the `time_ranged` tier (`--last 2h`, `--from`,
  `--to` on `finished_at`, else `created_at`), the `filters_records` tier (`--search` over
  name, stage, ref and failure reason), `--limit N` (default 50, bounded); `[gitlab] url,
  project`; pages read in parallel up to the limit. One dump instead of hand-built API
  calls: id, name, stage, status, failure reason, created, started, finished, duration,
  runner, pipeline id and ref, web URL.
- `ci_failure_trace.py`: `--project`, `--pipeline-id` (optional), `--margin S` (default 60);
  `[gitlab]`, `[kubernetes]`; the job read, then the event reads over the window in parallel.
- `toolbox_build.py`: `--tag`, `--builder openshift|podman`, the apply flags; `[toolbox]`,
  `[harbor]`; one build.
- `harbor_push.py`: `--image`, `--tag`, the apply flags; `[harbor]`; one push.
- `harbor_robot.py create`: `--name`, `--permission` (repeatable), `--token-out PATH`, the
  apply flags; `[harbor]`; one create.
- `harbor_sanity.py`: `--search`; `[harbor]`; project, repository and robot reads in parallel.

The source column of each row (path, function, lines, tests) is filled from
the read-only inventories of the source repo taken on 2026-10-06, in the
commit that follows this one; no row is filled from memory.

## The port recipe

The same ten steps for every row; the row repeats them with the real paths.

1. **Point.** Name the source script by path under the source repo root, the
   functions and lines that implement the task, and its tests.
2. **Bound the task.** One tool does one operator-named task.
3. **Library first.** Put the logic in `ci-skills/lib/core/<module>.py` as
   functions over the existing transports: `core.gitlab_api.GlabAPIClient`
   for GitLab, `core.runtime.run_command_bounded` for `oc`, `kubectl`,
   `podman` and `skopeo`, and one bounded standard-library HTTP helper
   `core/http.py` shared by the Harbor API and the reference fetch. Never a
   second transport, envelope, logger, exit table or redaction.
4. **Declare.** Add the command to `core/catalog.py` (`requires`, options,
   subcommands, `mutates`, `returns`, `required_tools`); add the `harbor`
   authority once (target `[harbor]` with `url`, `project`, `robot_file`;
   credential chain: target file, then the declared variables, then none,
   reported in `credential_sources` like the others); regenerate
   `tools.json`. The navigator picks the tool up with no further work.
5. **Thin main.** `ci-skills/bin/<name>.py`: `build_parser()` plus one
   `execute` call, with the shared locator import.
6. **Strip the project.** Every host, project, group, namespace, image name,
   robot name, path and timeout moves to a `target.toml` key or a flag.
7. **Mutations.** Plan by default with `plan_digest`, `--apply --confirm-plan
   DIGEST`, read before and after, independent read-back, cleanup on every
   exit, idempotent second run. The plan, apply and read-back skeleton is
   extracted once from `core/gitlab_actions.py` into `core/action.py`, so
   Harbor and toolbox actions reuse it.
8. **Concurrency.** Independent reads run through the executor pattern
   `core/collect.py` already uses, with a declared worker bound and per-call
   timeout; the row names which serial source steps become parallel.
9. **Tests.** Mocked external commands through the `conftest.py` fixtures;
   the pinned mutating matrix for every `apply`; a receipt kind in
   `tests/acceptance/expected.toml` when the operator names the live target.
10. **Docs.** The catalog entry renders `tools.json`; one routing row in
    `SKILL.md`; the row here carries source, destination, adaptation and
    receipt kind. The source repo is not edited.

## Config entries this phase adds to `target.toml.template`

Nonsecret values only; credentials by file reference, mode 0600, never in a
report. Each key's exact name and meaning is taken from the source script that
reads it, named in the row that introduces the key.

```toml
[harbor]
url = "https://harbor.example.test"
project = "toolbox"
# robot_file = "/home/operator/.ci-skills/credentials/harbor.robot"

[toolbox]
image = "toolbox"
containerfile = "platforms/component/toolbox/Containerfile"
context = "."
builder = "openshift"
namespace = "ci-build"

[openshift]
node_spec = "ocp/reference-node-spec.yaml"

[openshift.iso]
source = "rhcos"
serve_port = 8080
```

## One query grammar for every object class

What the operator is building is one query layer over the lab: every object
class (job, pipeline, runner, merge request, schedule, route, node, machine,
Pod, claim) answers the same verbs with the same filters, so an agent never
hand-builds an API call or parses one.

- `list`: the bounded dump. Filters are the catalog tiers, reused, never
  re-declared: `--status` (repeatable; object-specific values plus derived
  ones such as `stuck`), the `time_ranged` tier (`--last 2h`, `--from`,
  `--to`), the `filters_records` tier (`--search` substring over name, stage,
  ref, description, tags), `--name GLOB` (shell-style match on the object's
  name, for example `--name 'build-*'` to match each job of the last pipeline),
  `--limit N` (default 50). Pages are read in parallel up to the limit;
  `record_count` and `truncated` are always reported.
- `get`: one object by id or URL, with its related objects (a job with its
  pipeline and runner; a pipeline with its jobs and bridges; a runner with its
  projects and managers).
- `track`: poll `get` until a terminal status, with `--interval` and
  `--timeout`; the record is the sequence of observed statuses with timestamps.
- `logs`: the bounded text of the object (job trace, Pod log) with `--lines`,
  `--search` and `--failure-window`.
- actions (`start`, `play`, `retry`, `cancel`, `create`, `update`, `assign`,
  `delete`): plan by default, `--apply --confirm-plan`, independent read-back.

Examples the operator gave, in this grammar: `gitlab_job.py list --project P
--status failed --limit 1` (last failed job), `gitlab_pipeline.py list
--project P --limit 1 --name 'deploy-*'` (last pipeline, each job matching the
glob), `gitlab_runner.py list --project P` (tags, status, online, paused,
`runner_type`, attached projects, last contact, version, platform; the
executor is not in the REST runner record, so `get --managers` reads
`CiRunnerManager.executorName` through `glab api graphql` where the GitLab
version exposes it, else reports `unknown`; the smoke on the declared host
settles which, before the row is marked delivered).

## Concurrency and combo patterns

- **Parallel where the task is a collection.** A tool that reads many
  objects of one kind (pods, nodes, routes, jobs, repositories, machines)
  fans the reads out through the executor `core/collect.py` already uses, with
  a declared worker bound and a per-call timeout, and records each read's
  duration beside the wall time. A loop that runs the same `kubectl` or `oc`
  command once per object is a defect, not a tool; review rejects it.
  `k8s_state.py`, `ocp_ha.py`, `ocp_route.py --probe`, `ocp_machine.py`,
  `gitlab_pipeline.py logs` and `harbor_sanity.py` are collections.
- **Async only where truly needed.** Threads over subprocess and HTTP calls
  are enough for every tool in the catalogue; an `async` interface is added
  only when a tool must hold many open connections or streams at once, and
  then as a separate contract, never by detecting an event loop
  (`software-design.md`, "Synchronous and Asynchronous Contracts"). No such
  tool exists in this catalogue today.
- **Combo patterns, from the README.** Every row carries one of the four
  groupings the README defines: *Visibility* (one report answers a status
  question: `ocp_route.py`, `ocp_ha.py`, `ocp_machine.py`, `k8s_state.py`,
  `harbor_sanity.py`), *CI Combo* (one action wires the GitLab steps an
  agent would run separately: `gitlab_job.py`, `gitlab_pipeline.py`,
  `gitlab_schedule.py`, `gitlab_mr.py`), *Generic Combo* (a view of one
  complex object from its parts: `k8s_state.py`, `ocp_ha.py`), *Toolchain
  Combination* (a prescribed sequence that creates or configures something:
  `toolbox_build.py`, `harbor_push.py`, `harbor_robot.py`, `ocp_iso.py`).
  A combo is a set of existing tools wired as a workflow, in sequence or in
  parallel; it never re-implements a step another tool owns, and each step
  keeps its own plan, apply and read-back.

### Two combos the operator named, concretely

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
  pair `SKILL.md` tells an agent to compute by hand today), and correlate by
  the job's runner node and Pod. One record: the job (id, name, stage,
  status, interval, runner), the window, the events in it, and the
  correlated subset. This is the README's "CI Combo" in one command.

## Smoke cases: fixed arguments and the read-back that proves each tool

Executed from this laptop against the declared targets (CI06-TESTS, "Live
smoke"); one `[[smoke_cases]]` entry per tool in `tests/acceptance/expected.toml`;
every receipt is committed under `tests/acceptance/receipts/`. Values marked
"declared" are written into `expected.toml` by the operator before the smoke,
never chosen by a tool.

- `gitlab_job.py fetch --job-url <the declared job>`: read-back `records[0].id`
  equals the declared id, plus `pipeline.id`, `runner.id`, the trace tail.
- `gitlab_job.py logs --job-url <the declared job> --lines 200`: the trace
  contains the declared marker line; `lines <= 200`.
- `gitlab_pipeline.py start --project hott/test --ref <declared>`: plan, apply,
  read-back of the new pipeline id and `sha`; this pipeline runs one job that
  prints the marker line.
- `gitlab_pipeline.py track --project hott/test --pipeline-id <from start>`:
  the observed statuses with timestamps, ending in `success`.
- `gitlab_pipeline.py fetch` and `logs` on that pipeline: stage counts; the
  marker line in the job's trace.
- `gitlab_schedule.py create --project hott/test --description <declared>
  --ref <declared> --cron <declared>`: plan, apply, GET read-back equal to the
  request, second apply `NO_OP`; `update` and `play` the same way.
- `gitlab_mr.py check --mr-url <the declared merge request>`: read-back of
  `detailed_merge_status`, the head pipeline id and each failing job's name.
- `ocp_route.py --namespace <declared>`: the declared route host appears with
  its TLS and backend fields; probes recorded per route with durations.
- `ocp_ha.py`: control-plane node names and readiness, etcd member health,
  the list of degraded cluster operators (empty or named), machine config
  pool status; one duration per concurrent read.
- `ocp_machine.py --node-spec <declared path>`: each machine's match or
  mismatch against the spec, by field.
- `ocp_iso.py build --node <declared>` then `serve`: the ISO file's sha256
  recorded; `HEAD` on the served URL returns 200; the server stopped on exit
  (read back); second build with the same inputs `NO_OP`.
- `k8s_state.py`: node and pod counts per declared namespace; per-read
  durations and wall time.
- `cluster_health.py --ceph-namespace openshift-storage`: one line per
  component with its evidence (agent count, MTU value, degraded operator
  list, Ceph health string, pending claim count, warning event count) and a
  duration per component; `status` matches the live cluster on the day.
- `gitlab_job.py list --project hott/test --status failed --last 7d --limit 5`:
  the declared failing job's id, name, stage, `started_at` and `finished_at`
  among the returned records; `--status stuck --stuck-after 600` on the
  declared project returns the empty bounded dump with `record_count: 0`.
- `ci_failure_trace.py --project hott/test`: that job, its window, the
  events in it and the correlated subset (the hello-world pipeline includes
  one job that fails on purpose, so the read-back is deterministic).
- `toolbox_build.py apply --tag <declared>`: the image digest reported by the
  build equals the digest read back from Harbor; second apply `NO_OP`.
- `harbor_push.py apply --image <declared> --tag <declared>`: artifact digest
  read back equals the pushed digest.
- `harbor_robot.py create --name <declared> --token-out <declared path>`:
  GET robot by name equals the request, `sink_persisted` true, the token is
  not in the report, second apply `NO_OP`.
- `harbor_sanity.py`: the declared project and repository are listed.

A tool without a committed receipt for its case is not delivered.

## Source rows: OpenShift and Kubernetes (inventory of 2026-10-06)

Paths are relative to the source repo root recorded in `.internal/plans/`
(ignored). "new" marks a read the operator asked for that no source script
performs; it is built to the sentence and nothing more.

### Cross-cutting adaptations for every OpenShift port

- `--dry-run` changes meaning: the source's plan or `--dry-run` reads the
  cluster live (`scripts/ocp/nic-mtu.sh:19-23`, `csr-approve.sh:20-21`,
  `node-status.sh:16`, `serve-iso.sh:25-26`, `node-image.sh:119`,
  `get-rhcos-iso.sh:67`); here `--dry-run` contacts no API, so the source's
  live read-only plan becomes the default run with status `PLANNED`.
- Exit codes collapse to the one table: source 1 becomes `PARTIAL` or
  `BLOCKED`; source 64 to 67 become an unusable input; signals reuse
  `core/mtu_consistency.py` `Interrupted` and `_signal_interrupt`.
- Every `oc` and `kubectl` call goes through `core.runtime.run_command_bounded`
  (replaces the source's two-timer bound, `automation/lib/core/k8s_state_observer.bash:104,224-302`).
- Removed on port: the source's environment-variable prefix, function prefix,
  label and receipt `apiVersion` namespace, `inventory/clusters/...` paths,
  `schemas/*-v1.schema.json` paths, its conda environment name, and every
  site value in `scripts/ocp/README.md` and `scripts/ocp/reference-node-spec.yaml`.

### `ocp_route.py`

- Source: `automation/lib/deploy/public_routes.bash:584` (Admitted plus ready
  backend per Route), `:690` (host under the wildcard, TLS Secret, trusted
  handshake), `:819` (public reachability and the served certificate),
  `:293-389` (one Route list bound to the run), `automation/lib/core/route_readiness.bash:29,45`
  (Admitted; ready EndpointSlice addresses), driver `scripts/deploy/public-routes.sh:158-233`.
- Destination: `ci-skills/bin/ocp_route.py` thin main, `ci-skills/lib/core/ocp_routes.py`
  with the two pure functions from `route_readiness.bash` ported first.
- Adapt: the source checks only Routes declared in a repo manifest
  (`public_routes.bash:47,58-93`); the view lists the cluster's Routes live
  (new read), joins EndpointSlices by service name (`:629-630`), reports
  Admitted per router (`route_readiness.bash:31-32`), TLS and
  `externalCertificate` (`:720,727-743`); the handshake uses Python `ssl`,
  not the conda wrapper at `:165`. The `apply`, `delete` and `absent` steps
  (`:264,451,424`) are not part of a view and stay out.
- Config: `[routes]` namespaces, optional expected list, wildcard, router
  addresses, attempts and interval (`public-routes.sh:17-18`).
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
- Destination: `ci-skills/bin/ocp_ha.py`, `ci-skills/lib/core/ocp_ha.py`.
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
- Destination: `ci-skills/bin/ocp_machine.py`, `ci-skills/lib/core/ocp_machines.py`.
- Adapt: list nodes once, pools once, MachineConfigs once; reuse
  `core/mtu_consistency.py` `physical_uplinks` (do not port `ocp_lib.bash:294`);
  node-local facts through the existing-Pod route (`core/node_pod.py`
  `select_node_pod`) so the view stays read-only; MachineSets, Machines and
  BareMetalHosts are **new** reads (the cluster is bare metal,
  `scripts/ocp/README.md:4-5`); the live-versus-reference comparator is
  **new**; the reference spec is supplied by the consuming project as
  `[openshift] node_spec`, never shipped.
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
- Destination: `ci-skills/bin/ocp_iso.py` with `rhcos-report`,
  `rhcos-download`, `rhcos-serve`, `node-build`, `node-serve`, `delete`;
  `ci-skills/lib/core/ocp_iso.py` renders nodes-config and the server
  manifest in Python (replacing `nodes-config.jq:12-31`, `server.yq:15-19`).
- Adapt: every `--confirm-plan` names the namespace, the cluster-admin
  ClusterRoleBinding (`node-iso-server.yaml:36-40`), the pull-secret copy
  (`serve-node-iso.sh:208-213`), the 0600 pull-secret files on disk
  (`node-image.sh:99-111`, `ocp_lib.bash:194-207`, `serve-node-iso.sh:241-249`),
  the NodePort, the images and the rollout timeout; delete ports the
  UID-precondition delete and absence read-back
  (`automation/lib/core/k8s_lifecycle.bash:25,280`) plus the joiner read-back
  (`node_iso.bash:187`), and drops `serve-iso.sh:80-90`'s requirement that
  delete needs a Ready node.
- Config: `[iso]` namespaces and NodePorts (`node-iso` 30881, `rhcos-iso`
  30880), images, root device (`/dev/sda`), default interface (`eno1`), dns,
  gateway, build timeout (900), arch (`x86_64`), emptyDir sizes (8Gi, 4Gi).
- Concurrency: in the plan, the serving node, the API URL (read three times
  in the source, `serve-node-iso.sh:506-531`), the version, the bootimage
  ConfigMap (read twice, `serve-iso.sh:80-81`), the release image and the
  pull secret in parallel; builds inside the pod stay serial
  (`build.sh:21-35`) unless one pod per ISO is used.

### `k8s_state.py`

- Source: `automation/lib/core/k8s_state_observer.bash:224` (one bounded
  read), `:342,404,494` (classify Pod, workload, Job into ready, transient,
  terminal, unobservable with the terminal waiting reasons `:144-151`),
  `:542-604` (observe and decide), `:635,660` (ready pods by selector;
  workload selector), `automation/lib/core/k8s_lifecycle.bash:563` (namespace
  snapshot of Jobs, Pods, Builds, PVCs, BuildConfigs, CronJobs), `:1029,1211`
  (per-UID status, events and log digests), `automation/lib/deploy/storage_readback.bash:33`.
- Destination: `ci-skills/bin/k8s_state.py`, `ci-skills/lib/core/k8s_state.py`
  with the classifiers ported as pure Python.
- Adapt: reuse `core/collect.py` `collect_storage` (covers
  `storage_readback.bash`) and `collect_events`; capabilities `namespaced`,
  `filters_records`, `time_ranged`.
- Concurrency: the namespace lists (`k8s_lifecycle.bash:568-618`, serial in
  the source), per-UID evidence (`:1062-1199`), claims and volumes
  (`storage_readback.bash:42,52`) in parallel.

### `cluster_health.py`

- Source: the readers above (`cilium_health.bash`, `sanity_check_ceph.sh:177,210,224`,
  `storage_readback.bash:33`, `k8s_state_observer.bash`), composed.
- Destination: `ci-skills/lib/core/cluster_health.py` calling the collectors'
  library functions; thin main `ci-skills/bin/cluster_health.py`.
- Adapt: `sanity_check_ceph.sh` runs `ceph osd stat`, `ceph pg stat` and
  `ceph df` twice each (`:182-187,213-218`) and repeats them for its JSON
  (`:560-563`); take each read once; add `ceph df` to `core/ceph_cluster.py`.

### Also in the source, with a decided home

- `scripts/ocp/csr-approve.sh:56,79-90` with `ocp_lib.bash:69,77,84`:
  `ocp_csr.py` (list pending as `PLANNED`; `--apply --confirm-plan` approves
  only the planned names and signers; one CSR list instead of a read per CSR).
- `scripts/ocp/nic-mtu.sh:157`, `scripts/ocp/nic_mtu.bash:494,749` and
  `scripts/ocp/templates/nic-mtu/*`: extend `core/mtu_consistency.py`; the
  MachineConfig rendering and the fingerprinted `oc create -f` apply become
  `core/nic_mtu_config.py`; `[openshift.nic_mtu]` replaces
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

Paths relative to the source repo root. The source has three GitLab
transports (the shared `glab api` wrapper at `automation/lib/access/gitlab_auth.bash:195`,
`curl` with the token on argv at `automation/lib/ci/pipeline.bash:62-76`, and
`curl` to the MCP endpoint at `automation/lib/access/gitlab_mcp.bash:127`);
every port uses the one `core.gitlab_api.GlabAPIClient` (JSON bodies in a
0600 file, never argv) and emits through `report.emit`. Exit codes collapse
to the one table; the source's `READY` maps to `PASS`, `WOULD_CHANGE` to
`DRY_RUN` or `PLANNED`, every failure class to `BLOCKED` with the class kept
as `reason`, a lost write to `PARTIAL` through `unverified_write`.

### `gitlab_job.py fetch`, `list`, `track`, `logs`

- Source: job by name and id `automation/lib/ci/pipeline.bash:113-117`, job list
  (20 pages of 100, silently capped) `:94-108`, `GET jobs/<id>` `:140`; poll
  until terminal `:136-157` (its status set is wrong: `preparing`,
  `waiting_for_resource` and `scheduled` fall into the default branch and
  return BLOCKED, and an unplayed manual job returns 0 at `:147`); the driver
  `scripts/ci/pipeline.sh:129-139`. No source function reads a job trace: the
  failed job ids are printed and never fetched
  (`scripts/utility/gitlab_util.sh:2350-2352`).
- Already in `ci-skills`: `core/collect.py:783` `collect_gitlab_job` (job URL only;
  last 200 trace lines sanitized, `:853-860`), `TERMINAL_JOB_STATUSES` at
  `core/gitlab_pipelines.py:13`.
- Adapt: `list` and `get` by project and id or by pipeline and name glob;
  `track` with one terminal set and `manual` ending the track explicitly;
  `logs` needs a text method on `GlabAPIClient` (today only `*_json`,
  `core/gitlab_api.py:283-294`) or the trace read from `collect.py`, not a
  third transport; paginate everything (`pipeline.bash:98` caps silently).
- Concurrency: the trace read in `collect_gitlab_job` (`collect.py:853`) joins
  the existing pool (`:820`); list pages in parallel.

### `gitlab_pipeline.py fetch`, `list`, `track`, `logs`, `children`, `start`

- Source: pipeline GET with id, ref and sha checks
  `scripts/utility/gitlab_util.sh:1674-1680`; bridges to the child pipeline
  `:1701-1717`; poll until terminal `:1666-1694` (interval at most 10 s,
  `:62`; `scheduled` and `canceling` wrongly return INVALID_DATA at `:1687`);
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
- Adapt: `list` with the grammar above and `--name GLOB` over jobs;
  `children` reads bridges and downstream pipelines; `logs` fans out the
  failed jobs' traces; `start` is a new mutating kind in `core/gitlab_actions.py`
  with `--ref`, repeatable `--variable KEY=VALUE`, `--require-protected-ref`,
  `--expected-sha`, a `--live-plan` whose digest covers the resolved sha, and
  `--apply --confirm-plan` for the POST (the source POSTs a preview pipeline
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
  operator's sentence: reads through `GlabAPIClient.get_json`, paginated;
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
  traces (Proposal E of the plan; the source never fetches them).
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
- Adapt: `list` and `get` are new reads built to the operator's sentence
  (tags, status, online, paused, `runner_type`, projects, `contacted_at`,
  version, platform, architecture; executor only through the GraphQL
  `CiRunnerManager.executorName` read described under the query grammar);
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
- Adapt: add `list --state` (read-only, so in a non-mutating sibling if
  `mutates` must stay uniform per command); resolve `--milestone TITLE`
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
- Policies carried over from `scripts/utility/README.md`: secrets on stdin or
  in a file, never argv (`:30-32`); read the token whole (`:25-28`); exact-title
  match on top of substring search (`:114-123`); the read-back, not the 201,
  is the evidence (`:134-136`).

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
  OpenShift, "apply is not implemented"). The operator decides whether the
  Python port wraps or supersedes it; the plan keeps one implementation.
- Adapt: build from `git archive` of the commit (`binary_build.bash:124-141`),
  not the working tree; output `DockerImage` with `pushSecret` so the heal
  step, the mirror Job and the disabled TLS check disappear; push by digest
  and read Harbor's digest back (the pattern at
  `automation/lib/access/bootstrap_runner_images.bash:46-57`); the ImageStream
  heal, if kept, is its own destructive confirm; `podman` builder: no source,
  built to the sentence; `--timeout` maps to `completionDeadlineSeconds` and
  `cleanupTimeoutSeconds` (`toolbox.yaml:40-41`).
- Config, each key's source: `[toolbox] spec` (`toolbox_image.bash:22`),
  `source_repo` and `context` (`:182-185`), `dockerfile` (`toolbox.yaml:36`),
  `repository` and `tag` (`toolbox.yaml:10,32`), `namespace` (`:34`),
  `build_config` (`:35`), `push_secret` (`:37`), `successful_history`,
  `failed_history` (`:38-39`), `completion_deadline_seconds` (`:40`),
  `cleanup_timeout_seconds` (`:41`), `platform` (`:52`), `readback_command`
  (`toolbox_image.bash:740-743`), `latest_alias` (`:639`), `source_tls_verify`
  (new, default true, replaces `:680`).
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
  parallel 8, per-read 300 s, per-copy 1800 s; their libraries were not in the
  authorized read set and need one).
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
- Built to the operator's sentence: `POST /api/v2.0/robots` with declared
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
  issue posting at `:234-414,478-605` stay out or become explicit
  mutations); header-file auth carried over (`core/http.py` builds the header
  in process); page every list; `--max-time` on every call (the source has
  none).
- Concurrency: the three probes (`:194,210,222`) and the index downloads
  (`:136,146`).

### `harbor_charts.py` (read-only chart inspection, from `scripts/harbor_manager.sh`)

- Source: `scripts/harbor_manager.sh:159-636` (search, versions, pull,
  extract, grep, secret fields, compare; `helm pull oci://`); tests
  `tests/harbor_manager.bats` (11). Adapt: paging, timeouts, explicit
  `--blocks` and `--stamps` paths instead of the source repo's files
  (`:452-453`), `--all` as a bounded pool (`:579-589`).

### `[harbor]` keys and where each comes from

`url` (`scripts/harbor_manager.sh:19,93`; `constant.bash:71`; `toolbox.yaml:10`),
`project` (`harbor_manager.sh:20`; `toolbox.yaml:10`), `credential_file` (0600
`{username, password}` JSON or dockerconfigjson; replaces the env pair at
`harbor_manager.sh:64,131`, the Secret bindings `bindings.bash:63-64`, the CI
variable `constant.bash:16` and the mounted push Secret `toolbox_image.bash:694-704`),
`admin_credential_file` (robots and projects only), `anonymous_read`
(`harbor_manager.sh:115-134`), `no_proxy` (`:132,148`), `timeout_seconds`,
`attempts`, `backoff_seconds` (`rendered_images.bash:47-49`), `parallel`
(`push_helm.sh:29-30`), `page_size` (`harbor_manager.sh:161,178`). The target
file stays nonsecret: file paths only, like `token_file` today.

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

- Static: the neutrality checker (no source project name), `ruff`,
  markdownlint, the manifest byte-equality test, the catalog contract test
  for every new command.
- Tests: as listed per row; which surface runs them is D-GATE (CI03-GATES,
  G0; "no gate for now" as of 2026-10-06, so they are written and unexecuted
  until a route exists).
- Live: one receipt kind per mutating tool when the operator names the
  target; a tool without a named target ships with its dry-run proof only and
  says so in its row.

## Read-back

After each pull request: `bin/reference.py next <domain>` lists the new tool;
`<tool> --describe` validates against `command-contract`;
`tools/render_manifest.py --check` reports CURRENT; the neutrality checker
reports PASS.
