# NVIDIA GPU Operator atomic primitives and chain combos on OpenShift

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and
`Outcomes & Retrospective` must be kept up to date as work proceeds. It is maintained in accordance with
[docs/PLANS.md](../../PLANS.md) and delivers phase [CI17][phase]. Source baseline: `1ad150e`.

## Purpose / Big Picture

[CI16][ci16-exec] delivers generic OLM operator enablement (`ocp_operator.py`) using the NVIDIA Network Operator as its
initial consumer. OpenShift clusters running GPU workloads also depend on the NVIDIA GPU Operator
(`gpu-operator-certified`) and Node Feature Discovery (`nfd`), which require day-2 lifecycle operations beyond generic
OLM installation: inspecting GPU/CUDA/driver state across heterogeneous nodes, setting component image tags, updating
`ClusterPolicy` or `NVIDIADriver` driver versions (which determine the host CUDA driver ceiling), configuring rolling
`upgradePolicy` and drain settings, toggling documented node control labels, and waiting on the GPU Operator Upgrade
Controller state machine.

This plan builds **atomic, single-responsibility primitives first** using a **partitioned callback list**, and then
composes **chain combos** strictly from those atomic callbacks plus CI16's OLM callbacks. Every callback owns one
narrow Kubernetes concern, reads or writes via `KubernetesService` using qualified resource names, and can be tested
and reused independently.

Example golden-path commands after delivery:

```bash
# Atomic read-only snapshot (OLM + ClusterPolicy + NVIDIADrivers + GPU nodes + drift + validators)
nvidia_gpu_operator.py status --json

# Atomic driver/CUDA tag plan + confirmed apply on a named NVIDIADriver CR
nvidia_gpu_operator.py set-tag --nvidia-driver nvidia-driver-x210 --version 580.159.03 --live-plan
nvidia_gpu_operator.py set-tag --nvidia-driver nvidia-driver-x210 --version 580.159.03 \
  --apply --confirm-plan <sha256>

# Chain combo: preflight -> upgradePolicy -> set-tag -> wait-upgrade -> CUDA read-back
nvidia_gpu_operator.py upgrade-driver --nvidia-driver nvidia-driver-x210 \
  --version 580.159.03 --max-parallel 1 --drain --min-cuda 12.8 --live-plan
nvidia_gpu_operator.py upgrade-driver --nvidia-driver nvidia-driver-x210 \
  --version 580.159.03 --max-parallel 1 --drain --min-cuda 12.8 \
  --apply --confirm-plan <sha256>
```

## Progress

- [x] (DEC-CI17-01 to DEC-CI17-12) Design decisions grounded in official NVIDIA GPU Operator OpenShift docs and live
      read-only cluster observations.
- [x] (CI17-P1) Phase document, this ExecPlan and the plan index row are written.
- [ ] (CI17-M1) Operator-approved result schema (`nvidia-gpu-operator.schema.json`), models, Partition 1 discovery
      callbacks, `status` CLI verb, unit tests and live read-only proof.
- [ ] (CI17-M2) Partition 2 preflight/confirmation, Partition 3 atomic mutations (`set-tag`, `set-upgrade-policy`,
      `set-node-label`, `set-sharing`), Partition 4 waits (`wait-upgrade`) and unit/live-plan proofs.
- [ ] (CI17-M3) Chain combos (`enable-gpu-operator`, `upgrade-driver`, `rollout-component-tag`,
      `recover-failed-upgrade`) composed from CI17 atomic callbacks and CI16 OLM callbacks.
- [ ] (CI17-M4) Skill references, golden-path evidence and renewed skill-digest receipts.

## Surprises & Discoveries

These observations come from read-only reads of the operator's OpenShift cluster on `2026-11-22`:

- Observation: `gpu-operator-certified` is installed on a non-default channel while a newer default channel exists.
  Evidence: Subscription `gpu-operator-certified` in `nvidia-gpu-operator` tracks channel `v26.3` from
  `certified-operators` with CSV `gpu-operator-certified.v26.3.3` (`Succeeded`); PackageManifest default channel is
  `v26.7` (`gpu-operator-certified.v26.7.3`), supporting `OwnNamespace` and `SingleNamespace`. `v26.3` ships
  `ClusterPolicy` and `NVIDIADriver` in `alm-examples`; `v26.7` also ships `GPUCluster`. Effect: DEC-CI17-02.
