# NVIDIA GPU Operator atomic callbacks and chain combos on OpenShift

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and
`Outcomes & Retrospective` must be kept up to date as work proceeds. It is maintained in accordance with
[docs/PLANS.md](../../PLANS.md) and delivers phase [CI17][phase]. Source baseline: `1ad150e`.

## Purpose / Big Picture

[CI16][ci16-exec] delivers generic OLM operator enablement (`ocp_operator.py`) using the NVIDIA Network Operator as its
initial consumer. OpenShift clusters running GPU workloads also require NVIDIA GPU Operator (`gpu-operator-certified`)
lifecycle automation grounded in the official [NVIDIA GPU Operator on OpenShift documentation][nvidia-ocp-docs],
including [Cleanup][nvidia-ocp-cleanup], and OpenShift-native machine semantics (`MachineConfig`, `MachineConfigPool`,
CRI-O/CDI, Driver Toolkit, and MOK signing keys).

This plan partitions NVIDIA GPU Operator operations into **atomic, domain-semantic callbacks first** — each a reusable
`K8SCallback` owning one official operational procedure — and then composes **chain combos** from those callbacks plus
CI16's OLM callbacks via `WorkflowExecutor`:

- `GpuOperatorStatus` — read-only cluster, OLM, CRD, MCO/MCP, and node-drift snapshot.
- `CudaUpgrade(node, driver)` — cluster-wide (`node="*"`) or node/pool-scoped CUDA/driver upgrade.
- `ContainerToolkit` — `ClusterPolicy.spec.toolkit` + `spec.cdi` aligned with OpenShift CRI-O and `MachineConfigPool`.
- `EnableFeatureDiscovery` — GPU Feature Discovery (`ClusterPolicy` `/spec/gfd/enabled` + NFD prerequisite).
- `MachineConfigSync` — OpenShift `MachineConfig` / `MachineConfigPool` alignment (MOK key, IOMMU, CRI-O/CDI).
- `ManageUpgradePolicy` — `upgradePolicy` and per-node skip / `upgrade-required` recovery labels.
- `ConfigureGpuSharing` — MIG (`single|mixed`), time-slicing, and MPS ConfigMap + node label bindings.
- `Cleanup` — ordered OpenShift teardown per `clean-up.html` (`delete_crds` explicit opt-in).

Example golden-path commands after delivery:

```bash
# Atomic read-only status across the entire cluster (node="*")
nvidia_gpu_operator.py status --json

# Atomic EnableFeatureDiscovery (CRD-verified /spec/gfd/enabled)
nvidia_gpu_operator.py gfd --enabled true --live-plan
nvidia_gpu_operator.py gfd --enabled true --apply --confirm-plan <sha256>

# Atomic ContainerToolkit with OpenShift CDI / MachineConfigPool alignment check
nvidia_gpu_operator.py container-toolkit --enabled true --cdi-enabled true --live-plan

# Atomic CudaUpgrade(node="*", driver="580.159.03") or scoped to one pool/node
nvidia_gpu_operator.py cuda-upgrade --node "*" --nvidia-driver <name> \
  --driver 580.159.03 --min-cuda 12.8 --live-plan
nvidia_gpu_operator.py cuda-upgrade --node "*" --nvidia-driver <name> \
  --driver 580.159.03 --min-cuda 12.8 --apply --confirm-plan <sha256>

# Atomic Cleanup (ordered OpenShift teardown per NVIDIA clean-up.html)
nvidia_gpu_operator.py cleanup --scope operator --live-plan
```

## Progress

- [x] (DEC-CI17-01 to DEC-CI17-14) Design decisions grounded in official NVIDIA GPU Operator OpenShift docs and live
      read-only OpenShift cluster observations.
- [x] (CI17-P1) Phase document, this ExecPlan and the plan index row are written.
- [ ] (CI17-M1) Operator-approved result schema (`nvidia-gpu-operator.schema.json`), shared discovery/preflight/wait
      helpers, `GpuOperatorStatus`, unit tests and live read-only proof.
- [ ] (CI17-M2) Atomic callbacks `EnableFeatureDiscovery`, `ContainerToolkit`, and `MachineConfigSync` with unit and
      live `--live-plan` proofs.
