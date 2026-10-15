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
  machine specification, and the combo carries it to a joined, Ready node;
- the project motivation in [`README.md`](../../README.md#motivation): one combo instead of many hand-rolled
  calls, less context, the same result in one go;
- the tool catalogue [`CI11-TOOLS`](CI11-TOOLS.md), rows `ocp_machine.py`, `ocp_iso.py`, `ocp_csr.py` and
  `harbor_pull_secret.py`, which already plan the machine reads, the ISO verbs and certificate approval;
- the six-row evidence model in [`docs/gitlab-toolbelt-plan.md`](../gitlab-toolbelt-plan.md) block 7, which
  defines how a chain is proven;
- the [contribution guide](../contribution_guide.md), which defines the callback, state and workflow
  abstractions every step below is written against;
- the shared standards pinned by `standards-binding.yaml`, with the architecture and plan formats taken from the
  standards checkout the owner named on 2026-10-15.

<!-- architecture-map:v1 -->
    requirements:
      - id: R-CI12-001
        authority: [docs/combos.md, README.md]
        phaseId: CI12
        classification:
          grounding: GROUNDED
          implementation: NOT_IMPLEMENTED
          verification: UNVERIFIED
        deliverable: One machine specification is loaded, validated against a schema, and compared with a reference specification; drift is reported per field.
        owner: ci-skills/lib/python/core/ocp/machine_spec.py
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
        deliverable: The cluster's boot image is read from its published stream (URL and digest) and the pull secret is resolved by name and digest for the release read, without printing either value.
        owner: ci-skills/lib/python/core/ocp/boot_image.py
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
        deliverable: A self-installing node ISO is built inside the cluster from the release and a node configuration rendered from the specification, written to the agent-chosen output directory, and read back by digest and size; it is served in-cluster and the serving read back.
        owner: ci-skills/lib/python/core/ocp/node_image.py
        preserve: [Temporary resources are owned through ResourceSet and removed on every exit; the ISO persists]
        prIds: [PR-CI12-1]
        gateIds: [G-CI12-STATIC, G-CI12-LIVE]
        evidence: []
      - id: R-CI12-004
        authority: [docs/combos.md]
        phaseId: CI12
        classification:
          grounding: GROUNDED
          implementation: NOT_IMPLEMENTED
          verification: UNVERIFIED
        deliverable: Every temporary name (namespace, Job, labels) derives from one random run identifier; cleanup removes everything carrying it with absence read-back; the ISO and the token sink are never removed.
        owner: ci-skills/lib/python/core/kubernetes/run_scope.py
        preserve: [Existing cleanup ordering of the executor; EnsureNamespace and ResourceSet behaviour]
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
        owner: ci-skills/lib/python/core/ocp/join_requests.py
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
        deliverable: The combo waits for the joiner after the host boots from the served ISO, reads the node state (Ready, role, OS version, kernel against the reference), and removes the serving resources with absence read-back.
        owner: ci-skills/lib/python/core/ocp/join.py
        preserve: [No interaction with the host's management controller is implemented; the boot is the user's action]
        prIds: [PR-CI12-2]
        gateIds: [G-CI12-STATIC, G-CI12-LIVE]
        evidence: []
      - id: R-CI12-007
        authority: [docs/combos.md, README.md]
        phaseId: CI12
        classification:
          grounding: GROUNDED
          implementation: NOT_IMPLEMENTED
          verification: UNVERIFIED
        deliverable: One thin main composes the callbacks in order through WorkflowExecutor, exposes the command contract in the catalogue, routes in SKILL.md, and renders the shared report; each slice ships a sanitized live receipt bound to the functional checksum.
        owner: ci-skills/bin/ocp_node_onboard.py
        preserve: [The catalogue and parser accept the same verbs and options; --describe and --help work without credentials]
        prIds: [PR-CI12-1, PR-CI12-2, PR-CI12-3]
        gateIds: [G-CI12-STATIC, G-CI12-LIVE, G-CI12-GATE]
        evidence: []
      - id: R-CI12-008
        authority: [docs/combos.md, README.md]
        phaseId: CI12
        classification:
          grounding: GROUNDED
          implementation: NOT_IMPLEMENTED
          verification: UNVERIFIED
        deliverable: After the join, the role's CI preparation is applied and verified (labels and taints the specification declares, then the GPU and cluster-health views as verification), so the node is usable by CI jobs.
        owner: ci-skills/lib/python/core/ocp/prepare_ci.py
        preserve: [Read-only verification commands stay read-only]
        prIds: [PR-CI12-3]
        gateIds: [G-CI12-STATIC, G-CI12-LIVE]
        evidence: []
    pullRequests:
      - id: PR-CI12-1
        requirementIds: [R-CI12-001, R-CI12-002, R-CI12-003, R-CI12-004, R-CI12-007]
        dependsOn: []
        waveId: W-CI12-1
        owns: [ci-skills/lib/python/core/ocp/machine_spec.py, ci-skills/lib/python/core/ocp/boot_image.py, ci-skills/lib/python/core/ocp/node_image.py, ci-skills/lib/python/core/kubernetes/run_scope.py, ci-skills/bin/ocp_node_onboard.py, ci-skills/schemas/machine_spec.schema.json, tests/python/test_ocp_node_onboard.py]
      - id: PR-CI12-2
        requirementIds: [R-CI12-005, R-CI12-006, R-CI12-007]
        dependsOn: [PR-CI12-1]
        waveId: W-CI12-2
        owns: [ci-skills/lib/python/core/ocp/join_requests.py, ci-skills/lib/python/core/ocp/join.py, ci-skills/bin/ocp_node_onboard.py, tests/python/test_ocp_join.py]
      - id: PR-CI12-3
        requirementIds: [R-CI12-007, R-CI12-008]
        dependsOn: [PR-CI12-2]
        waveId: W-CI12-3
        owns: [ci-skills/lib/python/core/ocp/prepare_ci.py, ci-skills/bin/ocp_node_onboard.py, tests/python/test_ocp_prepare_ci.py]
    waves:
      - id: W-CI12-1
        prIds: [PR-CI12-1]
        after: []
        gateIds: [G-CI12-STATIC, G-CI12-LIVE, G-CI12-GATE]
      - id: W-CI12-2
        prIds: [PR-CI12-2]
        after: [W-CI12-1]
        gateIds: [G-CI12-STATIC, G-CI12-LIVE, G-CI12-GATE]
      - id: W-CI12-3
        prIds: [PR-CI12-3]
        after: [W-CI12-2]
        gateIds: [G-CI12-STATIC, G-CI12-LIVE, G-CI12-GATE]
    gates:
      - id: G-CI12-STATIC
        requirementIds: [R-CI12-001, R-CI12-002, R-CI12-003, R-CI12-004, R-CI12-005, R-CI12-006, R-CI12-007, R-CI12-008]
        command: ./bless.sh and the validate workflow (static gate, docstrings, schemas, unit tests, installed-package smoke)
        environment: Existing GitHub validate workflow on the pull request head
        enforcement: Existing; the ci-skills/required status is required by the main ruleset
        evidence: [Exact head SHA, run URL, every validate step green]
      - id: G-CI12-LIVE
        requirementIds: [R-CI12-001, R-CI12-002, R-CI12-003, R-CI12-004, R-CI12-005, R-CI12-006, R-CI12-007, R-CI12-008]
        command: ocp_node_onboard.py <verb> --spec PATH --output-dir DIR --json --receipt-out tests/acceptance/receipts/<name>.json from the installed candidate
        environment: The user's live smoke setup on the owner's bench, run from the installed candidate
        enforcement: Proposed; receipts become required when tools/check_live_acceptance.py lists the new operations
        evidence: [Sanitized receipt per step bound to the functional checksum, the ISO read back by digest from the output directory, the node Ready read-back]
      - id: G-CI12-GATE
        requirementIds: [R-CI12-007]
        command: python tools/check_live_acceptance.py --root . --json
        environment: Existing live_acceptance step of the validate workflow
        enforcement: Existing for the current operation set; proposed for the new operations once their receipts are declared
        evidence: [Checker PASS with the new receipts accepted at the candidate's functional checksum]
<!-- /architecture-map:v1 -->

## Consumer

The consumer is an agent, a Codex or Claude session, acting for a user who installed ci-skills and asked for help
onboarding a new host into their OpenShift cluster. The agent calls the installed command with the user's machine
specification and an output directory, and receives a structured result: the evidence rows of the chain, the ISO
on disk, and finally a node that reads Ready with the role the specification declares. The downstream consumer is
the user's CI, which needs that node prepared for its jobs.

The motivations, in the contract's categories: the capability it adds (a node from one specification, where today
the agent would hand-roll a dozen `oc` reads and writes and the user would assemble an ISO by hand); the necessity
it addresses (new nodes need CI preparation before they are useful, and every manual onboarding repeats the same
error-prone steps); and the limitation in established code it resolves (the skill has Kubernetes readers and a
toolbox-backed runner deployment but nothing that builds an ISO, waits for a joiner, approves its certificate
requests or prepares a node). Each traces to chain 2 of the catalogue and the README motivation.

## Motivation

A user adds hosts to a cluster to run CI workloads: machine-learning jobs, GPU jobs, container builds. Today that
is a sequence of reads from the cluster, a boot-image lookup, a pull-secret copy, an ISO build on a workstation
that cannot reach the host's management network, a manual boot, two rounds of certificate approval, and a check
that the node came up with the expected role and versions. Each step is a place to make a mistake, and none of it
is reusable by an agent. After this phase the agent runs one command per slice, every step is an atomic callback
with its own plan, apply and read-back, and the chain is proven by linked evidence rows bound to the installed
runtime's checksum. The observable outcome for the user: a specification goes in, an ISO comes out of the output
directory with its digest recorded, the host boots from it, and the node reads Ready and prepared.

## Diagram

    agent ── ocp_node_onboard.py <verb> --spec S --output-dir D ──► WorkflowAction (plan | apply)
                                                                          │
                       one DeliveryState-derived context, bound once by Callbacks.bind()
                                                                          │
      K8SAuth ─► ReadMachineSpec ─► ReadBootImage ─► ResolvePullSecret ─► BuildNodeImage ─► ServeNodeImage
      (slice 1)        │ state.spec     │ state.boot_image     │ state.pull_secret_ref  │ state.iso   │ state.served_url
                       └──────────────────────────────────────────────────────────────────────────────┘
                                                        ▼ (the user boots the host; outside the API)
      WaitForJoiner ─► ApproveJoinRequests ─► ReadNodeState ─► RemoveServing          (slice 2)
          │ state.joiner        │ state.approved          │ state.node
      PrepareForCI ─► VerifyPrepared                                                   (slice 3)
                                                                          │
                                   cleanup in reverse order through RunScope-owned ResourceSet
                                                                          │
                                      shared report ─► JSON result, sanitized receipt

Data flow runs left to right through named state fields; control flow is the executor's `collect → run → update`
per callback and reverse `cleanup`; construction dependency is the callback order declared by the thin main. The
boot itself is the user's action on their hypervisor or hardware and crosses the system boundary: the combo
records what to mount and waits for the joiner.

## Implementation Plan

### Current State and Intended Outcome

NOT_IMPLEMENTED: the chain as a whole. Inspected at main `e5fb154` (documentation only for this phase). The skill
has: the callback, state and executor abstractions (`core/abstract`, `core/executor.py`); the Kubernetes provider
base `K8SCallback`, `K8SAuth` and `ResourceSet` ownership (`core/kubernetes/`); a temporary Job with cleanup on every
exit (`PullImage` in `core/kubernetes/runner.py`); namespace creation (`EnsureNamespace` in `core/toolbox_actions.py`);
pull-secret handling as `dockerconfigjson` Secrets (`core/kubernetes/runner.py`); read-only Kubernetes collectors
with identity and read-back; the plan, apply and read-back adapter `WorkflowAction`; portable receipts and the
functional checksum (`core/provenance.py`). Nothing reads machine configurations, builds or serves an ISO, waits
for a joiner, approves certificate requests, or prepares a node. CI11 plans `ocp_machine.py`, `ocp_iso.py`,
`ocp_csr.py` and `harbor_pull_secret.py` as separate commands; this phase delivers their behaviour as callbacks
and one composed command, and the CI11 rows remain the owners of the single-command verbs.

Intended outcome: eight requirements, three pull requests, three waves, one command with the verbs `plan`,
`build`, `join` and `prepare`, each callback reusable by other chains (the image-build readiness probe reuses the
run scope; the GitLab runner manager reuses the serving pattern).

### Design and Decomposition

One concrete state. `OnboardState(DeliveryState)` adds the named fields the diagram shows: `spec`, `reference`,
`run_scope`, `boot_image`, `pull_secret_ref`, `iso`, `served_url`, `joiner`, `approved`, `node`. Every callback
reads earlier fields and writes its own; the fields are the only hand-off.

One action. `OnboardAction(SkillAction)` is a frozen dataclass with the specification path, the output directory,
the optional node name, the timeouts, the image digests, and the apply flags; `WorkflowAction` wraps it for plan,
confirm-plan and apply exactly as the toolbox and runner commands do. Every environment specific arrives here as an
argument or through the target resolver; none is hardcoded.

Callbacks, each a `K8SCallback[OnboardAction]` unless stated, each owning its hooks only:

- `ReadMachineSpec` (`LocalCallback`): `collect` loads and schema-validates the specification and the reference,
  `run` compares them field by field and writes `state.spec` and the drift rows. No cluster access.
- `ReadBootImage`: `run` reads the published boot-image stream and writes `state.boot_image` (URL, digest,
  version); `update` records that install must use this version, not the running nodes' version.
- `ResolvePullSecret`: `run` reads the cluster pull secret into a private per-run file (mode 0600) and writes
  `state.pull_secret_ref` as name and digest; `cleanup` removes the file. The value never enters state or report.
- `BuildNodeImage`: `collect` renders the node configuration from `state.spec`; `run` creates the build Job in the
  run-scoped namespace, waits within the timeout, copies the ISO to the output directory; `update` reads the file
  back (exists, digest, size) and writes `state.iso`; `cleanup` removes the Job through the owned `ResourceSet`.
- `ServeNodeImage`: `run` creates the serving Pod and Service in the run scope; `update` reads the URL back and
  writes `state.served_url`; its removal is `RemoveServing`, a separate callback, because the serving must outlive
  the build run and end only after the join.
- `WaitForJoiner`: `run` polls for the node object within the join timeout and writes `state.joiner`.
- `ApproveJoinRequests`: `collect` lists pending requests and selects by planned name and signer; `run` approves
  exactly those; `update` reads each back and loops, within the deadline, until the joiner's serving request is
  approved and issued and the node reads Ready; any other signer is reported. NO_OP when nothing is pending and
  the node is already Ready.
- `ReadNodeState`: `run` reads the node and compares role, OS version and kernel with the reference.
- `RemoveServing`: `run` deletes the serving resources with absence read-back.
- `PrepareForCI` and `VerifyPrepared`: apply the labels and taints the specification declares with read-back, then
  run the existing read-only views for verification.

Reuse: `K8SAuth` first; `EnsureNamespace` for the run-scoped namespace; `ResourceSet` for everything created;
the temporary-Job pattern of `PullImage` for the build Job; `APPLIED` and `NO_OP` from `lib/python/sys/status.py`;
the existing report renderer and portable receipt writer.

#### When to Decompose

Three slices, because they have independent reasons to change and independent proofs: slice 1 (reads and the ISO)
is provable with nothing but the cluster and an output directory; slice 2 (join) needs a host that boots; slice 3
(prepare) depends on what the specification declares for CI. Each is one pull request with its own live receipt.

#### When to Introduce Abstraction

One new abstraction is justified: `RunScope` in `core/kubernetes/run_scope.py`. Common behaviour: generate a random
run identifier, derive the namespace, resource names and labels from it, own them through a `ResourceSet`, and
remove everything carrying the identifier on cleanup with absence read-back. Consumers: this phase's build and
serving callbacks, the image-build readiness probe (chain 11), and the GitLab runner manager (block 7). It removes
the duplicated naming and cleanup logic those three would otherwise each carry, and it enforces the rule that no
temporary name is chosen by hand. Mechanism: a small module with one dataclass and two functions; no hierarchy, no
factory. The existing design is otherwise sufficient: callbacks subclass the provider base, the executor stays as
it is, and no new base class is introduced.

`MachineSpec` is a typed loader with a JSON schema under `ci-skills/schemas/`, not an abstraction: one consumer,
one shape, validated at `collect`.

### Open Decisions and Scope Boundary

- BLOCKER, missing authority: the project has no `.coordination/roadmap.md`, `.coordination/phases.md` or
  `.coordination/priorities.md`. Correction: the project owner creates them or names the authority this phase
  derives from; until then the phase identity `CI12` and the priority of this work are proposals.
- Phase identity: `CI12` and the document path follow the existing `CI01` to `CI11` convention; the owner approves.
- Where the ISO build runs: in-cluster, decided, because a workstation build cannot reach the host's network and
  streams gigabytes over a slow tunnel; recorded here, not an open question.
- The live-ISO alternative (a hand-built Ignition document installed from the stock live ISO) is out of scope; no
  planned verb exists for it.
- The host's boot is the user's action; automating the management controller is out of scope for this phase.

The boundary is the three slices above. A fourth pull request for the single-command CI11 verbs is a separate
proposal.

## PR Order

PR-CI12-1 (wave W-CI12-1) delivers the run scope, the specification loader, the boot-image and pull-secret reads,
the ISO build and serving, and the thin main with the `plan` and `build` verbs; its live proof is an ISO read back
from the output directory. PR-CI12-2 (wave W-CI12-2) depends on it for the state fields, the run scope and the
serving to remove, and delivers the `join` verb: the joiner wait, certificate approval, node state and serving
removal; its live proof needs a host on the bench. PR-CI12-3 (wave W-CI12-3) depends on a Ready node and delivers
the `prepare` verb. Each pull request leaves the installed command coherent under the existing gates, with the
unimplemented verbs absent from the catalogue rather than stubbed.

## Gates

The traceability map in `Authority` is the canonical map. `G-CI12-STATIC` is the existing validate workflow and
the local pre-commit gate; its enforcement exists through the required status on main. `G-CI12-LIVE` is the live
smoke of each slice from the installed candidate on the user's live smoke setup, with a sanitized receipt per step
bound to the functional checksum; it is proposed, and becomes enforced when the checker's required operation set
lists the new operations. `G-CI12-GATE` is the existing live-acceptance step, enforced for the current receipt set
and proposed for the new one. No requirement claims verification until a receipt exists; every row above records
`UNVERIFIED` for that reason.

## Live Smoke Evidence

Planned, not observed. Slice 1: the command run from the installed candidate against the live cluster with a
specification the agent passes and an output directory it chooses; evidence is the receipt's rows plus the ISO's
digest and size read back from that directory. Slice 2: the same host booted from the served ISO, the joiner seen,
the requests approved by name and signer, the node Ready with the expected role; evidence is the receipt and the
node read-back. Slice 3: the prepared node verified by the existing read-only views. All receipts are sanitized
with the portable writer and carry the functional checksum of the installed candidate.

## End-to-End Test

The consumer journey: the agent passes a specification and an output directory, runs `plan`, `build`, `join` and
`prepare` in order, and the final read-back is a Ready node with the declared role, labels and taints. The
proposed test is the three live receipts linked by the same run identifier and the same checksum; its acceptance
gate is `G-CI12-LIVE`. No execution is recorded yet.