- Observation: `ClusterPolicy` (`gpu-cluster-policy`, `status.state: ready`) sets `spec.driver.useNvidiaDriverCRD: true`
  and delegates driver management to four per-pool `NVIDIADriver` (`nvidia.com/v1alpha1`) CRs.
  Evidence: Active `NVIDIADriver` CRs target distinct node pools via `nodeSelector` with mixed repositories, images,
  precompiled flags, and driver versions (three pools on `nvcr.io/nvidia/driver` with `usePrecompiled: false` across
  `570.211.01` and `580.159.03`, and one pool on a custom precompiled driver image at `570.158.01` with
  `usePrecompiled: true`). Effect: DEC-CI17-04, DEC-CI17-05.
- Observation: Real GPU nodes exhibit desired-vs-observed driver drift and active upgrade-controller states/skip flags.
  Evidence: Nodes span `NVIDIA-L4`, `NVIDIA-H100-NVL`, and `NVIDIA-H200-NVL` (all reporting
  `nvidia.com/cuda.runtime-version.full=12.8`). One worker pool node ran `570.211.01` while its governing
  `NVIDIADriver` CR desired `580.159.03`; two edge nodes carried `nvidia.com/gpu-driver-upgrade.skip=true` alongside
  `nvidia.com/gpu-driver-upgrade-state=upgrade-failed` and `upgrade-required`. Effect: DEC-CI17-06, DEC-CI17-07,
  DEC-CI17-08.
- Observation: `spec.driver.useOpenKernelModules` is deprecated upstream in favor of `spec.driver.kernelModuleType`
  (`auto`, `open`, `proprietary`), which the live `ClusterPolicy` and `NVIDIADriver` CRs already use.
  Effect: DEC-CI17-05.

## Decision Log

- Decision DEC-CI17-01: Partition every GPU Operator workflow into four callback groups — Discovery (P1), Preflight &
  Confirmation (P2), Atomic Mutations (P3), and Wait & Read-Back (P4). Every callback performs one bounded concern and
  communicates through typed `GPUOperatorState`.
  Status: ACCEPTED
  Rationale: Prevents monolithic command handlers, makes every step independently unit-testable, and guarantees chain
  combos reuse the exact same logic as atomic primitives.
- Decision DEC-CI17-02: Do not duplicate OLM installation or removal code. `enable-gpu-operator` reuses CI16's
  `olm_callbacks` and `olm_resources` for `nfd` (`openshift-nfd`) and `gpu-operator-certified` (`nvidia-gpu-operator`).
  Status: ACCEPTED
  Rationale: One canonical OLM owner per repository standards.
- Decision DEC-CI17-03: Ground CUDA upgrades in the NVIDIA GPU Operator model. Host CUDA capability is controlled by
  the NVIDIA GPU driver container version (`ClusterPolicy.spec.driver.version` or `NVIDIADriver.spec.version`), and
  verified post-rollout through GFD node labels `nvidia.com/cuda.driver-version.full` and
  `nvidia.com/cuda.runtime-version.full`.
  Status: ACCEPTED
  Rationale: Containerized workloads bring their own CUDA user-space toolkit subject to the host driver ceiling.
- Decision DEC-CI17-04: Fail closed when `ClusterPolicy.spec.driver.useNvidiaDriverCRD` is `true` and a caller targets
  `--component driver` without `--nvidia-driver <name>`. Emit `BLOCKED` with `need: nvidia_driver_crd_required` listing
  the active `NVIDIADriver` CR names and their node selectors.
  Status: ACCEPTED
  Rationale: Patching `ClusterPolicy.spec.driver.version` while `useNvidiaDriverCRD: true` is active does not upgrade
  `NVIDIADriver`-managed node pools.
- Decision DEC-CI17-05: `PatchComponentTag` supports `version`, `repository`, `image`, `kernelModuleType`
  (`auto|open|proprietary`), `usePrecompiled`, and `imagePullSecrets` on `NVIDIADriver` or `ClusterPolicy.spec.driver`,
  and `version`, `repository`, `image` on operand components (`toolkit`, `devicePlugin`, `dcgm`, `dcgmExporter`, `gfd`,
  `migManager`, `validator`, `gds`, `gdrcopy`).
  Status: ACCEPTED
  Rationale: Real clusters combine `nvcr.io/nvidia/driver` (compiled via OCP Driver Toolkit) and custom precompiled
  driver images across node pools.
