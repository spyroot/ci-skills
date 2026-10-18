# Installed native OpenShift ISO evidence

Captured on 2026-10-18 from installed candidate
`bf69d6d52e8cc04cafd0d0a24d640762e73fb3df` against OpenShift 4.19.26.

- Package checksum: `dbaf02d4a9ab24fb4ee5980c66f42425fd84a4f5c1e4c7e6f73735c4942d5b6b`.
- Functional checksum: `8dde2e6f7389ab60a1cf978d7ead63366e6b045cb422b2a7f82705f209084ff1`.
- [Installed identity](01-installed-provenance.json), [proposition](02-proposition.json),
  [references](03-references.json), [actions](04-actions.json) and [help](05-help.txt)
  were captured from that installation.

## Native input and stored output

- YAML: [two-NIC native input](inputs/nodes-config.yaml), generated in scratch
  and copied to the selected PVC. [Build](yaml/02-build.json),
  [independent verify](yaml/03-verify.json) and [remove](yaml/05-remove.json)
  each returned `PASS`.
- MAC: `02:00:00:10:00:01`, generated directly on the selected PVC.
  [Build](mac/02-build.json), [independent verify](mac/03-verify.json) and
  [remove](mac/05-remove.json) each returned `PASS`.

The caller's [YAML destination](inputs/yaml-destination.json) and
[MAC destination](inputs/mac-destination.json) select an existing shared PVC.
`CheckDestinationStorage` observed writable NFS4 storage. The YAML route ran
`CopyArtifact`; the MAC route produced directly at its requested destination.
Both routes ran `VerifyStoredArtifact`, then a separate installed `verify`
invocation reopened the saved ISO and rechecked its size, hash and Ignition.

Each ISO measured 1,379,328,000 bytes. The verified SHA-256 digests were:

- YAML: `a18a4b5447e63be1646c5c5d90cf51f6f7f9fcd2d89e50b5185c0f72a29ce6d6`.
- MAC: `a147f306fd4e10cfa0200e223720450e9bc0fd459657f223909672bdd26d3529`.

YAML inspection observed the supplied hostname, both MAC addresses, `ens3`
MTU 9000 and `ens4` MTU 1500. Root-device hints were digest-only and the role
was unobserved. The MAC-only route observed its supplied MAC; hostname, network,
root-device hints and role were unobserved. These reports do not establish
hardware boot, node joining, CSR approval or semantic verification of every
native input field. Native OpenShift input remains OpenShift-owned.

## Native rejection

The same installed candidate received a deliberately malformed
[native input](rejection/native-input.txt). Its [plan](rejection/01-plan.json)
accepted the file for forwarding; OpenShift then rejected its YAML. The
[command result](rejection/02-result.json) reports expected `BLOCKED`, native
exit 1 and [process exit 2](rejection/process.exit), retaining the native parse
error. No successful build is claimed for this case.

[Independent read-back](rejection/03-readback.json) confirms cleanup passed,
run-owned resources were absent, the existing PVC stayed Bound and the exact
[output paths were absent](rejection/destination-absence.log). The scoped
before/after resource comparison observed no additions. One pre-existing
resource disappeared from each compared set concurrently; that unrelated
activity is retained in the observation and is not attributed to this command.

## Reusable storage behavior

The [installed callback proof](storage/callback-proof.json) exercises the same
installed storage callbacks with tiny files. It is a callback harness, not four
additional native ISO builds.

- Destination conflict failed before the producer sentinel started; the test
  conflict file was then removed.
- Persistent copy independently matched source and destination size and hash;
  this small positive case corroborates the full YAML ISO transfer above.
- Temporary output remained readable in its retained Pod after execution;
  explicit removal then removed the Pod and its ephemeral output.
- A destination conflict introduced after the storage check made transfer fail.
  The verified source stayed recoverable until explicit removal.

All four cases report `PASS`, with exact task-owned output and scope cleanup.
The raw harness SHA-256 is
`7803d8402d78057223884bb719e7ca70918581a376bf6cfd43be6c748ea73e6e`.

## Cleanup and observed limits

Both installed `remove` operations passed, removed their run records and
verified temporary resource absence. Product removal preserves caller output.
The [separate test-file cleanup](cleanup/iso-files.json) follows the operator's
subsequent instruction to delete the four exact test ISOs, including two prior
captured builds. It checks each file's known digest before deleting it and
records absence afterward: 5,517,312,000 bytes reclaimed.
[Deletion output](cleanup/iso-files.log) and
[independent absence](cleanup/iso-absence.log) preserve the exact file
observations. The existing PVC and unrelated model data are
outside that deletion scope. Cleanup lists retain repeated shared-state results
returned by default lifecycle hooks; those entries are not counted as separate
deletions. The actual removal claims use the independent absence observations.

[Native RBAC read-back](cleanup/native-rbac-absence.json) records absence of the
MAC build's observed temporary native ClusterRole and ClusterRoleBinding.
Broad cluster RBAC inventory is not included.
[Acceptance refresh cleanup](cleanup/acceptance-refresh.json) records cleanup
for the separate 24 existing
receipt categories captured from the same installation.

The [single MAC removal invocation](mac/06-remove-runtime.json) completed
after an observed wait of more than five minutes. No removal retry or extra build was used to turn that wait
into success. The receipt establishes completion; it does not diagnose the
cause of the wait. An earlier YAML plan invocation included `--receipt-out`,
which is not valid in plan mode; it was rejected before mutation and corrected
by capturing stdout. No provider receipt is synthesized for that harness error.

[Timing](measurement.json) is derived from the captured build and verify traces:
one sample per route and operation, with no performance claim. Every original
trace event remains in [the trace directory](traces/).
[Transformation details](TRACE-TRANSFORM.md),
[raw-to-portable hashes](TRANSFORM-MAPPING.json),
[raw capture hashes](raw-sha256.txt) and
[published hashes](sha256.txt) make the export auditable.
