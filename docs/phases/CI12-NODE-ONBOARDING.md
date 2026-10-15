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
- the owner's directions of 2026-10-15 on run-scoped names and agent-chosen locations, on CI12 checking the
  generated ISO against the specification with the supported tooling for the selected image, on the certificate
  approval and the node join being the one part of the chain that cannot be tested live (it needs a real host to
  join), so the test reads the pending requests live and mocks the approval and the join on the existing GitHub
  test path, and on adding no new gate and no separate plan; all recorded with their dates in the
  [ExecPlan's Decision Log](../exec-plans/active/ci12-node-onboarding.md#decision-log); the catalogue does not yet
  carry them, so the Decision Log is the cited authority for `R-CI12-004` and `R-CI12-008`;
- the project motivation in [`README.md`](../../README.md#motivation): one combo instead of many hand-rolled
  calls, less context, the same result in one go;
- the tool catalogue [`CI11-TOOLS`](CI11-TOOLS.md), rows `ocp_machine.py` (owner `core/ocp_machines.py`, which
  compares the cluster's machines against a declared specification), `ocp_iso.py` (`core/ocp_iso.py`, verbs
  `rhcos-report`, `rhcos-download`, `rhcos-serve`, `node-build`, `node-serve`, `delete`, whose source builds and
  serves in one Pod and whose
  confirmed plan names the namespace, the ClusterRoleBinding, the pull-secret copy and the NodePort) and
  `ocp_csr.py` (`core/ocp_csr.py`), which already plan the machine comparison, the ISO verbs and certificate
  approval as single commands; none of the three modules exists at main `e5fb154`;
- the six-row evidence model in [`docs/gitlab-toolbelt-plan.md`](../gitlab-toolbelt-plan.md) block 7, which
  defines how a chain is proven, and the owner's rule that only a live-tested head merges;
- the [contribution guide](../contribution_guide.md), sections 2 (one atomic callback), 3 (compose a thin
  workflow; for a domain other than the toolbox, reuse that domain's action adapter) and 4 (declare the command in
  `core/catalog.py`, pair a result schema, regenerate `tools.json`), which every step below is written against;
- the shared command contract every installed command exposes through `--describe`: the shared options
  (`--binding`, `--json`, `--yaml`, `--human`, `--dry-run`, `--revision`, `--log-*`, `--run-id`, `--output-dir`),
  the status values and the exit codes, as `ci-skills/lib/python/core/catalog.py` declares them;
- the live-acceptance gate as it exists: the policy
  [`inventory/ci/live-acceptance.json`](../../inventory/ci/live-acceptance.json) and the checker
  [`tools/check_live_acceptance.py`](../../tools/check_live_acceptance.py); how this command's evidence enters that
  gate is an open question for the owner, recorded below, not a decision of this document;
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
        deliverable: The cluster's boot image is read from its published stream (URL and digest), and the cluster pull secret is resolved by name and digest and copied into the run namespace as a dockerconfigjson Secret owned by the scope, without printing any value.
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
        deliverable: A self-installing node ISO is built inside the cluster from the release and a node configuration rendered from the specification, in the same run-scoped Pod that then serves it through a NodePort Service; the ISO is fetched over the served URL into the agent-chosen ISO directory and read back by digest and size, which also proves the serving, and the whole scope is kept across a successful build so the host can boot from it.
        owner: ci-skills/lib/python/core/ocp_iso.py
        preserve: [Every run-scoped object is removed on a failed build by the callback that created it and kept on a successful one; the ISO file persists]
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
        deliverable: Every temporary name (namespace, ServiceAccount, ClusterRoleBinding, Secret, ConfigMap, Pod, Service, labels) derives deterministically from one random run identifier generated after confirmation; a later verb reconstructs the same scope from that identifier alone and removes everything carrying it, cluster-scoped objects included, with absence read-back; the ISO and the ISO directory are never removed.
        owner: ci-skills/lib/python/core/kubernetes/run_scope.py
        preserve: [ResourceSet ownership semantics and its operation label; the executor's reverse cleanup order; the shared --run-id option's existing meaning as a correlation identifier]
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
        deliverable: A joining node's certificate signing requests are approved by a digest-stable selector (the hostname the specification declares and the two signers) over the pending list, re-collected until the joiner's serving request reads back approved and issued and the node reads Ready within a deadline; any other signer is reported, never approved.
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
        deliverable: Given the run identifier of a build and the same specification, the combo waits for the joiner's first client request after the host boots from the served ISO, drives the approval to a node that reads back Ready with the hostname and role the specification declares (OS version and kernel recorded for the report), and removes the whole scope with absence read-back.
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
        deliverable: One thin main composes the callbacks through an OpenShift action adapter over WorkflowExecutor, declares the command in the catalogue source with a paired result schema and regenerates tools.json, routes in SKILL.md, and renders the shared report, so the agent invokes it like every other installed command and reads the same envelope.
        owner: ci-skills/bin/ocp_node_onboard.py
        preserve: [The shared options, status values and exit codes; --describe and --help work without credentials; the existing gate's required operations and freshness policy]
        prIds: [PR-CI12-1, PR-CI12-2]
        gateIds: [G-CI12-STATIC, G-CI12-LIVE, G-CI12-GATE]
        evidence: []
      - id: R-CI12-008
        authority: [docs/exec-plans/active/ci12-node-onboarding.md, docs/combos.md]
        phaseId: CI12
        classification:
          grounding: GROUNDED
          implementation: NOT_IMPLEMENTED
          verification: UNVERIFIED
        deliverable: The generated ISO is checked against the specification it was built from, the node configuration the build embedded is extracted from the ISO and every declared field (hostname, first NIC hardware address, install device, build interface, role) is compared and reported as match or drift per field, both as the last step of build and as a standalone verb that needs no cluster; on drift the result is PARTIAL and the scope is kept so the agent can decide.
        owner: ci-skills/lib/python/core/ocp_iso.py
        preserve: [The ISO file is read, never rewritten; drift is reported as PARTIAL, never corrected]
        prIds: [PR-CI12-1]
        gateIds: [G-CI12-STATIC, G-CI12-LIVE]
        evidence: []
    pullRequests:
      - id: PR-CI12-1
        requirementIds: [R-CI12-001, R-CI12-002, R-CI12-003, R-CI12-004, R-CI12-007, R-CI12-008]
        dependsOn: []
        waveId: W-CI12-1
        owns: [ci-skills/lib/python/core/kubernetes/run_scope.py, ci-skills/lib/python/core/action_modes.py, ci-skills/lib/python/core/gitlab/gitlab_action.py, ci-skills/lib/python/core/ocp_machines.py, ci-skills/lib/python/core/ocp_iso.py, ci-skills/lib/python/core/ocp_onboard.py, ci-skills/lib/python/core/catalog.py, ci-skills/bin/ocp_node_onboard.py, ci-skills/schemas/machine-spec.schema.json, ci-skills/schemas/results/ocp-node-onboard.schema.json, ci-skills/tools.json, ci-skills/SKILL.md, tests/python/test_run_scope.py, tests/python/test_action_modes.py, tests/python/test_ocp_node_onboard.py, tests/acceptance/receipts/, tests/acceptance/runner-tag/, tests/acceptance/runner-lifecycle/, .github/workflows/validate.yml, lib/bash/automation/bless.bash]
      - id: PR-CI12-2
        requirementIds: [R-CI12-005, R-CI12-006, R-CI12-007]
        dependsOn: [PR-CI12-1]
        waveId: W-CI12-2
        owns: [ci-skills/lib/python/core/ocp_csr.py, ci-skills/lib/python/core/ocp_join.py, ci-skills/lib/python/core/ocp_onboard.py, ci-skills/lib/python/core/catalog.py, ci-skills/bin/ocp_node_onboard.py, ci-skills/schemas/results/ocp-node-onboard.schema.json, ci-skills/tools.json, ci-skills/SKILL.md, tests/python/test_ocp_join.py, tests/acceptance/receipts/, tests/acceptance/runner-tag/, tests/acceptance/runner-lifecycle/]
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
        requirementIds: [R-CI12-001, R-CI12-002, R-CI12-003, R-CI12-004, R-CI12-005, R-CI12-006, R-CI12-007, R-CI12-008]
        command: ./bless.sh --all locally; the validate workflow on the pull request head (static gate, docstrings, manifests and schemas including the catalogue render check, unit tests, installer fixtures, endpoint gate, aggregated required result)
        environment: GitHub Actions on the pull request head; ruleset "protected" (id 25130553) on main requires the ci-skills/required status with the strict up-to-date policy
        enforcement: Existing
        evidence: [Exact head SHA, validate run URL, every job green, ci-skills/required SUCCESS, the new schemas listed in the metaschema step per the existing pairing rule, tools.json regenerated from the catalogue source]
      - id: G-CI12-LIVE
        requirementIds: [R-CI12-001, R-CI12-002, R-CI12-003, R-CI12-004, R-CI12-005, R-CI12-006, R-CI12-007, R-CI12-008]
        command: The build apply, the verify, the remove apply and the join live plan under "Invocation and the result the agent reads", each with --receipt-out, run from the installed candidate against the live cluster; dry runs write no receipt
        environment: The user's live smoke setup against the live cluster for build, verify and remove; for join, a read-only live plan over the pending requests, since the approval and the node join need a real host and are mocked by the owner's decision
        enforcement: Proposed; the live run precedes the merge and its receipts are attached to the pull request; whether and how those receipts are committed into the gate is open question 2 in "Open Decisions and Scope Boundary", because the checker today accepts only its declared GitLab kinds and rejects any other receipt in a declared directory
        evidence: [One receipt per verb and result action, each row's read-back (ISO digest and size from the ISO directory, the per-field match of the ISO against the specification, the pending requests read live by the join plan), the mocked approval and node read-back from the unit tests marked as such, the run identifier shared by the build and join receipts, the functional checksum of the installed candidate]
      - id: G-CI12-GATE
        requirementIds: [R-CI12-007]
        command: python tools/check_live_acceptance.py --root . --json
        environment: The live_acceptance step of the validate job, on the committed receipts
        enforcement: Existing for the current operation set and unchanged by this phase until the owner answers open question 2; until then no receipt of this command is committed under a declared receipt directory, while the existing set is refreshed by the README rerun because each pull request changes the functional checksum
        evidence: [Checker PASS on the pull request head after the README rerun refreshes the existing receipt set at this head's functional checksum; the new receipts enter only after the owner's answer]
<!-- /architecture-map:v1 -->

## Consumer

The consumer is an agent, a Codex or Claude session, acting for a user who installed ci-skills and asked for help
onboarding a new host into their OpenShift cluster. The agent calls the installed command with the user's machine
specification and an ISO directory, and receives the shared result envelope: the evidence rows of the chain, the
ISO on disk checked field by field against the specification, the run identifier to hand to the next verb, and
finally a node that reads Ready with the hostname and role the specification declares. The exact invocations and
the JSON the agent reads are under "Invocation and the result the agent reads".

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
              OnboardState(DeliveryState), bound once; prepare() compares the digest, then state.run_scope = RunScope.new()
                                                                    │
      K8SAuth ─► ConfirmOnboardOperation ─► LoadMachineSpec ─► ReadMachinePool ─► ReadBootImage ─► OpenRunScope
                     │ target re-verified       │ state.spec        │ state.pool        │ state.boot_image  │ namespace, SA, binding
      ─► ResolvePullSecret ─► BuildNodeImage ──────────────────► ServeNodeImage ─► FetchNodeImage ─► VerifyNodeImage
             │ state.pull_secret_ref   │ ConfigMap (node config) + Pod:  │ Service;         │ ISO fetched over   │ embedded config
             │ (Secret in namespace)   │ init container builds into a    │ state.served_url │ served_url into    │ vs state.spec,
                                       │ volume, serving container       │                  │ the ISO directory; │ per field;
                                       │ serves it                       │                  │ state.iso          │ state.iso_check;
                                       │                                 │                  │                    │ cleanup records
                                       │                                 │                  │                    │ serving_kept
      cleanup, reverse: on a failed build each callback removes what it created; on success everything stays (serving_kept)
                                                        ▼ result and receipt carry run_id, namespace, served_url
                                                        ▼ (the user boots the host; outside the API)
    agent ─ ocp_node_onboard.py join --run-id <run_id> --spec S [--node N] ──► OnboardWorkflow
              state.run_scope = RunScope.from_id(run_id)
      K8SAuth ─► ConfirmOnboardOperation ─► WaitForJoiner ─► ApproveJoinRequests ─► ReadNodeState ─► RemoveScope
                                                │ state.joiner (first client request)  │ state.approved  │ state.node   │ absence read-back
    agent ─ ocp_node_onboard.py remove --run-id <run_id> ──► K8SAuth ─► ConfirmOnboardOperation ─► RemoveScope   (recovery)
    agent ─ ocp_node_onboard.py verify --iso P --spec S ──► LoadMachineSpec ─► VerifyNodeImage      (no cluster; any ISO)
                                                                    │
                                      shared report ─► JSON result, sanitized receipt (schema ocp-node-onboard)

Data flow runs left to right through named state fields; control flow is the executor's `collect → run → update`
per callback and reverse `cleanup` for every entered callback on success and on failure; construction dependency
is the callback order the adapter declares per verb. The hand-off between the verbs is the run identifier:
`build` returns it, the agent passes it to `join` or `remove`, and the scope is reconstructed from it. The boot
itself is the user's action on their hypervisor or hardware and crosses the system boundary: the combo records
what to mount and waits for the joiner's first certificate request.

## Implementation Plan

### Current State and Intended Outcome

NOT_IMPLEMENTED: the chain as a whole. Inspected at main `e5fb154` (documentation only for this phase). The
relevant tree, with the files this phase adds marked NEW:

    ci-skills/
      bin/
        ocp_node_onboard.py                      NEW  thin main: build, join, remove, verify
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
        kubernetes/run_scope.py                  NEW  RunScope over ResourceSet; OpenRunScope, RemoveScope
        ocp_machines.py                          NEW  CI11 owner; LoadMachineSpec, ReadMachinePool
        ocp_iso.py                               NEW  CI11 owner; ReadBootImage, ResolvePullSecret, BuildNodeImage, ServeNodeImage, FetchNodeImage, VerifyNodeImage
        ocp_csr.py                               NEW  CI11 owner; ApproveJoinRequests
        ocp_join.py                              NEW  WaitForJoiner, ReadNodeState
        ocp_onboard.py                           NEW  OnboardState, OnboardAction, ConfirmOnboardOperation, OnboardWorkflow
      schemas/
        machine-spec.schema.json                 NEW
        results/ocp-node-onboard.schema.json     NEW
      lib/python/sys/status.py                        PASS, DRY_RUN, PLANNED, PARTIAL, BLOCKED, APPLIED, NO_OP
    tools/check_live_acceptance.py                    required operation set, GATE_SURFACES, the GitLab receipt validator
    tools/render_manifest.py                          renders tools.json; --check fails on a stale copy
    inventory/ci/live-acceptance.json                 declared receipt directories, freshness
    tests/acceptance/receipts/                        committed sanitized receipts, all bound to one functional checksum

CI topology, from `.github/workflows/validate.yml`, existing elements plain and the two entries this phase adds
marked `+`:

    pull request head (exact SHA)
      ├─ validate ────────────────┐ static gate → docstrings → manifests and schemas (render check of tools.json;
      │                           │ metaschema list + ocp-node-onboard result schema, + machine-spec schema)
      │                           │ → unit tests (tests/python; + test_run_scope, + test_ocp_node_onboard, + test_ocp_join)
      │                           │ → offline acceptance tests → live_acceptance: checker over the committed receipts (unchanged)
      ├─ gate-ci-skills-endpoints ┤ endpoint declarations, unchanged
      ├─ test-install ────────────┤ installer fixtures and toolchain schema, unchanged
      │        each job writes its evidence file (artifact) ▼
      └─ required-result ─────────┴─► aggregates the three evidence files → publishes the final ci-skills/required status
                                          │ ruleset "protected": ci-skills/required SUCCESS on the up-to-date head, else no merge

The three jobs run in parallel and share nothing but the head; `required-result` consumes their evidence
artifacts and publishes the final required status (validate publishes a pending one at its start), so a red step
in any job stops advancement there. The local pre-commit gate `./bless.sh` runs the same whitespace, JSON, schema,
markdown, shell, Python and endpoint checks on the working tree; its schema selector matches only `schemas/*.json`
today, so the existing pairing rule for the two new schemas needs the selector to include `ci-skills/schemas/`,
which is open question 4 because it also changes what the gate checks for the existing result schemas.

Nothing reads machine specifications, builds or serves an ISO, waits for a joiner or approves certificate
requests. CI11 plans `ocp_machine.py`, `ocp_iso.py` and `ocp_csr.py` as single commands owned by
`core/ocp_machines.py`, `core/ocp_iso.py` and `core/ocp_csr.py`; none exists. This phase creates those three
modules with the callbacks the chain needs, so each path has one owner: CI11 keeps the single-command verbs on
top of the same callbacks, and CI12 owns the composition. The command is declared in `core/catalog.py`, the single
source from which `tools.json`, `--help` and `--describe` are rendered; a hand-edited `tools.json` fails the
render check in the validate workflow.

The gate today accepts a receipt only when its kind, operation and result action are in the checker's required
operation set, its functional checksum equals the candidate's, it passes the GitLab receipt validator and it
binds to exactly one setup context built from a `gitlab_access` receipt (the host, origin, identity and target
that access receipt recorded); any other receipt in a declared directory turns the gate red, and every committed
receipt turns red once anything under `ci-skills/bin`, `lib` or `schemas` changes until the README rerun
procedure refreshes it. Those are facts about the existing gate; what this phase does about them is open question
2. Three more facts shape the adapter: the GitLab adapter's mode rule refuses `--receipt-out` on a live plan, and
the mode rules exist today in several copies with diverging reasons (the apply-against-dry-run conflict in the
toolbox actions, the GitLab adapter and the MTU check, the live-plan and receipt rules in the GitLab adapter
alone, the dry-run output rule at six sites); an interrupt raises a signal exit from the executor's recoverable
guard, which the GitLab adapter's boundary does not catch, so an interrupted apply exits 128 plus the signal with
no result, where the MTU check converts it into a rendered `interrupted` reason; and the Kubernetes service has
no call that waits for a Pod's init container, so the build's wait is a poll of the Pod status through `json`,
shaped like the existing build wait.

Intended outcome: eight requirements, two pull requests, two waves, one command with the proposed verbs `build`,
`join`, `remove` and `verify` sharing the mode flags of the GitLab actions (`--dry-run`, `--live-plan`, `--apply
--confirm-plan`), each callback reusable by other chains, and the run scope reusable by the image-build readiness
probe (chain 11) and the GitLab runner manager (block 7). The command surface is open question 1 until the owner
approves it; the invocations below show the proposal exactly.

### Design and Decomposition

One concrete state. `OnboardState(DeliveryState)` adds the named fields the diagram shows: `spec`, `pool`,
`run_scope`, `boot_image`, `pull_secret_ref`, `iso`, `served_url`, `iso_check`, `serving_kept`, `joiner`,
`approved`, `node`. Every callback reads earlier fields and writes its own; the fields are the only hand-off.

One action. `OnboardAction(SkillAction)` is a frozen dataclass with `spec_path`, `iso_dir` (the ISO destination;
distinct from the shared `--output-dir`, which keeps its one meaning, paired report output), `run_id` (required by
`join` and `remove`; generated by `build`, and whether `build` may also accept a caller value through the shared
`--run-id` option, as the MTU command uses it for its temporary Pod, is open question 1), `node` (optional
override of the hostname the specification declares), `build_timeout`, `join_timeout`, `approve_timeout`,
`overwrite`, `iso_path` (the ISO to check, for `verify`), and the shared mode flags. Every environment specific
arrives here as an argument or through the target resolver; none is hardcoded.

One adapter. `OnboardWorkflow` in `core/ocp_onboard.py` mirrors `GitLabWorkflow` in `core/gitlab/gitlab_action.py`:
`prepare()` validates the mode, resolves the Kubernetes target (offline for `--dry-run`), and builds the plan and
its digest from the caller's inputs and the resolved target, the fields the GitLab plan binds plus this command's
own: the kind, the verb, the specification bytes, the ISO directory, the timeouts, the node, the caller-supplied
`--run-id` of `join` and `remove`, the target file and source, the revision, and the resolved context and server.
For an apply, `prepare()` compares `--confirm-plan` with that digest before any callback runs, exactly as the
GitLab adapter does for plans without a live snapshot; only then does `build` generate its identifier
(`RunScope.new()`), which is therefore outside `build`'s digest, while `join` and `remove` reconstruct theirs
(`RunScope.from_id(run_id)`) from the identifier inside their digest, so a digest confirmed for one run never
authorizes another. The modes are explicit per callback sequence: a live plan runs `K8SAuth` and the verb's
reads only, which for `join` is a read of the pending requests matching the selector, the snapshot pattern the
GitLab access callback uses, returning `PLANNED` with the pending-request rows and no approval; an apply runs
`K8SAuth`, then `ConfirmOnboardOperation` (re-verifies the target against the plan, as `ConfirmGitLabOperation`
does), then the verb's sequence. The mode-by-option table: `--dry-run` refuses `--receipt-out`, `--output-dir`
and `--log-file`; `--live-plan` accepts `--receipt-out` for this command, because the join plan is the mocked
join's only live evidence by the owner's decision, which is where this adapter's receipt policy differs from the
GitLab adapter's; an apply accepts all of them. The mode rules are not copied again: `prepare()` calls one shared
validator, `core/action_modes.py`, which carries one canonical reason per rule and a per-adapter policy naming
the adapter's read operations and which options need a live run; the GitLab adapter switches to it in the same
pull request (the CI13 proposal names the same validator; whichever merges first delivers it). `execute()` runs
the executor inside the recoverable-signal guard and renders the shared report; a signal exit raised by the
guard after cleanup is converted at the boundary into a `BLOCKED` result with reason `interrupted`, the absence
read-back and the receipt, exit code 2, as the MTU check converts its interrupt; the result validates against
`schemas/results/ocp-node-onboard.schema.json`. The toolbox adapter `WorkflowAction` is not reused: it parses
`--spec` as a toolbox specification and requires a toolbox catalogue kind, as the contribution guide states; its
report shape is followed, not its class. `EnsureNamespace` is not reused: it is bound
to the toolbox action and preserves a namespace that already exists, while a run-scoped namespace must not
pre-exist and is removed by the scope; its mechanism, a cluster-scoped `ResourceSet(service, "", operation)`
applying the Namespace document, is copied.

Callbacks, each a `K8SCallback[OnboardAction]` unless stated, each owning its hooks only, with their module:

- `LoadMachineSpec` (`LocalCallback`, `core/ocp_machines.py`): `collect` loads and schema-validates the
  specification and writes `state.spec`. No cluster access.
- `ReadMachinePool` (`core/ocp_machines.py`): `run` reads the target pool's machine objects and compares role,
  image and network settings with `state.spec`, as CI11's `ocp_machine.py --node-spec` defines the comparison;
  `update` writes `state.pool` and the drift rows.
- `ReadBootImage` (`core/ocp_iso.py`): `run` reads the published boot-image stream and writes `state.boot_image`
  (URL, digest, version); `update` records that install must use this version, not the running nodes' version.
- `OpenRunScope` (`K8SCallback[RunScopeAction]`, `core/kubernetes/run_scope.py`; its action carries the scope and
  an optional role name, so other chains compose it): `run` applies the Namespace through the scope's
  cluster-scoped set, the run's ServiceAccount, and, when the role argument is set, a ClusterRoleBinding to that
  role; CI11's source binds cluster-admin to its build-and-serve manifest, and the least role that works is pinned
  by the first live run and recorded in the receipt. `update` reads the objects back; `cleanup` removes them when
  `state.failed` is set and `state.serving_kept` is not, each with absence read-back.
- `ResolvePullSecret` (`core/ocp_iso.py`): `run` reads the cluster pull secret and copies it into the run namespace
  as a `dockerconfigjson` Secret owned by the scope, the pattern of `PullImage`; `update` writes
  `state.pull_secret_ref` as name and digest; `cleanup` removes the Secret under the same rule. The value never
  enters state, report or disk.
- `BuildNodeImage` (`core/ocp_iso.py`): `collect` renders the node configuration from `state.spec` into a
  ConfigMap owned by the scope; `run` creates the run-scoped Pod, as CI11's source does: an init container that
  builds the ISO from the release's boot image, the ConfigMap and the Secret into a Pod-local volume, and a serving
  container that serves that volume; it waits for the init container within `build_timeout`; `update` reads the
  Pod back; `cleanup` removes the Pod and the ConfigMap under the same rule.
- `ServeNodeImage` (`core/ocp_iso.py`): `run` creates the NodePort Service for the serving container; `update`
  reads the URL back and writes `state.served_url`; `cleanup` removes the Service with absence read-back under the
  same rule.
- `FetchNodeImage` (`core/ocp_iso.py`): `run` fetches the ISO from `state.served_url` into the ISO directory
  (blocked with a classified reason when the file exists and `overwrite` is unset, or when the served URL is not
  reachable from the agent's machine); `update` reads the file back (exists, digest, size) and writes `state.iso`.
  The Kubernetes service has no dedicated copy call, though its command runner could exec into the Pod; the
  fetch is the chosen route because it doubles as the serving proof; it requires the agent's machine to reach
  the NodePort.
- `VerifyNodeImage` (`LocalCallback`, `core/ocp_iso.py`): `run` extracts the node configuration the build embedded
  in the ISO at `state.iso` (or `iso_path` for `verify`), by the means open question 7 settles, and compares every
  field the specification declares (hostname, first NIC hardware address, install device, build interface, role)
  with `state.spec`; `update` writes `state.iso_check` as match or drift per field, `PASS` when all match and
  `PARTIAL` with the drift rows otherwise; on drift the callback is complete, so the scope is kept and the agent
  decides; the ISO is read, never rewritten. In `build` it is the last callback, so its `cleanup` runs first in
  reverse order and sets `state.serving_kept` when the run succeeded, which every earlier callback's cleanup reads.
- `WaitForJoiner` (`core/ocp_join.py`): `run` first reads the node by the hostname the specification declares (or
  `node`); when it is already Ready it writes `state.joiner` as already joined, so the approval step is `NO_OP`
  and a repeated `join` ends there; otherwise it polls, within `join_timeout`, for the first pending client
  certificate request whose subject matches that hostname, because the Node object exists only after that request
  is approved, and writes `state.joiner` (request name, hostname).
- `ApproveJoinRequests` (`core/ocp_csr.py`): the plan carries a selector, the hostname and the two signers
  (client, then serving), which is digest-stable because request names are not known in advance; `collect` lists
  the pending requests matching the selector; `run` is one bounded loop within `approve_timeout`, because the
  executor runs each hook once and the serving request appears only after the client request is approved: it
  approves the matching client request, polls for the serving request matching the selector, approves it, then
  polls until that request reads back approved and issued and the node reads Ready; `update` reads the final
  state back once; any other signer is reported. The selector never widens; the set of requests it matches grows
  by one. `NO_OP` when the joiner was already Ready and nothing is pending.
- `ReadNodeState` (`core/ocp_join.py`): `run` reads the node back and compares its hostname and role with
  `state.spec`, `PARTIAL` on a mismatch; Ready, OS version and kernel are recorded in the report.
- `RemoveScope` (`core/kubernetes/run_scope.py`): `run` deletes every object carrying the run identifier, the
  namespaced set first (Service, Pod, ConfigMap, Secret, ServiceAccount), then the cluster-scoped set (binding,
  namespace), with absence read-back; `NO_OP` when nothing carries it. `join` runs it last; `remove` runs it
  alone, without a joiner wait.

Reuse: `K8SAuth` first in every cluster verb; `ResourceSet` for everything created; the Secret-copy pattern of
`PullImage`; the statuses from `ci-skills/lib/python/sys/status.py`; the existing report renderer and the
portable receipt writer in `core/portable.py`; the catalogue declaration and render tool.

Scope lifetime, decided as mechanism: every run-scoped object has one owner, the run identifier, and one lifetime,
from the build to the end of `join` or `remove`. The executor runs cleanup for every entered callback on success and
on failure and sets `state.failed` before cleanup on failure, and it also sets `state.failed` when a cleanup hook
raises mid-way. So the keep-or-remove decision cannot rest on `state.failed` alone: `VerifyNodeImage.cleanup`, the
last callback of `build`, runs first in reverse order and records `state.serving_kept` on a successful run; every
earlier callback's cleanup removes what it created only when the run failed and the serving was not kept. A run that
fails before `serving_kept` is recorded leaves nothing behind, because each creator removes its own object and cleanup
runs only for entered callbacks. When a later cleanup hook fails after the serving was kept, the scope stays intact,
the result is `BLOCKED` with the run identifier, and `remove --run-id` is the recovery. `join` and `remove`
reconstruct `ResourceSet(namespace, operation=run_id)` from `--run-id`, and `RemoveScope` deletes only what carries
that label. No discovery by any other label, and a new run never changes or deletes another run's objects, because
`ResourceSet` refuses to modify an existing object whose operation label differs.

Concrete statuses, from the shared adapter rule: `--dry-run` returns `DRY_RUN` in phase `OFFLINE_PLAN` with the plan
digest and no API call; `--live-plan` returns `PLANNED` in phase `LIVE_PLAN` after `K8SAuth` reads the identity back
and the verb's reads ran (for `join`, the pending requests matching the selector); `--apply --confirm-plan <digest>`
returns `PASS` with `APPLIED` or `NO_OP` rows when the operation record is verified, has no errors and its own cleanup
field is neither `BLOCKED` nor `PARTIAL`, and `PARTIAL` otherwise; a cleanup hook that raises is rendered as `BLOCKED`
with `cleanup.status` `BLOCKED`; an interrupt is rendered as `BLOCKED` with reason `interrupted`; the exit code is 0
for `PASS`, `DRY_RUN` and `PLANNED`, and 2 for `PARTIAL` and `BLOCKED`.

#### Invocation and the result the agent reads

The agent invokes the command the way SKILL.md routes every installed command, from the skill root, and reads the
shared envelope the result schema declares; no new output format. The option names below are the proposal under
open question 1. The top-level keys and their shapes are the existing envelope's (`summary` is an object of
counts, `errors` entries carry `source` and `reason` only, `plan_digest` is 64 bare hex characters that
`--confirm-plan` takes as is); the rows inside `records` and `readback` are the paired `ocp-node-onboard`
schema's to declare, sketched here. `<SPEC>`, `<DIR>`, `<ISO>`, `<RECEIPT>` and `<digest>` are placeholders,
never examples.

    conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/ocp_node_onboard.py" --describe

returns the command contract as every installed command does: `kind: command_contract`, `command`, `purpose`,
`options` with one line each, `status_values`, `exit_codes` (`0` for `PASS`, `DRY_RUN` and `PLANNED`; `2` for
`BLOCKED` and `PARTIAL`), `requires_authorities: [kubernetes]`, `mutates: true`, `report_kind: ocp_node_onboard`,
`side_effects`. The verb is a positional argument, shown in the `--help` usage line as
`{build,join,remove,verify}` the way the GitLab runner command shows `{assign,create,delete,get,list,tag}`.

    conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/ocp_node_onboard.py" build --spec <SPEC> --iso-dir <DIR> --dry-run --json

    {
      "schema_version": "1.0", "kind": "ocp_node_onboard", "status": "DRY_RUN", "phase": "OFFLINE_PLAN",
      "operation": "build", "mutated": false,
      "plan": { "spec_digest": "sha256:<...>", "iso_dir": "<DIR>", "target": "<context> -> <server>",
                "names": "<pattern derived from the run identifier>",
                "objects": ["Namespace", "ServiceAccount", "ClusterRoleBinding", "Secret", "ConfigMap", "Pod", "Service"] },
      "plan_digest": "<64 hex characters>",
      "summary": { "record_count": 0, "error_count": 0 }, "errors": [], "captured_at": "<time>"
    }

    conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/ocp_node_onboard.py" build --spec <SPEC> --iso-dir <DIR> \
      --apply --confirm-plan <digest> --json --receipt-out <RECEIPT>

    {
      "schema_version": "1.0", "kind": "ocp_node_onboard", "status": "PASS", "phase": "APPLY",
      "operation": "build", "result_action": "APPLIED", "mutated": true,
      "plan_digest": "<64 hex characters>", "verified_target": { "context": "<context>", "server": "<server>" },
      "identity": { "<the identity K8SAuth read back>": "..." },
      "records": [ { "step": "ReadMachinePool", "action": "NO_OP", "verified": true, "drift": [] },
                   { "step": "ReadBootImage", "verified": true, "version": "<v>", "digest": "sha256:<...>" },
                   { "step": "ResolvePullSecret", "action": "APPLIED", "verified": true, "secret": "<name>", "digest": "sha256:<...>" },
                   { "step": "BuildNodeImage", "action": "APPLIED", "verified": true, "pod": "<name>" },
                   { "step": "ServeNodeImage", "action": "APPLIED", "verified": true, "served_url": "<node address>:<port>/<file>" },
                   { "step": "FetchNodeImage", "action": "APPLIED", "verified": true, "iso": "<DIR>/<file>", "digest": "sha256:<...>", "size": <bytes> },
                   { "step": "VerifyNodeImage", "verified": true, "match": true,
                     "fields": [ { "field": "hostname", "spec": "<value>", "iso": "<value>", "match": true },
                                 { "field": "nic_hardware_address", "spec": "<value>", "iso": "<value>", "match": true },
                                 { "field": "install_device", "spec": "<value>", "iso": "<value>", "match": true },
                                 { "field": "build_interface", "spec": "<value>", "iso": "<value>", "match": true },
                                 { "field": "role", "spec": "<value>", "iso": "<value>", "match": true } ] } ],
      "readback": { "verified": true, "run_id": "<run identifier>", "namespace": "<name>", "served_url": "<url>",
                    "iso_digest": "sha256:<...>", "iso_matches_spec": true },
      "cleanup": { "status": "NOT_APPLICABLE" },
      "summary": { "record_count": <count>, "error_count": 0 }, "errors": [], "captured_at": "<time>"
    }

The agent then tells the user what to mount (`served_url`) and waits for the boot. After it:

    conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/ocp_node_onboard.py" join --run-id <run identifier> --spec <SPEC> \
      --apply --confirm-plan <digest> --json --receipt-out <RECEIPT>

    {
      "schema_version": "1.0", "kind": "ocp_node_onboard", "status": "PASS", "phase": "APPLY",
      "operation": "join", "result_action": "APPLIED", "mutated": true,
      "records": [ { "step": "WaitForJoiner", "verified": true, "request": "<csr name>", "hostname": "<host>" },
                   { "step": "ApproveJoinRequests", "action": "APPLIED", "verified": true,
                     "approved": [ { "name": "<csr>", "signer": "<client signer>" },
                                   { "name": "<csr>", "signer": "<serving signer>" } ], "other_signers": [] },
                   { "step": "ReadNodeState", "verified": true, "node": "<name>", "ready": true,
                     "hostname_matches_spec": true, "role_matches_spec": true, "os": "<version>", "kernel": "<version>" },
                   { "step": "RemoveScope", "action": "APPLIED", "verified": true,
                     "removed": ["Service", "Pod", "ConfigMap", "Secret", "ServiceAccount", "ClusterRoleBinding", "Namespace"] } ],
      "readback": { "verified": true, "run_id": "<run identifier>", "node": "<name>", "ready": true, "absent": true },
      "cleanup": { "status": "PASS" },
      "summary": { "record_count": <count>, "error_count": 0 }, "errors": [], "captured_at": "<time>"
    }

A rerun of `join` against the joined node returns `status: PASS`, `result_action: NO_OP`, `mutated: false`. In
the live test, by the owner's decision, `join` runs as a live plan only:

    conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/ocp_node_onboard.py" join --run-id <run identifier> --spec <SPEC> \
      --live-plan --json --receipt-out <RECEIPT>

    {
      "schema_version": "1.0", "kind": "ocp_node_onboard", "status": "PLANNED", "phase": "LIVE_PLAN",
      "operation": "join", "mutated": false, "plan_digest": "<64 hex characters>",
      "plan": { "selector": { "hostname": "<host>", "signers": ["<client signer>", "<serving signer>"] },
                "pending": [ { "name": "<csr>", "signer": "<signer>", "matches": true }, "<one row per pending request>" ] },
      "readback": { "verified": true, "run_id": "<run identifier>", "pending_count": <count> },
      "summary": { "record_count": <count>, "error_count": 0 }, "errors": [], "captured_at": "<time>"
    }

When a step blocks, the envelope carries `status: BLOCKED`, a classified reason in `errors` (`source` and `reason`
only), the run identifier under `readback` and the `cleanup` record, and the exit code is 2; an interrupted apply
carries `reason: interrupted` after cleanup ran. A dry run refuses `--receipt-out`.

    conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/ocp_node_onboard.py" remove --run-id <run identifier> \
      --apply --confirm-plan <digest> --json --receipt-out <RECEIPT>

returns the same envelope with `operation: remove` and the `RemoveScope` record, `NO_OP` when nothing carries the
identifier. The standalone check needs no cluster and no credentials:

    conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/ocp_node_onboard.py" verify --iso <ISO> --spec <SPEC> --json --receipt-out <RECEIPT>

    {
      "schema_version": "1.0", "kind": "ocp_node_onboard", "status": "PASS", "phase": "READ",
      "operation": "verify", "mutated": false,
      "records": [ { "step": "VerifyNodeImage", "verified": true, "iso": "<ISO>", "digest": "sha256:<...>", "match": true,
                     "fields": [ { "field": "hostname", "spec": "<value>", "iso": "<value>", "match": true }, "<one row per field>" ] } ],
      "readback": { "verified": true, "iso_matches_spec": true },
      "summary": { "record_count": <count>, "error_count": 0 }, "errors": [], "captured_at": "<time>"
    }

`status: PARTIAL` with `match: false` rows and exit code 2 when a field differs. Every mutating verb also accepts
`--live-plan` (`status: PLANNED`, identity read back, no mutation); every verb accepts `--yaml`, `--human`,
`--output-dir` for paired reports, and the shared diagnostic options.

#### When to Decompose

Two slices, because they have independent reasons to change and independent proofs: `build` (reads and the ISO,
with `remove` as its recovery) is provable with nothing but the cluster and an ISO directory; `join` needs a host
that boots. Each is one pull request with its own live proof.

#### When to Introduce Abstraction

The options for run-scoped naming and cleanup, compared:

- Bare `ResourceSet(operation=<run id>)` with each callback choosing its own names: ownership and cleanup of the
  created set exist today, but identifier generation, deterministic name derivation per kind, the cluster-scoped
  set for the Namespace and the binding, and reconstruction from an identifier would be repeated in every callback
  and in the two other consumers (chain 11 probe, block 7 runner manager).
- Extending `ResourceSet` with those behaviours: couples a general ownership type to naming policy and to the
  keep-or-remove decision, which only this chain needs.
- A small `RunScope` over `ResourceSet`, chosen: one frozen dataclass (`run_id`, `namespace`, `labels`), two
  constructors (`RunScope.new()`, `RunScope.from_id(run_id)`), `scope.name(kind)` for deterministic names,
  `scope.resources(service)` for the namespaced set and `scope.cluster_resources(service)` for the cluster-scoped
  set (Namespace, ClusterRoleBinding), both bound to the operation label, and two callbacks with their own small
  action (`RunScopeAction`: the scope and an optional role): `OpenRunScope` and `RemoveScope`. No hierarchy, no
  factory. The adapter's `prepare()` constructs the scope and stores it in `state.run_scope`; callbacks only read
  it. Consumers: this phase's callbacks, the image-build readiness probe and the GitLab runner manager.

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
    - The project owner names the authority this phase derives from (the three files, or another document)

    BLOCKER: Running bin/check-system-architecture.py against this document at the project's pin
    Authority checked:
    - standards-binding.yaml (pin 56a579c); the contract was added at standards f39203a (2026-10-13) and the checker at a2256a8 (2026-10-14)
    Observation:
    - At the pin the contract and checker are absent; the checker reports ARCH-INPUT UNAVAILABLE for this project
    Risk:
    - This document's conformance is asserted, not enforced, until the pin moves
    Safe next step:
    - The owner bumps standards-binding.yaml to a revision at or after a2256a8 in its own pull request

Open questions for the owner. Each is a requirement, a user-facing contract or a gate behaviour that only the
owner decides; the document records the options and the finding that raised each, and decides none of them:

1. The command surface: the verbs `build`, `join`, `remove` and `verify` and the options `--spec`, `--iso-dir`,
   `--iso`, `--run-id` (reused with its existing meaning plus the scope key, as the MTU command uses it for its
   temporary Pod), `--node`, `--overwrite` and the three timeouts, as "Invocation and the result the agent reads"
   shows them. The owner asked for the ISO check on 2026-10-15; its verb name and options are still part of this
   question.
   Raised by the review that found `--output-dir` already taken by the shared parser. The `remove` verb exists
   only as the recovery path the review asked for; without it the recovery is deleting by the operation label.
   Sub-option for the identifier on `build`: generate only; or accept a caller value through the shared `--run-id`
   (hashed into the derived names to fit label limits), which sits against the direction that names are
   generated; or a separate flag.
2. How this command's live evidence enters the gate. Facts: the checker accepts only its declared GitLab kinds
   and rejects any other receipt in a declared directory; it fails on a missing required operation; every
   committed receipt turns red when anything under `ci-skills/bin`, `lib` or `schemas` changes, so each of these
   pull requests triggers the README rerun of the whole set; by the owner's decision the join is never applied
   live in the test, so its only live receipt is the read-only `PLANNED` one over the pending requests. The
   owner's follow-up of 2026-10-15 directs the existing GitHub test and check path with no new gate and no
   separate plan, so a separate receipt directory with its own freshness policy is withdrawn. What remains open:
   whether this command's receipts are committed into the existing gate, which needs the checker to accept the
   kind (a validator, a kubernetes setup context and required operations for `build` `APPLIED`, `verify`,
   `remove` and `join` `PLANNED`), or stay attached to the pull request only. Until answered, the pull requests
   attach their live evidence and commit no receipt under a declared directory.
3. Which receipts prove the chain for the six-row model: the build receipt with the ISO read-back and its
   per-field match, the verify and remove receipts, and the join plan receipt with the pending requests, linked
   by the run identifier, plus the mocked approval and node rows from the unit tests marked as mocked, are the
   proposal.
4. The local gate's schema selector matches only `schemas/*.json`, so the existing pairing rule cannot cover the
   two new schemas without widening it to `ci-skills/schemas/`, which also starts checking the existing result
   schemas locally. Raised by the review of the pairing claim.
5. Four places where this phase diverges from CI11's merged rows when those are built: the ISO flag (`--iso-dir`
   instead of the row's `--output-dir`); a second build with the same inputs being a new run that blocks on an
   existing ISO instead of `NO_OP`; the pull secret copied as a Secret instead of the row's 0600 files on disk;
   certificate approval by selector instead of the row's planned names.
6. Which workload needs cluster API access and with which role; CI11's source binds cluster-admin to its
   build-and-serve manifest, and the first live run pins the least role that works.
7. Where the supported tooling for the selected image runs when the ISO's embedded configuration is inspected,
   for `verify` and for the build's last step. The owner's follow-up of 2026-10-15 directs inspecting the ISO
   with the supported tooling for the selected image and comparing the declared hostname, network and hardware
   address, install device and the other supported fields with the embedded values, since file existence, size
   and checksum alone prove nothing about the embedding. The repository has no ISO reader today, and that
   tooling needs the release. Options: run it on the agent's machine, which is an install step; or run it inside
   the build Pod, serving the extracted configuration beside the ISO and fetching both, with the standalone verb
   limited to that sidecar and the ISO digest.

Decided as mechanism inside the library, recorded so no step hides a choice:

- Comparison baseline: the cluster's own machine pool, read and compared as CI11's `ocp_machine.py --node-spec`
  defines; no reference file and no capture verb. OS version and kernel of the joined node are recorded, not
  compared, because no recorded authority defines a baseline for them.
- Cross-verb hand-off: the run identifier, generated after confirmation and returned by `build`, passed to `join`
  and `remove`; the namespace and the served URL are also in the result for the agent's records, but the
  identifier alone reconstructs the scope. `build`'s generated identifier is outside its digest; the
  caller-supplied `--run-id` of `join` and `remove` is inside theirs, with the resolved target.
- Ownership against CI11: the callbacks live in the CI11-owned modules whose behaviour they implement, and CI11's
  single-command verbs become thin mains over the same callbacks when they are built; no second module tree.
  `RemoveScope` is delivered by PR-CI12-1 (it is `remove`'s only callback) and composed by PR-CI12-2.
- One Pod builds and serves, as CI11's source does, so the ISO never crosses workloads: the init container builds
  into a Pod-local volume and the serving container serves it; the node configuration reaches the init container
  as a ConfigMap and the pull secret as a Secret; the ISO reaches the ISO directory by a fetch over the served
  URL, chosen over an exec-based copy because the fetch is also the serving proof.
- Where the ISO build runs: in-cluster, because a workstation build cannot reach the host's network and streams
  gigabytes over a slow tunnel.
- The host's boot is the user's action; automating the management controller is out of scope for this phase.
- The live-ISO alternative (a hand-built Ignition document installed from the stock live ISO) is out of scope; no
  planned verb exists for it.
- Approval and join are mocked, by the owner's decision of 2026-10-15: they need a real host to join, the one
  thing the bench cannot supply on demand, so the live test runs `join --live-plan` to read the pending requests
  and the approval loop and the node join are exercised by unit tests with a simulated two-request sequence; the
  production `join` is unchanged, only its proof is.
- Post-join CI preparation (labels and taints from the specification, then verification) is a separate proposal:
  chain 2 ends at the served ISO's removal and no recorded authority carries the preparation step, so it is
  UNGROUNDED here and gets its own requirement when the owner records it.

The boundary is the two slices above. A third pull request for the single-command CI11 verbs is a separate
proposal.

## PR Order

PR-CI12-1 (wave W-CI12-1) delivers the run scope with its two callbacks, the specification loader and pool comparison,
the boot-image read, the pull-secret copy, the ISO build and serving, the adapter and the thin main with the `build`,
`remove` and `verify` verbs, the catalogue declaration with its regenerated `tools.json` and result schema, the schema
entries in the validate workflow per the existing pairing rule, and the routing; its live proof is the build run from
the installed candidate with the ISO read back from the ISO directory, checked field by field against the
specification, and the scope removed afterwards. PR-CI12-2 (wave W-CI12-2) depends on it for the state fields, the run
scope and the scope to remove, and delivers the `join` verb: the joiner wait, certificate approval and node state
compared with the specification, with the result schema extended for the join fields; its live proof is the read-only
`join --live-plan` over the pending requests, because the approval and the join need a real host and are mocked by the
owner's decision. Per the owner's rule, each pull request is live-tested on its own head from the installed candidate
before it merges; a change under `ci-skills/bin`, `lib` or `schemas` invalidates the proof, because the functional
checksum covers those paths, while a documentation or test-only change does not. What each pull request commits into
the gate follows the owner's answer to open question 2; until then the evidence is attached to the pull request. Each
pull request leaves the installed command coherent under the existing gates, with the unimplemented verb absent from
the catalogue rather than stubbed.

## Gates

The traceability map in `Authority` is the canonical map. `G-CI12-STATIC` is the existing validate workflow and the
local pre-commit gate; its enforcement is the main ruleset's required `ci-skills/required` status with the strict
up-to-date policy, read back from the repository's branch rules on 2026-10-15. `G-CI12-LIVE` is the live run of each
slice from the installed candidate on the user's live smoke setup, with one receipt per verb and result action, where
the join's live run is its read-only plan and the approval and join are mocked; it is proposed, its evidence is
attached to the pull request, and whether its receipts enter the committed gate is open question 2. `G-CI12-GATE` is
the existing live-acceptance step, unchanged by this phase until that question is answered.

Per-requirement gaps today, all of the same kind: no module, no unit test and no live run exists for any of
`R-CI12-001` to `R-CI12-007`, so every row records `UNVERIFIED`. The specific evidence each needs: `R-CI12-001` a
loader test (valid, invalid) and a pool comparison test (match, drift per field); `R-CI12-002` a result row with the
stream digest and the Secret's name and digest and no value; `R-CI12-003` the ISO digest and size read back and the
serving URL; `R-CI12-004` a scope test (names derive from the identifier, `from_id` reproduces them, removal deletes
exactly the owned set including the cluster-scoped objects) and nothing carrying the identifier after `join` or
`remove`; `R-CI12-005` an approval-loop test against a simulated two-request sequence, a foreign signer and an expired
deadline (the approval is never run live), and the live plan's pending-request rows; `R-CI12-006` the mocked joiner
and the mocked node read-back with hostname and role matching the specification, and the live plan over the pending
requests; `R-CI12-007` the catalogue consistency test, the render check and `--describe` without credentials;
`R-CI12-008` a test that builds a known configuration into a fixture ISO and asserts the comparison reports a changed
hostname and a changed hardware address as drift, and the live build's per-field match rows.

## Live Smoke Evidence

Planned, not observed. `build`: the command run from the installed candidate against the live cluster with a
specification the agent passes and an ISO directory it chooses; evidence is the result's rows plus the ISO's digest
and size read back from that directory, its per-field match against the specification, and the run identifier.
`verify`: the fetched ISO checked against the specification; `remove`: nothing left carrying the identifier. `join`:
`--live-plan` from the installed candidate reads the pending requests and returns `PLANNED` carrying the same run
identifier; the approval and the node join are not run live, by the owner's decision, because they need a real host to
join, and their rows come from the unit tests with a simulated two-request sequence, marked as mocked in the evidence.
Each receipt is sanitized with the portable writer and carries the functional checksum of the installed candidate;
where it is committed is open question 2.

## End-to-End Test

The consumer journey: the agent passes a specification and an ISO directory, runs `build`, hands the returned run
identifier to `join` after the user boots the host, and the final read-back is an ISO that matched the specification
field by field, then a Ready node with the declared hostname and role and no resource left carrying the identifier.
The proposed test is the live `build`, `verify` and `remove` receipts and the live `join` plan, linked by the same run
identifier and the same checksum, with the approval and the join mocked by the owner's decision; its acceptance gate
is `G-CI12-LIVE`. No execution is recorded yet.