- Decision DEC-CI17-06: `ReadGPUNodes` always computes per-node `matched_driver_cr` (evaluating `NVIDIADriver`
  `nodeSelector` against node labels) and `driver_drift` (desired CR/ClusterPolicy driver version vs observed
  `nvidia.com/cuda.driver-version.full`), and surfaces `nvidia.com/gpu-driver-upgrade.skip`.
  Status: ACCEPTED
  Rationale: Makes silent drift and skipped nodes visible before any upgrade starts.
- Decision DEC-CI17-07: `ValidateGPUTargetContract` blocks `upgrade-driver` by default if any targeted non-skipped node
  is already in `nvidia.com/gpu-driver-upgrade-state=upgrade-failed`, emitting `need: node_upgrade_failed` pointing to
  `recover-failed-upgrade`. Nodes labeled `nvidia.com/gpu-driver-upgrade.skip=true` are excluded from the wait set and
  explicitly reported in the plan and summary.
  Status: ACCEPTED
  Rationale: Prevents cascading upgrades over already-broken nodes while respecting operator-pinned skip nodes.
- Decision DEC-CI17-08: `LabelGPUNode` restricts node-label mutations to documented NVIDIA GPU Operator control keys
  (`nvidia.com/gpu-driver-upgrade.skip`, `nvidia.com/gpu-driver-upgrade-state`, `nvidia.com/gpu.deploy.driver`,
  `nvidia.com/mig.config`, `nvidia.com/device-plugin.config`) plus an explicit `--driver-track-label KEY=VALUE` only
  when that key matches an existing `NVIDIADriver.spec.nodeSelector` key on the cluster.
  Status: ACCEPTED
  Rationale: Supports safe track switching and stuck-node retry without becoming an arbitrary cluster labeler.
- Decision DEC-CI17-09: Every mutating primitive and combo defaults to `ActionMode.LIVE_PLAN` (read-only cluster read,
  Kubernetes server-side dry-run, exact before/after diff, `plan_digest`), requires `--apply --confirm-plan <sha256>`
  to write, and executes independent post-write read-back.
  Status: ACCEPTED
  Rationale: Matches CI16 and repository safety contracts; driver upgrades can cordon/drain production GPU nodes.
- Decision DEC-CI17-10: Every Kubernetes API read/write uses qualified resource names:
  `clusterpolicies.nvidia.com`, `nvidiadrivers.nvidia.com`, `nodefeaturediscoveries.nfd.openshift.io`, plus CI16's
  qualified OLM resources.
  Status: ACCEPTED
  Rationale: Avoids bare-kind API-group ambiguity (same rule as DEC-CI16-13).
- Decision DEC-CI17-11: `WaitDriverUpgradeState` tracks the documented Upgrade Controller state machine
  (`upgrade-required` → `cordon-required` → `wait-for-jobs-required` → `upgrade-done`), fails fast on `upgrade-failed`,
  and `VerifyCUDAReadback` confirms `nvidia-operator-validator` readiness, allocatable `nvidia.com/gpu`, and optional
  `--min-cuda <major.minor>` against observed `nvidia.com/cuda.runtime-version.full`.
  Status: ACCEPTED
  Rationale: Proves real driver/CUDA convergence rather than stopping at CR patch acknowledgment.
- Decision DEC-CI17-12: Result JSON contract (`ci-skills/schemas/results/nvidia-gpu-operator.schema.json`) and
  `tools.json` updates are proposed and approved explicitly by the operator in Wave 2 (CI17-M1) before any JSON or
  contract file is written.
  Status: ACCEPTED
  Rationale: Complies strictly with the repository's contract and JSON approval lock.

## Context and Orientation

Code lives under `ci-skills/lib/python/core/kubernetes/nvidia_gpu/` with a thin CLI entrypoint at
`ci-skills/bin/nvidia_gpu_operator.py`. Callbacks inherit `K8SCallback` (`core/kubernetes/kubernetes.py`), share
`KubernetesService`, `K8SAuth`, and `WorkflowExecutor` (`core/executor.py`), and hold zero cluster-specific values.

## Partitioned Callback Architecture

### Partition 1 — Discovery Callbacks (`discovery.py`, read-only)

1. `DiscoverGPUOperatorStack`: Reads NFD (`nfd` Subscription/CSV, `NodeFeatureDiscovery`) and `gpu-operator-certified`
   Subscription/CSV/PackageManifest channels (reusing CI16 read helpers).