- [ ] (CI17-M3) Atomic callbacks `CudaUpgrade(node, driver)`, `ManageUpgradePolicy`, `ConfigureGpuSharing`, and
      `Cleanup` with unit and live `--live-plan` proofs.
- [ ] (CI17-M4) Chain combos, skill references, golden-path live evidence and renewed skill-digest receipts.

## Surprises & Discoveries

These observations come from read-only reads of the operator's OpenShift cluster on `2026-11-22`:

- Observation: `gpu-operator-certified` is installed on a non-default channel while a newer default channel exists.
  Evidence: Subscription `gpu-operator-certified` in `nvidia-gpu-operator` tracks channel `v26.3` from
  `certified-operators` with CSV `gpu-operator-certified.v26.3.3` (`Succeeded`); PackageManifest default channel is
  `v26.7` (`gpu-operator-certified.v26.7.3`), supporting `OwnNamespace` and `SingleNamespace`. `v26.3` ships
  `ClusterPolicy` and `NVIDIADriver` in `alm-examples`; `v26.7` also ships `GPUCluster`. Effect: DEC-CI17-02.
- Observation: `ClusterPolicy` CRD uses `/spec/gfd/enabled`, not `/spec/gfd/enable`.
  Evidence: Live `crd/clusterpolicies.nvidia.com` OpenAPI v3 schema lists `spec.gfd` keys as
  `args, enabled, env, hostNetwork, image, imagePullPolicy, imagePullSecrets, repository, resources, version`;
  `spec.toolkit` keys include `enabled, image, installDir, repository, version`; `spec.cdi` keys are
  `default, enabled, nriPluginEnabled`. Older doc snippets showing `/spec/gfd/enable` fail schema validation on v1.
  Effect: DEC-CI17-09, DEC-CI17-10.
- Observation: OpenShift `MachineConfig` already carries NVIDIA host configuration on this cluster.
  Evidence: `machineconfig.machineconfiguration.openshift.io/99-worker-nvidia-mok-key` is active alongside
  `ClusterPolicy` `spec.operator.use_ocp_driver_toolkit: true`, `spec.operator.runtimeClass: nvidia`, and
  `spec.cdi.enabled: true`. Host MCO state and GPU Operator CRD state must move hand-in-hand. Effect: DEC-CI17-08.
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
  `nvidia.com/gpu-driver-upgrade-state=upgrade-failed` and `upgrade-required`. Effect: DEC-CI17-06, DEC-CI17-07.
- Observation: `spec.driver.useOpenKernelModules` is deprecated upstream in favor of `spec.driver.kernelModuleType`
  (`auto`, `open`, `proprietary`), which the live `ClusterPolicy` and `NVIDIADriver` CRs already use.
  Effect: DEC-CI17-05.

## Decision Log

- Decision DEC-CI17-01: Model each NVIDIA OpenShift operational procedure as one named, domain-semantic, atomic
  `K8SCallback` (`GpuOperatorStatus`, `CudaUpgrade`, `ContainerToolkit`, `EnableFeatureDiscovery`, `MachineConfigSync`,
  `ManageUpgradePolicy`, `ConfigureGpuSharing`, `Cleanup`), backed by shared internal helpers (`discovery.py`,
  `preflight.py`, `waits.py`) and composed into chain combos in `chains.py`.
  Status: ACCEPTED
  Rationale: Matches operator mental model and official NVIDIA OpenShift docs 1:1 while keeping every callback small,
  reusable, and independently testable.
- Decision DEC-CI17-02: Do not duplicate OLM installation or removal code. Reuse CI16's `olm_callbacks` and
  `olm_resources` for `nfd` (`openshift-nfd`) and `gpu-operator-certified` (`nvidia-gpu-operator`).
  Status: ACCEPTED
  Rationale: One canonical OLM owner per repository standards.
- Decision DEC-CI17-03: `CudaUpgrade(node, driver)` accepts `node="*"` (entire cluster / all matching GPU nodes) or a
  specific node name / selector, plus target `driver` version tag (and optional `repository`, `image`,
  `kernel_module_type`, `use_precompiled`, `min_cuda`). Host CUDA capability is controlled by the driver container
  version and verified post-rollout through GFD node labels `nvidia.com/cuda.driver-version.full` and
  `nvidia.com/cuda.runtime-version.full`.
  Status: ACCEPTED
  Rationale: Single coherent callback for both cluster-wide and single-node/pool CUDA upgrades.
