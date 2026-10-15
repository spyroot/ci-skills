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
- the tool catalogue [`CI11-TOOLS`](CI11-TOOLS.md), rows `ocp_machine.py` (owner `core/ocp_machines.py`, which
  compares the cluster's machines against a declared specification), `ocp_iso.py` (`core/ocp_iso.py`, verbs
  `rhcos-report`, `node-build`, `node-serve`, `delete`, whose confirmed plan names the namespace, the
  ClusterRoleBinding, the pull-secret copy and the NodePort Service) and `ocp_csr.py` (`core/ocp_csr.py`), which
  already plan the machine comparison, the ISO verbs and certificate approval as single commands; none of the
  three modules exists at main `e5fb154`;
- the six-row evidence model in [`docs/gitlab-toolbelt-plan.md`](../gitlab-toolbelt-plan.md) block 7, which
  defines how a chain is proven;
- the [contribution guide](../contribution_guide.md), sections 2 (one atomic callback), 3 (compose a thin
  workflow; for a domain other than the toolbox, reuse that domain's action adapter) and 4 (declare the command in
  `core/catalog.py` and regenerate `tools.json`), which every step below is written against;
- the live-acceptance gate: policy [`inventory/ci/live-acceptance.json`](../../inventory/ci/live-acceptance.json),
  checker [`tools/check_live_acceptance.py`](../../tools/check_live_acceptance.py), and the rerun procedure in
  [`README.md`](../../README.md#live-acceptance-reruns), which together decide which receipts the gate accepts and
  when the whole set must be refreshed;
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
        deliverable: One machine specification is loaded and validated against a schema, the target pool's machine objects are read from the cluster and compared with it (role, image, network settings), and drift is reported per field.
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
        deliverable: The cluster's boot image is read from its published stream (URL and digest), and the cluster pull secret is resolved by name and digest and copied into the run namespace as a dockerconfigjson Secret owned by the scope and removed on every exit, without printing any value.
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
        deliverable: A self-installing node ISO is built inside the cluster from the release and a node configuration rendered from the specification, written to the agent-chosen ISO directory and read back by digest and size; it is served in-cluster through a NodePort Service, the serving read back, and the serving kept across a successful build so the host can boot from it.
        owner: ci-skills/lib/python/core/ocp_iso.py
        preserve: [The build Job and the pull-secret copy are removed on every exit; the ISO file persists; serving, the ServiceAccount, the ClusterRoleBinding and the namespace are removed on a failed build and kept on a successful one]
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
        deliverable: Every temporary name (namespace, ServiceAccount, ClusterRoleBinding, Secret, Job, Pod, Service, labels) derives deterministically from one random run identifier generated after confirmation; a later verb reconstructs the same scope from that identifier alone; a removal verb keyed by the identifier deletes everything carrying it, cluster-scoped objects included, with absence read-back and without waiting for a joiner; the ISO and the ISO directory are never removed.
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
        deliverable: A joining node's certificate signing requests are approved by a digest-stable selector (the joiner's identity and the two signers) over the pending list, re-collected until the joiner's serving request reads back approved and issued and the node reads Ready within a deadline; any other signer is reported, never approved.
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
        deliverable: Given the run identifier of a build, the combo waits for the joiner after the host boots from the served ISO, reads the node back Ready with its role (OS version and kernel recorded for the report), and removes the serving resources and their namespace with absence read-back.
        owner: ci-skills/lib/python/core/ocp_join.py
        preserve: [No interaction with the host's management controller is implemented; the boot is the user's action; a joined node is never deleted]
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
        deliverable: One thin main composes the callbacks through an OpenShift action adapter over WorkflowExecutor, declares the command in the catalogue source with a result schema and regenerates tools.json, routes in SKILL.md, renders the shared report, and extends the live-acceptance checker with a validator, a setup context and the required operations for the OpenShift receipt kind, so each slice's sanitized receipt is accepted at the functional checksum.
        owner: ci-skills/bin/ocp_node_onboard.py
        preserve: [The catalogue and parser accept the same verbs and options; --describe and --help work without credentials; the existing required GitLab operations and their freshness policy stay as they are]
        prIds: [PR-CI12-1, PR-CI12-2]
        gateIds: [G-CI12-STATIC, G-CI12-LIVE, G-CI12-GATE]
        evidence: []
    pullRequests:
      - id: PR-CI12-1
        requirementIds: [R-CI12-001, R-CI12-002, R-CI12-003, R-CI12-004, R-CI12-007]
        dependsOn: []
        waveId: W-CI12-1
        owns: [ci-skills/lib/python/core/kubernetes/run_scope.py, ci-skills/lib/python/core/ocp_machines.py, ci-skills/lib/python/core/ocp_iso.py, ci-skills/lib/python/core/ocp_onboard.py, ci-skills/lib/python/core/catalog.py, ci-skills/bin/ocp_node_onboard.py, ci-skills/schemas/machine-spec.schema.json, ci-skills/schemas/results/ocp-node-onboard.schema.json, ci-skills/tools.json, ci-skills/SKILL.md, tools/check_live_acceptance.py, tests/python/test_live_acceptance.py, tests/python/test_run_scope.py, tests/python/test_ocp_node_onboard.py, tests/acceptance/receipts/ocp-node-build-applied.json, .github/workflows/validate.yml, lib/bash/automation/bless.bash, README.md]
      - id: PR-CI12-2
        requirementIds: [R-CI12-005, R-CI12-006, R-CI12-007]
        dependsOn: [PR-CI12-1]
        waveId: W-CI12-2
        owns: [ci-skills/lib/python/core/ocp_csr.py, ci-skills/lib/python/core/ocp_join.py, ci-skills/lib/python/core/ocp_onboard.py, ci-skills/lib/python/core/catalog.py, ci-skills/bin/ocp_node_onboard.py, ci-skills/schemas/results/ocp-node-onboard.schema.json, ci-skills/tools.json, ci-skills/SKILL.md, tools/check_live_acceptance.py, tests/python/test_live_acceptance.py, tests/python/test_ocp_join.py, tests/acceptance/receipts/ocp-node-join-applied.json, tests/acceptance/receipts/ocp-node-join-no_op.json, README.md]
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
        command: ./bless.sh --all locally; the validate workflow on the pull request head (static gate, docstrings, manifests and schemas including the catalogue render check, unit tests, installer fixtures, endpoint gate, aggregated required result)
        environment: GitHub Actions on the pull request head; ruleset "protected" (id 25130553) on main requires the ci-skills/required status with the strict up-to-date policy
        enforcement: Existing
        evidence: [Exact head SHA, validate run URL, every job green, ci-skills/required SUCCESS, the new schemas listed in the metaschema step and selected by bless.bash, tools.json regenerated from the catalogue source]
      - id: G-CI12-LIVE
        requirementIds: [R-CI12-001, R-CI12-002, R-CI12-003, R-CI12-004, R-CI12-005, R-CI12-006, R-CI12-007]
        command: ocp_node_onboard.py <verb> ... --apply --confirm-plan <digest> --json --receipt-out tests/acceptance/receipts/<kind>-<verb>-<action>.json, run from the installed candidate, followed by the README rerun procedure for every other committed receipt
        environment: The user's live smoke setup against the live cluster; the host booted by the user between build and join
        enforcement: Proposed; a receipt is structurally accepted when its file sits in a declared receipt directory, its kind, operation and result action are in the checker's required operation set, its functional checksum matches the candidate, it passes the validator for its kind, and it binds to exactly one setup context; today every accepted receipt passes the GitLab validator and binds to a gitlab_access context, so the OpenShift kind needs its own validator, a kubernetes context from the access-check receipt's kubernetes surface and a match rule
        evidence: [One receipt per verb and result action, each row's read-back (ISO digest and size from the ISO directory, joiner name, approved request names and signers, node Ready), the run identifier shared by the build and join receipts, and every other committed receipt refreshed at the same functional checksum]
      - id: G-CI12-GATE
        requirementIds: [R-CI12-007]
        command: python tools/check_live_acceptance.py --root . --json
        environment: The live_acceptance step of the validate job, on the committed receipts
        enforcement: Existing for the current operation set; the OpenShift operations enter the required set in the same pull request that commits their receipts, and because each pull request changes the functional checksum, that pull request also refreshes every other committed receipt, so the live run precedes the merge
        evidence: [Checker PASS on the pull request head with the new receipts in its accepted list, no receipt_not_declared and no functional_digest_mismatch problem]
<!-- /architecture-map:v1 -->

## Consumer

The consumer is an agent, a Codex or Claude session, acting for a user who installed ci-skills and asked for help
onboarding a new host into their OpenShift cluster. The agent calls the installed command with the user's machine
specification and an ISO directory, and receives a structured result: the evidence rows of the chain, the ISO on
disk, the run identifier to hand to the next verb, and finally a node that reads Ready with the role the
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
runtime's checksum. The observable outcome for the user: a specification goes in, an ISO comes out of the ISO
directory with its digest recorded, the host boots from it, and the node reads Ready.

## Diagram

    agent ─ ocp_node_onboard.py build --spec S --iso-dir D ──► OnboardWorkflow (prepare → callbacks → execute)
                                                                    │ mode: --dry-run | --live-plan | --apply --confirm-plan
              OnboardState(DeliveryState), bound once; state.run_scope = RunScope.new() after confirmation
                                                                    │
      K8SAuth ─► LoadMachineSpec ─► ReadMachinePool ─► ReadBootImage ─► OpenRunScope ─► ResolvePullSecret
                     │ state.spec        │ state.pool        │ state.boot_image   │ namespace, SA,   │ state.pull_secret_ref
                                                                                  │ binding          │ (Secret in namespace)
      ─► BuildNodeImage ─► ServeNodeImage
             │ state.iso        │ state.served_url
      cleanup, reverse: Secret and Job always;
                       Pod and Service (ServeNodeImage), SA, binding and namespace (OpenRunScope) only when state.failed
                                                        ▼ result and receipt carry run_id, namespace, served_url
                                                        ▼ (the user boots the host; outside the API)
    agent ─ ocp_node_onboard.py join --run-id <run_id> [--node N] ──► OnboardWorkflow
              state.run_scope = RunScope.from_id(run_id)
      K8SAuth ─► WaitForJoiner ─► ApproveJoinRequests ─► ReadNodeState ─► RemoveServing
                     │ state.joiner        │ state.approved         │ state.node        │ absence read-back
    agent ─ ocp_node_onboard.py remove --run-id <run_id> ──► K8SAuth ─► RemoveServing     (recovery; no joiner wait)
                                                                    │
                                      shared report ─► JSON result, sanitized receipt (schema ocp-node-onboard)

Data flow runs left to right through named state fields; control flow is the executor's `collect → run → update`
per callback and reverse `cleanup` for every entered callback on success and on failure; construction dependency
is the callback order the adapter declares per verb. The hand-off between the verbs is the run identifier:
`build` returns it, the agent passes it to `join` or `remove`, and the scope is reconstructed from it. The boot
itself is the user's action on their hypervisor or hardware and crosses the system boundary: the combo records
what to mount and waits for the joiner.

## Implementation Plan

### Current State and Intended Outcome

NOT_IMPLEMENTED: the chain as a whole. Inspected at main `e5fb154` (documentation only for this phase). The
relevant tree, with the files this phase adds marked NEW:

    ci-skills/
      bin/
        ocp_node_onboard.py                      NEW  thin main: build, join, remove
      lib/python/core/
        abstract/                                     callback lifecycle, executor, state, action
        executor.py                                   WorkflowExecutor(context, Callbacks([...]))
        delivery_state.py                             DeliveryState, ProviderAuthAction
        catalog.py                                    the catalogue source; tools.json is rendered from it
        cli.py                                        shared parser; --output-dir means paired report output
        toolbox_actions.py                            WorkflowAction, EnsureNamespace (toolbox domain)
        provenance.py                                 functional checksum
        portable.py                                   portable receipts
        gitlab/gitlab_action.py                       GitLabWorkflow: the domain action adapter this phase mirrors
        kubernetes/kubernetes.py                      K8SCallback, K8SAuth
        kubernetes/resources.py                       ResourceSet, OPERATION_LABEL
        kubernetes/runner.py                          PullImage temporary Job, dockerconfigjson Secret copy
        kubernetes/run_scope.py                  NEW  RunScope over ResourceSet; OpenRunScope
        ocp_machines.py                          NEW  CI11 owner; LoadMachineSpec, ReadMachinePool
        ocp_iso.py                               NEW  CI11 owner; ReadBootImage, ResolvePullSecret, BuildNodeImage, ServeNodeImage, RemoveServing
        ocp_csr.py                               NEW  CI11 owner; ApproveJoinRequests
        ocp_join.py                              NEW  WaitForJoiner, ReadNodeState
        ocp_onboard.py                           NEW  OnboardState, OnboardAction, OnboardWorkflow
      schemas/
        machine-spec.schema.json                 NEW
        results/ocp-node-onboard.schema.json     NEW
      lib/python/sys/status.py                        PASS, DRY_RUN, PLANNED, PARTIAL, BLOCKED, APPLIED, NO_OP
    tools/check_live_acceptance.py                    required operation set, GATE_SURFACES, the GitLab receipt validator
    tools/render_manifest.py                          renders tools.json; --check fails on a stale copy
    inventory/ci/live-acceptance.json                 declared receipt directories, freshness
    tests/acceptance/receipts/                        committed sanitized receipts, all bound to one functional checksum

CI topology, from `.github/workflows/validate.yml`, existing elements plain and proposed changes marked `+`:

    pull request head (exact SHA)
      ├─ validate ────────────────┐ static gate → docstrings → manifests and schemas (render check of tools.json;
      │                           │ metaschema list + ocp-node-onboard result schema, + machine-spec schema)
      │                           │ → unit tests (tests/python; + test_run_scope, + test_ocp_node_onboard, + test_ocp_join)
      │                           │ → offline acceptance tests (test_live_acceptance; + OpenShift kind cases)
      │                           │ → live_acceptance: checker over the committed receipts (+ OpenShift validator and context)
      ├─ gate-ci-skills-endpoints ┤ endpoint declarations, unchanged
      ├─ test-install ────────────┤ installer fixtures and toolchain schema, unchanged
      │        each job writes its evidence file (artifact) ▼
      └─ required-result ─────────┴─► aggregates the three evidence files → publishes the ci-skills/required status
                                          │ ruleset "protected": ci-skills/required SUCCESS on the up-to-date head, else no merge

The three jobs run in parallel and share nothing but the head; `required-result` consumes their evidence
artifacts and is the only producer of the required status, so a red step in any job stops advancement there. The
local pre-commit gate `./bless.sh` runs the same whitespace, JSON, schema, markdown, shell, Python and endpoint
checks on the working tree; its schema selector matches only `schemas/*.json` today, so the pairing for the new
schemas widens that selector to `ci-skills/schemas/` (the existing result schemas are not metaschema-checked
locally either; the widening covers them too).

Nothing reads machine specifications, builds or serves an ISO, waits for a joiner or approves certificate
requests. CI11 plans `ocp_machine.py`, `ocp_iso.py` and `ocp_csr.py` as single commands owned by
`core/ocp_machines.py`, `core/ocp_iso.py` and `core/ocp_csr.py`; none exists. This phase creates those three
modules with the callbacks the chain needs, so each path has one owner: CI11 keeps the single-command verbs on
top of the same callbacks, and CI12 owns the composition. The command is declared in `core/catalog.py`, the single
source from which `tools.json`, `--help` and `--describe` are rendered; a hand-edited `tools.json` fails the
render check in the validate workflow.

The checker today accepts a receipt only when its kind, operation and result action are in its required
operation set, its functional checksum equals the candidate's, it passes the GitLab receipt validator (`origin`,
identity, credential digest, `verified_target`), and it binds to exactly one setup context built from a
`gitlab_access` receipt; anything else in a declared directory is `receipt_not_declared` or `kind_unexpected`
and turns the gate red. It also fails when a required operation has no receipt, and reports
`functional_digest_mismatch` for every receipt whose checksum differs from the candidate's, which is every
committed receipt once `ci-skills/bin`, `lib` or `schemas` change. The directory `tests/acceptance/receipts` is
already declared in the policy, so the policy file itself does not change.

