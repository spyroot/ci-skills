# NOT_IMPLEMENTED: CI12 — Onboard a host into OpenShift from one machine specification

- grounding: GROUNDED
- verification: UNVERIFIED
- planningStage: PROPOSED

## Authority

The phase identity `CI12` continues the project's `CI01` to `CI11` numbering and is a proposal until the project
owner approves it. The project has no `.coordination/roadmap.md`, `.coordination/phases.md` or
`.coordination/priorities.md`; that is recorded as a blocker in `Open Decisions and Scope Boundary`, and this
document plans only from the authority that does exist:

- the owner's direction of 2026-10-15, recorded in [`docs/combos.md`](../combos.md) chain 2 ("OpenShift node
  from one spec"): a user who installed ci-skills asks their agent to onboard a new host, the agent passes one
  machine specification, and the combo carries it to a joined, Ready node, ending with the served ISO removed;
- the owner's directions of 2026-10-15 on run-scoped names and agent-chosen locations, recorded with their dates in
  the [ExecPlan's Decision Log](../exec-plans/active/ci12-node-onboarding.md#decision-log); the catalogue does not
  yet carry them, so the Decision Log is the cited authority for `R-CI12-004`;
- the project motivation in [`README.md`](../../README.md#motivation): one combo instead of many hand-rolled
  calls, less context, the same result in one go;
- the tool catalogue [`CI11-TOOLS`](CI11-TOOLS.md), rows `ocp_machine.py` (owner `core/ocp_machines.py`),
  `ocp_iso.py` (`core/ocp_iso.py`, verbs `rhcos-report`, `node-build`, `node-serve`, `delete`) and `ocp_csr.py`
  (`core/ocp_csr.py`), which already plan the machine comparison, the ISO verbs and certificate approval as single
  commands; none of the three modules exists at main `e5fb154`;
- the six-row evidence model in [`docs/gitlab-toolbelt-plan.md`](../gitlab-toolbelt-plan.md) block 7, which
  defines how a chain is proven;
- the [contribution guide](../contribution_guide.md), sections 2 (one atomic callback) and 3 (compose a thin
  workflow; for a domain other than the toolbox, reuse that domain's action adapter), which every step below is
  written against;
- the live-acceptance gate: policy [`inventory/ci/live-acceptance.json`](../../inventory/ci/live-acceptance.json)
  and checker [`tools/check_live_acceptance.py`](../../tools/check_live_acceptance.py), whose required operation set
  decides which receipts the gate accepts;
- the shared standards pinned by `standards-binding.yaml` at revision `56a579c`. The architecture and plan formats
  this document follows are taken from standards revision `cae52b2`, the `main` the owner named on 2026-10-15; the
  gap between pin and format revision is recorded as a blocker in `Open Decisions and Scope Boundary`.

<!-- architecture-map:v1 -->
    requirements:
      - id: R-CI12-001
        authority: [docs/combos.md, docs/phases/CI11-TOOLS.md, README.md]
        phaseId: CI12
        classification:
          grounding: GROUNDED
          implementation: NOT_IMPLEMENTED
          verification: UNVERIFIED
        deliverable: One machine specification is loaded and validated against a schema; when the agent passes a reference specification it is compared field by field and drift is reported per field; the comparison is NO_OP without a reference.
        owner: ci-skills/lib/python/core/ocp_machines.py
        preserve: [The target and credential resolver, the result envelope, the exit-code contract]
        prIds: [PR-CI12-1]
        gateIds: [G-CI12-STATIC, G-CI12-LIVE]
        evidence: []
      - id: R-CI12-002
        authority: [docs/phases/CI11-TOOLS.md, docs/combos.md]
        phaseId: CI12
        classification:
          grounding: GROUNDED
          implementation: NOT_IMPLEMENTED
          verification: UNVERIFIED
        deliverable: The cluster's boot image is read from its published stream (URL and digest) and the cluster pull secret is resolved by name and digest into a private per-run file for the build, without printing either value.
        owner: ci-skills/lib/python/core/ocp_iso.py
        preserve: [Credential references never become values in reports or receipts]
        prIds: [PR-CI12-1]
        gateIds: [G-CI12-STATIC, G-CI12-LIVE]
        evidence: []
      - id: R-CI12-003
        authority: [docs/phases/CI11-TOOLS.md, docs/combos.md]
        phaseId: CI12
        classification:
          grounding: GROUNDED
          implementation: NOT_IMPLEMENTED
          verification: UNVERIFIED
        deliverable: A self-installing node ISO is built inside the cluster from the release and a node configuration rendered from the specification, written to the agent-chosen output directory and read back by digest and size; it is served in-cluster, the serving read back, and the serving kept across a successful build so the host can boot from it.
        owner: ci-skills/lib/python/core/ocp_iso.py
        preserve: [The build Job and the pull-secret file are removed on every exit; the ISO file persists; serving and its namespace are removed on a failed build and kept on a successful one]
        prIds: [PR-CI12-1]
        gateIds: [G-CI12-STATIC, G-CI12-LIVE]
        evidence: []
      - id: R-CI12-004
        authority: [docs/exec-plans/active/ci12-node-onboarding.md]
        phaseId: CI12
        classification:
          grounding: GROUNDED
          implementation: NOT_IMPLEMENTED
          verification: UNVERIFIED
        deliverable: Every temporary name (namespace, Job, Pod, Service, labels) derives deterministically from one random run identifier; a later verb reconstructs the same scope from that identifier alone; removal deletes exactly the resources carrying the identifier with absence read-back; the ISO and the output directory are never removed.
        owner: ci-skills/lib/python/core/kubernetes/run_scope.py
        preserve: [ResourceSet ownership semantics and its operation label; the executor's reverse cleanup order]
        prIds: [PR-CI12-1]
        gateIds: [G-CI12-STATIC, G-CI12-LIVE]
        evidence: []
      - id: R-CI12-005
        authority: [docs/phases/CI11-TOOLS.md, docs/combos.md]
        phaseId: CI12
        classification:
          grounding: GROUNDED
          implementation: NOT_IMPLEMENTED
          verification: UNVERIFIED
        deliverable: A joining node's certificate signing requests are approved by planned name and signer from one request list, the step ends when the joiner's serving request reads back approved and issued and the node reads Ready within a deadline, and any other signer is reported, never approved.
        owner: ci-skills/lib/python/core/ocp_csr.py
        preserve: [Plan, apply and read-back with the shared APPLIED and NO_OP values]
        prIds: [PR-CI12-2]
        gateIds: [G-CI12-STATIC, G-CI12-LIVE]
        evidence: []
      - id: R-CI12-006
        authority: [docs/combos.md]
        phaseId: CI12
        classification:
          grounding: GROUNDED
          implementation: NOT_IMPLEMENTED
          verification: UNVERIFIED
        deliverable: Given the run identifier of a build, the combo waits for the joiner after the host boots from the served ISO, reads the node state (Ready, role, OS version, kernel against the reference when one was passed), and removes the serving resources and their namespace with absence read-back.
        owner: ci-skills/lib/python/core/ocp_join.py
        preserve: [No interaction with the host's management controller is implemented; the boot is the user's action]
        prIds: [PR-CI12-2]
        gateIds: [G-CI12-STATIC, G-CI12-LIVE]
        evidence: []
      - id: R-CI12-007
        authority: [docs/combos.md, README.md, docs/contribution_guide.md]
        phaseId: CI12
        classification:
          grounding: GROUNDED
          implementation: NOT_IMPLEMENTED
          verification: UNVERIFIED
        deliverable: One thin main composes the callbacks through an OpenShift action adapter over WorkflowExecutor, exposes the command contract in the catalogue with a result schema, routes in SKILL.md, renders the shared report, and declares its operations and a kubernetes setup context to the live-acceptance checker so each slice's sanitized receipt is accepted at the functional checksum.
        owner: ci-skills/bin/ocp_node_onboard.py
        preserve: [The catalogue and parser accept the same verbs and options; --describe and --help work without credentials; the checker's existing required operation set and its freshness policy]
        prIds: [PR-CI12-1, PR-CI12-2]
        gateIds: [G-CI12-STATIC, G-CI12-LIVE, G-CI12-GATE]
        evidence: []
    pullRequests:
      - id: PR-CI12-1
        requirementIds: [R-CI12-001, R-CI12-002, R-CI12-003, R-CI12-004, R-CI12-007]
        dependsOn: []
        waveId: W-CI12-1
        owns: [ci-skills/lib/python/core/kubernetes/run_scope.py, ci-skills/lib/python/core/ocp_machines.py, ci-skills/lib/python/core/ocp_iso.py, ci-skills/lib/python/core/ocp_onboard.py, ci-skills/bin/ocp_node_onboard.py, ci-skills/schemas/machine-spec.schema.json, ci-skills/schemas/results/ocp-node-onboard.schema.json, ci-skills/tools.json, ci-skills/SKILL.md, tools/check_live_acceptance.py, tests/python/test_live_acceptance.py, tests/python/test_run_scope.py, tests/python/test_ocp_node_onboard.py, tests/acceptance/receipts/ocp-node-build-applied.json, .github/workflows/validate.yml, lib/bash/automation/bless.bash]
      - id: PR-CI12-2
        requirementIds: [R-CI12-005, R-CI12-006, R-CI12-007]
        dependsOn: [PR-CI12-1]
        waveId: W-CI12-2
        owns: [ci-skills/lib/python/core/ocp_csr.py, ci-skills/lib/python/core/ocp_join.py, ci-skills/lib/python/core/ocp_onboard.py, ci-skills/bin/ocp_node_onboard.py, ci-skills/tools.json, ci-skills/SKILL.md, tools/check_live_acceptance.py, tests/python/test_live_acceptance.py, tests/python/test_ocp_join.py, tests/acceptance/receipts/ocp-node-join-applied.json, tests/acceptance/receipts/ocp-node-join-no_op.json]
    waves:
      - id: W-CI12-1
        prIds: [PR-CI12-1]
        after: []
        gateIds: [G-CI12-STATIC, G-CI12-LIVE, G-CI12-GATE]
      - id: W-CI12-2
        prIds: [PR-CI12-2]
        after: [W-CI12-1]
        gateIds: [G-CI12-STATIC, G-CI12-LIVE, G-CI12-GATE]
    gates:
      - id: G-CI12-STATIC
        requirementIds: [R-CI12-001, R-CI12-002, R-CI12-003, R-CI12-004, R-CI12-005, R-CI12-006, R-CI12-007]
        command: ./bless.sh --all locally; the validate workflow on the pull request head (static gate, docstrings, manifests and schemas, unit tests, installer fixtures, endpoint gate, aggregated required result)
        environment: GitHub Actions on the pull request head; ruleset "protected" (id 25130553) on main requires the ci-skills/required status with the strict up-to-date policy
        enforcement: Existing
        evidence: [Exact head SHA, validate run URL, every job green, ci-skills/required SUCCESS, the new result schema listed in the metaschema step and in bless.bash]
      - id: G-CI12-LIVE
        requirementIds: [R-CI12-001, R-CI12-002, R-CI12-003, R-CI12-004, R-CI12-005, R-CI12-006, R-CI12-007]
        command: ocp_node_onboard.py <verb> ... --apply --confirm-plan <digest> --json --receipt-out tests/acceptance/receipts/<kind>-<verb>-<action>.json, run from the installed candidate
        environment: The user's live smoke setup against the live cluster; the host booted by the user between build and join
        enforcement: Proposed; a receipt is structurally accepted when its file sits in a declared receipt directory, its kind, operation and result action are in the checker's required operation set, its functional checksum matches the candidate, and it binds to exactly one setup context; the checker builds contexts only from gitlab_access receipts today, so the extension adds a kubernetes context from the access-check receipt's kubernetes surface (execution host, identity, the context-and-server target, credential source) and a match rule for the OpenShift kind
        evidence: [One receipt per verb and result action, each row's read-back (ISO digest and size from the output directory, joiner name, approved request names and signers, node Ready), the run identifier shared by the build and join receipts]
      - id: G-CI12-GATE
        requirementIds: [R-CI12-007]
        command: python tools/check_live_acceptance.py --root . --json
        environment: The live_acceptance step of the validate job, on the committed receipts
        enforcement: Existing for the current operation set; the OpenShift operations enter the required set in the same pull request that commits their receipt, so the live run precedes the merge
        evidence: [Checker PASS on the pull request head with the new receipts in its accepted list and no receipt_not_declared problem]
<!-- /architecture-map:v1 -->

## Consumer

The consumer is an agent, a Codex or Claude session, acting for a user who installed ci-skills and asked for help
onboarding a new host into their OpenShift cluster. The agent calls the installed command with the user's machine
specification and an output directory, and receives a structured result: the evidence rows of the chain, the ISO
on disk, the run identifier to hand to the next verb, and finally a node that reads Ready with the role the
specification declares.

The motivations, in the contract's categories: the capability it adds (a node from one specification, where today
the agent would hand-roll a dozen `oc` reads and writes and the user would assemble an ISO by hand); the necessity
it addresses (every manual onboarding repeats the same error-prone steps, and the certificate approval step is
the one most often done wrong); and the limitation in established code it resolves (the skill has Kubernetes
readers and a toolbox-backed runner deployment but nothing that builds an ISO, waits for a joiner or approves its
certificate requests). Each traces to chain 2 of the catalogue and the README motivation.

## Motivation

A user adds hosts to a cluster to run CI workloads: machine-learning jobs, GPU jobs, container builds. Today that
is a sequence of reads from the cluster, a boot-image lookup, a pull-secret copy, an ISO build on a workstation
that cannot reach the host's management network, a manual boot, two rounds of certificate approval, and a check
that the node came up with the expected role and versions. Each step is a place to make a mistake, and none of it
is reusable by an agent. After this phase the agent runs one command per slice, every step is an atomic callback
with its own plan, apply and read-back, and the chain is proven by linked evidence rows bound to the installed
runtime's checksum. The observable outcome for the user: a specification goes in, an ISO comes out of the output
directory with its digest recorded, the host boots from it, and the node reads Ready.

## Diagram

    agent ─ ocp_node_onboard.py build --spec S [--reference R] --output-dir D ─► OnboardWorkflow (prepare → callbacks → execute)
                                                                                      │ mode: --dry-run | --live-plan | --apply --confirm-plan
                       OnboardState(DeliveryState), bound once; state.run_scope = RunScope.new()
                                                                                      │
      K8SAuth ─► ReadMachineSpec ─► ReadBootImage ─► ResolvePullSecret ─► BuildNodeImage ─► ServeNodeImage
      (build)        │ state.spec        │ state.boot_image    │ state.pull_secret_ref   │ state.iso      │ state.served_url
                     │ state.reference                                                                    │
                     └─ cleanup, reverse: pull-secret file and Job always; Pod and Service (ServeNodeImage) and namespace (BuildNodeImage) only when state.failed ─┘
                                                        ▼ result and receipt carry run_id, namespace, served_url
                                                        ▼ (the user boots the host; outside the API)
    agent ─ ocp_node_onboard.py join --run-id <run_id> [--node N] [--reference R] ─► OnboardWorkflow
                       state.run_scope = RunScope.from_id(run_id)
      K8SAuth ─► WaitForJoiner ─► ApproveJoinRequests ─► ReadNodeState ─► RemoveServing              (join)
                     │ state.joiner        │ state.approved         │ state.node        │ absence read-back
                                                                                      │
                                      shared report ─► JSON result, sanitized receipt (schema ocp-node-onboard)

Data flow runs left to right through named state fields; control flow is the executor's `collect → run → update`
per callback and reverse `cleanup` for every entered callback on success and on failure; construction dependency
is the callback order the adapter declares per verb. The hand-off between the two verbs is the run identifier:
`build` returns it, the agent passes it to `join`, and the scope is reconstructed from it. The boot itself is the
user's action on their hypervisor or hardware and crosses the system boundary: the combo records what to mount and
waits for the joiner.

## Implementation Plan

### Current State and Intended Outcome

NOT_IMPLEMENTED: the chain as a whole. Inspected at main `e5fb154` (documentation only for this phase). The
relevant tree, with the files this phase adds marked NEW:

    ci-skills/
      bin/
        ocp_node_onboard.py                      NEW  thin main: build, join
      lib/python/core/
        abstract/                                     callback lifecycle, executor, state, action
        executor.py                                   WorkflowExecutor(context, Callbacks([...]))
        delivery_state.py                             DeliveryState, ProviderAuthAction
        toolbox_actions.py                            WorkflowAction, EnsureNamespace (toolbox domain)
        provenance.py                                 functional checksum, portable receipts
        gitlab/gitlab_action.py                       GitLabWorkflow: the domain action adapter this phase mirrors
        kubernetes/kubernetes.py                      K8SCallback, K8SAuth
        kubernetes/resources.py                       ResourceSet, OPERATION_LABEL
        kubernetes/runner.py                          PullImage temporary Job, dockerconfigjson Secrets
        kubernetes/run_scope.py                  NEW  RunScope over ResourceSet
        ocp_machines.py                          NEW  CI11 owner; ReadMachineSpec lives here
        ocp_iso.py                               NEW  CI11 owner; ReadBootImage, ResolvePullSecret, BuildNodeImage, ServeNodeImage, RemoveServing
        ocp_csr.py                               NEW  CI11 owner; ApproveJoinRequests
        ocp_join.py                              NEW  WaitForJoiner, ReadNodeState
        ocp_onboard.py                           NEW  OnboardState, OnboardAction, OnboardWorkflow
      schemas/
        machine-spec.schema.json                 NEW
        results/ocp-node-onboard.schema.json     NEW
      lib/python/sys/status.py                        PASS, DRY_RUN, PLANNED, PARTIAL, APPLIED, NO_OP
    tools/check_live_acceptance.py                    required operation set, GATE_SURFACES
    inventory/ci/live-acceptance.json                 declared receipt directories, freshness
    tests/acceptance/receipts/                        committed sanitized receipts

CI topology, from `.github/workflows/validate.yml`: the `validate` job runs the static gate, the docstring check,
the manifests-and-schemas step (an explicit metaschema list, which the new result schema joins, paired with the same
check in `lib/bash/automation/bless.bash`), the unit tests (`tests/python/`, with the checker tests run by their own
step), and the live_acceptance step that evaluates the committed receipts; `gate-ci-skills-endpoints` and
`test-install` run beside it; `required-result` aggregates them into the `ci-skills/required` status that the main
ruleset requires.

Nothing reads machine specifications, builds or serves an ISO, waits for a joiner or approves certificate
requests. CI11 plans `ocp_machine.py`, `ocp_iso.py` and `ocp_csr.py` as single commands owned by
`core/ocp_machines.py`, `core/ocp_iso.py` and `core/ocp_csr.py`; none exists. This phase creates those three
modules with the callbacks the chain needs, so each path has one owner: CI11 keeps the single-command verbs on
top of the same callbacks, and CI12 owns the composition. The checker accepts a receipt only when its kind,
operation and result action are in its required operation set; any other receipt in a declared directory is
`receipt_not_declared` and turns the gate red. An accepted operation receipt must also bind to exactly one setup
context, and the checker builds those only from `gitlab_access` receipts (execution host, origin, identity and
target), so an OpenShift receipt has no context to bind to today. The directory `tests/acceptance/receipts` is
already declared in the policy, so the policy file itself does not change; the checker and its tests do: the
required operation set gains the OpenShift operations, and a kubernetes setup context is derived from the
access-check receipt's kubernetes surface (execution host, identity, the context-and-server target, credential
source) with a match rule for the new kind.

Intended outcome: seven requirements, two pull requests, two waves, one command with the verbs `build` and `join`
sharing the mode flags of the GitLab actions (`--dry-run`, `--live-plan`, `--apply --confirm-plan`), each callback
reusable by other chains, and the run scope reusable by the image-build readiness probe (chain 11) and the GitLab
runner manager (block 7).

### Design and Decomposition

One concrete state. `OnboardState(DeliveryState)` adds the named fields the diagram shows: `spec`, `reference`,
`run_scope`, `boot_image`, `pull_secret_ref`, `iso`, `served_url`, `joiner`, `approved`, `node`. Every callback
reads earlier fields and writes its own; the fields are the only hand-off.

One action. `OnboardAction(SkillAction)` is a frozen dataclass with `spec_path`, `reference_path` (optional),
`output_dir`, `run_id` (required by `join`, generated by `build`), `node` (optional; otherwise the joiner is
discovered by the hostname the specification declares), `build_timeout`, `join_timeout`, `approve_timeout`,
`overwrite`, and the shared mode flags. Every environment specific arrives here as an argument or through the
target resolver; none is hardcoded.

One adapter. `OnboardWorkflow` in `core/ocp_onboard.py` mirrors `GitLabWorkflow` in `core/gitlab/gitlab_action.py`:
`prepare()` validates the mode, resolves the Kubernetes target (offline for `--dry-run`), builds the plan and its
digest from the specification, the reference, the output directory and the run identifier, and constructs the run
scope (`RunScope.new()` for `build`, `RunScope.from_id(run_id)` for `join`); `callbacks()` returns `K8SAuth`
followed by the verb's sequence, with a confirmation callback before any apply; `execute()` runs the executor and
renders the shared report; the result validates against `schemas/results/ocp-node-onboard.schema.json`. The
toolbox adapter `WorkflowAction` is not reused: it parses `--spec` as a toolbox specification and requires a toolbox
catalogue kind, as the contribution guide states; its mode handling and report shape are copied, not its class.
`EnsureNamespace` is not reused: it belongs to the toolbox action and keeps its namespace on success; here the
namespace is a `ResourceSet`-owned document with the operation label, created in `BuildNodeImage.collect` and
removed by the scope.

Callbacks, each a `K8SCallback[OnboardAction]` unless stated, each owning its hooks only, with their module:

- `ReadMachineSpec` (`LocalCallback`, `core/ocp_machines.py`): `collect` loads and schema-validates the
  specification and, when passed, the reference; `run` compares them field by field and writes `state.spec`,
  `state.reference` and the drift rows, `NO_OP` without a reference. No cluster access.
- `ReadBootImage` (`core/ocp_iso.py`): `run` reads the published boot-image stream and writes `state.boot_image`
  (URL, digest, version); `update` records that install must use this version, not the running nodes' version.
- `ResolvePullSecret` (`core/ocp_iso.py`): `run` reads the cluster pull secret into a private per-run file (mode
  0600) under the scope's private directory and writes `state.pull_secret_ref` as name and digest; `cleanup`
  removes the file on every exit. The value never enters state or report.
- `BuildNodeImage` (`core/ocp_iso.py`): `collect` applies the scope's namespace and renders the node configuration
  from `state.spec`; `run` creates the build Job in that namespace, waits within `build_timeout`, copies the ISO
  to the output directory (blocked when the file exists and `overwrite` is unset); `update` reads the file back
  (exists, digest, size) and writes `state.iso`; `cleanup` deletes the Job on every exit and, when `state.failed`,
  the namespace it applied, each with absence read-back: the callback that applies a resource owns its failure
  removal, because the executor runs cleanup only for entered callbacks.
- `ServeNodeImage` (`core/ocp_iso.py`): `run` creates the serving Pod and Service in the scope; `update` reads the
  URL back and writes `state.served_url`; `cleanup` reads `state.failed`: when the run failed it removes the Pod
  and the Service with absence read-back (the namespace is `BuildNodeImage`'s to remove), when it succeeded it
  leaves them and the result records `run_id`, the namespace and the URL so the host can boot.
- `WaitForJoiner` (`core/ocp_join.py`): `run` polls for the node object (by `node` or the declared hostname)
  within `join_timeout` and writes `state.joiner`.
- `ApproveJoinRequests` (`core/ocp_csr.py`): `collect` lists pending requests and selects by planned name and
  signer; `run` approves exactly those; `update` reads each back and loops, within `approve_timeout`, until the
  joiner's serving request is approved and issued and the node reads Ready; any other signer is reported. `NO_OP`
  when nothing is pending and the node is already Ready.
- `ReadNodeState` (`core/ocp_join.py`): `run` reads the node and compares role, OS version and kernel with
  `state.reference` when one was passed.
- `RemoveServing` (`core/ocp_iso.py`): `run` deletes the scope's owned Pod, Service and namespace with absence
  read-back; `NO_OP` when they are already absent.

Reuse: `K8SAuth` first in every verb; `ResourceSet` for everything created; the temporary-Job pattern of
`PullImage` for the build Job; `APPLIED` and `NO_OP` from `ci-skills/lib/python/sys/status.py`; the existing report
renderer and portable receipt writer.

Serving lifetime, decided: the serving resources have one owner, the run identifier, and one lifetime, from a
successful `build` to the end of `join`. The mechanism is the executor's contract: cleanup runs for every entered
callback on success and on failure, and `state.failed` is set before cleanup on failure, so `ServeNodeImage.cleanup`
can keep the Pod and Service on success and remove them on failure, and `BuildNodeImage.cleanup` does the same for
the namespace it applied. Cleanup runs only for entered callbacks, so a build that fails before serving starts
leaves no namespace behind, because its own callback removes it. `join` reconstructs `ResourceSet(namespace,
operation=run_id)` from `--run-id`, and `RemoveServing` deletes only what carries that label. No discovery by any
other label, and a new run never touches another run's resources, because `ResourceSet` refuses documents whose
operation label differs.

Concrete statuses, from the shared adapter rule: `--dry-run` returns `DRY_RUN` in phase `OFFLINE_PLAN` with the
plan digest and no API call; `--live-plan` returns `PLANNED` in phase `LIVE_PLAN` after `K8SAuth` reads the
identity back; `--apply --confirm-plan <digest>` returns `PASS` with `APPLIED` or `NO_OP` rows, `PARTIAL` when a
cleanup step reports `BLOCKED`.

#### When to Decompose

Two slices, because they have independent reasons to change and independent proofs: `build` (reads and the ISO) is
provable with nothing but the cluster and an output directory; `join` needs a host that boots. Each is one pull
request with its own live receipts.

#### When to Introduce Abstraction

The options for run-scoped naming and cleanup, compared:

- Bare `ResourceSet(operation=<run id>)` with each callback choosing its own names: ownership and cleanup of the
  created set exist today, but identifier generation, deterministic name derivation per kind, namespace creation
  and removal, and reconstruction from an identifier would be repeated in every callback and in the two other
  consumers (chain 11 probe, block 7 runner manager).
- Extending `ResourceSet` with those behaviours: couples a general ownership type to naming policy and to the
  `state.failed` keep-or-remove decision, which only this chain needs.
- A small `RunScope` over `ResourceSet`, chosen: one frozen dataclass (`run_id`, `namespace`, `labels`), two
  constructors (`RunScope.new()`, `RunScope.from_id(run_id)`), `scope.name(kind)` for deterministic names,
  `scope.private_dir` for per-run files, and `scope.resources(service) -> ResourceSet` bound to the operation label.
  No hierarchy, no factory. The adapter's `prepare()` constructs it and stores it in `state.run_scope`; callbacks
  only read it. Consumers: this phase's callbacks, the image-build readiness probe and the GitLab runner manager.

`MachineSpec` is a typed loader with a JSON schema under `ci-skills/schemas/`, not an abstraction: one consumer,
one shape, validated at `collect`.

### Open Decisions and Scope Boundary

    BLOCKER: Deriving the phase identity and priority of CI12 from project authority
    Authority checked:
    - docs/agents/contracts/system-architecture.md at standards revision cae52b2 (Authority section inputs)
    Observation:
    - .coordination/roadmap.md, .coordination/phases.md and .coordination/priorities.md do not exist in the project
    Risk:
    - CI12 could be numbered or prioritised against work the owner has not approved, and a later phase could reuse the identity
    Safe next step:
    - The project owner creates the three files or names the authority this phase derives from; until then CI12 stays PROPOSED

    BLOCKER: Running bin/check-system-architecture.py against this document at the project's pin
    Authority checked:
    - standards-binding.yaml (pin 56a579c); docs/agents/contracts/system-architecture.md exists only from revision cae52b2
    Observation:
    - At the pin the contract and checker are absent; the checker reports ARCH-INPUT UNAVAILABLE for this project
    Risk:
    - This document's conformance is asserted, not enforced, until the pin moves
    Safe next step:
    - The owner bumps standards-binding.yaml to a revision at or after cae52b2 in its own pull request; conformance was checked in a throwaway copy bound to cae52b2 and recorded in the pull request body

Decided, recorded here so no step hides a choice:

- Reference specification source: the agent passes `--reference PATH`, the same shape captured from a working node;
  without it the comparison is `NO_OP` and the node read-back compares nothing. No capture verb exists in this phase.
- Cross-verb hand-off: the run identifier, returned by `build` and passed to `join` as `--run-id`; the namespace and
  the served URL are also in the result for the agent's records, but the identifier alone reconstructs the scope.
- Ownership against CI11: the callbacks live in the CI11-owned modules whose behaviour they implement, and CI11's
  single-command verbs become thin mains over the same callbacks when they are built; no second module tree.
- Where the ISO build runs: in-cluster, because a workstation build cannot reach the host's network and streams
  gigabytes over a slow tunnel.
- The host's boot is the user's action; automating the management controller is out of scope for this phase.
- The live-ISO alternative (a hand-built Ignition document installed from the stock live ISO) is out of scope; no
  planned verb exists for it.
- Post-join CI preparation (labels and taints from the specification, then verification) is a separate proposal:
  chain 2 ends at the served ISO's removal and no recorded authority carries the preparation step, so it is
  UNGROUNDED here and gets its own requirement when the owner records it.

The boundary is the two slices above. A third pull request for the single-command CI11 verbs is a separate
proposal.

## PR Order

PR-CI12-1 (wave W-CI12-1) delivers the run scope, the specification loader, the boot-image and pull-secret reads,
the ISO build and serving, the adapter and the thin main with the `build` verb, the catalogue entry, the result
schema with its linter pairing, the routing, and the checker extension that declares the `build` operation and
binds OpenShift receipts to the kubernetes surface of the access-check receipt; its
live proof is the build receipt with the ISO read back from the output directory. PR-CI12-2 (wave W-CI12-2)
depends on it for the state fields, the run scope and the serving to remove, and delivers the `join` verb: the
joiner wait, certificate approval, node state and serving removal, with the checker extension for the `join`
operations; its live proof needs a host on the bench. Because the checker fails on a missing required operation
as well as on an undeclared receipt, each operation enters the required set in the same pull request that commits
its receipt, and that receipt comes from the pull request's own head run from the installed candidate: the live
run precedes the merge, and a head change invalidates the receipt. Each pull request leaves the installed command
coherent under the existing gates, with the unimplemented verb absent from the catalogue rather than stubbed.

## Gates

The traceability map in `Authority` is the canonical map. `G-CI12-STATIC` is the existing validate workflow and
the local pre-commit gate; its enforcement is the main ruleset's required `ci-skills/required` status with the
strict up-to-date policy, read back from the repository's branch rules on 2026-10-15. `G-CI12-LIVE` is the live
smoke of each slice from the installed candidate on the user's live smoke setup; it is proposed, and a receipt
counts only when it is structurally accepted: file in a declared receipt directory, kind, operation and result
action in the checker's required set, functional checksum equal to the candidate's, and exactly one setup context
to bind to, which for OpenShift receipts exists only after the extension adds the kubernetes context. `G-CI12-GATE`
is the existing
live-acceptance step, enforced for the current receipt set and extended by each pull request for its own
operations.

Per-requirement gaps today, all of the same kind: no module, no unit test and no receipt exists for any of
`R-CI12-001` to `R-CI12-007`, so every row records `UNVERIFIED`. The specific evidence each needs: `R-CI12-001`
a loader test (valid, invalid, drift, no reference); `R-CI12-002` a receipt row with the stream digest and the
pull-secret name and digest and no value; `R-CI12-003` the ISO digest and size read back and the serving URL;
`R-CI12-004` a scope test (names derive from the identifier, `from_id` reproduces them, removal deletes exactly
the owned set) and the namespace absent after `join`; `R-CI12-005` an approval-loop test against a two-request
sequence and a foreign signer, and the receipt's approved names and signers; `R-CI12-006` the joiner name and the
node Ready read-back; `R-CI12-007` the checker's accepted list naming the new receipts.

## Live Smoke Evidence

Planned, not observed. `build`: the command run from the installed candidate against the live cluster with a
specification the agent passes and an output directory it chooses; evidence is the receipt's rows plus the ISO's
digest and size read back from that directory and the run identifier. `join`: the same host booted from the
served ISO, the joiner seen, the requests approved by name and signer, the node Ready with the expected role, the
serving and its namespace absent; evidence is the receipt carrying the same run identifier and the node read-back.
All receipts are sanitized with the portable writer and carry the functional checksum of the installed candidate.

## End-to-End Test

The consumer journey: the agent passes a specification and an output directory, runs `build`, hands the returned
run identifier to `join` after the user boots the host, and the final read-back is a Ready node with the declared
role and no resource left carrying the identifier. The proposed test is the two live receipts linked by the same
run identifier and the same checksum; its acceptance gate is `G-CI12-LIVE`. No execution is recorded yet.