- Decision DEC-CI17-04: Fail closed when `ClusterPolicy.spec.driver.useNvidiaDriverCRD` is `true` and `CudaUpgrade`
  targets a driver update without naming `--nvidia-driver <name>` (or matching a single unambiguous `NVIDIADriver` CR
  for the requested `node`). Emit `BLOCKED` with `need: nvidia_driver_crd_required` listing active `NVIDIADriver` CRs
  and their `nodeSelector`s.
  Status: ACCEPTED
  Rationale: Patching `ClusterPolicy.spec.driver.version` while `useNvidiaDriverCRD: true` is active does not upgrade
  `NVIDIADriver`-managed node pools.
- Decision DEC-CI17-05: Driver/component updates support `version`, `repository`, `image`, `kernelModuleType`
  (`auto|open|proprietary`), `usePrecompiled`, and `imagePullSecrets` on `NVIDIADriver` or `ClusterPolicy.spec.driver`,
  and `version`, `repository`, `image`, `enabled` on operand components (`toolkit`, `gfd`, `devicePlugin`, `dcgm`,
  `dcgmExporter`, `migManager`, `validator`, `gds`, `gdrcopy`).
  Status: ACCEPTED
  Rationale: Real clusters combine `nvcr.io/nvidia/driver` (compiled via OCP Driver Toolkit) and custom precompiled
  driver images across node pools.
- Decision DEC-CI17-06: `GpuOperatorStatus` always computes per-node `matched_driver_cr` (evaluating `NVIDIADriver`
  `nodeSelector` against node labels) and `driver_drift` (desired version vs observed
  `nvidia.com/cuda.driver-version.full`), and surfaces `nvidia.com/gpu-driver-upgrade.skip`.
  Status: ACCEPTED
  Rationale: Makes silent drift and skipped nodes visible before any upgrade starts.
- Decision DEC-CI17-07: `CudaUpgrade` preflight blocks by default if any targeted non-skipped node is already in
  `nvidia.com/gpu-driver-upgrade-state=upgrade-failed`, emitting `need: node_upgrade_failed`. Nodes labeled
  `nvidia.com/gpu-driver-upgrade.skip=true` are excluded from the wait set and reported explicitly.
  Status: ACCEPTED
  Rationale: Prevents cascading upgrades over already-broken nodes while respecting operator-pinned skip nodes.
- Decision DEC-CI17-08: `ContainerToolkit` and `MachineConfigSync` treat `ClusterPolicy` CRD state and OpenShift
  `MachineConfig`/`MachineConfigPool` semantics as a coupled contract. `ContainerToolkit` verifies `spec.toolkit`,
  `spec.cdi`, `spec.operator.runtimeClass` (`nvidia`), `spec.operator.use_ocp_driver_toolkit`, `RuntimeClass/nvidia`,
  and worker `MachineConfigPool` (`Updated=True`, `Updating=False`, `Degraded=False`) so toolkit and host machine
  configuration never diverge.
  Status: ACCEPTED
  Rationale: On OpenShift, CRI-O runtime hooks, CDI, MOK signing keys, and kernel args (e.g., IOMMU) are governed by
  MCO and must converge with the operator CR.
- Decision DEC-CI17-09: `EnableFeatureDiscovery` validates NFD prerequisites (`nfd` Subscription/CSV and
  `feature.node.kubernetes.io/pci-10de.present=true` on GPU nodes), patches `ClusterPolicy` JSON path
  `/spec/gfd/enabled` (verified against the live CRD OpenAPI schema), and reads back GFD DaemonSet readiness plus
  `nvidia.com/gpu.product` and `nvidia.com/cuda.*` node labels.
  Status: ACCEPTED
  Rationale: Aligns NVIDIA's "Enabling GPU Feature Discovery" OpenShift procedure with live CRD v1 schema enforcement.
