# PARTIALLY_IMPLEMENTED: CI15 — Consistent runner workflows and owned scopes

- grounding: GROUNDED
- verification: UNVERIFIED
- planningStage: PLANNED

## Authority

The operator requests one consistent CI-Skills interface for runner workflows,
namespace selection and cleanup, a Conda-capable toolbox on selected Debian or
Ubuntu bases, a GitLab Kubernetes executor, the GitHub self-hosted runner use
case, and public GitLab.com coverage. This proposal uses `main` at
`77b481224a52a1e842d811656abe45c1e8543384` and its pinned Standards
revision `5bd31f103621dcd7e49ec6e6af55583de7056d7d`. CI11 owns runner
operations; CI13 owns the GitLab image-build chain. CI14 owns GitLab mirroring
and Agent installation. This phase assigns no priority rank to other work.
The input decisions below govern the architecture, PR plan and acceptance
criteria in this document. Their specification does not claim implementation
or live verification.

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

### NOT_IMPLEMENTED: repository-derived default names

**CI15-IDENTITY-001 — Derive repository-scoped names from the caller's repository.**
`REPO` means the repository or project selected by the caller's operation context;
it is not the CI-Skills installation directory or a new mandatory environment
variable. Reuse the selected provider repository/project identity and existing
project binding. When it is known, use its repository name for omitted
repository-scoped runner names/descriptions, workload names, namespaces and
repository-specific tags/labels. Do not substitute the literal `ci-skills`
unless that is the selected repository's actual name.

Resolve inputs in this order:

1. Keep the working directory at the calling project root, as required by
   [the installed skill](../../ci-skills/SKILL.md). The known caller `REPO`
   supplies the operation's repository context; never discover it from the
   installed skill directory.
2. Reuse `core/project_binding.py:resolve_target` and the existing
   [target-selection contract](../../ci-skills/references/access.md): explicit
   `--target` or `--binding`, then `K8S_ADMIN_DIAGNOSTICS_BINDING` or
   `CI_SKILLS_TARGET`, then the calling project's `.ci-skills/target.toml`, then
   the user target. Conflicting selectors or an invalid selected source are
   errors; do not merge sources or fall through after a selected-source error.
3. Forward the known operation repository through the command's declared
   interface. GitLab `--project` overrides `gitlab.project` in the selected
   target. GitHub uses that target's `github.repository`; the current command
   has no `--repo` option. Preserve the existing interface and expose any new
   selector through the approved shared catalog/schema, never a private flag.
4. For each object, use its explicit name input first, then its independently
   verified existing identity on reuse/update/delete, then derive a new default
   from the selected repository name. A numeric project ID requires provider
   read-back of its name before deriving a name; offline intent must say when
   that read-back remains pending.

Derivation is unavailable only when no repository is selected in either the
caller context or selected target, a group scope identifies no single
repository, or a selected numeric ID still lacks its provider name. Request
the missing declared repository selection or explicit object-name argument
before planning that name or applying the operation. For a group-wide resource,
accept an explicit name without inventing a repository. Invalid/conflicting
inputs and failed provider reads report their actual errors; they never select
a substitute identity. Do not silently fall back to a toolbox name/namespace
or the CI-Skills installation name. Protection remains a separate caller
choice; provider-assigned IDs and provider/system labels are not repository
names.

Use one shared resolver and the existing resource-specific validation and
normalization. Preserve the generator's uniqueness and ownership guarantees;
derive its human-readable name component from the repository. Do not infer
ownership from a matching name. Plans and results report the selected repository,
the resolved name and its source. Generated examples and toolbox inputs use the
same resolved repository. A known `REPO=payments` must produce payments-derived
names, including when CI-Skills is installed in a directory named `ci-skills`.

### PARTIALLY_IMPLEMENTED: namespace selection and ownership

**CI15-NS-001 — Explicit and omitted namespace selection.** Both cases are
supported. Explicit namespaces are independent of runner naming and tags; callers
are not required to follow an invented namespace naming convention. Generated
repository-scoped defaults follow CI15-IDENTITY-001.

- **Explicit `--namespace NAME` on initial deployment:** Use that exact existing namespace. The caller
  controls its creation, reuse and cleanup. Preserve it on success, failure and runner deletion. A missing
  selected namespace blocks before runner mutation.
- **Omitted on GitHub create/update:** Preserve a previously resolved scope. For a new runner, derive the
  default from the selected repository under CI15-IDENTITY-001; if derivation lacks its required input,
  request the explicit repository or namespace selection before proceeding. Read the selected namespace before
  creation; reuse it when present or plan creation when absent.
