# PARTIALLY_IMPLEMENTED: CI15 — Consistent runner workflows and owned scopes

- grounding: GROUNDED
- verification: UNVERIFIED
- planningStage: PLANNED

## Authority

The operator requests one consistent CI-Skills interface for runner workflows,
owned namespaces and cleanup, a Conda-capable toolbox on selected Debian or
Ubuntu bases, a GitLab Kubernetes executor, the GitHub self-hosted runner use
case, and public GitLab.com coverage. This proposal uses `main` at
`0aad4746bd460d0df0f8cf2859db19841e0843fc` and its pinned Standards
revision `5bd31f103621dcd7e49ec6e6af55583de7056d7d`. CI11 owns runner
operations; CI13 owns the GitLab image-build chain. CI14 owns GitLab mirroring
and Agent installation. This phase assigns no priority rank to other work.

## Consumer

A user installs CI-Skills and asks an agent to prepare or inspect a runner for
the selected repository or project. The catalog directs the agent to an
installed command. The command reads the selected target and live resources,
returns a plan, applies only a confirmed plan, and reports bounded JSON with
the actual namespace, provider runner identity, retained resources and cleanup.
The selected target supplies access; operation inputs select the toolbox and
workload. Credential values stay out of reports. No target key is added here.

## Motivation

Three installed command paths currently use two runner implementations; the
GitLab Kubernetes executor is the planned third type. Their
namespace and CLI conventions differ, so an agent can select the wrong scope
or mistake a Podman job container for a separate runner. A common owned-scope
contract makes creation, retention and cleanup observable. The existing
single GitHub runner does not replenish an ephemeral fleet. GitLab.com has no
installed-candidate live proof.

## Diagram

```text
Request + target + ToolboxSpec
  -> catalog / schema / help -> existing WorkflowExecutor
       -> shared scope and toolbox lifecycle
          -> GitHub callbacks -> Kubernetes runner -> GitHub read-back
          -> GitLab callbacks -+-> shell manager + nested Podman -> GitLab read-back
                              +-> Kubernetes executor + job Pods -> GitLab read-back
          -> CI13 chain -> toolbox + GitLab shell manager (reuses callbacks)
       -> image, OS, environment and scope read-back
  -> bounded report to the agent

Temporary owned resources -> cleanup -> absence read-back
Persistent runner resources -> retain on success -> explicit owned delete
```

Provider services own API calls. Shared callbacks own sequencing and resource
lifetime. Command mains parse inputs and render the common result.
Python paths below are under `ci-skills/lib/python/`; schema paths are under
`ci-skills/`.

## Implementation Plan

### IMPLEMENTED: three paths, two runner implementations

`create_selfhosted_runner.py create|update|delete` builds and deploys one
persistent GitHub runner in Kubernetes. `gitlab_runner_manager.py deploy|delete`
hosts a GitLab shell executor in OpenShift; its Pod runs nested Podman jobs.
`image_build_readiness.py chain` already builds and publishes a toolbox,
creates a GitLab runner record, and deploys that same manager. Preserve these
paths. Project/group are GitLab runner scopes, not new executors. The requested
GitLab Kubernetes executor is not installed yet.

### PARTIALLY_IMPLEMENTED: namespace and Podman ownership

`schemas/toolbox.schema.json` already requires `spec.build.namespace`.
`WorkflowAction._workload` accepts `--namespace` or that build namespace.
`EnsureNamespace` creates missing namespaces and cleans its creations after
dependent failure. GitLab manager and Podman probe paths already use
`RunScope` ownership. The GitHub delete path removes its Deployment and
Secrets but has no matching owned-namespace deletion; its default also joins
runner placement to the build namespace.

Reuse `core/kubernetes/run_scope.py` and `ResourceSet` to resolve or create the
actual scope before apply. Keep build/probe resources temporary and prove
their cleanup on every exit. Keep a successful persistent runner and its
namespace until explicit delete; then remove only resources whose ownership
matches live read-back and verify absence. Preserve pre-existing namespaces.
Podman containers, volumes and storage inside a runner Pod have their own
cleanup evidence; namespace deletion alone does not establish that cleanup.

### PARTIALLY_IMPLEMENTED: consistent agent interface

Reuse `core/catalog.py`, the shared parsers, `WorkflowExecutor`, existing
result schemas and generated `tools.json`. Align plan/apply confirmation,
namespace meaning, `--help`, `--json`/`--yaml`, and report status across the
three paths while preserving existing accepted flags. GitHub labels and GitLab
tags remain distinct provider concepts. The catalog and CLI must tell the
agent which scopes hold builds, managers, job Pods and runners. A result must
list each observed scope's purpose, name, UID, ownership, retention and cleanup.
Schema and examples change together with implementation; exact fields require
separate approval. Input schemas reject invalid executor, toolbox and scope
intent before mutation. Result schemas give agents stable observed identities,
blockers and cleanup evidence;
[CI07-SCHEMA](CI07-SCHEMA.md) owns this contract.

### PARTIALLY_IMPLEMENTED: Debian/Ubuntu and Conda toolbox

`ToolboxSpec` already selects `baseImage`, dependencies and optional `conda`;
the build fingerprints the Conda environment file. Current rendering requires
`conda` in the base image, so a plain Debian or Ubuntu base is not yet an
out-of-box Conda toolbox. The selected base must resolve to an immutable
digest. Read its actual OS identity, use the existing dependency mechanism to
provide Conda when absent, and verify the requested environment's interpreter
and tools inside the resulting image and consuming runner. The agent selects
Debian or Ubuntu only through `baseImage`; read-back reports the actual OS.
Do not add a separate OS-flavor input. Qualify the same toolbox contract in
the nested Podman path and the Kubernetes-hosted private runner path.