Intended outcome: seven requirements, two pull requests, two waves, one command with the verbs `build`, `join`
and `remove` sharing the mode flags of the GitLab actions (`--dry-run`, `--live-plan`, `--apply --confirm-plan`),
each callback reusable by other chains, and the run scope reusable by the image-build readiness probe (chain 11)
and the GitLab runner manager (block 7).

### Design and Decomposition

One concrete state. `OnboardState(DeliveryState)` adds the named fields the diagram shows: `spec`, `pool`,
`run_scope`, `boot_image`, `pull_secret_ref`, `iso`, `served_url`, `joiner`, `approved`, `node`. Every callback
reads earlier fields and writes its own; the fields are the only hand-off.

One action. `OnboardAction(SkillAction)` is a frozen dataclass with `spec_path`, `iso_dir` (the ISO destination;
distinct from the shared `--output-dir`, which keeps its one meaning, paired report output), `run_id` (required
by `join` and `remove`, generated by `build`), `node` (optional; otherwise the joiner is discovered by the hostname
the specification declares), `build_timeout`, `join_timeout`, `approve_timeout`, `overwrite`, and the shared mode
flags. Every environment specific arrives here as an argument or through the target resolver; none is hardcoded.

One adapter. `OnboardWorkflow` in `core/ocp_onboard.py` mirrors `GitLabWorkflow` in `core/gitlab/gitlab_action.py`:
`prepare()` validates the mode, resolves the Kubernetes target (offline for `--dry-run`), and builds the plan and
its digest from the caller's inputs only: the specification bytes, the ISO directory, the timeouts, the node and
the run identifier when the verb takes one. The run identifier of a `build` is not a caller input: it is generated
after the confirmation succeeds, so the digest of `--dry-run` or `--live-plan` is reproduced by the apply, and the
plan shows the name pattern while the apply result shows the names. `prepare()` then constructs the run scope
(`RunScope.new()` after confirmation for `build`, `RunScope.from_id(run_id)` for `join` and `remove`);
`callbacks()` returns `K8SAuth` followed by the verb's sequence, with a confirmation callback before any apply;
`execute()` runs the executor and renders the shared report; the result validates against
`schemas/results/ocp-node-onboard.schema.json`. The toolbox adapter `WorkflowAction` is not reused: it parses
`--spec` as a toolbox specification and requires a toolbox catalogue kind, as the contribution guide states; its
mode handling and report shape are copied, not its class. `EnsureNamespace` is not reused: it is bound to the
toolbox action and preserves a namespace that already exists, while a run-scoped namespace must not pre-exist and
is removed by the scope; its mechanism, a cluster-scoped `ResourceSet(service, "", operation)` applying the
Namespace document, is copied.