- Decision DEC-CI17-10: Every mutating callback defaults to `ActionMode.LIVE_PLAN` (read-only cluster read,
  Kubernetes `--dry-run=server` against the live CRD schema, exact before/after diff, `plan_digest`), writes only on
  `--apply --confirm-plan <sha256>`, and executes independent post-write read-back.
  Status: ACCEPTED
  Rationale: Matches CI16 and repository safety contracts; driver upgrades and `MachineConfig` changes can drain or
  reboot OpenShift nodes.
- Decision DEC-CI17-11: Every Kubernetes API read/write uses qualified resource names:
  `clusterpolicies.nvidia.com`, `nvidiadrivers.nvidia.com`, `nodefeaturediscoveries.nfd.openshift.io`,
  `machineconfigs.machineconfiguration.openshift.io`, `machineconfigpools.machineconfiguration.openshift.io`,
  `runtimeclasses.node.k8s.io`, plus CI16's qualified OLM resources.
  Status: ACCEPTED
  Rationale: Avoids bare-kind API-group ambiguity (same rule as DEC-CI16-13).
- Decision DEC-CI17-12: `Cleanup` follows NVIDIA's official OpenShift [Cleanup][nvidia-ocp-cleanup] order: delete
  `NVIDIADriver` CRs → delete `ClusterPolicy` (wait for operand DaemonSets to terminate) → remove OLM `Subscription`
  and `CSV` (reusing CI16 `remove`) → optional `OperatorGroup`/namespace cleanup → optional CRD deletion
  (`clusterpolicies.nvidia.com`, `nvidiadrivers.nvidia.com`) **only** when `--delete-crds` is explicitly passed and
  confirmed in `plan_digest`. Independent read-back verifies absence at the requested scope.
  Status: ACCEPTED
  Rationale: Matches NVIDIA OpenShift cleanup semantics while preventing accidental cluster-wide CRD loss.
- Decision DEC-CI17-13: `ManageUpgradePolicy` restricts node-label mutations to documented NVIDIA GPU Operator control
  keys (`nvidia.com/gpu-driver-upgrade.skip`, `nvidia.com/gpu-driver-upgrade-state`, `nvidia.com/gpu.deploy.driver`,
  `nvidia.com/mig.config`, `nvidia.com/device-plugin.config`) plus `--driver-track-label KEY=VALUE` only when `KEY`
  matches an existing `NVIDIADriver.spec.nodeSelector` key on the cluster.
  Status: ACCEPTED
  Rationale: Supports safe track switching and stuck-node retry without becoming an arbitrary cluster labeler.
- Decision DEC-CI17-14: Result JSON contract (`ci-skills/schemas/results/nvidia-gpu-operator.schema.json`) and
  `tools.json` updates are proposed and approved explicitly by the operator in `CI17-M1` before any JSON or contract
  file is written.
  Status: ACCEPTED
  Rationale: Complies strictly with the repository's contract and JSON approval lock.

## Context and Orientation

Code lives under `ci-skills/lib/python/core/kubernetes/nvidia_gpu/` with a thin CLI entrypoint at
`ci-skills/bin/nvidia_gpu_operator.py`. Callbacks inherit `K8SCallback` (`core/kubernetes/kubernetes.py`), share
`KubernetesService`, `K8SAuth`, and `WorkflowExecutor` (`core/executor.py`), and hold zero cluster-specific values.

## Atomic Callback Specifications

Each atomic callback owns one domain operation with `collect`, `run`, `update`, and `cleanup` hooks as needed, and
delegates low-level reads, server-side dry-runs, confirmation, and waits to shared helpers (`discovery.py`,
`preflight.py`, `waits.py`):

1. `GpuOperatorStatus(node="*")`: Read-only snapshot of NFD, OLM `gpu-operator-certified` (installed CSV + channels),
   `ClusterPolicy` (`status.state`, `useNvidiaDriverCRD`, all operand tags, `cdi`, `upgradePolicy`), `NVIDIADriver`
   CRs, NVIDIA-related `MachineConfig` and `MachineConfigPool` state, GPU node labels (`gpu.product`,
   `cuda.driver-version.full`, `cuda.runtime-version.full`, `gpu-driver-upgrade-state`, `gpu-driver-upgrade.skip`,
   `mig.config`), allocatable `nvidia.com/gpu`, desired-vs-observed `driver_drift`, and validator pods.