### NOT_IMPLEMENTED: GitLab Kubernetes executor

Extend `gitlab_runner_manager.py` with one typed executor selection in its
catalog and schema; keep the shell/Podman variant and its accepted inputs.
Reuse GitLab registration and token handling, `RunScope`, toolbox publication
and `DeployRunner`. Current bootstrap fixes `--executor shell`, while the
manager action requires a Podman node and privileged SCC. Separate shared
identity and scope from executor-specific prerequisites: the Kubernetes
variant omits Podman setup and uses the existing `RunnerAction.podman=None`
deployment path. Its executor callback owns native runner configuration and
scoped Kubernetes permissions; both variants share workload and report logic.
The shared Deployment currently disables ServiceAccount token mounting, so
its inputs must support executor-specific API authentication. D-002 resolves
the exact credential and permission mechanism.

Keep the manager in a retained, owned namespace. Job scopes must be owned,
read back and cleaned without removing pre-existing namespaces. D-002 selects
the supported runner configuration and required permissions before apply;
confirmed plans show any namespace-create/delete permission. Independently
read back the exact job, Pod, namespace and toolbox digest. A prefix or shared
manager namespace does not prove job ownership. Verify job scope removal while
the manager remains; explicit owned manager deletion then verifies its own
absence. Unknown ownership or failed cleanup returns bounded `BLOCKED`
evidence. Report the distinct manager image and job toolbox image.

### PROPOSED: GitHub ephemeral fleet

The current GitHub workload is one persistent replica and its registration is
not ephemeral. Extend its existing provider and workload boundary for unique
one-job registrations, replenishment, desired capacity and owned deletion.
Choose the lifecycle mechanism before implementation: extend current
workloads or compose GitHub Actions Runner Controller. Preserve the persistent
command. Repository versus organization scope, registration versus JIT,
capacity and credential delivery are open decisions.

### UNVERIFIED: GitLab.com qualification

The existing GitLab API and CI13 chain use the selected host, so qualify the
shell chain and new Kubernetes executor on an approved GitLab.com project and
supported cluster. Read back runner registration, manager workload and a
separately requested consumer job. Record any demonstrated defect against its
existing owner; do not create a GitLab.com-specific provider or assume
GitLab-hosted compute provides the toolbox.

### Open decisions

D-001: The GitHub fleet lifecycle mechanism, scope, capacity and credential
delivery need approval before PR-003. D-002: The Kubernetes executor's exact
CLI and schema, namespace lifecycle configuration and scoped permissions need
approval before PR-004. D-003: GitLab.com qualification needs an approved
project and cluster target before PR-005. These choices do not block the
independent owned-scope and toolbox design work.

## PR Order

Planning IDs are not forge PR numbers. Each PR is a coherent increment and
merges after its listed dependencies and applicable evidence.

| PR / wave | Outcome | Dependency |
| --- | --- | --- |
| PR-001 / W-001 | Shared owned-scope guard and consistent runner interface | Exact schema/interface approval |
| PR-002 / W-002 | Conda-capable Debian/Ubuntu toolbox and consuming-runner read-back | PR-001 scope lifecycle |
| PR-003 / W-003 | GitHub ephemeral fleet using existing provider services | PR-001/002; fleet decisions |
| PR-004 / W-004 | GitLab Kubernetes executor and job namespace ownership | PR-001/002; executor decisions |
| PR-005 / W-005 | GitLab.com qualification and only demonstrated corrections | PR-001/002/004; approved target |

PR-003 and PR-004 have no runtime dependency; merge them in listed order.

## Gates

G-001 is the existing catalog, parser, schema and static validation route.
G-002 through G-006 are proposed acceptance checks, not enforced passes:
G-002 creates/deletes an owned scope and reads back retention and Podman cleanup;
G-003 builds one toolbox and reads OS, Conda and consuming-job evidence;
G-004 reads back one separately requested ephemeral GitHub job, replenishment
and cleanup; G-005 reads back one separately requested Kubernetes-executor
job, Pod, image and scope cleanup while its manager remains; G-006 reads back
a separately requested GitLab.com consumer job, runner and manager for each
variant. G-007 is the
existing exact-head GitHub required result. Focused checks cover complex edge
cases. Consumer jobs are separate acceptance actions, not workflow callbacks.

| ID / authority | State | Delivery | Gate and gap |
| --- | --- | --- | --- |
| R-001 Existing callers / CI11, CI13 | IMPLEMENTED / UNVERIFIED | PR-001/W-001 | G-001/007; compatibility |
| R-002 Owned namespace / CI15 | PARTIALLY_IMPLEMENTED / UNVERIFIED | PR-001/W-001 | G-002; UID and delete |
| R-003 Podman cleanup / CI15 | PARTIALLY_IMPLEMENTED / UNVERIFIED | PR-001/W-001 | G-002; retention |
| R-004 Interface parity / CI15 | PARTIALLY_IMPLEMENTED / UNVERIFIED | PR-001/W-001 | G-001/007; schema/help |
| R-005 OS and Conda / CI15 | PARTIALLY_IMPLEMENTED / UNVERIFIED | PR-002/W-002 | G-003; real job |
| R-006 GitHub fleet / CI15 | NOT_IMPLEMENTED / UNVERIFIED | PR-003/W-003 | G-004; one-job fleet |
| R-007 K8s executor / CI15 | NOT_IMPLEMENTED / UNVERIFIED | PR-004/W-004 | G-005; job cleanup |
| R-008 GitLab.com / CI15 | PARTIALLY_IMPLEMENTED / UNVERIFIED | PR-005/W-005 | G-006; SaaS job |

Earlier DCloud receipts do not prove this CI-Skills candidate or GitLab.com
behavior. G-007 must pass for each actual PR head.