- **Omitted on GitLab manager deploy / CI13 chain:** Use the operation's existing scope identity to find one
  matching live `RunScope`; reuse that scope. With no match, plan one namespace through the shared generator,
  using the resolved repository name component under CI15-IDENTITY-001; request missing input before
  generating it. Multiple matches block.

GitHub delete still requires explicit `--namespace` and `--name`; it has no
toolbox fallback. GitLab manager delete resolves existing resources by its
recorded scope identity and validates `--namespace` when supplied; it never
creates a namespace. Missing or ambiguous ownership blocks deletion.

Selection and ownership are separate. Namespaces predating the original
operation remain borrowed even when selected by a default. A namespace created
by the operation remains operation-owned across updates and deletion; supplying
its name later does not transfer ownership. Extend `RunScope`'s existing generator with the repository name component
under CI15-IDENTITY-001; do not derive a namespace from runner names, tags or
operation IDs. Report selection and ownership separately, including when the
selection source is the repository, explicit input or existing
run scope. Resolve retries through recorded identity and
live ownership; ambiguous discovery blocks rather than guessing a name.

Current implementation and remaining delta:

`schemas/toolbox.schema.json` already requires `spec.build.namespace`.
`WorkflowAction._workload` accepts `--namespace` or that build namespace.
`EnsureNamespace` creates missing namespaces and cleans its creations after
dependent failure. GitLab manager and Podman probe paths already use
`RunScope` ownership. The GitHub delete path removes its Deployment and
Secrets but has no matching owned-namespace deletion; its default also joins
runner placement to the build namespace.

Use `core/kubernetes/run_scope.py` and `ResourceSet` for shared resolution,
ownership and UID-checked deletion. CI15 changes the GitHub explicit
`--namespace` path from creating a missing namespace to reporting the missing
caller prerequisite. For omitted runner namespaces, CI15 replaces the
build-namespace fallback with the explicit resolution order in CI15-IDENTITY-001;
a missing identity requires input instead of a substitute default.
Independent toolbox builds and temporary tool-Pod workflows retain their own
contracts; this decision governs runner placement.

Keep build/probe resources temporary and prove cleanup on every exit. Retain
a successful persistent runner and its operation-owned namespace until
explicit confirmed deletion. Remove only resources whose ownership and UID
match independent live read-back, then verify absence. Always preserve
borrowed namespaces. Failed operations clean only their own creations.
Podman containers, volumes and storage inside a runner Pod have separate
cleanup evidence; namespace deletion alone does not establish that cleanup.

### PARTIALLY_IMPLEMENTED: consistent agent interface

**CI15-ARGS-001 — One declared interface for each shared input.** Reuse
`core/catalog.py`, shared parsers and `WorkflowExecutor`. Catalog declarations,
generated `tools.json`, `--help`, `--describe`, schemas, examples and result
reports must agree on names, aliases, cardinality, requiredness and omission.

The following PR-001 interface binding preserves existing accepted names:

- **Runner namespace:** `--namespace NAME`; one value. Omission: Per-command create/update/deploy/chain and
  delete rules in CI15-NS-001.
- **GitHub runner/workload name:** Existing `--name NAME`; do not apply its naming contract to namespaces.
  Omission: CI15-IDENTITY-001; request missing repository/name input; delete still requires its declared
  identity.
- **GitLab runner description:** `--description TEXT`; add it to the chain and retain `--runner-description`
  as its alias. Omission: CI15-IDENTITY-001; request missing repository/description input for create, manager
  deploy and chain.
- **GitLab runner tags:** Repeatable `--tag TAG`; manager retains `--label`; chain gains `--tag` and retains
  `--runner-label`. Omission: Derive repository-specific defaults under CI15-IDENTITY-001; request explicit
  tags when required and no repository is selected.
- **GitHub runner labels:** Existing repeatable `--label LABEL`; remains a GitHub provider concept. Omission:
  Derive repository-specific defaults under CI15-IDENTITY-001; request explicit labels when no repository is
  selected.
- **GitLab runner protection:** Proposed `--protected true|false`, defined below. Omission:
  CI15-PROTECTION-001.

GitLab manager `--label` is only a legacy alias for GitLab `--tag`; GitHub
`--label` retains its separate provider meaning. Mixed repeatable GitLab
aliases append to one tag list in argument order. Reject blank, comma-containing
or duplicate tags across the combined list using shared GitLab validation;
do not silently deduplicate. Reject conflicting singular aliases before
mutation. Forward the same tags
from record creation through manager deployment and verify them from the
provider. Do not derive tags from namespace or runner names. Provider runner
description, numeric runner ID and Kubernetes workload name remain distinct
identities; report each instead of treating them as interchangeable.