2. `CudaUpgrade(node, driver, ...)`: Upgrades the CUDA driver for `node="*"` (cluster-wide) or a targeted node/pool.
   - Resolves target (`NVIDIADriver` CR when `useNvidiaDriverCRD=true`, else `ClusterPolicy.spec.driver`).
   - Preflights `upgrade-failed` nodes and `gpu-driver-upgrade.skip=true` exclusions.
   - Runs `--dry-run=server` JSON/merge patch, binds before/after diff into `plan_digest`, applies on confirmation.
   - Waits for `ClusterPolicy`/`NVIDIADriver` `status.state=ready` and per-node `nvidia.com/gpu-driver-upgrade-state`
     (`upgrade-required` → `cordon-required` → `wait-for-jobs-required` → `upgrade-done`, failing fast on
     `upgrade-failed`).
   - Independently verifies `nvidia.com/cuda.driver-version.full`, `nvidia.com/cuda.runtime-version.full` (and optional
     `--min-cuda`), allocatable `nvidia.com/gpu`, and `nvidia-operator-validator` readiness.
3. `ContainerToolkit(enabled=True, ...)`: Manages `ClusterPolicy.spec.toolkit.*` (`enabled`, `version`, `repository`,
   `image`, `installDir`), `spec.cdi.*` (`enabled`, `default`, `nriPluginEnabled`), and OpenShift runtime semantics
   (`spec.operator.runtimeClass=nvidia`, `spec.operator.use_ocp_driver_toolkit=true`, `RuntimeClass/nvidia`, and worker
   `MachineConfigPool` readiness `Updated=True`, `Updating=False`, `Degraded=False`).
4. `EnableFeatureDiscovery(enabled=True, ...)`: Verifies NFD (`NodeFeatureDiscovery` + `pci-10de.present=true`),
   server-side dry-runs and applies JSON patch `[{"op": "replace", "path": "/spec/gfd/enabled", "value": <bool>}]`
   (plus optional tag/repository overrides), waits for `gpu-feature-discovery` DaemonSet and `ClusterPolicy` ready,
   and verifies GFD node labels (`nvidia.com/gpu.product`, `nvidia.com/cuda.*`).
5. `MachineConfigSync(role="worker", ...)`: OpenShift MCO callback for NVIDIA host prerequisites (MOK signing key,
   IOMMU kernel arguments, CRI-O/CDI drop-ins). Plans diff with server-side dry-run, applies on confirmation, and
   waits for `MachineConfigPool` convergence (`Updated=True`, `Updating=False`, `Degraded=False`).
6. `ManageUpgradePolicy(...)`: Updates `upgradePolicy` (`autoUpgrade`, `maxParallelUpgrades`, `maxUnavailable`,
   `drain`, `waitForCompletion`) on `ClusterPolicy` or `NVIDIADriver`, and manages documented node control labels
   (`gpu-driver-upgrade.skip`, `gpu-driver-upgrade-state=upgrade-required`, `gpu.deploy.driver`, or declared driver
   track labels).
7. `ConfigureGpuSharing(...)`: Configures MIG (`spec.mig.strategy`, `spec.migManager.config`), time-slicing
   (`spec.devicePlugin.config`), or MPS (`spec.devicePlugin.mps`) after confirming referenced `ConfigMap`s exist, plus
   optional node sharing labels (`nvidia.com/mig.config`, `nvidia.com/device-plugin.config`).
8. `Cleanup(scope="operator", delete_crds=False)`: Grounded in NVIDIA OpenShift `clean-up.html`. Plans full impact
   (listing CRs, Subscription, CSV, DaemonSets, and whether CRDs will be deleted), tears down in safe order
   (`NVIDIADriver` → `ClusterPolicy` → CI16 OLM remove → optional `OperatorGroup`/namespace → explicit opt-in CRD
   delete), and reads back absence.

### Chain Composition (`chains.py`)

Chain combos compose strictly from these atomic callbacks and CI16 OLM callbacks via `WorkflowExecutor`:

