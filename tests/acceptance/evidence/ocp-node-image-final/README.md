# Final installed native OpenShift ISO evidence

Captured on 2026-10-18 from installed source
`48c4489afdf339d016cdc713affa5c1acdc9405b`.

- Package SHA-256: `ca60a9cf729239fe161121f72d094b2bf1f4b9f075507bc03fe9fa2daa944972`.
- Functional SHA-256: `87f13c4f55a8e965c3e719e8a5b0e7a11b498082cfec1442e3b79d2fe12d240a`.
- [Installed identity](01-installed-provenance.json), [installation record](02-installation-record.json),
  [proposition](03-proposition.json), [references](04-references.json),
  [actions](05-actions.json), [help](06-help.txt) and [description](07-description.json)
  retain the captured candidate identity.

## Two native builds

| Route | Verified bytes | Verified ISO SHA-256 |
| --- | ---: | --- |
| YAML | 1379328000 | `3c942a87cb17ddbbfee77f94060200eced9d92c64584e9d47d9784df9d953b98` |
| MAC | 1379328000 | `64b1caa11efff902d9db4ff6bf6ec31fa95f9618d19a31b0c4c37f228f610c9c` |

The [native YAML](inputs/nodes-config.yaml) and caller-selected
[YAML destination](inputs/yaml-destination.json) and
[MAC destination](inputs/mac-destination.json) were used by the successful final plans.
Both [YAML build](yaml/02-build.json) and [MAC build](mac/02-build.json) returned `PASS`.
Separate installed [YAML verification](yaml/03-verify.json) and
[MAC verification](mac/03-verify.json) reopened the saved ISOs and matched their
size, SHA-256 and embedded Ignition. These captures do not prove hardware boot or node joining.

## Removal and exact test-file cleanup

Both [YAML removal](yaml/05-remove.json) and [MAC removal](mac/05-remove.json)
returned `PASS`. Product removal preserves caller output. The separately approved
[exact file cleanup](cleanup/iso-cleanup.log) checked both known hashes, deleted
only these two test ISOs and their empty parents, and reclaimed
2,758,656,000 bytes.
[Independent read-back](cleanup/iso-absence.log) found both exact ISO paths absent.

The [scoped cleanup read-back](cleanup/scoped-readback.json) records exact run-owned
and cleanup-helper absence and the same PVC UID in `Bound` state before and after.
Three native objects created after the final operation were absent on a later
exact-name read; no deletion was issued for them. This is a bounded attribution
assessment, not a global cluster-emptiness claim. [Acceptance-refresh cleanup](cleanup/acceptance-refresh.json)
records the separate receipt refresh.

[Traces](traces/) retain captured events; process exit files retain actual exit statuses.
[Transformation details](TRACE-TRANSFORM.md), [raw-to-published mapping](TRANSFORM-MAPPING.json),
[raw hashes](raw-sha256.txt) and [published hashes](sha256.txt) identify the evidence bytes.
Earlier [ISO evidence](../ocp-node-image/README.md) retains its original source identity.