Plans and reports distinguish requested inputs, resolved defaults and observed
values. Confirmed apply binds the canonical requested inputs, concrete selected
namespace and its source/ownership, runner identity, combined tags and explicit
protection to the plan fingerprint. A generated namespace is a planned candidate,
not an observed object until creation and read-back. If resolution changes any
bound value, require a new plan and confirmation; offline plans must not claim
live observations. Scope evidence includes purpose, namespace, UID, ownership,
retention and cleanup; failed or offline reads must not fabricate observations.
Retain the existing result envelopes and callback records. PR-001 must propose
one shared typed scope definition and reference it from both
`schemas/results/toolbox-workflow.schema.json` and
`schemas/results/image-build-readiness.schema.json`; their current extensible
records do not enforce this shape. The exact JSON diff and examples require
approval before implementation under [CI07-SCHEMA](CI07-SCHEMA.md).

### NOT_IMPLEMENTED: caller-selectable GitLab runner protection

**CI15-PROTECTION-001 — Caller selects protected or unprotected.** Protection
is independent of project/group scope and shell/Kubernetes executor selection.
Current `core/gitlab/gitlab_runners.py` reads and filters `access_level`, but
record creation and the CI13 chain do not accept a protection selection.

PR-001 proposes one optional `--protected true|false` input for GitLab record
creation, chain creation and manager deployment. Explicit `true` maps to the
existing provider value `ref_protected`; explicit `false` maps to
`not_protected`. Do not conflate unprotected with running untagged jobs.
Creation passes the selected value through the shared provider service. A
manager deploying an existing runner verifies the requested value and blocks
on mismatch; it does not silently change that runner's access policy.

When omitted during creation, preserve the existing provider-default behavior
and report the actual value returned by independent read-back. When omitted
for an existing runner, preserve and report its current value. The plan must
identify explicit selection, provider default or preservation; omission must
not be reported as an explicit `false`. Include explicit protection in the
confirmation fingerprint. Reuse `access_level` in observed runner results,
add expected-value verification to shared read-back, and block on mismatch
or missing evidence for an explicit choice. Malformed values fail before
mutation. This GitLab setting has no implicit GitHub equivalent.

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

Keep the manager in its retained namespace selected under CI15-NS-001.
Manager and job workloads must have proven ownership; their namespaces can
be borrowed or operation-owned. Job cleanup removes owned resources and only
operation-owned job namespaces, preserving borrowed namespaces. D-002 selects
the supported runner configuration and required permissions before apply;
confirmed plans show any namespace-create/delete permission. Independently
read back the exact job, Pod, namespace and toolbox digest. A prefix or shared
manager namespace does not prove job ownership. Verify owned job-resource
removal and borrowed-namespace preservation while the manager remains;
explicit manager deletion then verifies owned resources are absent. Unknown
ownership or failed cleanup returns bounded `BLOCKED` evidence. Report the distinct manager image and job toolbox image.

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

CI15-NS-001, CI15-ARGS-001, CI15-PROTECTION-001 and CI15-IDENTITY-001
record settled behavior.
Do not reopen those choices because source code or older planning text differs.
The exact interface binding proposed above and the corresponding JSON/schema
artifacts still require exact-content approval; that is an artifact review,
not an undecided namespace or protection policy.

D-001: The GitHub fleet lifecycle mechanism, scope, capacity and credential
delivery need approval before PR-003. D-002: The Kubernetes executor's exact
configuration and scoped permissions need approval before PR-004, within
CI15-NS-001; it must not redefine caller namespace ownership. D-003: GitLab.com
qualification needs an approved project and cluster target before PR-005.
These choices do not block independent PR-001 contract preparation or toolbox
design. No default for these unresolved choices is implied.

## PR Order

Planning IDs are not forge PR numbers. Each PR is a coherent increment and
merges after its listed dependencies and applicable evidence.

- **PR-001 / W-001:** Shared scope lifecycle, repository-derived defaults, argument parity and GitLab
  protection choice. Dependency: Exact interface/schema artifacts for the four decisions.
- **PR-002 / W-002:** Conda-capable Debian/Ubuntu toolbox and consuming-runner read-back. Dependency: PR-001
  scope lifecycle.
- **PR-003 / W-003:** GitHub ephemeral fleet using existing provider services. Dependency: PR-001/002; fleet
  decisions.
