# Installed Podman readiness: final candidate

Captured on 2026-10-18 from installed revision `3570e1b7618e34a7d365002735c0df2fcb70667b`.

- Package checksum: `db811e2af1e4ec717956f409933a773429e2f813fb16baf9f8456fe4d54cdb11`.
- Functional checksum: `e5a3d6b3cae147228cddb0fa0b1978c94f3c4cd776ca57113589acd7ef769720`.
- Successful probe: **PASS**, including installed cleanup and independent absence reads.
- Interruption proof: **not established** by the mistimed attempt recorded here.

## Successful probe

The [installed receipt](success/receipt.json) records all eight runtime checks
passing, the exact Job and Pod identities, the pinned image and Pod exit code
zero. The installed callbacks created their temporary SCC RoleBinding and
removed their owned Job, ConfigMap, RoleBinding and Namespace. The
[trace](success/trace.jsonl) preserves the actual callback and provider events.

The [original observer](success/observer.json) missed the short-lived Pod and
timed out during a broad cleanup read. It remains `BLOCKED`. The later
[exact cleanup reads](success/supplemental-cleanup.json) independently verified
absence of the Namespace, Job, Pod, ConfigMap and RoleBinding and an empty
three-label object selection. That read-back explicitly identifies the Pod UID
as coming from the installed command's API-backed receipt. Existing SCC and
ClusterRole identities were unchanged. No shared storage was touched.

## Mistimed interruption attempt

The [attempt observer](mistimed-term/observer.json) never saw the brief owned
Pod before the command completed normally. It sent **no signal**; its result
is `BLOCKED`, with `expected_result: false`. The command's
[receipt](mistimed-term/receipt.json) is a normal successful probe, not evidence
of interruption handling. Cleanup and the
[exact Pod absence read](mistimed-term/supplemental-pod-absence.json) passed.
The [trace](mistimed-term/trace.jsonl) is retained without relabeling the result.

## Discovery and provenance

The [proposition](discovery/02-proposition.json),
[task references](discovery/03-task-references.json),
[selected action](discovery/06-selected-action.json) and
[help](discovery/08-help.txt) were read from this installation.

[Transformation mapping](TRANSFORM-MAPPING.json) binds private raw captures to
published files by SHA-256. JSON uses this installation's existing redaction and
portable-path helpers, plus the existing exporter’s stable selector tokens.
Only private paths and target selectors are tokenized. Result statuses, UIDs,
timestamps, callback identities, return codes and trace event order are retained.
Probe markers, probe log and help are copied unchanged. `raw-sha256.txt` covers
the published tree. These files do not change the executable functional checksum.

## Ordinary acceptance

The 24 ordinary installed capability checks also passed with this same
functional checksum. Their CLI-written portable receipts are saved unchanged
under the existing acceptance receipt paths; [hashes](acceptance-receipts.sha256)
identify those exact files. [Cleanup](acceptance-cleanup.json) passed with no
errors after removing the disposable test resources. This refresh does not
substitute for the interruption proof above.