Callbacks, each a `K8SCallback[OnboardAction]` unless stated, each owning its hooks only, with their module:

- `LoadMachineSpec` (`LocalCallback`, `core/ocp_machines.py`): `collect` loads and schema-validates the
  specification and writes `state.spec`. No cluster access.
- `ReadMachinePool` (`core/ocp_machines.py`): `run` reads the target pool's machine objects and compares role,
  image and network settings with `state.spec`, as CI11's `ocp_machine.py --node-spec` defines the comparison;
  `update` writes `state.pool` and the drift rows.
- `ReadBootImage` (`core/ocp_iso.py`): `run` reads the published boot-image stream and writes `state.boot_image`
  (URL, digest, version); `update` records that install must use this version, not the running nodes' version.
- `OpenRunScope` (`K8SCallback[RunScopeAction]`, `core/kubernetes/run_scope.py`; its action carries the scope and
  the role name, so other chains compose it): `run` applies the Namespace through the scope's cluster-scoped set,
  then the run's ServiceAccount and, when the role argument is set, a ClusterRoleBinding to that role. Which
  workload needs cluster API access (the build Job, the serving Pod, or neither) and the least role that works are
  expectations from CI11's source, pinned by the first live run and recorded in the receipt; both workloads run
  under the run's ServiceAccount. `update` reads the objects back; `cleanup` deletes the binding, the
  ServiceAccount and the namespace when `state.failed`, each with absence read-back, and keeps them otherwise, so
  they share the serving's lifetime.