- **PR-004 / W-004:** GitLab Kubernetes executor and job namespace ownership. Dependency: PR-001/002; executor
  decisions.
- **PR-005 / W-005:** GitLab.com qualification and only demonstrated corrections. Dependency: PR-001/002/004;
  approved target.

PR-003 and PR-004 have no runtime dependency; merge them in listed order.

## Gates

G-001 is the existing catalog, parser, schema and static validation route.
PR-001 extends its focused checks for canonical flags and legacy aliases,
mixed repeated aliases, repository-derived defaults, omission rules, invalid inputs, protection values,
confirmation binding and schema conformance. Include a consumer repository named
`payments` with the skill installed under `ci-skills`, explicit overrides,
missing/group-only repository selection, numeric-ID read-back, selector errors,
normalization, uniqueness and existing-resource
identity preservation. The new cases are not implemented yet.

G-002 through G-006 are proposed acceptance checks, not enforced passes:

- G-002 proves explicit namespace reuse and preservation, missing explicit
  namespace rejection, omitted selection and rediscovery, ambiguous lookup
  rejection, delete input rules, ownership retention across updates, owned
  creation/deletion, UID read-back, retention and Podman cleanup. For GitLab it also
  proves explicit protected/unprotected creation, omitted-value read-back and
  rejection of a manager protection mismatch before workload mutation.
- G-003 builds one toolbox and reads OS, Conda and consuming-job evidence.
- G-004 reads back one separately requested ephemeral GitHub job,
  replenishment and cleanup.
- G-005 reads back one separately requested Kubernetes-executor job, Pod,
  image and owned-resource cleanup while its manager and borrowed namespaces
  remain. Verify the selected runner protection and actual tags.
- G-006 reads back a separately requested GitLab.com consumer job, runner and
  manager for each variant, including the selected tags and protection.

G-007 is the existing exact-head GitHub required result. Focused checks cover
complex edge cases. Consumer jobs are separate acceptance actions, not
workflow callbacks. Passing static checks alone does not satisfy live gates.

- **R-001 Existing callers / CI11, CI13:** IMPLEMENTED / UNVERIFIED. Delivery: PR-001/W-001. Gate and gap:
  G-001/007; compatibility.
- **R-002 Namespace selection / CI15-NS-001:** PARTIALLY_IMPLEMENTED / UNVERIFIED. Delivery: PR-001/W-001;
  RunScope, ResourceSet, WorkflowAction. Gate and gap: G-002; explicit/omitted selection, UID and cleanup.
- **R-003 Podman cleanup / CI15:** PARTIALLY_IMPLEMENTED / UNVERIFIED. Delivery: PR-001/W-001. Gate and gap:
  G-002; retention.
- **R-004 Interface parity / CI15-ARGS-001:** PARTIALLY_IMPLEMENTED / UNVERIFIED. Delivery: PR-001/W-001;
  catalog, parsers, shared result schemas. Gate and gap: G-001/007; names, aliases, repetition and omission.
- **R-005 OS and Conda / CI15:** PARTIALLY_IMPLEMENTED / UNVERIFIED. Delivery: PR-002/W-002. Gate and gap:
  G-003; real job.
- **R-006 GitHub fleet / CI15:** NOT_IMPLEMENTED / UNVERIFIED. Delivery: PR-003/W-003. Gate and gap: G-004;
  one-job fleet.
- **R-007 K8s executor / CI15:** NOT_IMPLEMENTED / UNVERIFIED. Delivery: PR-004/W-004. Gate and gap: G-005;
  job cleanup.
- **R-008 GitLab.com / CI15:** PARTIALLY_IMPLEMENTED / UNVERIFIED. Delivery: PR-005/W-005. Gate and gap:
  G-006; SaaS job.
- **R-009 Runner protection / CI15-PROTECTION-001:** NOT_IMPLEMENTED / UNVERIFIED. Delivery: PR-001/W-001;
  gitlab_runners.py, chain, manager, shared read-back. Gate and gap: G-001/002/005/006; caller choice and
  observed access_level.
- **R-010 Repository-derived names / CI15-IDENTITY-001:** NOT_IMPLEMENTED / UNVERIFIED. Delivery:
  PR-001/W-001; shared identity and scope resolver, catalog and parsers. Gate and gap: G-001/002; caller
  repository, explicit overrides, missing-input handling and identity preservation.

Earlier DCloud receipts do not prove this CI-Skills candidate or GitLab.com
behavior. G-007 must pass for each actual PR head.
