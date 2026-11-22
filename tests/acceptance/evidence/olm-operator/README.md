# CI16-M1 live plans: OLM operator on the discovery cluster

Date: 2026-11-22. Both CI16-M1 live proofs from an installed copy of the
`ci16-olm-operator` head `af6ef55`, against the OpenShift 4.19 discovery
cluster selected by `~/.ci-skills/target.toml`.

## `install-live-plan.json`

`ocp_operator.py install --package nvidia-network-operator
--namespace nvidia-network-operator --live-plan` returned `PLANNED` with the
plan the cluster states:

- channel `v26.7` — the existing Subscription's channel, not the PackageManifest
  default `stable` (DEC-CI16-06);
- catalog source `certified-operators` / `openshift-marketplace`;
- `enabled: true`, installed CSV `nvidia-network-operator.v26.7.1`, phase
  `Succeeded`, install mode `OwnNamespace` (read from the installed CSV's
  `spec.installModes`), approval `Automatic`;
- plan digest `2b0745a1…`, no `needs`, no errors.

## `apply-live-plan.json`

`ocp_operator.py apply --package nvidia-network-operator
--namespace nvidia-network-operator --kind NicClusterPolicy --shipped-default
--live-plan` returned `PLANNED` and resolved the document the installed CSV
ships in its `alm-examples` annotation (DEC-CI16-03): kind `NicClusterPolicy`,
name `nic-cluster-policy`.

No object was created or changed by either run; CI16-M1 is read-only by design,
and confirmed apply is rejected with `olm_apply_not_delivered_until_ci16_m2`
until the writer chains land.

Companion err files carry the command's own diagnostic log lines for each run.
