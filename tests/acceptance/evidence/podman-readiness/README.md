# Installed Podman readiness live evidence

Captured on 2026-10-18 from installed candidate
`855f00b4d2fad7c54764b33df256236e1f9b412d`.

- Package checksum: `ab0640b86de635a743383bf7543fb67c99b0ad98461d8a5be78e43d9b7f69564`.
- Functional checksum: `49525a9ed253dd627ee4c75b2a46fa9dab9494883e6fa4f789ee8fe3e4f547d3`.
- Run ID: `694346b9bc6b4547aeae61c02a1d368c`.
- Installed result: `PASS`; nested Podman runtime verified.
- Cleanup result: `PASS`; no run-owned object remains.

## Installed discovery

The installed [proposition][node] offers the Podman readiness action and its
focused references. [Action discovery][action] identifies the executable,
provider, side effects and read-back. [Options][options] and [help][help] show
the caller inputs and plan/apply confirmation.

## Live result

The [installed receipt][receipt] records a verified temporary SCC RoleBinding,
the exact Job and Pod identities, the admitted runtime image digest and exit
code zero. Its probe rows confirm Podman 5.8.7, overlay storage on XFS, one
nested container, bind-mount read-back, inner exit-code 7 propagation and no
remaining inner containers. The same observations are retained in the
[markers][markers] and [probe log][probe-log]. The [debug trace][trace] contains
the complete callback order and provider return codes.

The command removed its Job, ConfigMap, temporary SCC RoleBinding and generated
Namespace. [Independent cleanup][cleanup] found no Namespace, Pod, ConfigMap,
Job or RoleBinding carrying the run ID; the pre-existing privileged SCC and its
ClusterRole still exist. No PVC, PV, NFS path or model data was used or changed.

The prepared auxiliary observer's [baseline][observer-baseline] query completed
after the generated Namespace opened, so it classified that Namespace as
pre-existing and its [final result][observer-final] selected no fresh namespace.
That observer result is retained as a timing limitation and is not presented as
runtime proof. The installed command's provider-native Job, Pod and log reads in
the receipt and trace supply the live runtime proof; the later exact-selector
reads supply independent cleanup proof.

## Before correction

The earlier installed candidate `23025b981392ee30c6d5902587abba6be2b92202`
used functional checksum
`4ff31de56ba4605abe43b0b035d6cabad866b274e889722f85cd75a5f911a88a`.
Its [receipt][before-receipt] is `BLOCKED`, not runtime proof: the old default
image digest returned `manifest_unknown`. [Registry diagnosis][diagnosis]
resolved the current Linux/amd64 content digest used by the passing run, and
[cleanup][before-cleanup] verified the failed run left no owned resources.

[Transformation details](TRACE-TRANSFORM.md) describe portable publication.
[Hash mapping](TRANSFORM-MAPPING.json) binds every private source artifact to
its published copy. [Published hashes](raw-sha256.txt) cover this directory.

[node]: 02-proposition.json
[action]: 06-selected-action.json
[options]: 07-options.json
[help]: 08-help.txt
[receipt]: 10-live-apply.json
[markers]: 11-probe-markers.json
[probe-log]: 12-probe.log
[cleanup]: 13-independent-cleanup.json
[observer-baseline]: 14-observer-baseline.json
[observer-final]: 15-observer-final.json
[trace]: traces/10-live-apply.jsonl
[before-receipt]: before/01-blocked-receipt.json
[diagnosis]: before/02-registry-diagnosis.json
[before-cleanup]: before/03-independent-cleanup.json