- `enable-gpu-operator`: `[K8SAuth, *ci16_nfd_callbacks, MachineConfigSync(verify_only=True),
  *ci16_gpu_operator_callbacks, ContainerToolkit(enabled=True), EnableFeatureDiscovery(enabled=True),
  GpuOperatorStatus()]`
- `upgrade-cuda`: `[K8SAuth, GpuOperatorStatus(node=node), *(optional ManageUpgradePolicy),
  CudaUpgrade(node=node, driver=driver), GpuOperatorStatus(node=node)]`
- `align-toolkit-ocp`: `[K8SAuth, GpuOperatorStatus(), MachineConfigSync(...), ContainerToolkit(...),
  GpuOperatorStatus()]`
- `recover-failed-upgrade`: `[K8SAuth, GpuOperatorStatus(node=node), ManageUpgradePolicy(retry_failed=True),
  CudaUpgrade(node=node, wait_only=True), GpuOperatorStatus(node=node)]`
- `cleanup-gpu-operator`: `[K8SAuth, GpuOperatorStatus(), Cleanup(scope=scope, delete_crds=delete_crds),
  GpuOperatorStatus()]`

## Verification Gates

| Gate ID | Scope | Proof Required |
| --- | --- | --- |
| `G-CI17-01` | Unit & contracts | Unit tests per atomic callback, CRD schema guard, MCO check, `NO_OP`, drift, schema |
| `G-CI17-02` | Live `status` | Installed `GpuOperatorStatus` returns `PASS` with policy, driver CRs, MCO, drift |
| `G-CI17-03` | Live `--live-plan` | Server-side dry-run + `plan_digest` with `mutated: false` for mutating atomics |
| `G-CI17-04` | Atomic write/restore | Authorized reversible atomic write verified by read-back and restored cleanly |
| `G-CI17-05` | Chain combo | Installed combo verified end-to-end with renewed candidate acceptance receipts |

## Interfaces and Dependencies

No new third-party Python dependency is introduced. All cluster operations go through `KubernetesService` (`kubectl`)
and CI16's `olm_callbacks`/`olm_resources`.

Planned module layout under `ci-skills/lib/python/core/kubernetes/nvidia_gpu/`:

- `models.py`: Qualified resource constants (`CLUSTER_POLICIES`, `NVIDIA_DRIVERS`, `NODE_FEATURE_DISCOVERIES`,
  `MACHINE_CONFIGS`, `MACHINE_CONFIG_POOLS`, `RUNTIME_CLASSES`), `GPUOperatorAction`, `GPUOperatorState`,
  `DriverPoolInfo`, `GPUNodeInfo`, `GPUNeed`, and `GPU_OPERATOR_MODE_POLICY`.
- `discovery.py`: Shared read helpers for OLM, `ClusterPolicy`, `NVIDIADriver`, MCO/MCP, GPU nodes, and validators.
- `preflight.py`: Shared contract validation, `--dry-run=server` diffing, and `plan_digest` confirmation.
- `waits.py`: Shared polling helpers for `ClusterPolicy`/`NVIDIADriver` readiness, `gpu-driver-upgrade-state`,
  `MachineConfigPool` convergence, and CUDA/validator read-back.
- `callbacks.py`: Domain-semantic atomic callbacks (`GpuOperatorStatus`, `CudaUpgrade`, `ContainerToolkit`,
  `EnableFeatureDiscovery`, `MachineConfigSync`, `ManageUpgradePolicy`, `ConfigureGpuSharing`, `Cleanup`).
- `chains.py`: Callback-list builders for standalone atomic verbs and composed chain combos.
- `cli.py`: `build_parser`, `workflow`, `NvidiaGPUOperator`, `main`, invoked by `ci-skills/bin/nvidia_gpu_operator.py`.

## Outcomes & Retrospective

Not yet implemented. Updated at the completion of each milestone (`CI17-M1` through `CI17-M4`).

[phase]: ../../phases/CI17-NVIDIA-GPU-OPERATOR.md
[ci16-exec]: ci16-olm-operator.md
[nvidia-ocp-docs]: https://docs.nvidia.com/datacenter/cloud-native/openshift/latest/index.html
[nvidia-ocp-cleanup]: https://docs.nvidia.com/datacenter/cloud-native/openshift/latest/clean-up.html
