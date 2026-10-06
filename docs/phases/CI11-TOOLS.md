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
| GitLab pipeline start, retry, cancel | `gitlab_pipeline.py start` | `core/gitlab_pipelines.py` | gitlab | yes | README |
| GitLab pipeline schedules | `gitlab_schedule.py` | `core/gitlab_schedules.py` | gitlab | play, create, update | port |
| Merge-request checks | `gitlab_mr.py check` | `core/gitlab_merge_requests.py` | gitlab | no | port |
| OpenShift routes, full view | `ocp_route.py` | `core/ocp_routes.py` | kubernetes | no | port |
| OpenShift HA view | `ocp_ha.py` | `core/ocp_ha.py` | kubernetes | no | port |
| OpenShift machine view | `ocp_machine.py` | `core/ocp_machines.py` | kubernetes | no | port |
| OpenShift build ISO | `ocp_iso.py plan\|build\|serve` | `core/ocp_iso.py` | kubernetes, local | build, serve | port |
| k8s state snapshot | `k8s_state.py` | `core/collect.py` | kubernetes | no | port |
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
- `toolbox_build.py apply --tag <declared>`: the image digest reported by the
  build equals the digest read back from Harbor; second apply `NO_OP`.
- `harbor_push.py apply --image <declared> --tag <declared>`: artifact digest
  read back equals the pushed digest.
- `harbor_robot.py create --name <declared> --token-out <declared path>`:
  GET robot by name equals the request, `sink_persisted` true, the token is
  not in the report, second apply `NO_OP`.
- `harbor_sanity.py`: the declared project and repository are listed.

A tool without a committed receipt for its case is not delivered.

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