- `ResolvePullSecret` (`core/ocp_iso.py`): `run` reads the cluster pull secret and copies it into the run namespace
  as a `dockerconfigjson` Secret owned by the scope, the pattern of `PullImage`; `update` writes
  `state.pull_secret_ref` as name and digest; `cleanup` deletes the Secret on every exit. The value never enters
  state, report or disk.
- `BuildNodeImage` (`core/ocp_iso.py`): `collect` renders the node configuration from `state.spec`; `run` creates
  the build Job in the run namespace under the run's ServiceAccount, waits within `build_timeout`, copies the
  ISO to the ISO directory (blocked when the file exists and `overwrite` is unset); `update` reads the file back
  (exists, digest, size) and writes `state.iso`; `cleanup` deletes the Job on every exit with absence read-back.
- `ServeNodeImage` (`core/ocp_iso.py`): `run` creates the serving Pod and the NodePort Service in the scope;
  `update` reads the URL back and writes `state.served_url`; `cleanup` reads `state.failed`: when the run failed
  it removes the Pod and the Service with absence read-back (the namespace is `OpenRunScope`'s to remove), when it
  succeeded it leaves them and the result records `run_id`, the namespace and the URL so the host can boot.
- `WaitForJoiner` (`core/ocp_join.py`): `run` polls for the node object (by `node` or the declared hostname)
  within `join_timeout` and writes `state.joiner`.
- `ApproveJoinRequests` (`core/ocp_csr.py`): the plan carries a selector, the joiner's identity and the two
  signers (client, then serving), which is digest-stable because request names are not known in advance; `collect`
  lists the pending requests matching the selector; `run` approves exactly those; `update` reads each back and
  re-collects, within `approve_timeout`, because the serving request appears only after the client request is
  approved, until the joiner's serving request is approved and issued and the node reads Ready; any other signer
  is reported. The selector never widens; the set of requests it matches grows by one. `NO_OP` when nothing is
  pending and the node is already Ready.
- `ReadNodeState` (`core/ocp_join.py`): `run` reads the node back: Ready, role, with OS version and kernel
  recorded in the report.
- `RemoveServing` (`core/ocp_iso.py`): `run` deletes the scope's Pod, Service and ServiceAccount through the
  namespaced set, then the ClusterRoleBinding and the namespace through the cluster-scoped set, with absence
  read-back; `NO_OP` when they are already absent. `join` runs it last; `remove` runs it alone, without a joiner
  wait.

Reuse: `K8SAuth` first in every verb; `ResourceSet` for everything created; the temporary-Job and Secret-copy
pattern of `PullImage`; the statuses from `ci-skills/lib/python/sys/status.py`; the existing report renderer and
the portable receipt writer in `core/portable.py`; the catalogue declaration and render tool.

Serving lifetime, decided: the serving resources have one owner, the run identifier, and one lifetime, from a
successful `build` to the end of `join` or `remove`. The mechanism is the executor's contract: cleanup runs for
every entered callback on success and on failure, and `state.failed` is set before cleanup on failure, so
`ServeNodeImage.cleanup` can keep the Pod and Service on success and remove them on failure, and
`OpenRunScope.cleanup` does the same for the namespace, the ServiceAccount and the binding it applied. Cleanup
runs only for entered callbacks, so a
build that fails before serving starts leaves no namespace behind, because its own callback removes it. One edge
remains: when a cleanup hook fails after the serving was already kept, the executor marks the run failed only
then, the serving stays, and the result is `BLOCKED` with the run identifier; `remove --run-id` is the recovery.
`join` and `remove` reconstruct `ResourceSet(namespace, operation=run_id)` from `--run-id`, and `RemoveServing`
deletes only what carries that label. No discovery by any other label, and a new run never changes or deletes
another run's objects, because `ResourceSet` refuses to modify an existing object whose operation label differs.

Concrete statuses, from the shared adapter rule: `--dry-run` returns `DRY_RUN` in phase `OFFLINE_PLAN` with the
plan digest and no API call; `--live-plan` returns `PLANNED` in phase `LIVE_PLAN` after `K8SAuth` reads the
identity back; `--apply --confirm-plan <digest>` returns `PASS` with `APPLIED` or `NO_OP` rows when the operation
record is verified, has no errors and its own cleanup field is neither `BLOCKED` nor `PARTIAL`, and `PARTIAL`
otherwise; a cleanup hook that raises is rendered as `BLOCKED` with `cleanup.status` `BLOCKED`.

#### When to Decompose

Two slices, because they have independent reasons to change and independent proofs: `build` (reads and the ISO,
with `remove` as its recovery) is provable with nothing but the cluster and an ISO directory; `join` needs a host
that boots. Each is one pull request with its own live receipts.

#### When to Introduce Abstraction

The options for run-scoped naming and cleanup, compared:

- Bare `ResourceSet(operation=<run id>)` with each callback choosing its own names: ownership and cleanup of the
  created set exist today, but identifier generation, deterministic name derivation per kind, the cluster-scoped
  set for the Namespace and the binding, and reconstruction from an identifier would be repeated in every callback
  and in the two other consumers (chain 11 probe, block 7 runner manager).
- Extending `ResourceSet` with those behaviours: couples a general ownership type to naming policy and to the
  `state.failed` keep-or-remove decision, which only this chain needs.
- A small `RunScope` over `ResourceSet`, chosen: one frozen dataclass (`run_id`, `namespace`, `labels`), two
  constructors (`RunScope.new()`, `RunScope.from_id(run_id)`), `scope.name(kind)` for deterministic names,
  `scope.resources(service)` for the namespaced set and `scope.cluster_resources(service)` for the cluster-scoped
  set (Namespace, ClusterRoleBinding), both bound to the operation label, and one callback, `OpenRunScope`, with
  its own small action (`RunScopeAction`: the scope and an optional role), that applies the namespace, the
  ServiceAccount and the binding. No hierarchy, no factory. The adapter's `prepare()`
  constructs the scope and stores it in `state.run_scope`; callbacks only read it. Consumers: this phase's
  callbacks, the image-build readiness probe and the GitLab runner manager.

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
    - standards-binding.yaml (pin 56a579c); the contract was added at standards f39203a (2026-10-13) and the checker at a2256a8 (2026-10-14)
    Observation:
    - At the pin the contract and checker are absent; the checker reports ARCH-INPUT UNAVAILABLE for this project
    Risk:
    - This document's conformance is asserted, not enforced, until the pin moves
    Safe next step:
    - The owner bumps standards-binding.yaml to a revision at or after a2256a8 in its own pull request

Decided, recorded here so no step hides a choice; the owner may overrule any of them:

- Comparison baseline: the cluster's own machine pool, read and compared as CI11's `ocp_machine.py --node-spec`
  defines; no reference file and no capture verb. OS version and kernel of the joined node are recorded, not
  compared, because no recorded authority defines a baseline for them.
- Cross-verb hand-off: the run identifier, generated after confirmation and returned by `build`, passed to `join`
  and `remove` as `--run-id`; the namespace and the served URL are also in the result for the agent's records, but
  the identifier alone reconstructs the scope. It is not part of any plan digest.
- ISO destination: `--iso-dir`, a distinct option, because `--output-dir` already means paired report output in
  the shared parser and a dry run with it is rejected; CI11's `ocp_iso.py` row, which names `--output-dir` for the
  ISO, inherits this rename when its verbs are built.
- Repeated builds: a second `build` with the same inputs is a new run with a new identifier and blocks on an
  existing ISO unless `--overwrite` is passed; CI11's row expects `NO_OP` for a second build with the same inputs,
  and the single-command verb aligns to this rule when it is built.
- Required OpenShift operations: `build` `APPLIED` is required and repeatable, since it needs only the cluster and
  an ISO directory; for `join`, the `NO_OP` receipt (a rerun against the already-joined node) is required and
  repeatable, and the `APPLIED` receipt is captured once at delivery, accepted when present, and moved to the
  historical directory on a later refresh, because an `APPLIED` join needs a host that has not yet joined and the
  gate reruns every 30 days and on every runtime change. The alternatives were the same required set for both
  (a fresh host per rerun) and a separate receipt directory with its own freshness policy (a second policy to
  maintain). The README rerun list gains the OpenShift operations, so both pull requests own `README.md`.
- Ownership against CI11: the callbacks live in the CI11-owned modules whose behaviour they implement, and CI11's
  single-command verbs become thin mains over the same callbacks when they are built; no second module tree.
  `RemoveServing` is delivered by PR-CI12-1 (it is `remove`'s only callback) and composed by PR-CI12-2.
- In-cluster credentials and access: the pull secret is copied into the run namespace as a Secret, never written
  to disk by this combo; the build Job and the serving Pod run under a run-scoped ServiceAccount, with a
  ClusterRoleBinding only if the first live run shows one of them needs cluster API access (CI11's source binds
  cluster-admin to its serving manifest; the least role that works is pinned there and recorded), and the
  ServiceAccount and binding share the namespace's lifetime; the serving Service is a NodePort, so the served
  URL is a node address and port the host must reach.
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

PR-CI12-1 (wave W-CI12-1) delivers the run scope, the specification loader and pool comparison, the boot-image
read, the pull-secret copy, the ISO build and serving, the serving removal, the adapter and the thin main with
the `build` and `remove` verbs, the catalogue declaration with its regenerated `tools.json` and result schema, the
schema pairing in the validate workflow and the local gate, the routing, and the checker extension for the
OpenShift receipt kind with the `build` operation; its live proof is the build receipt with the ISO read back from
the ISO directory. PR-CI12-2 (wave W-CI12-2) depends on it for the state fields, the run scope and the serving to
remove, and delivers the `join` verb: the joiner wait, certificate approval and node state, with the result
schema extended for the join fields and the checker's `join` operations; its live proof needs a host on the
bench. Because the checker fails on a missing required operation as well as on an undeclared receipt, each
operation enters the required set in the same pull request that commits its receipt; and because each pull
request changes the functional checksum, that pull request also refreshes every other committed receipt through
the README rerun procedure on its own head from the installed candidate, and commits only a `PASS` set; those
refreshed receipts, under the declared receipt directories and the historical directory, are the one set of
files a pull request rewrites without listing them in its owned paths, since every runtime-changing pull request
rewrites all of them. The live run precedes the merge, and a head change invalidates every receipt. Each pull
request leaves the installed
command coherent under the existing gates, with the unimplemented verb absent from the catalogue rather than
stubbed.

## Gates

The traceability map in `Authority` is the canonical map. `G-CI12-STATIC` is the existing validate workflow and
the local pre-commit gate; its enforcement is the main ruleset's required `ci-skills/required` status with the
strict up-to-date policy, read back from the repository's branch rules on 2026-10-15. `G-CI12-LIVE` is the live
smoke of each slice from the installed candidate on the user's live smoke setup; it is proposed, and a receipt
counts only when it is structurally accepted: file in a declared receipt directory, kind, operation and result
action in the checker's required set, functional checksum equal to the candidate's, a passing validator for its
kind, and exactly one setup context to bind to; for OpenShift receipts the validator and the context exist only
after the extension adds them. `G-CI12-GATE` is the existing live-acceptance step, enforced for the current
receipt set and extended by each pull request for its own operations, with the whole receipt set refreshed at
that pull request's checksum.

Per-requirement gaps today, all of the same kind: no module, no unit test and no receipt exists for any of
`R-CI12-001` to `R-CI12-007`, so every row records `UNVERIFIED`. The specific evidence each needs: `R-CI12-001`
a loader test (valid, invalid) and a pool comparison test (match, drift per field); `R-CI12-002` a receipt row
with the stream digest and the Secret's name and digest and no value, and the Secret absent afterwards;
`R-CI12-003` the ISO digest and size read back and the serving URL; `R-CI12-004` a scope test (names derive from
the identifier, `from_id` reproduces them, removal deletes exactly the owned set including the cluster-scoped
objects) and the namespace, ServiceAccount and binding absent after `join` or `remove`; `R-CI12-005` an
approval-loop test against a
two-request sequence, a foreign signer and an expired deadline, and the receipt's approved names and signers;
`R-CI12-006` the joiner name and the node Ready read-back; `R-CI12-007` the checker's accepted list naming the new
receipts with every other receipt refreshed.

## Live Smoke Evidence

Planned, not observed. `build`: the command run from the installed candidate against the live cluster with a
specification the agent passes and an ISO directory it chooses; evidence is the receipt's rows plus the ISO's
digest and size read back from that directory and the run identifier. `join`: the same host booted from the
served ISO, the joiner seen, the requests approved by selector, the node Ready with the expected role, the
serving and its namespace absent; evidence is the receipt carrying the same run identifier and the node
read-back, then a rerun's `NO_OP` receipt. All receipts are sanitized with the portable writer and carry the
functional checksum of the installed candidate, and every other committed receipt is refreshed at that checksum.

## End-to-End Test

The consumer journey: the agent passes a specification and an ISO directory, runs `build`, hands the returned
run identifier to `join` after the user boots the host, and the final read-back is a Ready node with the declared
role and no resource left carrying the identifier. The proposed test is the two live receipts linked by the same
run identifier and the same checksum; its acceptance gate is `G-CI12-LIVE`. No execution is recorded yet.