2. `ReadClusterPolicy`: Reads `clusterpolicies.nvidia.com` (`status.state`, `spec.driver.useNvidiaDriverCRD`, all
   component specs, `spec.driver.upgradePolicy`, `spec.mig`, `spec.devicePlugin.config`).
3. `ReadNVIDIADrivers`: Lists `nvidiadrivers.nvidia.com` CRs with image/version/repository/kernelModuleType/precompiled
   fields, `nodeSelector`, and `status.state`.
4. `ReadGPUNodes`: Reads nodes carrying `feature.node.kubernetes.io/pci-10de.present=true` or
   `nvidia.com/gpu.present=true`, extracts GPU/CUDA/MIG/upgrade-state/skip labels and allocatable `nvidia.com/gpu`,
   matches each node to its governing `NVIDIADriver` CR (or `ClusterPolicy`), and computes `driver_drift`.
5. `ReadGPUValidators`: Reads `nvidia-operator-validator`, `nvidia-cuda-validator`, and driver DaemonSet pod status in
   the GPU Operator namespace.

### Partition 2 — Preflight & Confirmation Callbacks (`preflight.py`, read-only)

1. `ValidateGPUTargetContract`: Validates target CR existence, `useNvidiaDriverCRD` guard, ConfigMap/Secret references,
   allowed node-label keys, and pre-existing `upgrade-failed` blockers; populates structured `needs` on block.
2. `ServerSideDryRunPatch`: Applies the planned JSON/merge patch with `--dry-run=server` against the live CRD schema
   and records exact before/after field diffs.
3. `ConfirmGPUPlan`: Computes deterministic `plan_digest` over target identity + before/after diff; stops on
   `LIVE_PLAN` or mismatched `--confirm-plan`.

### Partition 3 — Atomic Mutation Callbacks (`mutations.py`, single-resource writes)

1. `PatchComponentTag`: Idempotently patches component/driver image coordinates (`NO_OP` if already equal).
2. `PatchUpgradePolicy`: Idempotently patches `upgradePolicy` fields (`NO_OP` if already equal).
3. `LabelGPUNode`: Idempotently sets or removes validated NVIDIA control labels on selected node(s).
4. `PatchGPUSharing`: Idempotently patches MIG strategy/config or Device Plugin time-slicing/MPS ConfigMap binding.

### Partition 4 — Wait & Independent Read-Back Callbacks (`waits.py`, read-only)

1. `WaitClusterPolicyReady`: Polls `ClusterPolicy.status.state=ready` (and targeted `NVIDIADriver.status.state=ready`).
2. `WaitDriverUpgradeState`: Polls per-node `nvidia.com/gpu-driver-upgrade-state` until all targeted non-skipped nodes
   reach `upgrade-done` (failing fast on `upgrade-failed`).
3. `VerifyCUDAReadback`: Independently re-reads node CUDA driver/runtime versions (checking optional `--min-cuda`),
   allocatable `nvidia.com/gpu`, and validator pods.

### Chain Composition (`chains.py`)

Each CLI verb returns a flat partitioned list of callbacks executed by `WorkflowExecutor`:

- `status`: `K8SAuth`, `DiscoverGPUOperatorStack`, `ReadClusterPolicy`, `ReadNVIDIADrivers`, `ReadGPUNodes`,
  `ReadGPUValidators`.
- `set-tag`: `K8SAuth`, `ReadClusterPolicy`, `ReadNVIDIADrivers`, `ValidateGPUTargetContract`,
  `ServerSideDryRunPatch`, `ConfirmGPUPlan`, `PatchComponentTag`, `ReadClusterPolicy`, `ReadNVIDIADrivers`.
- `set-upgrade-policy`: `K8SAuth`, `ReadClusterPolicy`, `ReadNVIDIADrivers`, `ValidateGPUTargetContract`,
  `ServerSideDryRunPatch`, `ConfirmGPUPlan`, `PatchUpgradePolicy`, `ReadClusterPolicy`, `ReadNVIDIADrivers`.
- `set-node-label`: `K8SAuth`, `ReadNVIDIADrivers`, `ReadGPUNodes`, `ValidateGPUTargetContract`, `ConfirmGPUPlan`,
  `LabelGPUNode`, `ReadGPUNodes`.
- `set-sharing`: `K8SAuth`, `ReadClusterPolicy`, `ValidateGPUTargetContract`, `ServerSideDryRunPatch`,
  `ConfirmGPUPlan`, `PatchGPUSharing`, `ReadClusterPolicy`.
- `wait-upgrade`: `K8SAuth`, `ReadClusterPolicy`, `ReadNVIDIADrivers`, `WaitClusterPolicyReady`,
  `WaitDriverUpgradeState`, `VerifyCUDAReadback`.
- `enable-gpu-operator`: `K8SAuth`, CI16 NFD install/apply callbacks, `VerifyNFDLabels`, CI16 `gpu-operator-certified`
  install/apply callbacks, `WaitClusterPolicyReady`, `ReadGPUNodes`, `ReadGPUValidators`.
- `upgrade-driver`: `K8SAuth`, `ReadClusterPolicy`, `ReadNVIDIADrivers`, `ReadGPUNodes`, `ValidateGPUTargetContract`,
  `ServerSideDryRunPatch`, `ConfirmGPUPlan`, optional `PatchUpgradePolicy`, `PatchComponentTag`,
  `WaitClusterPolicyReady`, `WaitDriverUpgradeState`, `VerifyCUDAReadback`.
- `rollout-component-tag`: `K8SAuth`, `ReadClusterPolicy`, `ValidateGPUTargetContract`, `ServerSideDryRunPatch`,
  `ConfirmGPUPlan`, `PatchComponentTag`, `WaitClusterPolicyReady`, `ReadGPUValidators`.
- `recover-failed-upgrade`: `K8SAuth`, `ReadNVIDIADrivers`, `ReadGPUNodes`, `ValidateGPUTargetContract`,
  `ConfirmGPUPlan`, optional `LabelGPUNode` (clear skip), `LabelGPUNode` (`upgrade-required`),
  `WaitDriverUpgradeState`, `VerifyCUDAReadback`.

## Verification Gates

| Gate ID | Scope | Proof Required |
| --- | --- | --- |
| `G-CI17-01` | Unit & contracts | Unit tests for P1–P4 callbacks, CRD guard, `NO_OP`, drift, fail-fast, schema parity |
| `G-CI17-02` | Live `status` | Installed `status --json` returns `PASS` with policy, driver CRs, nodes, drift |
| `G-CI17-03` | Live `--live-plan` | Server-side dry-run + `plan_digest` with `mutated: false` for atomic mutations |
| `G-CI17-04` | Atomic write/restore | Authorized reversible atomic mutation verified by read-back and restored |
| `G-CI17-05` | Chain combo | Installed combo verified end-to-end with renewed acceptance receipts |

## Interfaces and Dependencies

No new third-party Python dependency is introduced. All cluster operations go through `KubernetesService` (`kubectl`)
and CI16's `olm_callbacks`/`olm_resources`.

Planned module layout under `ci-skills/lib/python/core/kubernetes/nvidia_gpu/`:

- `models.py`: Qualified resource constants (`CLUSTER_POLICIES`, `NVIDIA_DRIVERS`, `NODE_FEATURE_DISCOVERIES`),
  `GPUOperatorAction`, `GPUOperatorState`, `DriverPoolInfo`, `GPUNodeInfo`, `GPUNeed`, and `GPU_OPERATOR_MODE_POLICY`.
- `discovery.py`: Partition 1 callbacks (`DiscoverGPUOperatorStack`, `ReadClusterPolicy`, `ReadNVIDIADrivers`,
  `ReadGPUNodes`, `ReadGPUValidators`).
- `preflight.py`: Partition 2 callbacks (`ValidateGPUTargetContract`, `ServerSideDryRunPatch`, `ConfirmGPUPlan`).
- `mutations.py`: Partition 3 callbacks (`PatchComponentTag`, `PatchUpgradePolicy`, `LabelGPUNode`, `PatchGPUSharing`).
- `waits.py`: Partition 4 callbacks (`WaitClusterPolicyReady`, `WaitDriverUpgradeState`, `VerifyCUDAReadback`).
- `chains.py`: Pure callback-list builders for each atomic verb and chain combo.
- `cli.py`: `build_parser`, `workflow`, `NvidiaGPUOperator`, `main`, invoked by `ci-skills/bin/nvidia_gpu_operator.py`.

## Outcomes & Retrospective

Not yet implemented. Updated at the completion of each milestone (`CI17-M1` through `CI17-M4`).

[phase]: ../../phases/CI17-NVIDIA-GPU-OPERATOR.md
[ci16-exec]: ci16-olm-operator.md
